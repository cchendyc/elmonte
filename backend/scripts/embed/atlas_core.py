"""Pure atlas algorithms — no DB imports (unit-testable).

Steps that every atlas view shares:
  1. collapse parallel edges
  2. association-strength normalization (van Eck & Waltman 2009)
  3. disparity-filter backbone (Serrano et al. 2009)
  4. Leiden communities with a resolution sweep (Traag et al. 2019)
"""

from __future__ import annotations

import numpy as np


def collapse_edges(
    edges: list[tuple[int, int, float]], n: int
) -> dict[tuple[int, int], float]:
    """Sum parallel edges, canonical order (min, max), drop self-loops/zeros."""
    out: dict[tuple[int, int], float] = {}
    for a, b, w in edges:
        if a == b or w <= 0:
            continue
        if a < 0 or b < 0 or a >= n or b >= n:
            continue
        key = (min(a, b), max(a, b))
        out[key] = out.get(key, 0.0) + w
    return out


def association_strength(
    n: int, edges: list[tuple[int, int, float]]
) -> np.ndarray:
    """s_ij = 2m·w_ij / (k_i·k_j) — degree-normalized similarity (VOSviewer)."""
    m = sum(w for _, _, w in edges)
    strength = np.zeros(n, dtype=np.float64)
    for i, j, w in edges:
        strength[i] += w
        strength[j] += w
    sim = np.zeros((n, n), dtype=np.float64)
    for i, j, w in edges:
        denom = strength[i] * strength[j]
        if denom > 0:
            sim[i, j] = sim[j, i] = 2.0 * m * w / denom
    return sim


def disparity_filter(
    edges: list[tuple[int, int, float]],
    n: int,
    alpha: float = 0.05,
) -> list[tuple[int, int, float]]:
    """Keep edges whose normalized weight is significant at either endpoint.

    p_ij = w_ij / s_i; significance alpha_ij = (1 - p_ij)^(k_i - 1).
    OR rule (either endpoint significant). Isolated pairs are always kept so
    no component vanishes.
    """
    strength = np.zeros(n, dtype=np.float64)
    degree = np.zeros(n, dtype=np.int64)
    for i, j, w in edges:
        strength[i] += w
        strength[j] += w
        degree[i] += 1
        degree[j] += 1
    kept: list[tuple[int, int, float]] = []
    for i, j, w in edges:
        if degree[i] == 1 and degree[j] == 1:
            kept.append((i, j, w))
            continue
        sig_i = sig_j = 1.0
        if strength[i] > 0 and degree[i] > 1:
            p = w / strength[i]
            sig_i = (1.0 - p) ** (degree[i] - 1)
        if strength[j] > 0 and degree[j] > 1:
            p = w / strength[j]
            sig_j = (1.0 - p) ** (degree[j] - 1)
        if min(sig_i, sig_j) < alpha:
            kept.append((i, j, w))
    return kept


def leiden_communities(
    n: int,
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
    seed: int = 42,
) -> list[int]:
    """Leiden modularity clustering (igraph C core)."""
    if n <= 1:
        return [0] * n
    import igraph as ig

    g = ig.Graph(n=n)
    if edges:
        g.add_edges([(i, j) for i, j, _ in edges])
        g.es["weight"] = [float(w) for _, _, w in edges]
    # NOTE: igraph's leiden takes no seed through this API; dropping the old
    # `np.random.seed(seed)` call removes a global-RNG side effect. Layouts
    # downstream all use `np.random.default_rng(seed)`, so the pipeline stays
    # deterministic even though leiden itself is not seeded here.
    part = g.community_leiden(
        objective_function="modularity",
        weights="weight" if edges else None,
        resolution=float(resolution),
        n_iterations=2,
    )
    return list(part.membership)


def sweep_resolution(
    edges: list[tuple[int, int, float]],
    n: int,
    target_min: int,
    target_max: int,
    *,
    seed: int = 42,
    max_iterations: int = 12,
) -> tuple[list[int], float]:
    """Binary-search Leiden resolution to land cluster count in [min, max].

    Higher resolution -> more, smaller clusters.
    """
    lo, hi = 0.2, 8.0
    best_labels: list[int] = []
    best_gamma = 1.0
    best_gap: float | None = None
    target_mid = (target_min + target_max) / 2.0
    for _ in range(max_iterations):
        gamma = (lo + hi) / 2.0
        labels = leiden_communities(n, edges, resolution=gamma, seed=seed)
        k = max(labels) + 1
        gap = abs(k - target_mid)
        if best_gap is None or gap < best_gap:
            best_labels, best_gamma, best_gap = labels, gamma, gap
        if target_min <= k <= target_max:
            return labels, gamma
        if k < target_min:
            lo = gamma  # need more clusters -> raise resolution
        else:
            hi = gamma
    return (best_labels if best_labels else leiden_communities(n, edges)), best_gamma


# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


