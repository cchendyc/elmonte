from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from db.config import get_direct_url

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "db" / "schema.sql"

TEARDOWN_SQL = """
DROP MATERIALIZED VIEW IF EXISTS
  org_current_roster,
  person_coauthor_edges,
  person_anchor,
  org_tree_current
CASCADE;

DROP TABLE IF EXISTS
  evidence,
  external_identifiers,
  source_snapshots,
  grant_participants,
  grants,
  publication_citations,
  publication_author_affiliations,
  publication_authors,
  publication_concepts,
  publications,
  person_relationships,
  affiliation_org_assignments,
  person_affiliations,
  person_awards,
  awards,
  org_relationships,
  organizations,
  person_concepts,
  concepts,
  person_aliases,
  people,
  projection_cluster_edges,
  projection_clusters,
  person_projections_2d,
  embedding_runs,
  person_topics,
  publication_topics,
  topics,
  org_units,
  org_unit_relationships,
  companies,
  person_positions,
  position_org_assignments,
  founded_relationships
CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

DROP TYPE IF EXISTS
  verification_status,
  claimed_status,
  org_kind,
  org_relationship_type,
  affiliation_kind,
  position_rank,
  assignment_type,
  person_relationship_type,
  grant_role,
  identifier_provider,
  source_kind
CASCADE;
"""


def _sqlalchemy_url(url: str) -> str:
    """Force psycopg3, accepting either PostgreSQL URL spelling."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def apply_schema(connection: Any | None = None) -> None:
    """Full rebuild from db/schema.sql (authoritative) — FRESH databases only.

    ``schema.sql`` owns every object in the current schema (including the
    atlas/topic tables added by historical migrations), so running it over a
    populated database destroys data.  The initial migration calls this once
    with the Alembic connection; later historical migrations become no-ops
    because the end state is already present.
    """
    if connection is None:
        engine = create_engine(_sqlalchemy_url(get_direct_url()))
        try:
            with engine.begin() as conn:
                _guard_schema_rebuild(conn)
                conn.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
            return
        finally:
            engine.dispose()

    _guard_schema_rebuild(connection)
    connection.exec_driver_sql(SCHEMA_SQL.read_text(encoding="utf-8"))


def _guard_schema_rebuild(connection: Any) -> None:
    """Refuse to rebuild when the target DB already has migrations or data.

    Defense-in-depth against the 2026-08-09 incident: a URL mismatch pointed
    this at the app database.
    """
    # Catalog-only probes: on a fresh database alembic_version does not exist
    # yet, so never reference it in SQL that Postgres resolves at parse time.
    has_alembic = connection.execute(
        text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    ).scalar()
    n_migrations = (
        connection.execute(text("SELECT count(*) FROM alembic_version")).scalar()
        if has_alembic
        else 0
    )
    has_migration_tables = connection.execute(
        text(
            "SELECT to_regclass('public.topics') IS NOT NULL"
            " OR to_regclass('public.person_projections_2d') IS NOT NULL"
        )
    ).scalar()
    if n_migrations or has_migration_tables:
        raise RuntimeError(
            "apply_schema() refused: the target database is not fresh "
            f"(alembic_version={n_migrations}, migration tables present="
            f"{has_migration_tables}). schema.sql is a full rebuild for "
            "empty databases only; use `alembic upgrade head` instead."
        )


def teardown_schema(connection: Any | None = None) -> None:
    """Drop all objects owned by the El Monte schema."""
    if connection is None:
        engine = create_engine(_sqlalchemy_url(get_direct_url()))
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(TEARDOWN_SQL)
            return
        finally:
            engine.dispose()

    connection.exec_driver_sql(TEARDOWN_SQL)
