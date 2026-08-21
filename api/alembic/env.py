"""Alembic environment.

The database URL comes from pydantic-settings, never from alembic.ini, so there
is exactly one source of connection configuration.

M0 has no revisions: `alembic upgrade head` is a successful no-op.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the models package registers every mapper on `Base.metadata`.
# Without it, autogenerate sees an empty schema and cheerfully drops the world.
import app.models  # noqa: F401
from app.config import get_settings
from app.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def include_object(
    obj: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Hide the indexed-field worker's indexes from autogenerate.

    `app.workers.indexing` builds `ix_lv_<sha1>` expression indexes at runtime —
    the one sanctioned piece of runtime DDL (architecture rule 7). No migration
    describes them and none should: they come and go as workspaces declare and
    withdraw indexed fields.

    Without this filter, autogenerate sees them in the database, finds nothing
    matching in the metadata, and helpfully writes `op.drop_index(...)` into the
    next revision — which would delete a live customer's index on deploy.
    """
    return not (type_ == "index" and name is not None and name.startswith("ix_lv_"))


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DBAPI connection."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
