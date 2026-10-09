"""Analytics over ClickHouse: KPIs, heatmap, replay."""

from datetime import UTC, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from scada_common import SensorType, catalog, keys
from scada_db import models as m
from sqlalchemy import select

from app.alerts.router import to_model
from app.auth.security import Principal
from app.deps import CH, DB, redis_sync, require
from app.kpi.schemas import (
    AlertsKpi,
    BuildingTraffic,
    GateKpi,
    HeatCell,
    Heatmap,
    HourCount,
    KpiResponse,
    Replay,
    ReplayTrack,
    RuleCount,
    VehicleKpi,
)
from app.registry import repo
from app.registry.router import FromQ, ToQ, period
from app.registry.schemas import RoutePoint

router = APIRouter(tags=["analytics"])
CanView = Annotated[Principal, Depends(require("kpi:view"))]
# trackers report by exception (scada_common.catalog.REPORTING): a fix stands for the time until the next one,
# capped so that a tracker silent for an hour does not count as an hour of anything
MAX_FIX_SPAN_S = catalog.REPORTING[SensorType.GNSS].offline_after_s or 300


def _utc(dt):
    return dt.replace(tzinfo=UTC)


@router.get("/kpi", response_model=KpiResponse)
def kpi(db: DB, ch: CH, _: CanView, start: FromQ = None, end: ToQ = None) -> KpiResponse:
    """Показатели за период (по умолчанию — последний час)."""
    start, end = period(start, end, max_days=31)
    p = {"from": start, "to": end}
    trackers = {v.sensor_id: v for v in repo.vehicles(db)}

    rows = ch.query("""
        SELECT sensor_id, max(odo) - min(odo) AS km,
               sumIf(span, speed > 1) AS moving,
               sumIf(span, speed <= 1 AND engine = 1) AS idle,
               sumIf(span, speed <= 1 AND engine = 0) AS stopped
        FROM (
            SELECT sensor_id, odo, speed, engine,
                   if(isNull(next_ts), 0, least(dateDiff('millisecond', ts, assumeNotNull(next_ts)) / 1000, {cap:UInt32})) AS span
            FROM (
                SELECT sensor_id, ts, metrics['odometer_km'] AS odo, metrics['speed_kmh'] AS speed,
                       metrics['engine_on'] AS engine,
                       leadInFrame(toNullable(ts)) OVER (PARTITION BY sensor_id ORDER BY ts
                                                         ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) AS next_ts
                FROM telemetry
                WHERE type = 'gnss' AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
            )
        )
        GROUP BY sensor_id""", parameters=p | {"cap": MAX_FIX_SPAN_S}).result_rows
    vehicles = []
    for sid, km, moving, idle, stopped in rows:
        if sid not in trackers:
            continue
        on_site = moving + idle + stopped
        vehicles.append(VehicleKpi(
            vehicle_id=trackers[sid].id, plate=trackers[sid].plate, mileage_km=round(km or 0, 2),
            moving_min=round(moving / 60, 1), idle_min=round(idle / 60, 1), stopped_min=round(stopped / 60, 1),
            utilization_pct=round(100 * moving / on_site, 1) if on_site else 0.0))
    vehicles.sort(key=lambda v: v.vehicle_id)

    span_h = (end - start).total_seconds() / 3600
    bucket_min = 5 if span_h <= 3 else 60 if span_h <= 48 else 1440  # ~10-50 bars whatever the period
    gates = list(db.scalars(select(m.Zone.id).where(m.Zone.zone_type == "gate"))) or [""]  # every site's КПП
    by_hour = ch.query("""
        SELECT toStartOfInterval(ts, toIntervalMinute({b:UInt32})) h, countIf(transition = 'enter'),
               countIf(transition = 'exit') FROM zone_events
        WHERE zone_id IN {zones:Array(String)} AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
        GROUP BY h ORDER BY h""", parameters=p | {"zones": gates, "b": bucket_min}).result_rows
    plates = ch.query("""
        SELECT count() FROM telemetry WHERE type = 'anpr_camera' AND zone_id IN {zones:Array(String)}
          AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}""", parameters=p | {"zones": gates}).result_rows
    gate = GateKpi(entries=sum(r[1] for r in by_hour), exits=sum(r[2] for r in by_hour), plates_recognized=plates[0][0],
                   bucket_minutes=bucket_min,
                   by_hour=[HourCount(hour=_utc(h), entries=e, exits=x) for h, e, x in by_hour])

    names = dict(db.execute(select(m.Building.id, m.Building.name)).all())
    entries = dict(ch.query("""
        SELECT zone_id, count() FROM zone_events
        WHERE transition = 'enter' AND vehicle_id IS NULL AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
        GROUP BY zone_id""", parameters=p).result_rows)
    people = redis_sync().mget([keys.zone_people(b) for b in names]) if names else []
    buildings = [BuildingTraffic(building_id=b, name=n, entries=entries.get(b, 0), people_now=int(c or 0))
                 for (b, n), c in zip(names.items(), people)]

    sev = dict(ch.query("""SELECT severity, count() FROM alerts_log WHERE transition = 'opened'
                           AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)} GROUP BY severity""",
                        parameters=p).result_rows)
    top = ch.query("""SELECT rule_id, count() c FROM alerts_log WHERE transition = 'opened'
                      AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)} GROUP BY rule_id ORDER BY c DESC LIMIT 10""",
                   parameters=p).result_rows
    ack = ch.query("""
        SELECT avg(dateDiff('millisecond', opened, acked)) / 1000 FROM (
            SELECT alert_id, minIf(ts, transition = 'opened') opened, minIf(ts, transition = 'ack') acked
            FROM alerts_log GROUP BY alert_id HAVING countIf(transition = 'opened') > 0 AND countIf(transition = 'ack') > 0
               AND opened BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)})""", parameters=p).result_rows
    mean_ack = ack[0][0] if ack and ack[0][0] == ack[0][0] else None  # NaN when there were no acks
    open_now = sum(1 for _ in redis_sync().scan_iter(keys.alert_open("*", "*"), count=500))
    alerts = AlertsKpi(opened=sum(sev.values()), by_severity=sev, open_now=open_now,
                       top_rules=[RuleCount(rule_id=r, count=c) for r, c in top],
                       mean_ack_s=round(mean_ack, 1) if mean_ack is not None else None)
    return KpiResponse(period_from=start, period_to=end, vehicles=vehicles, gate=gate, buildings=buildings, alerts=alerts)


