"""Backfill affiliations for people without institutions using OpenAlex.

Resolves each person's OpenAlex author record, walks the institution lineage
up to the root, materializes university/school/department/lab nodes as needed,
and chart-anchors a primary affiliation at the most specific unit.

    .venv/bin/python -m scripts.backfill.openalex_institutions --dry-run
    .venv/bin/python -m scripts.backfill.openalex_institutions
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

from scripts.backfill.common import upsert_external_identifier, write_snapshot
from scripts.backfill.openalex import OpenAlexClient, pick_best_author, short_id
from scripts.backfill.rosters import refresh_roster_views

EVIDENCE_LABEL = "openalex:affiliation"
_INSTITUTION_CACHE: dict[str, dict[str, Any]] = {}


def _short_name(name: str) -> str | None:
    cleaned = name.strip()
    if not cleaned:
        return None
    paren = re.search(r"\(([^)]+)\)\s*$", cleaned)
    if paren:
        return paren.group(1).strip()
    # Only a genuinely short/acronym name is a useful UI label. The old
    # "first four words" fallback produced labels like
    # "University of California, San" — truncation is not abbreviation.
    return cleaned if len(cleaned.split()) <= 4 else None


def _infer_org_kind(inst: dict[str, Any], *, is_root: bool, is_leaf: bool) -> str:
    oa_type = (inst.get("type") or "").lower()
    name = (inst.get("display_name") or "").lower()

    if oa_type == "company":
        return "company"
    if oa_type == "nonprofit":
        return "nonprofit"
    if oa_type == "government":
        return "government"
    if oa_type == "publisher":
        return "publisher"
    if oa_type == "consortium":
        return "consortium"

    if "department" in name or " dept" in name or name.startswith("dept "):
        return "department"
    if "school of" in name or name.endswith(" school"):
        return "school"
    if "laboratory" in name or " lab" in name or name.endswith(" lab"):
        return "lab"
    if "institute" in name:
        return "institute"

    if oa_type == "education":
        if is_root:
            if re.search(
                r"universit|college|school|academy|polytechnic|universidad|universität|hochschule|institute of technology|eth zurich|unsw|uclouvain|tu wien|vishwa vidyapeetham|jamia hamdard",
                name,
                re.IGNORECASE,
            ):
                return "university"
            # OpenAlex marks hospitals, museums and research centres as
            # education orgs. They are real institutions, but not
            # universities, and should not crowd the trace picker.
            return "institute"
        if is_leaf:
            return "department"
        return "school"

    if is_root:
        if re.search(
            r"universit|college|school|academy|polytechnic|universidad|universität|hochschule|institute of technology|eth zurich|unsw|uclouvain|tu wien|vishwa vidyapeetham|jamia hamdard",
            name,
            re.IGNORECASE,
        ):
            return "university"
        return "institute"
    if is_leaf:
        return "department"
    return "institute"


def _pick_institution(author: dict[str, Any]) -> dict[str, Any] | None:
    insts = list(author.get("last_known_institutions") or [])
    if insts:
        education = [inst for inst in insts if inst.get("type") == "education"]
        return education[0] if education else insts[0]

    affiliations = list(author.get("affiliations") or [])
    if not affiliations:
        return None

    def latest_year(aff: dict[str, Any]) -> int:
        years = aff.get("years") or []
        return max(years) if years else 0

    ranked = sorted(affiliations, key=latest_year, reverse=True)
    education = [
        aff
        for aff in ranked
        if (aff.get("institution") or {}).get("type") == "education"
    ]
    pick = education[0] if education else ranked[0]
    return pick.get("institution")


def _fetch_institution(client: OpenAlexClient, institution_id: str) -> dict[str, Any]:
    iid = short_id(institution_id)
    if not iid:
        raise ValueError(f"invalid institution id {institution_id!r}")
    cached = _INSTITUTION_CACHE.get(iid)
    if cached is not None:
        return cached
    inst = client.institution_by_id(iid)
    if inst is None:
        raise RuntimeError(f"OpenAlex institution {iid} not found")
    _INSTITUTION_CACHE[iid] = inst
    return inst


def _institution_chain(
    client: OpenAlexClient, institution: dict[str, Any]
) -> list[dict[str, Any]]:
    lineage = institution.get("lineage") or []
    if not lineage:
        lineage = [institution.get("id")]
    chain: list[dict[str, Any]] = []
    for raw_id in lineage:
        if not raw_id:
            continue
        chain.append(_fetch_institution(client, str(raw_id)))
    if not chain:
        chain = [institution]
    return chain


def _load_openalex_org_map(session: Session) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            SELECT external_id, organization_id
            FROM external_identifiers
            WHERE provider = 'openalex' AND organization_id IS NOT NULL
            """
        )
    ).all()
    return {str(eid): int(oid) for eid, oid in rows}


