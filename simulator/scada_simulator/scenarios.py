"""Scenario engine: YAML steps -> actions that start time-limited effects in the simulation."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scada_simulator.sim import GATE, OFF_SITE, Simulation, Task, skud
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


def _vehicle(sim: Simulation, vehicle_id: str | None, kind: str = "truck"):
    if vehicle_id:
        try:
            return sim.vehicle(vehicle_id)
        except StopIteration:
            raise ScenarioError(f"unknown vehicle {vehicle_id}") from None
    moving = [v for v in sim.vehicles if v.kind == kind and v.speed_kmh > 0 and 0 < v.x < 800]
    if not moving:
        raise ScenarioError(f"no moving {kind} on site right now")
    return sim.rnd.choice(moving)


def _sensor(sim: Simulation, sensor_id: str, sensor_type: str) -> None:
    spec = sim.world.sensors.get(sensor_id)
    if spec is None or spec.type != sensor_type:
        raise ScenarioError(f"{sensor_id} is not a registered {sensor_type} sensor")


@action
def vehicle_breakdown(sim: Simulation, duration_s: float, vehicle: str | None = None) -> list[Outgoing]:
    v = _vehicle(sim, vehicle)
    sim.add_effect("breakdown", v.id, duration_s)
    return []


@action
def vehicle_speeding(sim: Simulation, kmh: float, duration_s: float, vehicle: str | None = None) -> list[Outgoing]:
    v = _vehicle(sim, vehicle, kind="car")
    sim.add_effect("speed", v.id, duration_s, kmh=kmh)
    return []


@action
def vehicle_arrival(sim: Simulation, vehicle: str) -> list[Outgoing]:
    """Send a vehicle that waits beyond the gate through КПП-1 to a dock and back out."""
    v = _vehicle(sim, vehicle)
    v.parked_off_site = False
    v.x, v.y = OFF_SITE
    v.path, v.dwell_left = [], 0
    dock = sim.rnd.choice(sim._docks)
    v.tasks = [Task("drive", GATE), Task("event", emit=("cam-gate-in", "approach")), Task("drive", dock),
               Task("dwell", seconds=90), Task("drive", GATE), Task("event", emit=("cam-gate-out", "leave")),
               Task("drive", OFF_SITE), Task("park")]  # back to waiting beyond the gate
    return []


@action
def climate_drift(sim: Simulation, sensor: str, delta_c: float, duration_s: float, ramp_s: float = 120) -> list[Outgoing]:
    _sensor(sim, sensor, "climate")
    sim.add_effect("climate_drift", sensor, duration_s, delta_c=delta_c, ramp_s=ramp_s)
    return []


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
    return []


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

    def validate_first_step(self, scenario: Scenario) -> None:
        """Fail fast on the request for obvious mistakes instead of inside a background task."""
        first = scenario.steps[0]
        if "action" in first and first.get("vehicle"):
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

    def stop_all(self) -> None:
        for run in self.runs.values():
            if run.task and not run.task.done():
                run.task.cancel()
        self.sim.effects.clear()
