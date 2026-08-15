"""Backfill OpenAlex topics onto publications and people.

Pipeline:
  1. For each publication with an OpenAlex external id, fetch the work and
     write its ``topics`` array (plus primary flag) into ``publication_topics``.
  2. Upsert topic lineage rows (subfield/field/domain names) into ``topics``.
  3. Aggregate each person's works into ``person_topics``.

The OpenAlex client works keyless (anonymous tier) or with ``OPENALEX_API_KEY`` in ``.env``. Mirror ``concepts.py`` conventions.

    .venv/bin/python -m scripts.backfill.topics [--limit N] [--dry]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

from scripts.backfill.openalex import OpenAlexClient, short_id

# OpenAlex topic scores on works are 0-100; we store 0-1.
MIN_TOPIC_SCORE = 5.0
MAX_TOPICS_PER_WORK = 12


def _normalize_score(raw: Any) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val > 1.0:
        val /= 100.0
    return max(0.0, min(1.0, val))


_TOPIC_UPSERT = text(
    """
    INSERT INTO topics (openalex_topic_id, display_name, subfield_name, field_name, domain_name)
    VALUES (:tid, :name, :sub, :field, :domain)
    ON CONFLICT (openalex_topic_id) DO UPDATE SET
      display_name = EXCLUDED.display_name,
      subfield_name = EXCLUDED.subfield_name,
      field_name = EXCLUDED.field_name,
      domain_name = EXCLUDED.domain_name
    """
)

_PUBLICATION_TOPIC_UPSERT = text(
    """
    INSERT INTO publication_topics (publication_id, topic_id, score, is_primary)
    VALUES (:pid, :tid, :score, :is_primary)
    ON CONFLICT DO NOTHING
    """
)


def link_publication_topics(
    session: Session,
    publication_id: int,
    work: dict[str, Any],
) -> int:
    """Write topics for one work; return how many were linked.

    Both writes are batched per work (executemany) — on a remote Postgres
    (Neon) a row-per-statement loop costs one round trip per topic.
    """
    raw = work.get("topics") or []
    if not raw and work.get("primary_topic"):
        raw = [work["primary_topic"]]
    primary_id = short_id((work.get("primary_topic") or {}).get("id"))
    topic_rows: list[dict[str, Any]] = []
    link_rows: list[dict[str, Any]] = []
    for t in raw[:MAX_TOPICS_PER_WORK]:
        score = _normalize_score(t.get("score"))
        if score < MIN_TOPIC_SCORE / 100.0:
            continue
        tid = short_id(t.get("id"))
        if not tid:
            continue
        sub = t.get("subfield") or {}
        field = t.get("field") or {}
        domain = t.get("domain") or {}
        topic_rows.append(
            {
                "tid": tid,
                "name": (t.get("display_name") or "").strip(),
                "sub": (sub.get("display_name") or "").strip() or None,
                "field": (field.get("display_name") or "").strip() or None,
                "domain": (domain.get("display_name") or "").strip() or None,
            }
        )
        link_rows.append(
            {
                "pid": publication_id,
                "tid": tid,
                "score": score,
                "is_primary": tid == primary_id,
            }
        )
    if topic_rows:
        session.execute(_TOPIC_UPSERT, topic_rows)
        session.execute(_PUBLICATION_TOPIC_UPSERT, link_rows)
    return len(link_rows)


def fetch_and_link(
    session: Session,
    client: OpenAlexClient,
    publication_id: int,
    work_id: str,
) -> int:
    """Fetch a work by its OpenAlex id and link its topics (idempotent)."""
    existing = session.execute(
        text(
            "SELECT count(*) FROM publication_topics WHERE publication_id = :pid"
        ),
        {"pid": publication_id},
    ).scalar()
    if existing:
        return 0
    work = client.get_json(f"/works/{work_id}")
    return link_publication_topics(session, publication_id, work)


def rebuild_person_topics(session: Session) -> None:
    """Aggregate publication topics into per-person profiles (idempotent rebuild)."""
    session.execute(text("DELETE FROM person_topics"))
    session.execute(
        text(
            """
            INSERT INTO person_topics (person_id, topic_id, score, works_count)
            SELECT
              pa.person_id,
              pt.topic_id,
              avg(coalesce(pt.score, 0.5))::real,
              count(DISTINCT pa.publication_id)::int
            FROM publication_authors pa
            JOIN publication_topics pt ON pt.publication_id = pa.publication_id
            GROUP BY pa.person_id, pt.topic_id
            """
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="max works to process (0 = all)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    client = OpenAlexClient()
    with _SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT p.id, e.external_id
                FROM publications p
                JOIN external_identifiers e
                  ON e.publication_id = p.id AND e.provider = 'openalex'
                ORDER BY p.id
                """
            )
        ).all()
        if args.limit:
            rows = rows[: args.limit]
        print(f"[topics] {len(rows)} publications to process")
        linked = 0
        for done, (pub_id, work_id) in enumerate(rows, start=1):
            linked += fetch_and_link(session, client, int(pub_id), str(work_id))
            if done % 50 == 0:
                print(f"[topics] {done}/{len(rows)} works, {linked} topic links")
        if args.dry:
            print("[topics] --dry: skipping person aggregation and commit")
            return
        rebuild_person_topics(session)
        session.commit()
        print("[topics] done — person_topics rebuilt")


if __name__ == "__main__":
    main()
