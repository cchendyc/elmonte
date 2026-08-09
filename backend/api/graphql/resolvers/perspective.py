from __future__ import annotations

"""perspective field resolver."""

from datetime import date
from typing import Any

from sqlalchemy import text

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.repositories.people import _person_briefs, _person_relations_all
from api.services.graph import _alter_groups
from api.services.names import _full_name

PERSPECTIVE_ALTER_LIMIT = 40

@query.field("perspective")
def resolve_perspective(_obj, info, personId: str) -> dict[str, Any]:
    row_id = _decode_id(personId, "person")
    session = _session(info)
    on = date.today()
    total = int(
        session.execute(
            text("SELECT count(*) FROM publication_authors WHERE person_id = :pid"),
            {"pid": row_id},
        ).scalar_one()
    )
    rows = session.execute(
        text(
            """
            SELECT CASE WHEN e.person_a = :pid THEN e.person_b ELSE e.person_a END AS other_id,
                   e.paper_count
            FROM person_coauthor_edges e
            WHERE e.person_a = :pid OR e.person_b = :pid
            """
        ),
        {"pid": row_id},
    ).all()
    alters: dict[int, dict[str, Any]] = {}
    for other_id, count in rows:
        other_id = int(other_id)
        s = float(count) / total if total else 0.0
        alters[other_id] = {
            "personId": encode("person", other_id),
            "paperCount": int(count),
            "importance": min(1.0, s),
            "group": 0,
            "hop": 1,
            "relation": "coauthor",
        }
    for rel in _person_relations_all(session, row_id, "advised_by"):
        other = int(rel.to_person_id if rel.from_person_id == row_id else rel.from_person_id)
        entry = alters.setdefault(
            other,
            {"personId": encode("person", other), "paperCount": 0, "importance": 0.0,
             "group": 0, "hop": 1, "relation": None},
        )
        entry["relation"] = "advisor" if rel.from_person_id == row_id else "advisee"
        entry["importance"] = max(entry["importance"], 0.55)

    # hop-2: shared-coauthor-only alters (≥2 shared coauthors, not direct).
    rows2 = session.execute(
        text(
            """
            WITH me AS (
              SELECT CASE WHEN person_a = :pid THEN person_b ELSE person_a END AS other
              FROM person_coauthor_edges
              WHERE person_a = :pid OR person_b = :pid
            ),
            shared AS (
              SELECT e.person_a AS a, e.person_b AS b
              FROM person_coauthor_edges e
              JOIN me ma ON ma.other = e.person_a
              JOIN me mb ON mb.other = e.person_b
              WHERE e.person_a <> :pid AND e.person_b <> :pid
            )
            SELECT other_id, count(*) AS shared_count
            FROM (
              SELECT CASE WHEN a = :pid THEN b ELSE a END AS other_id FROM shared
              UNION ALL
              SELECT CASE WHEN b = :pid THEN a ELSE b END FROM shared
            ) x
            WHERE other_id <> :pid
            GROUP BY other_id
            HAVING count(*) >= 2
            """
        ),
        {"pid": row_id},
    ).all()
    for other_id, shared_count in rows2:
        other_id = int(other_id)
        if other_id in alters:
            continue
        s = min(0.35 * (float(shared_count) / 10.0), 0.35)
        alters[other_id] = {
            "personId": encode("person", other_id),
            "paperCount": 0,
            "importance": s,
            "group": 0,
            "hop": 2,
            "relation": None,
        }

    ordered = sorted(alters.values(), key=lambda a: -a["importance"])[:PERSPECTIVE_ALTER_LIMIT]
    alter_ids = [_decode_id(a["personId"], "person") for a in ordered]
    groups = _alter_groups(session, alter_ids)
    # Batch briefs: up to 40 alters in ~4 queries instead of ~120.
    briefs = _person_briefs(session, alter_ids, on)
    for a, pid in zip(ordered, alter_ids):
        a["group"] = groups.get(pid, 0)
        brief = briefs.get(pid)
        if brief:
            person = brief["person"]
            a["label"] = _full_name(person.firstname, person.middlename, person.lastname)
            a["institution"] = brief["institution"]
            a["rank"] = brief["rank"]
        else:
            a["label"] = "?"
    max_paper = max((a["paperCount"] or 0) for a in ordered) if ordered else 0
    return {
        "focusId": personId,
        "alterCount": len(ordered),
        "maxPaperCount": max_paper,
        "alters": ordered,
    }
