"""In-memory copy of what connectors need from Postgres: API keys, sensors, the plan's georef.

Refreshed periodically; a miss (new key or sensor) triggers an early reload,
throttled so a flood of unknown ids cannot hammer the database.
"""

import asyncio
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

from geoalchemy2.shape import to_shape
from scada_common import SensorType
from scada_common.geo import Georef
from scada_db import models as m
from scada_db.postgres import engine
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

KEY_PREFIX_LEN = 12  # matches the seed: key_prefix = key[:12]
REFRESH_S = 30
MISS_RELOAD_MIN_S = 5


@dataclass(frozen=True)
class ApiKeyInfo:
    id: int
    name: str
    key_hash: str
    sensor_types: frozenset[str] | None
    rate_limit_per_min: int


@dataclass(frozen=True)
class SensorInfo:
    id: str
    type: SensorType
    enabled: bool
    is_mobile: bool
    x: float | None
    y: float | None
    floor: int | None
    zone_id: str | None


class Registry:
    def __init__(self) -> None:
        self.keys: dict[str, ApiKeyInfo] = {}
        self.sensors: dict[str, SensorInfo] = {}
        self.georef = Georef(0, 0)
        self.loaded_at = 0.0
        self._lock = asyncio.Lock()

    def _load(self) -> tuple[dict, dict, Georef]:
        with Session(engine()) as s:
            keys = {k.key_prefix: ApiKeyInfo(k.id, k.name, k.key_hash,
                                             frozenset(k.sensor_types) if k.sensor_types else None,
                                             k.rate_limit_per_min)
                    for k in s.scalars(select(m.ApiKey).where(m.ApiKey.revoked_at.is_(None)))}
            sensors = {}
            for row in s.scalars(select(m.Sensor)):
                pt = to_shape(row.geom) if row.geom is not None else None
                sensors[row.id] = SensorInfo(row.id, SensorType(row.type), row.enabled, row.is_mobile,
                                             pt.x if pt else None, pt.y if pt else None, row.floor, row.zone_id)
            layout = s.scalars(select(m.Layout.geojson).order_by(m.Layout.version.desc()).limit(1)).first()
        return keys, sensors, Georef.from_layout(layout) if layout else Georef(0, 0)

    async def reload(self) -> None:
        async with self._lock:
            self.keys, self.sensors, self.georef = await asyncio.to_thread(self._load)
            self.loaded_at = time.monotonic()
            log.info("registry: %d keys, %d sensors", len(self.keys), len(self.sensors))

    async def reload_on_miss(self) -> None:
        if time.monotonic() - self.loaded_at > MISS_RELOAD_MIN_S:
            await self.reload()

    async def refresh_forever(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_S)
            try:
                await self.reload()
            except Exception:  # keep serving the last good copy
                log.exception("registry refresh failed")

    async def check_key(self, api_key: str | None) -> ApiKeyInfo | None:
        if not api_key or len(api_key) < KEY_PREFIX_LEN:
            return None
        prefix = api_key[:KEY_PREFIX_LEN]
        if prefix not in self.keys:
            await self.reload_on_miss()
        info = self.keys.get(prefix)
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        return info if info and hmac.compare_digest(info.key_hash, digest) else None

    async def sensor(self, sensor_id: str) -> SensorInfo | None:
        if sensor_id not in self.sensors:
            await self.reload_on_miss()
        return self.sensors.get(sensor_id)
