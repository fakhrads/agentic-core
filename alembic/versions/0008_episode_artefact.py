"""episode_artefact table (M10 reward attribution)

Revision ID: 0008_episode_artefact
Revises: 0007_skill_benchmark
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_episode_artefact"
down_revision: str | None = "0007_skill_benchmark"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "episode_artefact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episode.id"],
            name="fk_episode_artefact_episode_id_episode", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_episode_artefact"),
    )
    op.create_index(
        "ix_episode_artefact_episode_id", "episode_artefact", ["episode_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_episode_artefact_episode_id", table_name="episode_artefact")
    op.drop_table("episode_artefact")
