"""The simulated enterprise: vehicles on missions, people through turnstiles, climate and motion.

One tick per second. Agents only *generate device messages*; deciding whether
something is an alert is the worker's job. Scenarios change behaviour through
time-limited `Effect`s that agents consult, so normal life resumes on expiry.
"""

import math
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from scada_common.geo import heading_deg

from scada_simulator.roads import Node, Road, RoadGraph
from scada_simulator.transport import Outgoing
from scada_simulator.world import SensorSpec, World

GATE: Node = (0.0, 250.0)
OFF_SITE: Node = (-60.0, 250.0)  # public road beyond КПП-1
PARKING: Node = (100.0, 110.0)  # end of the parking spur, inside z-parking
GNSS_EVERY_S = 2
CLIMATE_EVERY_S = 15
MOTION_EVERY_S = 15


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class Effect:
    kind: str  # breakdown | speed | climate_drift | motion_alarm | silence
    target: str
    until: float
    params: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)

    def active(self, t: float) -> bool:
        return t < self.until


# --- vehicles -------------------------------------------------------------------------------------


@dataclass
class Task:
    kind: str  # drive | dwell | event | park
    target: Node | None = None
    seconds: float = 0.0
    engine: bool = False
    emit: tuple[str, str] | None = None  # (camera sensor id, "approach"|"leave")


@dataclass
class VehicleAgent:
    id: str
    plate: str
    kind: str
    sensor_id: str
    x: float
    y: float
    cruise_kmh: float
    fuel_pct: float
    odometer_km: float
    parked_off_site: bool = False  # v-truck-8 waits beyond the gate until its scenario
    speed_kmh: float = 0.0
    heading: float = 90.0
    engine_on: bool = False
    tasks: list[Task] = field(default_factory=list)
    path: list[tuple[Node, float]] = field(default_factory=list)
    dwell_left: float = 0.0


