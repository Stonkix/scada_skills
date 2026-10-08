from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.alerts.schemas import Alert, AlertAction, AlertPage, AlertStatus, Severity
from app.mock import world

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _get(alert_id: int) -> Alert:
    if alert_id not in world.alerts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
    return world.alerts[alert_id]


@router.get("", response_model=AlertPage)
def list_alerts(
    status_: Annotated[list[AlertStatus] | None, Query(alias="status")] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    sensor_id: str | None = None,
    building_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertPage:
    """Новые сверху."""
    items = [a for a in world.alerts.values()
             if (not status_ or a.status in status_) and (not severity or a.severity in severity)
             and (sensor_id is None or a.sensor_id == sensor_id) and (building_id is None or a.building_id == building_id)]
    items.sort(key=lambda a: a.opened_at, reverse=True)
    return AlertPage(items=items[offset:offset + limit], total=len(items))


@router.post("/{alert_id}/ack", response_model=Alert)
def ack_alert(alert_id: int, body: AlertAction) -> Alert:
    alert = _get(alert_id)
    if alert.status != AlertStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is {alert.status}, only open alerts can be acknowledged")
    alert = alert.model_copy(update={"status": AlertStatus.ACK, "ack_at": datetime.now(UTC), "ack_by": "dispatcher",
                                     "comment": body.comment or alert.comment})
    world.alerts[alert_id] = alert
    return alert


@router.post("/{alert_id}/resolve", response_model=Alert)
def resolve_alert(alert_id: int, body: AlertAction) -> Alert:
    alert = _get(alert_id)
    if alert.status == AlertStatus.RESOLVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Alert is already resolved")
    alert = alert.model_copy(update={"status": AlertStatus.RESOLVED, "resolved_at": datetime.now(UTC),
                                     "comment": body.comment or alert.comment})
    world.alerts[alert_id] = alert
    return alert
