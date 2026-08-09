"""Build the 2D projection served to the scatter canvas.

Stage 1 — multimodal researcher similarity (ground truth):
    R research (OpenAlex concepts; SPECTER2 when available)
    N coauthor + shared-coauthor network
    C career-path overlap
    I institution (university) overlap
    D department (anchor org) match

    S = 0.50·N + 0.31·R + 0.09·C + 0.06·I + 0.04·D
    (blocks with no real data are auto-disabled; coauthorship-only until
     concepts and career timelines are backfilled)

    D_ij = 1 - S_ij

Stage 2 — 2D layout (lossy projection of D):
    Benchmark metric MDS vs PaCMAP; pick best by neighbor recall@10 + Spearman ρ.

Run:

    .venv/bin/python -m scripts.embed.build_projection
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.sparse import coo_matrix, csgraph
from scipy.spatial.distance import squareform
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import _SessionLocal


from scripts.embed.researcher_similarity import (
    NETWORK_FLOOR,
    block_feature_matrix,
    choose_layout,
    load_similarity_blocks,
    similarity_to_distance,
)


ALGORITHM = "multimodal_v1"
KIND = "person_graph"
COAUTHOR_WEIGHT_CAP = 20.0
ADVISOR_WEIGHT = 5.0
# Shared org-years while affiliation validity ranges overlap.
CAREER_OVERLAP_WEIGHT = 2.5
CAREER_OVERLAP_YEAR_CAP = 12.0
# Same primary anchor org today (weak — coauthors at the same school stay closer).
CURRENT_DEPT_WEIGHT = 0.55
CONCEPT_WEIGHT = 1.5
CONCEPT_SIM_CAP = 3.0
# Edges at or above this weight are never dropped by kNN sparsification.
STRONG_EDGE_FLOOR = 2.0
# Weak same-lab tie added only for otherwise-isolated people (similarity groups).
SAME_ANCHOR_ORG_WEIGHT = 0.35
CANVAS_SPAN = 2.0
DEFAULT_KNN = 32
# Dissimilarity = 1 / (weight + eps) ** power — coauthor-heavy pairs land closer.
DISSIM_EPS = 0.12
DISSIM_POWER = 0.85
# Pairs with no direct tie get this high dissimilarity (92nd pct of tied pairs).
UNLINKED_PERCENTILE = 92
# Weak ties (dept, career, topic) count at this fraction in layout distance.
SOFT_LAYOUT_SCALE = 0.08
STRESS_REFINE_STEPS = 80
STRESS_LEARNING_RATE = 0.06
SPRING_ITERATIONS = 220
SPRING_ATTRACTION = 0.09
SPRING_REPULSION = 0.28
SPRING_DAMPING = 0.85
SPRING_MAX_STEP = CANVAS_SPAN * 0.012
COAUTHOR_ENFORCE_STEPS = 320
COAUTHOR_PULL = 0.38
STRANGER_REPULSE_RADIUS = CANVAS_SPAN * 0.028
SOFT_STRESS_STEPS = 40
SOFT_STRESS_SCALE = 0.12
OVERLAP_MAX_ITER = 80
OVERLAP_PUSH_FACTOR = 0.25
OVERLAP_MIN_SEP_FRACTION = 0.22


# ---------------------------------------------------------------------------
# Signal loading — person–person weighted edges
# ---------------------------------------------------------------------------


def load_person_anchors(session: Session) -> dict[int, int]:
    """Current primary anchor org per person (for orphan placement)."""
    rows = session.execute(
        text(
            """
            SELECT person_id, organization_id
            FROM person_anchor
            WHERE validity @> CURRENT_DATE
              AND is_primary
            """
        )
    ).all()
    return {int(person_id): int(org_id) for person_id, org_id in rows}


def load_person_edges(session: Session) -> tuple[list[int], list[tuple[int, int, float]]]:
    """Return `(all_people, weighted_edges)` for layout and grouping."""
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

    # Career timeline overlap: years at the same org while both affiliations active.
    career_rows = session.execute(
        text(
            """
            SELECT
              pa1.person_id,
              pa2.person_id,
              sum(
                GREATEST(
                  0,
                  upper(pa1.validity * pa2.validity)
                  - lower(pa1.validity * pa2.validity)
                )
              )::float / 365.25 AS overlap_years
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
            """
        )
    ).all()
    for a, b, years in career_rows:
        overlap = min(float(years), CAREER_OVERLAP_YEAR_CAP)
        if overlap > 0:
            add(int(a), int(b), overlap * CAREER_OVERLAP_WEIGHT * 0.12)

    # Current department: same primary anchor org today.
    dept_rows = session.execute(
        text(
            """
            SELECT pa1.person_id, pa2.person_id
            FROM person_anchor pa1
            JOIN person_anchor pa2
              ON pa1.organization_id = pa2.organization_id
             AND pa1.person_id < pa2.person_id
            WHERE pa1.validity @> CURRENT_DATE
              AND pa2.validity @> CURRENT_DATE
              AND pa1.is_primary
              AND pa2.is_primary
            """
        )
    ).all()
    for a, b in dept_rows:
        add(int(a), int(b), CURRENT_DEPT_WEIGHT)

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
        add(
            int(a),
            int(b),
            min(float(sim), CONCEPT_SIM_CAP) * CONCEPT_WEIGHT * 0.4,
        )

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
    """Keep at most *k* neighbours per node; never drop strong collaboration edges."""
    if k <= 0 or k >= n:
        return edges

    pinned = {key: w for key, w in edges.items() if w >= STRONG_EDGE_FLOOR}

    neighbors: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for (i, j), w in edges.items():
        neighbors[i].append((j, w))
        neighbors[j].append((i, w))

    keep: set[tuple[int, int]] = set(pinned)
    for i in range(n):
        for j, _w in sorted(neighbors[i], key=lambda t: -t[1])[:k]:
            keep.add((min(i, j), max(i, j)))

    return {key: edges[key] for key in keep if key in edges}


def _build_layout_graph(
    edges: list[tuple[int, int, float]],
    people: list[int],
    knn_k: int,
) -> dict[tuple[int, int], float]:
    """kNN-sparsified person graph from all layout signals."""
    n = len(people)
    graph = _sum_edges(edges, people)
    return _knn_sparsify(graph, n, knn_k)


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


def _adaptive_min_sep(pos: np.ndarray) -> float:
    """Minimum dot separation scaled to local density — not a global equalizer."""
    n = pos.shape[0]
    if n < 2:
        return CANVAS_SPAN * 0.02

    sample = min(n, 96)
    idx = np.linspace(0, n - 1, sample, dtype=int)
    nearest: list[float] = []
    for i in idx:
        diff = pos - pos[i]
        dist = np.linalg.norm(diff, axis=1)
        dist[i] = np.inf
        nearest.append(float(np.min(dist)))

    local = float(np.median(nearest)) if nearest else CANVAS_SPAN * 0.03
    density_floor = CANVAS_SPAN / max(n**0.55, 1.0) * 0.18
    return max(local * OVERLAP_MIN_SEP_FRACTION, density_floor, CANVAS_SPAN * 0.008)


def _relax_overlaps(
    x: np.ndarray,
    y: np.ndarray,
    pinned: set[tuple[int, int]] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Push apart only overlapping dots; never separate strong collaborators."""
    pos = np.column_stack([x, y]).copy()
    n = pos.shape[0]
    if n < 2:
        return x, y

    min_sep = _adaptive_min_sep(pos)

    for _ in range(OVERLAP_MAX_ITER):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                key = (i, j)
                if pinned and key in pinned:
                    continue
                diff = pos[i] - pos[j]
                d = float(np.linalg.norm(diff))
                if d >= min_sep:
                    continue
                moved = True
                if d < 1e-10:
                    rng = np.random.default_rng((i + 1) * 100_003 + (j + 1) * 1_009)
                    diff = rng.normal(size=2)
                    d = float(np.linalg.norm(diff)) + 1e-10
                unit = diff / d
                push = (min_sep - d) * unit * OVERLAP_PUSH_FACTOR
                pos[i] += push
                pos[j] -= push
        if not moved:
            break

    return pos[:, 0], pos[:, 1]


