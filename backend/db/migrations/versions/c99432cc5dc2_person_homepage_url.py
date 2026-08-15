"""Add `people.homepage_url` for the researcher's own website.

Distinct from the department profile URL (which lives in `external_identifiers`
as `provider='official_url'`). The personal site is where CVs, publication
lists, and full bios live — the next backfill hop for enrichment.

Revision ID: c99432cc5dc2
Revises: 07966a575e9a
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c99432cc5dc2"
down_revision: str | Sequence[str] | None = "07966a575e9a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()
    if _col("people", "homepage_url"):
        return
    op.add_column(
        "people",
        sa.Column("homepage_url", sa.Text, nullable=True),
    )
    # CHECK: if set, it must look like an http/https URL. Cheap sanity net;
    # the parsers do the real validation before insert.
    op.create_check_constraint(
        "people_homepage_url_scheme",
        "people",
        "homepage_url IS NULL OR homepage_url ~* '^https?://'",
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
    if not _col("people", "homepage_url"):
        return
    op.drop_constraint("people_homepage_url_scheme", "people", type_="check")
    op.drop_column("people", "homepage_url")
