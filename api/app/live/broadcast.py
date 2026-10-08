"""Fan-out of Redis pub/sub `live:*` to WebSocket clients, each with its own filter.

One pattern subscription for the whole process; per-client bounded queues so a
slow browser tab drops its own messages instead of stalling everyone.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

import redis.asyncio as aioredis
from scada_common.live import WsSubscribe

log = logging.getLogger(__name__)

QUEUE_SIZE = 1000


@dataclass(eq=False)
class Client:
    sub: WsSubscribe = field(default_factory=WsSubscribe)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    dropped: int = 0

    def wants(self, building: str, layer: str, payload: str) -> bool:
        s = self.sub
        if s.layers is not None and layer not in s.layers:
            return False
        if s.buildings is not None and building not in s.buildings:
            return False
        if s.sensor_types is not None and layer == "sensors":
            return json.loads(payload)["data"]["type"] in s.sensor_types
        return True

    def offer(self, payload: str) -> None:
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            self.dropped += 1


class Broadcaster:
    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis
        self.clients: set[Client] = set()

    def add(self) -> Client:
        c = Client()
        self.clients.add(c)
        return c

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    async def run_forever(self) -> None:
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.psubscribe("live:*")
                async for msg in pubsub.listen():
                    if msg["type"] != "pmessage":
                        continue
                    _, building, layer = msg["channel"].split(":", 2)
                    for c in list(self.clients):
                        if c.wants(building, layer, msg["data"]):
                            c.offer(msg["data"])
            except (aioredis.ConnectionError, OSError) as e:
                log.warning("pub/sub lost (%s), resubscribing", e)
                await asyncio.sleep(2)
