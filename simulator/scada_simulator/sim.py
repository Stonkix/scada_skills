"""The simulated enterprise: trucks on trips between sites, people through turnstiles, climate and motion.

One tick per second. Devices report the way real ones are configured (scada_common.catalog.REPORTING):
by time plus "by exception" — a tracker on distance/turn/speed change, a climate logger when the
reading moves by more than its deadband, a motion detector when its state flips.

Agents only *generate device messages* (and the TMS's waybills);
deciding whether something is an alert is the worker's job. Scenarios change behaviour
through time-limited `Effect`s that agents consult, so normal life resumes on expiry.

Trucks run trips between the sites along the public roads of the plan: load at a dock,
leave through the gate camera, drive the highway, enter the destination through its gate,
unload, then the next trip starts from there. Every status change of a trip goes out as a
waybill, the way a transport management system would push it.
"""

import math
import os
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from scada_common import SensorType, catalog
from scada_common.geo import heading_deg

from scada_simulator.roads import Node, RoadGraph
from scada_simulator.transport import Outgoing
from scada_simulator.world import SensorSpec, SiteSpec, World

GNSS = catalog.REPORTING[SensorType.GNSS]
CLIMATE = catalog.REPORTING[SensorType.CLIMATE]
MOTION = catalog.REPORTING[SensorType.MOTION]
CLIMATE_SAMPLE_S = int(CLIMATE.min_interval_s)  # the logger measures this often and decides whether to send
MOTION_SAMPLE_S = 15
SITE_ROAD_MAX_KMH = 40  # roads with a lower limit are site roads: drive at the vehicle's site cruise speed
ETA_FACTOR = 0.85  # ETA assumes a bit below cruise: junctions, traffic
FUEL_PCT_PER_M = 0.00012  # ~30 l/100 km from a 250 l tank
MAIN_SITE = "s-podolsk"
# stopping in these geozones is normal (rule-breakdown allows parking/docks/restricted; the gate is a queue)
STOP_ZONE_TYPES = {"parking", "docks", "restricted", "gate"}
MAX_TIME_SCALE = 30  # demo fast-forward: vehicles drive and dwell this many times faster
SITE_TZ = ZoneInfo(os.environ.get("SITE_TZ", "Europe/Moscow"))  # shifts follow the plant clock, not the container

# cargo by the type of the site it leaves: (cargo, weight range t, temperature mode)
CARGO: dict[str, list[tuple[str, tuple[float, float], str | None]]] = {
    "plant": [("Металлоконструкции", (8, 18), None), ("Готовая продукция: стеллажи", (5, 12), None),
              ("Упаковка и тара", (2, 6), None), ("Комплектующие для сборки", (4, 10), None)],
    "dc": [("Сборный груз для магазинов", (4, 14), None), ("Бытовая химия", (6, 15), None),
           ("Возвратная тара", (1, 4), None)],
    "cold_store": [("Замороженная продукция", (10, 20), "-18…-22 °C"), ("Мясо охлаждённое", (6, 14), "0…+4 °C"),
                   ("Мороженое", (5, 12), "-20…-24 °C")],
}
DRIVERS = ["Смирнов Алексей Викторович", "Кузнецов Дмитрий Сергеевич", "Попов Андрей Николаевич",
           "Васильев Игорь Петрович", "Соколов Михаил Юрьевич", "Морозов Олег Иванович",
           "Новиков Павел Андреевич", "Фёдоров Виктор Олегович", "Волков Роман Евгеньевич"]


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class Effect:
    kind: str  # breakdown | speed | climate_drift | motion_alarm | silence | closed
    target: str
    until: float
    params: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def active(self, t: float) -> bool:
        return t < self.until


# --- sites ----------------------------------------------------------------------------------------


@dataclass
class SiteNet:
    """A site as drivers see it: road-graph nodes to stop at and the cameras that see them."""

    spec: SiteSpec
    gate: Node
    approach: Node
    docks: list[Node]
    parking: list[Node]
    yard: list[Node]
    cam_in: str | None
    cam_out: str | None
    dock_cams: dict[Node, str]

    @property
    def id(self) -> str:
        return self.spec.id


