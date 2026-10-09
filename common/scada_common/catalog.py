"""Sensor type catalogue: display names and metrics of each payload field.

Single source for the API (`/objects.sensor_types`), the registry seed
(`sensor_types` table) and connectors. Metric keys equal payload field names.
"""

from pydantic import BaseModel, Field

from scada_common.events import (
    AccessControlPayload,
    AnprCameraPayload,
    ClimatePayload,
    GnssPayload,
    MotionPayload,
    SensorType,
)

PAYLOAD_MODELS: dict[SensorType, type[BaseModel]] = {
    SensorType.ANPR_CAMERA: AnprCameraPayload,
    SensorType.ACCESS_CONTROL: AccessControlPayload,
    SensorType.GNSS: GnssPayload,
    SensorType.MOTION: MotionPayload,
    SensorType.CLIMATE: ClimatePayload,
}

SENSOR_TYPES: list[dict] = [
    {"id": SensorType.ANPR_CAMERA, "name": "Камера распознавания номеров", "is_mobile": False, "metrics": [
        {"key": "plate", "name": "Гос. номер", "unit": None, "kind": "string"},
        {"key": "direction", "name": "Направление", "unit": None, "kind": "string"},
        {"key": "confidence", "name": "Уверенность распознавания", "unit": None, "kind": "number"},
    ]},
    {"id": SensorType.ACCESS_CONTROL, "name": "СКУД (пропуска)", "is_mobile": False, "metrics": [
        {"key": "card_id", "name": "Пропуск", "unit": None, "kind": "string"},
        {"key": "direction", "name": "Направление", "unit": None, "kind": "string"},
        {"key": "granted", "name": "Доступ разрешён", "unit": None, "kind": "bool"},
    ]},
    {"id": SensorType.GNSS, "name": "ГЛОНАСС-трекер транспорта", "is_mobile": True, "metrics": [
        {"key": "speed_kmh", "name": "Скорость", "unit": "км/ч", "kind": "number"},
        {"key": "heading_deg", "name": "Курс", "unit": "°", "kind": "number"},
        {"key": "fuel_pct", "name": "Топливо", "unit": "%", "kind": "number"},
        {"key": "odometer_km", "name": "Пробег", "unit": "км", "kind": "number"},
        {"key": "engine_on", "name": "Двигатель", "unit": None, "kind": "bool"},
    ]},
    {"id": SensorType.MOTION, "name": "Датчик движения (охрана)", "is_mobile": False, "metrics": [
        {"key": "detected", "name": "Движение", "unit": None, "kind": "bool"},
    ]},
    {"id": SensorType.CLIMATE, "name": "Климат (температура и влажность)", "is_mobile": False, "metrics": [
        {"key": "temperature_c", "name": "Температура", "unit": "°C", "kind": "number"},
        {"key": "humidity_pct", "name": "Влажность", "unit": "%", "kind": "number"},
    ]},
]


class Reporting(BaseModel):
    """How often a device of the type reports: by time, plus "by exception" (on a significant change) in between.

    Devices own the schedule (trackers and loggers are configured this way: Teltonika FMB defaults are
    300 s / 100 m / 10° moving and 3600 s parked; Russian rules for monitored transport ask for at least
    one fix every 30 s; GDP cold-chain practice logs temperature every 5 min). Connectors reject reports
    faster than `min_interval_s`; the offline rule fires after `offline_after_s` of silence.
    """

    heartbeat_s: int | None = Field(None, description="Отчёт при отсутствии изменений; null — только по событиям")
    moving_period_s: int | None = Field(None, description="Транспорт в движении: не реже, чем раз в N с")
    idle_period_s: int | None = Field(None, description="Транспорт стоит с работающим двигателем")
    on_change: dict[str, float] = Field(default_factory=dict,
                                        description="Внеочередной отчёт при изменении метрики на величину "
                                                    "(distance_m — пройденный путь, м)")
    min_interval_s: float = Field(0, description="Чаще коннекторы не принимают (защита от «болтливых» устройств)")
    offline_after_s: int | None = Field(None, description="Молчание дольше — датчик «нет связи»")


REPORTING: dict[SensorType, Reporting] = {
    # moving: a fix every second so every vehicle visibly moves on the map; standing: once a minute
    SensorType.GNSS: Reporting(heartbeat_s=60, moving_period_s=1, idle_period_s=60,
                               on_change={"distance_m": 300, "heading_deg": 10, "speed_kmh": 10},
                               min_interval_s=0.5, offline_after_s=300),
    SensorType.CLIMATE: Reporting(heartbeat_s=120, on_change={"temperature_c": 0.5, "humidity_pct": 3},
                                  min_interval_s=10, offline_after_s=360),
    SensorType.MOTION: Reporting(heartbeat_s=120, on_change={"detected": 1}, min_interval_s=1, offline_after_s=360),
    SensorType.ANPR_CAMERA: Reporting(),  # one event per passing vehicle
    SensorType.ACCESS_CONTROL: Reporting(),  # one event per card swipe
}


def numeric_metrics(sensor_type: SensorType) -> list[str]:
    """Metrics that go to ClickHouse aggregates: numbers and bools (as 0/1)."""
    t = next(t for t in SENSOR_TYPES if t["id"] == sensor_type)
    return [m["key"] for m in t["metrics"] if m["kind"] in ("number", "bool")]


def gnss_sensor_id(vehicle_id: str) -> str:
    """Tracker id convention: v-truck-1 -> gnss-truck-1."""
    return "gnss-" + vehicle_id.removeprefix("v-")


def default_thresholds(sensor_type: SensorType, sensor_id: str) -> list[dict]:
    """Typical limits used by the seed and when a sensor is registered without its own.

    Norm is [min, max]; outside it is a warning, outside [critical_min, critical_max] is critical.
    """
    def th(metric: str, **limits: float) -> dict:
        return {"metric": metric, "nominal": None, "min": None, "max": None,
                "critical_min": None, "critical_max": None} | limits

    match sensor_type:
        case SensorType.CLIMATE if "cold" in sensor_id and "dock" not in sensor_id:  # морозильная камера
            return [th("temperature_c", nominal=-18, min=-22, max=-16, critical_min=-26, critical_max=-12),
                    th("humidity_pct", nominal=90, min=80, max=95, critical_min=70, critical_max=98)]
        case SensorType.CLIMATE if "wh2" in sensor_id or "cold" in sensor_id:  # холодный склад, шлюз камер
            return [th("temperature_c", nominal=4, min=2, max=6, critical_min=0, critical_max=8),
                    th("humidity_pct", nominal=60, min=40, max=75, critical_min=30, critical_max=85)]
        case SensorType.CLIMATE if "server" in sensor_id:
            return [th("temperature_c", nominal=21, min=18, max=24, critical_min=15, critical_max=28),
                    th("humidity_pct", nominal=45, min=30, max=60, critical_min=20, critical_max=70)]
        case SensorType.CLIMATE:
            return [th("temperature_c", nominal=18, min=12, max=24, critical_min=5, critical_max=30),
                    th("humidity_pct", nominal=55, min=30, max=70, critical_min=20, critical_max=85)]
        case SensorType.GNSS:
            return [th("speed_kmh", max=20, critical_max=30),
                    th("fuel_pct", min=15, critical_min=5)]
    return []
