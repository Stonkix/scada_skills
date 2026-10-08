import csv
import io
import json
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from scada_common.catalog import numeric_metrics

from app.mock import world
from app.registry.schemas import (
    BulkResult,
    BulkRowError,
    Sensor,
    SensorCreate,
    SensorHistory,
    Vehicle,
    VehicleRoute,
)

router = APIRouter(tags=["registry"])

FromQ = Annotated[datetime | None, Query(alias="from", description="По умолчанию — час назад")]
ToQ = Annotated[datetime | None, Query(alias="to", description="По умолчанию — сейчас")]


def _get_sensor(sensor_id: str) -> Sensor:
    if sensor_id not in world.sensors:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sensor {sensor_id} not found")
    return world.sensors[sensor_id]


def _period(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    end = end or datetime.now().astimezone()
    start = start or end - timedelta(hours=1)
    if start >= end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "'from' must be before 'to'")
    return start, end


@router.get("/sensors", response_model=list[Sensor])
def list_sensors() -> list[Sensor]:
    return list(world.sensors.values())


@router.get("/sensors/{sensor_id}", response_model=Sensor)
def get_sensor(sensor_id: str) -> Sensor:
    return _get_sensor(sensor_id)


@router.post("/sensors", response_model=Sensor, status_code=status.HTTP_201_CREATED)
def create_sensor(body: SensorCreate) -> Sensor:
    if body.id in world.sensors:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Sensor {body.id} already exists")
    return world.add_sensor(body)


@router.post("/sensors/bulk", response_model=BulkResult)
async def bulk_create_sensors(
    file: UploadFile,
    dry_run: Annotated[bool, Query(description="Только проверить, ничего не создавать")] = True,
) -> BulkResult:
    """CSV (колонки как у SensorCreate, `thresholds` — JSON-строка) или JSON-массив.

    Применяется всё или ничего: при любой ошибке не создаётся ни один датчик.
    """
    raw = (await file.read()).decode("utf-8-sig")
    try:
        if (file.filename or "").lower().endswith(".json"):
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise ValueError("JSON must be an array of sensors")
        else:
            rows = [{k: v for k, v in r.items() if v not in ("", None)} for r in csv.DictReader(io.StringIO(raw))]
            for r in rows:
                if "thresholds" in r:
                    r["thresholds"] = json.loads(r["thresholds"])
                if "x" in r and "y" in r:
                    r["geo"] = {"x": r.pop("x"), "y": r.pop("y"), "floor": r.get("floor")}
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Cannot parse file: {e}") from e

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
        if s.id in world.sensors or s.id in seen:
            errors.append(BulkRowError(row=i, field="id", message=f"Duplicate sensor id {s.id}"))
            continue
        seen.add(s.id)
        valid.append(s)

    created = 0
    if not dry_run and not errors:
        for s in valid:
            world.add_sensor(s)
        created = len(valid)
    return BulkResult(dry_run=dry_run, total=len(rows), valid=len(valid), created=created, errors=errors)


@router.get("/sensors/{sensor_id}/history", response_model=SensorHistory)
def sensor_history(
    sensor_id: str,
    metric: Annotated[str, Query(description="Числовая метрика типа датчика, напр. temperature_c")],
    start: FromQ = None,
    end: ToQ = None,
) -> SensorHistory:
    """Шаг выбирается по длине периода: до 1 ч — raw, до суток — 1m, дальше — 1h."""
    sensor = _get_sensor(sensor_id)
    if metric not in numeric_metrics(sensor.type):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Metric {metric} is not numeric for {sensor.type}; use {numeric_metrics(sensor.type)}")
    start, end = _period(start, end)
    step, points = world.history(sensor, metric, start, end)
    return SensorHistory(sensor_id=sensor_id, metric=metric, step=step, points=points)


@router.get("/vehicles", response_model=list[Vehicle])
def list_vehicles() -> list[Vehicle]:
    return list(world.vehicles.values())


@router.get("/vehicles/{vehicle_id}/route", response_model=VehicleRoute)
def vehicle_route(vehicle_id: str, start: FromQ = None, end: ToQ = None) -> VehicleRoute:
    if vehicle_id not in world.vehicles:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Vehicle {vehicle_id} not found")
    start, end = _period(start, end)
    return VehicleRoute(vehicle_id=vehicle_id, points=world.route(vehicle_id, start, end))
