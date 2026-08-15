"""Atlas v2: topics, person_topics, projection views and cluster tables.

Revision ID: fbb6b2990cac
Revises: d4f2a8c19e01

Idempotent by design: a fresh database that bootstrapped from the current
`db/schema.sql` already contains every object this migration creates.  The
historical migration path (old schema -> migrations) also still works, as do
re-runs after a partial failure.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fbb6b2990cac"
down_revision = "d4f2a8c19e01"
branch_labels = None
depends_on = None


def _relation_exists(conn, relation: str) -> bool:
    return conn.execute(
        sa.text("SELECT to_regclass(:r) IS NOT NULL"), {"r": relation}
    ).scalar()


def _column_exists(conn, table: str, column: str) -> bool:
    return conn.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c)"
        ),
        {"t": table, "c": column},
    ).scalar()


def _pk_definition(conn, table: str) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c "
            "WHERE c.conrelid = :t::regclass AND c.contype = 'p'"
        ),
        {"t": table},
    ).scalar()


def upgrade() -> None:
    conn = op.get_bind()

    if not _relation_exists(conn, "topics"):
        op.execute(
            """
            CREATE TABLE topics (
              openalex_topic_id TEXT PRIMARY KEY,
              display_name      TEXT NOT NULL,
              subfield_name     TEXT,
              field_name        TEXT,
              domain_name       TEXT,
              level             SMALLINT NOT NULL DEFAULT 3,
              created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    if not _relation_exists(conn, "publication_topics"):
        op.execute(
            """
            CREATE TABLE publication_topics (
              publication_id BIGINT NOT NULL,
              topic_id       TEXT NOT NULL,
              score          REAL,
              is_primary     BOOLEAN NOT NULL DEFAULT FALSE,
              PRIMARY KEY (publication_id, topic_id)
            )
            """
        )
    if not _relation_exists(conn, "idx_publication_topics_topic"):
        op.execute(
            "CREATE INDEX idx_publication_topics_topic ON publication_topics (topic_id)"
        )
    if not _relation_exists(conn, "person_topics"):
        op.execute(
            """
            CREATE TABLE person_topics (
              person_id    BIGINT NOT NULL,
              topic_id     TEXT NOT NULL,
              score        REAL NOT NULL,
              works_count  INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (person_id, topic_id)
            )
            """
        )
    if not _relation_exists(conn, "idx_person_topics_topic"):
        op.execute("CREATE INDEX idx_person_topics_topic ON person_topics (topic_id)")

    if not _column_exists(conn, "person_projections_2d", "view"):
        op.add_column(
            "person_projections_2d",
            sa.Column("view", sa.Text, nullable=False, server_default="topic"),
        )
    if not _column_exists(conn, "person_projections_2d", "cluster_id"):
        op.add_column(
            "person_projections_2d",
            sa.Column("cluster_id", sa.SmallInteger, nullable=True),
        )

    pk = _pk_definition(conn, "person_projections_2d") or ""
    if "view" not in pk:
        op.execute(
            "ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey"
        )
        op.execute(
            "ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id, view)"
        )

    if not _relation_exists(conn, "projection_clusters"):
        op.execute(
            """
            CREATE TABLE projection_clusters (
              run_id        BIGINT NOT NULL,
              view          TEXT NOT NULL,
              cluster_index SMALLINT NOT NULL,
              label         TEXT NOT NULL,
              field_name    TEXT,
              member_count  INTEGER NOT NULL,
              cx            DOUBLE PRECISION NOT NULL,
              cy            DOUBLE PRECISION NOT NULL,
              color_slot    SMALLINT NOT NULL DEFAULT 0,
              PRIMARY KEY (run_id, view, cluster_index)
            )
            """
        )
    if not _relation_exists(conn, "projection_cluster_edges"):
        op.execute(
            """
            CREATE TABLE projection_cluster_edges (
              run_id               BIGINT NOT NULL,
              view                 TEXT NOT NULL,
              source_cluster       SMALLINT NOT NULL,
              target_cluster       SMALLINT NOT NULL,
              collaboration_weight DOUBLE PRECISION,
              topic_weight         DOUBLE PRECISION,
              PRIMARY KEY (run_id, view, source_cluster, target_cluster)
            )
            """
        )


def downgrade() -> None:
    conn = op.get_bind()

    op.execute("DROP TABLE IF EXISTS projection_cluster_edges")
    op.execute("DROP TABLE IF EXISTS projection_clusters")

    if _column_exists(conn, "person_projections_2d", "cluster_id"):
        op.drop_column("person_projections_2d", "cluster_id")
    pk = _pk_definition(conn, "person_projections_2d") or ""
    if "view" in pk:
        op.execute(
            "ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey"
        )
        op.execute(
            "ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id)"
        )
    if _column_exists(conn, "person_projections_2d", "view"):
        op.drop_column("person_projections_2d", "view")

    op.execute("DROP TABLE IF EXISTS person_topics")
    op.execute("DROP TABLE IF EXISTS publication_topics")
    op.execute("DROP TABLE IF EXISTS topics")
