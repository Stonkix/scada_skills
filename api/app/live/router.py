import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Annotated

import jwt
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from scada_common import keys
from scada_common.live import LiveState, SensorLive, VehicleLive, WsSubscribe, ZoneOccupancy

from app.auth.security import Principal, principal_from_access
from app.deps import redis_async, require

router = APIRouter(tags=["live"])
WS_UNAUTHORIZED = 4401  # application close codes live in 4000-4999


@router.get("/state/live", response_model=LiveState)
async def live_state(
    request: Request,
    _: Annotated[Principal, Depends(require("map:view"))],
    redis: Annotated[aioredis.Redis, Depends(redis_async)],
    building_id: Annotated[str | None, Query(description='"site" — объекты на улице (машины)')] = None,
) -> LiveState:
    """Снимок текущего состояния; дальше изменения приходят по `WS /ws/live`."""
    sensor_keys = [k async for k in redis.scan_iter(keys.live_sensor("*"), count=500)]
    pipe = redis.pipeline(transaction=False)
    for k in sensor_keys:
        pipe.hmget(k, "doc", "kind")
    sensors, vehicles = [], []
    for doc, kind in await pipe.execute():
        if doc is None:
            continue
        if kind == "vehicle":
            if building_id in (None, keys.SITE):
                vehicles.append(VehicleLive.model_validate_json(doc))
        else:
            s = SensorLive.model_validate_json(doc)
            if building_id is None or (s.building_id or keys.SITE) == building_id:
                sensors.append(s)
    buildings = request.app.state.building_ids
    if building_id is not None:
        buildings = [b for b in buildings if b == building_id]
    counts = await redis.mget([keys.zone_people(b) for b in buildings]) if buildings else []
    open_alerts = sum([1 async for _ in redis.scan_iter(keys.alert_open("*", "*"), count=500)])
    sensors.sort(key=lambda s: s.sensor_id)
    vehicles.sort(key=lambda v: v.vehicle_id)
    return LiveState(ts=datetime.now(UTC), sensors=sensors, vehicles=vehicles, open_alerts=open_alerts,
                     zones=[ZoneOccupancy(zone_id=b, people=int(c or 0)) for b, c in zip(buildings, counts)])


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket, token: Annotated[str | None, Query(description="access token (JWT)")] = None) -> None:
    """Поток изменений. Авторизация — `?token=<access_token>` (браузер не умеет заголовки у WebSocket).

    После подключения клиент шлёт `WsSubscribe` (можно повторно, чтобы сменить фильтр); без него приходит всё.
    Сообщения сервера — `WsServerMessage` (`type`: sensor | vehicle | zone | alert), схема в contracts/ws.schema.json.
    """
    try:
        user = principal_from_access(token or "")
    except jwt.InvalidTokenError:
        await ws.close(code=WS_UNAUTHORIZED, reason="invalid or missing token")
        return
    if not user.can("map:view"):
        await ws.close(code=WS_UNAUTHORIZED, reason="map:view permission required")
        return
    await ws.accept()
    hub = ws.app.state.broadcaster
    client = hub.add()

    async def read_subscriptions() -> None:
        while True:
            try:
                client.sub = WsSubscribe.model_validate_json(await ws.receive_text())
            except ValidationError as e:
                await ws.send_text(json.dumps({"type": "error", "message": e.errors(include_url=False)[0]["msg"]}))

    reader = asyncio.create_task(read_subscriptions())
    try:
        while not reader.done():
            get = asyncio.create_task(client.queue.get())
            done, _ = await asyncio.wait({get, reader}, return_when=asyncio.FIRST_COMPLETED)
            if get in done:
                await ws.send_text(get.result())
            else:
                get.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(client)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
            await reader
