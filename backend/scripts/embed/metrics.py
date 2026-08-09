"""Atlas layout quality metrics.

These replace recall@10/Spearman as the primary quality signals: they measure
readability and semantic separation rather than reproduction of a degenerate
all-pairs matrix. All functions are pure numpy — no DB access.
"""

from __future__ import annotations

import numpy as np


def cluster_purity(cluster_ids: np.ndarray, field_ids: np.ndarray) -> float:
    """Size-weighted mean of each cluster's dominant-field share."""
    total = 0.0
    count = 0
    for c in np.unique(cluster_ids):
        lab = field_ids[cluster_ids == c]
        if lab.size == 0:
            continue
        dom = np.bincount(lab).max() / lab.size
        total += dom * lab.size
        count += lab.size
    return float(total / count) if count else 0.0


def separation_ratio(pos: np.ndarray, cluster_ids: np.ndarray) -> float:
    """Median inter-cluster distance / median intra-cluster distance."""
    n = pos.shape[0]
    if n < 2:
        return 1.0
    dist = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    iu = np.triu_indices(n, k=1)
    d = dist[iu]
    ci, cj = cluster_ids[iu[0]], cluster_ids[iu[1]]
    inter = np.median(d[ci != cj]) if np.any(ci != cj) else 0.0
    intra = np.median(d[ci == cj]) if np.any(ci == cj) else 1e-9
    return float(inter / max(intra, 1e-9))


def strong_edge_fidelity(
    pos: np.ndarray,
    edges: list[tuple[int, int, float]],
    top_frac: float = 0.1,
) -> float:
    """Mean 2D distance of the strongest edges — lower is better."""
    if not edges:
        return 0.0
    top = sorted(edges, key=lambda t: -t[2])[: max(1, int(len(edges) * top_frac))]
    ds = [float(np.linalg.norm(pos[i] - pos[j])) for i, j, _ in top]
    return float(np.mean(ds)) if ds else 0.0


def overlap_rate(
    pos: np.ndarray,
    cluster_ids: np.ndarray,
    min_sep: float = 0.02,
) -> float:
    """Fraction of cross-cluster pairs closer than `min_sep` among all close pairs."""
    n = pos.shape[0]
    if n < 2:
        return 0.0
    dist = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    iu = np.triu_indices(n, k=1)
    d = dist[iu]
    close = d < min_sep
    if not np.any(close):
        return 0.0
    cross = cluster_ids[iu[0]][close] != cluster_ids[iu[1]][close]
    return float(cross.mean())


def gold_set_distances(
    pos: np.ndarray,
    index_by_lastname: dict[str, int],
    gold_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """2D distance for each gold pair whose members are on the map."""
    out: dict[tuple[str, str], float] = {}
    for a, b in gold_pairs:
        ia, ib = index_by_lastname.get(a), index_by_lastname.get(b)
        if ia is None or ib is None:
            continue
        out[(a, b)] = float(np.linalg.norm(pos[ia] - pos[ib]))
    return out
