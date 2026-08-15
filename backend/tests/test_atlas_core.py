def test_runtime_deps_import():
    import igraph
    import numpy as np  # noqa: F401
    import scipy  # noqa: F401

    assert igraph is not None


import numpy as np
from scripts.embed.atlas_core import (
    association_strength,
    collapse_edges,
    disparity_filter,
    leiden_communities,
    sweep_resolution,
)


def test_collapse_edges_sums_parallel_edges():
    out = collapse_edges([(0, 1, 1.0), (1, 0, 2.0), (1, 2, 3.0)], 3)
    assert out == {(0, 1): 3.0, (1, 2): 3.0}


def test_association_strength_downweights_hub_edges():
    # Star: hub 0 has 4 weak edges; leaf pair (1,2) has a strong edge.
    edges = [(0, 1, 1.0), (0, 2, 1.0), (0, 3, 1.0), (0, 4, 1.0), (1, 2, 3.0)]
    a = association_strength(5, edges)
    # Leaf pair normalized similarity must exceed hub-leaf similarity.
    assert a[1, 2] > a[0, 1]
    assert np.isclose(a[0, 1], a[0, 2])
    assert np.allclose(a, a.T)


def test_disparity_filter_keeps_significant_edges():
    # Two triangles joined by one weak bridge; each triangle has a
    # dominant edge (24 vs 1) so it is significant at both endpoints.
    edges = [
        (0, 1, 24.0), (0, 2, 1.0), (1, 2, 1.0),
        (3, 4, 24.0), (3, 5, 1.0), (4, 5, 1.0),
        (2, 3, 0.5),
    ]
    kept = disparity_filter(edges, 6, alpha=0.05)
    kept_set = {(min(i, j), max(i, j)) for i, j, _ in kept}
    assert (2, 3) not in kept_set
    assert (0, 1) in kept_set
    assert (3, 4) in kept_set


def test_leiden_recovers_two_communities():
    # Two dense cliques connected by one edge.
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(2, 3, 0.1)]
    )
    labels = leiden_communities(6, edges, resolution=1.0)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_sweep_resolution_hits_target_range():
    # 3-clique chain: 15 nodes. Force a target of 2-3 clusters.
    edges = []
    for c in range(3):
        base = c * 5
        for i in range(base, base + 5):
            for j in range(i + 1, base + 5):
                edges.append((i, j, 1.0))
        if c < 2:
            edges.append((base + 4, base + 5, 0.05))
    labels, gamma = sweep_resolution(edges, 15, target_min=2, target_max=3)
    k = max(labels) + 1
    assert 2 <= k <= 3
    assert 0.0 < gamma


from scripts.embed.atlas_core import (
    assign_topic_clusters,
    classical_mds,
    linlog_layout,
    local_spring_layout,
    normalize_to_canvas,
)


def test_linlog_separates_two_cliques():
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(2, 3, 0.1)]
    )
    pos = linlog_layout(6, edges, seed=0)
    intra = [np.linalg.norm(pos[i] - pos[j]) for i, j, _ in edges if min(i, j) < 3 <= max(i, j) or min(i, j) >= 3]
    inter = np.linalg.norm(pos[0] - pos[5])
    # Members of each clique stay tight; cliques drift apart.
    assert max(intra) < inter
    assert pos.shape == (6, 2)
    assert np.all(np.isfinite(pos))


def test_local_spring_keeps_strong_ties_close():
    edges = [(0, 1, 10.0), (1, 2, 10.0), (2, 0, 10.0), (3, 0, 0.2)]
    pos = local_spring_layout(4, edges, seed=0)
    d01 = np.linalg.norm(pos[0] - pos[1])
    d03 = np.linalg.norm(pos[0] - pos[3])
    assert d01 < d03


def test_classical_mds_recovers_line():
    x = np.array([[0.0], [1.0], [3.0], [6.0]])
    dist = np.abs(x - x.T)
    pos = classical_mds(dist, dim=2)
    # Rank order of pairwise distances preserved along the first axis.
    d = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    assert d[0, 1] < d[0, 2] < d[0, 3]


def test_normalize_to_canvas_centers_and_scales():
    pos = np.array([[100.0, 100.0], [0.0, 0.0], [100.0, 0.0]])
    out = normalize_to_canvas(pos, span=2.0)
    assert np.allclose(out.mean(axis=0), 0.0, atol=1e-9)
    assert np.max(np.ptp(out, axis=0)) <= 2.0 + 1e-9


