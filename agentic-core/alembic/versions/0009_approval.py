"""approval table (M11 tool forge / APPROVE tier)

Revision ID: 0009_approval
Revises: 0008_episode_artefact
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_approval"
down_revision: str | None = "0008_episode_artefact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action_kind", sa.String(length=32), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tier", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_approval"),
    )
    op.create_index("ix_approval_action_kind", "approval", ["action_kind"])
    op.create_index("ix_approval_status", "approval", ["status"])


def downgrade() -> None:
    op.drop_index("ix_approval_status", table_name="approval")
    op.drop_index("ix_approval_action_kind", table_name="approval")
    op.drop_table("approval")
