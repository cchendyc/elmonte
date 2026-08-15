"""Add CV URL and cached snapshot reference on people.

`cv_url` is the external source. `cv_snapshot_id` points at a local copy in
`source_snapshots` / `data/ingest/raw/` so the app can serve it at
`/api/people/{id}/cv`.

Revision ID: d4f2a8c19e01
Revises: c3a7e91b4f20
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f2a8c19e01"
down_revision = "c3a7e91b4f20"
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
    if _col("people", "cv_url"):
        return
    op.add_column("people", sa.Column("cv_url", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("cv_snapshot_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "people_cv_url_scheme",
        "people",
        "cv_url IS NULL OR cv_url ~* '^https?://'",
    )
    op.create_index(
        "idx_people_cv_snapshot",
        "people",
        ["cv_snapshot_id"],
        unique=False,
        postgresql_where=sa.text("cv_snapshot_id IS NOT NULL"),
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
    if not _col("people", "cv_url"):
        return
    op.drop_index("idx_people_cv_snapshot", table_name="people")
    op.drop_constraint("people_cv_url_scheme", "people", type_="check")
    op.drop_column("people", "cv_snapshot_id")
    op.drop_column("people", "cv_url")
