import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from scada_db import models as m
from scada_db.config import settings
from scada_db.postgres import engine
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.alerts.router import router as alerts_router
from app.auth.router import router as auth_router
from app.config import api_settings
from app.connectors.router import router as connectors_router
from app.deps import clickhouse
from app.kpi.router import router as kpi_router
from app.layout.router import router as layout_router
from app.live.broadcast import Broadcaster
from app.live.router import router as live_router
from app.predict.router import router as predict_router
from app.registry.router import router as registry_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


def _building_ids() -> list[str]:
    with Session(engine()) as s:
        return list(s.scalars(select(m.Building.id).order_by(m.Building.id)))


async def _refresh_buildings(app: FastAPI) -> None:
    """Buildings change only when the plan is saved; the live snapshot needs their ids for headcounts."""
    while True:
        await asyncio.sleep(30)
        try:
            app.state.building_ids = await asyncio.to_thread(_building_ids)
        except Exception:
            log.exception("building list refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.broadcaster = Broadcaster(app.state.redis)
    app.state.building_ids = await asyncio.to_thread(_building_ids)
    tasks = [asyncio.create_task(app.state.broadcaster.run_forever()), asyncio.create_task(_refresh_buildings(app))]
    yield
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await app.state.redis.aclose()


app = FastAPI(
    title="SCADA Logistics API",
    version="0.2.0",
    description=(
        "Мониторинг логистической инфраструктуры предприятия.\n\n"
        "**Авторизация:** `POST /auth/login` → `Authorization: Bearer <access_token>`; access живёт 15 мин, "
        "обновление — `POST /auth/refresh`. WebSocket: `/ws/live?token=<access_token>`. "
        "Демо-пользователи: `dispatcher`, `security`, `admin`, пароль `demo`.\n\n"
        "Координаты — метры локального плана (см. `GET /objects`). Время — ISO 8601 с таймзоной.\n\n"
        "WebSocket `/ws/live`: формат сообщений — `contracts/ws.schema.json`."
    ),
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=api_settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

Instrumentator(excluded_handlers=["/metrics", "/health"]).instrument(app).expose(app, include_in_schema=False)

for r in (auth_router, layout_router, registry_router, live_router, alerts_router, kpi_router, connectors_router, predict_router):
    app.include_router(r)


def _ping_postgres() -> None:
    with Session(engine()) as s:
        s.execute(text("SELECT 1"))


def _ping_clickhouse() -> None:
    if not clickhouse().ping():
        raise ConnectionError("clickhouse ping failed")


@app.get("/health", tags=["system"])
async def health() -> JSONResponse:
    """Доступность хранилищ; 503, если какое-то недоступно."""
    checks = {}
    for name, probe in (("postgres", _ping_postgres), ("clickhouse", _ping_clickhouse)):
        try:
            await asyncio.to_thread(probe)
            checks[name] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[name] = f"error: {type(e).__name__}"
    try:
        await app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)
