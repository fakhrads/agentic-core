"""Alembic environment — async online, offline SQL supported.

The DB URL comes from agent settings, not alembic.ini. For offline (`--sql`)
mode the `+asyncpg` driver suffix is stripped so DDL renders without a driver.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from agent.config import get_settings
from agent.db.base import Base
from agent.db import models  # noqa: F401  # register tables on Base.metadata
from agent.memory import models as _memory_models  # noqa: F401  # register memory_item

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _async_url() -> str:
    return get_settings().postgres_dsn


def run_migrations_offline() -> None:
    url = _async_url().replace("+asyncpg", "")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_async_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
