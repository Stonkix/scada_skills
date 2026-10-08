"""Connection settings from environment variables (see .env.example). Defaults match the dev compose stack."""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    pg_host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "localhost"))
    pg_port: int = field(default_factory=lambda: int(_env("POSTGRES_PORT", "5433")))
    pg_db: str = field(default_factory=lambda: _env("POSTGRES_DB", "scada"))
    pg_user: str = field(default_factory=lambda: _env("POSTGRES_USER", "scada"))
    pg_password: str = field(default_factory=lambda: _env("POSTGRES_PASSWORD", "scada"))

    ch_host: str = field(default_factory=lambda: _env("CLICKHOUSE_HOST", "localhost"))
    ch_port: int = field(default_factory=lambda: int(_env("CLICKHOUSE_HTTP_PORT", "8123")))
    ch_db: str = field(default_factory=lambda: _env("CLICKHOUSE_DB", "scada"))
    ch_user: str = field(default_factory=lambda: _env("CLICKHOUSE_USER", "scada"))
    ch_password: str = field(default_factory=lambda: _env("CLICKHOUSE_PASSWORD", "scada"))

    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", f"redis://localhost:{_env('REDIS_PORT', '6379')}/0"))

    seed_dir: Path = field(default_factory=lambda: Path(
        _env("SCADA_SEED_DIR", str(Path(__file__).resolve().parents[2] / "deploy" / "seed"))))

    def pg_url(self, driver: str = "psycopg") -> str:
        return f"postgresql+{driver}://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_db}"


settings = Settings()
