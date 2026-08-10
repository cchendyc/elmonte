"""Atlas v2: topics, person_topics, projection views and cluster tables.

Revision ID: fbb6b2990cac
Revises: d4f2a8c19e01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fbb6b2990cac"
down_revision = "d4f2a8c19e01"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.execute(
        "CREATE INDEX idx_publication_topics_topic ON publication_topics (topic_id)"
    )
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
    op.execute("CREATE INDEX idx_person_topics_topic ON person_topics (topic_id)")
    op.add_column(
        "person_projections_2d",
        sa.Column("view", sa.Text, nullable=False, server_default="topic"),
    )
    op.add_column(
        "person_projections_2d",
        sa.Column("cluster_id", sa.SmallInteger, nullable=True),
    )
    # One row per (run, person, view) — the old (run_id, person_id) PK would
    # be violated by the second view's rows.
    op.execute("ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey")
    op.execute("ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id, view)")
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
    op.drop_table("projection_cluster_edges")
    op.drop_table("projection_clusters")
    op.drop_column("person_projections_2d", "cluster_id")
    op.execute("ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey")
    op.execute("ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id)")
    op.drop_column("person_projections_2d", "view")
    op.drop_table("person_topics")
    op.drop_table("publication_topics")
    op.drop_table("topics")
