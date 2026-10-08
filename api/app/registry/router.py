import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from geoalchemy2.shape import from_shape
from pydantic import ValidationError
from scada_common import SensorType, catalog
from scada_db import models as m
from shapely.geometry import Point
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.security import Principal
from app.deps import CH, DB, require
from app.registry import repo
from app.registry.schemas import (
    BulkResult,
    BulkRowError,
    HistoryPoint,
    RoutePoint,
    Sensor,
    SensorCreate,
    SensorHistory,
    SensorTypeInfo,
    SensorUpdate,
    Threshold,
    Vehicle,
    VehicleRoute,
    WhitelistEntry,
    WhitelistPage,
)

router = APIRouter(tags=["registry"])

FromQ = Annotated[datetime | None, Query(alias="from", description="По умолчанию — час назад")]
ToQ = Annotated[datetime | None, Query(alias="to", description="По умолчанию — сейчас")]
CanView = Annotated[Principal, Depends(require("sensors:view"))]
CanEdit = Annotated[Principal, Depends(require("sensors:edit"))]
MAX_ROUTE_POINTS = 2000


def period(start: datetime | None, end: datetime | None, max_days: int = 31) -> tuple[datetime, datetime]:
    end = (end or datetime.now(UTC)).astimezone(UTC)
    start = (start or end - timedelta(hours=1)).astimezone(UTC)
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "'from' must be before 'to'")
    if end - start > timedelta(days=max_days):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Period is limited to {max_days} days")
    return start, end


