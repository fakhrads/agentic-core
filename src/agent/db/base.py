"""Async engine + declarative base.

Engines are cached per-DSN so the process holds one pool per target (spec §12:
one AsyncClient/engine per target, reusable). The vector index lives in
Postgres, never in process memory (RAM target < 300MB).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Portable JSON: JSONB on Postgres, plain JSON elsewhere (sqlite in tests).
JSONType = JSON().with_variant(JSONB, "postgresql")

# Dimensionless pgvector on Postgres; JSON list on sqlite (tests).
EmbeddingType = Vector().with_variant(JSON(), "sqlite")

# Stable constraint names help Alembic diffs stay deterministic.
_NAMING = MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


class Base(DeclarativeBase):
    metadata = _NAMING


def utcnow() -> datetime:
    """Timezone-aware UTC now — all timestamps are UTC ISO-8601 (contract §4)."""
    return datetime.now(UTC)


_engines: dict[str, AsyncEngine] = {}
_makers: dict[str, async_sessionmaker[AsyncSession]] = {}


def get_engine(dsn: str) -> AsyncEngine:
    engine = _engines.get(dsn)
    if engine is None:
        engine = create_async_engine(dsn, pool_pre_ping=True, future=True)
        _engines[dsn] = engine
    return engine


def get_sessionmaker(dsn: str) -> async_sessionmaker[AsyncSession]:
    maker = _makers.get(dsn)
    if maker is None:
        maker = async_sessionmaker(get_engine(dsn), expire_on_commit=False)
        _makers[dsn] = maker
    return maker


@asynccontextmanager
async def session_scope(dsn: str) -> AsyncIterator[AsyncSession]:
    """Transactional session: commit on success, rollback on error."""
    maker = get_sessionmaker(dsn)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engines() -> None:
    """Close all pooled engines — call on graceful shutdown / test teardown."""
    for engine in _engines.values():
        await engine.dispose()
    _engines.clear()
    _makers.clear()
