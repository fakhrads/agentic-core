"""llm_call table (M3 cost accounting)

Revision ID: 0002_llm_call
Revises: 0001_episode_step
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_llm_call"
down_revision: str | None = "0001_episode_step"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("tok_in", sa.Integer(), nullable=False),
        sa.Column("tok_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_llm_call"),
    )
    op.create_index("ix_llm_call_trace_id", "llm_call", ["trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_llm_call_trace_id", table_name="llm_call")
    op.drop_table("llm_call")
