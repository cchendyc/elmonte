"""Backfill profile-page facts into the database.

Modes
-----
--parse-only   Read existing local snapshots, run parsers, print what would be
               written. Never touches the DB, never hits the network. Use this
               to iterate on a new parser.
--dry-run      Fetches missing pages if needed, but rolls back all DB writes at
               the end. Snapshot files still land on disk (dedup by hash), so
               it's cheap to re-run.
(default)      Live: fetch + parse + commit.

Scope
-----
Iterates over `external_identifiers` rows with `provider = 'official_url'` and
`person_id IS NOT NULL`, dispatched by hostname to the appropriate parser.
Add a new parser by wiring it into the registry below.

Fact writes today
-----------------
- people.biography (only when currently NULL, unless --overwrite)
- person_affiliations.title on current primary row (only when currently NULL,
  unless --overwrite)
- external_identifiers rows for ORCID (skipped on conflict)
- evidence rows linking each write back to the source snapshot

Skipped for now (not backfill vectors):
- photos, personal-website URLs (parser records them but does not persist)
- publications (deferred to OpenAlex ingest; profile pages rarely have DOIs)
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # allow `python scripts/backfill/profiles.py`

from api.deps import _SessionLocal
from scripts.backfill import common
from scripts.backfill.common import (
    PoliteFetcher,
    ProfileExtraction,
    add_evidence,
    load_snapshot_body,
    parser_for,
    upsert_biography,
    upsert_current_affiliation_title,
    upsert_external_identifier,
    upsert_homepage_url,
    write_snapshot,
)
from scripts.backfill.parsers import (
    berkeley_econ,
    berkeley_haas,
    stanford_econ,
    stanford_gsb,
)

common.register_parser("economics.stanford.edu", stanford_econ.parse)
common.register_parser("econ.berkeley.edu", berkeley_econ.parse)
common.register_parser("haas.berkeley.edu", berkeley_haas.parse)
common.register_parser("www.gsb.stanford.edu", stanford_gsb.parse)


def _iter_targets(session: Session) -> list[tuple[str, int, int | None]]:
    """Return the queue as `(url, person_id, existing_snapshot_id)` tuples.

    The snapshot id is the newest `official_profile` we already have for that
    URL, or None if we've never fetched it. Parse-only mode skips rows with
    None here.
    """
    rows = session.execute(
        text(
            """
            SELECT
              ei.external_id AS url,
              ei.person_id   AS person_id,
              latest.snap_id AS snap_id
            FROM external_identifiers ei
            LEFT JOIN LATERAL (
              SELECT id AS snap_id
              FROM source_snapshots
              WHERE source_url = ei.external_id
                AND source_kind = 'official_profile'
              ORDER BY fetched_at DESC
              LIMIT 1
            ) latest ON TRUE
            WHERE ei.provider = 'official_url'
              AND ei.person_id IS NOT NULL
            """
        )
    ).mappings().all()
    return [(r["url"], int(r["person_id"]), r["snap_id"]) for r in rows]


def _apply(
    session: Session,
    *,
    person_id: int,
    url: str,
    extraction: ProfileExtraction,
    snapshot_id: int,
    overwrite: bool,
) -> Counter:
    """Persist the extraction. Returns per-field counters for reporting."""
    stats: Counter = Counter()

    if extraction.biography:
        if upsert_biography(
            session,
            person_id=person_id,
            biography=extraction.biography,
            overwrite=overwrite,
        ):
            stats["biography_written"] += 1
            add_evidence(
                session,
                snapshot_id=snapshot_id,
                label="profile:biography",
                person_id=person_id,
            )

    if extraction.title:
        result = upsert_current_affiliation_title(
            session,
            person_id=person_id,
            title=extraction.title,
            overwrite=overwrite,
        )
        if result is None:
            stats["title_no_affiliation"] += 1
        else:
            aff_id, wrote = result
            if wrote:
                stats["title_written"] += 1
                add_evidence(
                    session,
                    snapshot_id=snapshot_id,
                    label="profile:title",
                    affiliation_id=aff_id,
                )
            else:
                stats["title_already_set"] += 1

    if extraction.orcid:
        if upsert_external_identifier(
            session,
            provider="orcid",
            external_id=extraction.orcid,
            person_id=person_id,
            snapshot_id=snapshot_id,
        ):
            stats["orcid_written"] += 1

    if extraction.personal_url:
        if upsert_homepage_url(
            session,
            person_id=person_id,
            homepage_url=extraction.personal_url,
            overwrite=overwrite,
        ):
            stats["homepage_written"] += 1
            add_evidence(
                session,
                snapshot_id=snapshot_id,
                label="profile:homepage",
                person_id=person_id,
            )

    if extraction.publications:
        # Deferred to OpenAlex ingest — profile pages lack DOIs and clean
        # author lists. We still count them so we know what we'd be leaving
        # on the table.
        stats["publications_seen_deferred"] += len(extraction.publications)

    return stats


def run(
    *,
    parse_only: bool,
    dry_run: bool,
    overwrite: bool,
    limit: int | None,
    only_host: str | None,
    verbose: bool,
) -> None:
    fetcher = PoliteFetcher()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
        if only_host:
            targets = [t for t in targets if urlparse(t[0]).netloc == only_host]
        if limit is not None:
            targets = targets[:limit]

        totals: Counter = Counter()
        totals["queued"] = len(targets)

        for url, person_id, snap_id in targets:
            host = urlparse(url).netloc
            parse_fn = parser_for(url)
            if parse_fn is None:
                totals[f"no_parser::{host}"] += 1
                continue

            body: bytes | None = None
            html: str | None = None

            if snap_id is not None:
                cached = load_snapshot_body(session, snap_id)
                if cached is not None:
                    _, html = cached

            if html is None:
                if parse_only:
                    totals["skipped_no_snapshot"] += 1
                    continue
                try:
                    status, body, _headers = fetcher.fetch(url)
                except Exception as err:  # noqa: BLE001 — reporting is enough
                    totals[f"fetch_error::{host}"] += 1
                    if verbose:
                        print(f"[fetch-error] {url}: {err}")
                    continue
                snap_id = write_snapshot(
                    session,
                    url=url,
                    source_kind="official_profile",
                    body=body,
                    http_status=status,
                )
                if status >= 400:
                    totals[f"http_{status}"] += 1
                    continue
                html = body.decode(errors="replace")

            assert html is not None
            assert snap_id is not None

            try:
                extraction = parse_fn(url, html)
            except Exception as err:  # noqa: BLE001
                totals[f"parse_error::{host}"] += 1
                if verbose:
                    print(f"[parse-error] {url}: {err}")
                continue

            if parse_only:
                totals["parsed"] += 1
                if extraction.biography:
                    totals["would_biography"] += 1
                if extraction.title:
                    totals["would_title"] += 1
                if extraction.orcid:
                    totals["would_orcid"] += 1
                if extraction.personal_url:
                    totals["would_personal_url"] += 1
                if extraction.notes and verbose:
                    print(f"[note] {url}: {'; '.join(extraction.notes)}")
                if verbose:
                    _print_preview(url, extraction)
                continue

            per = _apply(
                session,
                person_id=person_id,
                url=url,
                extraction=extraction,
                snapshot_id=snap_id,
                overwrite=overwrite,
            )
            totals.update(per)
            totals["applied"] += 1

        if parse_only or dry_run:
            session.rollback()
        else:
            session.commit()

    _print_totals(totals)


def _print_preview(url: str, extraction: ProfileExtraction) -> None:
    print(f"--- {url}")
    if extraction.title:
        print(f"    title: {extraction.title}")
    if extraction.biography:
        preview = extraction.biography.split("\n", 1)[0][:200]
        print(f"    bio:   {preview}")
    if extraction.orcid:
        print(f"    orcid: {extraction.orcid}")
    if extraction.personal_url:
        print(f"    web:   {extraction.personal_url}")


def _print_totals(totals: Counter) -> None:
    print()
    print("=== backfill summary ===")
    for k in sorted(totals):
        print(f"  {k:.<30s} {totals[k]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Read existing local snapshots only. No network, no writes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch as needed but roll back DB writes.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing biography/title values instead of only "
        "filling NULLs.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--host",
        default=None,
        help="Restrict to a single host, e.g. economics.stanford.edu.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    run(
        parse_only=args.parse_only,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        limit=args.limit,
        only_host=args.host,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
