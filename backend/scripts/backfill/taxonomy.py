"""Bulk-download the complete OpenAlex taxonomy (topics, optionally concepts).

OpenAlex publishes its full hierarchical taxonomy through cursor-paginated
endpoints — no per-work fetching needed:

  * ``/topics``   — ~4,500 topics, each with subfield/field/domain lineage.
  * ``/concepts`` — ~65,000 concepts (levels 0–4).  Gated behind
    ``--concepts``: it is ~325 pages keyless and mostly useful once the works
    pipeline actually references concepts.

Hierarchy note: OpenAlex stopped returning the concept parent tree — the
``ancestors`` field is null/empty on every concept in current responses (list
and single endpoints, levels 0–4; verified 2026-08), and there is no
``parent_id`` field anymore.  Concepts are therefore stored flat with their
``level``; ``concepts.parent_id`` stays unused (nothing in the app consumes
it).  The three-level *topics* hierarchy (domain → field → subfield → topic)
is unaffected and is what the atlas topic view uses.

Everything upserts idempotently (``ON CONFLICT``), so re-running picks up
taxonomy changes.  Uses the same polite-pool client as the other backfills
(rate-limited, backoff on 429/network errors, contact User-Agent).

Compliance notes (OpenAlex API terms):
  * Taxonomy data is CC0 — free to store and display; the app shows
    "Publication data from OpenAlex" attribution on publication surfaces.
  * The polite pool asks for a contact email in the User-Agent: set
    ``OPENALEX_CONTACT_EMAIL`` (or ``OPENALEX_API_KEY``) in ``.env``.  The
    keyless tier is rate-limited to ~3 requests/second, which this client
    respects.
  * Only the public taxonomy metadata is fetched and stored — no personal
    data, no per-work content.

Usage::

    python3 -m scripts.backfill.taxonomy             # topics (fast)
    python3 -m scripts.backfill.taxonomy --concepts  # + the 65k concept tree
    python3 -m scripts.backfill.taxonomy --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

from scripts.backfill.openalex import OpenAlexClient, short_id

PER_PAGE = 200
# Batches are executed with executemany: on Neon (or any remote Postgres) a
# row-by-row loop costs one round trip per row (~60-100 ms each).  Batching
# drops that to a handful of round trips for the whole run.
COMMIT_EVERY = 500


def _paginate(client: OpenAlexClient, path: str, per_page: int) -> Iterator[dict[str, Any]]:
    """Yield result dicts from a cursor-paginated OpenAlex collection."""
    cursor = "*"
    while cursor:
        data = client.get_json(path, params={"per-page": str(per_page), "cursor": cursor})
        yield from data.get("results") or []
        cursor = data.get("meta", {}).get("next_cursor")


_TOPIC_UPSERT = text(
    """
    INSERT INTO topics
      (openalex_topic_id, display_name, subfield_name, field_name, domain_name, level)
    VALUES (:tid, :name, :sub, :field, :domain, 3)
    ON CONFLICT (openalex_topic_id) DO UPDATE SET
      display_name  = EXCLUDED.display_name,
      subfield_name = EXCLUDED.subfield_name,
      field_name    = EXCLUDED.field_name,
      domain_name   = EXCLUDED.domain_name
    """
)


def upsert_topics(session: Session, client: OpenAlexClient, dry_run: bool) -> int:
    count = 0
    batch: list[dict[str, Any]] = []
    for t in _paginate(client, "/topics", PER_PAGE):
        tid = short_id(t.get("id"))
        if not tid:
            continue
        sub = t.get("subfield") or {}
        field = t.get("field") or {}
        domain = t.get("domain") or {}
        count += 1
        if dry_run:
            continue
        batch.append(
            {
                "tid": tid,
                "name": t.get("display_name") or "",
                "sub": (sub.get("display_name") or "").strip() or None,
                "field": (field.get("display_name") or "").strip() or None,
                "domain": (domain.get("display_name") or "").strip() or None,
            }
        )
        if len(batch) >= COMMIT_EVERY:
            session.execute(_TOPIC_UPSERT, batch)
            session.commit()
            batch = []
            print(f"  ...{count} topics")
    if batch:
        session.execute(_TOPIC_UPSERT, batch)
        session.commit()
    return count


_CONCEPT_UPSERT = text(
    """
    INSERT INTO concepts (display_name, level, openalex_id, parent_openalex_id)
    VALUES (:name, :level, :cid, :parent)
    ON CONFLICT (openalex_id) DO UPDATE SET
      display_name       = EXCLUDED.display_name,
      level              = EXCLUDED.level,
      parent_openalex_id = EXCLUDED.parent_openalex_id
    """
)

_CONCEPT_ADOPT = text(
    """
    UPDATE concepts
    SET openalex_id = :cid, level = :level, parent_openalex_id = :parent
    WHERE display_name = :name AND openalex_id IS NULL
    """
)


def _flush_concept_batch(session: Session, batch: list[dict[str, Any]]) -> None:
    """Insert one batch via executemany; on a display_name collision with a
    legacy seed row, retry row-by-row and adopt each collision by linking its
    openalex id instead of duplicating the concept."""
    try:
        session.execute(_CONCEPT_UPSERT, batch)
        session.commit()
    except IntegrityError:
        session.rollback()
        for row in batch:
            try:
                session.execute(_CONCEPT_UPSERT, row)
            except IntegrityError:
                session.rollback()
                session.execute(_CONCEPT_ADOPT, row)
        session.commit()


def upsert_concepts(session: Session, client: OpenAlexClient, dry_run: bool) -> int:
    """Insert the full /concepts tree (flat — see module docstring for why the
    parent links can't be filled: the API no longer returns them)."""
    count = 0
    batch: list[dict[str, Any]] = []
    for c in _paginate(client, "/concepts", PER_PAGE):
        cid = short_id(c.get("id"))
        if not cid:
            continue
        count += 1
        if dry_run:
            continue
        batch.append(
            {
                "name": c.get("display_name") or "",
                "level": int(c.get("level") or 0),
                "cid": cid,
                # parent_id was removed from the API — always None.  Kept in
                # the row so the column stays fillable if it ever returns.
                "parent": None,
            }
        )
        if len(batch) >= COMMIT_EVERY:
            _flush_concept_batch(session, batch)
            batch = []
            print(f"  ...{count} concepts")
    if batch:
        _flush_concept_batch(session, batch)
    if not dry_run:
        print("  concepts stored flat (API no longer returns the parent hierarchy)")
    return count


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--concepts",
        action="store_true",
        help="Also bulk-fetch the full /concepts tree (~65k rows; slow keyless).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    client = OpenAlexClient()
    with _SessionLocal() as session:
        print("Fetching /topics …")
        topics = upsert_topics(session, client, dry_run=args.dry_run)
        print(f"topics: {topics}" + (" (dry run)" if args.dry_run else ""))

        if args.concepts:
            print("Fetching /concepts …")
            concepts = upsert_concepts(session, client, dry_run=args.dry_run)
            print(f"concepts: {concepts}" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
