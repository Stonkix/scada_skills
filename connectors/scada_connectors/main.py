"""Connectors service: HTTP and MQTT ingress for sensors, publishing normalized events to Redis."""

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from scada_common import keys
from scada_db.config import settings

from scada_connectors.adapters import ADAPTERS
from scada_connectors.mqtt import API_KEY_PROPERTY, MqttIngress
from scada_connectors.pipeline import IngestError, Pipeline
from scada_connectors.registry import Registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ENABLE_MQTT = os.environ.get("CONNECTORS_MQTT", "1") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    registry = Registry()
    await registry.reload()
    app.state.pipeline = Pipeline(redis, registry)
    app.state.mqtt = MqttIngress(app.state.pipeline)
    tasks = [asyncio.create_task(registry.refresh_forever())]
    if ENABLE_MQTT:
        tasks.append(asyncio.create_task(app.state.mqtt.run_forever()))
    yield
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await redis.aclose()


app = FastAPI(
    title="SCADA Connectors",
    version="0.1.0",
    description=(
        "Приём данных с датчиков. HTTP: `POST /ingest/{adapter}` с заголовком `X-API-Key`, тело — объект или массив. "
        f"MQTT: топик `sensors/{{adapter}}/{{device_id}}`, ключ в user property `{API_KEY_PROPERTY}` (MQTT 5). "
        "Каталог форматов — `GET /adapters`. Отклонённое попадает в `stream:dlq` с причиной."
    ),
    lifespan=lifespan,
)


@app.exception_handler(IngestError)
async def ingest_error(_: Request, e: IngestError) -> JSONResponse:
    return JSONResponse({"detail": e.detail}, status_code=e.status)


class RejectionOut(BaseModel):
    index: int
    reason: str
    detail: Any = None


class IngestOut(BaseModel):
    accepted: int
    event_ids: list[str]
    rejected: list[RejectionOut]


class AdapterOut(BaseModel):
    name: str
    sensor_type: str | None
    title: str
    http: str
    mqtt_topic: str
    example: dict[str, Any]
    device_field: str = Field(..., description="Поле payload с id устройства (= id датчика в реестре)")
    ts_field: str = Field(..., description="Поле payload со временем измерения")
    ts_format: str = Field(..., description="iso | unix_s | unix_ms")
    schema_: dict[str, Any] = Field(..., serialization_alias="schema")


@app.get("/adapters", response_model=list[AdapterOut], tags=["catalog"])
def list_adapters() -> list[AdapterOut]:
    """Каталог поддерживаемых форматов: для страницы «подключить датчик за 5 минут»."""
    return [AdapterOut(name=a.name, sensor_type=a.sensor_type, title=a.title, http=f"POST /ingest/{a.name}",
                       mqtt_topic=f"sensors/{a.name}/{{device_id}}", example=a.example,
                       device_field=a.device_field, ts_field=a.ts_field, ts_format=a.ts_format,
                       schema_=a.raw_model.model_json_schema())
            for a in ADAPTERS.values()]


@app.post("/ingest/{adapter}", response_model=IngestOut, tags=["ingest"], status_code=202,
          responses={401: {}, 404: {}, 422: {"model": IngestOut}, 429: {}})
async def ingest(
    adapter: str,
    request: Request,
    body: Annotated[dict[str, Any] | list[dict[str, Any]], Body()],
    x_api_key: Annotated[str | None, Header()] = None,
):
    """202 — хотя бы одно событие принято; 422 — отклонены все (подробности в `rejected`)."""
    items = body if isinstance(body, list) else [body]
    result = await request.app.state.pipeline.ingest(adapter, items, x_api_key)
    out = IngestOut(accepted=len(result.accepted), event_ids=result.accepted,
                    rejected=[RejectionOut(**vars(r)) for r in result.rejected])
    return JSONResponse(out.model_dump(), status_code=202 if result.accepted or not items else 422)


@app.get("/health", tags=["system"])
async def health(request: Request) -> dict[str, Any]:
    p: Pipeline = request.app.state.pipeline
    return {
        "status": "ok",
        "mqtt_connected": request.app.state.mqtt.connected if ENABLE_MQTT else None,
        "registry": {"sensors": len(p.registry.sensors), "api_keys": len(p.registry.keys)},
        "accepted": p.stats.accepted,
        "rejected": p.stats.rejected,
        "rejected_by_reason": p.stats.by_reason,
        "stream_len": await p.redis.xlen(keys.STREAM_EVENTS),
    }
