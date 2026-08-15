"""Build the researcher atlas: two views, two-level layouts, weighted cluster edges.

Network view — communities from the disparity-filtered, association-strength
normalized coauthor graph; linlog cluster layout; local springs inside each
cluster footprint.
Topic view — OpenAlex topic profiles (person_topics); dominant-field coarse
clusters refined by Ward; MDS cluster layout on centroid topic distance;
local MDS inside each footprint.

Both views compute both inter-cluster edge semantics (collaboration weight =
sum of cross-cluster coauthor weight; topic weight = centroid cosine).

Run:
    python3 -m scripts.embed.build_atlas [--view both|network|topic] [--dry]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from api.deps import _SessionLocal
from sqlalchemy import text
from sqlalchemy.orm import Session

from scripts.embed.atlas_core import (
    assign_topic_clusters,
    association_strength,
    classical_mds,
    cluster_topic_centroids,
    collapse_edges,
    deoverlap_points,
    disparity_filter,
    dominant_field_from_profiles,
    linlog_layout,
    local_spring_layout,
    normalize_to_canvas,
    place_bridge_nodes,
    sweep_resolution,
)
from scripts.embed.metrics import (
    cluster_purity,
    gold_set_distances,
    overlap_rate,
    separation_ratio,
    strong_edge_fidelity,
)
from scripts.embed.topic_profiles import load_topic_profiles

KIND = "person_atlas_v2"
COAUTHOR_CAP = 20.0
ADVISOR_WEIGHT = 5.0
BACKBONE_ALPHA = 0.05
CANVAS_SPAN = 2.0
# Minimum world-space separation enforced after layout (see deoverlap_points):
# people with near-identical profiles can otherwise stack at one coordinate,
# which no camera zoom can separate.
MIN_LAYOUT_SEPARATION = 0.05
DEOVERLAP_ITERATIONS = 8


def _cluster_field_label(cluster: int, centroid: np.ndarray, field_names: list[str]) -> str:
    """Label a cluster by its dominant OpenAlex field; fall back to generic.

    ``centroid`` is the cluster's topic-centroid row (n_fields floats); the
    argmax field is what the topic view uses for its labels, so the two views
    speak the same vocabulary.
    """
    if centroid is None or len(centroid) == 0:
        return f"Community {cluster + 1}"
    top = int(np.argmax(centroid))
    if 0 <= top < len(field_names) and centroid[top] > 0 and field_names[top]:
        return field_names[top]
    return f"Community {cluster + 1}"
FOOTPRINT_A = 0.03
FOOTPRINT_B = 0.002
# Known researcher pairs that must land close (regression check).
GOLD_PAIRS: list[tuple[str, str]] = [
    ("Nakamura", "Steinsson"),
    ("Saez", "Zucman"),
    ("DellaVigna", "Malmendier"),
    ("Einav", "Levin"),
]


def load_people(session: Session) -> list[int]:
    return [int(r[0]) for r in session.execute(text("SELECT id FROM people ORDER BY id")).all()]


def load_network_edges(session: Session, people: list[int]) -> list[tuple[int, int, float]]:
    """Coauthor edges with FRACTIONAL weights (papers are hyperedges).

    A paper with k authors contributes 1/(k-1) to each author pair instead of
    +1, so a 4-author paper doesn't inflate six independent pairwise
    relationships. Display paperCount stays the integer count from
    person_coauthor_edges (used by resolvers/frontend unchanged).
    """
    people_set = set(people)
    edges: list[tuple[int, int, float]] = []
    rows = session.execute(
        text(
            """
            SELECT pa1.person_id AS a, pa2.person_id AS b,
                   sum(1.0 / (pub.k - 1)) AS frac_weight
            FROM publication_authors pa1
            JOIN publication_authors pa2
              ON pa2.publication_id = pa1.publication_id
             AND pa2.person_id > pa1.person_id
            JOIN (
              SELECT publication_id, count(*) AS k
              FROM publication_authors
              GROUP BY publication_id
            ) pub ON pub.publication_id = pa1.publication_id
            WHERE pub.k > 1
            GROUP BY pa1.person_id, pa2.person_id
            """
        )
    ).all()
    for a, b, frac in rows:
        a, b = int(a), int(b)
        if a in people_set and b in people_set:
            edges.append((a, b, min(float(frac), COAUTHOR_CAP)))
    for a, b in session.execute(
        text(
            "SELECT from_person_id, to_person_id FROM person_relationships "
            "WHERE type = 'advised_by'"
        )
    ).all():
        if a in people_set and b in people_set:
            edges.append((int(a), int(b), ADVISOR_WEIGHT))
    return edges


def cluster_targets(n: int) -> tuple[int, int]:
    """Readable cluster count: ~1 per 40 people, 8-30 for small maps."""
    k = max(8, min(30, round(n / 40)))
    return max(2, k - 2), min(n, k + 2)


def _footprint(m: int) -> float:
    return FOOTPRINT_A + FOOTPRINT_B * float(np.sqrt(m))


def _compute_network_layout(
    n: int,
    collapsed: dict[tuple[int, int], float],
    labels: list[int],
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Full 2D layout from cluster labels (pos, cpos, collab).

    Extracted so the backbone ladder can score multiple candidate clusterings
    by their final separation_ratio without duplicating the layout pipeline.
    """
    k = max(labels) + 1
    edge_list = [(i, j, w) for (i, j), w in collapsed.items()]

    collab = np.zeros((k, k))
    for (i, j), w in collapsed.items():
        ci, cj = labels[i], labels[j]
        if ci != cj:
            collab[ci, cj] += w
            collab[cj, ci] += w
    c_edges = [
        (s, t, collab[s, t])
        for s in range(k)
        for t in range(s + 1, k)
        if collab[s, t] > 0
    ]
    cpos = linlog_layout(k, c_edges, seed=seed)

    pos = np.zeros((n, 2))
    for c in range(k):
        members = np.array([i for i in range(n) if labels[i] == c], dtype=int)
        m = members.size
        if m == 1:
            pos[members[0]] = cpos[c]
            continue
        local_edges = [
            (int(np.where(members == i)[0][0]), int(np.where(members == j)[0][0]), w)
            for (i, j), w in collapsed.items()
            if labels[i] == c and labels[j] == c
        ]
        lpos = local_spring_layout(m, local_edges, seed=seed + c)
        radius = _footprint(m)
        span = float(np.max(np.abs(lpos))) if m > 1 else 1.0
        lpos = lpos / max(span, 1e-9) * radius
        pos[members] = cpos[c] + lpos

    # Bridge nodes: pull toward the weighted barycenter of neighboring
    # clusters so they read as "between groups" instead of forcing the
    # clusters together. Multi-group nodes stay put (edge weights carry
    # their relations).
    pos = place_bridge_nodes(pos, labels, cpos, edge_list)

    # Orphans (no edges at all): deterministic golden-angle spiral around the
    # global centroid — reproducible semantic position, not a display lie.
    deg = np.zeros(n, dtype=int)
    for (i, j) in collapsed:
        deg[i] += 1
        deg[j] += 1
    if np.any(deg == 0):
        cx, cy = pos.mean(axis=0)
        golden = 0.61803398875
        for i in np.flatnonzero(deg == 0):
            angle = i * golden * 2 * np.pi
            radius = 0.05 + 0.35 * (i % 89) / 89.0
            pos[i] = [cx + radius * np.cos(angle), cy + radius * np.sin(angle)]

    pos = normalize_to_canvas(pos, span=CANVAS_SPAN)
    deoverlap_points(pos, min_dist=MIN_LAYOUT_SEPARATION, iterations=DEOVERLAP_ITERATIONS)
    return pos, cpos, collab


