"""Add expires_at column to source_snapshots for retention policies.

CV snapshots expire 90 days after fetch; profile/HTML snapshots 365 days.
Nullable: existing rows get NULL (interpreted as "no expiry" — safe default).

Revision ID: d4d636fc3df0
Revises: fbb6b2990cac
Create Date: 2026-08-09 01:37:48.254121
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4d636fc3df0"
down_revision: str | Sequence[str] | None = "fbb6b2990cac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()

    if _col("source_snapshots", "expires_at"):
        return

    op.add_column(
        "source_snapshots",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    conn = op.get_bind()

    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()

    if not _col("source_snapshots", "expires_at"):
        return

    op.drop_column("source_snapshots", "expires_at")
