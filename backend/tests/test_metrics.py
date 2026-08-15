import numpy as np
from scripts.embed.metrics import (
    cluster_purity,
    gold_set_distances,
    overlap_rate,
    separation_ratio,
    strong_edge_fidelity,
)


def test_cluster_purity_weighted_mean():
    clusters = np.array([0, 0, 0, 1, 1])
    fields = np.array([0, 0, 1, 1, 1])
    # cluster 0: 2/3 pure, size 3; cluster 1: 3/3 pure, size 2 -> (2/3*3 + 1*2)/5
    assert abs(cluster_purity(clusters, fields) - (2.0 / 3.0 * 3 + 2) / 5) < 1e-9


def test_separation_ratio_above_one_for_separated_blobs():
    rng = np.random.default_rng(0)
    pos = np.vstack([
        rng.normal(0, 0.05, (20, 2)),
        rng.normal(2, 0.05, (20, 2)),
    ])
    clusters = np.array([0] * 20 + [1] * 20)
    assert separation_ratio(pos, clusters) > 3.0


def test_strong_edge_fidelity_prefers_short_edges():
    pos = np.array([[0.0, 0.0], [0.05, 0.0], [1.0, 0.0], [1.05, 0.0]])
    edges = [(0, 1, 10.0), (2, 3, 10.0), (0, 2, 1.0)]
    # top-1 by weight = (0,1) (int(3*1/3)=1 edge), ~0.05 long
    f = strong_edge_fidelity(pos, edges, top_frac=1 / 3)
    assert f < 0.1


def test_overlap_rate_counts_cross_cluster_near_pairs():
    pos = np.array([[0.0, 0.0], [0.005, 0.0], [5.0, 0.0]])
    clusters = np.array([0, 0, 1])
    assert overlap_rate(pos, clusters, min_sep=0.02) == 0.0
    clusters2 = np.array([0, 1, 1])
    assert overlap_rate(pos, clusters2, min_sep=0.02) == 1.0


def test_gold_set_distances_missing_people_skipped():
    pos = np.zeros((3, 2))
    pos[1] = [0.5, 0.0]
    index = {"naka": 0, "stein": 1, "saez": 2}
    out = gold_set_distances(pos, index, [("naka", "stein"), ("saez", "zucman")])
    assert ("naka", "stein") in out
    assert ("saez", "zucman") not in out
