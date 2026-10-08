"""Apply a saved plan (GeoJSON, format of deploy/seed/layout.geojson) to the registry tables.

The plan is the source of truth for geometry: buildings, rooms, geozones, roads,
checkpoints and the position of fixed sensors. Saving it upserts all of them;
plan objects that disappeared are deleted. A sensor that was on the previous
plan and is gone from the new one is disabled (not deleted: its history and
alerts stay meaningful); sensors registered through the API without being drawn
on the plan are left alone.
"""

from typing import Any, Literal

from geoalchemy2.shape import from_shape
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from scada_common import SensorType, catalog
from scada_db import models as m
from shapely.geometry import shape
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

Kind = Literal["site", "building", "room", "road", "checkpoint", "geozone", "sensor"]
GEOMETRY_OF = {"site": "Polygon", "building": "Polygon", "room": "Polygon", "geozone": "Polygon",
               "road": "LineString", "checkpoint": "Point", "sensor": "Point"}


class LayoutError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:20]))
        self.errors = errors


class _Props(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: Kind
    name: str = Field(..., min_length=1)


class _Feature(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    geometry: dict[str, Any]
    properties: _Props

    @model_validator(mode="after")
    def geometry_matches_kind(self) -> "_Feature":
        want = GEOMETRY_OF[self.properties.kind]
        if self.geometry.get("type") != want:
            raise ValueError(f"{self.properties.kind} needs {want}, got {self.geometry.get('type')}")
        if not shape(self.geometry).is_valid:
            raise ValueError("invalid geometry (self-intersection or too few points)")
        return self


def _validate(geojson: dict) -> list[_Feature]:
    if geojson.get("type") != "FeatureCollection" or "georef" not in geojson.get("metadata", {}):
        raise LayoutError(["must be a FeatureCollection with metadata.georef and metadata.extent"])
    errors, features = [], []
    for i, raw in enumerate(geojson.get("features", [])):
        try:
            features.append(_Feature.model_validate(raw))
        except ValidationError as e:
            errors += [f"feature {raw.get('id', i)}: {err['msg']}" for err in e.errors(include_url=False)]
    ids = [f.id for f in features]
    errors += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    buildings = {f.id for f in features if f.properties.kind == "building"}
    areas = buildings | {f.id for f in features if f.properties.kind in ("room", "geozone")}
    for f in features:
        p = f.properties.model_dump()
        if f.properties.kind in ("room", "sensor") and p.get("building_id") and p["building_id"] not in buildings:
            errors.append(f"{f.id}: unknown building {p['building_id']}")
        if f.properties.kind == "room" and not p.get("building_id"):
            errors.append(f"{f.id}: a room needs building_id")
        if f.properties.kind in ("sensor", "checkpoint") and p.get("zone_id") and p["zone_id"] not in areas:
            errors.append(f"{f.id}: unknown zone {p['zone_id']}")
        if f.properties.kind == "sensor":
            st = p.get("sensor_type")
            if st not in SensorType.__members__.values():
                errors.append(f"{f.id}: unknown sensor_type {st}")
            elif next(t for t in catalog.SENSOR_TYPES if t["id"] == st)["is_mobile"]:
                errors.append(f"{f.id}: {st} sensors are mobile and are not placed on the plan")
    if errors:
        raise LayoutError(errors)
    return features


def sensor_ids(geojson: dict) -> set[str]:
    return {f["id"] for f in geojson.get("features", []) if f.get("properties", {}).get("kind") == "sensor"}


def apply(db: Session, geojson: dict, previous: dict | None = None) -> dict[str, int]:
    """Validate and write the plan into the registry tables. Caller commits."""
    features = _validate(geojson)
    of = lambda kind: [f for f in features if f.properties.kind == kind]  # noqa: E731
    geom = lambda f: from_shape(shape(f.geometry), srid=m.SRID)  # noqa: E731

    sensors = {s.id: s for s in db.scalars(select(m.Sensor).where(m.Sensor.is_mobile.is_(False)))}
    for f in of("sensor"):
        if f.id in sensors and sensors[f.id].type != f.properties.model_dump()["sensor_type"]:
            raise LayoutError([f"{f.id}: sensor type cannot change ({sensors[f.id].type} on the plan is "
                               f"{f.properties.model_dump()['sensor_type']}); add a new sensor instead"])

    # geometry tables: upsert by id, delete what is gone (sensors keep working: building_id -> NULL)
    for model, kind, fields in ((m.Building, "building", ("building_type", "floors")),
                                (m.Road, "road", ("width_m", "speed_limit_kmh")),
                                (m.Checkpoint, "checkpoint", ("checkpoint_type", "zone_id"))):
        rows = of(kind)
        for f in rows:
            p = f.properties.model_dump()
            db.merge(model(id=f.id, name=p["name"], geom=geom(f), **{k: p.get(k) for k in fields}))
        db.execute(delete(model).where(model.id.notin_([f.id for f in rows] or [""])))
    zones = of("room") + of("geozone")
    for f in zones:
        p = f.properties.model_dump()
        db.merge(m.Zone(id=f.id, name=p["name"], kind=p["kind"], zone_type=p.get("room_type") or p.get("zone_type"),
                        building_id=p.get("building_id"), floor=p.get("floor"),
                        speed_limit_kmh=p.get("speed_limit_kmh"), geom=geom(f)))
    db.execute(delete(m.Zone).where(m.Zone.id.notin_([f.id for f in zones] or [""])))
    db.flush()

    created = 0
    placed = of("sensor")
    for f in placed:
        p = f.properties.model_dump()
        values = {"name": p["name"], "building_id": p.get("building_id"), "zone_id": p.get("zone_id"),
                  "floor": p.get("floor"), "geom": geom(f), "enabled": True}
        if f.id in sensors:
            for k, v in values.items():
                setattr(sensors[f.id], k, v)
        else:
            db.add(m.Sensor(id=f.id, type=p["sensor_type"], is_mobile=False, **values))
            db.add_all(m.Threshold(sensor_id=f.id, version=1, **t)
                       for t in catalog.default_thresholds(SensorType(p["sensor_type"]), f.id))
            created += 1
    gone = sorted((sensor_ids(previous or {}) - {f.id for f in placed}) & set(sensors))
    if gone:
        db.execute(update(m.Sensor).where(m.Sensor.id.in_(gone)).values(enabled=False))
    return {"buildings": len(of("building")), "zones": len(zones), "roads": len(of("road")),
            "checkpoints": len(of("checkpoint")), "sensors": len(placed), "sensors_created": created,
            "sensors_disabled": len(gone)}


def lock(db: Session) -> None:
    """Serialize plan saves: the version check and the write must not interleave."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext('scada.layout'))"))
