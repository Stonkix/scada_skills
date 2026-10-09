"""Container healthcheck: each worker replica writes its stats every 5 s; stale stats mean it is stuck."""

import os
import socket
import sys
import time

import redis
from scada_common import keys
from scada_db.config import settings

name = os.environ.get("WORKER_NAME") or socket.gethostname()  # same rule as scada_worker.main.NAME
updated = redis.Redis.from_url(settings.redis_url).hget(keys.worker_stats(name), "updated_at")
sys.exit(0 if updated and time.time() - float(updated) < 30 else 1)
