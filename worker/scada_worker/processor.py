"""One batch from stream:events, end to end.

    parse -> drop duplicates (event_id) -> enrich (geozones, transitions) -> ClickHouse
          -> live state + alert rules -> XACK

Messages are acknowledged only after ClickHouse accepted the batch; until then they
stay pending in the consumer group and are re-read after a crash. Live state and
alerts run after the insert: a failure there is logged, never loses telemetry.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from pydantic import ValidationError
from scada_common import Event, SensorType, catalog, keys, parse_event

from scada_worker.alerts import ALERTS_LOG_COLUMNS, AlertEngine
from scada_worker.live import Live
from scada_worker.registry import Snapshot
from scada_worker.rules import Context, RuleState, evaluate, sensor_status

log = logging.getLogger(__name__)

TELEMETRY_COLUMNS = ["event_id", "schema_v", "sensor_id", "type", "ts", "received_at", "x", "y", "floor", "zone_id",
                     "metrics", "payload"]
ZONE_EVENT_COLUMNS = ["ts", "event_id", "sensor_id", "vehicle_id", "zone_id", "transition", "x", "y"]
METRIC_KEYS = {t["id"]: catalog.numeric_metrics(t["id"]) for t in catalog.SENSOR_TYPES}


@dataclass
class BatchResult:
    received: int = 0
    stored: int = 0
    duplicates: int = 0
    invalid: int = 0
    insert_ms: float = 0.0


def telemetry_row(e: Event) -> list[Any]:
    payload = e.payload.model_dump(mode="json")
    metrics = {k: float(payload[k]) for k in METRIC_KEYS[e.type] if payload.get(k) is not None}
    geo = e.geo
    return [e.event_id, e.schema_v, e.sensor_id, e.type.value, e.ts, e.received_at or e.ts,
            geo.x if geo else None, geo.y if geo else None, geo.floor if geo else None, e.zone_id,
            metrics, json.dumps(payload, ensure_ascii=False)]


class Processor:
    def __init__(self, redis: aioredis.Redis, ch, alerts: AlertEngine, live: Live) -> None:
        self.redis = redis
        self.ch = ch
        self.alerts = alerts
        self.live = live
        self.rule_state = RuleState()

    async def _insert(self, table: str, rows: list[list[Any]], columns: list[str]) -> None:
        """Retry until ClickHouse accepts: the batch is still unacked, so backing off loses nothing."""
        delay = 1.0
        while True:
            try:
                await asyncio.to_thread(self.ch.insert, table, rows, column_names=columns)
                return
            except Exception as e:  # noqa: BLE001 — network, overload, restart: all retryable
                log.warning("clickhouse insert into %s failed (%s), retry in %.0fs", table, e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    async def _dedup(self, events: list[Event]) -> list[Event]:
        pipe = self.redis.pipeline(transaction=False)
        for e in events:
            pipe.set(keys.seen_event(str(e.event_id)), 1, nx=True, ex=keys.SEEN_EVENT_TTL_S)
        fresh = await pipe.execute()
        return [e for e, first in zip(events, fresh) if first]

    async def handle(self, entries: list[tuple[str, dict]], snap: Snapshot, recovering: bool = False) -> BatchResult:
        res = BatchResult(received=len(entries))
        events: list[Event] = []
        dlq = self.redis.pipeline(transaction=False)
        for msg_id, fields in entries:
            try:
                events.append(parse_event(fields["data"]))
            except (KeyError, ValidationError) as e:
                res.invalid += 1
                dlq.xadd(keys.STREAM_DLQ, {"reason": "worker_invalid", "detail": str(e)[:1000],
                                           "raw": json.dumps(fields, ensure_ascii=False)[:4096], "source": "worker"},
                         maxlen=keys.STREAM_DLQ_MAXLEN, approximate=True)
        if res.invalid:
            await dlq.execute()

        # A recovered (pending) batch was never acked, so it was never fully processed: don't drop it as seen.
        unique = events if recovering else await self._dedup(events)
        res.duplicates = len(events) - len(unique)
        unique.sort(key=lambda e: e.ts)

        contexts, zone_rows = [], []
        for e in unique:
            sensor = snap.sensors.get(e.sensor_id)
            if sensor is None:  # removed from the registry after connectors accepted it
                continue
            ctx = Context(e, sensor, snap.vehicles.get(sensor.vehicle_id) if sensor.vehicle_id else None)
            if e.type == SensorType.GNSS and e.geo is not None:
                ctx.zones = snap.zones_at(e.geo.x, e.geo.y)
                specific = [z for z in ctx.zones if z.zone_type != "speed"]
                e.zone_id = specific[0].id if specific else None
                if not self.live.is_late(e):
                    before = await self.live.previous_zones(e.sensor_id)
                    now_in = {z.id for z in ctx.zones}
                    ctx.entered = [z for z in ctx.zones if z.id not in before]
                    ctx.exited = [z for z in snap.geozones if z.id in before - now_in]
                    self.live.zones[e.sensor_id] = now_in
                    zone_rows += [[e.ts, e.event_id, e.sensor_id, sensor.vehicle_id, z.id, t, e.geo.x, e.geo.y]
                                  for t, zs in (("enter", ctx.entered), ("exit", ctx.exited)) for z in zs]
            elif e.type == SensorType.ACCESS_CONTROL and e.payload.granted and sensor.zone_id:
                zone_rows.append([e.ts, e.event_id, e.sensor_id, None, sensor.zone_id,
                                  "enter" if e.payload.direction == "in" else "exit", None, None])
            contexts.append(ctx)

        started = time.perf_counter()
        if unique:
            await self._insert("telemetry", [telemetry_row(e) for e in unique], TELEMETRY_COLUMNS)
        if zone_rows:
            await self._insert("zone_events", zone_rows, ZONE_EVENT_COLUMNS)
        res.insert_ms = (time.perf_counter() - started) * 1000
        res.stored = len(unique)

        now = datetime.now(UTC)
        pipe = self.redis.pipeline(transaction=False)
        for ctx in contexts:
            e = ctx.event
            try:
                signals = self.alerts.offline_cleared(e.sensor_id, snap)
                if not self.live.is_late(e):
                    status = sensor_status(e.payload.model_dump(), ctx.sensor.thresholds)
                    self.live.stage(pipe, e, ctx.sensor, ctx.vehicle, status)
                    if e.type == SensorType.ACCESS_CONTROL and e.payload.granted and ctx.sensor.zone_id in snap.building_types:
                        await self.live.count_person(ctx.sensor.zone_id, 1 if e.payload.direction == "in" else -1)
                    if e.type == SensorType.MOTION and ctx.sensor.building_id:
                        ctx.people = self.live.people.get(ctx.sensor.building_id, 0)
                    signals += evaluate(ctx, snap, self.rule_state)
                await self.alerts.apply(signals, snap, now)
            except Exception:
                log.exception("live/alerts failed for %s", e.event_id)
        await pipe.execute()
        await self.flush_alert_log()
        return res

    async def flush_alert_log(self) -> None:
        if self.alerts.log_rows:
            rows, self.alerts.log_rows = self.alerts.log_rows, []
            await self._insert("alerts_log", rows, ALERTS_LOG_COLUMNS)
