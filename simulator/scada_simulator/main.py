"""Simulator service: runs the simulated enterprise and exposes scenario controls (the demo "cheat menu")."""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from scada_simulator import world as world_mod
from scada_simulator.scenarios import Runner, ScenarioError, load
from scada_simulator.sim import MAX_TIME_SCALE, Simulation
from scada_simulator.transport import Transport

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("simulator")

SCENARIOS_PATH = Path(os.environ.get("SCENARIOS_PATH", Path(__file__).resolve().parents[1] / "scenarios.yaml"))
SENSOR_REFRESH_S = 60


async def _loop(sim: Simulation, transport: Transport) -> None:
    with contextlib.suppress(TimeoutError):  # don't drop the first GNSS fixes while MQTT connects
        await asyncio.wait_for(transport.mqtt_ready.wait(), timeout=15)
    loop = asyncio.get_running_loop()
    next_tick = loop.time()
    while True:
        transport.silenced = sim.silenced()
        try:
            await transport.send(sim.tick())
        except Exception:  # a delivery hiccup must not stop the world
            log.exception("tick failed")
        next_tick += 1
        await asyncio.sleep(max(0, next_tick - loop.time()))


async def _refresh_sensors(sim: Simulation) -> None:
    while True:
        await asyncio.sleep(SENSOR_REFRESH_S)
        try:
            sim.refresh_sensors(await asyncio.to_thread(world_mod.load_sensors))
        except Exception:
            log.exception("sensor refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    world = await asyncio.to_thread(world_mod.load_world)
    sim = Simulation(world)
    transport = Transport(
        connectors_url=os.environ.get("CONNECTORS_URL", "http://localhost:8001"),
        mqtt_host=os.environ.get("MQTT_HOST", "localhost"),
        mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
        api_key=os.environ.get("SIMULATOR_API_KEY", "sk_simulator_dev_3f9a1c7e5b2d4086a1e9c3b7d5f20e48"),
    )
    app.state.sim, app.state.transport = sim, transport
    app.state.scenarios = load(SCENARIOS_PATH)
    app.state.runner = Runner(sim, transport.send)
    tasks = [asyncio.create_task(c) for c in (transport.run_mqtt_forever(), _loop(sim, transport), _refresh_sensors(sim))]
    log.info("simulating %d vehicles, %d sensors", len(sim.vehicles), len(world.sensors))
    yield
    app.state.runner.stop_all()
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await transport.aclose()


app = FastAPI(title="SCADA Simulator", version="0.1.0", lifespan=lifespan,
              description="Симулятор предприятия. Весь трафик идёт через connectors. Сценарии — `simulator/scenarios.yaml`.")


@app.get("/scenarios", tags=["scenarios"])
def list_scenarios(request: Request) -> list[dict[str, Any]]:
    return [vars(s) for s in request.app.state.scenarios.values()]


@app.post("/scenario/{name}", status_code=202, tags=["scenarios"])
async def run_scenario(name: str, request: Request) -> dict[str, Any]:
    scenario = request.app.state.scenarios.get(name)
    if scenario is None:
        raise HTTPException(404, f"Unknown scenario '{name}'")
    try:
        run = request.app.state.runner.start(scenario)
    except ScenarioError as e:
        raise HTTPException(409, str(e)) from e
    return {"run_id": run.id, "scenario": name, "steps": len(scenario.steps)}


@app.post("/scenarios/stop", tags=["scenarios"])
async def stop_scenarios(request: Request) -> dict[str, str]:
    """Отменить все запущенные сценарии и эффекты: предприятие возвращается к обычной жизни."""
    request.app.state.runner.stop_all()
    return {"status": "stopped"}


class TimeScale(BaseModel):
    scale: float = Field(..., ge=1, le=MAX_TIME_SCALE, description="Во сколько раз быстрее ездит и работает техника")


@app.post("/time", tags=["scenarios"])
def set_time(body: TimeScale, request: Request) -> dict[str, float]:
    """Ускорение времени для показа: машины едут и грузятся в `scale` раз быстрее; 1 — обычный ход."""
    return {"time_scale": request.app.state.sim.set_time_scale(body.scale)}


class Chaos(BaseModel):
    on: bool
    every_s: float = Field(20, ge=5, le=600, description="Средний интервал между авариями")


@app.post("/chaos", tags=["scenarios"])
async def set_chaos(body: Chaos, request: Request) -> dict[str, Any]:
    """Случайные аварии по всем площадкам, пока включено: перегрев, поломка, чужой номер или пропуск,
    движение в пустом здании, превышение скорости. Каждая сразу даёт тревогу."""
    runner = request.app.state.runner
    if body.on:
        runner.start_chaos(body.every_s)
    else:
        runner.stop_chaos()
    return {"on": runner.chaos_on, "every_s": runner.chaos_every_s}


@app.get("/status", tags=["system"])
def status(request: Request) -> dict[str, Any]:
    t: Transport = request.app.state.transport
    return {
        **request.app.state.sim.status(),
        "sent": dict(t.sent), "failed": dict(t.failed), "last_error": t.last_error,
        "runs": [{"id": r.id, "scenario": r.scenario, "step": r.step, "done": r.done, "error": r.error}
                 for r in request.app.state.runner.runs.values()],
        "chaos": {"on": request.app.state.runner.chaos_on, "every_s": request.app.state.runner.chaos_every_s,
                  "recent": request.app.state.runner.chaos_log},
    }


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