def _inside(n: Node, bounds: tuple[float, float, float, float], margin: float = 0.0) -> bool:
    return bounds[0] + margin <= n[0] <= bounds[2] - margin and bounds[1] + margin <= n[1] <= bounds[3] - margin


# --- vehicles -------------------------------------------------------------------------------------


@dataclass
class Task:
    kind: str  # drive | dwell | event | park | doc | refuel
    target: Node | None = None
    seconds: float = 0.0
    engine: bool = False
    emit: tuple[str | None, str] | None = None  # (camera sensor id, "approach"|"leave")
    status: str | None = None  # doc: the new trip status


@dataclass
class VehicleAgent:
    id: str
    plate: str
    kind: str
    sensor_id: str
    x: float
    y: float
    cruise_kmh: float  # on site roads
    highway_kmh: float  # on public roads, capped by the limit
    fuel_pct: float
    odometer_km: float
    site_id: str | None = None  # the site it is at, or last left
    parked_off_site: bool = False  # v-truck-8 waits beyond the gate until its scenario
    speed_kmh: float = 0.0
    heading: float = 90.0
    engine_on: bool = False
    tasks: list[Task] = field(default_factory=list)
    path: list[tuple[Node, float]] = field(default_factory=list)
    dwell_left: float = 0.0
    trip: dict[str, Any] | None = None  # the waybill being executed
    reported: tuple[float, float, float, float, float, bool] | None = None  # last fix sent: t, x, y, heading, speed, engine


