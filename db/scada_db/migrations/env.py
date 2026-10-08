from alembic import context
from geoalchemy2 import Geometry, alembic_helpers
from sqlalchemy import create_engine, pool

from scada_db.config import settings
from scada_db.models import Base

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Tables we don't model belong to PostGIS (spatial_ref_sys, tiger.*, topology.*): never drop them
    if type_ == "table" and reflected and compare_to is None:
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def compare_type(ctx, inspected_column, metadata_column, inspected_type, metadata_type):
    # PostGIS reflects SRID 0 ("unknown", our local plan metres) as -1; that is not a change
    if isinstance(inspected_type, Geometry) and isinstance(metadata_type, Geometry):
        same_srid = {inspected_type.srid, metadata_type.srid} <= {0, -1} or inspected_type.srid == metadata_type.srid
        return not (same_srid and inspected_type.geometry_type == metadata_type.geometry_type)
    return None  # default comparison


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url") or settings.pg_url()
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=alembic_helpers.writer,
            render_item=alembic_helpers.render_item,
            compare_type=compare_type,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
