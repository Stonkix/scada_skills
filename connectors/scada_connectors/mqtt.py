"""MQTT ingress: topic sensors/{adapter}/{device_id}, JSON payload (object or array).

The API key travels as an MQTT 5 user property `x-api-key` — the MQTT analogue of
an HTTP header — so the broker can stay a dumb pipe and keys are checked in one place.
"""

import asyncio
import json
import logging
import os

import aiomqtt
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes

from scada_connectors.pipeline import IngestError, Pipeline

log = logging.getLogger(__name__)

TOPIC = "sensors/+/+"
API_KEY_PROPERTY = "x-api-key"


def mqtt_settings() -> tuple[str, int]:
    return os.environ.get("MQTT_HOST", "localhost"), int(os.environ.get("MQTT_PORT", "1883"))


def api_key_properties(api_key: str) -> Properties:
    """For publishers (simulator, tests): attach the key to a PUBLISH packet."""
    props = Properties(PacketTypes.PUBLISH)
    props.UserProperty = [(API_KEY_PROPERTY, api_key)]
    return props


def _api_key(message: aiomqtt.Message) -> str | None:
    for name, value in getattr(message.properties, "UserProperty", None) or []:
        if name == API_KEY_PROPERTY:
            return value
    return None


class MqttIngress:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.connected = False
        self.received = 0

    async def handle(self, message: aiomqtt.Message) -> None:
        self.received += 1
        parts = message.topic.value.split("/")
        if len(parts) != 3:
            return
        _, adapter_name, device_id = parts
        try:
            body = json.loads(message.payload)
        except (ValueError, TypeError):
            log.warning("mqtt %s: payload is not JSON", message.topic.value)
            return
        items = body if isinstance(body, list) else [body]
        try:
            await self.pipeline.ingest(adapter_name, items, _api_key(message), device_hint=device_id, source="mqtt")
        except IngestError as e:  # no reply channel in MQTT: log it, the DLQ has item-level failures
            log.warning("mqtt %s rejected: %s", message.topic.value, e.detail)

    async def run_forever(self) -> None:
        host, port = mqtt_settings()
        while True:
            try:
                async with aiomqtt.Client(host, port, identifier="scada-connectors",
                                          protocol=aiomqtt.ProtocolVersion.V5) as client:
                    await client.subscribe(TOPIC, qos=1)
                    self.connected = True
                    log.info("mqtt: subscribed to %s on %s:%d", TOPIC, host, port)
                    async for message in client.messages:
                        try:
                            await self.handle(message)
                        except Exception:  # one bad message must not kill the subscription
                            log.exception("mqtt: failed to handle %s", message.topic.value)
            except aiomqtt.MqttError as e:
                log.warning("mqtt: connection lost (%s), reconnecting in 3 s", e)
            finally:
                self.connected = False
            await asyncio.sleep(3)
