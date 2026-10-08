"""Live state in Redis + change notifications for the WebSocket.

live:sensor:{id} is a hash with `doc` (SensorLive / VehicleLive JSON, exactly what
the API serves), `kind`, `ts` and, for vehicles, `zones` (geozones it is in).
Every change is published on live:{building|site}:{layer} as a WsServerMessage.
"""

import json
from datetime import UTC, datetime

import redis.asyncio as aioredis
from scada_common import Event, SensorType, keys
from scada_common.live import (
    Layer,
    SensorLive,
    SensorStatus,
    VehicleLive,
    VehicleStatus,
    WsSensorUpdate,
    WsVehicleUpdate,
    WsZoneUpdate,
    ZoneOccupancy,
)

from scada_worker.registry import SensorRef, VehicleRef

# INCRBY that never goes below zero (missed exits must not produce negative headcounts)
_BOUNDED_INCR = """
local v = redis.call('INCRBY', KEYS[1], ARGV[1])
if v < 0 then redis.call('SET', KEYS[1], 0) v = 0 end
return v
"""


def _values(event: Event) -> dict:
    return {k: v for k, v in event.payload.model_dump(mode="json").items() if v is not None}


class Live:
    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis
        self.last_ts: dict[str, datetime] = {}
        self.zones: dict[str, set[str]] = {}  # GNSS sensor -> geozone ids it was in
        self.people: dict[str, int] = {}  # building -> headcount (mirror of zone:{id}:people)
        self._incr = redis.register_script(_BOUNDED_INCR)

    def is_late(self, event: Event) -> bool:
        """Older than what we already show: still stored and aggregated, but must not roll the map back."""
        last = self.last_ts.get(event.sensor_id)
        return last is not None and event.ts < last

    async def previous_zones(self, sensor_id: str) -> set[str]:
        if sensor_id not in self.zones:
            raw = await self.redis.hget(keys.live_sensor(sensor_id), "zones")
            self.zones[sensor_id] = set(filter(None, (raw or "").split(",")))
        return self.zones[sensor_id]

    def stage(self, pipe, event: Event, sensor: SensorRef, vehicle: VehicleRef | None, status: str) -> None:
        """Queue the live-state write and notification for one event onto a pipeline."""
        self.last_ts[event.sensor_id] = event.ts
        now = datetime.now(UTC)
        pipe.zadd(keys.LAST_SEEN, {sensor.id: now.timestamp()})
        if event.type == SensorType.GNSS and vehicle is not None:
            p = event.payload
            moving = p.speed_kmh > 1
            doc = VehicleLive(
                vehicle_id=vehicle.id, sensor_id=sensor.id, geo=event.geo, speed_kmh=p.speed_kmh,
                heading_deg=p.heading_deg, fuel_pct=p.fuel_pct, engine_on=p.engine_on, zone_id=event.zone_id,
                last_seen=event.ts,
                status=VehicleStatus.MOVING if moving else VehicleStatus.IDLE if p.engine_on else VehicleStatus.STOPPED)
            zones = ",".join(sorted(self.zones.get(sensor.id, ())))
            pipe.hset(keys.live_sensor(sensor.id), mapping={"doc": doc.model_dump_json(), "kind": "vehicle",
                                                             "ts": event.ts.timestamp(), "zones": zones})
            pipe.publish(keys.live_channel(keys.SITE, Layer.VEHICLES),
                         WsVehicleUpdate(ts=now, building_id=keys.SITE, data=doc).model_dump_json())
            return
        building = sensor.building_id or keys.SITE
        doc = SensorLive(sensor_id=sensor.id, type=sensor.type, building_id=sensor.building_id,
                         status=SensorStatus(status), last_seen=event.ts, values=_values(event))
        pipe.hset(keys.live_sensor(sensor.id), mapping={"doc": doc.model_dump_json(), "kind": "sensor",
                                                         "ts": event.ts.timestamp()})
        pipe.publish(keys.live_channel(building, Layer.SENSORS),
                     WsSensorUpdate(ts=now, building_id=building, data=doc).model_dump_json())

    async def load_people(self, building_ids: list[str]) -> None:
        values = await self.redis.mget([keys.zone_people(b) for b in building_ids])
        self.people = {b: int(v or 0) for b, v in zip(building_ids, values)}

    async def count_person(self, building_id: str, delta: int) -> None:
        people = await self._incr(keys=[keys.zone_people(building_id)], args=[delta])
        self.people[building_id] = int(people)
        msg = WsZoneUpdate(ts=datetime.now(UTC), building_id=building_id,
                           data=ZoneOccupancy(zone_id=building_id, people=int(people)))
        await self.redis.publish(keys.live_channel(building_id, Layer.PEOPLE), msg.model_dump_json())

    async def mark_offline(self, sensor: SensorRef) -> None:
        key = keys.live_sensor(sensor.id)
        raw = await self.redis.hget(key, "doc")
        if raw is None:
            return
        doc = json.loads(raw)
        doc["status"] = "offline"
        if sensor.is_mobile:
            vehicle = VehicleLive.model_validate(doc)
            msg = WsVehicleUpdate(ts=datetime.now(UTC), building_id=keys.SITE, data=vehicle)
            channel = keys.live_channel(keys.SITE, Layer.VEHICLES)
        else:
            live = SensorLive.model_validate(doc)
            building = sensor.building_id or keys.SITE
            msg = WsSensorUpdate(ts=datetime.now(UTC), building_id=building, data=live)
            channel = keys.live_channel(building, Layer.SENSORS)
        pipe = self.redis.pipeline(transaction=False)
        pipe.hset(key, "doc", msg.data.model_dump_json())
        pipe.publish(channel, msg.model_dump_json())
        await pipe.execute()
