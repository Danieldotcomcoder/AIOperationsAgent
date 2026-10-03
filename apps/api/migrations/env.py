"""Alembic environment.

The database URL comes from ``Settings`` so that migrations and the application can
never point at different databases. Importing ``opspilot.models`` is what populates
the metadata autogenerate compares against — a model module missing from that package
is silently missing from every future migration.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from opspilot.core.config import get_settings

# Importing the package registers every model on Base.metadata for autogenerate.
from opspilot.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Leave extension-owned objects alone.

    pgvector creates types and operator classes that Alembic would otherwise try to
    drop. Extensions are installed by infra/postgres/init, not by a migration.
    """
    return not (type_ == "table" and name in {"spatial_ref_sys"})


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            # One head, always. A branched history on a single-VPS deployment is a
            # problem discovered at the worst possible moment.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
