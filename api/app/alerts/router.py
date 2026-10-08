from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from scada_common import keys
from scada_common.enums import AlertKind
from scada_common.live import Layer, WsAlert
from scada_db import models as m
from sqlalchemy import func, select

from app.alerts.schemas import Alert, AlertAction, AlertPage, AlertStatus, Severity
from app.auth.security import Principal
from app.deps import CH, DB, redis_sync, require

router = APIRouter(prefix="/alerts", tags=["alerts"])
CanView = Annotated[Principal, Depends(require("map:view"))]
CanAck = Annotated[Principal, Depends(require("alerts:ack"))]
LOG_COLUMNS = ["ts", "alert_id", "transition", "rule_id", "rule_version", "kind", "severity", "escalation_level",
               "sensor_id", "vehicle_id", "building_id", "zone_id", "value", "actor"]


def to_model(db: DB, row: m.Alert) -> Alert:
    ack_by = db.scalar(select(m.User.username).where(m.User.id == row.ack_by)) if row.ack_by else None
    return Alert.model_validate({c.key: getattr(row, c.key) for c in m.Alert.__table__.columns} | {"ack_by": ack_by})


def _get(db: DB, alert_id: int) -> m.Alert:
    row = db.get(m.Alert, alert_id, with_for_update=True)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
    return row


def _announce(ch: CH, alert: Alert, transition: str, actor: str) -> None:
    """Same fan-out as the worker: KPI log, notifiers stream, WebSocket channel."""
    now = datetime.now(UTC)
    ch.insert("alerts_log", [[now, alert.id, transition, alert.rule_id, alert.rule_version, alert.kind, alert.severity,
                              alert.escalation_level, alert.sensor_id, alert.vehicle_id, alert.building_id,
                              alert.zone_id, alert.value, actor]], column_names=LOG_COLUMNS)
    building = alert.building_id or keys.SITE
    pipe = redis_sync().pipeline(transaction=False)
    pipe.xadd(keys.STREAM_ALERTS, {"data": alert.model_dump_json(), "transition": transition},
              maxlen=keys.STREAM_ALERTS_MAXLEN, approximate=True)
    pipe.publish(keys.live_channel(building, Layer.ALERTS),
                 WsAlert(ts=now, building_id=building, data=alert).model_dump_json())
    pipe.execute()


@router.get("", response_model=AlertPage)
def list_alerts(
    db: DB,
    _: CanView,
    status_: Annotated[list[AlertStatus] | None, Query(alias="status")] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    kind: Annotated[list[AlertKind] | None, Query()] = None,
    sensor_id: str | None = None,
    vehicle_id: str | None = None,
    building_id: str | None = None,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertPage:
    """Новые сверху. `from`/`to` — по времени открытия."""
    q = select(m.Alert)
    for col, values in ((m.Alert.status, status_), (m.Alert.severity, severity), (m.Alert.kind, kind)):
        if values:
            q = q.where(col.in_(values))
    for col, value in ((m.Alert.sensor_id, sensor_id), (m.Alert.vehicle_id, vehicle_id),
                       (m.Alert.building_id, building_id)):
        if value:
            q = q.where(col == value)
    if start:
        q = q.where(m.Alert.opened_at >= start)
    if end:
        q = q.where(m.Alert.opened_at <= end)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(m.Alert.opened_at.desc(), m.Alert.id.desc()).limit(limit).offset(offset))
    return AlertPage(items=[to_model(db, r) for r in rows], total=total)


@router.get("/{alert_id}", response_model=Alert)
def get_alert(alert_id: int, db: DB, _: CanView) -> Alert:
    row = db.get(m.Alert, alert_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
    return to_model(db, row)


def _comment(row: m.Alert, user: Principal, text: str | None) -> None:
    if text:
        stamp = f"[{datetime.now(UTC):%Y-%m-%d %H:%M} {user.username}] {text}"
        row.comment = f"{row.comment}\n{stamp}" if row.comment else stamp


@router.post("/{alert_id}/ack", response_model=Alert, responses={409: {"description": "Alert is not open"}})
def ack_alert(alert_id: int, body: AlertAction, db: DB, ch: CH, user: CanAck) -> Alert:
    """Подтвердить: эскалация останавливается, тревога остаётся живой до нормализации или ручного закрытия."""
    row = _get(db, alert_id)
    if row.status != AlertStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is {row.status}, only open alerts can be acknowledged")
    row.status, row.ack_at, row.ack_by = AlertStatus.ACK, datetime.now(UTC), user.id
    _comment(row, user, body.comment)
    db.commit()
    alert = to_model(db, row)
    _announce(ch, alert, "ack", user.username)
    return alert


@router.post("/{alert_id}/resolve", response_model=Alert, responses={409: {"description": "Already resolved"}})
def resolve_alert(alert_id: int, body: AlertAction, db: DB, ch: CH, user: CanAck) -> Alert:
    """Закрыть вручную. Если условие всё ещё выполняется, worker откроет новую тревогу."""
    row = _get(db, alert_id)
    if row.status == AlertStatus.RESOLVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Alert is already resolved")
    row.status, row.resolved_at = AlertStatus.RESOLVED, datetime.now(UTC)
    _comment(row, user, body.comment)
    db.commit()
    alert = to_model(db, row)
    redis_sync().delete(keys.alert_open(*row.dedup_key.split(":", 1)))  # the worker re-raises if still true
    _announce(ch, alert, "resolved", user.username)
    return alert


@router.post("/{alert_id}/comment", response_model=Alert)
def comment_alert(alert_id: int, body: AlertAction, db: DB, user: CanAck) -> Alert:
    if not body.comment:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "comment is required")
    row = _get(db, alert_id)
    _comment(row, user, body.comment)
    db.commit()
    return to_model(db, row)
