"""tool_need table (autonomous forge trigger)

Revision ID: 0010_tool_need
Revises: 0009_approval
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_tool_need"
down_revision: str | None = "0009_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_need",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("args_sample", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_by_trace", sa.String(length=64), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tool_need"),
    )
    op.create_index("ix_tool_need_name", "tool_need", ["name"], unique=True)
    op.create_index("ix_tool_need_status", "tool_need", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tool_need_status", table_name="tool_need")
    op.drop_index("ix_tool_need_name", table_name="tool_need")
    op.drop_table("tool_need")
