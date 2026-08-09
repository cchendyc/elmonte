"""Backfill publications from OpenAlex.

Matches people to OpenAlex authors (ORCID when available, else name + institution
hint from their official profile URL), fetches works, and writes:

- `publications` + `external_identifiers` (provider = openalex)
- `publication_authors` for every authorship we can resolve to a person in DB
- new `people` rows for OpenAlex coauthors not already in the database (unless
  ``--berkeley-only``, which only links coauthors already in the roster)
- person's OpenAlex author id in `external_identifiers` when newly matched

Berkeley-first workflow (default): ingest roster people anchored at UC Berkeley,
then concepts and projection. Use ``--all-universities`` to include Stanford etc.

    .venv/bin/python -m scripts.backfill.publications --refresh --rebuild-projection
    .venv/bin/python -m scripts.backfill.concepts --rebuild-projection

OpenAlex is the only wired publication API today; ORCID works / Crossref are
possible supplements (see project docs). Run ``cleanup_publications`` after ingest
to drop junk titles already filtered at ingest time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill import common
from scripts.backfill.common import (
    DUPLICATE_PUBLICATION_YEAR_TOLERANCE,
    clean_publication_title,
    publication_titles_equivalent,
    publications_likely_same_paper,
    resolve_berkeley_university_org_id,
    should_skip_openalex_work,
    sql_person_is_berkeley_anchored,
    upsert_external_identifier,
    write_snapshot,
)
from scripts.backfill.openalex import (
    OpenAlexClient,
    institution_hint_from_url,
    pick_best_author,
    short_id,
)

# Coauthors discovered during ingest only — keeps OpenAlex volume manageable.
MIN_COAUTHOR_YEAR = 2000
# Only link authorships in positions 1–4; mega-papers add noise without signal.
MAX_BACKFILL_AUTHOR_POSITION = 4
MAX_AUTHORS_PER_PAPER = 10


def _display_name(first: str, last: str) -> str:
    return f"{first.strip()} {last.strip()}".strip()


def _normalize_person_name(first: str, last: str) -> str:
    return _display_name(first, last).lower()


def _normalize_openalex_name(display_name: str) -> str:
    return " ".join(display_name.strip().lower().split())


def load_people(
    session: Session,
    *,
    person_id: int | None = None,
    skip_with_pubs: bool = False,
    berkeley_only: bool = True,
) -> list[dict[str, Any]]:
    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if person_id:
        where_parts.append("p.id = :pid")
        params["pid"] = person_id
    if skip_with_pubs:
        where_parts.append(
            """
            NOT EXISTS (
              SELECT 1 FROM publication_authors pa
              WHERE pa.person_id = p.id
            )
            """
        )
    if berkeley_only:
        params["berkeley_org_id"] = resolve_berkeley_university_org_id(session)
        where_parts.append(sql_person_is_berkeley_anchored("p.id").strip())
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = session.execute(
        text(
            f"""
            SELECT
              p.id,
              p.firstname,
              p.lastname,
              orcid.external_id  AS orcid,
              oa.external_id     AS openalex_author_id,
              prof.external_id   AS profile_url
            FROM people p
            LEFT JOIN external_identifiers orcid
              ON orcid.person_id = p.id AND orcid.provider = 'orcid'
            LEFT JOIN external_identifiers oa
              ON oa.person_id = p.id AND oa.provider = 'openalex'
            LEFT JOIN external_identifiers prof
              ON prof.person_id = p.id AND prof.provider = 'official_url'
            {where}
            ORDER BY p.id
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def _work_year(work: dict[str, Any]) -> int | None:
    year = work.get("publication_year")
    if year is None:
        return None
    try:
        return int(year)
    except (TypeError, ValueError):
        return None


def _include_work_for_coauthors(work: dict[str, Any]) -> bool:
    year = _work_year(work)
    if year is None or year < MIN_COAUTHOR_YEAR:
        return False
    authorships = work.get("authorships") or []
    if len(authorships) >= MAX_AUTHORS_PER_PAPER:
        return False
    return True