def build_network_view(
    n: int,
    collapsed: dict[tuple[int, int], float],
    target_min: int,
    target_max: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, str]:
    """-> (pos n*2, labels, cluster_pos k*2, collab k*k, gamma, backbone_tag)

    Evaluates ALL backbone ladder levels (disparity-filter α + kNN fallback)
    whose cluster count lands in [target_min, target_max] and picks the one
    with the best separation_ratio — not the first that fits.
    """
    edge_list = [(i, j, w) for (i, j), w in collapsed.items()]
    a = association_strength(n, edge_list)
    a_edges = [(i, j, float(a[i, j])) for (i, j) in collapsed]

    # ---- gather backbone candidates ----------------------------------------
    backbone_candidates: list[tuple[str, list[tuple[int, int, float]]]] = []

    for alpha in (BACKBONE_ALPHA, 0.1, 0.2, 0.5):
        bb = disparity_filter(edge_list, n, alpha=alpha)
        if bb:
            backbone_candidates.append((f"disparity{alpha}", bb))

    # kNN fallback: strongest 3n association-strength edges
    topk = sorted(a_edges, key=lambda t: -t[2])[: max(3 * n, n)]
    backbone_candidates.append(("knn", topk))

    # ---- evaluate every candidate that lands in range -----------------------
    best_sep = -1.0
    best_result: tuple | None = None

    for tag, bb in backbone_candidates:
        labels, gamma = sweep_resolution(bb, n, target_min, target_max, seed=seed)
        k = max(labels) + 1
        if target_min <= k <= target_max:
            pos, cpos, collab = _compute_network_layout(n, collapsed, labels, seed)
            sep = separation_ratio(pos, np.array(labels))
            if sep > best_sep:
                best_sep = sep
                best_result = (pos, labels, cpos, collab, gamma, tag)

    if best_result is not None:
        return best_result

    # ---- nothing in range: keep the old fallback chain ----------------------
    backbone = disparity_filter(edge_list, n, alpha=BACKBONE_ALPHA)
    if not backbone:
        backbone = sorted(a_edges, key=lambda t: -t[2])[: 3 * n]
        backbone_tag = "knn"
    else:
        backbone_tag = f"disparity{BACKBONE_ALPHA}"
    labels, gamma = sweep_resolution(backbone, n, target_min, target_max, seed=seed)
    if max(labels) + 1 > target_max:
        for relax in (0.1, 0.2, 0.5):
            bb = disparity_filter(edge_list, n, alpha=relax)
            if not bb:
                continue
            labels, gamma = sweep_resolution(bb, n, target_min, target_max, seed=seed)
            backbone_tag = f"disparity{relax}"
            if target_min <= max(labels) + 1 <= target_max:
                break
        else:
            topk_fb = sorted(a_edges, key=lambda t: -t[2])[: 3 * n]
            labels, gamma = sweep_resolution(topk_fb, n, target_min, target_max, seed=seed)
            backbone_tag = "knn"

    pos, cpos, collab = _compute_network_layout(n, collapsed, labels, seed)
    return pos, labels, cpos, collab, gamma, backbone_tag


