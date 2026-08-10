from __future__ import annotations

import os
import subprocess
from pathlib import Path

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


def _run_psql(sql: str | Path) -> None:
    # DDL through the direct URL — the pooled (PgBouncer) endpoint is for
    # the app; migrations and psql need a real session.
    url = get_direct_url()
    if isinstance(sql, Path):
        cmd = ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(sql)]
    else:
        cmd = ["psql", url, "-v", "ON_ERROR_STOP=1", "-c", sql]
    subprocess.run(cmd, check=True, env=os.environ.copy())


def apply_schema() -> None:
    """Full rebuild from db/schema.sql (authoritative) — FRESH databases only.

    schema.sql drops and recreates the base tables it owns; tables added by
    later migrations (topics, person_topics, projections, ...) are invisible
    to it, so running it over a migrated database silently wipes their data
    while leaving the migration tables standing.  The initial migration
    (9a20bc7cc432) calls this exactly once, on an empty database.
    """
    _guard_schema_rebuild()
    _run_psql(SCHEMA_SQL)


def _guard_schema_rebuild() -> None:
    """Refuse to rebuild when the target DB already has migrations or
    migration-only tables (defense-in-depth against the 2026-08-09 incident:
    a URL mismatch pointed this at the app database)."""
    from sqlalchemy import create_engine, text

    url = get_direct_url()
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            # Catalog-only probes: on a fresh database alembic_version doesn't
            # exist yet, so never reference it in SQL that Postgres would
            # resolve at parse time.
            has_alembic = conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar()
            n_migrations = (
                conn.execute(text("SELECT count(*) FROM alembic_version")).scalar()
                if has_alembic
                else 0
            )
            has_migration_tables = conn.execute(
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
    finally:
        engine.dispose()


def teardown_schema() -> None:
    """Drop all objects owned by the El Monte schema."""
    _run_psql(TEARDOWN_SQL)
