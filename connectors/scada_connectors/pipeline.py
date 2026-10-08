"""The ingest path shared by HTTP and MQTT: authorize -> adapt -> check registry -> validate -> publish.

Rejected items never disappear: they go to stream:dlq with the reason, so a
misconfigured vendor shows up in one place instead of silently losing data.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from pydantic import ValidationError
from scada_common import keys, parse_event

from scada_connectors.adapters import ADAPTERS, Adapter
from scada_connectors.registry import ApiKeyInfo, Registry

MAX_FUTURE_SKEW = timedelta(minutes=5)
DLQ_RAW_LIMIT = 4096


class IngestError(Exception):
    """Whole-request failure (auth, rate limit, unknown adapter)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Rejection:
    index: int
    reason: str
    detail: Any = None


@dataclass
class IngestResult:
    accepted: list[str] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)


@dataclass
class Stats:
    accepted: int = 0
    rejected: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)


def _errors(e: ValidationError) -> list[dict]:
    return [{"loc": list(err["loc"]), "msg": err["msg"]} for err in e.errors(include_url=False)]


class Pipeline:
    def __init__(self, redis: aioredis.Redis, registry: Registry) -> None:
        self.redis = redis
        self.registry = registry
        self.stats = Stats()

    def get_adapter(self, name: str) -> Adapter:
        if name not in ADAPTERS:
            raise IngestError(404, f"Unknown adapter '{name}'. Available: {', '.join(ADAPTERS)}")
        return ADAPTERS[name]

    async def authorize(self, api_key: str | None, count: int) -> ApiKeyInfo:
        key = await self.registry.check_key(api_key)
        if key is None:
            raise IngestError(401, "Missing or invalid API key")
        window = keys.rate_limit(key.id, int(time.time() // 60))
        used = await self.redis.incrby(window, count)
        if used == count:
            await self.redis.expire(window, 120)
        if used > key.rate_limit_per_min:
            raise IngestError(429, f"Rate limit {key.rate_limit_per_min} events/min exceeded")
        return key

    async def _convert(self, adapter: Adapter, item: Any, key: ApiKeyInfo, device_hint: str | None,
                       now: datetime) -> tuple[Any, None] | tuple[None, tuple[str, Any]]:
        try:
            raw = adapter.raw_model.model_validate(item)
        except ValidationError as e:
            return None, ("invalid_payload", _errors(e))
        if device_hint is not None and adapter.device_id(raw) != device_hint:
            return None, ("topic_mismatch", f"topic device '{device_hint}' != payload '{adapter.device_id(raw)}'")
        fields = adapter.convert(raw, self.registry.georef)

        sensor = await self.registry.sensor(fields["sensor_id"])
        if sensor is None:
            return None, ("unknown_sensor", f"Sensor '{fields['sensor_id']}' is not registered")
        if not sensor.enabled:
            return None, ("sensor_disabled", fields["sensor_id"])
        sensor_type = adapter.sensor_type or fields.get("type")
        if sensor_type != sensor.type:
            return None, ("type_mismatch", f"Sensor '{sensor.id}' is {sensor.type}, got {sensor_type}")
        if key.sensor_types is not None and sensor.type not in key.sensor_types:
            return None, ("forbidden_type", f"API key '{key.name}' may not send {sensor.type}")

        fields["type"] = sensor.type
        fields["received_at"] = now
        if not sensor.is_mobile:  # fixed sensors: position and zone come from the registry
            if fields.get("geo") is None and sensor.x is not None:
                fields["geo"] = {"x": sensor.x, "y": sensor.y, "floor": sensor.floor}
            if not fields.get("zone_id"):
                fields["zone_id"] = sensor.zone_id
        try:
            event = parse_event(fields)
        except ValidationError as e:
            return None, ("contract_violation", _errors(e))
        if event.ts > now + MAX_FUTURE_SKEW:
            return None, ("clock_skew", f"ts {event.ts.isoformat()} is in the future")
        return event, None

    async def ingest(self, adapter_name: str, items: list[Any], api_key: str | None,
                     device_hint: str | None = None, source: str = "http") -> IngestResult:
        adapter = self.get_adapter(adapter_name)
        key = await self.authorize(api_key, len(items))
        now = datetime.now(UTC)
        result = IngestResult()
        pipe = self.redis.pipeline(transaction=False)
        for i, item in enumerate(items):
            event, failure = await self._convert(adapter, item, key, device_hint, now)
            if event is not None:
                pipe.xadd(keys.STREAM_EVENTS, {"data": event.model_dump_json()},
                          maxlen=keys.STREAM_EVENTS_MAXLEN, approximate=True)
                result.accepted.append(str(event.event_id))
                continue
            reason, detail = failure
            result.rejected.append(Rejection(i, reason, detail))
            pipe.xadd(keys.STREAM_DLQ, {
                "reason": reason, "adapter": adapter_name, "source": source, "api_key": key.name,
                "detail": json.dumps(detail, ensure_ascii=False, default=str),
                "raw": json.dumps(item, ensure_ascii=False, default=str)[:DLQ_RAW_LIMIT],
                "received_at": now.isoformat(),
            }, maxlen=keys.STREAM_DLQ_MAXLEN, approximate=True)
            self.stats.by_reason[reason] = self.stats.by_reason.get(reason, 0) + 1
        await pipe.execute()
        self.stats.accepted += len(result.accepted)
        self.stats.rejected += len(result.rejected)
        return result
