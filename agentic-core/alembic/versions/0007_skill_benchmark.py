"""skill + benchmark tables with gating CHECK (M9)

Revision ID: 0007_skill_benchmark
Revises: 0006_goal
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "0007_skill_benchmark"
down_revision: str | None = "0006_goal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("created_from_trace", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pass_rate", sa.Float(), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_skill"),
    )
    op.create_index("ix_skill_name", "skill", ["name"], unique=True)
    op.create_index("ix_skill_status", "skill", ["status"])

    op.create_table(
        "benchmark",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("checker", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("gating", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"], ["skill.id"], name="fk_benchmark_skill_id_skill",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark"),
        # INVARIANT: gating implies external origin.
        sa.CheckConstraint(
            "NOT gating OR origin = 'external'", name="ck_benchmark_gating_external"
        ),
    )
    op.create_index("ix_benchmark_skill_id", "benchmark", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_skill_id", table_name="benchmark")
    op.drop_table("benchmark")
    op.drop_index("ix_skill_status", table_name="skill")
    op.drop_index("ix_skill_name", table_name="skill")
    op.drop_table("skill")
