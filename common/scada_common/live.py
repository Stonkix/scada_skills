"""Live state: written by the worker, served by the API as REST snapshot and WebSocket messages."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter
from scada_common.alerts import Alert
from scada_common.events import Geo, SensorType


class SensorStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    OFFLINE = "offline"


class VehicleStatus(StrEnum):
    MOVING = "moving"
    IDLE = "idle"  # двигатель работает, стоит
    STOPPED = "stopped"  # двигатель заглушен
    OFFLINE = "offline"


class Layer(StrEnum):
    SENSORS = "sensors"
    VEHICLES = "vehicles"
    PEOPLE = "people"
    ALERTS = "alerts"


class SensorLive(BaseModel):
    sensor_id: str = Field(..., examples=["clim-wh2-storage"])
    type: SensorType
    building_id: str | None = None
    status: SensorStatus
    last_seen: datetime | None
    values: dict[str, float | bool | str] = Field(
        ..., examples=[{"temperature_c": 4.2, "humidity_pct": 61.0}], description="Последний payload датчика"
    )


class VehicleLive(BaseModel):
    vehicle_id: str = Field(..., examples=["v-truck-1"])
    sensor_id: str
    status: VehicleStatus
    geo: Geo
    speed_kmh: float
    heading_deg: float
    fuel_pct: float | None = None
    engine_on: bool
    zone_id: str | None = Field(None, description="Геозона, в которой сейчас машина")
    last_seen: datetime


class ZoneOccupancy(BaseModel):
    zone_id: str = Field(..., examples=["b-wh1"], description="Здание или помещение")
    people: int = Field(..., ge=0, description="Людей внутри по событиям СКУД вход/выход")


class LiveState(BaseModel):
    ts: datetime
    sensors: list[SensorLive]
    vehicles: list[VehicleLive]
    zones: list[ZoneOccupancy]
    open_alerts: int


# --- WebSocket /ws/live ----------------------------------------------------------------------


class WsSubscribe(BaseModel):
    """Клиент → сервер. Можно слать повторно, чтобы сменить фильтр. null в поле = без фильтра."""

    op: Literal["subscribe"] = "subscribe"
    buildings: list[str] | None = Field(None, examples=[["b-wh1", "site"]], description='"site" — объекты на улице')
    layers: list[Layer] | None = None
    sensor_types: list[SensorType] | None = None


class _WsServerMessage(BaseModel):
    ts: datetime
    building_id: str | None = Field(None, description='Здание объекта; "site" — улица')


class WsSensorUpdate(_WsServerMessage):
    type: Literal["sensor"] = "sensor"
    data: SensorLive


class WsVehicleUpdate(_WsServerMessage):
    type: Literal["vehicle"] = "vehicle"
    data: VehicleLive


class WsZoneUpdate(_WsServerMessage):
    type: Literal["zone"] = "zone"
    data: ZoneOccupancy


class WsAlert(_WsServerMessage):
    type: Literal["alert"] = "alert"
    data: Alert


WsServerMessage = Annotated[
    Union[WsSensorUpdate, WsVehicleUpdate, WsZoneUpdate, WsAlert],
    Field(discriminator="type"),
]
WS_SERVER_ADAPTER: TypeAdapter[WsServerMessage] = TypeAdapter(WsServerMessage)
