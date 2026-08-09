"""Rename community_id to similarity_group on person_projections_2d.

Revision ID: c3a7e91b4f20
Revises: b8e4f1a92c3d
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op


revision = "c3a7e91b4f20"
down_revision = "b8e4f1a92c3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "person_projections_2d",
        "community_id",
        new_column_name="similarity_group",
    )


def downgrade() -> None:
    op.alter_column(
        "person_projections_2d",
        "similarity_group",
        new_column_name="community_id",
    )
