#!/usr/bin/env python3
"""Delete a person and all associated data from the database.

Usage::

    python3 -m scripts.admin.delete_person <person_id_or_orcid> [--dry-run]

Resolves the person by integer id, ``p:<id>`` encoded id, or ORCID (looked up
via ``external_identifiers`` where ``provider='orcid'``).

Without ``--dry-run`` the script commits the deletion at the end.
``--dry-run`` prints what *would* be deleted without executing.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal  # noqa: E402
from api.id_codec import decode  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Tables in dependency order (child before parent) for deletion.
# Each entry: (table_name, condition_sql, param_keys)
# condition_sql uses :pid as the person_id placeholder.
_RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "ingest" / "raw"


def _resolve_person(session: Any, identifier: str) -> tuple[int, str, dict[str, Any]]:
    """Return (person_id, display_name, row_dict) or exit with error."""
    # Try encoded id: p:<id> — decode() accepts both the obfuscated ids the
    # frontend displays (p:qe2pe…) and legacy numeric ids (p:42).
    if identifier.startswith("p:"):
        try:
            kind, person_id = decode(identifier)
        except ValueError:
            print(f"ERROR: malformed encoded id {identifier!r}", file=sys.stderr)
            sys.exit(1)
        if kind != "person":
            print(f"ERROR: {identifier!r} is not a person id", file=sys.stderr)
            sys.exit(1)
        row = session.execute(
            text("SELECT id, firstname, lastname FROM people WHERE id = :pid"),
            {"pid": person_id},
        ).mappings().first()
        if row is None:
            print(f"ERROR: No person found with id {person_id}", file=sys.stderr)
            sys.exit(1)
        name = f"{row['firstname']} {row['lastname']}".strip()
        return person_id, name, dict(row)

    # Try plain integer
    if identifier.isdigit() or (identifier.startswith("-") and identifier[1:].isdigit()):
        person_id = int(identifier)
        row = session.execute(
            text("SELECT id, firstname, lastname FROM people WHERE id = :pid"),
            {"pid": person_id},
        ).mappings().first()
        if row is None:
            print(f"ERROR: No person found with id {person_id}", file=sys.stderr)
            sys.exit(1)
        name = f"{row['firstname']} {row['lastname']}".strip()
        return person_id, name, dict(row)

    # Try ORCID
    row = session.execute(
        text(
            """
            SELECT p.id, p.firstname, p.lastname
            FROM people p
            JOIN external_identifiers ei ON ei.person_id = p.id
            WHERE ei.provider = 'orcid' AND ei.external_id = :orcid
            """
        ),
        {"orcid": identifier},
    ).mappings().first()
    if row is None:
        print(
            f"ERROR: No person found with id or ORCID '{identifier}'",
            file=sys.stderr,
        )
        sys.exit(1)
    name = f"{row['firstname']} {row['lastname']}".strip()
    return int(row["id"]), name, dict(row)


# ---------------------------------------------------------------------------
# Count rows
# ---------------------------------------------------------------------------


def _count(session: Any, table: str, condition: str, params: dict[str, Any]) -> int:
    return int(
        session.execute(
            text(f"SELECT count(*) FROM {table} WHERE {condition}"), params
        ).scalar_one()
    )


# ---------------------------------------------------------------------------
# Deletion plan
# ---------------------------------------------------------------------------


def _build_plan(
    session: Any, person_id: int
) -> dict[str, dict[str, Any]]:
    """Collect every id we need to delete and return a plan dict.

    Returns a dict keyed by "step label" with:
        sql: the DELETE statement (or None for informational steps)
        params: bind params
        count: row count
        info: extra info string
    """
    plan: dict[str, dict[str, Any]] = {}

    # --- Collect IDs ---
    # affiliation ids
    aff_rows = session.execute(
        text("SELECT id FROM person_affiliations WHERE person_id = :pid"),
        {"pid": person_id},
    ).all()
    affiliation_ids = [int(r[0]) for r in aff_rows]

    # person award ids
    pa_rows = session.execute(
        text("SELECT id FROM person_awards WHERE person_id = :pid"),
        {"pid": person_id},
    ).all()
    person_award_ids = [int(r[0]) for r in pa_rows]

    # person relationship ids
    pr_rows = session.execute(
        text(
            """
            SELECT id FROM person_relationships
            WHERE from_person_id = :pid OR to_person_id = :pid
            """
        ),
        {"pid": person_id},
    ).all()
    relationship_ids = [int(r[0]) for r in pr_rows]

    # publication ids where this person is an author
    pub_rows = session.execute(
        text(
            "SELECT publication_id FROM publication_authors WHERE person_id = :pid"
        ),
        {"pid": person_id},
    ).all()
    all_pub_ids = list({int(r[0]) for r in pub_rows})

    # orphan publication ids (no remaining authors after removing this person)
    orphan_pub_ids: list[int] = []
    if all_pub_ids:
        orphan_rows = session.execute(
            text(
                """
                SELECT pa.publication_id
                FROM publication_authors pa
                WHERE pa.publication_id = ANY(:pub_ids)
                GROUP BY pa.publication_id
                HAVING count(*) = 1
                AND bool_or(pa.person_id = :pid)
                """
            ),
            {"pub_ids": all_pub_ids, "pid": person_id},
        ).all()
        orphan_pub_ids = [int(r[0]) for r in orphan_rows]

    # grant ids where person is a participant
    grant_rows = session.execute(
        text(
            "SELECT grant_id FROM grant_participants WHERE person_id = :pid"
        ),
        {"pid": person_id},
    ).all()
    all_grant_ids = list({int(r[0]) for r in grant_rows})

    # orphan grant ids (no remaining participants)
    orphan_grant_ids: list[int] = []
    if all_grant_ids:
        orgrant_rows = session.execute(
            text(
                """
                SELECT gp.grant_id
                FROM grant_participants gp
                WHERE gp.grant_id = ANY(:gids)
                GROUP BY gp.grant_id
                HAVING count(*) = 1
                AND bool_or(gp.person_id = :pid)
                """
            ),
            {"gids": all_grant_ids, "pid": person_id},
        ).all()
        orphan_grant_ids = [int(r[0]) for r in orgrant_rows]

    # --- person_topics ---
    plan["person_topics"] = {
        "sql": "DELETE FROM person_topics WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "person_topics", "person_id = :pid", {"pid": person_id}),
    }

    # --- person_concepts ---
    plan["person_concepts"] = {
        "sql": "DELETE FROM person_concepts WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "person_concepts", "person_id = :pid", {"pid": person_id}),
    }

    # --- pub_author_affiliation ids (for evidence) ---
    pub_aff_rows = session.execute(
        text(
            "SELECT id FROM publication_author_affiliations WHERE person_id = :pid"
        ),
        {"pid": person_id},
    ).all()
    pub_author_affiliation_ids = [int(r[0]) for r in pub_aff_rows]

    # --- person_awards ---
    plan["person_awards"] = {
        "sql": "DELETE FROM person_awards WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "person_awards", "person_id = :pid", {"pid": person_id}),
    }

    # --- person_relationships ---
    plan["person_relationships"] = {
        "sql": "DELETE FROM person_relationships WHERE from_person_id = :pid OR to_person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(
            session,
            "person_relationships",
            "from_person_id = :pid OR to_person_id = :pid",
            {"pid": person_id},
        ),
    }

    # --- person_aliases ---
    plan["person_aliases"] = {
        "sql": "DELETE FROM person_aliases WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "person_aliases", "person_id = :pid", {"pid": person_id}),
    }

    # --- external_identifiers (person_id) ---
    plan["external_identifiers"] = {
        "sql": "DELETE FROM external_identifiers WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "external_identifiers", "person_id = :pid", {"pid": person_id}),
    }

    # --- person_projections_2d ---
    plan["person_projections_2d"] = {
        "sql": "DELETE FROM person_projections_2d WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(session, "person_projections_2d", "person_id = :pid", {"pid": person_id}),
    }

    # --- Publication cleanup ---
    # publication_concepts for orphan pubs
    plan["publication_concepts (orphan)"] = {
        "sql": "DELETE FROM publication_concepts WHERE publication_id = ANY(:ids)",
        "params": {"ids": orphan_pub_ids},
        "count": (
            _count(
                session,
                "publication_concepts",
                "publication_id = ANY(:ids)",
                {"ids": orphan_pub_ids},
            )
            if orphan_pub_ids
            else 0
        ),
    }

    # publication_topics for orphan pubs
    plan["publication_topics (orphan)"] = {
        "sql": "DELETE FROM publication_topics WHERE publication_id = ANY(:ids)",
        "params": {"ids": orphan_pub_ids},
        "count": (
            _count(
                session,
                "publication_topics",
                "publication_id = ANY(:ids)",
                {"ids": orphan_pub_ids},
            )
            if orphan_pub_ids
            else 0
        ),
    }

    # publication_citations for orphan pubs
    plan["publication_citations (orphan)"] = {
        "sql": (
            "DELETE FROM publication_citations "
            "WHERE citing_publication_id = ANY(:ids) OR cited_publication_id = ANY(:ids)"
        ),
        "params": {"ids": orphan_pub_ids},
        "count": (
            _count(
                session,
                "publication_citations",
                "citing_publication_id = ANY(:ids) OR cited_publication_id = ANY(:ids)",
                {"ids": orphan_pub_ids},
            )
            if orphan_pub_ids
            else 0
        ),
    }

    # publication_author_affiliations for all person's pubs
    plan["publication_author_affiliations"] = {
        "sql": (
            "DELETE FROM publication_author_affiliations "
            "WHERE publication_id = ANY(:pub_ids) AND person_id = :pid"
        ),
        "params": {"pub_ids": all_pub_ids, "pid": person_id},
        "count": (
            _count(
                session,
                "publication_author_affiliations",
                "publication_id = ANY(:pub_ids) AND person_id = :pid",
                {"pub_ids": all_pub_ids, "pid": person_id},
            )
            if all_pub_ids
            else 0
        ),
    }

    # publication_authors
    plan["publication_authors"] = {
        "sql": "DELETE FROM publication_authors WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(
            session,
            "publication_authors",
            "person_id = :pid",
            {"pid": person_id},
        ),
    }

    # orphan publications
    plan["publications (orphan)"] = {
        "sql": "DELETE FROM publications WHERE id = ANY(:ids)",
        "params": {"ids": orphan_pub_ids},
        "count": len(orphan_pub_ids),
    }

    # --- Affiliation cleanup ---
    plan["affiliation_org_assignments"] = {
        "sql": "DELETE FROM affiliation_org_assignments WHERE affiliation_id = ANY(:ids)",
        "params": {"ids": affiliation_ids},
        "count": (
            _count(
                session,
                "affiliation_org_assignments",
                "affiliation_id = ANY(:ids)",
                {"ids": affiliation_ids},
            )
            if affiliation_ids
            else 0
        ),
    }

    plan["person_affiliations"] = {
        "sql": "DELETE FROM person_affiliations WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(
            session,
            "person_affiliations",
            "person_id = :pid",
            {"pid": person_id},
        ),
    }

    # --- Evidence cleanup ---
    # Build conditions for all evidence subjects
    ev_conditions: list[str] = []
    ev_params: dict[str, Any] = {"pid": person_id}
    if person_id:
        ev_conditions.append("person_id = :pid")
    if affiliation_ids:
        ev_conditions.append("affiliation_id = ANY(:aff_ids)")
        ev_params["aff_ids"] = affiliation_ids
    if relationship_ids:
        ev_conditions.append("person_relationship_id = ANY(:rel_ids)")
        ev_params["rel_ids"] = relationship_ids
    if person_award_ids:
        ev_conditions.append("person_award_id = ANY(:award_ids)")
        ev_params["award_ids"] = person_award_ids
    if pub_author_affiliation_ids:
        ev_conditions.append("pub_author_affiliation_id = ANY(:pub_aff_ids)")
        ev_params["pub_aff_ids"] = pub_author_affiliation_ids
    if all_grant_ids:
        ev_conditions.append("grant_id = ANY(:grant_ids)")
        ev_params["grant_ids"] = all_grant_ids

    if ev_conditions:
        ev_where = " OR ".join(ev_conditions)
        plan["evidence"] = {
            "sql": f"DELETE FROM evidence WHERE {ev_where}",
            "params": ev_params,
            "count": _count(
                session,
                "evidence",
                ev_where,
                {**ev_params},
            ),
        }
    else:
        plan["evidence"] = {
            "sql": None,
            "params": {},
            "count": 0,
        }

    # --- Collect snapshot IDs from to-be-deleted evidence ---
    snapshot_ids: list[int] = []
    if ev_conditions:
        ev_where = " OR ".join(ev_conditions)
        snap_rows = session.execute(
            text(
                f"SELECT DISTINCT snapshot_id FROM evidence WHERE {ev_where}"
            ),
            {**ev_params},
        ).all()
        snapshot_ids = [int(r[0]) for r in snap_rows if r[0] is not None]

    # --- Grant cleanup ---
    plan["grant_participants"] = {
        "sql": "DELETE FROM grant_participants WHERE person_id = :pid",
        "params": {"pid": person_id},
        "count": _count(
            session,
            "grant_participants",
            "person_id = :pid",
            {"pid": person_id},
        ),
    }

    plan["grants (orphan)"] = {
        "sql": "DELETE FROM grants WHERE id = ANY(:ids)",
        "params": {"ids": orphan_grant_ids},
        "count": len(orphan_grant_ids),
    }

    # --- Source snapshots ---
    # After evidence deletion, find snapshots no longer referenced
    if snapshot_ids:
        orphan_snap = session.execute(
            text(
                """
                SELECT ss.id, ss.local_path
                FROM source_snapshots ss
                WHERE ss.id = ANY(:sids)
                AND NOT EXISTS (
                    SELECT 1 FROM evidence e WHERE e.snapshot_id = ss.id
                )
                """
            ),
            {"sids": snapshot_ids},
        ).all()
        orphan_snapshot_ids = [int(r[0]) for r in orphan_snap]
        orphan_local_paths = [r[1] for r in orphan_snap if r[1]]
    else:
        orphan_snapshot_ids = []
        orphan_local_paths = []

    plan["source_snapshots (orphan)"] = {
        "sql": "DELETE FROM source_snapshots WHERE id = ANY(:ids)",
        "params": {"ids": orphan_snapshot_ids},
        "count": len(orphan_snapshot_ids),
        "info": f"local files to remove: {len(orphan_local_paths)}",
        "local_paths": orphan_local_paths,
    }

    # --- Person ---
    plan["people"] = {
        "sql": "DELETE FROM people WHERE id = :pid",
        "params": {"pid": person_id},
        "count": 1,
    }

    # --- Materialized views ---
    plan["REFRESH person_coauthor_edges"] = {"sql": None, "params": {}, "count": 0}
    plan["REFRESH person_anchor"] = {"sql": None, "params": {}, "count": 0}
    plan["REFRESH org_current_roster"] = {"sql": None, "params": {}, "count": 0}
    plan["REFRESH org_tree_current"] = {"sql": None, "params": {}, "count": 0}

    return plan


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def _execute_plan(
    session: Any,
    plan: dict[str, dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Execute (or simulate) the deletion plan. Returns summary of counts."""
    summary: dict[str, int] = {}

    for label, step in plan.items():
        if label.startswith("REFRESH "):
            mv_name = label.removeprefix("REFRESH ")
            if dry_run:
                print(f"  [DRY-RUN] Would REFRESH MATERIALIZED VIEW {mv_name}")
                summary[label] = 0
            else:
                try:
                    session.execute(
                        text(f"REFRESH MATERIALIZED VIEW {mv_name}")
                    )
                    print(f"  [OK] REFRESH {mv_name}")
                    summary[label] = 0
                except Exception as exc:
                    print(f"  [FAIL] REFRESH {mv_name}: {exc}")
                    summary[label] = 0
            continue

        sql = step.get("sql")
        count = step.get("count", 0)
        params = step.get("params", {})

        if sql is None:
            summary[label] = 0
            continue

        if count == 0 and "ANY(:ids)" not in str(sql):
            # Nothing to delete for this step
            summary[label] = 0
            continue

        if dry_run:
            print(f"  [DRY-RUN] {label}: {count} row(s)")
            summary[label] = count
            continue

        if count > 0 or "ANY(:ids)" in str(sql):
            result = session.execute(text(sql), params)
            actual = result.rowcount
            summary[label] = actual
            if actual > 0:
                print(f"  [DEL] {label}: {actual} row(s)")
            else:
                print(f"  [--]  {label}: 0 rows")

        # Handle local file cleanup for source_snapshots
        if label == "source_snapshots (orphan)" and not dry_run:
            local_paths = step.get("local_paths", [])
            for local_path in local_paths:
                if not local_path:
                    continue
                full_path = Path(local_path)
                if not full_path.is_absolute():
                    full_path = Path(__file__).resolve().parents[2] / local_path
                try:
                    resolved = full_path.resolve()
                    raw_resolved = _RAW_DIR.resolve()
                    # Safety: only remove if inside data/ingest/raw/
                    if str(resolved).startswith(str(raw_resolved) + os.sep) or resolved == raw_resolved:
                        if resolved.exists():
                            os.remove(str(resolved))
                            print(f"  [RM]  {local_path}")
                        else:
                            print(f"  [--]  {local_path} (already gone)")
                    else:
                        print(f"  [SKIP] {local_path} (outside raw dir, not removed)")
                except Exception as exc:
                    print(f"  [WARN] Could not remove {local_path}: {exc}")

    return summary


