"""Normalized event contract (v1).

Every sensor reading travels through the system in this shape:
connectors normalize vendor payloads into it, the worker consumes it,
ClickHouse stores it. Vendor formats never leak past connectors.

Coordinates are always local plan metres (see deploy/seed/layout.geojson);
connectors convert WGS84 from GNSS trackers before publishing.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = 1


class SensorType(StrEnum):
    ANPR_CAMERA = "anpr_camera"  # камера распознавания гос. номеров
    ACCESS_CONTROL = "access_control"  # СКУД: турникет / считыватель пропусков
    GNSS = "gnss"  # ГЛОНАСС/GPS-трекер транспорта
    MOTION = "motion"  # датчик движения охранной системы
    CLIMATE = "climate"  # температура и влажность
    SMOKE = "smoke"  # дымовой пожарный извещатель


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class Geo(BaseModel):
    """Point in local plan coordinates, metres from the site origin (south-west corner)."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    floor: int | None = Field(None, description="Этаж внутри здания; null — улица")


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnprCameraPayload(_Payload):
    plate: str = Field(..., examples=["А123ВС77"], description="Гос. номер, кириллица, без пробелов")
    direction: Direction
    confidence: float = Field(..., ge=0, le=1)


class AccessControlPayload(_Payload):
    card_id: str = Field(..., examples=["P-000123"])
    direction: Direction
    granted: bool = Field(..., description="Решение контроллера СКУД")


class GnssPayload(_Payload):
    speed_kmh: float = Field(..., ge=0)
    heading_deg: float = Field(..., ge=0, lt=360)
    fuel_pct: float | None = Field(None, ge=0, le=100)
    odometer_km: float | None = Field(None, ge=0)
    engine_on: bool


class MotionPayload(_Payload):
    detected: bool


class ClimatePayload(_Payload):
    temperature_c: float
    humidity_pct: float = Field(..., ge=0, le=100)


class SmokePayload(_Payload):
    smoke_pct: float = Field(..., ge=0, le=100, description="Задымлённость (оптическая плотность), %/м; чистый воздух — 0…2")


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4, description="Ключ идемпотентности")
    schema_v: Literal[1] = SCHEMA_VERSION
    sensor_id: str = Field(..., min_length=1, max_length=64)
    ts: AwareDatetime = Field(..., description="Время измерения на датчике; агрегаты считаются по нему")
    received_at: AwareDatetime | None = Field(None, description="Время приёма коннектором; ставит коннектор")
    geo: Geo | None = None
    zone_id: str | None = Field(None, description="Зона плана; ставит датчик (СКУД) или worker (point-in-polygon)")


class AnprCameraEvent(_EventBase):
    type: Literal[SensorType.ANPR_CAMERA]
    payload: AnprCameraPayload


class AccessControlEvent(_EventBase):
    type: Literal[SensorType.ACCESS_CONTROL]
    payload: AccessControlPayload


class GnssEvent(_EventBase):
    type: Literal[SensorType.GNSS]
    payload: GnssPayload


class MotionEvent(_EventBase):
    type: Literal[SensorType.MOTION]
    payload: MotionPayload


class ClimateEvent(_EventBase):
    type: Literal[SensorType.CLIMATE]
    payload: ClimatePayload


class SmokeEvent(_EventBase):
    type: Literal[SensorType.SMOKE]
    payload: SmokePayload


Event = Annotated[
    Union[AnprCameraEvent, AccessControlEvent, GnssEvent, MotionEvent, ClimateEvent, SmokeEvent],
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: dict | str | bytes) -> Event:
    """Validate a dict or JSON document into the matching typed event."""
    if isinstance(data, (str, bytes)):
        return EVENT_ADAPTER.validate_json(data)
    return EVENT_ADAPTER.validate_python(data)


def utcnow() -> datetime:
    return datetime.now(UTC)