def _find_org_by_name(session: Session, name: str) -> int | None:
    row = session.execute(
        text("SELECT id FROM organizations WHERE name = :name LIMIT 1"),
        {"name": name},
    ).scalar()
    return int(row) if row is not None else None


def _ensure_org_relationship(
    session: Session, *, child_org_id: int, parent_org_id: int
) -> None:
    if child_org_id == parent_org_id:
        # Some OpenAlex lineage chains repeat an institution; a self-parent is
        # a database check violation, not a reason to abort the whole person.
        return
    child_kind = session.execute(
        text("SELECT kind FROM organizations WHERE id = :oid"), {"oid": child_org_id}
    ).scalar()
    if child_kind == "university":
        # The product model treats universities as roots. OpenAlex lineages can
        # place an existing university under a state system / institute (UC
        # Berkeley under IGI, MIT under Lincoln Lab, ...); accepting that would
        # silently re-home every chart already anchored to the university.
        return
    existing_parent = session.execute(
        text(
            """
            SELECT parent_org_id FROM org_relationships
            WHERE child_org_id = :child
              AND relationship_type = 'primary'
              AND ends_at IS NULL
            LIMIT 1
            """
        ),
        {"child": child_org_id},
    ).scalar()
    if existing_parent is not None:
        return
    session.execute(
        text(
            """
            INSERT INTO org_relationships
              (child_org_id, parent_org_id, relationship_type, verification_status)
            VALUES (:child, :parent, 'primary', 'unverified')
            """
        ),
        {"child": child_org_id, "parent": parent_org_id},
    )


def ensure_institution_tree(
    session: Session,
    client: OpenAlexClient,
    institution: dict[str, Any],
    *,
    openalex_map: dict[str, int],
) -> int | None:
    """Materialize the OpenAlex lineage chain and return the leaf org id."""
    chain = _institution_chain(client, institution)
    if not chain:
        return None

    org_ids: list[int] = []
    for idx, inst in enumerate(chain):
        is_root = idx == 0
        is_leaf = idx == len(chain) - 1
        kind = _infer_org_kind(inst, is_root=is_root, is_leaf=is_leaf)
        oa_id = short_id(inst.get("id"))
        name = (inst.get("display_name") or "").strip()

        if oa_id and oa_id in openalex_map:
            org_id = openalex_map[oa_id]
        else:
            existing = _find_org_by_name(session, name)
            if existing is not None:
                org_id = existing
            else:
                org_id = int(
                    session.execute(
                        text(
                            """
                            INSERT INTO organizations
                              (name, short_name, country, kind)
                            VALUES (:name, :short, :country, :kind)
                            RETURNING id
                            """
                        ),
                        {
                            "name": name,
                            "short": _short_name(name),
                            "country": inst.get("country_code"),
                            "kind": kind,
                        },
                    ).scalar_one()
                )
            if oa_id:
                upsert_external_identifier(
                    session,
                    provider="openalex",
                    external_id=oa_id,
                    organization_id=org_id,
                )
                openalex_map[oa_id] = org_id
        if not org_ids or org_ids[-1] != org_id:
            org_ids.append(org_id)

    if len(org_ids) == 1:
        return org_ids[0]

    for child_id, parent_id in zip(org_ids[1:], org_ids[:-1], strict=False):
        _ensure_org_relationship(session, child_org_id=child_id, parent_org_id=parent_id)

    return org_ids[-1]


def _ensure_primary_affiliation(
    session: Session,
    *,
    person_id: int,
    organization_id: int,
    snapshot_id: int,
) -> bool:
    existing = session.execute(
        text(
            """
            SELECT pa.id
            FROM person_affiliations pa
            JOIN affiliation_org_assignments aoa ON aoa.affiliation_id = pa.id
            WHERE pa.person_id = :p
              AND pa.is_primary
              AND pa.ends_at IS NULL
              AND aoa.organization_id = :o
              AND aoa.assignment_type = 'chart_anchor'
            """
        ),
        {"p": person_id, "o": organization_id},
    ).scalar()
    if existing is not None:
        return False

    aff_id = int(
        session.execute(
            text(
                """
                INSERT INTO person_affiliations
                  (person_id, affiliation_kind, is_primary, verification_status)
                VALUES (:p, 'employment', TRUE, 'unverified')
                RETURNING id
                """
            ),
            {"p": person_id},
        ).scalar_one()
    )
    session.execute(
        text(
            """
            INSERT INTO affiliation_org_assignments
              (affiliation_id, organization_id, assignment_type)
            VALUES (:a, :o, 'chart_anchor')
            """
        ),
        {"a": aff_id, "o": organization_id},
    )
    session.execute(
        text(
            """
            INSERT INTO evidence (snapshot_id, label, affiliation_id)
            VALUES (:s, :l, :a)
            """
        ),
        {"s": snapshot_id, "l": EVIDENCE_LABEL, "a": aff_id},
    )
    return True


