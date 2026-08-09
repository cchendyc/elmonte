from __future__ import annotations

"""Projection (atlas) data access: active run + on-map coauthor ties."""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.id_codec import encode

def _active_projection_run(session: Session) -> dict[str, Any] | None:
    return session.execute(
        text(
            """
            SELECT id, algorithm, point_count
            FROM embedding_runs
            WHERE is_active
            """
        )
    ).mappings().first()
def _coauthor_ties_on_map(
    session: Session,
    person_id: int,
    run_id: int,
    view: str = "topic",
) -> list[dict[str, Any]]:
    """Coauthor pairs where the other person is on the active projection.

    ``person_projections_2d`` has one row per (run, person, view), so the
    join MUST filter by view — without it every edge is duplicated once per
    view, which breaks the frontend's per-person React keys and drops edges.
    """
    rows = session.execute(
        text(
            """
            SELECT
              CASE
                WHEN e.person_a = :pid THEN e.person_b
                ELSE e.person_a
              END AS other_id,
              e.paper_count
            FROM person_coauthor_edges e
            JOIN person_projections_2d p
              ON p.person_id = CASE
                WHEN e.person_a = :pid THEN e.person_b
                ELSE e.person_a
              END
             AND p.run_id = :run_id
             AND p.view = :view
            WHERE e.person_a = :pid OR e.person_b = :pid
            ORDER BY e.paper_count DESC
            """
        ),
        {"pid": person_id, "run_id": run_id, "view": view},
    ).mappings().all()
    return [dict(r) for r in rows]