def _remaining_people(session: Any) -> int:
    return int(
        session.execute(text("SELECT count(*) FROM people")).scalar_one()
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Delete a person and all associated data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "identifier",
        help="Person id (integer), p:<id>, or ORCID",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what WOULD be deleted without executing.",
    )
    args = parser.parse_args(argv)

    session = _SessionLocal()
    try:
        person_id, name, _person_row = _resolve_person(session, args.identifier)
        print(f"Person: {name} (id={person_id})")

        plan = _build_plan(session, person_id)

        if args.dry_run:
            print("\n=== DRY RUN — no changes will be made ===\n")
        else:
            print(f"\nDeleting {name} (id={person_id}) ...\n")

        summary = _execute_plan(session, plan, dry_run=args.dry_run)

        if args.dry_run:
            session.rollback()
            print("\nDry run complete. No changes committed.")
        else:
            session.commit()
            remaining = _remaining_people(session)
            print(f"\nCommit OK. Remaining people: {remaining}")

        # Print summary
        print("\n--- Deletion Summary ---")
        total_deleted = 0
        for label, count in summary.items():
            if count > 0:
                print(f"  {label}: {count}")
                total_deleted += count
        if not args.dry_run:
            print(f"  (total rows deleted: {total_deleted})")
        print(f"  Remaining people: {_remaining_people(session) if not args.dry_run else _count(session, 'people', 'TRUE', {})}")
        print("-------------------------")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
