"""episode + step tables (M2 audit spine)

Revision ID: 0001_episode_step
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_episode_step"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "episode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("human_score", sa.Integer(), nullable=True),
        sa.Column("human_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_episode"),
    )
    op.create_index(
        "ix_episode_trace_id", "episode", ["trace_id"], unique=True
    )

    op.create_table(
        "step",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            ["episode.id"],
            name="fk_step_episode_id_episode",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_step"),
    )
    op.create_index("ix_step_episode_id", "step", ["episode_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_step_episode_id", table_name="step")
    op.drop_table("step")
    op.drop_index("ix_episode_trace_id", table_name="episode")
    op.drop_table("episode")
