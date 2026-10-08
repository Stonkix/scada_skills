"""Redis key and channel conventions shared by connectors, worker and api.

Streams
  stream:events   connectors -> worker (normalized Event JSON in field "data")
  stream:dlq      invalid input with the reason, for debugging connectors
  stream:alerts   worker -> notifiers (VK bot); field "data" is Alert JSON

Live state (written by worker, read by api)
  live:sensor:{id}         HASH  last payload fields + status, ts, x, y, zone_id
  zone:{id}:people         STRING counter, INCR/DECR from access-control events
  last_seen                ZSET  sensor_id -> unix ts of the last event (offline detection)
  alert:open:{rule}:{obj}  STRING alert id while the alert is not resolved (dedup / hysteresis)
  seen:event:{event_id}    STRING with TTL; SET NX before processing to drop duplicates

Pub/sub
  live:{building}:{layer}  building id or "site" (outdoors); layer = sensors|vehicles|people|alerts
"""

STREAM_EVENTS = "stream:events"
STREAM_DLQ = "stream:dlq"
STREAM_ALERTS = "stream:alerts"

# Approximate MAXLEN for XADD: bounds memory if the worker is down for a while.
STREAM_EVENTS_MAXLEN = 1_000_000
STREAM_DLQ_MAXLEN = 10_000
STREAM_ALERTS_MAXLEN = 10_000

WORKER_GROUP = "worker"
NOTIFIER_GROUP = "notifier"

# stream -> consumer groups that must exist before producers start
CONSUMER_GROUPS = {
    STREAM_EVENTS: [WORKER_GROUP],
    STREAM_ALERTS: [NOTIFIER_GROUP],
}

LAST_SEEN = "last_seen"
SEEN_EVENT_TTL_S = 3600  # duplicates later than this are left to ClickHouse ReplacingMergeTree
SITE = "site"  # pseudo building id for outdoor objects


def live_sensor(sensor_id: str) -> str:
    return f"live:sensor:{sensor_id}"


def zone_people(zone_id: str) -> str:
    return f"zone:{zone_id}:people"


def alert_open(rule_id: str, object_id: str) -> str:
    return f"alert:open:{rule_id}:{object_id}"


def seen_event(event_id: str) -> str:
    return f"seen:event:{event_id}"


def live_channel(building_id: str, layer: str) -> str:
    return f"live:{building_id}:{layer}"
