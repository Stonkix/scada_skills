"""Scenario engine: YAML steps -> actions that start time-limited effects in the simulation."""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scada_simulator.sim import Simulation, Task, anpr, skud
from scada_simulator.transport import Outgoing


class ScenarioError(ValueError):
    pass


@dataclass
class Scenario:
    name: str
    title: str
    description: str
    steps: list[dict[str, Any]]


@dataclass
class Run:
    id: str
    scenario: str
    step: int = 0
    done: bool = False
    error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


Action = Callable[..., list[Outgoing]]
ACTIONS: dict[str, Action] = {}


def action(fn: Action) -> Action:
    ACTIONS[fn.__name__] = fn
    return fn


def _in_stop_zone(sim: Simulation, x: float, y: float) -> bool:
    return sim.in_stop_zone(x, y)


def _vehicle(sim: Simulation, vehicle_id: str | None, kind: str | tuple[str, ...] = "truck",
             on_roadway: bool = False):
    """The named vehicle, or a random one moving on a site; kinds are tried in order of preference."""
    if vehicle_id:
        try:
            return sim.vehicle(vehicle_id)
        except StopIteration:
            raise ScenarioError(f"unknown vehicle {vehicle_id}") from None
    kinds = (kind,) if isinstance(kind, str) else kind
    for k in kinds:
        moving = [v for v in sim.vehicles if v.kind == k and v.speed_kmh > 0 and sim.site_at(v.x, v.y)
                  and not (on_roadway and _in_stop_zone(sim, v.x, v.y))]
        if moving:
            return sim.rnd.choice(moving)
    raise ScenarioError(f"no moving {' or '.join(kinds)} on site right now")


def _sensor(sim: Simulation, sensor_id: str, sensor_type: str) -> None:
    spec = sim.world.sensors.get(sensor_id)
    if spec is None or spec.type != sensor_type:
        raise ScenarioError(f"{sensor_id} is not a registered {sensor_type} sensor")


@action
def vehicle_breakdown(sim: Simulation, duration_s: float, vehicle: str | None = None) -> list[Outgoing]:
    # trucks spend most of their time on highways: a loader or a service car will do on site
    try:
        v = _vehicle(sim, vehicle, kind=("truck", "loader", "car"), on_roadway=True)
    except ScenarioError:
        if vehicle:
            raise
        # nobody is driving on a site right now: the service car or a loader standing on a roadway breaks down
        # where it is; if they all stand at docks/parkings, arm one to break down once it is out on a roadway
        spare = sorted((x for x in sim.vehicles if x.kind in ("car", "loader") and sim.site_at(x.x, x.y)),
                       key=lambda x: (_in_stop_zone(sim, x.x, x.y), x.kind != "car", x.id))
        if not spare:
            raise
        if not duration_s:  # duration 0 is the runner's dry run
            return []
        if _in_stop_zone(sim, spare[0].x, spare[0].y):
            sim.add_effect("breakdown_next", spare[0].id, duration_s + 300, break_s=duration_s)
        else:
            sim.add_effect("breakdown", spare[0].id, duration_s)
        return []
    if sim.site_at(v.x, v.y) is None:  # a breakdown on a public road is not our alarm
        raise ScenarioError(f"{v.id} is not on site right now; omit 'vehicle' to pick a moving truck")
    if _in_stop_zone(sim, v.x, v.y):
        raise ScenarioError(f"{v.id} is at a dock/parking where stopping is normal; omit 'vehicle'")
    sim.add_effect("breakdown", v.id, duration_s)
    return []


@action
def vehicle_speeding(sim: Simulation, kmh: float, duration_s: float, vehicle: str | None = None) -> list[Outgoing]:
    v = _vehicle(sim, vehicle, kind="car")
    sim.add_effect("speed", v.id, duration_s, kmh=kmh)
    site = sim.site_at(v.x, v.y)
    if not v.path and site:  # standing: drive off to the far end of the yard right now
        target = max(site.yard, key=lambda n: (n[0] - v.x) ** 2 + (n[1] - v.y) ** 2)
        v.dwell_left, v.tasks, v.engine_on = 0, [], True
        v.path = sim.graph.path((v.x, v.y), target)[1:]
    return []


