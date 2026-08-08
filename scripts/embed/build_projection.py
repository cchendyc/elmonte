"""Build the 2D projection served to the scatter canvas.

Signal model — people are positioned by *adjacency*, not by institution
membership. A weighted person–person graph is built from every direct tie
we know about:

    coauthor pair-weight  (primary — cross-institution collaboration)
    advised_by            (strong mentor/mentee link)
    overlapping affiliation at the same org (shared experience)
    shared research concepts (topic similarity)

The graph is sparsified to each person's top-32 neighbours (kNN). Coordinates
come from classical MDS on shortest-path distances in that graph, followed
by collision resolution so points don't stack on top of each other.

Org/school is deliberately *not* a global clustering feature — two
coauthors at Berkeley and Chicago should land near each other because of
their paper edge, not be pulled apart by separate school buckets.

Isolated people (no ties at all) are placed on a deterministic ring at
the cloud periphery so they don't collapse to the origin.

Run:

    .venv/bin/python -m scripts.embed.build_projection

Options:

    --k       number of eigen components to compute (default 10)
    --knn     per-person neighbor cap for graph sparsification (default 32)
    --dry     print stats without writing to the DB
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.sparse import coo_matrix, csgraph
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import _SessionLocal


ALGORITHM = "graph_mds_v2"
KIND = "person_graph"
COAUTHOR_WEIGHT_CAP = 20.0
ADVISOR_WEIGHT = 5.0
AFFILIATION_OVERLAP_WEIGHT = 2.0
CONCEPT_WEIGHT = 1.5
# Weak same-lab tie added only for otherwise-isolated people.
SAME_ANCHOR_ORG_WEIGHT = 0.35
CANVAS_SPAN = 2.0
DEFAULT_KNN = 32
MIN_POINT_SEP = 0.065
COLLISION_ITERATIONS = 120
RNG_SEED = 0xC0FFEE


# ---------------------------------------------------------------------------
# Signal loading — person–person weighted edges
# ---------------------------------------------------------------------------


def load_person_edges(session: Session) -> tuple[list[int], list[tuple[int, int, float]]]:
    """Return `(all_people, edges)` where each edge is `(a, b, weight)` with
    `a < b` canonical ordering not required (we symmetrise downstream)."""
    people_rows = session.execute(text("SELECT id FROM people ORDER BY id")).all()
    people = [int(r[0]) for r in people_rows]
    people_set = set(people)

    edges: list[tuple[int, int, float]] = []

    def add(a: int, b: int, w: float) -> None:
        if a == b or w <= 0:
            return
        if a not in people_set or b not in people_set:
            return
        edges.append((a, b, w))

    coauthor_rows = session.execute(
        text(
            """
            SELECT person_a, person_b, paper_count
            FROM person_coauthor_edges
            """
        )
    ).all()
    for a, b, count in coauthor_rows:
        add(int(a), int(b), min(float(count), COAUTHOR_WEIGHT_CAP))

    advisor_rows = session.execute(
        text(
            """
            SELECT from_person_id, to_person_id
            FROM person_relationships
            WHERE type = 'advised_by'
            """
        )
    ).all()
    for a, b in advisor_rows:
        add(int(a), int(b), ADVISOR_WEIGHT)

    overlap_rows = session.execute(
        text(
            """
            SELECT
              pa1.person_id,
              pa2.person_id,
              count(DISTINCT aoa1.organization_id)::float AS shared_orgs
            FROM person_affiliations pa1
            JOIN affiliation_org_assignments aoa1
              ON aoa1.affiliation_id = pa1.id
            JOIN person_affiliations pa2
              ON pa2.person_id > pa1.person_id
            JOIN affiliation_org_assignments aoa2
              ON aoa2.affiliation_id = pa2.id
             AND aoa2.organization_id = aoa1.organization_id
            WHERE pa1.validity && pa2.validity
            GROUP BY pa1.person_id, pa2.person_id
            HAVING count(DISTINCT aoa1.organization_id) <= 2
            """
        )
    ).all()
    for a, b, shared in overlap_rows:
        add(int(a), int(b), float(shared) * AFFILIATION_OVERLAP_WEIGHT * 0.5)

    concept_rows = session.execute(
        text(
            """
            SELECT
              pc1.person_id,
              pc2.person_id,
              sum(
                COALESCE(pc1.score, 0.5) * COALESCE(pc2.score, 0.5)
              )::float AS concept_sim
            FROM person_concepts pc1
            JOIN person_concepts pc2
              ON pc1.concept_id = pc2.concept_id
             AND pc1.person_id < pc2.person_id
            GROUP BY pc1.person_id, pc2.person_id
            """
        )
    ).all()
    for a, b, sim in concept_rows:
        add(int(a), int(b), min(float(sim), 3.0) * CONCEPT_WEIGHT * 0.4)

    return people, edges


def load_anchor_fallback_edges(session: Session) -> list[tuple[int, int, float]]:
    """Same-lab weak ties — applied only after kNN sparsification for nodes
    that would otherwise be disconnected."""
    rows = session.execute(
        text(
            """
            SELECT pa1.person_id, pa2.person_id
            FROM person_anchor pa1
            JOIN person_anchor pa2
              ON pa1.organization_id = pa2.organization_id
             AND pa1.person_id < pa2.person_id
            WHERE pa1.validity @> CURRENT_DATE
              AND pa2.validity @> CURRENT_DATE
            """
        )
    ).all()
    return [(int(a), int(b), SAME_ANCHOR_ORG_WEIGHT) for a, b in rows]


# ---------------------------------------------------------------------------
# Graph construction + Laplacian eigenmap
# ---------------------------------------------------------------------------


def _sum_edges(
    edges: list[tuple[int, int, float]],
    people: list[int],
) -> dict[tuple[int, int], float]:
    """Collapse parallel edges and map person ids → matrix indices."""
    p_idx = {pid: i for i, pid in enumerate(people)}
    summed: dict[tuple[int, int], float] = {}
    for a, b, w in edges:
        if a not in p_idx or b not in p_idx:
            continue
        i, j = p_idx[a], p_idx[b]
        if i == j:
            continue
        key = (min(i, j), max(i, j))
        summed[key] = summed.get(key, 0.0) + w
    return summed


def _knn_sparsify(
    edges: dict[tuple[int, int], float],
    n: int,
    k: int,
) -> dict[tuple[int, int], float]:
    """Keep at most *k* highest-weight neighbors per node."""
    if k <= 0 or k >= n:
        return edges

    neighbors: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for (i, j), w in edges.items():
        neighbors[i].append((j, w))
        neighbors[j].append((i, w))

    keep: set[tuple[int, int]] = set()
    for i in range(n):
        for j, _w in sorted(neighbors[i], key=lambda t: -t[1])[:k]:
            keep.add((min(i, j), max(i, j)))

    return {key: edges[key] for key in keep if key in edges}


def _attach_isolated(
    graph: dict[tuple[int, int], float],
    fallback_edges: list[tuple[int, int, float]],
    people: list[int],
    n: int,
) -> dict[tuple[int, int], float]:
    """Add weak same-lab ties only for nodes with no neighbours yet."""
    p_idx = {pid: i for i, pid in enumerate(people)}
    degree = np.zeros(n, dtype=np.int32)
    for i, j in graph:
        degree[i] += 1
        degree[j] += 1

    out = dict(graph)
    for a, b, w in fallback_edges:
        if a not in p_idx or b not in p_idx:
            continue
        i, j = p_idx[a], p_idx[b]
        if degree[i] == 0 or degree[j] == 0:
            key = (min(i, j), max(i, j))
            out[key] = out.get(key, 0.0) + w
            degree[i] += 1
            degree[j] += 1
    return out


def _resolve_collisions(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Push overlapping points apart until everyone has breathing room."""
    pos = np.column_stack([x, y]).copy()
    n = pos.shape[0]
    min_sep = MIN_POINT_SEP
    rng = np.random.default_rng(RNG_SEED)

    for _ in range(COLLISION_ITERATIONS):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                diff = pos[i] - pos[j]
                d = float(np.linalg.norm(diff))
                if d >= min_sep:
                    continue
                moved = True
                if d < 1e-8:
                    diff = rng.normal(size=2)
                    d = float(np.linalg.norm(diff)) + 1e-8
                unit = diff / d
                push = (min_sep - d) * unit
                pos[i] += push
                pos[j] -= push
        if not moved:
            break

    return pos[:, 0], pos[:, 1]


