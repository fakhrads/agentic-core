"""regression_run + change_event tables (M6)

Revision ID: 0004_regression_change
Revises: 0003_memory_item
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_regression_change"
down_revision: str | None = "0003_memory_item"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regression_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("suite", sa.String(length=32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_regression_run"),
    )
    op.create_index("ix_regression_run_suite", "regression_run", ["suite"])

    op.create_table(
        "change_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_change_event"),
    )
    op.create_index("ix_change_event_at", "change_event", ["at"])


def downgrade() -> None:
    op.drop_index("ix_change_event_at", table_name="change_event")
    op.drop_table("change_event")
    op.drop_index("ix_regression_run_suite", table_name="regression_run")
    op.drop_table("regression_run")