def _sensor_row(db, sensor_id: str) -> m.Sensor:
    row = db.get(m.Sensor, sensor_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sensor {sensor_id} not found")
    return row


def _validate_refs(db, building_id: str | None, zone_id: str | None, vehicle_id: str | None) -> None:
    if building_id and db.get(m.Building, building_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown building {building_id}")
    if zone_id and db.get(m.Zone, zone_id) is None and db.get(m.Building, zone_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown zone {zone_id}")
    if vehicle_id and db.get(m.Vehicle, vehicle_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown vehicle {vehicle_id}")


# --- sensor types & sensors --------------------------------------------------------------------------


@router.get("/sensor-types", response_model=list[SensorTypeInfo])
def list_sensor_types(_: CanView) -> list[SensorTypeInfo]:
    return repo.sensor_types()


@router.get("/sensors", response_model=list[Sensor])
def list_sensors(db: DB, _: CanView, type: SensorType | None = None, building_id: str | None = None) -> list[Sensor]:
    return repo.sensors(db, sensor_type=type, building_id=building_id)


@router.get("/sensors/{sensor_id}", response_model=Sensor)
def get_sensor(sensor_id: str, db: DB, _: CanView) -> Sensor:
    _sensor_row(db, sensor_id)
    return repo.sensors(db, ids=[sensor_id])[0]


@router.post("/sensors", response_model=Sensor, status_code=status.HTTP_201_CREATED,
             responses={409: {"description": "Sensor id already exists"}})
def create_sensor(body: SensorCreate, db: DB, _: CanEdit) -> Sensor:
    """Без `thresholds` датчик получает типовые пороги своего типа. Начинает принимать данные сразу (коннекторы
    перечитывают реестр), симулятор подхватывает климат и движение в течение минуты."""
    if db.get(m.Sensor, body.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Sensor {body.id} already exists")
    _validate_refs(db, body.building_id, body.zone_id, body.vehicle_id)
    if repo.is_mobile(body.type) and body.geo is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mobile sensors have no fixed position")
    repo.insert_sensor(db, body)
    db.commit()
    return repo.sensors(db, ids=[body.id])[0]


@router.patch("/sensors/{sensor_id}", response_model=Sensor)
def update_sensor(sensor_id: str, body: SensorUpdate, db: DB, _: CanEdit) -> Sensor:
    row = _sensor_row(db, sensor_id)
    changes = body.model_dump(exclude_unset=True)
    _validate_refs(db, changes.get("building_id"), changes.get("zone_id"), None)
    if "geo" in changes:
        geo = changes.pop("geo")
        if row.is_mobile and geo is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mobile sensors have no fixed position")
        row.geom = from_shape(Point(geo["x"], geo["y"]), srid=m.SRID) if geo else None
        if geo and geo.get("floor") is not None:
            row.floor = geo["floor"]
    for k, v in changes.items():
        setattr(row, k, v)
    db.commit()
    return repo.sensors(db, ids=[sensor_id])[0]


@router.put("/sensors/{sensor_id}/thresholds", response_model=Sensor)
def set_thresholds(sensor_id: str, body: list[Threshold], db: DB, _: CanEdit) -> Sensor:
    """Новые пороги становятся следующей версией; старые закрываются (`valid_to`). Поле `version` во входе игнорируется.
    Worker перечитывает пороги каждые 30 с."""
    row = _sensor_row(db, sensor_id)
    allowed = catalog.numeric_metrics(row.type)
    if any(t.metric not in allowed for t in body) or len({t.metric for t in body}) != len(body):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Metrics must be unique and one of {allowed}; got {[t.metric for t in body]}")
    repo.replace_thresholds(db, sensor_id, body)
    db.commit()
    return repo.sensors(db, ids=[sensor_id])[0]


# --- bulk upload ---------------------------------------------------------------------------------------


def _parse_bulk(raw: str, filename: str) -> list[dict]:
    if filename.lower().endswith(".json"):
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError("JSON must be an array of sensors")
        return rows
    rows = [{k.strip(): v for k, v in r.items() if v not in ("", None)} for r in csv.DictReader(io.StringIO(raw))]
    for r in rows:
        if "thresholds" in r:
            r["thresholds"] = json.loads(r["thresholds"])
        if "x" in r and "y" in r:
            r["geo"] = {"x": r.pop("x"), "y": r.pop("y"), "floor": r.get("floor")}
    return rows


@router.post("/sensors/bulk", response_model=BulkResult)
async def bulk_create_sensors(
    file: UploadFile,
    db: DB,
    _: CanEdit,
    dry_run: Annotated[bool, Query(description="Только проверить, ничего не создавать")] = True,
) -> BulkResult:
    """CSV (колонки как у SensorCreate; `x`,`y` — координаты; `thresholds` — JSON-строка) или JSON-массив.

    Всё или ничего: при любой ошибке не создаётся ни один датчик, ошибки возвращаются построчно.
    """
    try:
        rows = _parse_bulk((await file.read()).decode("utf-8-sig"), file.filename or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Cannot parse file: {e}") from e

    existing = set(db.scalars(select(m.Sensor.id)))
    buildings = set(db.scalars(select(m.Building.id)))
    areas = buildings | set(db.scalars(select(m.Zone.id)))
    valid: list[SensorCreate] = []
    errors: list[BulkRowError] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        try:
            s = SensorCreate.model_validate(row)
        except ValidationError as e:
            errors += [BulkRowError(row=i, field=".".join(map(str, err["loc"])) or None, message=err["msg"])
                       for err in e.errors()]
            continue
        problems = []
        if s.id in existing or s.id in seen:
            problems.append(("id", f"Duplicate sensor id {s.id}"))
        if s.building_id and s.building_id not in buildings:
            problems.append(("building_id", f"Unknown building {s.building_id}"))
        if s.zone_id and s.zone_id not in areas:
            problems.append(("zone_id", f"Unknown zone {s.zone_id}"))
        if repo.is_mobile(s.type) and s.geo is not None:
            problems.append(("geo", "Mobile sensors have no fixed position"))
        errors += [BulkRowError(row=i, field=f, message=msg) for f, msg in problems]
        if not problems:
            seen.add(s.id)
            valid.append(s)

    created = 0
    if not dry_run and not errors and valid:
        for s in valid:
            repo.insert_sensor(db, s)
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, f"Conflict while saving: {e.orig}") from e
        created = len(valid)
    return BulkResult(dry_run=dry_run, total=len(rows), valid=len(valid), created=created, errors=errors)


# --- history -------------------------------------------------------------------------------------------


@router.get("/sensors/{sensor_id}/history", response_model=SensorHistory)
def sensor_history(
    sensor_id: str,
    db: DB,
    ch: CH,
    _: CanView,
    metric: Annotated[str, Query(description="Числовая метрика типа датчика, напр. temperature_c")],
    start: FromQ = None,
    end: ToQ = None,
) -> SensorHistory:
    """Шаг выбирается по длине периода: до 1 ч — сырые точки, до суток — минутные агрегаты, дальше — часовые."""
    row = _sensor_row(db, sensor_id)
    if metric not in catalog.numeric_metrics(row.type):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Metric {metric} is not numeric for {row.type}; use {catalog.numeric_metrics(row.type)}")
    start, end = period(start, end, max_days=366)
    span = end - start
    params = {"s": sensor_id, "m": metric, "from": start, "to": end}
    if span <= timedelta(hours=1):
        step = "raw"
        q = """SELECT ts, metrics[{m:String}] AS v, v, v FROM telemetry FINAL
               WHERE sensor_id = {s:String} AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
                 AND mapContains(metrics, {m:String}) ORDER BY ts LIMIT 5000"""
    else:
        step, table = ("1m", "telemetry_1m") if span <= timedelta(days=1) else ("1h", "telemetry_1h")
        q = f"""SELECT bucket, min(min_v), sum(sum_v) / sum(cnt), max(max_v) FROM {table}
                WHERE sensor_id = {{s:String}} AND metric = {{m:String}}
                  AND bucket BETWEEN {{from:DateTime}} AND {{to:DateTime}}
                GROUP BY bucket ORDER BY bucket"""
    points = [HistoryPoint(ts=r[0].replace(tzinfo=UTC), min=round(r[1], 3), avg=round(r[2], 3), max=round(r[3], 3))
              for r in ch.query(q, parameters=params).result_rows]
    return SensorHistory(sensor_id=sensor_id, metric=metric, step=step, points=points)


# --- vehicles ------------------------------------------------------------------------------------------


@router.get("/vehicles", response_model=list[Vehicle])
def list_vehicles(db: DB, _: CanView) -> list[Vehicle]:
    return repo.vehicles(db)


@router.get("/vehicles/{vehicle_id}/route", response_model=VehicleRoute)
def vehicle_route(vehicle_id: str, db: DB, ch: CH, _: CanView, start: FromQ = None, end: ToQ = None) -> VehicleRoute:
    """Трек машины по ГЛОНАСС; длинные периоды прореживаются до ~2000 точек (последняя точка в интервале)."""
    tracker = db.scalar(select(m.Sensor.id).where(m.Sensor.vehicle_id == vehicle_id))
    if tracker is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Vehicle {vehicle_id} not found or has no tracker")
    start, end = period(start, end, max_days=7)
    step_s = max(1, int((end - start).total_seconds() // MAX_ROUTE_POINTS))
    rows = ch.query("""
        SELECT toStartOfInterval(ts, toIntervalSecond({step:UInt32})) AS t, argMax(x, ts), argMax(y, ts),
               argMax(metrics['speed_kmh'], ts)
        FROM telemetry
        WHERE sensor_id = {s:String} AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)} AND x IS NOT NULL
        GROUP BY t ORDER BY t""", parameters={"s": tracker, "from": start, "to": end, "step": step_s}).result_rows
    return VehicleRoute(vehicle_id=vehicle_id, points=[
        RoutePoint(ts=r[0].replace(tzinfo=UTC), x=r[1], y=r[2], speed_kmh=r[3]) for r in rows])


# --- whitelist -----------------------------------------------------------------------------------------


def mask_name(name: str | None) -> str | None:
    """«Иванов Иван Иванович» -> «И****в И. И.»: enough to tell people apart, not to identify them."""
    if not name:
        return name
    surname, *rest = name.split()
    masked = surname[0] + "*" * max(1, len(surname) - 2) + (surname[-1] if len(surname) > 1 else "")
    return " ".join([masked, *(f"{p[0]}." for p in rest)])


@router.get("/whitelist", response_model=WhitelistPage)
def list_whitelist(
    db: DB,
    user: Annotated[Principal, Depends(require("map:view"))],
    kind: Annotated[str | None, Query(pattern="^(card|plate)$")] = None,
    q: Annotated[str | None, Query(description="Поиск по номеру пропуска / гос. номеру")] = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WhitelistPage:
    """Пропуска и номера. ФИО видны только с правом `people:view_pii` (охрана, админ), остальным — маскированные."""
    now = datetime.now(UTC)
    is_active = or_(m.WhitelistEntry.valid_to.is_(None), m.WhitelistEntry.valid_to > now)
    query = select(m.WhitelistEntry)
    if kind:
        query = query.where(m.WhitelistEntry.kind == kind)
    if q:
        query = query.where(m.WhitelistEntry.value.ilike(f"%{q}%"))
    if active is not None:
        query = query.where(is_active if active else ~is_active)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    pii = user.can("people:view_pii")
    rows = db.scalars(query.order_by(m.WhitelistEntry.kind, m.WhitelistEntry.value).limit(limit).offset(offset))
    items = [WhitelistEntry(id=e.id, kind=e.kind, value=e.value,
                            holder_name=e.holder_name if pii or e.kind == "plate" else mask_name(e.holder_name),
                            holder_org=e.holder_org, allowed_building_ids=e.allowed_building_ids,
                            schedule_id=e.schedule_id, valid_from=e.valid_from, valid_to=e.valid_to,
                            active=e.valid_to is None or e.valid_to > now)
             for e in rows]
    return WhitelistPage(items=items, total=total)