def similarity_group_count(n: int) -> int:
    """Pick a group count that is readable on the map (~1 group per 40 people)."""
    if n < 12:
        return max(2, (n + 2) // 3)
    return max(6, min(12, round(n / 40)))


def _graph_shortest_paths(
    graph: dict[tuple[int, int], float],
    n: int,
) -> np.ndarray:
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for (i, j), w in graph.items():
        cost = 1.0 / (w + 0.15) ** 1.2
        rows.extend([i, j])
        cols.extend([j, i])
        data.extend([cost, cost])
    w_mat = coo_matrix((data, (rows, cols)), shape=(n, n), dtype=np.float64).tocsr()
    dist = csgraph.shortest_path(w_mat, directed=False, unweighted=False)
    finite = dist[np.isfinite(dist)]
    if finite.size:
        dist[~np.isfinite(dist)] = float(np.nanmax(finite) * 1.5)
    return dist


def assign_similarity_groups(
    graph: dict[tuple[int, int], float],
    n: int,
) -> list[int]:
    """Cluster people by graph distance into a sensible number of similarity groups."""
    if n == 0:
        return []
    if n == 1:
        return [0]

    k = similarity_group_count(n)
    dist = _graph_shortest_paths(graph, n)
    np.fill_diagonal(dist, 0.0)
    condensed = squareform(dist, checks=False)
    tree = linkage(condensed, method="average")
    labels = fcluster(tree, t=k, criterion="maxclust")
    return [int(lab) - 1 for lab in labels]


def _place_orphans_by_anchor(
    x: np.ndarray,
    y: np.ndarray,
    orphans: np.ndarray,
    people: list[int],
    anchors: dict[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Place zero-edge nodes near their institution cluster, not on a uniform ring."""
    if not np.any(orphans):
        return x, y

    placed = ~orphans
    if not np.any(placed):
        return _fallback_ring(len(x))

    x = x.copy()
    y = y.copy()

    org_members: dict[int, list[int]] = {}
    for i in np.flatnonzero(placed):
        org = anchors.get(people[i])
        if org is not None:
            org_members.setdefault(org, []).append(i)

    org_centroid: dict[int, tuple[float, float]] = {}
    for org, members in org_members.items():
        org_centroid[org] = (float(np.mean(x[members])), float(np.mean(y[members])))

    global_cx = float(np.mean(x[placed]))
    global_cy = float(np.mean(y[placed]))
    span = max(
        float(np.max(np.abs(x[placed]))),
        float(np.max(np.abs(y[placed]))),
        0.3,
    )

    for i in np.flatnonzero(orphans):
        pid = people[i]
        org = anchors.get(pid)
        if org is not None and org in org_centroid:
            cx, cy = org_centroid[org]
            jitter_r = span * 0.06
        else:
            cx, cy = global_cx, global_cy
            jitter_r = span * 0.45

        # Deterministic jitter — breaks exact overlap without equal-angle spacing.
        golden = 0.61803398875
        angle = (pid * golden) % (2 * np.pi)
        radius = jitter_r * (0.35 + (pid % 89) / 89.0)
        x[i] = cx + radius * np.cos(angle)
        y[i] = cy + radius * np.sin(angle)

    return x, y


def _layout_weight(w: float) -> float:
    """Coauthor/advisor ties dominate; soft ties nudge but don't flatten the map."""
    if w >= STRONG_EDGE_FLOOR:
        return w
    return w * SOFT_LAYOUT_SCALE


def _split_weights(
    weights: dict[tuple[int, int], float],
) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], float]]:
    strong: dict[tuple[int, int], float] = {}
    soft: dict[tuple[int, int], float] = {}
    for key, w in weights.items():
        if w >= STRONG_EDGE_FLOOR:
            strong[key] = w
        else:
            soft[key] = w
    return strong, soft


