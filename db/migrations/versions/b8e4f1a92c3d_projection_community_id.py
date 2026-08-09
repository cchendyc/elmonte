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
    op.add_column(
        "person_projections_2d",
        sa.Column("community_id", sa.SmallInteger, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("person_projections_2d", "community_id")