def linlog_layout(
    n: int,
    edges: list[tuple[int, int, float]],
    *,
    iterations: int = 600,
    seed: int = 42,
) -> np.ndarray:
    """Force-directed layout with logarithmic attraction (linlog, Noack 2009).

    Attraction grows as log(1+d), so distant weak ties pull far less than in
    a linear model — communities separate into compact clusters.
    """
    rng = np.random.default_rng(seed)
    pos = rng.normal(scale=0.2, size=(n, 2))
    max_step = 0.15
    for _ in range(iterations):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-9
        np.fill_diagonal(dist, np.inf)
        inv = 1.0 / dist
        rep = np.zeros_like(pos)
        for i in range(n):
            rep[i] = (inv[i, :, None] ** 2 * (pos[i] - pos)).sum(axis=0)
        attr = np.zeros_like(pos)
        for i, j, w in edges:
            d = dist[i, j]
            u = (pos[i] - pos[j]) / d
            f = 0.02 * w * np.log1p(d) * u
            attr[i] -= f
            attr[j] += f
        force = 0.05 * rep + attr - 0.002 * pos
        step = force * 0.15
        norm = np.linalg.norm(step, axis=1, keepdims=True)
        step = np.where(
            norm > max_step, step * (max_step / np.maximum(norm, 1e-12)), step
        )
        pos += step
    pos -= pos.mean(axis=0)
    span = np.max(np.ptp(pos, axis=0))
    if span > 0:
        pos /= span
    return pos


def local_spring_layout(
    n: int,
    edges: list[tuple[int, int, float]],
    *,
    iterations: int = 240,
    seed: int = 42,
) -> np.ndarray:
    """Small intra-cluster layout; target distance shrinks with edge weight."""
    if n == 0:
        return np.zeros((0, 2))
    if n == 1:
        return np.zeros((1, 2))
    rng = np.random.default_rng(seed)
    pos = rng.normal(scale=0.3, size=(n, 2))
    for _ in range(iterations):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-9
        np.fill_diagonal(dist, 0.0)
        rep = np.zeros_like(pos)
        close = (dist > 0) & (dist < 0.5)
        with np.errstate(divide="ignore", invalid="ignore"):
            rep = (
                -(np.where(close, (0.5 - dist) / dist, 0.0))[:, :, None] * diff * 0.02
            ).sum(axis=1)
        attr = np.zeros_like(pos)
        for i, j, w in edges:
            d = dist[i, j]
            target = 0.05 + 0.25 / (w + 0.3)
            u = (pos[j] - pos[i]) / d
            f = 0.06 * (d - target) * u
            attr[i] += f
            attr[j] -= f
        pos += (rep + attr) * 0.4
    pos -= pos.mean(axis=0)
    return pos


def classical_mds(dist: np.ndarray, dim: int = 2) -> np.ndarray:
    """Torgerson MDS on a distance matrix."""
    n = dist.shape[0]
    if n < 2:
        return np.zeros((n, max(dim, 2)))
    d2 = dist.astype(np.float64) ** 2
    h = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * h @ d2 @ h
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals = np.maximum(vals[order][:dim], 0.0)
    vecs = vecs[:, order][:, :dim]
    return vecs * np.sqrt(vals)


def normalize_to_canvas(pos: np.ndarray, span: float = 2.0) -> np.ndarray:
    """Center and scale a layout to a square canvas of side ~= span."""
    out = pos.copy()
    out -= out.mean(axis=0)
    s = float(np.max(np.ptp(out, axis=0)))
    if s > 1e-9:
        out = out / s * (span * 0.88)
    return out


# ---------------------------------------------------------------------------
# Topic clustering
# ---------------------------------------------------------------------------


