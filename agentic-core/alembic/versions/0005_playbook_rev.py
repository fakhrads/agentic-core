"""playbook_rev table (M7)

Revision ID: 0005_playbook_rev
Revises: 0004_regression_change
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_playbook_rev"
down_revision: str | None = "0004_regression_change"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "playbook_rev",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file", sa.String(length=32), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_bool", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_playbook_rev"),
    )
    op.create_index("ix_playbook_rev_file", "playbook_rev", ["file"])


def downgrade() -> None:
    op.drop_index("ix_playbook_rev_file", table_name="playbook_rev")
    op.drop_table("playbook_rev")
