from datetime import datetime

from pydantic import BaseModel, Field

from app.alerts.schemas import Alert
from app.registry.schemas import RoutePoint


class VehicleKpi(BaseModel):
    vehicle_id: str
    plate: str
    mileage_km: float = Field(..., description="По одометру трекера за период")
    moving_min: float = Field(..., description="Скорость > 1 км/ч")
    idle_min: float = Field(..., description="Стоит с работающим двигателем")
    stopped_min: float = Field(..., description="Стоит с заглушенным двигателем")
    utilization_pct: float = Field(..., description="Доля времени в движении; всё считается только на территории")


class HourCount(BaseModel):
    hour: datetime
    entries: int
    exits: int


class GateKpi(BaseModel):
    entries: int = Field(..., description="Въезды в зону КПП-1 по ГЛОНАСС")
    exits: int
    plates_recognized: int = Field(..., description="Распознаваний номеров камерами КПП")
    by_hour: list[HourCount]


class BuildingTraffic(BaseModel):
    building_id: str
    name: str
    entries: int = Field(..., description="Проходов внутрь по СКУД")
    people_now: int


class RuleCount(BaseModel):
    rule_id: str
    count: int


class AlertsKpi(BaseModel):
    opened: int
    by_severity: dict[str, int]
    top_rules: list[RuleCount]
    mean_ack_s: float | None = Field(..., description="Среднее время от открытия до подтверждения")
    open_now: int


class KpiResponse(BaseModel):
    period_from: datetime
    period_to: datetime
    vehicles: list[VehicleKpi]
    gate: GateKpi
    buildings: list[BuildingTraffic]
    alerts: AlertsKpi


class HeatCell(BaseModel):
    x: float = Field(..., description="Центр ячейки, м")
    y: float
    count: int


class Heatmap(BaseModel):
    cell_m: float
    source: str
    max_count: int
    cells: list[HeatCell]


class ReplayTrack(BaseModel):
    vehicle_id: str
    points: list[RoutePoint]


class Replay(BaseModel):
    period_from: datetime
    period_to: datetime
    step_s: int
    tracks: list[ReplayTrack]
    alerts: list[Alert] = Field(..., description="Тревоги, открытые в этом окне: для отметок на таймлайне")