class Simulation:
    def __init__(self, world: World, seed: int = 1) -> None:
        self.world = world
        self.graph = RoadGraph(world.roads + [_approach_road()])
        self.rnd = random.Random(seed)
        self.effects: list[Effect] = []
        self.tick_no = 0
        self.vehicles = [self._spawn(v, i) for i, v in enumerate(world.vehicles.values())]
        self.inside: dict[str, set[str]] = {b: set() for b, a in world.areas.items() if a.kind == "building"}
        self._docks = [n for n in self.graph.nodes if math.isclose(n[1], 290) and 200 < n[0] < 760]  # inside z-docks-*
        self._yard = [n for n in self.graph.nodes if 0 < n[0] <= 800 and 0 <= n[1] <= 500]

    # --- setup ------------------------------------------------------------------------------------

    def _spawn(self, v, i: int) -> VehicleAgent:
        cruise = {"truck": 17, "loader": 10, "car": 18}.get(v.kind, 15)
        # trucks start beyond the gate and arrive staggered, so every entry passes the ANPR camera
        start = OFF_SITE if v.kind == "truck" else self.rnd.choice([n for n in self.graph.nodes if n[0] > 0])
        fuel = 17.0 if v.id == "v-truck-5" else self.rnd.uniform(40, 95)  # one truck runs low on fuel
        agent = VehicleAgent(v.id, v.plate, v.kind, v.sensor_id, *start, cruise_kmh=cruise, fuel_pct=fuel,
                             odometer_km=self.rnd.uniform(5_000, 250_000), parked_off_site=v.id == "v-truck-8")
        agent.dwell_left = self.rnd.uniform(0, 150) if v.kind == "truck" else 0
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

    # --- missions ---------------------------------------------------------------------------------

    def _plan(self, v: VehicleAgent) -> list[Task]:
        r = self.rnd
        if v.parked_off_site:
            return [Task("dwell", seconds=30)]
        if v.kind == "truck":
            dock = r.choice(self._docks)
            wh = {290: 1, 470: 2, 650: 3}.get(int(dock[0]))
            cam = f"cam-dock-wh{wh}" if wh else None
            tasks = [Task("drive", GATE), Task("event", emit=("cam-gate-in", "approach")),
                     Task("drive", dock), *([Task("event", emit=(cam, "approach"))] if cam else []),
                     Task("dwell", seconds=r.uniform(60, 180), engine=False),
                     *([Task("event", emit=(cam, "leave"))] if cam else [])]
            if r.random() < 0.3:  # sometimes waits on the truck parking before leaving
                tasks += [Task("drive", PARKING), Task("dwell", seconds=r.uniform(60, 240))]
            return tasks + [Task("drive", GATE), Task("event", emit=("cam-gate-out", "leave")),
                            Task("drive", OFF_SITE), Task("dwell", seconds=r.uniform(30, 120))]
        if v.kind == "loader":
            return [Task("drive", r.choice(self._docks)), Task("dwell", seconds=r.uniform(20, 60), engine=True)]
        return [Task("drive", r.choice(self._yard)), Task("dwell", seconds=r.uniform(10, 40), engine=True)]

    def _step_vehicle(self, v: VehicleAgent, dt: float, out: list[Outgoing]) -> None:
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
            elif task.kind == "event" and task.emit and task.emit[0] in self.world.sensors:
                out.append(anpr(task.emit[0], v.plate, task.emit[1]))
                return
        boost = self.effect("speed", v.id)
        budget = dt
        while v.path and budget > 0:
            (tx, ty), limit = v.path[0]
            kmh = boost.params["kmh"] if boost else min(v.cruise_kmh, limit or v.cruise_kmh)
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
            v.fuel_pct = max(0.0, v.fuel_pct - moved * 0.0008)
        if v.x <= OFF_SITE[0] + 1 and v.fuel_pct < 12:
            v.fuel_pct = self.rnd.uniform(70, 95)  # refuelled outside the site

    def _gnss(self, v: VehicleAgent, t: float) -> Outgoing:
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
        hour = datetime.now().hour
        b = self.rnd.choice(list(self.inside))
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
        detected = bool(alarm) or (occupied and self.rnd.random() < 0.6)
        return Outgoing("motion", s.id, {"device": s.id, "state": "alarm" if detected else "idle",
                                         "ts": datetime.fromtimestamp(t, UTC).isoformat()})

    # --- tick -------------------------------------------------------------------------------------

    def tick(self, dt: float = 1.0) -> list[Outgoing]:
        t = time.time()
        out: list[Outgoing] = []
        for v in self.vehicles:
            self._step_vehicle(v, dt, out)
            if self.tick_no % GNSS_EVERY_S == 0:
                out.append(self._gnss(v, t))
        self._step_people(out)
        # spread periodic sensors over the period instead of a burst every 15 s
        for s in self.world.sensors_of("climate"):
            if (self.tick_no + hash(s.id)) % CLIMATE_EVERY_S == 0:
                out.append(self._climate(s, t))
        for s in self.world.sensors_of("motion"):
            if (self.tick_no + hash(s.id)) % MOTION_EVERY_S == 0:
                out.append(self._motion(s, t))
        self.effects = [e for e in self.effects if e.active(time.monotonic())]
        self.tick_no += 1
        return out

    def status(self) -> dict[str, Any]:
        t = time.monotonic()
        return {
            "tick": self.tick_no,
            "vehicles": [{"id": v.id, "x": round(v.x, 1), "y": round(v.y, 1), "speed_kmh": round(v.speed_kmh, 1),
                          "engine_on": v.engine_on, "fuel_pct": round(v.fuel_pct, 1),
                          "task": v.tasks[0].kind if v.tasks else None} for v in self.vehicles],
            "people_inside": {b: len(p) for b, p in self.inside.items()},
            "effects": [{"kind": e.kind, "target": e.target, "left_s": round(e.until - t)} for e in self.effects],
        }


def _approach_road() -> Road:
    return Road("public-approach", [OFF_SITE, GATE], 40)


def anpr(camera_id: str, plate: str, direction: str) -> Outgoing:
    return Outgoing("anpr", camera_id, {"camera_id": camera_id, "plate": plate, "direction": direction,
                                        "confidence_pct": round(random.uniform(88, 99.5), 1),
                                        "captured_at": now_utc().isoformat()})


def skud(reader_id: str, card: str, event: str, granted: bool) -> Outgoing:
    return Outgoing("skud", reader_id, {"reader_id": reader_id, "card": card, "event": event,
                                        "result": "granted" if granted else "denied",
                                        "time": now_utc().isoformat()})