class Simulation:
    def __init__(self, world: World, seed: int = 1) -> None:
        self.world = world
        self.clock = time.time  # device clocks; tests replace it to run faster than real time
        self.time_scale = 1.0  # simulated seconds per tick of the world (the cheat menu fast-forwards)
        self.graph = RoadGraph(world.roads)
        self.rnd = random.Random(seed)
        self.effects: list[Effect] = []
        self.tick_no = 0
        self.outbox: list[Outgoing] = []  # messages produced outside a tick (trips already under way at start)
        self.sites = {s.id: self._site_net(s) for s in world.sites.values()}
        self.main = self.sites.get(MAIN_SITE) or next(iter(self.sites.values()))
        self._route_m: dict[tuple[str, str], float] = {}
        self._waybill_seq = int(time.time()) % 100_000 * 10
        self.inside: dict[str, set[str]] = {b: set() for b, a in world.areas.items() if a.kind == "building"}
        self.last_sent: dict[str, tuple[float, tuple]] = {}  # fixed sensors: t, reading of the last report
        self.vehicles = [self._spawn(v, i) for i, v in enumerate(world.vehicles.values())]

    # --- setup ------------------------------------------------------------------------------------

    def _site_net(self, s: SiteSpec) -> SiteNet:
        zones = [a for a in self.world.areas.values() if a.kind == "geozone" and s.contains(*a.center)]
        yard = [n for n in self.graph.nodes if s.contains(*n)]

        def in_zone(zone_type: str) -> list[Node]:
            # 2 m inside the zone: stopping on its edge would let GNSS noise flip zone membership
            return sorted(n for n in yard if any(z.type == zone_type and _inside(n, z.bounds, 2) for z in zones))

        anpr = self.world.sensors_of("anpr_camera")
        gate_zones = {z.id for z in zones if z.type == "gate"}
        gate_cams = sorted(c.id for c in anpr if c.zone_id in gate_zones)
        cam_in = next((c for c in gate_cams if c.endswith("-in")), gate_cams[0] if gate_cams else None)
        cam_out = next((c for c in gate_cams if c.endswith("-out")), cam_in)
        docks = in_zone("docks")
        dock_cams = {}
        for n in docks:
            zone = next(z for z in zones if z.type == "docks" and _inside(n, z.bounds, 2))
            cam = next((c.id for c in anpr if c.zone_id == zone.id), None)
            if cam:
                dock_cams[n] = cam
        return SiteNet(s, self.graph.nearest(*s.gate), self.graph.nearest(*s.approach), docks, in_zone("parking"),
                       yard, cam_in, cam_out, dock_cams)

    def in_stop_zone(self, x: float, y: float) -> bool:
        return any(a.kind == "geozone" and a.type in STOP_ZONE_TYPES
                   and a.bounds[0] <= x <= a.bounds[2] and a.bounds[1] <= y <= a.bounds[3]
                   for a in self.world.areas.values())

    def site_at(self, x: float, y: float) -> SiteNet | None:
        return next((s for s in self.sites.values() if s.spec.contains(x, y)), None)

    def _spawn(self, v, i: int) -> VehicleAgent:
        site = self.sites.get(v.home_site_id or "") or self.main
        r = self.rnd
        cruise = {"truck": 17, "loader": 10, "car": 18}.get(v.kind, 15)
        fuel = 17.0 if v.id == "v-truck-5" else r.uniform(45, 95)  # one truck runs low on fuel
        parked = v.id == "v-truck-8"
        if parked:
            start = site.approach
        elif v.kind == "truck":
            start = r.choice(site.parking or site.docks or site.yard)
        else:
            start = r.choice(site.docks if v.kind == "loader" and site.docks else site.yard)
        agent = VehicleAgent(v.id, v.plate, v.kind, v.sensor_id, *start, cruise_kmh=cruise,
                             highway_kmh=r.uniform(62, 78) if v.kind == "truck" else 70, fuel_pct=fuel,
                             odometer_km=r.uniform(5_000, 250_000), site_id=site.id, parked_off_site=parked)
        if v.kind == "truck" and not parked:
            if i % 2 == 0 and len(self.sites) > 1:
                self._start_mid_route(agent, site)  # the map is lively from the first second
            else:
                agent.dwell_left = r.uniform(0, 150)
        return agent

    def refresh_sensors(self, sensors: dict[str, SensorSpec]) -> None:
        self.world.sensors = sensors

    # --- effects ----------------------------------------------------------------------------------

    def add_effect(self, kind: str, target: str, duration_s: float, **params) -> Effect:
        effect = Effect(kind, target, time.monotonic() + duration_s, params)
        self.effects.append(effect)
        return effect

    def effect(self, kind: str, target: str) -> Effect | None:
        t = time.monotonic()
        return next((e for e in self.effects if e.kind == kind and e.target == target and e.active(t)), None)

    def silenced(self) -> set[str]:
        t = time.monotonic()
        return {e.target for e in self.effects if e.kind == "silence" and e.active(t)}

    def vehicle(self, vehicle_id: str) -> VehicleAgent:
        return next(v for v in self.vehicles if v.id == vehicle_id)

    # --- trips ------------------------------------------------------------------------------------

    def route_m(self, a: SiteNet, b: SiteNet) -> float:
        key = (a.id, b.id)
        if key not in self._route_m:
            path = self.graph.path(a.gate, b.gate)
            self._route_m[key] = sum(math.dist(p, q) for (p, _), (q, _) in zip(path, path[1:]))
        return self._route_m[key]

    def _new_trip(self, v: VehicleAgent, origin: SiteNet, dest: SiteNet) -> dict[str, Any]:
        cargo, (lo, hi), temp = self.rnd.choice(CARGO.get(origin.spec.site_type, CARGO["plant"]))
        weight = round(self.rnd.uniform(lo, hi), 1)
        self._waybill_seq += 1
        return {"waybill_no": f"ПЛ-{now_utc():%y%m%d}-{self._waybill_seq:06d}", "plate": v.plate,
                "origin_site_id": origin.id, "destination_site_id": dest.id, "cargo": cargo, "weight_t": weight,
                "pallets": round(weight * 1.6), "temperature_mode": temp,
                "driver_name": DRIVERS[sum(map(ord, v.id)) % len(DRIVERS)], "status": "planned",
                "planned_departure": None, "departed_at": None, "eta": None, "arrived_at": None}

    def _dock_event(self, site: SiteNet, dock: Node, direction: str) -> list[Task]:
        cam = site.dock_cams.get(dock)
        return [Task("event", emit=(cam, direction))] if cam else []

    def _departure(self, site: SiteNet) -> tuple[Node, list[Task]]:
        dock = self.rnd.choice(site.docks or site.yard)
        return dock, [Task("drive", dock), *self._dock_event(site, dock, "approach"), Task("doc", status="loading"),
                      Task("dwell", seconds=self.rnd.uniform(60, 180), engine=False),
                      *self._dock_event(site, dock, "leave"), Task("drive", site.gate),
                      Task("event", emit=(site.cam_out, "leave")), Task("doc", status="en_route")]

    def _arrival(self, site: SiteNet) -> list[Task]:
        dock = self.rnd.choice(site.docks or site.yard)
        tasks = [Task("drive", site.gate), Task("event", emit=(site.cam_in, "approach")), Task("drive", dock),
                 *self._dock_event(site, dock, "approach"), Task("doc", status="unloading"),
                 Task("dwell", seconds=self.rnd.uniform(60, 180), engine=False),
                 *self._dock_event(site, dock, "leave"), Task("refuel"), Task("doc", status="done")]
        if site.parking and self.rnd.random() < 0.3:  # sometimes waits on the truck parking before the next trip
            tasks += [Task("drive", self.rnd.choice(site.parking)), Task("dwell", seconds=self.rnd.uniform(60, 240))]
        return tasks

    def _plan_trip(self, v: VehicleAgent) -> list[Task]:
        origin = self.sites.get(v.site_id or "") or self.main
        others = [s for s in self.sites.values() if s.id != origin.id]
        if not others:  # a single-site plan: shuttle between docks
            dock = self.rnd.choice(origin.docks or origin.yard)
            return [Task("drive", dock), Task("dwell", seconds=self.rnd.uniform(60, 180))]
        dest = self.rnd.choice(others)
        v.trip = self._new_trip(v, origin, dest)
        _, departure = self._departure(origin)
        return departure + self._arrival(dest)

    def _start_mid_route(self, v: VehicleAgent, origin: SiteNet) -> None:
        """Put a truck somewhere on its highway with a trip already under way."""
        others = [s for s in self.sites.values() if s.id != origin.id]
        dest = self.rnd.choice(others)
        v.trip = self._new_trip(v, origin, dest)
        path = self.graph.path(origin.gate, dest.gate)
        k = int(len(path) * self.rnd.uniform(0.15, 0.85))
        (v.x, v.y), v.path, v.site_id = path[k][0], path[k + 1:], None
        v.engine_on, v.speed_kmh = True, 0.0
        total = self.route_m(origin, dest)
        done_m = sum(math.dist(p, q) for (p, _), (q, _) in zip(path[:k + 1], path[1:k + 1]))
        eta_h = (total - done_m) / 1000 / (v.highway_kmh * ETA_FACTOR) / self.time_scale
        now = now_utc()
        v.trip |= {"status": "en_route", "departed_at": now - timedelta(hours=done_m / 1000 / (v.highway_kmh * ETA_FACTOR)),
                   "eta": now + timedelta(hours=eta_h)}
        v.tasks = self._arrival(dest)[1:]  # the drive to the gate is already in v.path
        self.outbox.append(self._waybill(v))

    def _waybill(self, v: VehicleAgent) -> Outgoing:
        doc = {k: (val.isoformat() if isinstance(val, datetime) else val) for k, val in (v.trip or {}).items()}
        return Outgoing("waybill", doc["waybill_no"], doc)

    def _trip_status(self, v: VehicleAgent, status: str) -> Outgoing | None:
        if v.trip is None:
            return None
        now = now_utc()
        v.trip["status"] = status
        if status == "loading":
            v.trip["planned_departure"] = now + timedelta(minutes=3 / self.time_scale)
        elif status == "en_route":
            origin, dest = self.sites[v.trip["origin_site_id"]], self.sites[v.trip["destination_site_id"]]
            v.trip["departed_at"] = now
            hours = self.route_m(origin, dest) / 1000 / (v.highway_kmh * ETA_FACTOR) / self.time_scale
            v.trip["eta"] = now + timedelta(hours=hours)
            v.site_id = None
        elif status == "unloading":
            v.trip["arrived_at"] = now
            v.site_id = v.trip["destination_site_id"]
        out = self._waybill(v)
        if status == "done":
            v.trip = None
        return out

    # --- missions ---------------------------------------------------------------------------------

    def _plan(self, v: VehicleAgent) -> list[Task]:
        r = self.rnd
        site = self.sites.get(v.site_id or "") or self.main
        if v.parked_off_site:
            return [Task("dwell", seconds=30)]
        if v.kind == "truck":
            return self._plan_trip(v)
        if v.kind == "loader":
            return [Task("drive", r.choice(site.docks or site.yard)), Task("dwell", seconds=r.uniform(20, 60), engine=True)]
        return [Task("drive", r.choice(site.yard)), Task("dwell", seconds=r.uniform(10, 40), engine=True)]

    def _speed_on(self, v: VehicleAgent, limit: float) -> float:
        if not limit:
            return v.cruise_kmh
        return min(v.cruise_kmh, limit) if limit <= SITE_ROAD_MAX_KMH else min(v.highway_kmh, limit)

    def _step_vehicle(self, v: VehicleAgent, dt: float, out: list[Outgoing]) -> None:
        pending = self.effect("breakdown_next", v.id)  # armed by the scenario: break down once out on a roadway
        if pending and v.speed_kmh > 0.5 and self.site_at(v.x, v.y) and not self.in_stop_zone(v.x, v.y):
            self.effects.remove(pending)
            self.add_effect("breakdown", v.id, pending.params["break_s"])
        if self.effect("breakdown", v.id):
            v.speed_kmh, v.engine_on, v.path = 0.0, False, []
            return
        if v.dwell_left > 0:
            v.dwell_left -= dt
            v.speed_kmh = 0.0
            return
        if not v.path:
            if not v.tasks:
                v.tasks = self._plan(v)
            task = v.tasks.pop(0)
            if task.kind == "drive":
                v.path = self.graph.path((v.x, v.y), task.target)[1:]
                v.engine_on = True
            elif task.kind == "dwell":
                v.dwell_left, v.engine_on, v.speed_kmh = task.seconds, task.engine, 0.0
                return
            elif task.kind == "park":
                v.parked_off_site, v.engine_on, v.speed_kmh = True, False, 0.0
                return
            elif task.kind == "event":
                if task.emit and task.emit[0] in self.world.sensors:
                    out.append(anpr(task.emit[0], v.plate, task.emit[1]))
                return
            elif task.kind == "doc":
                if msg := self._trip_status(v, task.status or "planned"):
                    out.append(msg)
                return
            elif task.kind == "refuel":
                if v.fuel_pct < 30:
                    v.fuel_pct = self.rnd.uniform(85, 95)
                return
        boost = self.effect("speed", v.id)
        budget = dt
        while v.path and budget > 0:
            (tx, ty), limit = v.path[0]
            kmh = boost.params["kmh"] if boost else self._speed_on(v, limit)
            step = kmh / 3.6 * budget
            dist = math.dist((v.x, v.y), (tx, ty))
            if dist > 1e-6:
                v.heading = heading_deg(tx - v.x, ty - v.y)
            if dist <= step:
                v.x, v.y = tx, ty
                v.path.pop(0)
                budget -= dist / (kmh / 3.6)
            else:
                v.x += (tx - v.x) / dist * step
                v.y += (ty - v.y) / dist * step
                budget = 0
            v.speed_kmh = kmh
            moved = min(dist, step)
            v.odometer_km += moved / 1000
            v.fuel_pct = max(0.0, v.fuel_pct - moved * FUEL_PCT_PER_M)

    def _gnss_due(self, v: VehicleAgent, t: float) -> bool:
        """Tracker logic: a fix on period (moving / idling / parked) or on distance, turn, speed change, ignition."""
        if v.reported is None:
            return True
        t0, x0, y0, h0, s0, engine0 = v.reported
        if v.engine_on != engine0:
            return True
        moving = v.speed_kmh > 0.5
        period = GNSS.moving_period_s if moving else GNSS.idle_period_s if v.engine_on else GNSS.heartbeat_s
        if t - t0 >= (period or 0):
            return True
        ch = GNSS.on_change
        turned = abs((v.heading - h0 + 180) % 360 - 180)
        return (math.dist((v.x, v.y), (x0, y0)) >= ch["distance_m"] or (moving and turned >= ch["heading_deg"])
                or abs(v.speed_kmh - s0) >= ch["speed_kmh"])

    def _gnss(self, v: VehicleAgent, t: float) -> Outgoing:
        v.reported = (t, v.x, v.y, v.heading, v.speed_kmh, v.engine_on)
        lat, lon = self.world.georef.to_wgs84(v.x + self.rnd.gauss(0, 0.4), v.y + self.rnd.gauss(0, 0.4))
        return Outgoing("gnss", v.sensor_id, {
            "device_id": v.sensor_id, "lat": round(lat, 7), "lon": round(lon, 7),
            "speed": round(v.speed_kmh + (self.rnd.uniform(-0.6, 0.6) if v.speed_kmh else 0), 1),
            "course": round(v.heading, 1), "ignition": int(v.engine_on), "fuel_pct": round(v.fuel_pct, 1),
            "odometer_m": round(v.odometer_km * 1000), "fix_time": round(t, 3)})

    # --- people -----------------------------------------------------------------------------------

    def _target_occupancy(self, building_id: str, hour: int) -> int:
        base = {"office": 40, "warehouse": 12, "production": 25, "garage": 5}
        day = 7 <= hour < 21
        return round(base.get(self.world.areas[building_id].type, 5) * (1 if day else 0.15))

    def _step_people(self, out: list[Outgoing]) -> None:
        """At most one turnstile pass per tick; occupancy drifts towards a time-of-day target."""
        hour = datetime.now(SITE_TZ).hour
        b = self.rnd.choice(list(self.inside))
        if self.effect("closed", b):
            return
        reader = self.world.entrance(b)
        people = self.inside[b]
        target = self._target_occupancy(b, hour)
        if reader is None or (len(people) == target and self.rnd.random() < 0.7):
            return
        if len(people) < target:
            entering = self.rnd.random() < 0.85
        elif len(people) > target + 3:
            entering = False
        else:
            entering = self.rnd.random() < 0.5
        if entering:
            busy = set().union(*self.inside.values())
            pool = [c for c, allowed in self.world.valid_cards if c not in busy and (allowed is None or b in allowed)]
            if not pool:
                return
            card = self.rnd.choice(pool)
            people.add(card)
            out.append(skud(reader.id, card, "entry", True))
        elif people:
            card = self.rnd.choice(sorted(people))
            people.discard(card)
            out.append(skud(reader.id, card, "exit", True))

    # --- fixed sensors ----------------------------------------------------------------------------

    def _due(self, sensor_id: str, t: float, reading: tuple, changed) -> bool:
        """Fixed sensors: report on heartbeat or when `changed(previous, reading)`."""
        last = self.last_sent.get(sensor_id)
        policy = CLIMATE if sensor_id in self.world.sensors and self.world.sensors[sensor_id].type == "climate" else MOTION
        if last is None or t - last[0] >= (policy.heartbeat_s or 0) or changed(last[1], reading):
            self.last_sent[sensor_id] = (t, reading)
            return True
        return False

    def _climate(self, s: SensorSpec, t: float) -> Outgoing:
        phase = (hash(s.id) % 1000) / 1000 * 2 * math.pi
        temp = s.nominal.get("temperature_c", 18) + 0.8 * math.sin(t / 900 + phase) + self.rnd.gauss(0, 0.12)
        if drift := self.effect("climate_drift", s.id):
            ramp = min(1.0, (time.monotonic() - drift.started) / drift.params.get("ramp_s", 120))
            temp += drift.params["delta_c"] * ramp
        rh = s.nominal.get("humidity_pct", 55) + 4 * math.sin(t / 1300 + phase) + self.rnd.gauss(0, 0.4)
        fahrenheit = s.id.startswith("clim-prod")  # these units are configured in °F; connectors normalize
        return Outgoing("climate", s.id, {
            "device": s.id, "temperature": round(temp * 9 / 5 + 32 if fahrenheit else temp, 2),
            "unit": "F" if fahrenheit else "C", "humidity": round(min(100, max(0, rh)), 1), "ts_ms": int(t * 1000)})

    def _motion(self, s: SensorSpec, t: float) -> Outgoing:
        alarm = self.effect("motion_alarm", s.id)
        occupied = bool(self.inside.get(s.building_id or "", ()))
        detected = bool(alarm) or (occupied and self.rnd.random() < 0.9)
        return Outgoing("motion", s.id, {"device": s.id, "state": "alarm" if detected else "idle",
                                         "ts": datetime.fromtimestamp(t, UTC).isoformat()})

    # --- tick -------------------------------------------------------------------------------------

    def set_time_scale(self, scale: float) -> float:
        self.time_scale = min(MAX_TIME_SCALE, max(1.0, float(scale)))
        return self.time_scale

    def tick(self, dt: float | None = None) -> list[Outgoing]:
        """One real second of the world: vehicles advance `time_scale` simulated seconds."""
        dt = self.time_scale if dt is None else dt
        t = self.clock()
        out, self.outbox = self.outbox, []
        for v in self.vehicles:
            self._step_vehicle(v, dt, out)
            if self._gnss_due(v, t):
                out.append(self._gnss(v, t))
        self._step_people(out)
        # sensors measure on their own staggered clocks and send only what is worth sending
        deadband = CLIMATE.on_change
        for s in self.world.sensors_of("climate"):
            if (self.tick_no + hash(s.id)) % CLIMATE_SAMPLE_S == 0:
                msg = self._climate(s, t)
                p = msg.payload
                temp_c = (p["temperature"] - 32) * 5 / 9 if p["unit"] == "F" else p["temperature"]
                if self._due(s.id, t, (temp_c, p["humidity"]), lambda a, b: abs(a[0] - b[0]) >= deadband["temperature_c"]
                             or abs(a[1] - b[1]) >= deadband["humidity_pct"]):
                    out.append(msg)
        for s in self.world.sensors_of("motion"):
            if (self.tick_no + hash(s.id)) % MOTION_SAMPLE_S == 0:
                msg = self._motion(s, t)
                if self._due(s.id, t, (msg.payload["state"],), lambda a, b: a != b):
                    out.append(msg)
        self.effects = [e for e in self.effects if e.active(time.monotonic())]
        self.tick_no += 1
        return out

    def status(self) -> dict[str, Any]:
        t = time.monotonic()
        return {
            "tick": self.tick_no,
            "time_scale": self.time_scale,
            "vehicles": [{"id": v.id, "x": round(v.x, 1), "y": round(v.y, 1), "speed_kmh": round(v.speed_kmh, 1),
                          "engine_on": v.engine_on, "fuel_pct": round(v.fuel_pct, 1), "site_id": v.site_id,
                          "trip": v.trip and {"waybill_no": v.trip["waybill_no"], "status": v.trip["status"],
                                              "to": v.trip["destination_site_id"]},
                          "task": v.tasks[0].kind if v.tasks else None} for v in self.vehicles],
            "people_inside": {b: len(p) for b, p in self.inside.items()},
            "effects": [{"kind": e.kind, "target": e.target, "left_s": round(e.until - t)} for e in self.effects],
        }


def anpr(camera_id: str, plate: str, direction: str) -> Outgoing:
    return Outgoing("anpr", camera_id, {"camera_id": camera_id, "plate": plate, "direction": direction,
                                        "confidence_pct": round(random.uniform(88, 99.5), 1),
                                        "captured_at": now_utc().isoformat()})


def skud(reader_id: str, card: str, event: str, granted: bool) -> Outgoing:
    return Outgoing("skud", reader_id, {"reader_id": reader_id, "card": card, "event": event,
                                        "result": "granted" if granted else "denied",
                                        "time": now_utc().isoformat()})
