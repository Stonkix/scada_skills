from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from scada_common import SensorType, catalog
from scada_db import models as m
from sqlalchemy import select

from app.auth.security import Principal
from app.deps import CH, DB, require
from app.predict import model
from app.predict.schemas import ForecastPoint, Maintenance, PredictResponse, Prediction

router = APIRouter(tags=["analytics"])
CanView = Annotated[Principal, Depends(require("sensors:view"))]

HISTORY = timedelta(hours=24)
SERVICE_INTERVAL_KM = 10_000  # regulation used for the "до ТО" estimate
DEFAULT_METRIC = {SensorType.CLIMATE: "temperature_c", SensorType.GNSS: "fuel_pct"}
RISK_ORDER = {"critical": 0, "warning": 1, "watch": 2, "ok": 3}


def _bounds(db: DB, sensor_ids: list[str], metric: str) -> dict[str, model.Bounds]:
    rows = db.scalars(select(m.Threshold).where(m.Threshold.sensor_id.in_(sensor_ids), m.Threshold.metric == metric,
                                                m.Threshold.valid_to.is_(None)))
    return {t.sensor_id: model.Bounds(t.min, t.max, t.critical_min, t.critical_max) for t in rows}


def _series(ch: CH, sensor_ids: list[str], metric: str, now: datetime) -> dict[str, list[tuple[datetime, float]]]:
    rows = ch.query("""
        SELECT sensor_id, bucket, sum(sum_v) / sum(cnt) FROM telemetry_1m
        WHERE sensor_id IN {ids:Array(String)} AND metric = {m:String} AND bucket >= {since:DateTime}
        GROUP BY sensor_id, bucket ORDER BY sensor_id, bucket""",
                    parameters={"ids": sensor_ids, "m": metric, "since": now - HISTORY}).result_rows
    out: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for sid, bucket, avg in rows:
        out[sid].append((bucket.replace(tzinfo=UTC), avg))
    return out


def _prediction(sensor: m.Sensor, metric: str, points, bounds: model.Bounds, now: datetime) -> Prediction | None:
    f = model.forecast(points, bounds, now)
    if f is None:
        return None
    spec = next((x for t in catalog.SENSOR_TYPES if t["id"] == sensor.type for x in t["metrics"] if x["key"] == metric), {})
    return Prediction(
        sensor_id=sensor.id, name=sensor.name, metric=metric, unit=spec.get("unit"), generated_at=now,
        samples=f.samples, current=round(f.current, 3), baseline_mean=f.baseline_mean, baseline_std=f.baseline_std,
        zscore=None if f.zscore is None else round(f.zscore, 2), anomaly=f.anomaly,
        trend_per_hour=None if f.trend_per_hour is None else round(f.trend_per_hour, 4),
        trend_r2=None if f.trend_r2 is None else round(f.trend_r2, 3), direction=f.direction,
        eta_warning_h=None if f.eta_warning_h is None else round(f.eta_warning_h, 2),
        eta_critical_h=None if f.eta_critical_h is None else round(f.eta_critical_h, 2),
        bound_warning=f.bound_warning, bound_critical=f.bound_critical, risk=f.risk,
        summary=model.summary(f, spec.get("name", metric), spec.get("unit")),
        forecast=[ForecastPoint(ts=t, value=round(v, 3)) for t, v in f.projection],
    )


