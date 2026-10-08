from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from scada_common import SensorType


class Endpoints(BaseModel):
    """Где устройства и шлюзы подключаются к уровню коннекторов (адреса снаружи, для инструкций)."""

    http_base: str = Field(..., examples=["http://localhost:8001"])
    mqtt_host: str = Field(..., examples=["localhost"])
    mqtt_port: int = Field(..., examples=[1883])
    api_key_header: str = "X-API-Key"
    mqtt_key_property: str = Field("x-api-key", description="MQTT 5 user property с ключом")


class ApiKey(BaseModel):
    id: int
    name: str
    key_prefix: str = Field(..., description="Первые символы ключа: по ним ключ узнают в логах и DLQ")
    sensor_types: list[SensorType] | None = Field(None, description="null — любой тип датчиков")
    rate_limit_per_min: int
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, examples=["Шлюз климата, склад №2"])
    sensor_types: list[SensorType] | None = None
    rate_limit_per_min: int = Field(6000, ge=1, le=1_000_000)


class ApiKeyIssued(ApiKey):
    key: str = Field(..., description="Показывается один раз; в базе хранится только SHA-256")


class Rejection(BaseModel):
    id: str = Field(..., description="id записи в stream:dlq (время приёма)")
    received_at: str | None
    reason: str = Field(..., examples=["unknown_sensor"])
    adapter: str | None
    source: str | None = Field(None, description="http | mqtt | worker")
    api_key: str | None = Field(None, description="Имя ключа, с которым пришло сообщение")
    detail: Any = None
    raw: str | None = Field(None, description="Исходное сообщение (обрезано до 4 КБ)")
