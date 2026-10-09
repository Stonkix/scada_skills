from datetime import datetime

from pydantic import BaseModel, Field
from scada_common.enums import TripStatus


class Trip(BaseModel):
    """Рейс по путевому листу: откуда, куда, что везём. Приходит из транспортной системы через коннекторы."""

    id: str = Field(..., examples=["ПЛ-2026-000123"], description="Номер путевого листа")
    vehicle_id: str = Field(..., examples=["v-truck-1"])
    plate: str = Field(..., examples=["А123ВС77"])
    origin_site_id: str = Field(..., examples=["s-podolsk"])
    destination_site_id: str = Field(..., examples=["s-chekhov"])
    route_id: str | None = Field(None, examples=["route-chekhov-podolsk"], description="Трасса между площадками")
    cargo: str = Field(..., examples=["Металлоконструкции"])
    weight_t: float = Field(..., examples=[12.4])
    pallets: int | None = None
    temperature_mode: str | None = Field(None, examples=["-18…-22 °C"])
    driver_name: str | None = Field(None, description="ФИО; маскируется без права people:view_pii")
    status: TripStatus
    planned_departure: datetime | None = None
    departed_at: datetime | None = None
    eta: datetime | None = None
    arrived_at: datetime | None = None
    updated_at: datetime