@action
def vehicle_arrival(sim: Simulation, vehicle: str) -> list[Outgoing]:
    """Send a vehicle that waits beyond the gate of the main plant through КПП-1 to a dock and back out."""
    v = _vehicle(sim, vehicle)
    site = sim.main
    v.parked_off_site = False
    v.x, v.y = site.gate  # already at the barrier: the camera sees it on the next tick
    v.path, v.dwell_left, v.trip = [], 0, None
    dock = sim.rnd.choice(site.docks)
    v.tasks = [Task("event", emit=(site.cam_in, "approach")), Task("drive", dock),
               Task("dwell", seconds=90), Task("drive", site.gate), Task("event", emit=(site.cam_out, "leave")),
               Task("drive", site.approach), Task("park")]  # back to waiting beyond the gate
    return []


@action
def climate_drift(sim: Simulation, sensor: str, delta_c: float, duration_s: float, ramp_s: float = 0) -> list[Outgoing]:
    _sensor(sim, sensor, "climate")
    sim.add_effect("climate_drift", sensor, duration_s, delta_c=delta_c, ramp_s=ramp_s)
    return [] if ramp_s else [sim.report_now(sensor)]  # a jump is reported at once


@action
def sensor_offline(sim: Simulation, sensor: str, duration_s: float) -> list[Outgoing]:
    if sensor not in sim.world.sensors:
        raise ScenarioError(f"unknown sensor {sensor}")
    sim.add_effect("silence", sensor, duration_s)
    return []


@action
def motion_alarm(sim: Simulation, sensor: str, duration_s: float) -> list[Outgoing]:
    _sensor(sim, sensor, "motion")
    sim.add_effect("motion_alarm", sensor, duration_s)
    return [sim.report_now(sensor)]


@action
def evacuate(sim: Simulation, building: str, duration_s: float) -> list[Outgoing]:
    """Everyone leaves through the turnstile and nobody enters for duration_s."""
    reader = sim.world.entrance(building)
    if reader is None or building not in sim.inside:
        raise ScenarioError(f"{building} has no access-control entrance")
    sim.add_effect("closed", building, duration_s)
    out = [skud(reader.id, card, "exit", True) for card in sorted(sim.inside[building])]
    sim.inside[building].clear()
    return out


@action
def card_swipe(sim: Simulation, reader: str, card: str, granted: bool, event: str = "entry") -> list[Outgoing]:
    """card: an id, or 'unknown' / 'expired' to pick one from the demo gaps in the whitelist."""
    _sensor(sim, reader, "access_control")
    pool = {"unknown": sim.world.unknown_cards, "expired": sim.world.expired_cards}.get(card)
    if pool is not None:
        if not pool:
            raise ScenarioError(f"no {card} cards in the whitelist seed")
        card = sim.rnd.choice(pool)
    return [skud(reader, card, event, granted)]


@action
def plate_at_gate(sim: Simulation, camera: str | None = None) -> list[Outgoing]:
    """A car with a plate nobody knows drives up to a gate camera (any site's КПП when camera is omitted)."""
    cams = [s.cam_in for s in sim.sites.values() if s.cam_in]
    camera = camera or sim.rnd.choice(cams)
    _sensor(sim, camera, "anpr_camera")
    letters = "АВЕКМНОРСТУХ"
    r = sim.rnd
    plate = f"{r.choice(letters)}{r.randint(100, 999)}{r.choice(letters)}{r.choice(letters)}{r.choice(['199', '799', '977'])}"
    return [anpr(camera, plate, "approach")]


# --- random incidents ("chaos"): one now and then, anywhere, while it is switched on ----------------------


