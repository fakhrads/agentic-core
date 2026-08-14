"""Memory ORM model.

One table, `memory_item`, holds semantic and reasoning memories. Every artefact
carries fitness and a lifecycle status but is NEVER deleted (Prinsip 1) — the
terminal automatic state is `archived`, not removed.

The embedding column is dimensionless `vector` on Postgres (dimension comes from
the Ollama model at runtime, not hardcoded) and a JSON list on sqlite (tests).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from agent.db.base import Base, EmbeddingType, utcnow

# Tiers.
TIER_SEMANTIC = "semantic"
TIER_REASONING = "reasoning"

# Source kinds — external content is never trusted directly (Prinsip 2).
SRC_USER = "user"
SRC_EXTERNAL = "external"
SRC_SELF = "self"

# Lifecycle status.
MSTATUS_QUARANTINE = "quarantine"
MSTATUS_ACTIVE = "active"
MSTATUS_RETIRED = "retired"
MSTATUS_ARCHIVED = "archived"


class MemoryItem(Base):
    __tablename__ = "memory_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)

    source: Mapped[str] = mapped_column(String(128))
    source_kind: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    retrieval_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    contradiction_count: Mapped[int] = mapped_column(Integer, default=0)
    human_reward: Mapped[float] = mapped_column(Float, default=0.0)
    fitness: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(
        String(16), default=MSTATUS_QUARANTINE, index=True
    )
    # Set when the curator resamples an archived item back to active (M10).
    resampled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
