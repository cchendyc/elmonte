"""Merge duplicate people rows left behind by coauthor discovery.

Coauthor discovery keys people by (normalized firstname, lastname) built from
the *roster* on one path and from OpenAlex *display_name* on another, so the
same real person can end up as two rows (e.g. "Demo Person D" and
"Demo Person D. Doe").  This script merges such pairs — but only when there
is hard evidence they are the same person:

  * they share a publication (publication_authors overlap), or
  * they share an external identifier (OpenAlex author id / ORCID).

Groups are formed by normalized name (first token of firstname + lastname);
a name collision with *no* shared evidence (e.g. two unrelated "Jamie Doe"s
in the legacy seed) is reported but never merged.

Canonical row = the person with the most authorships (ties → lowest id).
All person-id columns are reassigned (with dedup on composite keys), the
duplicate rows are deleted, and the affected materialized views are
refreshed.

Usage::

    python3 -m scripts.admin.dedup_people            # merge + refresh
    python3 -m scripts.admin.dedup_people --dry-run  # report only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

# (table, person_id column, conflict-key columns).  Rows whose key already
# exists for the canonical person are skipped — the canonical row already
# represents that authorship/topic/etc.
_REASSIGN = [
    ("publication_authors", "person_id", ("publication_id",)),
    ("person_topics", "person_id", ("topic_id",)),
    ("person_concepts", "person_id", ("concept_id",)),
    ("grant_participants", "person_id", ("grant_id", "role")),
    ("external_identifiers", "person_id", ()),
    ("person_aliases", "person_id", ("alias",)),
]

_MATVIEWS = ["person_anchor", "person_coauthor_edges"]


def _duplicate_groups(session: Session) -> list[tuple[str, str, list[int]]]:
    """Return [(fn, ln, person_ids)] for normalized-name collisions."""
    rows = session.execute(
        text(
            """
            SELECT lower(split_part(firstname, ' ', 1)) AS fn,
                   lower(lastname) AS ln,
                   array_agg(id ORDER BY id) AS ids
            FROM people
            GROUP BY 1, 2
            HAVING count(*) > 1
            ORDER BY 2, 1
            """
        )
    ).mappings().all()
    return [(r["fn"], r["ln"], list(r["ids"])) for r in rows]


def _shared_evidence(session: Session, a: int, b: int) -> str | None:
    """Return 'publication' / 'external_id' / None for evidence a==b."""
    shared_pub = session.execute(
        text(
            """
            SELECT count(*) FROM publication_authors pa
            JOIN publication_authors pb
              ON pa.publication_id = pb.publication_id
            WHERE pa.person_id = :a AND pb.person_id = :b
            """
        ),
        {"a": a, "b": b},
    ).scalar_one()
    if shared_pub:
        return "publication"
    shared_id = session.execute(
        text(
            """
            SELECT count(*) FROM external_identifiers ea
            JOIN external_identifiers eb
              ON ea.provider = eb.provider AND ea.external_id = eb.external_id
            WHERE ea.person_id = :a AND eb.person_id = :b
            """
        ),
        {"a": a, "b": b},
    ).scalar_one()
    if shared_id:
        return "external_id"
    return None


def _authorship_count(session: Session, person_id: int) -> int:
    return int(
        session.execute(
            text("SELECT count(*) FROM publication_authors WHERE person_id = :p"),
            {"p": person_id},
        ).scalar_one()
    )


def _merge_pair(session: Session, keep: int, drop: int) -> None:
    for table, col, key_cols in _REASSIGN:
        if key_cols:
            # Conflict-aware: skip rows whose key already exists for `keep`
            # (the canonical row already carries that authorship/alias/etc.).
            conflicts = " AND ".join(
                f"NOT EXISTS (SELECT 1 FROM {table} b WHERE b.{kc} = a.{kc} "
                f"AND b.person_id = :keep)"
                for kc in key_cols
            )
            session.execute(
                text(
                    f"UPDATE {table} a SET {col} = :keep WHERE a.{col} = :drop "
                    f"AND {conflicts}"
                ),
                {"keep": keep, "drop": drop},
            )
        else:
            session.execute(
                text(f"UPDATE {table} SET {col} = :keep WHERE {col} = :drop"),
                {"keep": keep, "drop": drop},
            )
    session.execute(text("DELETE FROM people WHERE id = :drop"), {"drop": drop})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only")
    parser.add_argument(
        "--force-merge",
        metavar="ID,ID",
        action="append",
        default=[],
        help="merge a human-verified duplicate pair (comma-separated ids) that "
        "has no shared publication/external-id evidence; the row with more "
        "authorships wins. Only use after manual verification.",
    )
    args = parser.parse_args(argv)

    with _SessionLocal() as session:
        forced: list[tuple[int, int]] = []
        for pair in args.force_merge:
            a, b = (int(x) for x in pair.split(","))
            if _authorship_count(session, a) < _authorship_count(session, b):
                a, b = b, a  # keep the one with more authorships
            forced.append((a, b))
        groups = _duplicate_groups(session)
        merged = 0
        kept_groups = 0
        for fn, ln, ids in groups:
            if len(ids) < 2:
                continue
            # Merge any pair in the group that has shared evidence, transitively.
            keep = ids[0]
            drops: list[int] = []
            evidence: str | None = None
            for other in ids[1:]:
                ev = _shared_evidence(session, keep, other)
                if ev:
                    evidence = ev
                    drops.append(other)
                else:
                    # Try the other direction only when the pair lacks evidence.
                    print(
                        f"  skip {fn} {ln}: person {keep} vs {other} — "
                        f"no shared publication/external id"
                    )
            if not drops:
                continue
            # Canonical = most authorships among the merged set.
            cand = sorted([keep, *drops], key=lambda p: (-_authorship_count(session, p), p))
            keep = cand[0]
            drops = [p for p in cand if p != keep]
            kept_groups += 1
            print(
                f"  merge {fn} {ln}: keep person {keep} ({_authorship_count(session, keep)}"
                f" authorships, evidence={evidence}), drop {drops}"
            )
            if not args.dry_run:
                for drop in drops:
                    _merge_pair(session, keep, drop)
                merged += len(drops)

        for keep, drop in forced:
            kept_groups += 1
            merged += 1
            print(
                f"  force-merge: keep person {keep} ({_authorship_count(session, keep)}"
                f" authorships), drop {drop}"
            )
            if not args.dry_run:
                _merge_pair(session, keep, drop)

        if args.dry_run:
            print(f"[dry-run] {kept_groups} mergeable groups; nothing written")
            return

        for mv in _MATVIEWS:
            session.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
        session.commit()
        print(f"merged {merged} duplicate people into {kept_groups} canonical rows; matviews refreshed")


if __name__ == "__main__":
    main()