@router.get("/heatmap", response_model=Heatmap)
def heatmap(
    ch: CH,
    _: CanView,
    start: FromQ = None,
    end: ToQ = None,
    cell: Annotated[float, Query(ge=2, le=500, description="Размер ячейки, м (сотни метров — обзор всего региона)")] = 10,
    source: Annotated[Literal["vehicles", "stops"], Query(description="vehicles — все позиции; stops — где стоят")] = "vehicles",
) -> Heatmap:
    """Загруженность территории по ГЛОНАСС: сколько замеров попало в каждую ячейку сетки."""
    start, end = period(start, end, max_days=31)
    stops = "AND metrics['speed_kmh'] <= 1" if source == "stops" else ""
    rows = ch.query(f"""
        SELECT floor(x / {{c:Float64}}) cx, floor(y / {{c:Float64}}) cy, count() n FROM telemetry
        WHERE type = 'gnss' AND x IS NOT NULL AND x >= 0 AND y >= 0 {stops}
          AND ts BETWEEN {{from:DateTime64(3)}} AND {{to:DateTime64(3)}}
        GROUP BY cx, cy""", parameters={"c": cell, "from": start, "to": end}).result_rows
    cells = [HeatCell(x=(cx + 0.5) * cell, y=(cy + 0.5) * cell, count=n) for cx, cy, n in rows]
    return Heatmap(cell_m=cell, source=source, max_count=max((c.count for c in cells), default=0), cells=cells)


@router.get("/replay", response_model=Replay)
def replay(
    db: DB,
    ch: CH,
    _: CanView,
    start: FromQ = None,
    end: ToQ = None,
    step: Annotated[int, Query(ge=1, le=600, description="Шаг выборки, с")] = 5,
) -> Replay:
    """Треки всех машин за окно (до 6 ч) с шагом `step` и тревоги этого окна — для таймлайна воспроизведения."""
    start, end = period(start, end, max_days=1)
    end = min(end, start + timedelta(hours=6))
    trackers = {v.sensor_id: v.id for v in repo.vehicles(db)}
    rows = ch.query("""
        SELECT sensor_id, toStartOfInterval(ts, toIntervalSecond({step:UInt32})) t, argMax(x, ts), argMax(y, ts),
               argMax(metrics['speed_kmh'], ts)
        FROM telemetry WHERE type = 'gnss' AND x IS NOT NULL AND ts BETWEEN {from:DateTime64(3)} AND {to:DateTime64(3)}
        GROUP BY sensor_id, t ORDER BY sensor_id, t""", parameters={"from": start, "to": end, "step": step}).result_rows
    tracks: dict[str, list[RoutePoint]] = {}
    for sid, t, x, y, speed in rows:
        if sid in trackers:
            tracks.setdefault(trackers[sid], []).append(RoutePoint(ts=_utc(t), x=x, y=y, speed_kmh=speed))
    alerts = db.scalars(select(m.Alert).where(m.Alert.opened_at.between(start, end)).order_by(m.Alert.opened_at))
    return Replay(period_from=start, period_to=end, step_s=step,
                  tracks=[ReplayTrack(vehicle_id=v, points=p) for v, p in sorted(tracks.items())],
                  alerts=[to_model(db, a) for a in alerts])
