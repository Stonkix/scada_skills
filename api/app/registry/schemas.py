from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from scada_common import Geo, SensorType
from scada_common.enums import VehicleKind


class MetricSpec(BaseModel):
    key: str = Field(..., examples=["temperature_c"], description="Ключ поля в payload события")
    name: str = Field(..., examples=["Температура"])
    unit: str | None = Field(None, examples=["°C"])
    kind: Literal["number", "bool", "string"] = "number"


class SensorTypeInfo(BaseModel):
    id: SensorType
    name: str = Field(..., examples=["Климат (температура и влажность)"])
    is_mobile: bool = Field(..., description="Подвижный датчик: позиция приходит в событиях, а не задана на плане")
    metrics: list[MetricSpec]


class Threshold(BaseModel):
    """Пороги одной метрики. Норма — [min, max]; вне нормы — warning; вне [critical_min, critical_max] — critical."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(..., examples=["temperature_c"])
    nominal: float | None = Field(None, examples=[4.0], description="Номинальное значение")
    min: float | None = Field(None, examples=[2.0])
    max: float | None = Field(None, examples=[6.0])
    critical_min: float | None = Field(None, examples=[0.0])
    critical_max: float | None = Field(None, examples=[8.0])
    version: int = Field(1, ge=1, description="Версия порога; алерт хранит версию, по которой сработал")


class SensorBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$", examples=["clim-wh2-storage"])
    type: SensorType
    name: str = Field(..., min_length=1, examples=["Климат, склад №2: хранение"])
    description: str | None = None
    building_id: str | None = Field(None, examples=["b-wh2"])
    zone_id: str | None = Field(None, examples=["r-wh2-storage"])
    floor: int | None = None
    geo: Geo | None = Field(None, description="Точка установки; null для подвижных датчиков")
    vehicle_id: str | None = Field(None, description="Для GNSS-трекера: машина, на которой он стоит")
    thresholds: list[Threshold] = []
    enabled: bool = True


class SensorCreate(SensorBase):
    pass


class Sensor(SensorBase):
    is_mobile: bool
    created_at: datetime


class SensorUpdate(BaseModel):
    """Частичное обновление: передаются только меняемые поля. Пороги — отдельным PUT /sensors/{id}/thresholds."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1)
    description: str | None = None
    building_id: str | None = None
    zone_id: str | None = None
    floor: int | None = None
    geo: Geo | None = None
    enabled: bool | None = None


class WhitelistEntry(BaseModel):
    id: int
    kind: Literal["card", "plate"]
    value: str = Field(..., examples=["P-000123"])
    holder_name: str | None = Field(None, description="ФИО; маскируется без права people:view_pii")
    holder_org: str | None = None
    allowed_building_ids: list[str] | None = None
    schedule_id: str | None = None
    valid_from: datetime
    valid_to: datetime | None
    active: bool


class WhitelistPage(BaseModel):
    items: list[WhitelistEntry]
    total: int


class BulkRowError(BaseModel):
    row: int = Field(..., description="Номер строки, начиная с 1 (без заголовка CSV)")
    field: str | None = Field(None, examples=["type"])
    message: str = Field(..., examples=["Input should be 'anpr_camera', 'access_control', ..."])


class BulkResult(BaseModel):
    dry_run: bool
    total: int
    valid: int
    created: int = Field(..., description="Сколько датчиков создано; 0 при dry_run или при любой ошибке")
    errors: list[BulkRowError]


class Vehicle(BaseModel):
    id: str = Field(..., examples=["v-truck-1"])
    plate: str = Field(..., examples=["А123ВС77"])
    kind: VehicleKind
    model: str = Field(..., examples=["КАМАЗ 65115"])
    sensor_id: str = Field(..., examples=["gnss-truck-1"], description="GNSS-трекер машины")
    carrier: str | None = Field(None, examples=["ООО «ТрансЛогистик»"])


class HistoryPoint(BaseModel):
    ts: datetime
    min: float
    avg: float
    max: float


class SensorHistory(BaseModel):
    sensor_id: str
    metric: str
    step: Literal["raw", "1m", "1h"] = Field(..., description="raw — сырые точки; 1m/1h — агрегаты")
    points: list[HistoryPoint]


class RoutePoint(BaseModel):
    ts: datetime
    x: float
    y: float
    speed_kmh: float


class VehicleRoute(BaseModel):
    vehicle_id: str
    points: list[RoutePoint]
