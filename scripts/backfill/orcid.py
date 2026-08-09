"""Backfill career history from ORCID public records.

Iterates people with an ORCID in `external_identifiers`, fetches their public
record, and writes employment/education rows into `person_affiliations`.

    .venv/bin/python -m scripts.backfill.orcid --dry-run --verbose
    .venv/bin/python -m scripts.backfill.orcid --refresh
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
from scripts.backfill.common import (
    clear_timeline_affiliations,
    insert_timeline_affiliation,
    write_snapshot,
)
from scripts.backfill.orcid_client import OrcidClient
from scripts.backfill.parsers.orcid_record import parse_orcid_record

ORCID_KIND = "manual"
EVIDENCE_LABEL = "orcid:affiliation"


def _iter_targets(session: Session) -> list[tuple[int, str]]:
    rows = session.execute(
        text(
            """
            SELECT person_id, external_id
            FROM external_identifiers
            WHERE provider = 'orcid'
              AND person_id IS NOT NULL
            ORDER BY person_id
            """
        )
    ).all()
    return [(int(pid), str(orcid)) for pid, orcid in rows]


def _apply_orcid(
    session: Session,
    *,
    person_id: int,
    orcid: str,
    record: dict,
    snapshot_id: int,
) -> Counter:
    stats: Counter = Counter()
    candidates = parse_orcid_record(record)
    stats["entries_seen"] += len(candidates)
    for candidate in candidates:
        status, _aff_id = insert_timeline_affiliation(
            session,
            person_id=person_id,
            candidate=candidate,
            snapshot_id=snapshot_id,
            evidence_label=EVIDENCE_LABEL,
        )
        stats[f"affiliation_{status}"] += 1
    return stats


def run(
    *,
    dry_run: bool,
    refresh: bool,
    limit: int | None,
    person_id: int | None,
    verbose: bool,
) -> None:
    client = OrcidClient()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
        if person_id is not None:
            targets = [t for t in targets if t[0] == person_id]
        if limit is not None:
            targets = targets[:limit]

        totals: Counter = Counter()
        totals["queued"] = len(targets)

        for pid, orcid in targets:
            if refresh and not dry_run:
                cleared = clear_timeline_affiliations(
                    session, pid, evidence_label=EVIDENCE_LABEL
                )
                if cleared:
                    totals["orcid_affiliations_cleared"] += cleared

            record = client.fetch_record(orcid)
            if record is None:
                totals["orcid_fetch_failed"] += 1
                if verbose:
                    print(f"[orcid-miss] person={pid} {orcid}")
                continue

            source_url = f"https://pub.orcid.org/v3.0/{orcid}/record"
            snapshot_id = write_snapshot(
                session,
                url=source_url,
                source_kind=ORCID_KIND,
                body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                http_status=200,
                file_ext=".json",
            )

            if verbose:
                print(f"[orcid] person={pid} {orcid} ({len(parse_orcid_record(record))} entries)")

            if dry_run:
                per = _apply_orcid(
                    session,
                    person_id=pid,
                    orcid=orcid,
                    record=record,
                    snapshot_id=snapshot_id,
                )
                totals.update(per)
                totals["parsed"] += 1
                continue

            per = _apply_orcid(
                session,
                person_id=pid,
                orcid=orcid,
                record=record,
                snapshot_id=snapshot_id,
            )
            totals.update(per)
            totals["applied"] += 1

        if dry_run:
            session.rollback()
        else:
            session.commit()

    _print_totals(totals)


def _print_totals(totals: Counter) -> None:
    print()
    print("=== orcid backfill summary ===")
    for key in sorted(totals):
        print(f"  {key:.<30s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse but roll back DB writes.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Delete ORCID-imported affiliations before re-importing.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    run(
        dry_run=args.dry_run,
        refresh=args.refresh,
        limit=args.limit,
        person_id=args.person_id,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