def _graph_to_coords(
    graph: dict[tuple[int, int], float],
    n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Classical MDS on shortest-path distances in the kNN graph."""
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for (i, j), w in graph.items():
        # Strong ties → short hops; weak ties stay reachable but distant.
        cost = 1.0 / (w + 0.25)
        rows.extend([i, j])
        cols.extend([j, i])
        data.extend([cost, cost])
    W = coo_matrix((data, (rows, cols)), shape=(n, n), dtype=np.float64).tocsr()
    dist = csgraph.shortest_path(W, directed=False, unweighted=False)
    dist[~np.isfinite(dist)] = float(np.nanmax(dist[np.isfinite(dist)]) * 1.5)

    d2 = dist**2
    H = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * H @ d2 @ H
    vals, vecs = np.linalg.eigh(B)
    order = np.argsort(vals)[::-1]
    vals = vals[order]
    vecs = vecs[:, order]
    keep = np.where(vals > 1e-9)[0][:2]
    if len(keep) < 2:
        return _fallback_ring(n)
    coords = vecs[:, keep] * np.sqrt(vals[keep])
    return _rescale(coords[:, 0]), _rescale(coords[:, 1])


def laplacian_eigenmap_projection(
    people: list[int],
    edges: list[tuple[int, int, float]],
    fallback_edges: list[tuple[int, int, float]],
    k: int,
    knn_k: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Normalized Laplacian eigenmap of the person adjacency graph."""
    n = len(people)
    if n < 2:
        raise ValueError(f"need >= 2 people, got {n}")

    summed = _sum_edges(edges, people)
    if not summed:
        return _fallback_ring(n)

    summed = _knn_sparsify(summed, n, knn_k)
    summed = _attach_isolated(summed, fallback_edges, people, n)

    if not summed:
        return _fallback_ring(n)

    x, y = _graph_to_coords(summed, n)
    x, y = _resolve_collisions(x, y)
    x = _rescale(x)
    y = _rescale(y)
    return x, y


def _fallback_ring(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic ring for degenerate graphs."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return _rescale(np.cos(t)), _rescale(np.sin(t))


def _place_isolated(
    x: np.ndarray, y: np.ndarray, isolated: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Push zero-degree nodes to a peripheral ring so they don't sit on
    top of the connected core."""
    if not np.any(isolated):
        return x, y
    connected = ~isolated
    if not np.any(connected):
        return _fallback_ring(len(x))

    cx = float(np.mean(x[connected]))
    cy = float(np.mean(y[connected]))
    span = max(float(np.max(np.abs(x[connected]))), float(np.max(np.abs(y[connected]))), 0.5)
    ring_r = span * 1.35

    idx = np.flatnonzero(isolated)
    t = np.linspace(0, 2 * np.pi, len(idx), endpoint=False)
    x = x.copy()
    y = y.copy()
    x[idx] = cx + ring_r * np.cos(t)
    y[idx] = cy + ring_r * np.sin(t)
    return x, y


def _rescale(v: np.ndarray) -> np.ndarray:
    v = v - float(np.mean(v))
    span = float(np.max(np.abs(v)))
    if span == 0:
        return v
    return v * (CANVAS_SPAN / (2 * span))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def write_run(
    session: Session,
    people: list[int],
    x: np.ndarray,
    y: np.ndarray,
    raw_dim: int,
) -> int:
    run_id = int(
        session.execute(
            text(
                """
                INSERT INTO embedding_runs
                    (kind, algorithm, raw_dim, point_count, is_active, notes)
                VALUES
                    (:kind, :algorithm, :raw_dim, :count, FALSE, NULL)
                RETURNING id
                """
            ),
            {
                "kind": KIND,
                "algorithm": ALGORITHM,
                "raw_dim": raw_dim,
                "count": len(people),
            },
        ).scalar_one()
    )

    rows = [
        {"run_id": run_id, "person_id": pid, "x": float(x[i]), "y": float(y[i])}
        for i, pid in enumerate(people)
        if np.isfinite(x[i]) and np.isfinite(y[i])
    ]
    if rows:
        session.execute(
            text(
                """
                INSERT INTO person_projections_2d (run_id, person_id, x, y)
                VALUES (:run_id, :person_id, :x, :y)
                """
            ),
            rows,
        )

    session.execute(
        text(
            "UPDATE embedding_runs SET is_active = FALSE "
            "WHERE is_active AND id <> :new_id"
        ),
        {"new_id": run_id},
    )
    session.execute(
        text("UPDATE embedding_runs SET is_active = TRUE WHERE id = :new_id"),
        {"new_id": run_id},
    )
    session.commit()
    return run_id


def projection_display_edges(
    session: Session,
    person_ids: list[int],
    knn: int = DEFAULT_KNN,
) -> list[tuple[int, int, float]]:
    """kNN-sparsified person graph for the scatter canvas — same signals as layout."""
    if len(person_ids) < 2:
        return []

    person_set = set(person_ids)
    all_people, raw_edges = load_person_edges(session)
    people = [pid for pid in all_people if pid in person_set]
    if len(people) < 2:
        return []

    filtered = [
        (a, b, w) for a, b, w in raw_edges if a in person_set and b in person_set
    ]
    summed = _sum_edges(filtered, people)
    sparse = _knn_sparsify(summed, len(people), knn)
    fallback = [
        (a, b, w)
        for a, b, w in load_anchor_fallback_edges(session)
        if a in person_set and b in person_set
    ]
    graph = _attach_isolated(sparse, fallback, people, len(people))
    idx_to_pid = {i: pid for i, pid in enumerate(people)}
    return [(idx_to_pid[i], idx_to_pid[j], w) for (i, j), w in graph.items()]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=10, help="eigenvectors to compute")
    parser.add_argument(
        "--knn",
        type=int,
        default=DEFAULT_KNN,
        help="per-person neighbor cap (0 = no sparsification)",
    )
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    started = time.time()
    with _SessionLocal() as session:
        people, edges = load_person_edges(session)
        fallback_edges = load_anchor_fallback_edges(session)
        print(
            f"[embed] {len(edges)} person edges, {len(people)} people, "
            f"knn={args.knn}"
        )
        if len(people) < 2:
            print("[embed] too few people — exiting")
            return
        x, y = laplacian_eigenmap_projection(
            people, edges, fallback_edges, k=args.k, knn_k=args.knn
        )
        finite = int(np.sum(np.isfinite(x) & np.isfinite(y)))
        print(
            f"[embed] graph MDS ok — {finite}/{len(people)} finite in "
            f"{time.time() - started:.1f}s"
        )
        if args.dry:
            print("[embed] --dry: skipping DB writes")
            return
        run_id = write_run(session, people, x, y, raw_dim=args.k)
        print(f"[embed] wrote run_id={run_id}, is_active=TRUE")


if __name__ == "__main__":
    main()
