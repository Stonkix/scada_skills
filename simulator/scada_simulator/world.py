"""What the simulator knows about the enterprise, read from the registry (Postgres).

Sensors are re-read periodically, so a sensor added through the API or the plan
editor starts producing data without restarting the simulator.
"""

from dataclasses import dataclass, field

from geoalchemy2.shape import to_shape
from scada_common.geo import Georef
from scada_db import models as m
from scada_db.postgres import engine
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from scada_simulator.roads import Road


@dataclass
class SensorSpec:
    id: str
    type: str
    building_id: str | None
    zone_id: str | None
    x: float | None
    y: float | None
    nominal: dict[str, float] = field(default_factory=dict)  # metric -> nominal value


@dataclass
class VehicleSpec:
    id: str
    plate: str
    kind: str
    sensor_id: str
    home_site_id: str | None = None


@dataclass
class SiteSpec:
    id: str
    name: str
    site_type: str
    bounds: tuple[float, float, float, float]
    gate: tuple[float, float]  # where the site road meets the access road (КПП)
    approach: tuple[float, float]  # outer end of the access road, on the way to public roads

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        x0, y0, x1, y1 = self.bounds
        return x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin


def sites_from_layout(layout: dict) -> dict[str, SiteSpec]:
    sites = {}
    for f in layout["features"]:
        p = f["properties"]
        if p["kind"] != "site":
            continue
        ring = f["geometry"]["coordinates"][0]
        xs, ys = [c[0] for c in ring], [c[1] for c in ring]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        gate = tuple(p.get("gate") or (x0, (y0 + y1) / 2))
        approach = tuple(p.get("approach") or (gate[0] - 60, gate[1]))
        sites[f["id"]] = SiteSpec(f["id"], p["name"], p.get("site_type", "plant"), (x0, y0, x1, y1), gate, approach)
    return sites


@dataclass
class Area:
    id: str
    kind: str  # building | room | geozone
    type: str
    bounds: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds
        return (x0 + x1) / 2, (y0 + y1) / 2


@dataclass
class World:
    georef: Georef
    sites: dict[str, SiteSpec]
    roads: list[Road]
    areas: dict[str, Area]
    sensors: dict[str, SensorSpec]
    vehicles: dict[str, VehicleSpec]
    valid_cards: list[tuple[str, list[str] | None]]  # card id, allowed buildings (None = all)
    expired_cards: list[str]
    unknown_cards: list[str]

    def sensors_of(self, sensor_type: str) -> list[SensorSpec]:
        return [s for s in self.sensors.values() if s.type == sensor_type]

    def entrance(self, building_id: str) -> SensorSpec | None:
        """The access-control reader that counts people for a building (its zone_id is the building)."""
        return next((s for s in self.sensors_of("access_control") if s.zone_id == building_id), None)


def _load_sensors(s: Session) -> dict[str, SensorSpec]:
    sensors = {}
    for row in s.scalars(select(m.Sensor).where(m.Sensor.enabled)):
        pt = to_shape(row.geom) if row.geom is not None else None
        sensors[row.id] = SensorSpec(row.id, row.type, row.building_id, row.zone_id,
                                     pt.x if pt else None, pt.y if pt else None)
    for th in s.scalars(select(m.Threshold).where(m.Threshold.valid_to.is_(None), m.Threshold.nominal.isnot(None))):
        if th.sensor_id in sensors:
            sensors[th.sensor_id].nominal[th.metric] = th.nominal
    return sensors


def load_sensors() -> dict[str, SensorSpec]:
    with Session(engine()) as s:
        return _load_sensors(s)


def load_world() -> World:
    with Session(engine()) as s:
        layout = s.scalars(select(m.Layout.geojson).order_by(m.Layout.version.desc()).limit(1)).one()
        roads = [Road(r.id, [(c[0], c[1]) for c in to_shape(r.geom).coords], r.speed_limit_kmh)
                 for r in s.scalars(select(m.Road))]
        areas = {b.id: Area(b.id, "building", b.building_type, to_shape(b.geom).bounds) for b in s.scalars(select(m.Building))}
        areas |= {z.id: Area(z.id, z.kind, z.zone_type, to_shape(z.geom).bounds) for z in s.scalars(select(m.Zone))}
        trackers = {row.vehicle_id: row.id for row in s.scalars(select(m.Sensor).where(m.Sensor.vehicle_id.isnot(None)))}
        vehicles = {v.id: VehicleSpec(v.id, v.plate, v.kind, trackers[v.id], v.home_site_id)
                    for v in s.scalars(select(m.Vehicle).where(m.Vehicle.is_active)) if v.id in trackers}
        cards = s.execute(text("SELECT value, allowed_building_ids, (valid_to IS NULL OR valid_to > now()) AS active "
                               "FROM whitelist WHERE kind = 'card' ORDER BY value")).all()
        known = {c.value for c in cards}
        return World(
            georef=Georef.from_layout(layout), sites=sites_from_layout(layout), roads=roads, areas=areas, sensors=_load_sensors(s), vehicles=vehicles,
            valid_cards=[(c.value, c.allowed_building_ids) for c in cards if c.active],
            expired_cards=[c.value for c in cards if not c.active],
            unknown_cards=[f"P-{n:06d}" for n in range(290, 300) if f"P-{n:06d}" not in known],
        )