def build_person_indexes(
    session: Session,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return `(openalex_author_id -> person_id, normalized_name -> person_id)`."""
    openalex_map: dict[str, int] = {}
    name_hits: dict[str, list[int]] = {}

    rows = session.execute(
        text(
            """
            SELECT person_id, external_id
            FROM external_identifiers
            WHERE provider = 'openalex' AND person_id IS NOT NULL
            """
        )
    ).all()
    for pid, eid in rows:
        openalex_map[str(eid)] = int(pid)

    people = session.execute(
        text("SELECT id, firstname, lastname FROM people")
    ).all()
    for pid, first, last in people:
        key = _normalize_person_name(first, last)
        name_hits.setdefault(key, []).append(int(pid))

    name_map = {k: v[0] for k, v in name_hits.items() if len(v) == 1}
    return openalex_map, name_map


def _split_display_name(display_name: str) -> tuple[str, str | None, str]:
    parts = display_name.strip().split()
    if not parts:
        raise ValueError("blank display name")
    if len(parts) == 1:
        return parts[0], None, parts[0]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _orcid_from_author(author: dict[str, Any]) -> str | None:
    raw = author.get("orcid") or (author.get("ids") or {}).get("orcid")
    if not raw:
        return None
    token = str(raw).rstrip("/").rsplit("/", 1)[-1]
    return token or None


def ensure_person_from_openalex_author(
    session: Session,
    author: dict[str, Any],
    *,
    openalex_map: dict[str, int],
    name_map: dict[str, int],
) -> int | None:
    """Create a `people` row for an OpenAlex coauthor not yet in the database.

    Scatter placement only includes people in `people`, so unresolved coauthors
    on ingested works never appear on the map until we materialize them here.
    """
    oa_id = short_id(author.get("id"))
    if oa_id and oa_id in openalex_map:
        return openalex_map[oa_id]

    display = (author.get("display_name") or "").strip()
    if not display:
        return None

    key = _normalize_openalex_name(display)
    if key in name_map:
        person_id = name_map[key]
        if oa_id:
            if upsert_external_identifier(
                session,
                provider="openalex",
                external_id=oa_id,
                person_id=person_id,
            ):
                openalex_map[oa_id] = person_id
        return person_id

    first, middle, last = _split_display_name(display)
    person_id = int(
        session.execute(
            text(
                """
                INSERT INTO people (firstname, middlename, lastname)
                VALUES (:f, :m, :l)
                RETURNING id
                """
            ),
            {"f": first, "m": middle, "l": last},
        ).scalar_one()
    )

    if oa_id:
        upsert_external_identifier(
            session,
            provider="openalex",
            external_id=oa_id,
            person_id=person_id,
        )
        openalex_map[oa_id] = person_id

    orcid = _orcid_from_author(author)
    if orcid:
        upsert_external_identifier(
            session,
            provider="orcid",
            external_id=orcid,
            person_id=person_id,
        )

    name_map[key] = person_id
    return person_id


def resolve_openalex_author(
    client: OpenAlexClient,
    person: dict[str, Any],
) -> dict[str, Any] | None:
    if person.get("openalex_author_id"):
        author = client.author_by_id(str(person["openalex_author_id"]))
        if author:
            return author

    orcid = person.get("orcid")
    if orcid:
        author = client.author_by_orcid(str(orcid))
        if author:
            return author

    display = _display_name(person["firstname"], person["lastname"])
    hint = institution_hint_from_url(person.get("profile_url"))
    candidates = client.search_authors(display, institution_hint=hint)
    if not candidates and hint:
        candidates = client.search_authors(display)
    return pick_best_author(candidates, display_name=display, institution_hint=hint)


def _parse_publication_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def lookup_publication_by_openalex(
    session: Session, work_id: str
) -> int | None:
    row = session.execute(
        text(
            """
            SELECT publication_id
            FROM external_identifiers
            WHERE provider = 'openalex' AND external_id = :eid
            """
        ),
        {"eid": work_id},
    ).scalar()
    return int(row) if row is not None else None


def lookup_publication_by_dedupe(
    session: Session, title: str, year: int
) -> int | None:
    """Find an existing row that matches this title within a small year window."""
    year_int = int(year)
    ymin = year_int - DUPLICATE_PUBLICATION_YEAR_TOLERANCE
    ymax = year_int + DUPLICATE_PUBLICATION_YEAR_TOLERANCE
    rows = session.execute(
        text(
            """
            SELECT id, title, publication_year
            FROM publications
            WHERE publication_year BETWEEN :ymin AND :ymax
            """
        ),
        {"ymin": ymin, "ymax": ymax},
    ).all()
    for pub_id, existing_title, existing_year in rows:
        if publications_likely_same_paper(
            title, year_int, existing_title, existing_year
        ):
            return int(pub_id)
    return None


def lookup_publication_by_doi(session: Session, doi: str | None) -> int | None:
    if not doi:
        return None
    doi_id = doi.removeprefix("https://doi.org/").strip()
    if not doi_id:
        return None
    row = session.execute(
        text(
            """
            SELECT publication_id
            FROM external_identifiers
            WHERE provider = 'doi' AND external_id = :eid
            """
        ),
        {"eid": doi_id},
    ).scalar()
    return int(row) if row is not None else None


def upsert_publication(
    session: Session,
    *,
    work: dict[str, Any],
    snapshot_id: int | None,
) -> tuple[int, bool]:
    work_id = short_id(work.get("id"))
    if not work_id:
        raise ValueError("work missing OpenAlex id")

    title = clean_publication_title(
        (work.get("title") or work.get("display_name") or "").strip()
    )
    year = work.get("publication_year")
    if not title or year is None:
        raise ValueError(f"work {work_id} missing title or year")
    if should_skip_openalex_work(work):
        raise ValueError(f"work {work_id} skipped as non-paper output")

    cited_by = work.get("cited_by_count")
    pub_date = _parse_publication_date(work.get("publication_date"))
    doi = (work.get("doi") or "").strip() or None

    existing = lookup_publication_by_openalex(session, work_id)
    if existing is None:
        existing = lookup_publication_by_doi(session, doi)
    if existing is None:
        existing = lookup_publication_by_dedupe(session, title, int(year))
    if existing is not None:
        session.execute(
            text(
                """
                UPDATE publications
                SET title = :title,
                    publication_year = :year,
                    publication_date = coalesce(:pdate, publication_date),
                    cited_by_count = :cites
                WHERE id = :id
                """
            ),
            {
                "title": title,
                "year": int(year),
                "pdate": pub_date,
                "cites": int(cited_by) if cited_by is not None else None,
                "id": existing,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO external_identifiers
                  (provider, external_id, publication_id, snapshot_id)
                VALUES ('openalex', :eid, :pid, :sid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"eid": work_id, "pid": existing, "sid": snapshot_id},
        )
        if doi and doi.startswith("https://doi.org/"):
            doi_id = doi.removeprefix("https://doi.org/")
            session.execute(
                text(
                    """
                    INSERT INTO external_identifiers
                      (provider, external_id, publication_id, snapshot_id)
                    VALUES ('doi', :eid, :pid, :sid)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"eid": doi_id, "pid": existing, "sid": snapshot_id},
            )
        return existing, False

    pub_id = int(
        session.execute(
            text(
                """
                INSERT INTO publications
                  (title, publication_year, publication_date, cited_by_count)
                VALUES (:title, :year, :pdate, :cites)
                RETURNING id
                """
            ),
            {
                "title": title,
                "year": int(year),
                "pdate": pub_date,
                "cites": int(cited_by) if cited_by is not None else None,
            },
        ).scalar_one()
    )

    session.execute(
        text(
            """
            INSERT INTO external_identifiers
              (provider, external_id, publication_id, snapshot_id)
            VALUES ('openalex', :eid, :pid, :sid)
            ON CONFLICT DO NOTHING
            """
        ),
        {"eid": work_id, "pid": pub_id, "sid": snapshot_id},
    )

    doi = (work.get("doi") or "").strip()
    if doi.startswith("https://doi.org/"):
        doi_id = doi.removeprefix("https://doi.org/")
        session.execute(
            text(
                """
                INSERT INTO external_identifiers
                  (provider, external_id, publication_id, snapshot_id)
                VALUES ('doi', :eid, :pid, :sid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"eid": doi_id, "pid": pub_id, "sid": snapshot_id},
        )

    return pub_id, True


def link_authorship(
    session: Session,
    *,
    publication_id: int,
    person_id: int,
    author_position: int,
    is_corresponding: bool,
) -> bool:
    """Link a person to a publication if not already linked.

    Skips when the author position is already taken by someone else (common when
  the same paper was ingested via another roster author or name resolution differs).
    """
    existing = session.execute(
        text(
            """
            SELECT is_corresponding
            FROM publication_authors
            WHERE publication_id = :pub AND person_id = :person
            """
        ),
        {"pub": publication_id, "person": person_id},
    ).scalar_one_or_none()
    if existing is not None:
        if is_corresponding and not bool(existing):
            session.execute(
                text(
                    """
                    UPDATE publication_authors
                    SET is_corresponding = true
                    WHERE publication_id = :pub AND person_id = :person
                    """
                ),
                {"pub": publication_id, "person": person_id},
            )
            return True
        return False

    position_taken = session.execute(
        text(
            """
            SELECT 1
            FROM publication_authors
            WHERE publication_id = :pub
              AND author_position = :pos
              AND person_id != :person
            """
        ),
        {"pub": publication_id, "pos": author_position, "person": person_id},
    ).scalar_one_or_none()
    if position_taken is not None:
        return False

    result = session.execute(
        text(
            """
            INSERT INTO publication_authors
              (publication_id, person_id, author_position, is_corresponding)
            VALUES (:pub, :person, :pos, :corr)
            ON CONFLICT (publication_id, person_id) DO NOTHING
            """
        ),
        {
            "pub": publication_id,
            "person": person_id,
            "pos": author_position,
            "corr": bool(is_corresponding),
        },
    )
    return bool(result.rowcount)


def resolve_person_for_authorship(
    authorship: dict[str, Any],
    *,
    openalex_map: dict[str, int],
    name_map: dict[str, int],
    seed_person_id: int | None,
    seed_author_id: str | None,
) -> int | None:
    author = authorship.get("author") or {}
    oa_id = short_id(author.get("id"))
    if oa_id and oa_id in openalex_map:
        return openalex_map[oa_id]

    display = author.get("display_name")
    if display:
        key = _normalize_openalex_name(display)
        if key in name_map:
            return name_map[key]

    if seed_person_id and seed_author_id and oa_id == seed_author_id:
        return seed_person_id
    return None


def ingest_person_works(
    session: Session,
    client: OpenAlexClient,
    person: dict[str, Any],
    *,
    openalex_map: dict[str, int],
    name_map: dict[str, int],
    max_works: int,
    verbose: bool,
    store_snapshots: bool,
    discover_coauthors: bool = True,
) -> Counter:
    stats: Counter = Counter()
    person_id = int(person["id"])
    display = _display_name(person["firstname"], person["lastname"])

    author = resolve_openalex_author(client, person)
    if author is None:
        stats["authors_unmatched"] += 1
        if verbose:
            print(f"  no OpenAlex match for {display} (id={person_id})")
        return stats

    author_id = short_id(author.get("id"))
    if not author_id:
        stats["authors_unmatched"] += 1
        return stats

    if not person.get("openalex_author_id"):
        if upsert_external_identifier(
            session,
            provider="openalex",
            external_id=author_id,
            person_id=person_id,
        ):
            stats["openalex_ids_linked"] += 1
        openalex_map[author_id] = person_id

    works = client.works_for_author(
        author_id, max_works=max_works, min_year=MIN_COAUTHOR_YEAR
    )
    stats["works_fetched"] += len(works)

    for work in works:
        work_id = short_id(work.get("id"))
        if not work_id:
            continue

        if not _include_work_for_coauthors(work):
            authorships = work.get("authorships") or []
            if len(authorships) >= MAX_AUTHORS_PER_PAPER:
                stats["works_skipped_many_authors"] += 1
            else:
                stats["works_skipped_pre_cutoff"] += 1
            continue

        snapshot_id: int | None = None
        if store_snapshots:
            snapshot_url = f"https://api.openalex.org/works/{work_id}"
            snapshot_id = write_snapshot(
                session,
                url=snapshot_url,
                source_kind="openalex",
                body=json.dumps(work, separators=(",", ":")).encode("utf-8"),
                http_status=200,
            )

        try:
            pub_id, created = upsert_publication(
                session, work=work, snapshot_id=snapshot_id
            )
        except ValueError:
            stats["works_skipped"] += 1
            continue

        if created:
            stats["publications_created"] += 1
        else:
            stats["publications_updated"] += 1

        authorships = work.get("authorships") or []
        for idx, authorship in enumerate(authorships):
            author_position = idx + 1
            if author_position > MAX_BACKFILL_AUTHOR_POSITION:
                continue
            resolved = resolve_person_for_authorship(
                authorship,
                openalex_map=openalex_map,
                name_map=name_map,
                seed_person_id=person_id,
                seed_author_id=author_id,
            )
            if resolved is None and discover_coauthors:
                author_payload = authorship.get("author") or {}
                resolved = ensure_person_from_openalex_author(
                    session,
                    author_payload,
                    openalex_map=openalex_map,
                    name_map=name_map,
                )
                if resolved is not None:
                    stats["coauthors_discovered"] += 1
            if resolved is None:
                continue
            if link_authorship(
                session,
                publication_id=pub_id,
                person_id=resolved,
                author_position=author_position,
                is_corresponding=bool(authorship.get("is_corresponding")),
            ):
                stats["authorship_links"] += 1

    if verbose:
        print(
            f"  {display}: author={author_id} works={len(works)} "
            f"cited={author.get('cited_by_count')}"
        )
    return stats


def repair_publication_titles(session: Session) -> int:
    """Re-clean stored titles. Returns number of rows updated."""
    rows = session.execute(text("SELECT id, title FROM publications")).all()
    updated = 0
    for pub_id, title in rows:
        cleaned = clean_publication_title(title)
        if cleaned and cleaned != title:
            session.execute(
                text("UPDATE publications SET title = :t WHERE id = :id"),
                {"t": cleaned, "id": pub_id},
            )
            updated += 1
    return updated


def refresh_coauthor_edges(session: Session) -> None:
    session.execute(
        text("REFRESH MATERIALIZED VIEW CONCURRENTLY person_coauthor_edges")
    )


def run(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    person_id: int | None = None,
    max_works: int = 120,
    refresh: bool = False,
    rebuild_projection: bool = False,
    store_snapshots: bool = False,
    skip_with_pubs: bool = False,
    verbose: bool = False,
    berkeley_only: bool = True,
) -> None:
    client = OpenAlexClient()
    totals: Counter = Counter()
    discover_coauthors = not berkeley_only

    with _SessionLocal() as session:
        people = load_people(
            session,
            person_id=person_id,
            skip_with_pubs=skip_with_pubs,
            berkeley_only=berkeley_only,
        )
        if limit is not None:
            people = people[:limit]

        openalex_map, name_map = build_person_indexes(session)
        scope = "Berkeley roster" if berkeley_only else "all universities"
        coauthor_mode = (
            "roster coauthors only" if berkeley_only else "discover new coauthors"
        )
        print(
            f"Ingesting OpenAlex works for {len(people)} people ({scope}, "
            f"{coauthor_mode}, max {max_works} works each)"
        )

        for i, person in enumerate(people, start=1):
            if verbose or i % 25 == 0 or i == 1:
                print(f"[{i}/{len(people)}] {person['firstname']} {person['lastname']}", flush=True)
            try:
                per = ingest_person_works(
                    session,
                    client,
                    person,
                    openalex_map=openalex_map,
                    name_map=name_map,
                    max_works=max_works,
                    verbose=verbose,
                    store_snapshots=store_snapshots,
                    discover_coauthors=discover_coauthors,
                )
            except RuntimeError as err:
                print(f"  skipped {person['firstname']} {person['lastname']}: {err}", flush=True)
                totals["people_skipped"] += 1
                if dry_run:
                    session.rollback()
                else:
                    session.commit()
                continue
            totals.update(per)

            if dry_run:
                session.rollback()
            else:
                session.commit()

        if dry_run:
            print("Dry run — rolled back all writes.")
        else:
            print("Committed publication ingest.")

            if refresh:
                print("Refreshing person_coauthor_edges …")
                refresh_coauthor_edges(session)
                session.commit()

    if rebuild_projection and not dry_run:
        print("Rebuilding projection …")
        subprocess.run(
            [sys.executable, "-m", "scripts.embed.build_projection"],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )

    print()
    print("=== publication backfill summary ===")
    for key in sorted(totals):
        print(f"  {key:.<28s} {totals[key]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repair-titles",
        action="store_true",
        help="Re-clean publication titles already in the database, then exit.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument(
        "--max-works",
        type=int,
        default=120,
        help="Cap works fetched per person from OpenAlex (default 120).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="REFRESH MATERIALIZED VIEW person_coauthor_edges after commit.",
    )
    parser.add_argument(
        "--rebuild-projection",
        action="store_true",
        help="Run scripts.embed.build_projection after ingest.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip people who already have publication_authors rows.",
    )
    parser.add_argument(
        "--snapshots",
        action="store_true",
        help="Store per-work OpenAlex JSON snapshots (slower; off by default).",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--all-universities",
        action="store_true",
        help="Include Stanford etc.; default is Berkeley-anchored roster only.",
    )
    args = parser.parse_args(argv)

    if args.repair_titles:
        with _SessionLocal() as session:
            n = repair_publication_titles(session)
            if args.dry_run:
                session.rollback()
                print(f"Dry run — would update {n} publication titles.")
            else:
                session.commit()
                print(f"Updated {n} publication titles.")
        return

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        person_id=args.person_id,
        max_works=args.max_works,
        refresh=args.refresh,
        rebuild_projection=args.rebuild_projection,
        store_snapshots=args.snapshots,
        skip_with_pubs=args.resume,
        verbose=args.verbose,
        berkeley_only=not args.all_universities,
    )


if __name__ == "__main__":
    main()
