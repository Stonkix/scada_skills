"""ClickHouse schema migrations: numbered *.sql files applied once, tracked in schema_migrations."""

from pathlib import Path

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from scada_db.config import settings

SQL_DIR = Path(__file__).with_name("clickhouse")


def client(database: str | None = None) -> Client:
    return clickhouse_connect.get_client(host=settings.ch_host, port=settings.ch_port, username=settings.ch_user,
                                         password=settings.ch_password, database=database or settings.ch_db)


def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def migrate() -> list[str]:
    ch = client()
    ch.command("""CREATE TABLE IF NOT EXISTS schema_migrations
                  (name String, applied_at DateTime DEFAULT now()) ENGINE = MergeTree ORDER BY name""")
    done = {r[0] for r in ch.query("SELECT name FROM schema_migrations").result_rows}
    applied = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        if path.name in done:
            continue
        for stmt in _statements(path.read_text(encoding="utf-8")):
            ch.command(stmt)
        ch.insert("schema_migrations", [[path.name]], column_names=["name"])
        applied.append(path.name)
    return applied
