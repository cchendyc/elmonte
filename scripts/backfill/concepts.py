"""Backfill OpenAlex concepts onto publications and people.

Pipeline:
  1. For each publication linked to OpenAlex, fetch work metadata (or read a
     stored snapshot) and write ``publication_concepts``.
  2. Aggregate each person's top concepts into ``person_concepts``.

Requires ``OPENALEX_API_KEY`` in ``.env``. Berkeley-first (default):

    .venv/bin/python -m scripts.backfill.publications --refresh
    .venv/bin/python -m scripts.backfill.concepts --rebuild-projection

Use ``--all-universities`` on both scripts to include Stanford etc.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import (
    resolve_berkeley_university_org_id,
    sql_person_is_berkeley_anchored,
)
from scripts.backfill.openalex import OpenAlexClient, short_id

# OpenAlex concept scores on works are 0–100; we store 0–1 in Postgres.
MIN_WORK_CONCEPT_SCORE = 25.0
MAX_CONCEPTS_PER_WORK = 8
MAX_CONCEPTS_PER_PERSON = 15
# Skip ultra-broad level-0 tags unless nothing else exists.
SKIP_LEVEL_ZERO_UNLESS_ONLY = True


def _normalize_openalex_score(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val > 1.0:
        val /= 100.0
    return max(0.0, min(1.0, val))


def upsert_concept(session: Session, display_name: str, level: int) -> int:
    row = session.execute(
        text(
            """
            INSERT INTO concepts (display_name, level)
            VALUES (:name, :level)
            ON CONFLICT (display_name) DO UPDATE
              SET level = LEAST(concepts.level, EXCLUDED.level)
            RETURNING id
            """
        ),
        {"name": display_name.strip(), "level": int(level)},
    ).scalar_one()
    return int(row)


def link_publication_concept(
    session: Session,
    publication_id: int,
    concept_id: int,
    score: float | None,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO publication_concepts (publication_id, concept_id, score)
            VALUES (:pub, :concept, :score)
            ON CONFLICT (publication_id, concept_id) DO UPDATE
              SET score = GREATEST(
                COALESCE(publication_concepts.score, 0),
                COALESCE(EXCLUDED.score, 0)
              )
            """
        ),
        {"pub": publication_id, "concept": concept_id, "score": score},
    )


def extract_work_concepts(work: dict[str, Any]) -> list[tuple[str, int, float]]:
    """Return (display_name, level, score_0_1) sorted by score descending."""
    out: list[tuple[str, int, float]] = []
    for item in work.get("concepts") or []:
        name = (item.get("display_name") or "").strip()
        if not name:
            continue
        score = _normalize_openalex_score(item.get("score"))
        if score is None or score * 100.0 < MIN_WORK_CONCEPT_SCORE:
            continue
        level = int(item.get("level") or 0)
        out.append((name, level, score))
    out.sort(key=lambda t: t[2], reverse=True)
    return out[:MAX_CONCEPTS_PER_WORK]


