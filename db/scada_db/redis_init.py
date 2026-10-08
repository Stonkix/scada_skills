"""Create streams and consumer groups so producers and consumers can start in any order."""

import redis
from scada_common import keys

from scada_db.config import settings


def client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def init() -> list[str]:
    r = client()
    created = []
    for stream, groups in keys.CONSUMER_GROUPS.items():
        for group in groups:
            try:
                # "0": a group created after events were produced still gets them
                r.xgroup_create(stream, group, id="0", mkstream=True)
                created.append(f"{stream}/{group}")
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise
    return created


def reset_live_state() -> int:
    """Drop live state and streams (used by `seed --force`); keeps nothing derived from old events."""
    r = client()
    patterns = ["live:sensor:*", "zone:*:people", "alert:open:*", "seen:event:*"]
    doomed = [k for p in patterns for k in r.scan_iter(p, count=1000)]
    doomed += [keys.LAST_SEEN, keys.STREAM_EVENTS, keys.STREAM_DLQ, keys.STREAM_ALERTS]
    return r.delete(*doomed) if doomed else 0
