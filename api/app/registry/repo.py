"""Postgres rows -> API models for the registry."""

from collections import defaultdict
from datetime import UTC, datetime

from geoalchemy2.shape import from_shape, to_shape
from scada_common import Geo, SensorType, catalog
from scada_db import models as m
from shapely.geometry import Point
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.registry.schemas import Sensor, SensorCreate, SensorTypeInfo, Threshold, Vehicle


def sensor_types() -> list[SensorTypeInfo]:
    return [SensorTypeInfo.model_validate(t | {"reporting": catalog.REPORTING[t["id"]]}) for t in catalog.SENSOR_TYPES]


def _current_thresholds(db: Session, sensor_ids: list[str] | None = None) -> dict[str, list[Threshold]]:
    q = select(m.Threshold).where(m.Threshold.valid_to.is_(None)).order_by(m.Threshold.metric)
    if sensor_ids is not None:
        q = q.where(m.Threshold.sensor_id.in_(sensor_ids))
    out: dict[str, list[Threshold]] = defaultdict(list)
    for t in db.scalars(q):
        out[t.sensor_id].append(Threshold(metric=t.metric, nominal=t.nominal, min=t.min, max=t.max,
                                          critical_min=t.critical_min, critical_max=t.critical_max, version=t.version))
    return out


def to_sensor(row: m.Sensor, thresholds: list[Threshold]) -> Sensor:
    pt = to_shape(row.geom) if row.geom is not None else None
    return Sensor(id=row.id, type=row.type, name=row.name, description=row.description, building_id=row.building_id,
                  zone_id=row.zone_id, floor=row.floor, geo=Geo(x=pt.x, y=pt.y, floor=row.floor) if pt else None,
                  vehicle_id=row.vehicle_id, thresholds=thresholds, enabled=row.enabled, is_mobile=row.is_mobile,
                  created_at=row.created_at)


def sensors(db: Session, *, sensor_type: str | None = None, building_id: str | None = None,
            ids: list[str] | None = None) -> list[Sensor]:
    q = select(m.Sensor).order_by(m.Sensor.id)
    if sensor_type:
        q = q.where(m.Sensor.type == sensor_type)
    if building_id:
        q = q.where(m.Sensor.building_id == building_id)
    if ids is not None:
        q = q.where(m.Sensor.id.in_(ids))
    rows = db.scalars(q).all()
    th = _current_thresholds(db, [r.id for r in rows] if (sensor_type or building_id or ids) else None)
    return [to_sensor(r, th.get(r.id, [])) for r in rows]


def vehicles(db: Session) -> list[Vehicle]:
    trackers = dict(db.execute(select(m.Sensor.vehicle_id, m.Sensor.id).where(m.Sensor.vehicle_id.isnot(None))).all())
    return [Vehicle(id=v.id, plate=v.plate, kind=v.kind, model=v.model, carrier=v.carrier, home_site_id=v.home_site_id,
                    sensor_id=trackers.get(v.id, catalog.gnss_sensor_id(v.id)))
            for v in db.scalars(select(m.Vehicle).where(m.Vehicle.is_active).order_by(m.Vehicle.id))]


def is_mobile(sensor_type: SensorType) -> bool:
    return next(t["is_mobile"] for t in catalog.SENSOR_TYPES if t["id"] == sensor_type)


def insert_sensor(db: Session, data: SensorCreate) -> None:
    """Add a sensor and its thresholds (typical ones from the catalogue if none are given). No commit."""
    db.add(m.Sensor(id=data.id, type=data.type, name=data.name, description=data.description,
                    is_mobile=is_mobile(data.type), building_id=data.building_id, zone_id=data.zone_id,
                    floor=data.floor, vehicle_id=data.vehicle_id, enabled=data.enabled,
                    geom=from_shape(Point(data.geo.x, data.geo.y), srid=m.SRID) if data.geo else None))
    thresholds = [t.model_dump(exclude={"version"}) for t in data.thresholds] or \
        catalog.default_thresholds(data.type, data.id)
    db.add_all(m.Threshold(sensor_id=data.id, version=1, **t) for t in thresholds)


def replace_thresholds(db: Session, sensor_id: str, new: list[Threshold]) -> None:
    """Close current versions and add the given ones as the next version per metric. No commit."""
    now = datetime.now(UTC)
    db.execute(update(m.Threshold).where(m.Threshold.sensor_id == sensor_id, m.Threshold.valid_to.is_(None))
               .values(valid_to=now))
    db.flush()
    for t in new:
        last = db.scalar(select(m.Threshold.version).where(m.Threshold.sensor_id == sensor_id,
                                                           m.Threshold.metric == t.metric)
                         .order_by(m.Threshold.version.desc()).limit(1))
        db.add(m.Threshold(sensor_id=sensor_id, metric=t.metric, nominal=t.nominal, min=t.min, max=t.max,
                           critical_min=t.critical_min, critical_max=t.critical_max,
                           version=(last or 0) + 1, valid_from=now))