def _maintenance(db: DB, ch: CH, sensor: m.Sensor, now: datetime) -> Maintenance | None:
    vehicle = db.get(m.Vehicle, sensor.vehicle_id) if sensor.vehicle_id else None
    if vehicle is None:
        return None
    rows = ch.query("""
        SELECT argMax(metrics['odometer_km'], ts), max(metrics['odometer_km']) - min(metrics['odometer_km']),
               dateDiff('second', min(ts), max(ts))
        FROM telemetry WHERE sensor_id = {s:String} AND ts >= {since:DateTime64(3)} AND mapContains(metrics, 'odometer_km')""",
                    parameters={"s": sensor.id, "since": now - HISTORY}).result_rows
    if not rows or not rows[0][0]:
        return None
    odo, driven, span_s = rows[0]
    next_km = (odo // SERVICE_INTERVAL_KM + 1) * SERVICE_INTERVAL_KM
    remaining = next_km - odo
    per_day = driven / span_s * 86400 if span_s and span_s >= 1800 and driven > 0 else None  # need ≥30 min of track
    eta = remaining / per_day if per_day else None
    text = f"ТО каждые {SERVICE_INTERVAL_KM:,} км: осталось {remaining:,.0f} км".replace(",", " ")
    if eta is not None:
        text += f", при текущем пробеге ~{per_day:.0f} км/сут — через {eta:.0f} сут"
    return Maintenance(vehicle_id=vehicle.id, plate=vehicle.plate, odometer_km=round(odo, 1),
                       service_interval_km=SERVICE_INTERVAL_KM, next_service_km=next_km, remaining_km=round(remaining, 1),
                       km_per_day=None if per_day is None else round(per_day, 1),
                       eta_days=None if eta is None else round(eta, 1), summary=text)


@router.get("/predict/{sensor_id}", response_model=PredictResponse)
def predict_sensor(
    sensor_id: str, db: DB, ch: CH, _: CanView,
    metric: Annotated[str | None, Query(description="По умолчанию: температура для климата, топливо для трекеров")] = None,
) -> PredictResponse:
    """Прогноз по метрике датчика (тренд, аномалия, время до выхода за пороги); для машин — ещё и до ТО."""
    sensor = db.get(m.Sensor, sensor_id)
    if sensor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sensor {sensor_id} not found")
    numeric = [k for k in catalog.numeric_metrics(sensor.type) if k not in ("engine_on", "detected", "granted")]
    metric = metric or DEFAULT_METRIC.get(SensorType(sensor.type)) or (numeric[0] if numeric else None)
    if metric is None:
        return PredictResponse()
    if metric not in numeric:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Metric {metric} is not forecastable; use {numeric}")
    now = datetime.now(UTC)
    points = _series(ch, [sensor_id], metric, now).get(sensor_id, [])
    bounds = _bounds(db, [sensor_id], metric).get(sensor_id, model.Bounds())
    return PredictResponse(prediction=_prediction(sensor, metric, points, bounds, now),
                           maintenance=_maintenance(db, ch, sensor, now) if sensor.type == SensorType.GNSS else None)


@router.get("/predict", response_model=list[Prediction])
def predict_all(
    db: DB, ch: CH, _: CanView,
    sensor_type: SensorType = SensorType.CLIMATE,
    risk_at_least: Annotated[str, Query(pattern="^(ok|watch|warning|critical)$")] = "watch",
) -> list[Prediction]:
    """Риски по всем датчикам типа: сначала самые срочные. По умолчанию — только требующие внимания."""
    metric = DEFAULT_METRIC.get(sensor_type)
    if metric is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"No forecast metric for {sensor_type}")
    sensors = {s.id: s for s in db.scalars(select(m.Sensor).where(m.Sensor.type == sensor_type, m.Sensor.enabled))}
    if not sensors:
        return []
    now = datetime.now(UTC)
    series = _series(ch, list(sensors), metric, now)
    bounds = _bounds(db, list(sensors), metric)
    out = [p for sid, pts in series.items()
           if (p := _prediction(sensors[sid], metric, pts, bounds.get(sid, model.Bounds()), now))
           and RISK_ORDER[p.risk] <= RISK_ORDER[risk_at_least]]
    return sorted(out, key=lambda p: (RISK_ORDER[p.risk], p.eta_critical_h if p.eta_critical_h is not None else 1e9,
                                      p.eta_warning_h if p.eta_warning_h is not None else 1e9))
