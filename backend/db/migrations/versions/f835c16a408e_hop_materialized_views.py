"""Add hop materialized views: person_anchor, person_coauthor_edges, org_current_roster.

The click-driven UI needs three lookups to be indexed and cheap:

* person_anchor: "where does person X currently hang in the tree"
* person_coauthor_edges: "top-K coauthors of person X by shared paper count"
* org_current_roster: "sorted, paginable roster of org O"

Without materialization these each require a multi-table join with a validity
filter (person_anchor, org_current_roster) or a self-join across every
publication (person_coauthor_edges). At scale they become the bottleneck of
every click.

The views preserve the temporal `validity` column rather than filtering by
CURRENT_DATE, so they only go stale on writes, not on the passage of time.

Revision ID: f835c16a408e
Revises: 5a41dfb4d79c
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f835c16a408e"
down_revision = "5a41dfb4d79c"
branch_labels = None
depends_on = None


PERSON_ANCHOR = """
CREATE MATERIALIZED VIEW person_anchor AS
SELECT
  pa.person_id,
  pa.id             AS affiliation_id,
  pa.title,
  pa.position_rank,
  pa.is_primary,
  pa.validity,
  aoa.organization_id
FROM person_affiliations pa
JOIN affiliation_org_assignments aoa
  ON aoa.affiliation_id = pa.id
WHERE aoa.assignment_type = 'chart_anchor';
"""

PERSON_ANCHOR_INDEXES = [
    "CREATE UNIQUE INDEX person_anchor_affiliation ON person_anchor (affiliation_id)",
    "CREATE INDEX person_anchor_person ON person_anchor (person_id)",
    "CREATE INDEX person_anchor_org ON person_anchor (organization_id)",
    "CREATE INDEX person_anchor_validity ON person_anchor USING gist (validity)",
]

PERSON_COAUTHOR_EDGES = """
CREATE MATERIALIZED VIEW person_coauthor_edges AS
SELECT
  LEAST(a.person_id, b.person_id)    AS person_a,
  GREATEST(a.person_id, b.person_id) AS person_b,
  count(*)                           AS paper_count,
  min(p.publication_year)            AS first_year,
  max(p.publication_year)            AS last_year
FROM publication_authors a
JOIN publication_authors b
  ON b.publication_id = a.publication_id
 AND b.person_id > a.person_id
JOIN publications p ON p.id = a.publication_id
GROUP BY 1, 2;
"""

PERSON_COAUTHOR_EDGES_INDEXES = [
    "CREATE UNIQUE INDEX person_coauthor_edges_pair ON person_coauthor_edges (person_a, person_b)",
    "CREATE INDEX person_coauthor_edges_a_top ON person_coauthor_edges (person_a, paper_count DESC)",
    "CREATE INDEX person_coauthor_edges_b_top ON person_coauthor_edges (person_b, paper_count DESC)",
]

ORG_CURRENT_ROSTER = r"""
CREATE MATERIALIZED VIEW org_current_roster AS
SELECT
  aoa.organization_id,
  pa.person_id,
  pa.id                                                    AS affiliation_id,
  pe.firstname,
  pe.middlename,
  pe.lastname,
  lower(pe.lastname) || E'\t' || lower(pe.firstname)       AS sort_key,
  pa.title,
  pa.position_rank,
  pa.is_primary,
  pa.validity,
  pe.claimed_status
FROM person_affiliations pa
JOIN affiliation_org_assignments aoa ON aoa.affiliation_id = pa.id
JOIN people pe ON pe.id = pa.person_id
WHERE aoa.assignment_type = 'chart_anchor';
"""

ORG_CURRENT_ROSTER_INDEXES = [
    "CREATE UNIQUE INDEX org_current_roster_id ON org_current_roster (organization_id, affiliation_id)",
    "CREATE INDEX org_current_roster_page ON org_current_roster (organization_id, sort_key, person_id)",
    "CREATE INDEX org_current_roster_rank ON org_current_roster (organization_id, position_rank) WHERE position_rank IS NOT NULL",
    "CREATE INDEX org_current_roster_person ON org_current_roster (person_id)",
    "CREATE INDEX org_current_roster_validity ON org_current_roster USING gist (validity)",
]


def upgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT to_regclass('person_anchor') IS NOT NULL")).scalar():
        return
    op.execute(PERSON_ANCHOR)
    for ix in PERSON_ANCHOR_INDEXES:
        op.execute(ix)

    op.execute(PERSON_COAUTHOR_EDGES)
    for ix in PERSON_COAUTHOR_EDGES_INDEXES:
        op.execute(ix)

    op.execute(ORG_CURRENT_ROSTER)
    for ix in ORG_CURRENT_ROSTER_INDEXES:
        op.execute(ix)


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT to_regclass('person_anchor') IS NULL")).scalar():
        return
    op.execute("DROP MATERIALIZED VIEW IF EXISTS org_current_roster CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS person_coauthor_edges CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS person_anchor CASCADE")