def build_topic_view(
    n: int,
    topic_mat: np.ndarray,
    field_mat: np.ndarray,
    field_names: list[str],
    collapsed: dict[tuple[int, int], float],
    target_min: int,
    target_max: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """-> (pos n*2, labels, cluster_pos k*2, collab k*k, cluster_names)"""
    max_field_members = max(20, round(n / 40))
    dominant = dominant_field_from_profiles(field_mat)
    labels, names = assign_topic_clusters(
        dominant, topic_mat, field_names, max_field_members=max_field_members
    )
    k = max(labels) + 1
    T = topic_mat.shape[1]
    centroids = np.zeros((k, T))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        if members.size:
            centroids[c] = topic_mat[members].mean(axis=0)
    cn = np.linalg.norm(centroids, axis=1, keepdims=True)
    cn = np.maximum(cn, 1e-9)
    centroids = centroids / cn
    cdist = 1.0 - centroids @ centroids.T
    cpos = classical_mds(cdist)

    pos = np.zeros((n, 2))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        m = members.size
        if m == 1:
            pos[members[0]] = cpos[c]
            continue
        sub = topic_mat[members]
        sd = 1.0 - sub @ sub.T
        lpos = classical_mds(sd)
        radius = _footprint(m)
        span = float(np.max(np.abs(lpos))) if m > 1 else 1.0
        lpos = lpos / max(span, 1e-9) * radius
        pos[members] = cpos[c] + lpos

    collab = np.zeros((k, k))
    for (i, j), w in collapsed.items():
        ci, cj = labels[i], labels[j]
        if ci != cj:
            collab[ci, cj] += w
            collab[cj, ci] += w
    pos = normalize_to_canvas(pos, span=CANVAS_SPAN)
    deoverlap_points(pos, min_dist=MIN_LAYOUT_SEPARATION, iterations=DEOVERLAP_ITERATIONS)
    return pos, labels, cpos, collab, names


def topic_edge_weights(centroids: np.ndarray) -> np.ndarray:
    cn = np.linalg.norm(centroids, axis=1, keepdims=True)
    cn = np.maximum(cn, 1e-9)
    return (centroids / cn) @ (centroids / cn).T


def write_run(
    session: Session,
    people: list[int],
    views: dict[str, dict],
    notes: str,
) -> int:
    n = len(people)
    run_id = int(
        session.execute(
            text(
                """
                INSERT INTO embedding_runs (kind, algorithm, raw_dim, point_count, is_active, notes)
                VALUES (:kind, 'atlas_v2', 2, :count, FALSE, :notes)
                RETURNING id
                """
            ),
            {"kind": KIND, "count": n * len(views), "notes": notes},
        ).scalar_one()
    )
    for view, v in views.items():
        pos, labels, cluster_pos, collab, names, topic_centroids = (
            v["pos"], v["labels"], v["cluster_pos"], v["collab"], v["names"], v["topic_centroids"]
        )
        k = max(labels) + 1
        session.execute(
            text(
                """
                INSERT INTO person_projections_2d (run_id, person_id, view, x, y, cluster_id)
                SELECT :run, :pid, :view, :x, :y, :cid
                """
            ),
            [
                {"run": run_id, "pid": pid, "view": view, "x": float(pos[i, 0]), "y": float(pos[i, 1]), "cid": int(labels[i])}
                for i, pid in enumerate(people)
                if np.isfinite(pos[i, 0]) and np.isfinite(pos[i, 1])
            ],
        )
        members = np.bincount(labels, minlength=k)
        session.execute(
            text(
                """
                INSERT INTO projection_clusters
                  (run_id, view, cluster_index, label, field_name, member_count, cx, cy, color_slot)
                VALUES (:run, :view, :cidx, :label, :field, :members, :cx, :cy, :slot)
                """
            ),
            [
                {
                    "run": run_id,
                    "view": view,
                    "cidx": int(c),
                    "label": names[c],
                    "field": names[c].split(" · ")[0],
                    "members": int(members[c]),
                    "cx": float(cluster_pos[c][0]),
                    "cy": float(cluster_pos[c][1]),
                    "slot": int(c) % 12,
                }
                for c in range(k)
            ],
        )
        tw = topic_edge_weights(topic_centroids)
        session.execute(
            text(
                """
                INSERT INTO projection_cluster_edges
                  (run_id, view, source_cluster, target_cluster, collaboration_weight, topic_weight)
                VALUES (:run, :view, :s, :t, :collab, :topic)
                """
            ),
            [
                {
                    "run": run_id,
                    "view": view,
                    "s": int(s),
                    "t": int(t),
                    "collab": float(collab[s, t]),
                    "topic": float(tw[s, t]),
                }
                for s in range(k)
                for t in range(s + 1, k)
            ],
        )
    # Two statements (NOT the tempting single `is_active = (id = :new_id)`):
    # the partial unique index on is_active checks per-row mid-UPDATE, so the
    # single-statement form can transiently hold two active rows and blow up.
    session.execute(
        text("UPDATE embedding_runs SET is_active = FALSE WHERE is_active AND id <> :new_id"),
        {"new_id": run_id},
    )
    session.execute(text("UPDATE embedding_runs SET is_active = TRUE WHERE id = :new_id"), {"new_id": run_id})
    session.commit()
    return run_id


def run_metrics(
    people: list[int],
    pos: np.ndarray,
    labels: list[int],
    collapsed: dict[tuple[int, int], float],
    field_ids: np.ndarray,
    lastnames: dict[int, str],
    view: str,
) -> list[str]:
    out: list[str] = []
    out.append(f"{view}: purity={cluster_purity(np.array(labels), field_ids):.3f}")
    out.append(f"{view}: sep={separation_ratio(pos, np.array(labels)):.2f}")
    edges = [(i, j, w) for (i, j), w in collapsed.items()]
    out.append(f"{view}: strong_edge_fidelity={strong_edge_fidelity(pos, edges):.4f}")
    out.append(f"{view}: overlap_rate={overlap_rate(pos, np.array(labels)):.3f}")
    index = {lastnames[people[i]].lower(): i for i in range(len(people))}
    gold = gold_set_distances(pos, index, [(a.lower(), b.lower()) for a, b in GOLD_PAIRS])
    for (a, b), d in gold.items():
        out.append(f"{view}: gold {a}-{b} dist={d:.4f}")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", choices=["both", "network", "topic"], default="both")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args(argv)

    started = time.time()
    with _SessionLocal() as session:
        people = load_people(session)
        n = len(people)
        edges = load_network_edges(session, people)
        collapsed = collapse_edges(edges, n)
        target_min, target_max = cluster_targets(n)
        print(f"[atlas] {n} people, {len(collapsed)} edges, targets {target_min}-{target_max}")

        lastnames: dict[int, str] = {}
        for pid, last in session.execute(text("SELECT id, lastname FROM people")).all():
            if int(pid) in people:
                lastnames[int(pid)] = (last or "")

        topic_mat, field_mat, field_names = load_topic_profiles(session, people)
        field_ids = dominant_field_from_profiles(field_mat)
        field_ids = np.clip(field_ids, 0, max(len(field_names) - 1, 0))
        notes: list[str] = []
        views: dict[str, dict] = {}

        if args.view in ("both", "network"):
            pos, labels, cpos, collab, gamma, backbone_tag = build_network_view(
                n, collapsed, target_min, target_max
            )
            centroids = cluster_topic_centroids(topic_mat, labels)
            names = [
                _cluster_field_label(c, centroids[c], field_names)
                for c in range(max(labels) + 1)
            ]
            views["network"] = {
                "pos": pos,
                "labels": labels,
                "cluster_pos": cpos,
                "collab": collab,
                "names": names,
                "topic_centroids": centroids,
            }
            notes.append(f"network gamma={gamma:.3f} backbone={backbone_tag}")
            notes += run_metrics(people, pos, labels, collapsed, field_ids, lastnames, "network")

        if args.view in ("both", "topic"):
            pos, labels, cpos, collab, names = build_topic_view(
                n, topic_mat, field_mat, field_names, collapsed, target_min, target_max
            )
            views["topic"] = {
                "pos": pos,
                "labels": labels,
                "cluster_pos": cpos,
                "collab": collab,
                "names": names,
                "topic_centroids": cluster_topic_centroids(topic_mat, labels),
            }
            notes += run_metrics(people, pos, labels, collapsed, field_ids, lastnames, "topic")

        if args.dry:
            print("[atlas] --dry: skipping DB writes")
            for line in notes:
                print(f"[atlas] {line}")
            return

        run_id = write_run(session, people, views, "; ".join(notes))
        print(f"[atlas] wrote run_id={run_id} in {time.time() - started:.1f}s")
        for line in notes:
            print(f"[atlas] {line}")


if __name__ == "__main__":
    main()