def test_assign_topic_clusters_refines_large_fields():
    # 80 people in field 0 (macro), 10 in field 1 (finance); profiles random-ish.
    rng = np.random.default_rng(0)
    n0, n1 = 80, 10
    dominant = np.array([0] * n0 + [1] * n1)
    prof = rng.normal(size=(n0 + n1, 6))
    prof[n0:] += np.array([0.0, 0, 0, 2.0, 2.0, 2.0])  # finance people cluster apart
    labels, names = assign_topic_clusters(
        dominant, prof, ["Macroeconomics", "Finance"], max_field_members=60
    )
    k = max(labels) + 1
    assert k >= 3  # field 0 split into 2+, field 1 kept whole
    assert len(names) == k
    assert set(labels[n0:]) == {labels[n0]}
    # Every label has at least one member
    for c in range(k):
        assert (labels == c).any()


def test_place_bridge_nodes_between_two_cliques():
    from scripts.embed.atlas_core import place_bridge_nodes

    # Node 6 ties cliques {0,1,2} and {3,4,5} equally and strongly.
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(6, 0, 1.0), (6, 1, 1.0), (6, 2, 1.0),
           (6, 3, 1.0), (6, 4, 1.0), (6, 5, 1.0)]
    )
    labels = [0, 0, 0, 1, 1, 1, 0]  # 6 is assigned to clique 0
    cpos = np.array([[0.0, 0.0], [2.0, 0.0]])
    pos = np.zeros((7, 2))
    pos[6] = [0.5, 0.5]
    out = place_bridge_nodes(pos, labels, cpos, edges)
    # Pulled to the weighted barycenter of its neighbor clusters: (1, 0).
    assert abs(out[6, 0] - 1.0) < 1e-9
    assert abs(out[6, 1]) < 1e-9
    # Nodes without cross ties are untouched.
    assert np.allclose(out[:6], pos[:6])


def test_place_bridge_nodes_skips_multi_group_nodes():
    from scripts.embed.atlas_core import place_bridge_nodes

    # Node 4 ties one edge to each of 4 clusters: external weight spread
    # evenly (top-2 share 2/3 < 0.8) -> must stay put. A flat map cannot
    # hold a node with K overlapping memberships; edges carry that instead.
    labels = [0, 1, 2, 3, 0]
    cpos = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    pos = np.zeros((5, 2))
    pos[4] = [9.0, 9.0]
    edges = [(4, 0, 1.0), (4, 1, 1.0), (4, 2, 1.0), (4, 3, 1.0)]
    out = place_bridge_nodes(pos, labels, cpos, edges)
    assert np.allclose(out[4], [9.0, 9.0])  # untouched


def test_cluster_topic_centroids_normalized():
    from scripts.embed.atlas_core import cluster_topic_centroids

    prof = np.array([[3.0, 0.0], [2.0, 1.0], [0.0, 2.0]])
    labels = [0, 0, 1]
    out = cluster_topic_centroids(prof, labels)
    assert out.shape == (2, 2)
    assert abs(np.linalg.norm(out[0]) - 1.0) < 1e-9
    assert out[0, 0] > out[0, 1]


def test_dominant_field_extraction():
    from scripts.embed.atlas_core import dominant_field_from_profiles

    fmat = np.zeros((3, 2))
    fmat[0] = [1.0, 0.2]
    fmat[1] = [0.1, 1.0]
    fmat[2] = [0.0, 0.0]
    dom = dominant_field_from_profiles(fmat)
    assert dom.tolist() == [0, 1, -1]


def test_deoverlap_points_separates_coincident_points():
    from scripts.embed.atlas_core import deoverlap_points

    pos = np.array([[0.0, 0.0], [0.0, 0.0], [2.0, 2.0]])
    out = deoverlap_points(pos.copy(), min_dist=0.05, iterations=8)
    # The coincident pair must be pushed apart to >= min_dist.
    d = np.hypot(out[0, 0] - out[1, 0], out[0, 1] - out[1, 1])
    assert d >= 0.05 - 1e-9
    # The far point is untouched.
    assert np.allclose(out[2], [2.0, 2.0])


def test_deoverlap_points_enforces_min_dist_deterministically():
    from scripts.embed.atlas_core import deoverlap_points

    rng = np.random.default_rng(7)
    pos = rng.uniform(-1, 1, size=(30, 2))
    out = deoverlap_points(pos.copy(), min_dist=0.05, iterations=8)
    # Deterministic: same input -> same output.
    out2 = deoverlap_points(pos.copy(), min_dist=0.05, iterations=8)
    assert np.allclose(out, out2)
    # All pairs now separated (or nothing closer than min_dist existed).
    for i in range(out.shape[0]):
        for j in range(i + 1, out.shape[0]):
            d = np.hypot(out[i, 0] - out[j, 0], out[i, 1] - out[j, 1])
            assert d >= 0.05 - 1e-9, (i, j, d)


def test_deoverlap_points_single_point_noop():
    from scripts.embed.atlas_core import deoverlap_points

    pos = np.array([[1.0, 2.0]])
    assert np.allclose(deoverlap_points(pos), pos)
