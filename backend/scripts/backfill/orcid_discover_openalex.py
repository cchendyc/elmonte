"""Discover ORCID ids for people missed by ORCID search, via OpenAlex.

Falls back to OpenAlex author records when the ORCID registry search was
ambiguous or empty. Requires ``OPENALEX_API_KEY`` in ``.env``.

    .venv/bin/python -m scripts.backfill.orcid_discover_openalex --dry-run --verbose
    .venv/bin/python -m scripts.backfill.orcid_discover_openalex
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import canonicalize_orcid, upsert_external_identifier, write_snapshot
from scripts.backfill.openalex import (
    OpenAlexClient,
    institution_hint_from_url,
    pick_best_author,
    short_id,
)


def _iter_targets(session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id,
              p.firstname,
              p.lastname,
              prof.external_id AS profile_url,
              oa.external_id   AS openalex_author_id
            FROM people p
            LEFT JOIN external_identifiers prof
              ON prof.person_id = p.id AND prof.provider = 'official_url'
            LEFT JOIN external_identifiers oa
              ON oa.person_id = p.id AND oa.provider = 'openalex'
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


def _orcid_from_author(author: dict) -> str | None:
    return canonicalize_orcid(str(author.get("orcid") or ""))


def _discover_via_openalex(
    client: OpenAlexClient,
    *,
    firstname: str,
    lastname: str,
    profile_url: str | None,
    openalex_author_id: str | None,
) -> tuple[dict | None, str | None]:
    display_name = f"{firstname} {lastname}".strip()
    hint = institution_hint_from_url(profile_url)

    if openalex_author_id:
        author = client.author_by_id(openalex_author_id)
        if author and _orcid_from_author(author):
            return author, _orcid_from_author(author)

    candidates = client.search_authors(display_name, institution_hint=hint)
    author = pick_best_author(
        candidates,
        display_name=display_name,
        institution_hint=hint,
    )
    if author is None:
        return None, None
    orcid = _orcid_from_author(author)
    if not orcid:
        return author, None
    return author, orcid


def run(
    *,
    dry_run: bool,
    limit: int | None,
    person_id: int | None,
    verbose: bool,
) -> None:
    client = OpenAlexClient()

    with _SessionLocal() as session:
        targets = _iter_targets(session)
        if person_id is not None:
            targets = [t for t in targets if int(t["id"]) == person_id]
        if limit is not None:
            targets = targets[:limit]

        totals: Counter = Counter()
        totals["queued"] = len(targets)

        for person in targets:
            pid = int(person["id"])
            firstname = str(person["firstname"])
            lastname = str(person["lastname"])
            profile_url = person.get("profile_url")
            openalex_author_id = person.get("openalex_author_id")

            try:
                author, orcid = _discover_via_openalex(
                    client,
                    firstname=firstname,
                    lastname=lastname,
                    profile_url=profile_url,
                    openalex_author_id=openalex_author_id,
                )
            except RuntimeError as err:
                totals["openalex_error"] += 1
                if verbose:
                    print(f"[error] person={pid} {firstname} {lastname}: {err}")
                continue

            if author is None:
                totals["no_author_match"] += 1
                if verbose:
                    print(f"[miss-author] person={pid} {firstname} {lastname}")
                continue

            if not orcid:
                totals["author_without_orcid"] += 1
                if verbose:
                    aid = short_id(author.get("id"))
                    print(
                        f"[miss-orcid] person={pid} {firstname} {lastname} "
                        f"(OpenAlex {aid})"
                    )
                continue

            totals["matched"] += 1
            if verbose:
                print(f"[match] person={pid} {firstname} {lastname} -> {orcid}")

            if dry_run:
                totals["would_write"] += 1
                continue

            snapshot_id = write_snapshot(
                session,
                url=str(author.get("id") or f"openalex:author:{pid}"),
                source_kind="openalex",
                body=json.dumps(author, ensure_ascii=False).encode("utf-8"),
                http_status=200,
                file_ext=".json",
            )

            oa_id = short_id(author.get("id"))
            if oa_id and not openalex_author_id:
                upsert_external_identifier(
                    session,
                    provider="openalex",
                    external_id=oa_id,
                    person_id=pid,
                    snapshot_id=snapshot_id,
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

        if dry_run:
            session.rollback()
        else:
            session.commit()

    _print_totals(totals)


def _print_totals(totals: Counter) -> None:
    print()
    print("=== orcid discover (openalex) summary ===")
    for key in sorted(totals):
        print(f"  {key:.<30s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
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
