"""Smoke test: build_atlas dry-run against the seeded demo DB."""

import numpy as np
import pytest
from api.deps import _SessionLocal
from sqlalchemy import text


def test_orphan_spiral_deterministic_placement():
    """Orphan nodes (no edges) land on a deterministic golden-angle spiral,
    not at the centroid, and the result is identical across runs."""
    from scripts.embed.build_atlas import build_network_view

    # n=8, edges only among nodes 0..3; nodes 4..7 have NO edges
    collapsed = {
        (0, 1): 1.0, (0, 2): 1.0, (0, 3): 1.0,
        (1, 2): 1.0, (1, 3): 1.0, (2, 3): 1.0,
    }

    def run():
        pos, labels, _cpos, _collab, _gamma, _tag = build_network_view(
            8, collapsed, target_min=1, target_max=4, seed=42
        )
        return pos, labels

    pos1, labels1 = run()
    pos2, labels2 = run()

    # Deterministic: identical across runs (same seed).
    assert np.allclose(pos1, pos2)
    assert np.array_equal(labels1, labels2)

    # Every node has finite coordinates.
    assert np.all(np.isfinite(pos1))
    assert pos1.shape == (8, 2)

    # Orphan nodes (4..7) must not overlap the centroid exactly.
    centroid = pos1.mean(axis=0)
    for i in range(4, 8):
        dist = np.linalg.norm(pos1[i] - centroid)
        assert dist > 0.01, f"orphan node {i} too close to centroid ({dist})"


@pytest.mark.integration
def test_build_atlas_dry_run():
    from scripts.embed.atlas_core import collapse_edges
    from scripts.embed.build_atlas import build_network_view, build_topic_view
    from scripts.embed.topic_profiles import load_topic_profiles

    with _SessionLocal() as s:
        people = [int(r[0]) for r in s.execute(text("SELECT id FROM people ORDER BY id")).all()]
        assert len(people) >= 2
        from scripts.embed.build_atlas import cluster_targets, load_network_edges
        edges = load_network_edges(s, people)
        collapsed = collapse_edges(edges, len(people))
        tmin, tmax = cluster_targets(len(people))
        _pos, labels, _cpos, _collab, _gamma, _tag = build_network_view(len(people), collapsed, tmin, tmax)
        assert len(labels) == len(people)
        assert 1 <= max(labels) + 1 <= len(people)
        topic_mat, field_mat, fields = load_topic_profiles(s, people)
        _pos2, labels2, _cpos2, _collab2, names = build_topic_view(len(people), topic_mat, field_mat, fields, collapsed, tmin, tmax)
        assert len(labels2) == len(people)
        assert len(names) == max(labels2) + 1
