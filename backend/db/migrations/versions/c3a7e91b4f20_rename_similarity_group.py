"""Rename community_id to similarity_group on person_projections_2d.

Revision ID: c3a7e91b4f20
Revises: b8e4f1a92c3d
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "c3a7e91b4f20"
down_revision = "b8e4f1a92c3d"
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
    if _col("person_projections_2d", "similarity_group"):
        return
    op.alter_column(
        "person_projections_2d",
        "community_id",
        new_column_name="similarity_group",
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
    if not _col("person_projections_2d", "similarity_group"):
        return
    op.alter_column(
        "person_projections_2d",
        "similarity_group",
        new_column_name="community_id",
    )
