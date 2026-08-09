"""Cache CVs from personal homepages and optionally parse them.

Recommended order for career timelines:
1. `scripts.backfill.orcid`  — structured employment/education (preferred)
2. `scripts.backfill.cvs --save-only`  — cache PDFs for the CV link in the UI
3. `scripts.backfill.cvs`  — optional second pass to parse PDFs (noisy)

Pipeline
--------
1. Iterate people with `homepage_url` set.
2. Fetch the homepage (or reuse a cached snapshot).
3. Discover a CV link (`cv.pdf`, "Curriculum Vitae", etc.).
4. Fetch the CV and store a snapshot + `people.cv_url` / `cv_snapshot_id`.
5. Unless `--save-only`, parse appointments/degrees into `person_affiliations`.

Modes mirror `scripts.backfill.profiles`:
- `--parse-only`   use cached snapshots, no network, no DB writes
- `--dry-run`      fetch as needed, roll back writes at the end
- default          live commit

    .venv/bin/python -m scripts.backfill.cvs --dry-run --verbose
    .venv/bin/python -m scripts.backfill.cvs --limit 5
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import (
    PoliteFetcher,
    clear_cv_affiliations,
    insert_cv_affiliation,
    load_snapshot_body,
    load_snapshot_bytes,
    upsert_person_cv,
    write_snapshot,
)
from scripts.backfill.parsers.cv_discovery import discover_cv_url
from scripts.backfill.parsers.cv_parse import (
    CvTextExtractionError,
    cv_text_from_body,
    is_pdf_body,
    parse_cv_text,
)

HOMEPAGE_KIND = "official_profile"
CV_KIND = "official_profile"


def _iter_targets(session: Session) -> list[tuple[int, str, int | None, int | None]]:
    """`(person_id, homepage_url, homepage_snap_id, cv_snap_id)`."""
    rows = session.execute(
        text(
            """
            SELECT
              p.id            AS person_id,
              p.homepage_url  AS homepage_url,
              home.snap_id    AS homepage_snap_id,
              cv.snap_id      AS cv_snap_id
            FROM people p
            LEFT JOIN LATERAL (
              SELECT id AS snap_id
              FROM source_snapshots
              WHERE source_url = p.homepage_url
                AND source_kind = :homepage_kind
              ORDER BY fetched_at DESC
              LIMIT 1
            ) home ON TRUE
            LEFT JOIN LATERAL (
              SELECT ss.id AS snap_id
              FROM source_snapshots ss
              JOIN evidence e ON e.snapshot_id = ss.id
              JOIN person_affiliations pa ON pa.id = e.affiliation_id
              WHERE pa.person_id = p.id
                AND e.label = 'cv:affiliation'
              ORDER BY ss.fetched_at DESC
              LIMIT 1
            ) cv ON TRUE
            WHERE p.homepage_url IS NOT NULL
            ORDER BY p.id
            """
        ),
        {"homepage_kind": HOMEPAGE_KIND},
    ).mappings().all()
    return [
        (
            int(row["person_id"]),
            row["homepage_url"],
            row["homepage_snap_id"],
            row["cv_snap_id"],
        )
        for row in rows
    ]


def _latest_cv_snapshot_for_homepage(
    session: Session, *, person_id: int, homepage_url: str
) -> tuple[int, str] | None:
    row = session.execute(
        text(
            """
            SELECT ss.id, ss.source_url
            FROM source_snapshots ss
            JOIN evidence e ON e.snapshot_id = ss.id
            JOIN person_affiliations pa ON pa.id = e.affiliation_id
            WHERE pa.person_id = :pid
              AND e.label = 'cv:affiliation'
              AND ss.source_url <> :home
            ORDER BY ss.fetched_at DESC
            LIMIT 1
            """
        ),
        {"pid": person_id, "home": homepage_url},
    ).mappings().first()
    if row is None:
        return None
    return int(row["id"]), row["source_url"]


def _fetch_or_load_homepage(
    session: Session,
    fetcher: PoliteFetcher,
    *,
    homepage_url: str,
    homepage_snap_id: int | None,
    parse_only: bool,
) -> tuple[int, str] | None:
    if homepage_snap_id is not None:
        cached = load_snapshot_body(session, homepage_snap_id)
        if cached is not None:
            return homepage_snap_id, cached[1]

    if parse_only:
        return None

    status, body, _headers = fetcher.fetch(homepage_url)
    snap_id = write_snapshot(
        session,
        url=homepage_url,
        source_kind=HOMEPAGE_KIND,
        body=body,
        http_status=status,
        file_ext=".html",
    )
    if status >= 400:
        return None
    return snap_id, body.decode(errors="replace")


def _fetch_or_load_cv(
    session: Session,
    fetcher: PoliteFetcher,
    *,
    cv_url: str,
    parse_only: bool,
) -> tuple[int, bytes, str | None] | None:
    row = session.execute(
        text(
            """
            SELECT id
            FROM source_snapshots
            WHERE source_url = :u
            ORDER BY fetched_at DESC
            LIMIT 1
            """
        ),
        {"u": cv_url},
    ).scalar()
    if row is not None:
        cached = load_snapshot_bytes(session, int(row))
        if cached is not None:
            return int(row), cached[1], None

    if parse_only:
        return None

    status, body, headers = fetcher.fetch(
        cv_url,
        accept="application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    )
    file_ext = ".pdf" if is_pdf_body(body) else ".html"
    snap_id = write_snapshot(
        session,
        url=cv_url,
        source_kind=CV_KIND,
        body=body,
        http_status=status,
        file_ext=file_ext,
    )
    if status >= 400:
        return None
    return snap_id, body, headers.get("content-type")


def _preview_cv(
    *,
    cv_url: str,
    cv_body: bytes,
    content_type: str | None,
    verbose: bool,
) -> Counter:
    stats: Counter = Counter()
    try:
        raw_text = cv_text_from_body(cv_url, cv_body, content_type=content_type)
    except CvTextExtractionError:
        stats["cv_parse_error"] += 1
        return stats

    if not raw_text:
        stats["cv_empty"] += 1
        return stats

    candidates = parse_cv_text(raw_text)
    stats["entries_seen"] += len(candidates)
    stats["would_affiliation"] += len(candidates)
    if verbose:
        for candidate in candidates[:8]:
            start = candidate.starts_at.year if candidate.starts_at else "?"
            end = (
                candidate.ends_at.year
                if candidate.ends_at
                else ("present" if candidate.starts_at else "?")
            )
            org = candidate.organization or "?"
            print(f"    {start}-{end}  {candidate.title} @ {org}")
        if len(candidates) > 8:
            print(f"    ... {len(candidates) - 8} more")
    return stats


def _apply_cv(
    session: Session,
    *,
    person_id: int,
    cv_url: str,
    cv_body: bytes,
    cv_snap_id: int,
    content_type: str | None,
) -> Counter:
    stats: Counter = Counter()
    try:
        raw_text = cv_text_from_body(cv_url, cv_body, content_type=content_type)
    except CvTextExtractionError:
        stats["cv_parse_error"] += 1
        return stats

    if not raw_text:
        stats["cv_empty"] += 1
        return stats

    candidates = parse_cv_text(raw_text)
    stats["entries_seen"] += len(candidates)
    for candidate in candidates:
        status, _aff_id = insert_cv_affiliation(
            session,
            person_id=person_id,
            candidate=candidate,
            snapshot_id=cv_snap_id,
        )
        stats[f"affiliation_{status}"] += 1
    return stats


def run(
    *,
    parse_only: bool,
    dry_run: bool,
    refresh: bool,
    save_only: bool,
    limit: int | None,
    person_id: int | None,
    verbose: bool,
) -> None:
    fetcher = PoliteFetcher()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
        if person_id is not None:
            targets = [t for t in targets if t[0] == person_id]
        if limit is not None:
            targets = targets[:limit]

        totals: Counter = Counter()
        totals["queued"] = len(targets)

        for pid, homepage_url, homepage_snap_id, _cv_snap_id in targets:
            if refresh and not parse_only and not save_only:
                cleared = clear_cv_affiliations(session, pid)
                if cleared:
                    totals["cv_affiliations_cleared"] += cleared

            host = urlparse(homepage_url).netloc
            homepage = _fetch_or_load_homepage(
                session,
                fetcher,
                homepage_url=homepage_url,
                homepage_snap_id=homepage_snap_id,
                parse_only=parse_only,
            )
            if homepage is None:
                totals[f"homepage_missing::{host}"] += 1
                if verbose:
                    print(f"[skip-homepage] person={pid} {homepage_url}")
                continue

            _homepage_snap_id, homepage_html = homepage
            cv_url = discover_cv_url(homepage_url, homepage_html)
            if not cv_url:
                totals["no_cv_link"] += 1
                if verbose:
                    print(f"[no-cv] person={pid} {homepage_url}")
                continue

            totals["cv_link_found"] += 1
            if verbose:
                print(f"[cv] person={pid} {cv_url}")

            if parse_only:
                prior = _latest_cv_snapshot_for_homepage(
                    session, person_id=pid, homepage_url=homepage_url
                )
                if prior is not None:
                    snap_id, snap_url = prior
                    cached = load_snapshot_bytes(session, snap_id)
                    if cached is not None:
                        per = _preview_cv(
                            cv_url=snap_url,
                            cv_body=cached[1],
                            content_type=None,
                            verbose=verbose,
                        )
                        totals.update(per)
                        totals["parsed"] += 1
                        continue

                totals["skipped_no_cv_snapshot"] += 1
                continue

            loaded = _fetch_or_load_cv(
                session, fetcher, cv_url=cv_url, parse_only=parse_only
            )
            if loaded is None:
                totals[f"cv_fetch_failed::{urlparse(cv_url).netloc}"] += 1
                continue

            cv_snap_id, cv_body, content_type = loaded
            if not parse_only and not dry_run:
                if upsert_person_cv(
                    session,
                    person_id=pid,
                    cv_url=cv_url,
                    cv_snapshot_id=cv_snap_id,
                ):
                    totals["cv_saved"] += 1

            if save_only:
                totals["applied"] += 1
                continue

            try:
                per = _apply_cv(
                    session,
                    person_id=pid,
                    cv_url=cv_url,
                    cv_body=cv_body,
                    cv_snap_id=cv_snap_id,
                    content_type=content_type,
                )
            except Exception as err:  # noqa: BLE001 — batch should continue
                totals["cv_parse_error"] += 1
                if verbose:
                    print(f"[cv-parse-error] person={pid} {cv_url}: {err}")
                continue
            totals.update(per)
            totals["applied"] += 1

        if parse_only or dry_run:
            session.rollback()
        else:
            session.commit()

    _print_totals(totals)


def _print_totals(totals: Counter) -> None:
    print()
    print("=== cv backfill summary ===")
    for key in sorted(totals):
        print(f"  {key:.<30s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parse-only",
        action="store_true",
        help="Read cached snapshots only. No network, no writes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch as needed but roll back DB writes.",
    )
    parser.add_argument(
        "--save-only",
        action="store_true",
        help="Fetch and cache CVs for in-app viewing without parsing PDFs.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Delete CV-imported affiliations for each person before re-parsing.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    run(
        parse_only=args.parse_only,
        dry_run=args.dry_run,
        refresh=args.refresh,
        save_only=args.save_only,
        limit=args.limit,
        person_id=args.person_id,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
