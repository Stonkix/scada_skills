"""Delivers simulated device messages to connectors exactly as real devices would.

GNSS trackers, climate and motion sensors publish over MQTT; ANPR cameras and
access controllers post webhooks over HTTP. Nothing bypasses connectors.
"""

import asyncio
import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import aiomqtt
import httpx
from scada_connectors.mqtt import api_key_properties

log = logging.getLogger(__name__)

MQTT_ADAPTERS = {"gnss", "climate", "motion"}


@dataclass
class Outgoing:
    adapter: str
    device_id: str
    payload: dict[str, Any]


@dataclass
class Transport:
    connectors_url: str
    mqtt_host: str
    mqtt_port: int
    api_key: str
    silenced: set[str] = field(default_factory=set)  # sensor ids that "lost power" (offline scenario)
    sent: Counter = field(default_factory=Counter)
    failed: Counter = field(default_factory=Counter)
    last_error: str | None = None

    def __post_init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=self.connectors_url, headers={"X-API-Key": self.api_key}, timeout=5)
        self._mqtt: aiomqtt.Client | None = None
        self.mqtt_ready = asyncio.Event()
        self._props = api_key_properties(self.api_key)

    async def run_mqtt_forever(self) -> None:
        while True:
            try:
                async with aiomqtt.Client(self.mqtt_host, self.mqtt_port, identifier="scada-simulator",
                                          protocol=aiomqtt.ProtocolVersion.V5) as client:
                    self._mqtt = client
                    self.mqtt_ready.set()
                    log.info("mqtt connected to %s:%d", self.mqtt_host, self.mqtt_port)
                    await asyncio.Event().wait()  # keep the connection open
            except aiomqtt.MqttError as e:
                self.last_error = f"mqtt: {e}"
                log.warning("mqtt connection lost (%s), reconnecting", e)
            finally:
                self._mqtt = None
                self.mqtt_ready.clear()
            await asyncio.sleep(3)

    async def send(self, batch: list[Outgoing]) -> None:
        batch = [o for o in batch if o.device_id not in self.silenced]
        http: dict[str, list[dict]] = defaultdict(list)
        for o in batch:
            if o.adapter in MQTT_ADAPTERS:
                await self._publish(o)
            else:
                http[o.adapter].append(o.payload)
        await asyncio.gather(*(self._post(adapter, items) for adapter, items in http.items()))

    async def _publish(self, o: Outgoing) -> None:
        if self._mqtt is None:
            self.failed[o.adapter] += 1
            return
        try:
            await self._mqtt.publish(f"sensors/{o.adapter}/{o.device_id}", json.dumps(o.payload), qos=0,
                                     properties=self._props)
            self.sent[o.adapter] += 1
        except aiomqtt.MqttError as e:
            self.failed[o.adapter] += 1
            self.last_error = f"mqtt publish: {e}"

    async def _post(self, adapter: str, items: list[dict]) -> None:
        try:
            resp = await self._http.post(f"/ingest/{adapter}", json=items)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            self.sent[adapter] += body.get("accepted", 0)
            if resp.status_code >= 400 or body.get("rejected"):
                self.failed[adapter] += len(items) - body.get("accepted", 0)
                self.last_error = f"http {adapter}: {resp.status_code} {resp.text[:200]}"
        except httpx.HTTPError as e:
            self.failed[adapter] += len(items)
            self.last_error = f"http {adapter}: {e!r}"

    async def aclose(self) -> None:
        await self._http.aclose()