def random_incident(sim: Simulation) -> tuple[str, list[Outgoing]]:
    """Pick an incident whose alarm shows at once, on a random site; returns (what happened, messages)."""
    r = sim.rnd
    kinds = ["overheat", "breakdown", "plate", "card", "intrusion", "speeding"]
    r.shuffle(kinds)
    for kind in kinds:
        try:
            if kind == "overheat":
                s = r.choice(sim.world.sensors_of("climate"))
                return f"Перегрев: {s.id}", climate_drift(sim, s.id, delta_c=14, duration_s=r.uniform(150, 300))
            if kind == "breakdown":
                out = vehicle_breakdown(sim, duration_s=r.uniform(150, 300))
                return f"Поломка: {sim.effects[-1].target}", out
            if kind == "plate":
                out = plate_at_gate(sim)
                return f"Номер вне базы: {out[0].payload['plate']}", out
            if kind == "card":
                reader = r.choice(sim.world.sensors_of("access_control")).id
                return f"Чужой пропуск: {reader}", card_swipe(sim, reader, r.choice(["unknown", "expired"]), r.random() < 0.5)
            if kind == "intrusion":
                motion = [s for s in sim.world.sensors_of("motion") if s.building_id and sim.world.entrance(s.building_id)
                          and sim.world.areas[s.building_id].type in ("warehouse", "production")]
                s = r.choice(motion)
                out = evacuate(sim, s.building_id, duration_s=300) + motion_alarm(sim, s.id, duration_s=150)
                return f"Движение в пустом здании: {s.building_id}", out
            if kind == "speeding":
                return "Превышение скорости", vehicle_speeding(sim, kmh=r.uniform(36, 48), duration_s=45)
        except (ScenarioError, IndexError, StopIteration):
            continue  # this one is not possible right now: try another kind
    return "нет подходящей аварии", []


def load(path: Path) -> dict[str, Scenario]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = {}
    for name, spec in data["scenarios"].items():
        for step in spec["steps"]:
            if "wait_s" not in step and step.get("action") not in ACTIONS:
                raise ScenarioError(f"{name}: unknown action {step.get('action')!r}")
        scenarios[name] = Scenario(name, spec["title"], spec.get("description", "").strip(), spec["steps"])
    return scenarios


class Runner:
    def __init__(self, sim: Simulation, send: Callable[[list[Outgoing]], Any]) -> None:
        self.sim = sim
        self.send = send
        self.runs: dict[str, Run] = {}
        self.chaos: asyncio.Task | None = None
        self.chaos_every_s = 20.0
        self.chaos_log: list[dict[str, Any]] = []

    def validate_first_step(self, scenario: Scenario) -> None:
        """Fail fast on the request for obvious mistakes instead of inside a background task."""
        first = scenario.steps[0]
        if first.get("action") == "vehicle_breakdown":
            vehicle_breakdown(self.sim, 0, first.get("vehicle"))  # zero duration: a dry run of the checks
        elif "action" in first and first.get("vehicle"):
            _vehicle(self.sim, first["vehicle"])

    def start(self, scenario: Scenario) -> Run:
        self.validate_first_step(scenario)
        run = Run(uuid.uuid4().hex[:8], scenario.name)
        run.task = asyncio.create_task(self._execute(run, scenario))
        self.runs[run.id] = run
        return run

    async def _execute(self, run: Run, scenario: Scenario) -> None:
        try:
            for i, step in enumerate(scenario.steps):
                run.step = i
                if "wait_s" in step:
                    await asyncio.sleep(step["wait_s"])
                    continue
                params = {k: v for k, v in step.items() if k != "action"}
                out = ACTIONS[step["action"]](self.sim, **params)
                if out:
                    await self.send(out)
        except (ScenarioError, TypeError) as e:
            run.error = str(e)
        finally:
            run.done = True

    def start_chaos(self, every_s: float) -> None:
        self.chaos_every_s = every_s
        if self.chaos is None or self.chaos.done():
            self.chaos = asyncio.create_task(self._chaos())

    def stop_chaos(self) -> None:
        if self.chaos and not self.chaos.done():
            self.chaos.cancel()
        self.chaos = None

    @property
    def chaos_on(self) -> bool:
        return self.chaos is not None and not self.chaos.done()

    async def _chaos(self) -> None:
        while True:
            what, out = random_incident(self.sim)
            self.chaos_log = [{"at": time.time(), "what": what}, *self.chaos_log][:8]
            if out:
                await self.send(out)
            await asyncio.sleep(self.chaos_every_s * self.sim.rnd.uniform(0.7, 1.3))

    def stop_all(self) -> None:
        self.stop_chaos()
        for run in self.runs.values():
            if run.task and not run.task.done():
                run.task.cancel()
        self.sim.effects.clear()
