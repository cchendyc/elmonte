from __future__ import annotations

import os
import subprocess
from pathlib import Path

from db.config import get_database_url

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
    if isinstance(sql, Path):
        cmd = ["psql", get_database_url(), "-v", "ON_ERROR_STOP=1", "-f", str(sql)]
    else:
        cmd = ["psql", get_database_url(), "-v", "ON_ERROR_STOP=1", "-c", sql]
    subprocess.run(cmd, check=True, env=os.environ.copy())


def apply_schema() -> None:
    """Full rebuild from db/schema.sql (authoritative)."""
    _run_psql(SCHEMA_SQL)


def teardown_schema() -> None:
    """Drop all objects owned by the El Monte schema."""
    _run_psql(TEARDOWN_SQL)
