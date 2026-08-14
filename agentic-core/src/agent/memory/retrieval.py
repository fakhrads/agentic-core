"""Retrieval — vector similarity over active memory (pgvector).

M5 does pure vector search DB-side (the index stays in Postgres, never in
process memory — RAM target). M7 layers utility + fitness reranking (MemRL) on
top. `mark_retrieved` bumps usage counters so fitness/decay reflect real use.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.memory.fitness import compute_fitness
from agent.memory.models import MSTATUS_ACTIVE, MemoryItem


class Embedder(Protocol):
    async def embed(self, text: str) -> list[float]: ...


async def semantic_search(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    limit: int = 8,
    statuses: tuple[str, ...] = (MSTATUS_ACTIVE,),
) -> list[tuple[MemoryItem, float]]:
    """Return (item, cosine_distance) nearest neighbours among the given statuses."""
    distance = MemoryItem.embedding.cosine_distance(query_embedding)
    stmt = (
        select(MemoryItem, distance.label("distance"))
        .where(MemoryItem.status.in_(statuses))
        .where(MemoryItem.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    rows = await session.execute(stmt)
    return [(item, float(dist)) for item, dist in rows.all()]


async def mark_retrieved(session: AsyncSession, items: list[MemoryItem]) -> None:
    now = utcnow()
    for item in items:
        item.retrieval_count += 1
        item.last_used_at = now
    await session.flush()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def rerank(
    hits: list[tuple[MemoryItem, float]],
    *,
    w_similarity: float = 0.7,
    w_fitness: float = 0.3,
    now: datetime | None = None,
) -> list[tuple[MemoryItem, float]]:
    """Combine vector similarity with fitness utility (MemRL, spec §retrieval).

    Similarity = 1 - cosine_distance. Fitness is min-max normalized across the
    candidate set so a strong-but-slightly-farther memory can outrank a close
    but low-utility one.
    """
    if not hits:
        return []
    fits = [compute_fitness(item, now) for item, _ in hits]
    lo, hi = min(fits), max(fits)

    def norm(f: float) -> float:
        return 0.5 if hi == lo else (f - lo) / (hi - lo)

    scored = [
        (item, w_similarity * (1.0 - dist) + w_fitness * norm(f))
        for (item, dist), f in zip(hits, fits, strict=True)
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


async def hybrid_search(
    session: AsyncSession,
    query_embedding: list[float],
    *,
    limit: int = 8,
    candidate_factor: int = 3,
    now: datetime | None = None,
) -> list[tuple[MemoryItem, float]]:
    """Vector recall then fitness-aware rerank; returns (item, score)."""
    hits = await semantic_search(session, query_embedding, limit=limit * candidate_factor)
    return rerank(hits, now=now)[:limit]
