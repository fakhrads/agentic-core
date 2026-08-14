"""goal table (M8)

Revision ID: 0006_goal
Revises: 0005_playbook_rev
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_goal"
down_revision: str | None = "0005_playbook_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("parent_goal_id", sa.Integer(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("probe_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_goal_id"], ["goal.id"], name="fk_goal_parent_goal_id_goal",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_goal"),
    )
    op.create_index("ix_goal_status", "goal", ["status"])


def downgrade() -> None:
    op.drop_index("ix_goal_status", table_name="goal")
    op.drop_table("goal")
