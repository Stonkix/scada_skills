from datetime import datetime

from pydantic import BaseModel, Field
from scada_common.enums import AlertKind, AlertStatus, Severity


class Alert(BaseModel):
    id: int
    rule_id: str = Field(..., examples=["rule-cold-storage-temp"])
    rule_version: int
    kind: AlertKind
    severity: Severity
    status: AlertStatus
    title: str = Field(..., examples=["Перегрев холодного склада"])
    message: str = Field(..., examples=["Температура 9.4 °C выше критического порога 8.0 °C"])
    sensor_id: str | None = Field(None, examples=["clim-wh2-storage"])
    vehicle_id: str | None = None
    building_id: str | None = Field(None, examples=["b-wh2"])
    zone_id: str | None = Field(None, examples=["r-wh2-storage"])
    value: float | None = Field(None, examples=[9.4])
    opened_at: datetime
    last_seen_at: datetime = Field(..., description="Последнее событие, подтвердившее алерт (дедупликация)")
    ack_at: datetime | None = None
    ack_by: str | None = None
    resolved_at: datetime | None = None
    comment: str | None = None
    escalation_level: int = Field(0, ge=0, description="Растёт по таймеру, пока алерт не подтверждён")


class AlertPage(BaseModel):
    items: list[Alert]
    total: int


class AlertAction(BaseModel):
    comment: str | None = Field(None, max_length=1000)
