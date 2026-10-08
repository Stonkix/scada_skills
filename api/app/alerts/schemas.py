from pydantic import BaseModel, Field
from scada_common.alerts import Alert
from scada_common.enums import AlertKind, AlertStatus, Severity

__all__ = ["Alert", "AlertAction", "AlertKind", "AlertPage", "AlertStatus", "Severity"]


class AlertPage(BaseModel):
    items: list[Alert]
    total: int


class AlertAction(BaseModel):
    comment: str | None = Field(None, max_length=1000)
