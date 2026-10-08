"""Everything the worker needs from Postgres, cached in memory and refreshed periodically.

Rules, thresholds, whitelists and the plan change rarely; reading them per event
would put Postgres on the hot path of every sensor message.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from geoalchemy2.shape import to_shape
from scada_common import SensorType
from scada_db import models as m
from scada_db.postgres import engine
from shapely import STRtree
from shapely.geometry import Point, Polygon
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Threshold:
    metric: str
    version: int
    nominal: float | None
    min: float | None
    max: float | None
    critical_min: float | None
    critical_max: float | None


@dataclass(frozen=True)
class SensorRef:
    id: str
    type: SensorType
    name: str
    building_id: str | None
    zone_id: str | None
    is_mobile: bool
    vehicle_id: str | None
    thresholds: dict[str, Threshold] = field(default_factory=dict)


@dataclass(frozen=True)
class VehicleRef:
    id: str
    plate: str
    kind: str


@dataclass(frozen=True)
class ZoneRef:
    id: str
    name: str
    zone_type: str
    polygon: Polygon


@dataclass(frozen=True)
class Rule:
    id: str
    version: int
    name: str
    kind: str
    severity: str
    params: dict[str, Any]
    schedule_id: str | None
    escalate_after_s: int | None


@dataclass(frozen=True)
class Schedule:
    id: str
    timezone: str
    intervals: list[dict]


@dataclass(frozen=True)
class Pass:
    allowed_building_ids: tuple[str, ...] | None
    schedule_id: str | None


@dataclass
class Snapshot:
    sensors: dict[str, SensorRef]
    vehicles: dict[str, VehicleRef]
    building_types: dict[str, str]
    building_names: dict[str, str]
    geozones: list[ZoneRef]
    rules: dict[str, Rule]  # current enabled version per rule id
    schedules: dict[str, Schedule]
    cards: dict[str, Pass]  # active passes only
    expired_cards: set[str]
    plates: set[str]  # active plates
    usernames: dict[int, str]
    _tree: STRtree | None = None

    def __post_init__(self) -> None:
        self._tree = STRtree([z.polygon for z in self.geozones]) if self.geozones else None

    def zones_at(self, x: float, y: float) -> list[ZoneRef]:
        """Outdoor geozones covering a point, smallest first (the first is the most specific)."""
        if self._tree is None:
            return []
        hits = [self.geozones[i] for i in self._tree.query(Point(x, y), predicate="covered_by")]
        return sorted(hits, key=lambda z: z.polygon.area)

    def rules_of(self, kind: str) -> list[Rule]:
        return [r for r in self.rules.values() if r.kind == kind]


def load(now: datetime) -> Snapshot:
    with Session(engine()) as s:
        thresholds: dict[str, dict[str, Threshold]] = {}
        for t in s.scalars(select(m.Threshold).where(m.Threshold.valid_to.is_(None))):
            thresholds.setdefault(t.sensor_id, {})[t.metric] = Threshold(
                t.metric, t.version, t.nominal, t.min, t.max, t.critical_min, t.critical_max)
        sensors = {x.id: SensorRef(x.id, SensorType(x.type), x.name, x.building_id, x.zone_id, x.is_mobile,
                                   x.vehicle_id, thresholds.get(x.id, {}))
                   for x in s.scalars(select(m.Sensor).where(m.Sensor.enabled))}
        vehicles = {v.id: VehicleRef(v.id, v.plate, v.kind) for v in s.scalars(select(m.Vehicle))}
        buildings = list(s.scalars(select(m.Building)))
        geozones = [ZoneRef(z.id, z.name, z.zone_type, to_shape(z.geom))
                    for z in s.scalars(select(m.Zone).where(m.Zone.kind == "geozone"))]
        rules = {r.id: Rule(r.id, r.version, r.name, r.kind, r.severity, r.params or {}, r.schedule_id,
                            r.escalate_after_s)
                 for r in s.scalars(select(m.AlertRule).where(m.AlertRule.is_current, m.AlertRule.enabled))}
        schedules = {x.id: Schedule(x.id, x.timezone, x.intervals) for x in s.scalars(select(m.Schedule))}
        entries = s.scalars(select(m.WhitelistEntry).where(m.WhitelistEntry.valid_from <= now)).all()
        live = {e.id for e in entries if e.valid_to is None or e.valid_to > now}
        cards = {e.value: Pass(tuple(e.allowed_building_ids) if e.allowed_building_ids else None, e.schedule_id)
                 for e in entries if e.kind == "card" and e.id in live}
        expired = {e.value for e in entries if e.kind == "card" and e.id not in live} - set(cards)
        plates = {e.value for e in entries if e.kind == "plate" and e.id in live}
        usernames = {u.id: u.username for u in s.scalars(select(m.User))}
    return Snapshot(sensors, vehicles, {b.id: b.building_type for b in buildings}, {b.id: b.name for b in buildings},
                    geozones, rules, schedules, cards, expired, plates, usernames)
