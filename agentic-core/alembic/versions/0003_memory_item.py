"""memory_item table + pgvector extension (M5)

Revision ID: 0003_memory_item
Revises: 0002_llm_call
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0003_memory_item"
down_revision: str | None = "0002_llm_call"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "memory_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Dimensionless vector — dimension is set by the Ollama model at runtime.
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieval_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("contradiction_count", sa.Integer(), nullable=False),
        sa.Column("human_reward", sa.Float(), nullable=False),
        sa.Column("fitness", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resampled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_memory_item"),
    )
    op.create_index("ix_memory_item_tier", "memory_item", ["tier"])
    op.create_index("ix_memory_item_status", "memory_item", ["status"])
    op.create_index("ix_memory_item_trace_id", "memory_item", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_item_trace_id", table_name="memory_item")
    op.drop_index("ix_memory_item_status", table_name="memory_item")
    op.drop_index("ix_memory_item_tier", table_name="memory_item")
    op.drop_table("memory_item")
