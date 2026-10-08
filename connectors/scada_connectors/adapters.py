"""Vendor payload -> normalized Event fields.

Adding a sensor vendor is one class: declare its raw payload as a Pydantic model,
convert it to event fields, register it with @adapter. The pipeline does auth,
registry checks, validation against the contract and publishing.

The formats below are representative of what such devices emit (webhook JSON
from ANPR cameras and access controllers, tracker telemetry with lat/lon, plain
IoT sensors); they are not tied to a specific manufacturer.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from scada_common import SensorType
from scada_common.geo import Georef

# uuid5 namespace for event ids derived from vendor data: a retried delivery gets the same id.
EVENT_NS = uuid.UUID("7d3f4a52-0c51-4b8e-9a51-2f7f0e9b6c11")

_LATIN_TO_CYRILLIC = str.maketrans("ABEKMHOPCTYX", "АВЕКМНОРСТУХ")


class Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")  # vendors add fields freely; we take what we need


class Adapter:
    name: ClassVar[str]
    sensor_type: ClassVar[SensorType | None]  # None: any type (native contract)
    title: ClassVar[str]
    raw_model: ClassVar[type[Raw]]
    example: ClassVar[dict[str, Any]]
    # where the device id and the timestamp sit in the vendor payload: lets UIs fill in a real
    # sensor and the current time when building a test message from `example`
    device_field: ClassVar[str]
    ts_field: ClassVar[str]
    ts_format: ClassVar[Literal["iso", "unix_s", "unix_ms"]] = "iso"

    def device_id(self, raw: Raw) -> str:
        raise NotImplementedError

    def convert(self, raw: Raw, georef: Georef) -> dict[str, Any]:
        """Return Event fields except type/received_at; event_id is derived if absent."""
        raise NotImplementedError


ADAPTERS: dict[str, Adapter] = {}


def adapter(cls: type[Adapter]) -> type[Adapter]:
    ADAPTERS[cls.name] = cls()
    return cls


def derived_event_id(adapter_name: str, sensor_id: str, ts: datetime, discriminator: str = "") -> uuid.UUID:
    """Same delivery -> same id. The timestamp is always part of the key: a vendor field such as a
    card number is not unique on its own (the same card passes the same reader many times a day)."""
    return uuid.uuid5(EVENT_NS, f"{adapter_name}|{sensor_id}|{ts.isoformat()}|{discriminator}")


def _from_unix(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


# --- native ---------------------------------------------------------------------------------------


@adapter
class NativeAdapter(Adapter):
    """Devices and gateways that already speak our contract (common/scada_common/events.py)."""

    name = "native"
    device_field = "sensor_id"
    ts_field = "ts"
    sensor_type = None
    title = "Единый формат события (контракт v1)"

    class RawNative(Raw):
        model_config = ConfigDict(extra="allow")
        sensor_id: str

    raw_model = RawNative
    example = {"sensor_id": "clim-wh1-storage", "type": "climate", "ts": "2026-10-08T12:00:00+03:00",
               "payload": {"temperature_c": 17.8, "humidity_pct": 52.0}}

    def device_id(self, raw: Raw) -> str:
        return raw.sensor_id

    def convert(self, raw: Raw, georef: Georef) -> dict[str, Any]:
        return raw.model_dump()


# --- ANPR camera ----------------------------------------------------------------------------------


@adapter
class AnprAdapter(Adapter):
    name = "anpr"
    device_field = "camera_id"
    ts_field = "captured_at"
    sensor_type = SensorType.ANPR_CAMERA
    title = "Камера распознавания номеров (webhook JSON)"

    class RawAnpr(Raw):
        camera_id: str
        plate: str = Field(..., description="As recognized; Latin look-alikes and spaces are normalized")
        direction: Literal["approach", "leave"]
        confidence_pct: float = Field(..., ge=0, le=100)
        captured_at: AwareDatetime
        event_uid: str | None = None

    raw_model = RawAnpr
    example = {"camera_id": "cam-gate-in", "plate": "a 123 bc 77", "direction": "approach", "confidence_pct": 96.5,
               "captured_at": "2026-10-08T12:00:00+03:00", "event_uid": "c7f1-000183"}

    def device_id(self, raw: RawAnpr) -> str:
        return raw.camera_id

    def convert(self, raw: RawAnpr, georef: Georef) -> dict[str, Any]:
        plate = raw.plate.upper().replace(" ", "").translate(_LATIN_TO_CYRILLIC)
        return {"sensor_id": raw.camera_id, "ts": raw.captured_at,
                "event_id": derived_event_id(self.name, raw.camera_id, raw.captured_at, raw.event_uid or ""),
                "payload": {"plate": plate, "direction": "in" if raw.direction == "approach" else "out",
                            "confidence": round(raw.confidence_pct / 100, 4)}}


# --- access control (СКУД) ------------------------------------------------------------------------


@adapter
class SkudAdapter(Adapter):
    name = "skud"
    device_field = "reader_id"
    ts_field = "time"
    sensor_type = SensorType.ACCESS_CONTROL
    title = "Контроллер СКУД (пропуска, Wiegand)"

    class RawSkud(Raw):
        reader_id: str
        card: str | int = Field(..., description='"P-000123" or a numeric Wiegand code')
        event: Literal["entry", "exit"]
        result: Literal["granted", "denied"]
        time: AwareDatetime

    raw_model = RawSkud
    example = {"reader_id": "acs-wh1", "card": 123, "event": "entry", "result": "granted",
               "time": "2026-10-08T08:01:12+03:00"}

    def device_id(self, raw: RawSkud) -> str:
        return raw.reader_id

    def convert(self, raw: RawSkud, georef: Georef) -> dict[str, Any]:
        card = f"P-{raw.card:06d}" if isinstance(raw.card, int) else raw.card.strip().upper()
        return {"sensor_id": raw.reader_id, "ts": raw.time,
                "event_id": derived_event_id(self.name, raw.reader_id, raw.time, f"{card}|{raw.event}"),
                "payload": {"card_id": card, "direction": "in" if raw.event == "entry" else "out",
                            "granted": raw.result == "granted"}}


# --- GNSS tracker ---------------------------------------------------------------------------------


@adapter
class GnssAdapter(Adapter):
    name = "gnss"
    device_field = "device_id"
    ts_field = "fix_time"
    ts_format = "unix_s"
    sensor_type = SensorType.GNSS
    title = "ГЛОНАСС/GPS-трекер (координаты WGS84)"

    class RawGnss(Raw):
        device_id: str
        lat: float = Field(..., ge=-90, le=90)
        lon: float = Field(..., ge=-180, le=180)
        speed: float = Field(..., ge=0, description="km/h")
        course: float = Field(..., ge=0, le=360)
        ignition: bool | Literal[0, 1]
        fuel_pct: float | None = Field(None, ge=0, le=100)
        odometer_m: float | None = Field(None, ge=0)
        fix_time: float = Field(..., description="Unix seconds (UTC)")

    raw_model = RawGnss
    example = {"device_id": "gnss-truck-1", "lat": 55.702249, "lon": 37.404731, "speed": 18.4, "course": 92.0,
               "ignition": 1, "fuel_pct": 64.5, "odometer_m": 152340, "fix_time": 1791450000}

    def device_id(self, raw: RawGnss) -> str:
        return raw.device_id

    def convert(self, raw: RawGnss, georef: Georef) -> dict[str, Any]:
        x, y = georef.to_local(raw.lat, raw.lon)
        ts = _from_unix(raw.fix_time)
        return {"sensor_id": raw.device_id, "ts": ts, "event_id": derived_event_id(self.name, raw.device_id, ts),
                "geo": {"x": round(x, 2), "y": round(y, 2)},
                "payload": {"speed_kmh": raw.speed, "heading_deg": raw.course % 360, "fuel_pct": raw.fuel_pct,
                            "odometer_km": None if raw.odometer_m is None else round(raw.odometer_m / 1000, 3),
                            "engine_on": bool(raw.ignition)}}


# --- motion ---------------------------------------------------------------------------------------


@adapter
class MotionAdapter(Adapter):
    name = "motion"
    device_field = "device"
    ts_field = "ts"
    sensor_type = SensorType.MOTION
    title = "Датчик движения охранной системы"

    class RawMotion(Raw):
        device: str
        state: Literal["alarm", "idle"]
        ts: AwareDatetime

    raw_model = RawMotion
    example = {"device": "mot-wh3", "state": "alarm", "ts": "2026-10-08T02:14:00+03:00"}

    def device_id(self, raw: RawMotion) -> str:
        return raw.device

    def convert(self, raw: RawMotion, georef: Georef) -> dict[str, Any]:
        return {"sensor_id": raw.device, "ts": raw.ts, "event_id": derived_event_id(self.name, raw.device, raw.ts),
                "payload": {"detected": raw.state == "alarm"}}


# --- climate --------------------------------------------------------------------------------------


@adapter
class ClimateAdapter(Adapter):
    name = "climate"
    device_field = "device"
    ts_field = "ts_ms"
    ts_format = "unix_ms"
    sensor_type = SensorType.CLIMATE
    title = "Датчик температуры и влажности"

    class RawClimate(Raw):
        device: str
        temperature: float
        unit: Literal["C", "F"] = "C"
        humidity: float = Field(..., ge=0, le=100)
        ts_ms: int = Field(..., description="Unix milliseconds (UTC)")

    raw_model = RawClimate
    example = {"device": "clim-wh2-storage", "temperature": 39.6, "unit": "F", "humidity": 61.2, "ts_ms": 1791450000000}

    def device_id(self, raw: RawClimate) -> str:
        return raw.device

    def convert(self, raw: RawClimate, georef: Georef) -> dict[str, Any]:
        t = raw.temperature if raw.unit == "C" else (raw.temperature - 32) * 5 / 9
        ts = _from_unix(raw.ts_ms / 1000)
        return {"sensor_id": raw.device, "ts": ts, "event_id": derived_event_id(self.name, raw.device, ts),
                "payload": {"temperature_c": round(t, 2), "humidity_pct": raw.humidity}}
