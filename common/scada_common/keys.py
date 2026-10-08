"""Redis key and channel conventions shared by connectors, worker and api."""

STREAM_EVENTS = "stream:events"
STREAM_DLQ = "stream:dlq"
STREAM_ALERTS = "stream:alerts"

WORKER_GROUP = "worker"

LAST_SEEN = "last_seen"  # ZSET sensor_id -> unix ts


def live_sensor(sensor_id: str) -> str:
    return f"live:sensor:{sensor_id}"


def zone_people(zone_id: str) -> str:
    return f"zone:{zone_id}:people"


def alert_open(rule_id: str, sensor_id: str) -> str:
    return f"alert:open:{rule_id}:{sensor_id}"


def live_channel(building_id: str, layer: str) -> str:
    """Pub/sub channel; building_id "site" is used for outdoor objects."""
    return f"live:{building_id}:{layer}"