def load_publication_targets(
    session: Session, *, berkeley_only: bool = True
) -> list[dict[str, Any]]:
    berkeley_filter = ""
    params: dict[str, Any] = {}
    if berkeley_only:
        params["berkeley_org_id"] = resolve_berkeley_university_org_id(session)
        berkeley_filter = f"AND {sql_person_is_berkeley_anchored('pa2.person_id').strip()}"
    rows = session.execute(
        text(
            f"""
            SELECT
              pub.id AS publication_id,
              ei.external_id AS work_id,
              snap.local_path AS snapshot_path
            FROM publications pub
            JOIN external_identifiers ei
              ON ei.publication_id = pub.id
             AND ei.provider = 'openalex'
            LEFT JOIN source_snapshots snap ON snap.id = ei.snapshot_id
            WHERE EXISTS (
              SELECT 1
              FROM publication_authors pa2
              WHERE pa2.publication_id = pub.id
              {berkeley_filter}
            )
            ORDER BY pub.id
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def backfill_publication_concepts(
    session: Session,
    client: OpenAlexClient,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    limit: int | None = None,
    berkeley_only: bool = True,
) -> dict[str, int]:
    stats = {
        "publications_seen": 0,
        "works_fetched": 0,
        "snapshot_hits": 0,
        "concept_links": 0,
        "publications_with_concepts": 0,
        "errors": 0,
    }
    targets = load_publication_targets(session, berkeley_only=berkeley_only)
    if limit is not None:
        targets = targets[:limit]
    stats["publications_seen"] = len(targets)

    for row in targets:
        pub_id = int(row["publication_id"])
        work_id = short_id(row.get("work_id"))
        if not work_id:
            stats["errors"] += 1
            continue

        work: dict[str, Any] | None = None
        snapshot_path = row.get("snapshot_path")
        if snapshot_path:
            path = Path(str(snapshot_path))
            if path.is_file():
                try:
                    work = json.loads(path.read_bytes())
                    stats["snapshot_hits"] += 1
                except (json.JSONDecodeError, OSError):
                    work = None

        if work is None:
            try:
                work = client.get_json(f"/works/{work_id}")
                stats["works_fetched"] += 1
            except RuntimeError as err:
                stats["errors"] += 1
                if verbose:
                    print(f"  skip work {work_id}: {err}", flush=True)
                continue

        concepts = extract_work_concepts(work)
        if not concepts:
            continue

        if SKIP_LEVEL_ZERO_UNLESS_ONLY:
            non_root = [c for c in concepts if c[1] > 0]
            if non_root:
                concepts = non_root

        linked = 0
        for name, level, score in concepts:
            concept_id = upsert_concept(session, name, level)
            link_publication_concept(session, pub_id, concept_id, score)
            linked += 1
        if linked:
            stats["concept_links"] += linked
            stats["publications_with_concepts"] += 1

        if verbose and linked:
            print(f"  pub {pub_id} ({work_id}): {linked} concepts", flush=True)

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return stats


def refresh_person_concepts(
    session: Session,
    *,
    person_id: int | None = None,
    berkeley_only: bool = True,
) -> int:
    """Rebuild person_concepts from publication_concepts. Returns row count."""
    berkeley_params: dict[str, Any] = {}
    berkeley_person_filter = ""
    if berkeley_only:
        berkeley_params["berkeley_org_id"] = resolve_berkeley_university_org_id(session)
        berkeley_person_filter = (
            f"AND {sql_person_is_berkeley_anchored('pa.person_id').strip()}"
        )

    if person_id is not None:
        session.execute(
            text("DELETE FROM person_concepts WHERE person_id = :pid"),
            {"pid": person_id},
        )
        person_filter = "AND pa.person_id = :pid"
        params: dict[str, Any] = {
            "pid": person_id,
            "max_rank": MAX_CONCEPTS_PER_PERSON,
            **berkeley_params,
        }
    elif berkeley_only:
        session.execute(
            text(
                f"""
                DELETE FROM person_concepts pc
                WHERE {sql_person_is_berkeley_anchored("pc.person_id").strip()}
                """
            ),
            berkeley_params,
        )
        person_filter = berkeley_person_filter
        params = {"max_rank": MAX_CONCEPTS_PER_PERSON, **berkeley_params}
    else:
        session.execute(text("DELETE FROM person_concepts"))
        person_filter = ""
        params = {"max_rank": MAX_CONCEPTS_PER_PERSON}

    result = session.execute(
        text(
            f"""
            INSERT INTO person_concepts (person_id, concept_id, score, rank)
            SELECT person_id, concept_id, score, rn
            FROM (
              SELECT
                pa.person_id,
                pc.concept_id,
                (sum(COALESCE(pc.score, 0.5)) / count(*)::float) AS score,
                row_number() OVER (
                  PARTITION BY pa.person_id
                  ORDER BY sum(COALESCE(pc.score, 0.5)) DESC
                ) AS rn
              FROM publication_authors pa
              JOIN publication_concepts pc ON pc.publication_id = pa.publication_id
              WHERE 1=1 {person_filter}
              GROUP BY pa.person_id, pc.concept_id
            ) ranked
            WHERE rn <= :max_rank
            ON CONFLICT (person_id, concept_id) DO UPDATE
              SET score = EXCLUDED.score,
                  rank = EXCLUDED.rank
            """
        ),
        params,
    )
    return int(result.rowcount or 0)


def run(
    *,
    dry_run: bool = False,
    verbose: bool = False,
    person_id: int | None = None,
    rebuild_projection: bool = False,
    skip_publications: bool = False,
    limit: int | None = None,
    berkeley_only: bool = True,
) -> None:
    client = OpenAlexClient()
    scope = "Berkeley" if berkeley_only else "all universities"
    with _SessionLocal() as session:
        pub_stats: dict[str, int] = {}
        if not skip_publications:
            print(f"Backfilling publication concepts ({scope}) …")
            pub_stats = backfill_publication_concepts(
                session,
                client,
                dry_run=dry_run,
                verbose=verbose,
                limit=limit,
                berkeley_only=berkeley_only,
            )

        if dry_run:
            session.rollback()
            person_rows = 0
        else:
            person_rows = refresh_person_concepts(
                session, person_id=person_id, berkeley_only=berkeley_only
            )
            session.commit()

        print("=== concept backfill summary ===")
        for key, val in sorted(pub_stats.items()):
            print(f"  {key:.<28s} {val}")
        print(f"  person_concept_rows_written... {person_rows}")

        if dry_run:
            print("Dry run — no person_concepts written.")

    if rebuild_projection and not dry_run:
        print("Rebuilding projection …")
        subprocess.run(
            [sys.executable, "-m", "scripts.embed.build_projection"],
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--person-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--skip-publications",
        action="store_true",
        help="Only rebuild person_concepts from existing publication_concepts.",
    )
    parser.add_argument(
        "--rebuild-projection",
        action="store_true",
        help="Run scripts.embed.build_projection after commit.",
    )
    parser.add_argument(
        "--all-universities",
        action="store_true",
        help="Include Stanford etc.; default is Berkeley-anchored roster only.",
    )
    args = parser.parse_args(argv)
    run(
        dry_run=args.dry_run,
        verbose=args.verbose,
        person_id=args.person_id,
        rebuild_projection=args.rebuild_projection,
        skip_publications=args.skip_publications,
        limit=args.limit,
        berkeley_only=not args.all_universities,
    )


if __name__ == "__main__":
    main()
