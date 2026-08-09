"""Debug why three people land close on the scatter map."""

from __future__ import annotations

import numpy as np
from sqlalchemy import text

from api.deps import _SessionLocal
from scripts.embed.researcher_similarity import load_similarity_blocks

TARGETS = ("Livdan", "Kermani", "Lettau")


def main() -> None:
    session = _SessionLocal()
    rows = session.execute(
        text(
            """
            SELECT id, firstname, lastname
            FROM people
            WHERE lastname IN ('Livdan', 'Kermani', 'Lettau')
            ORDER BY lastname, firstname
            """
        )
    ).all()
    if not rows:
        print("No matching people in DB")
        return

    people = [int(r[0]) for r in rows]
    labels = {int(r[0]): f"{r[1]} {r[2]}" for r in rows}
    idx = {pid: i for i, pid in enumerate(people)}

    print("=== People ===")
    for pid in people:
        print(f"  {pid}: {labels[pid]}")

    print("\n=== Direct coauthor edges ===")
    for i, a in enumerate(people):
        for b in people[i + 1 :]:
            count = session.execute(
                text(
                    """
                    SELECT paper_count
                    FROM person_coauthor_edges
                    WHERE person_a = LEAST(:a, :b) AND person_b = GREATEST(:a, :b)
                    """
                ),
                {"a": a, "b": b},
            ).scalar()
            print(f"  {labels[a]} <-> {labels[b]}: {count or 0} papers")

    print("\n=== Shared coauthors (top overlap) ===")
    for i, a in enumerate(people):
        for b in people[i + 1 :]:
            shared = session.execute(
                text(
                    """
                    WITH na AS (
                      SELECT CASE WHEN person_a = :a THEN person_b ELSE person_a END AS other
                      FROM person_coauthor_edges
                      WHERE person_a = :a OR person_b = :a
                    ),
                    nb AS (
                      SELECT CASE WHEN person_a = :b THEN person_b ELSE person_a END AS other
                      FROM person_coauthor_edges
                      WHERE person_a = :b OR person_b = :b
                    )
                    SELECT count(*) FROM na JOIN nb ON na.other = nb.other
                    """
                ),
                {"a": a, "b": b},
            ).scalar()
            print(f"  {labels[a]} <-> {labels[b]}: {shared} shared coauthors")

    print("\n=== Anchors ===")
    for pid in people:
        row = session.execute(
            text(
                """
                SELECT pa.title, o.name AS org, inst.name AS university
                FROM person_anchor pa
                JOIN organizations o ON o.id = pa.organization_id
                LEFT JOIN LATERAL (
                  SELECT o2.name
                  FROM org_tree_current t
                  JOIN organizations o2 ON o2.id = ANY(t.ancestor_ids)
                  WHERE t.organization_id = pa.organization_id
                    AND o2.kind = 'university'
                  LIMIT 1
                ) inst ON TRUE
                WHERE pa.person_id = :pid
                  AND pa.validity @> CURRENT_DATE
                  AND pa.is_primary
                """
            ),
            {"pid": pid},
        ).mappings().first()
        print(f"  {labels[pid]}: {dict(row) if row else 'no anchor'}")

    # Projection positions
    print("\n=== Map positions (active run) ===")
    for pid in people:
        row = session.execute(
            text(
                """
                SELECT p.x, p.y
                FROM person_projections_2d p
                JOIN embedding_runs r ON r.id = p.run_id AND r.is_active
                WHERE p.person_id = :pid
                """
            ),
            {"pid": pid},
        ).first()
        if row:
            print(f"  {labels[pid]}: x={row[0]:.4f}, y={row[1]:.4f}")

    # Similarity blocks for all projection people
    all_rows = session.execute(
        text(
            """
            SELECT p.person_id
            FROM person_projections_2d p
            JOIN embedding_runs r ON r.id = p.run_id AND r.is_active
            ORDER BY p.person_id
            """
        )
    ).all()
    all_people = [int(r[0]) for r in all_rows]
    all_idx = {pid: i for i, pid in enumerate(all_people)}

    blocks = load_similarity_blocks(session, all_people)
    combined = blocks.combined()

    print("\n=== Similarity matrix (combined) ===")
    for i, a in enumerate(people):
        for b in people[i + 1 :]:
            ia, ib = all_idx[a], all_idx[b]
            print(f"  {labels[a]} <-> {labels[b]}:")
            print(f"    combined={combined[ia, ib]:.3f}")
            print(f"    network={blocks.network[ia, ib]:.3f}")
            print(f"    research={blocks.research[ia, ib]:.3f}")
            print(f"    career={blocks.career[ia, ib]:.3f}")
            print(f"    institution={blocks.institution[ia, ib]:.3f}")
            print(f"    department={blocks.department[ia, ib]:.3f}")

    session.close()


if __name__ == "__main__":
    main()
