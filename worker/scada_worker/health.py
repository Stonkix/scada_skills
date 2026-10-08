"""Container healthcheck: the worker writes stats every 5 s; stale stats mean it is stuck."""

import sys
import time

import redis
from scada_db.config import settings

updated = redis.Redis.from_url(settings.redis_url).hget("worker:stats", "updated_at")
sys.exit(0 if updated and time.time() - float(updated) < 30 else 1)
