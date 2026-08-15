"""Add community_id to person_projections_2d for scatter color modes.

Revision ID: b8e4f1a92c3d
Revises: c99432cc5dc2
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e4f1a92c3d"
down_revision = "c99432cc5dc2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()
    if _col("person_projections_2d", "community_id") or _col("person_projections_2d", "similarity_group"):
        return
    op.add_column(
        "person_projections_2d",
        sa.Column("community_id", sa.SmallInteger, nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()
    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()
    if not _col("person_projections_2d", "community_id"):
        return
    op.drop_column("person_projections_2d", "community_id")
