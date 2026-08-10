"""Discover ORCID ids via the ORCID public search API.

Matches people by name and cross-checks against their current institution
from `person_affiliations` / `organizations`, not just the profile URL host.

    .venv/bin/python -m scripts.backfill.orcid_discover --dry-run --verbose
    .venv/bin/python -m scripts.backfill.orcid_discover

Then import career timelines:

    .venv/bin/python -m scripts.backfill.orcid
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import upsert_external_identifier, write_snapshot
from scripts.backfill.orcid_client import (
    OrcidClient,
    affiliation_hint_from_profile_url,
    affiliation_hints_from_org,
    pick_best_orcid,
    search_orcid_candidates,
)


def _iter_targets(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id,
              p.firstname,
              p.lastname,
              prof.external_id AS profile_url,
              org.name         AS org_name,
              org.short_name   AS org_short_name
            FROM people p
            LEFT JOIN external_identifiers prof
              ON prof.person_id = p.id AND prof.provider = 'official_url'
            LEFT JOIN person_affiliations pa
              ON pa.person_id = p.id AND pa.is_primary AND pa.ends_at IS NULL
            LEFT JOIN affiliation_org_assignments aoa
              ON aoa.affiliation_id = pa.id AND aoa.assignment_type = 'chart_anchor'
            LEFT JOIN organizations org
              ON org.id = aoa.organization_id
            WHERE NOT EXISTS (
              SELECT 1
              FROM external_identifiers o
              WHERE o.provider = 'orcid' AND o.person_id = p.id
            )
            ORDER BY p.id
            """
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _affiliation_hints(
    *,
    org_name: str | None,
    org_short_name: str | None,
    profile_url: str | None,
) -> list[str]:
    hints = affiliation_hints_from_org(org_name, org_short_name)
    profile_hint = affiliation_hint_from_profile_url(profile_url)
    if profile_hint and profile_hint.lower() not in {h.lower() for h in hints}:
        hints.append(profile_hint)
    return hints


def _discover_orcid(
    client: OrcidClient,
    *,
    firstname: str,
    lastname: str,
    affiliation_hints: list[str],
) -> tuple[dict | None, list[dict]]:
    candidates = search_orcid_candidates(
        client,
        firstname=firstname,
        lastname=lastname,
        affiliation_hints=affiliation_hints,
    )
    match = pick_best_orcid(
        candidates,
        firstname=firstname,
        lastname=lastname,
        affiliation_hints=affiliation_hints,
    )
    return match, candidates


def run(
    *,
    dry_run: bool,
    limit: int | None,
    person_id: int | None,
    verbose: bool,
) -> None:
    client = OrcidClient()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
    if person_id is not None:
        targets = [t for t in targets if int(t["id"]) == person_id]
    if limit is not None:
        targets = targets[:limit]

    totals: Counter = Counter()
    totals["queued"] = len(targets)
    pending_writes: list[tuple[int, str, str, str, dict, list[dict], list[str]]] = []

    for person in targets:
        pid = int(person["id"])
        firstname = str(person["firstname"])
        lastname = str(person["lastname"])
        profile_url = person.get("profile_url")
        affiliation_hints = _affiliation_hints(
            org_name=person.get("org_name"),
            org_short_name=person.get("org_short_name"),
            profile_url=profile_url,
        )

        match, candidates = _discover_orcid(
            client,
            firstname=firstname,
            lastname=lastname,
            affiliation_hints=affiliation_hints,
        )
        if match is None:
            totals["no_match"] += 1
            if verbose:
                org = person.get("org_short_name") or person.get("org_name") or "?"
                print(
                    f"[miss] person={pid} {firstname} {lastname} @ {org} "
                    f"({len(candidates)} candidates)"
                )
            continue

        orcid = str(match["orcid-id"])
        totals["matched"] += 1
        if verbose:
            print(f"[match] person={pid} {firstname} {lastname} -> {orcid}")

        if dry_run:
            totals["would_write"] += 1
            continue

        pending_writes.append(
            (pid, firstname, lastname, orcid, match, candidates, affiliation_hints)
        )

    if pending_writes and not dry_run:
        with _SessionLocal() as session:
            for (
                pid,
                firstname,
                lastname,
                orcid,
                match,
                candidates,
                affiliation_hints,
            ) in pending_writes:
                search_url = (
                    f"https://pub.orcid.org/v3.0/expanded-search/"
                    f"?q=family-name:{lastname}+AND+given-names:{firstname.split()[0]}"
                )
                snapshot_id = write_snapshot(
                    session,
                    url=search_url,
                    source_kind="manual",
                    body=json.dumps(
                        {
                            "match": match,
                            "candidates": candidates,
                            "affiliation_hints": affiliation_hints,
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    http_status=200,
                    file_ext=".json",
                )
                if upsert_external_identifier(
                    session,
                    provider="orcid",
                    external_id=orcid,
                    person_id=pid,
                    snapshot_id=snapshot_id,
                ):
                    totals["orcid_written"] += 1
                else:
                    totals["orcid_conflict"] += 1
            session.commit()

    _print_totals(totals)


def _print_totals(totals: Counter) -> None:
    print()
    print("=== orcid discover summary ===")
    for key in sorted(totals):
        print(f"  {key:.<30s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Search and report matches without writing to the DB.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        person_id=args.person_id,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