def _iter_targets(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id,
              p.firstname,
              p.lastname,
              oa.external_id AS openalex_author_id
            FROM people p
            LEFT JOIN external_identifiers oa
              ON oa.person_id = p.id AND oa.provider = 'openalex'
            WHERE NOT EXISTS (
              SELECT 1 FROM person_affiliations pa WHERE pa.person_id = p.id
            )
            ORDER BY p.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def ingest_person(
    session: Session,
    client: OpenAlexClient,
    person: dict[str, Any],
    *,
    openalex_org_map: dict[str, int],
) -> Counter:
    stats: Counter = Counter()
    person_id = int(person["id"])
    display = f"{person['firstname']} {person['lastname']}".strip()

    author_id = person.get("openalex_author_id")
    author: dict[str, Any] | None = None
    if author_id:
        author = client.author_by_id(str(author_id))
    if author is None:
        candidates = client.search_authors(display, per_page=5)
        author = pick_best_author(candidates, display_name=display, institution_hint=None)
        if author is None:
            stats["author_unresolved"] += 1
            return stats
        oa_id = short_id(author.get("id"))
        if oa_id:
            upsert_external_identifier(
                session,
                provider="openalex",
                external_id=oa_id,
                person_id=person_id,
            )

    institution = _pick_institution(author)
    if institution is None:
        stats["institution_missing"] += 1
        return stats

    snapshot_id = write_snapshot(
        session,
        url=f"https://api.openalex.org/authors/{short_id(author.get('id'))}",
        source_kind="openalex",
        body=json.dumps(author, separators=(",", ":")).encode("utf-8"),
        http_status=200,
        file_ext=".json",
    )

    leaf_org_id = ensure_institution_tree(
        session, client, institution, openalex_map=openalex_org_map
    )
    if leaf_org_id is None:
        stats["institution_tree_failed"] += 1
        return stats

    stats["orgs_materialized"] += 1
    if _ensure_primary_affiliation(
        session,
        person_id=person_id,
        organization_id=leaf_org_id,
        snapshot_id=snapshot_id,
    ):
        stats["affiliations_created"] += 1
    else:
        stats["affiliations_existing"] += 1
    return stats


def run(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    commit_batch: int = 50,
    verbose: bool = False,
) -> None:
    client = OpenAlexClient()
    totals: Counter = Counter()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
        if limit is not None:
            targets = targets[:limit]
        totals["queued"] = len(targets)
        openalex_org_map = _load_openalex_org_map(session)

        pending = 0
        for i, person in enumerate(targets, start=1):
            if verbose or i % 25 == 0 or i == 1:
                print(
                    f"[{i}/{len(targets)}] {person['firstname']} {person['lastname']}",
                    flush=True,
                )
            map_before = dict(openalex_org_map)
            try:
                # A savepoint scopes each person's writes. One bad lineage can
                # no longer strand a half-written org/assignment pair from the
                # same transaction, and it never rolls back previously
                # successful people waiting in the current batch.
                with session.begin_nested():
                    per = ingest_person(
                        session,
                        client,
                        person,
                        openalex_org_map=openalex_org_map,
                    )
            except (RuntimeError, urllib.error.URLError, IntegrityError) as err:
                totals["errors"] += 1
                if verbose:
                    print(f"  error: {err}", flush=True)
                # The DB savepoint rolled back any org rows inserted for this
                # person; the Python org-id cache must be rolled back too, or
                # the next author at that institution would receive an
                # affiliation assignment to a row that no longer exists.
                openalex_org_map.clear()
                openalex_org_map.update(map_before)
                continue
            totals.update(per)
            pending += 1
            if dry_run:
                session.rollback()
                openalex_org_map.clear()
                openalex_org_map.update(map_before)
            elif pending >= commit_batch:
                session.commit()
                pending = 0

        if dry_run:
            print("Dry run — rolled back all writes.")
        else:
            if pending:
                session.commit()
            print("Refreshing org/person materialized views …", flush=True)
            refresh_roster_views(session)
            session.commit()

    print("\n=== openalex institution backfill summary ===")
    for key in sorted(totals):
        print(f"  {key:.<28s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    run(dry_run=args.dry_run, limit=args.limit, verbose=args.verbose)


if __name__ == "__main__":
    main()
