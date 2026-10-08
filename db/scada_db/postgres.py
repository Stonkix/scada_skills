from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

from scada_db.config import settings

MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", settings.pg_url())
    return cfg


def migrate() -> None:
    command.upgrade(alembic_config(), "head")


def make_revision(message: str) -> None:
    """Autogenerate a migration from the diff between models.py and the live database."""
    command.revision(alembic_config(), message=message, autogenerate=True)


@lru_cache
def engine() -> Engine:
    return create_engine(settings.pg_url(), pool_pre_ping=True)