def _target_distance(w: float) -> float:
    eff = _layout_weight(w)
    return 1.0 / (eff + DISSIM_EPS) ** DISSIM_POWER


def _pairwise_dissimilarity(
    weights: dict[tuple[int, int], float], n: int
) -> np.ndarray:
    """Direct person-to-person dissimilarity from summed feature weights.

    No shortest-path hops — each pair's distance reflects only their own ties.
    This avoids department-hub effects that collapse everyone onto a ring in MDS.
    """
    affinity = np.zeros((n, n), dtype=np.float64)
    for (i, j), w in weights.items():
        eff = _layout_weight(w)
        affinity[i, j] = eff
        affinity[j, i] = eff

    tied = affinity[affinity > 0]
    if tied.size == 0:
        return np.ones((n, n), dtype=np.float64) - np.eye(n)

    tied_dissim = 1.0 / (tied + DISSIM_EPS) ** DISSIM_POWER
    fill = float(np.percentile(tied_dissim, UNLINKED_PERCENTILE))

    dist = np.full((n, n), fill, dtype=np.float64)
    mask = affinity > 0
    dist[mask] = 1.0 / (affinity[mask] + DISSIM_EPS) ** DISSIM_POWER
    np.fill_diagonal(dist, 0.0)
    return dist


def _mds_from_distances(dist: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Classical (Torgerson) MDS on a precomputed dissimilarity matrix."""
    n = dist.shape[0]
    if n < 2:
        return _fallback_ring(n)

    d2 = dist.astype(np.float64) ** 2
    H = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * H @ d2 @ H
    vals, vecs = np.linalg.eigh(B)
    order = np.argsort(vals)[::-1]
    vals = vals[order]
    vecs = vecs[:, order]
    keep = np.where(vals > 1e-9)[0][:2]
    if len(keep) < 2:
        return _fallback_ring(n)
    coords = vecs[:, keep] * np.sqrt(np.maximum(vals[keep], 0.0))
    return coords[:, 0], coords[:, 1]


def _refine_stress(
    pos: np.ndarray,
    target_dist: np.ndarray,
    steps: int = STRESS_REFINE_STEPS,
    edge_mask: np.ndarray | None = None,
    scale: float = 1.0,
) -> np.ndarray:
    """Stress refinement, optionally limited to selected pairs (e.g. strong ties)."""
    pos = pos.copy()
    n = pos.shape[0]
    if n < 3:
        return pos

    eye = np.eye(n, dtype=bool)
    if edge_mask is None:
        importance = np.zeros_like(target_dist)
        finite_mask = (target_dist > 0) & ~eye
        cutoff = float(np.percentile(target_dist[finite_mask], 40))
        local = finite_mask & (target_dist <= cutoff)
        importance[local] = 1.0 / (target_dist[local] + 0.05) ** 1.5
    else:
        importance = np.where(edge_mask, 1.0 / (target_dist + 0.05) ** 1.5, 0.0)
        importance[eye] = 0.0

    importance *= scale
    lr = STRESS_LEARNING_RATE * scale

    for _ in range(steps):
        diff = pos[:, None, :] - pos[None, :, :]
        actual = np.linalg.norm(diff, axis=2) + 1e-9
        delta = actual - target_dist
        delta[eye] = 0.0
        direction = diff / actual[:, :, None]
        force = importance[:, :, None] * delta[:, :, None] * direction
        force[eye] = 0.0
        weight_sum = importance.sum(axis=1, keepdims=True) + 1e-9
        pos -= lr * force.sum(axis=1) / weight_sum
    return pos


def _position_isolated(
    pos: np.ndarray,
    soft_weights: dict[tuple[int, int], float],
    strong_weights: dict[tuple[int, int], float],
) -> np.ndarray:
    """Place people with no collaboration edges near soft-affiliated neighbors."""
    pos = pos.copy()
    n = pos.shape[0]
    strong_deg = np.zeros(n, dtype=np.int32)
    for i, j in strong_weights:
        strong_deg[i] += 1
        strong_deg[j] += 1

    soft_neighbors: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for (i, j), w in soft_weights.items():
        soft_neighbors[i].append((j, w))
        soft_neighbors[j].append((i, w))

    rng = np.random.default_rng(42)
    jitter = CANVAS_SPAN * 0.02

    for i in range(n):
        if strong_deg[i] > 0:
            continue
        nbrs = soft_neighbors[i]
        if nbrs:
            idx = np.array([j for j, _ in nbrs], dtype=int)
            wts = np.array([w for _, w in nbrs], dtype=np.float64)
            centroid = np.average(pos[idx], axis=0, weights=wts)
            pos[i] = centroid + rng.normal(scale=jitter, size=2)
        else:
            pos[i] = rng.uniform(-CANVAS_SPAN * 0.35, CANVAS_SPAN * 0.35, size=2)
    return pos


def _canvas_target_distance(w: float) -> float:
    """Desired on-screen separation for a tie of weight *w* (after canvas scaling)."""
    # 21 coauthored papers → ~0.004 world units (~2px at overview zoom).
    return 0.0015 + 0.028 / (max(w, 0.5) + 0.15) ** 0.9


def _normalize_to_canvas(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Scale to canvas using bbox — preserves cluster density, not equal spacing."""
    pos = np.column_stack([x, y])
    mins = pos.min(axis=0)
    maxs = pos.max(axis=0)
    center = (mins + maxs) / 2.0
    pos -= center
    span = float(np.max(maxs - mins))
    if span > 1e-9:
        pos = pos / span * (CANVAS_SPAN * 0.88)
    return pos


def _spring_refine_canvas(
    pos: np.ndarray,
    strong_weights: dict[tuple[int, int], float],
) -> np.ndarray:
    """Force-directed layout in final canvas coordinates."""
    pos = pos.copy()
    n = pos.shape[0]
    if n < 3 or not strong_weights:
        return pos

    rep_radius = CANVAS_SPAN * 0.06
    stranger_radius = CANVAS_SPAN * 0.045
    strong_set = set(strong_weights.keys())

    for _ in range(SPRING_ITERATIONS):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-9
        np.fill_diagonal(dist, 0.0)

        too_close = (dist > 0) & (dist < rep_radius)
        repulse = np.zeros_like(dist)
        repulse[too_close] = SPRING_REPULSION * (rep_radius - dist[too_close]) / dist[too_close]
        forces = -(repulse[:, :, None] * diff).sum(axis=1) / max(n - 1, 1)

        # Push apart unrelated people who landed too close via soft-feature MDS.
        for i in range(n):
            for j in range(i + 1, n):
                key = (i, j)
                if key in strong_set:
                    continue
                d = float(dist[i, j])
                if d >= stranger_radius:
                    continue
                unit = diff[i, j] / d
                push = SPRING_REPULSION * 0.55 * (stranger_radius - d) * unit
                forces[i] += push
                forces[j] -= push

        for (i, j), w in strong_weights.items():
            target = _canvas_target_distance(w)
            dvec = pos[j] - pos[i]
            d = float(np.linalg.norm(dvec)) + 1e-9
            delta = d - target
            strength = 1.0 + min(w, 12.0) ** 1.35 * 0.45
            f = SPRING_ATTRACTION * strength * delta * dvec / d
            forces[i] += f
            forces[j] -= f

        step = forces * SPRING_DAMPING
        step_norm = np.linalg.norm(step, axis=1, keepdims=True)
        step_norm = np.maximum(step_norm, 1e-12)
        step = np.where(step_norm > SPRING_MAX_STEP, step * (SPRING_MAX_STEP / step_norm), step)
        pos += step

        if not np.all(np.isfinite(pos)):
            break

    return np.clip(pos, -CANVAS_SPAN, CANVAS_SPAN)


def _enforce_coauthor_proximity(
    pos: np.ndarray,
    strong_weights: dict[tuple[int, int], float],
) -> np.ndarray:
    """Iteratively pull collaborators together; nudge unrelated dots out of the way."""
    pos = pos.copy()
    n = pos.shape[0]
    if n < 2 or not strong_weights:
        return pos

    strong_set = set(strong_weights.keys())

    for step in range(COAUTHOR_ENFORCE_STEPS):
        for (i, j), w in strong_weights.items():
            target = _canvas_target_distance(w)
            dvec = pos[j] - pos[i]
            d = float(np.linalg.norm(dvec)) + 1e-12
            if d <= target:
                continue
            pull = min(COAUTHOR_PULL, 0.1 + w * 0.012)
            move = 0.5 * pull * (d - target) * dvec / d
            pos[i] += move
            pos[j] -= move

        if step % 4 != 0:
            continue

        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-12
        for i in range(n):
            for j in range(i + 1, n):
                if (i, j) in strong_set:
                    continue
                d = float(dist[i, j])
                if d >= STRANGER_REPULSE_RADIUS:
                    continue
                unit = diff[i, j] / d
                push = 0.2 * (STRANGER_REPULSE_RADIUS - d) * unit
                pos[i] -= push
                pos[j] += push

    return np.clip(pos, -CANVAS_SPAN, CANVAS_SPAN)


def _edge_mask_from_weights(
    weights: dict[tuple[int, int], float], n: int
) -> np.ndarray:
    mask = np.zeros((n, n), dtype=bool)
    for (i, j) in weights:
        mask[i, j] = True
        mask[j, i] = True
    return mask


def _feature_coords(
    weights: dict[tuple[int, int], float], n: int
) -> tuple[np.ndarray, np.ndarray, set[tuple[int, int]]]:
    """MDS init from all features; spring + stress lock in collaboration structure."""
    strong, soft = _split_weights(weights)
    pinned = set(strong.keys())

    # Global spread from all features (soft scaled down); spring enforces collaboration order.
    dist_all = _pairwise_dissimilarity(weights, n)
    x, y = _mds_from_distances(dist_all)
    pos = np.column_stack([x, y])

    dist_strong = _pairwise_dissimilarity(strong, n) if strong else dist_all
    strong_mask = _edge_mask_from_weights(strong, n)
    pos = _refine_stress(pos, dist_strong, edge_mask=strong_mask)

    if soft:
        dist_soft = _pairwise_dissimilarity(soft, n)
        soft_mask = _edge_mask_from_weights(soft, n)
        pos = _refine_stress(
            pos,
            dist_soft,
            steps=SOFT_STRESS_STEPS,
            edge_mask=soft_mask,
            scale=SOFT_STRESS_SCALE,
        )

    return pos[:, 0], pos[:, 1], pinned


def _graph_to_coords(
    graph: dict[tuple[int, int], float],
    n: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Classical MDS on shortest-path distances in the kNN graph."""
    dist = _graph_shortest_paths(graph, n)

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
    return coords[:, 0], coords[:, 1]


def multimodal_projection(
    session: Session,
    people: list[int],
    edges: list[tuple[int, int, float]],
    knn_k: int,
) -> tuple[np.ndarray, np.ndarray, list[int], str, str]:
    """Similarity matrix → best 2D layout; similarity groups from network block."""
    n = len(people)
    if n < 2:
        raise ValueError(f"need >= 2 people, got {n}")

    blocks = load_similarity_blocks(session, people)
    sim = blocks.combined()
    dist = similarity_to_distance(sim)
    features = block_feature_matrix(blocks)

    pos, layout_method, evals = choose_layout(dist, features)
    metrics_note = "; ".join(
        f"{e.method}: recall@10={e.neighbor_recall_at_10:.3f} rho={e.global_spearman:.3f}"
        for e in evals
    )
    algorithm = f"{ALGORITHM}+{layout_method}"

    pos = _normalize_to_canvas(pos[:, 0], pos[:, 1])

    # Similarity groups for coloring — network block only.
    network_edge_list: list[tuple[int, int, float]] = []
    for i in range(n):
        for j in range(i + 1, n):
            w = float(blocks.network[i, j])
            if w > 0.05:
                network_edge_list.append((people[i], people[j], w * COAUTHOR_WEIGHT_CAP))
    if network_edge_list:
        layout_sparse = _knn_sparsify(_sum_edges(network_edge_list, people), n, knn_k)
        similarity_groups = assign_similarity_groups(layout_sparse or _sum_edges(network_edge_list, people), n)
    else:
        summed = _sum_edges(edges, people)
        layout_sparse = _knn_sparsify(summed, n, knn_k)
        similarity_groups = assign_similarity_groups(layout_sparse or summed, n)

    x, y = _light_overlap_separation(
        pos[:, 0], pos[:, 1], sim=sim, network=blocks.network
    )
    pos = _normalize_to_canvas(x, y)
    x, y = pos[:, 0], pos[:, 1]

    return x, y, similarity_groups, algorithm, metrics_note


def _light_overlap_separation(
    x: np.ndarray,
    y: np.ndarray,
    sim: np.ndarray | None = None,
    network: np.ndarray | None = None,
    min_sep: float | None = None,
    sim_preserve: float = 0.55,
) -> tuple[np.ndarray, np.ndarray]:
    """Nudge only stacked points apart; never spread clusters to equal spacing."""
    pos = np.column_stack([x, y]).copy()
    n = pos.shape[0]
    if n < 2:
        return x, y

    if min_sep is None:
        min_sep = CANVAS_SPAN * 0.004

    rng = np.random.default_rng(7)
    for _ in range(12):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                if network is not None and network[i, j] >= NETWORK_FLOOR:
                    continue
                if sim is not None and sim[i, j] >= sim_preserve:
                    continue
                diff = pos[i] - pos[j]
                d = float(np.linalg.norm(diff))
                if d >= min_sep:
                    continue
                moved = True
                if d < 1e-12:
                    diff = rng.normal(size=2)
                    d = float(np.linalg.norm(diff)) + 1e-12
                push = (min_sep - d) * 0.35 * diff / d
                pos[i] += push
                pos[j] -= push
        if not moved:
            break
    return pos[:, 0], pos[:, 1]


def laplacian_eigenmap_projection(
    session: Session,
    people: list[int],
    edges: list[tuple[int, int, float]],
    fallback_edges: list[tuple[int, int, float]],
    anchors: dict[int, int],
    k: int,
    knn_k: int,
) -> tuple[np.ndarray, np.ndarray, list[int], str, str]:
    del fallback_edges, anchors, k
    return multimodal_projection(session, people, edges, knn_k)


def _fallback_ring(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic ring for degenerate graphs."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.cos(t), np.sin(t)


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
    similarity_groups: list[int],
    raw_dim: int,
    algorithm: str | None = None,
    notes: str | None = None,
) -> int:
    run_id = int(
        session.execute(
            text(
                """
                INSERT INTO embedding_runs
                    (kind, algorithm, raw_dim, point_count, is_active, notes)
                VALUES
                    (:kind, :algorithm, :raw_dim, :count, FALSE, :notes)
                RETURNING id
                """
            ),
            {
                "kind": KIND,
                "algorithm": algorithm or ALGORITHM,
                "raw_dim": raw_dim,
                "count": len(people),
                "notes": notes,
            },
        ).scalar_one()
    )

    rows = [
        {
            "run_id": run_id,
            "person_id": pid,
            "x": float(x[i]),
            "y": float(y[i]),
            "similarity_group": int(similarity_groups[i]),
        }
        for i, pid in enumerate(people)
        if np.isfinite(x[i]) and np.isfinite(y[i])
    ]
    if rows:
        session.execute(
            text(
                """
                INSERT INTO person_projections_2d (run_id, person_id, x, y, similarity_group)
                VALUES (:run_id, :person_id, :x, :y, :similarity_group)
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
    all_people, edges = load_person_edges(session)
    people = [pid for pid in all_people if pid in person_set]
    if len(people) < 2:
        return []

    filtered = [(a, b, w) for a, b, w in edges if a in person_set and b in person_set]
    summed = _sum_edges(filtered, people)
    sparse = _knn_sparsify(summed, len(people), knn)
    idx_to_pid = {i: pid for i, pid in enumerate(people)}
    return [(idx_to_pid[i], idx_to_pid[j], w) for (i, j), w in sparse.items()]


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
        anchors = load_person_anchors(session)
        print(
            f"[embed] {len(edges)} weighted edges, {len(people)} people, "
            f"knn={args.knn}"
        )
        if len(people) < 2:
            print("[embed] too few people — exiting")
            return
        x, y, similarity_groups, algorithm, metrics = laplacian_eigenmap_projection(
            session,
            people,
            edges,
            fallback_edges,
            anchors,
            k=args.k,
            knn_k=args.knn,
        )
        finite = int(np.sum(np.isfinite(x) & np.isfinite(y)))
        print(
            f"[embed] {algorithm} — {finite}/{len(people)} finite in "
            f"{time.time() - started:.1f}s"
        )
        print(f"[embed] layout metrics: {metrics}")
        if args.dry:
            print("[embed] --dry: skipping DB writes")
            return
        run_id = write_run(
            session,
            people,
            x,
            y,
            similarity_groups,
            raw_dim=5,
            algorithm=algorithm,
            notes=metrics,
        )
        print(f"[embed] wrote run_id={run_id}, is_active=TRUE")


if __name__ == "__main__":
    main()
