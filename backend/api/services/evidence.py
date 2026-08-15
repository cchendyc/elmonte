from __future__ import annotations

"""Evidence-source access shared by profile/export resolvers.

Every supported fact table stores its provenance in ``evidence``.  The
resolvers expose a small, stable shape — label + source URL + source kind —
so the UI can render the evidence-first promise instead of merely asserting
it in copy.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Whitelist of evidence subject columns.  This module builds SQL from these
# names, so it must never be sourced from request input.
EVIDENCE_SUBJECT_COLUMNS = (
    "affiliation_id",
    "person_relationship_id",
    "org_relationship_id",
    "person_award_id",
    "grant_id",
    "pub_author_affiliation_id",
    "person_id",
    "organization_id",
)


def evidence_sources(
    session: Session,
    *,
    subject_column: str,
    subject_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Return ``{subject_id: [source, ...]}`` for one evidence subject column.

    Rows are ordered by snapshot recency (newest first), then snapshot id for
    deterministic output.  A missing or deleted source_snapshot is represented
    as ``[]`` — the fact can still be shown without its provenance detail.
    """
    if subject_column not in EVIDENCE_SUBJECT_COLUMNS:
        raise ValueError(f"unknown evidence subject column: {subject_column!r}")
    ids = list(dict.fromkeys(int(i) for i in subject_ids if i is not None))
    if not ids:
        return {}

    rows = session.execute(
        text(
            f"""
            SELECT
              e.{subject_column} AS subject_id,
              e.label,
              s.source_url,
              s.source_kind
            FROM evidence e
            JOIN source_snapshots s ON s.id = e.snapshot_id
            WHERE e.{subject_column} = ANY(:ids)
            ORDER BY e.{subject_column}, s.fetched_at DESC NULLS LAST, s.id DESC
            """
        ),
        {"ids": ids},
    ).mappings().all()

    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        subject_id = int(row["subject_id"])
        result.setdefault(subject_id, []).append(
            {
                "label": row["label"],
                "url": row["source_url"],
                "sourceKind": row["source_kind"],
            }
        )
    return result
