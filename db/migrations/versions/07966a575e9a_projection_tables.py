"""Person 2D projections for the scatter canvas.

`embedding_runs` records each offline projection build (algorithm, dim of the
raw embedding space, when it ran, whether it's the active run served to the
frontend). Exactly one row has `is_active = TRUE`.

`person_projections_2d` stores just the two coordinates we render. The raw
high-dim vector is intentionally NOT stored yet — we'd only need it if the
frontend wanted to compute distances client-side, which it doesn't. Adding
pgvector later is a schema-additive change.

Revision ID: 07966a575e9a
Revises: f835c16a408e
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "07966a575e9a"
down_revision = "f835c16a408e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "kind",
            sa.Text,
            nullable=False,
            comment="What the embedding represents. Currently only 'person_graph'.",
        ),
        sa.Column(
            "algorithm",
            sa.Text,
            nullable=False,
            comment="Free-form identifier like 'spectral_svd_v1' or 'node2vec_dim64'.",
        ),
        sa.Column(
            "raw_dim",
            sa.Integer,
            nullable=False,
            comment="Dimensionality of the high-dim embedding before 2D projection.",
        ),
        sa.Column(
            "point_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("raw_dim > 0", name="embedding_runs_dim_positive"),
    )

    # Only ONE active run at a time. Partial unique index over the `is_active`
    # flag; simpler than an exclusion constraint for this narrow case.
    op.execute(
        "CREATE UNIQUE INDEX embedding_runs_one_active "
        "ON embedding_runs (is_active) WHERE is_active"
    )

    op.create_table(
        "person_projections_2d",
        sa.Column("run_id", sa.BigInteger, nullable=False),
        sa.Column("person_id", sa.BigInteger, nullable=False),
        sa.Column("x", sa.Float, nullable=False),
        sa.Column("y", sa.Float, nullable=False),
        sa.PrimaryKeyConstraint("run_id", "person_id"),
    )
    op.create_index(
        "idx_person_projections_2d_person",
        "person_projections_2d",
        ["person_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_person_projections_2d_person",
        table_name="person_projections_2d",
    )
    op.drop_table("person_projections_2d")
    op.execute("DROP INDEX IF EXISTS embedding_runs_one_active")
    op.drop_table("embedding_runs")
