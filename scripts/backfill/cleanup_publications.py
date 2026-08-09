"""Remove junk publications and merge duplicates already in the database.

Run after OpenAlex ingest to repair inflated coauthor counts:

    .venv/bin/python -m scripts.backfill.cleanup_publications
    .venv/bin/python -m scripts.backfill.cleanup_publications --rebuild-projection
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import (
    is_junk_publication_title,
    normalize_title_for_dedupe,
    publications_likely_same_paper,
)
from scripts.backfill.publications import (
    MAX_AUTHORS_PER_PAPER,
    MAX_BACKFILL_AUTHOR_POSITION,
    refresh_coauthor_edges,
)


def _chunked(ids: list[int], size: int) -> list[list[int]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


def _delete_publications(session: Session, pub_ids: list[int]) -> None:
    if not pub_ids:
        return
    params = {"ids": pub_ids}
    session.execute(
        text(
            "DELETE FROM publication_author_affiliations "
            "WHERE publication_id = ANY(:ids)"
        ),
        params,
    )
    session.execute(
        text("DELETE FROM publication_authors WHERE publication_id = ANY(:ids)"),
        params,
    )
    session.execute(
        text("DELETE FROM publication_concepts WHERE publication_id = ANY(:ids)"),
        params,
    )
    session.execute(
        text(
            "DELETE FROM publication_citations "
            "WHERE citing_publication_id = ANY(:ids) "
            "   OR cited_publication_id = ANY(:ids)"
        ),
        params,
    )
    session.execute(
        text("DELETE FROM external_identifiers WHERE publication_id = ANY(:ids)"),
        params,
    )
    session.execute(text("DELETE FROM publications WHERE id = ANY(:ids)"), params)


def _pick_canonical(rows: list[dict]) -> int:
    """Prefer higher citations, then DOI, then lowest id."""

    def score(row: dict) -> tuple[int, int, int]:
        cites = int(row["cited_by_count"] or 0)
        has_doi = 1 if row["has_doi"] else 0
        return (cites, has_doi, -int(row["id"]))

    return int(max(rows, key=score)["id"])


def _merge_publication(session: Session, dup_id: int, canonical_id: int) -> None:
    if dup_id == canonical_id:
        return

    existing_people = {
        int(row[0])
        for row in session.execute(
            text(
                "SELECT person_id FROM publication_authors WHERE publication_id = :id"
            ),
            {"id": canonical_id},
        ).all()
    }
    max_pos = int(
        session.execute(
            text(
                """
                SELECT coalesce(max(author_position), 0)
                FROM publication_authors
                WHERE publication_id = :id
                """
            ),
            {"id": canonical_id},
        ).scalar_one()
    )

    dup_authors = session.execute(
        text(
            """
            SELECT person_id, author_position, is_corresponding
            FROM publication_authors
            WHERE publication_id = :dup
            ORDER BY author_position
            """
        ),
        {"dup": dup_id},
    ).all()
    for person_id, _pos, is_corresponding in dup_authors:
        person_id = int(person_id)
        if person_id in existing_people:
            continue
        max_pos += 1
        session.execute(
            text(
                """
                INSERT INTO publication_authors
                  (publication_id, person_id, author_position, is_corresponding)
                VALUES (:pub, :person, :pos, :corr)
                """
            ),
            {
                "pub": canonical_id,
                "person": person_id,
                "pos": max_pos,
                "corr": bool(is_corresponding),
            },
        )
        existing_people.add(person_id)
    session.execute(
        text(
            """
            INSERT INTO publication_concepts (publication_id, concept_id, score)
            SELECT :canonical, concept_id, score
            FROM publication_concepts
            WHERE publication_id = :dup
            ON CONFLICT (publication_id, concept_id) DO NOTHING
            """
        ),
        {"canonical": canonical_id, "dup": dup_id},
    )
    session.execute(
        text(
            """
            UPDATE external_identifiers
            SET publication_id = :canonical
            WHERE publication_id = :dup
              AND NOT EXISTS (
                SELECT 1 FROM external_identifiers ei
                WHERE ei.publication_id = :canonical
                  AND ei.provider = external_identifiers.provider
              )
            """
        ),
        {"canonical": canonical_id, "dup": dup_id},
    )
    session.execute(
        text("DELETE FROM external_identifiers WHERE publication_id = :dup"),
        {"dup": dup_id},
    )
    session.execute(
        text(
            """
            UPDATE publication_citations
            SET citing_publication_id = :canonical
            WHERE citing_publication_id = :dup
              AND cited_publication_id <> :canonical
            """
        ),
        {"canonical": canonical_id, "dup": dup_id},
    )
    session.execute(
        text(
            """
            UPDATE publication_citations
            SET cited_publication_id = :canonical
            WHERE cited_publication_id = :dup
              AND citing_publication_id <> :canonical
            """
        ),
        {"canonical": canonical_id, "dup": dup_id},
    )
    _delete_publications(session, [dup_id])


def _load_publications(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT
              p.id,
              p.title,
              p.publication_year,
              p.cited_by_count,
              EXISTS (
                SELECT 1 FROM external_identifiers ei
                WHERE ei.publication_id = p.id AND ei.provider = 'doi'
              ) AS has_doi
            FROM publications p
            ORDER BY p.id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def _cluster_duplicates(rows: list[dict]) -> list[list[dict]]:
    """Cluster duplicate papers (same title across years or OpenAlex work ids)."""
    norm_groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        norm_groups[normalize_title_for_dedupe(row["title"])].append(row)

    clusters: list[list[dict]] = []
    for group in norm_groups.values():
        if len(group) <= 1:
            continue

        parent = list(range(len(group)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def unite(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if publications_likely_same_paper(
                    group[i]["title"],
                    group[i]["publication_year"],
                    group[j]["title"],
                    group[j]["publication_year"],
                ):
                    unite(i, j)

        buckets: dict[int, list[dict]] = defaultdict(list)
        for idx, row in enumerate(group):
            buckets[find(idx)].append(row)
        clusters.extend(bucket for bucket in buckets.values() if len(bucket) > 1)
    return clusters


def _prune_authorship_limits(session: Session) -> tuple[int, int, int]:
    """Drop mega-papers and authorship rows beyond the backfill position cap."""
    mega_ids = [
        int(row[0])
        for row in session.execute(
            text(
                """
                SELECT publication_id
                FROM publication_authors
                GROUP BY publication_id
                HAVING count(*) >= :max_authors
                """
            ),
            {"max_authors": MAX_AUTHORS_PER_PAPER},
        ).all()
    ]
    if mega_ids:
        _delete_publications(session, mega_ids)

    _ = int(
        session.execute(
            text(
                """
                DELETE FROM publication_author_affiliations paa
                USING publication_authors pa
                WHERE pa.publication_id = paa.publication_id
                  AND pa.person_id = paa.person_id
                  AND pa.author_position > :max_position
                """
            ),
            {"max_position": MAX_BACKFILL_AUTHOR_POSITION},
        ).rowcount
        or 0
    )
    removed_authors = int(
        session.execute(
            text(
                """
                DELETE FROM publication_authors
                WHERE author_position > :max_position
                """
            ),
            {"max_position": MAX_BACKFILL_AUTHOR_POSITION},
        ).rowcount
        or 0
    )

    orphan_ids = [
        int(row[0])
        for row in session.execute(
            text(
                """
                SELECT p.id
                FROM publications p
                WHERE NOT EXISTS (
                  SELECT 1 FROM publication_authors pa
                  WHERE pa.publication_id = p.id
                )
                """
            )
        ).all()
    ]
    if orphan_ids:
        _delete_publications(session, orphan_ids)

    return len(mega_ids), removed_authors, len(orphan_ids)


def cleanup_publications(
    session: Session,
    *,
    dry_run: bool = False,
    commit_batch: int = 100,
    prune_only: bool = False,
) -> Counter:
    stats: Counter = Counter()
    mega_deleted, authors_trimmed, orphans_deleted = _prune_authorship_limits(session)
    stats["mega_papers_deleted"] = mega_deleted
    stats["late_authors_removed"] = authors_trimmed
    stats["orphan_papers_deleted"] = orphans_deleted
    if prune_only:
        return stats

    print("Loading publications for dedupe …", flush=True)
    rows = _load_publications(session)
    print(f"  {len(rows)} publication rows", flush=True)

    junk_ids = [
        int(row["id"]) for row in rows if is_junk_publication_title(row["title"])
    ]
    stats["junk_deleted"] = len(junk_ids)
    if junk_ids and not dry_run:
        for chunk in _chunked(junk_ids, commit_batch):
            _delete_publications(session, chunk)
            session.commit()

    junk_set = set(junk_ids)
    survivors = [row for row in rows if int(row["id"]) not in junk_set]
    clusters = _cluster_duplicates(survivors)
    print(f"  {len(clusters)} duplicate clusters to merge", flush=True)
    merged_ids: set[int] = set()
    pending = 0
    for cluster in clusters:
        cluster = [row for row in cluster if int(row["id"]) not in merged_ids]
        if len(cluster) < 2:
            continue
        canonical_id = _pick_canonical(cluster)
        for row in cluster:
            dup_id = int(row["id"])
            if dup_id == canonical_id or dup_id in merged_ids:
                continue
            stats["duplicates_merged"] += 1
            if not dry_run:
                _merge_publication(session, dup_id, canonical_id)
                merged_ids.add(dup_id)
                pending += 1
                if pending >= commit_batch:
                    session.commit()
                    pending = 0

    if not dry_run and pending:
        session.commit()

    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Only drop mega-papers and trim authorships beyond position 4.",
    )
    parser.add_argument(
        "--rebuild-projection",
        action="store_true",
        help="Refresh coauthor edges and rebuild the scatter projection.",
    )
    args = parser.parse_args(argv)

    with _SessionLocal() as session:
        stats = cleanup_publications(
            session, dry_run=args.dry_run, prune_only=args.prune_only
        )
        if args.dry_run:
            session.rollback()
            print("Dry run — rolled back.")
        else:
            print("Committed publication cleanup.")
            print("Refreshing person_coauthor_edges …")
            refresh_coauthor_edges(session)
            session.commit()

    print("\n=== publication cleanup summary ===")
    for key in sorted(stats):
        print(f"  {key:.<28s} {stats[key]}")

    if args.rebuild_projection and not args.dry_run:
        print("Rebuilding projection …")
        subprocess.run(
            [sys.executable, "-m", "scripts.embed.build_projection"],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )


if __name__ == "__main__":
    main()
