"""Read-only referential-integrity audit for the El Monte database.

The schema intentionally has no FOREIGN KEY constraints (ingest and merges stay
flexible), which means deleted people/publications can leave orphaned topic,
concept, authorship and projection rows behind.  This command reports those
orphans and, with ``--fix``, removes the safe deterministic ones and refreshes
the materialized views affected by authorship changes.

Usage:
    python3 -m scripts.admin.integrity_check
    python3 -m scripts.admin.integrity_check --fix
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from sqlalchemy import text
from sqlalchemy.orm import Session

# (table.column, target.table.column) — orphan rows deleted by --fix.
_ORPHAN_DELETES: tuple[tuple[str, str, str, str], ...] = (
    ("person_topics", "person_id", "people", "id"),
    ("person_concepts", "person_id", "people", "id"),
    ("publication_authors", "person_id", "people", "id"),
    ("publication_authors", "publication_id", "publications", "id"),
    ("publication_topics", "publication_id", "publications", "id"),
    ("publication_topics", "topic_id", "topics", "openalex_topic_id"),
    ("publication_concepts", "publication_id", "publications", "id"),
    ("publication_concepts", "concept_id", "concepts", "id"),
    ("publication_author_affiliations", "publication_id", "publications", "id"),
    ("publication_author_affiliations", "person_id", "people", "id"),
    ("publication_author_affiliations", "organization_id", "organizations", "id"),
    ("affiliation_org_assignments", "affiliation_id", "person_affiliations", "id"),
    ("affiliation_org_assignments", "organization_id", "organizations", "id"),
    ("person_awards", "person_id", "people", "id"),
    ("person_awards", "award_id", "awards", "id"),
    ("grant_participants", "grant_id", "grants", "id"),
    ("grant_participants", "person_id", "people", "id"),
    ("grant_participants", "organization_id", "organizations", "id"),
    ("person_projections_2d", "person_id", "people", "id"),
)

_MATVIEWS = (
    "person_coauthor_edges",
    "person_anchor",
    "org_current_roster",
)


def _count_orphans(session: Session, table: str, column: str, target: str, target_col: str) -> int:
    row = session.execute(
        text(
            f"SELECT count(*) FROM {table} child "
            f"LEFT JOIN {target} parent ON parent.{target_col} = child.{column} "
            f"WHERE parent.{target_col} IS NULL"
        )
    ).scalar_one()
    return int(row)


def _delete_orphans(session: Session, table: str, column: str, target: str, target_col: str) -> int:
    return int(
        session.execute(
            text(
                f"DELETE FROM {table} child "
                f"WHERE NOT EXISTS ("
                f"SELECT 1 FROM {target} parent WHERE parent.{target_col} = child.{column}"
                f")"
            )
        ).rowcount
    )


def _refresh_matviews(session: Session) -> None:
    session.commit()
    for view in _MATVIEWS:
        session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view}"))
    session.commit()


def run(*, fix: bool = False) -> int:
    issues = 0
    stats: Counter = Counter()
    with _SessionLocal() as session:
        print("=== El Monte referential integrity audit ===\n")
        for table, column, target, target_col in _ORPHAN_DELETES:
            count = _count_orphans(session, table, column, target, target_col)
            label = f"{table}.{column} -> {target}.{target_col}"
            if count:
                issues += count
                stats["orphan_rows"] += count
                print(f"  FAIL {label:70s} {count}")
            else:
                print(f"  ok   {label:70s} 0")

        # Referential checks we report but never auto-delete: evidence and
        # external ids are provenance records; deleting them silently would
        # erase audit trail.
        for label, sql in (
            ("evidence.snapshot_id -> source_snapshots", "SELECT count(*) FROM evidence e LEFT JOIN source_snapshots s ON s.id=e.snapshot_id WHERE s.id IS NULL"),
            ("external_identifiers.snapshot_id -> source_snapshots", "SELECT count(*) FROM external_identifiers e LEFT JOIN source_snapshots s ON s.id=e.snapshot_id WHERE e.snapshot_id IS NOT NULL AND s.id IS NULL"),
            ("people.cv_snapshot_id -> source_snapshots", "SELECT count(*) FROM people p LEFT JOIN source_snapshots s ON s.id=p.cv_snapshot_id WHERE p.cv_snapshot_id IS NOT NULL AND s.id IS NULL"),
        ):
            count = int(session.execute(text(sql)).scalar_one())
            if count:
                issues += count
                print(f"  FAIL {label:70s} {count}")
            else:
                print(f"  ok   {label:70s} 0")

        # Shape invariants for the projection tables.
        for label, sql in (
            ("projection_clusters member_count > 0", "SELECT count(*) FROM projection_clusters WHERE member_count <= 0"),
            ("projection_cluster_edges self-loop", "SELECT count(*) FROM projection_cluster_edges WHERE source_cluster = target_cluster"),
            ("projection_cluster_edges negative weight", "SELECT count(*) FROM projection_cluster_edges WHERE (collaboration_weight IS NOT NULL AND collaboration_weight < 0) OR (topic_weight IS NOT NULL AND topic_weight < 0)"),
            ("projection points non-finite", "SELECT count(*) FROM person_projections_2d WHERE NOT (x BETWEEN -1000000 AND 1000000 AND y BETWEEN -1000000 AND 1000000)"),
        ):
            count = int(session.execute(text(sql)).scalar_one())
            if count:
                issues += count
                print(f"  FAIL {label:70s} {count}")
            else:
                print(f"  ok   {label:70s} 0")

        print(f"\n{issues} integrity issue(s) detected.\n")

        if not fix or issues == 0:
            return issues

        print("Fixing safe orphan rows …")
        for table, column, target, target_col in _ORPHAN_DELETES:
            deleted = _delete_orphans(session, table, column, target, target_col)
            if deleted:
                stats["deleted"] += deleted
                print(f"  deleted {deleted} rows from {table}.{column}")
        session.commit()
        print("Refreshing affected materialized views …")
        _refresh_matviews(session)
        print("Done.")
    return issues


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="delete safe orphan rows")
    args = parser.parse_args(argv)
    raise SystemExit(1 if run(fix=args.fix) and not args.fix else 0)


if __name__ == "__main__":
    main()
