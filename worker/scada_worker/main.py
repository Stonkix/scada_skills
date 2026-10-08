"""Worker: consumes stream:events, writes ClickHouse, keeps live state, runs alert rules.

Scale-out: run more replicas; each joins the same consumer group under its own
name (WORKER_NAME, default hostname) and Redis spreads messages between them.
Rule timers and late-event checks are per replica, which is exact for one replica
and approximate beyond that (a known trade-off; partition by sensor to tighten it).
"""

import asyncio
import logging
import os
import socket
import time
from datetime import UTC, datetime

import redis.asyncio as aioredis
from scada_common import keys
from scada_db import clickhouse
from scada_db.config import settings

from scada_worker import registry
from scada_worker.alerts import AlertEngine
from scada_worker.live import Live
from scada_worker.processor import Processor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

NAME = os.environ.get("WORKER_NAME") or socket.gethostname()
BATCH = int(os.environ.get("WORKER_BATCH", "2000"))
BLOCK_MS = int(os.environ.get("WORKER_BLOCK_MS", "1000"))
STATS_KEY = "worker:stats"


class Worker:
    def __init__(self) -> None:
        self.redis = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
        self.snap: registry.Snapshot | None = None
        self.alerts = AlertEngine(self.redis)
        self.live = Live(self.redis)
        self.processor = Processor(self.redis, clickhouse.client(), self.alerts, self.live)
        self.totals = {"received": 0, "stored": 0, "duplicates": 0, "invalid": 0, "batches": 0}
        self.last = {"size": 0, "ms": 0.0, "insert_ms": 0.0}

    async def refresh(self) -> None:
        self.snap = await asyncio.to_thread(registry.load, datetime.now(UTC))

    async def _ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(keys.STREAM_EVENTS, keys.WORKER_GROUP, id="0", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _batch(self, start: str) -> int:
        resp = await self.redis.xreadgroup(keys.WORKER_GROUP, NAME, {keys.STREAM_EVENTS: start}, count=BATCH,
                                           block=None if start == "0" else BLOCK_MS)
        entries = resp[0][1] if resp else []
        if not entries:
            return 0
        started = time.perf_counter()
        res = await self.processor.handle(entries, self.snap, recovering=start == "0")
        await self.redis.xack(keys.STREAM_EVENTS, keys.WORKER_GROUP, *[i for i, _ in entries])
        for k in ("received", "stored", "duplicates", "invalid"):
            self.totals[k] += getattr(res, k)
        self.totals["batches"] += 1
        self.last = {"size": len(entries), "ms": round((time.perf_counter() - started) * 1000, 1),
                     "insert_ms": round(res.insert_ms, 1)}
        return len(entries)

    async def consume(self) -> None:
        while await self._batch("0"):  # first finish what a previous run left unacknowledged
            pass
        while True:
            try:
                await self._batch(">")
            except aioredis.ConnectionError as e:
                log.warning("redis unavailable (%s), retrying", e)
                await asyncio.sleep(2)

    async def every(self, seconds: float, fn, name: str) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await fn()
            except Exception:
                log.exception("%s failed", name)

    async def write_stats(self) -> None:
        lag = None
        for g in await self.redis.xinfo_groups(keys.STREAM_EVENTS):
            if g["name"] == keys.WORKER_GROUP:
                lag = g.get("lag")
        await self.redis.hset(STATS_KEY, mapping={
            **{f"{k}_total": v for k, v in self.totals.items()},
            "last_batch_size": self.last["size"], "last_batch_ms": self.last["ms"],
            "last_insert_ms": self.last["insert_ms"], "lag": lag if lag is not None else -1,
            "alerts_open": len(self.alerts.open), "alerts_opened_total": self.alerts.opened,
            "alerts_resolved_total": self.alerts.resolved, "updated_at": time.time(), "worker": NAME})

    async def offline(self) -> None:
        await self.alerts.check_offline(self.snap, datetime.now(UTC), self.live.mark_offline)
        await self.processor.flush_alert_log()

    async def escalation(self) -> None:
        await self.alerts.check_escalation(self.snap, datetime.now(UTC))
        await self.processor.flush_alert_log()

    async def run(self) -> None:
        await self._ensure_group()
        await self.refresh()
        await self.alerts.load_open()
        await self.live.load_people(list(self.snap.building_types))
        log.info("worker %s: %d sensors, %d rules", NAME, len(self.snap.sensors), len(self.snap.rules))
        await asyncio.gather(
            self.consume(),
            self.every(30, self.refresh, "registry refresh"),
            self.every(10, self.offline, "offline check"),
            self.every(15, self.escalation, "escalation"),
            self.every(5, self.write_stats, "stats"),
        )


def main() -> None:
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
