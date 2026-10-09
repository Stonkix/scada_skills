"""Turns rule signals into stored alerts: one live alert per (rule, object), lifecycle, fan-out.

State of truth is Postgres `alerts`; Redis `alert:open:*` mirrors which alerts are
live so other processes (API) can see it cheaply; ClickHouse `alerts_log` records
every transition for KPIs. Each opened/changed alert is published to
`stream:alerts` (notifiers) and pub/sub `live:{building}:alerts` (WebSocket).
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from prometheus_client import Counter
from scada_common import keys, metrics
from scada_common.alerts import Alert
from scada_common.live import Layer, WsAlert
from scada_db import models as m
from scada_db.postgres import engine
from sqlalchemy import and_, func, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from scada_worker.registry import Snapshot
from scada_worker.rules import Signal

log = logging.getLogger(__name__)

TOUCH_EVERY_S = 30  # how often last_seen_at of a live alert is written back
MAX_ESCALATION = 3
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
A = m.Alert.__table__
TRANSITIONS = Counter(metrics.WORKER_ALERT_TRANSITIONS.removesuffix("_total"), "Alert lifecycle transitions", ["transition"])


class AlertEngine:
    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis
        self.open: dict[str, tuple[int, str]] = {}  # dedup key -> (alert id, severity)
        self._touched: dict[int, float] = {}
        self.log_rows: list[list[Any]] = []  # alerts_log rows, flushed by the processor
        self.opened = self.resolved = 0

    # --- startup ----------------------------------------------------------------------------------

    async def load_open(self) -> None:
        def q():
            with engine().connect() as c:
                return c.execute(select(A.c.id, A.c.dedup_key, A.c.severity).where(A.c.status != "resolved")).all()

        rows = await asyncio.to_thread(q)
        self.open = {r.dedup_key: (r.id, r.severity) for r in rows}
        pipe = self.redis.pipeline(transaction=False)
        async for k in self.redis.scan_iter(keys.alert_open("*", "*")):
            pipe.delete(k)
        for key, (alert_id, _) in self.open.items():
            pipe.set(keys.alert_open(*key.split(":", 1)), alert_id)
        await pipe.execute()
        log.info("alerts: %d live at startup", len(self.open))

    # --- signals ----------------------------------------------------------------------------------

    async def apply(self, signals: list[Signal], snap: Snapshot, now: datetime) -> None:
        for s in signals:
            try:
                if s.action == "open":
                    await self._open(s, snap, now)
                elif s.key in self.open:
                    await self._resolve(s.key, snap, now, comment="auto: условие нормализовалось")
            except Exception:  # one failing alert write must not block the rest of the batch
                log.exception("alert %s failed", s.key)

    async def _open(self, s: Signal, snap: Snapshot, now: datetime) -> None:
        if s.key in self.open:
            alert_id, severity = self.open[s.key]
            if SEVERITY_RANK[s.severity] > SEVERITY_RANK[severity]:
                row = await self._update(alert_id, severity=s.severity, last_seen_at=now, message=s.message,
                                         value=s.value)
                if row is not None:
                    self.open[s.key] = (alert_id, s.severity)
                    await self._emit(row, "severity", snap, now)
                    return
            elif time.monotonic() - self._touched.get(alert_id, 0) < TOUCH_EVERY_S:
                return
            else:
                row = await self._update(alert_id, last_seen_at=now, value=s.value)
                self._touched[alert_id] = time.monotonic()
                if row is not None:
                    return
            # resolved behind our back (operator in the UI): forget it; if the condition persists, re-raise
            await self._forget(s.key)

        values = {"rule_id": s.rule.id, "rule_version": s.rule.version, "kind": s.rule.kind, "severity": s.severity,
                  "status": "open", "title": s.title[:256], "message": s.message, "sensor_id": s.sensor_id,
                  "vehicle_id": s.vehicle_id, "building_id": s.building_id, "zone_id": s.zone_id, "value": s.value,
                  "dedup_key": s.key, "opened_at": now, "last_seen_at": now}

        def ins():
            with engine().begin() as c:
                return c.execute(insert(A).values(**values).returning(A)).one()

        try:
            row = await asyncio.to_thread(ins)
        except IntegrityError:  # someone else holds the key: adopt it
            await self.load_open()
            return
        self.open[s.key] = (row.id, s.severity)
        self._touched[row.id] = time.monotonic()
        await self.redis.set(keys.alert_open(s.rule.id, s.object_id), row.id)
        self.opened += 1
        await self._emit(row, "opened", snap, now)

    async def _resolve(self, key: str, snap: Snapshot, now: datetime, comment: str) -> None:
        alert_id, _ = self.open[key]
        row = await self._update(alert_id, status="resolved", resolved_at=now,
                                 comment=func.coalesce(A.c.comment, comment))
        await self._forget(key)
        if row is not None:
            self.resolved += 1
            await self._emit(row, "resolved", snap, now)

    async def _forget(self, key: str) -> None:
        alert_id, _ = self.open.pop(key, (None, None))
        self._touched.pop(alert_id, None)
        await self.redis.delete(keys.alert_open(*key.split(":", 1)))

    async def _update(self, alert_id: int, **values) -> Any:
        """Update a not-yet-resolved alert; None if it was resolved meanwhile."""
        def q():
            with engine().begin() as c:
                return c.execute(update(A).where(and_(A.c.id == alert_id, A.c.status != "resolved"))
                                 .values(**values).returning(A)).one_or_none()

        return await asyncio.to_thread(q)

    # --- timers -----------------------------------------------------------------------------------

    async def check_offline(self, snap: Snapshot, now: datetime, mark_offline) -> None:
        rule = next(iter(snap.rules_of("offline")), None)
        if rule is None:
            return
        timeout, mobile_timeout = rule.params.get("timeout_s", 300), rule.params.get("mobile_timeout_s", 120)
        types = rule.params.get("sensor_types")  # None: every sensor is expected to report periodically
        stale = await self.redis.zrangebyscore(keys.LAST_SEEN, 0, now.timestamp() - min(timeout, mobile_timeout),
                                               withscores=True)
        signals = []
        for sensor_id, seen in stale:
            sensor = snap.sensors.get(sensor_id)
            silent = now.timestamp() - seen
            if sensor is None or silent < (mobile_timeout if sensor.is_mobile else timeout):
                continue
            if types is not None and sensor.type not in types:
                continue
            if f"{rule.id}:{sensor_id}" not in self.open:
                await mark_offline(sensor)
            signals.append(Signal("open", rule, sensor_id, rule.severity, f"Датчик не на связи: {sensor.name}",
                                  f"Нет данных от {sensor_id} {silent / 60:.0f} мин", round(silent),
                                  sensor_id=sensor_id, vehicle_id=sensor.vehicle_id, building_id=sensor.building_id,
                                  zone_id=sensor.zone_id))
        await self.apply(signals, snap, now)

    def offline_cleared(self, sensor_id: str, snap: Snapshot) -> list[Signal]:
        """A sensor that speaks again resolves its offline alert."""
        return [Signal("clear", r, sensor_id) for r in snap.rules_of("offline") if f"{r.id}:{sensor_id}" in self.open]

    async def check_escalation(self, snap: Snapshot, now: datetime) -> None:
        def q():
            with engine().begin() as c:
                return c.execute(text("""
                    UPDATE alerts a SET escalation_level = a.escalation_level + 1
                    FROM alert_rules r
                    WHERE a.rule_id = r.id AND a.rule_version = r.version AND a.status = 'open'
                      AND r.escalate_after_s IS NOT NULL AND a.escalation_level < :max_level
                      AND now() - a.opened_at >= make_interval(secs => r.escalate_after_s * (a.escalation_level + 1))
                    RETURNING a.*"""), {"max_level": MAX_ESCALATION}).all()

        for row in await asyncio.to_thread(q):
            await self._emit(row, "escalated", snap, now)

    # --- fan-out ----------------------------------------------------------------------------------

    def to_model(self, row: Any, snap: Snapshot) -> Alert:
        data = dict(row._mapping)
        data["ack_by"] = snap.usernames.get(data["ack_by"]) if data.get("ack_by") else None
        return Alert.model_validate(data)

    async def _emit(self, row: Any, transition: str, snap: Snapshot, now: datetime) -> None:
        alert = self.to_model(row, snap)
        self.log_rows.append([now, alert.id, transition, alert.rule_id, alert.rule_version, alert.kind,
                              alert.severity, alert.escalation_level, alert.sensor_id, alert.vehicle_id,
                              alert.building_id, alert.zone_id, alert.value, None])
        building = alert.building_id or keys.SITE
        pipe = self.redis.pipeline(transaction=False)
        pipe.xadd(keys.STREAM_ALERTS, {"data": alert.model_dump_json(), "transition": transition},
                  maxlen=keys.STREAM_ALERTS_MAXLEN, approximate=True)
        pipe.publish(keys.live_channel(building, Layer.ALERTS),
                     WsAlert(ts=datetime.now(UTC), building_id=building, data=alert).model_dump_json())
        await pipe.execute()
        TRANSITIONS.labels(transition).inc()
        log.info("alert %s %s: %s", transition, alert.id, alert.title)


ALERTS_LOG_COLUMNS = ["ts", "alert_id", "transition", "rule_id", "rule_version", "kind", "severity",
                      "escalation_level", "sensor_id", "vehicle_id", "building_id", "zone_id", "value", "actor"]
