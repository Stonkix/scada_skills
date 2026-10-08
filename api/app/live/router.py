import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.alerts.schemas import Alert, AlertKind, AlertStatus, Severity
from app.live.schemas import (
    Layer,
    LiveState,
    WsAlert,
    WsSensorUpdate,
    WsSubscribe,
    WsVehicleUpdate,
    WsZoneUpdate,
)
from app.mock import world

router = APIRouter(tags=["live"])

SITE = "site"
VEHICLE_EVERY_S = 1
SENSOR_EVERY_S = 5
ZONE_EVERY_S = 10
ALERT_EVERY_S = 45


@router.get("/state/live", response_model=LiveState)
def live_state(building_id: Annotated[str | None, Query(description='"site" — объекты на улице')] = None) -> LiveState:
    now = datetime.now(UTC)
    sensors = [world.sensor_live(s, now) for s in world.sensors.values()
               if not s.is_mobile and building_id in (None, s.building_id or SITE)]
    vehicles = [world.vehicle_live(v, now) for v in world.vehicles] if building_id in (None, SITE) else []
    zones = [z for z in world.zones(now) if building_id in (None, z.zone_id)]
    open_alerts = sum(a.status == AlertStatus.OPEN for a in world.alerts.values())
    return LiveState(ts=now, sensors=sensors, vehicles=vehicles, zones=zones, open_alerts=open_alerts)


def _wants(sub: WsSubscribe, layer: Layer, building: str, sensor_type: str | None = None) -> bool:
    return ((sub.layers is None or layer in sub.layers)
            and (sub.buildings is None or building in sub.buildings)
            and (sensor_type is None or sub.sensor_types is None or sensor_type in sub.sensor_types))


def _mock_alert(now: datetime) -> Alert:
    v = world.vehicle_live("v-car-1", now)
    alert_id = max(world.alerts, default=0) + 1
    return Alert(id=alert_id, rule_id="rule-speed", rule_version=1, kind=AlertKind.SPEED, severity=Severity.WARNING,
                 status=AlertStatus.OPEN, title="Превышение скорости",
                 message=f"М001ММ77: {v.speed_kmh} км/ч при ограничении 20 км/ч", sensor_id=v.sensor_id,
                 vehicle_id=v.vehicle_id, zone_id=v.zone_id, value=v.speed_kmh, opened_at=now, last_seen_at=now)


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    """Поток изменений. После подключения клиент шлёт WsSubscribe; без него приходит всё.

    Сообщения сервера — WsServerMessage (поле `type`: sensor | vehicle | zone | alert).
    """
    await ws.accept()
    sub = WsSubscribe()

    async def read_subscriptions() -> None:
        nonlocal sub
        while True:
            try:
                sub = WsSubscribe.model_validate_json(await ws.receive_text())
            except ValidationError as e:
                await ws.send_json({"type": "error", "message": e.errors(include_url=False)[0]["msg"]})

    reader = asyncio.create_task(read_subscriptions())
    tick = 0
    try:
        while not reader.done():
            now = datetime.now(UTC)
            msgs = []
            if tick % VEHICLE_EVERY_S == 0:
                msgs += [WsVehicleUpdate(ts=now, building_id=SITE, data=world.vehicle_live(v, now))
                         for v in world.vehicles if _wants(sub, Layer.VEHICLES, SITE)]
            if tick % SENSOR_EVERY_S == 0:
                msgs += [WsSensorUpdate(ts=now, building_id=s.building_id or SITE, data=world.sensor_live(s, now))
                         for s in world.sensors.values()
                         if not s.is_mobile and _wants(sub, Layer.SENSORS, s.building_id or SITE, s.type)]
            if tick % ZONE_EVERY_S == 0:
                msgs += [WsZoneUpdate(ts=now, building_id=z.zone_id, data=z)
                         for z in world.zones(now) if _wants(sub, Layer.PEOPLE, z.zone_id)]
            if tick and tick % ALERT_EVERY_S == 0:
                alert = _mock_alert(now)
                world.alerts[alert.id] = alert
                if _wants(sub, Layer.ALERTS, SITE):
                    msgs.append(WsAlert(ts=now, building_id=SITE, data=alert))
            for m in msgs:
                await ws.send_text(m.model_dump_json())
            tick += 1
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