def assign_topic_clusters(
    dominant_field: np.ndarray,
    topic_profiles: np.ndarray,
    field_names: list[str],
    *,
    max_field_members: int = 60,
    members_per_cluster: int = 40,
) -> tuple[np.ndarray, list[str]]:
    """Coarse = dominant OpenAlex field; refine oversized fields by Ward.

    dominant_field[i] is an index into field_names, or -1 for no topics
    (they form one "Unknown" cluster). Returns (labels, cluster_names).
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    n = dominant_field.shape[0]
    labels = dominant_field.astype(np.int64).copy()
    next_id = int(labels.max() + 1) if labels.size else 0
    names: dict[int, str] = {}
    for f in np.unique(dominant_field[dominant_field >= 0]):
        members = np.flatnonzero(dominant_field == f)
        name = field_names[int(f)] if 0 <= int(f) < len(field_names) else "Unknown"
        if members.size <= max_field_members:
            names[int(f)] = name
            continue
        k = max(2, int(np.ceil(members.size / members_per_cluster)))
        prof = topic_profiles[members]
        tree = linkage(pdist(prof, metric="euclidean"), method="ward")
        sub = fcluster(tree, t=k, criterion="maxclust")
        for j, s in enumerate(np.unique(sub)):
            labels[members[sub == s]] = next_id + j
            names[next_id + j] = f"{name} · {j + 1}"
        next_id += k
    if np.any(dominant_field < 0):
        names[0] = "Unknown"
        labels[dominant_field < 0] = 0
    ordered = sorted(names.items())
    remap = {old: new for new, (old, _) in enumerate(ordered)}
    labels = np.array([remap[int(x)] for x in labels])
    cluster_names = [name for _, name in ordered]
    return labels, cluster_names


def place_bridge_nodes(
    pos: np.ndarray,
    labels: list[int],
    cluster_pos: np.ndarray,
    edges: list[tuple[int, int, float]],
    *,
    bridge_frac: float = 0.5,
    top2_share: float = 0.8,
) -> np.ndarray:
    """Move bridge nodes to the weighted barycenter of their neighboring
    cluster centroids.

    A node with strong ties to 1-2 foreign clusters would otherwise force
    those clusters together in a flat layout. Instead we keep the clusters
    separated and let the node sit between them — it lands in the overlap of
    the two hulls, which reads as "bridges these groups". Nodes whose
    external weight is spread over many clusters stay put: a flat 2D map
    cannot hold a node with K overlapping memberships; the weighted cluster
    edges carry that information instead.
    """
    n = pos.shape[0]
    out = pos.copy()
    neighbor: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for i, j, w in edges:
        neighbor[i].append((j, w))
        neighbor[j].append((i, w))
    for i in range(n):
        nbrs = neighbor[i]
        if not nbrs:
            continue
        total = sum(w for _, w in nbrs)
        own = sum(w for j, w in nbrs if labels[j] == labels[i])
        external = total - own
        if total <= 0 or external / total < bridge_frac:
            continue
        ext_by_cluster: dict[int, float] = {}
        for j, w in nbrs:
            c = labels[j]
            if c != labels[i]:
                ext_by_cluster[c] = ext_by_cluster.get(c, 0.0) + w
        if not ext_by_cluster:
            continue
        top2 = sum(sorted(ext_by_cluster.values(), reverse=True)[:2])
        if top2 / external < top2_share:
            continue
        accum = np.zeros(2)
        wsum = 0.0
        for j, w in nbrs:
            accum += w * cluster_pos[labels[j]]
            wsum += w
        out[i] = accum / wsum
    return out


def cluster_topic_centroids(profiles: np.ndarray, labels: list[int]) -> np.ndarray:
    """Mean topic profile per cluster, L2-normalized (for edge weights)."""
    k = max(labels) + 1
    out = np.zeros((k, profiles.shape[1]))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        if members.size:
            out[c] = profiles[members].mean(axis=0)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    out = out / np.maximum(norms, 1e-9)
    return out


def dominant_field_from_profiles(field_profile: np.ndarray) -> np.ndarray:
    """Argmax field per person; -1 for people with no profile."""
    n = field_profile.shape[0]
    dom = np.full(n, -1, dtype=np.int64)
    has = field_profile.sum(axis=1) > 1e-9
    dom[has] = field_profile[has].argmax(axis=1)
    return dom


def deoverlap_points(
    pos: np.ndarray,
    min_dist: float = 0.05,
    iterations: int = 8,
) -> np.ndarray:
    """Deterministically push apart points closer than *min_dist*.

    MDS/spring layouts can place people with (nearly) identical profiles at
    (nearly) the same coordinate — a visible stack of dots that no camera
    zoom can separate.  This post-pass pushes each too-close pair apart along
    their own axis by half the deficit, a bounded number of times.

    * Deterministic: fixed pair order, no randomness — same input, same
      output, so atlas runs stay reproducible.
    * Structure-preserving: only pairs closer than *min_dist* (a few percent
      of the canvas) are touched, and each move is a half-deficit nudge
      along the pair axis; genuine cluster shape is untouched.
    * Bounded: O(n² · iterations) with an early exit when nothing moves;
      callers should gate large datasets (n > ~2000) if needed.

    Mutates and returns *pos*.
    """
    n = pos.shape[0]
    if n < 2:
        return pos
    min2 = min_dist * min_dist
    for _ in range(iterations):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = pos[j, 0] - pos[i, 0]
                dy = pos[j, 1] - pos[i, 1]
                d2 = dx * dx + dy * dy
                if d2 >= min2:
                    continue
                if d2 < 1e-18:
                    # Exactly coincident: push along a fixed axis so the
                    # direction is deterministic (and not zero).  Distance is
                    # 0 here, so the push below is a full half-deficit nudge.
                    ux, uy = 1.0, 0.0
                    d = 0.0
                else:
                    d = np.sqrt(d2)
                    ux, uy = dx / d, dy / d
                push = 0.5 * (min_dist - d)
                pos[i, 0] -= ux * push
                pos[i, 1] -= uy * push
                pos[j, 0] += ux * push
                pos[j, 1] += uy * push
                moved = True
        if not moved:
            break
    return pos
