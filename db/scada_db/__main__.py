"""python -m scada_db <command>

    migrate          apply Postgres (Alembic) and ClickHouse migrations, create Redis consumer groups
    seed [--force]   load the demo enterprise; without --force only into an empty registry
    init             migrate + seed (what the db-init container runs on `up`)
    revision "msg"   autogenerate an Alembic migration from models.py
"""

import sys
import time

from scada_db import clickhouse, postgres, redis_init
from scada_db.seed import seed


def _wait_for_stores(timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with postgres.engine().connect():
                pass
            clickhouse.client().ping()
            redis_init.client().ping()
            return
        except Exception as e:  # noqa: BLE001 — any connection error means "not ready yet"
            if time.monotonic() > deadline:
                raise SystemExit(f"stores are not reachable: {e}") from e
            time.sleep(1)


def migrate() -> None:
    _wait_for_stores()
    postgres.migrate()
    print("postgres: at head")
    print("clickhouse: applied", clickhouse.migrate() or "nothing new")
    print("redis: consumer groups", redis_init.init() or "already exist")


def run_seed(force: bool) -> None:
    result = seed(force=force)
    if result is None:
        print("seed: registry is not empty, skipped (use --force to reset to the demo state)")
    else:
        print("seed:", ", ".join(f"{k}={v}" for k, v in result.items()))


def main() -> None:
    args = sys.argv[1:]
    match args:
        case ["migrate"]:
            migrate()
        case ["seed", *rest] if set(rest) <= {"--force"}:
            run_seed("--force" in rest)
        case ["init"]:
            migrate()
            run_seed(force=False)
        case ["revision", message]:
            postgres.make_revision(message)
        case _:
            print(__doc__)
            sys.exit(2)


if __name__ == "__main__":
    main()
