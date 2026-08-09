"""Multimodal researcher similarity → distance matrix → 2D layout.

Stage 1 (ground truth): block similarities R, N, C, I, D combined into S, then D = 1 - S.
Stage 2 (visualization): project D (or weighted features) to 2D; pick best by fidelity metrics.

Research uses OpenAlex concept vectors today; swap in SPECTER2 paper embeddings when available.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import spearmanr
from sqlalchemy import text
from sqlalchemy.orm import Session

# Block weights (explicit, tunable).
W_NETWORK = 0.50
W_RESEARCH = 0.31
W_CAREER = 0.09
W_INSTITUTION = 0.06
W_DEPARTMENT = 0.04

# Research is skipped when concept data is too sparse to differentiate people.
MIN_DISTINCT_CONCEPTS_FOR_RESEARCH = 10
MIN_AVG_CONCEPTS_PER_PERSON = 2.0

COAUTHOR_CAP = 20.0
CAREER_YEAR_CAP = 12.0
NETWORK_FLOOR = 0.05


@dataclass(frozen=True)
class SimilarityBlocks:
    research: np.ndarray
    network: np.ndarray
    career: np.ndarray
    institution: np.ndarray
    department: np.ndarray
    weights: tuple[float, float, float, float, float] = (
        W_RESEARCH,
        W_NETWORK,
        W_CAREER,
        W_INSTITUTION,
        W_DEPARTMENT,
    )

    def combined(self) -> np.ndarray:
        wr, wn, wc, wi, wd = self.weights
        s = (
            wr * self.research
            + wn * self.network
            + wc * self.career
            + wi * self.institution
            + wd * self.department
        )
        np.fill_diagonal(s, 1.0)
        return np.clip(s, 0.0, 1.0)


def research_is_informative(session: Session, people: list[int]) -> bool:
    """False when every tagged person shares the same handful of concepts."""
    row = session.execute(
        text(
            """
            SELECT
              count(DISTINCT concept_id) AS concepts,
              count(*)::float / NULLIF(count(DISTINCT person_id), 0) AS avg_per_person
            FROM person_concepts
            WHERE person_id = ANY(:ids)
            """
        ),
        {"ids": people},
    ).one()
    concepts = int(row[0] or 0)
    avg_per_person = float(row[1] or 0.0)
    if concepts < MIN_DISTINCT_CONCEPTS_FOR_RESEARCH:
        return False
    return avg_per_person >= MIN_AVG_CONCEPTS_PER_PERSON


def resolve_block_weights(
    session: Session, people: list[int]
) -> tuple[float, float, float, float, float]:
    """Return (research, network, career, institution, department) weights summing to 1."""
    if research_is_informative(session, people):
        return (W_RESEARCH, W_NETWORK, W_CAREER, W_INSTITUTION, W_DEPARTMENT)

    # Topics not backfilled yet — coauthorship only. Career/institution/dept
    # aren't populated enough to use (career overlap today ≈ same-school tenure).
    return (0.0, 1.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class LayoutEvaluation:
    method: str
    neighbor_recall_at_10: float
    global_spearman: float
    score: float


def _normalize_similarity(sim: np.ndarray) -> np.ndarray:
    sim = np.clip(sim.astype(np.float64), 0.0, 1.0)
    np.fill_diagonal(sim, 1.0)
    return sim


def _pair_matrix(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Return meshgrid indices (i, j) for all ordered pairs."""
    idx = np.arange(n, dtype=np.int32)
    return np.meshgrid(idx, idx, indexing="ij")


def load_research_similarity(session: Session, people: list[int]) -> np.ndarray:
    """Cosine similarity of OpenAlex concept profiles (proxy for paper embeddings)."""
    n = len(people)
    if n == 0:
        return np.zeros((0, 0))
    rows = session.execute(
        text(
            """
            SELECT person_id, concept_id, COALESCE(score, 0.5)::float AS score
            FROM person_concepts
            WHERE person_id = ANY(:ids)
            """
        ),
        {"ids": people},
    ).all()
    if not rows:
        return np.eye(n)

    p_idx = {pid: i for i, pid in enumerate(people)}
    concept_ids = sorted({int(r[1]) for r in rows})
    c_idx = {cid: j for j, cid in enumerate(concept_ids)}

    mat = np.zeros((n, len(concept_ids)), dtype=np.float64)
    for person_id, concept_id, score in rows:
        i = p_idx.get(int(person_id))
        j = c_idx.get(int(concept_id))
        if i is None or j is None:
            continue
        mat[i, j] = max(mat[i, j], float(score))

    # TF-IDF downweights concepts everyone shares (e.g. generic "Economics").
    df = np.count_nonzero(mat > 0, axis=0).astype(np.float64)
    idf = np.log((n + 1.0) / (df + 1.0)) + 1.0
    mat *= idf

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-9)
    unit = mat / norms
    return _normalize_similarity(unit @ unit.T)


def load_network_similarity(session: Session, people: list[int]) -> np.ndarray:
    """Direct coauthorship + shared-coauthor overlap."""
    n = len(people)
    sim = np.zeros((n, n), dtype=np.float64)
    if n < 2:
        return np.eye(n)

    p_idx = {pid: i for i, pid in enumerate(people)}
    direct: dict[tuple[int, int], float] = {}
    rows = session.execute(
        text(
            """
            SELECT person_a, person_b, paper_count
            FROM person_coauthor_edges
            WHERE person_a = ANY(:ids) AND person_b = ANY(:ids)
            """
        ),
        {"ids": people},
    ).all()
    for a, b, count in rows:
        w = min(float(count), COAUTHOR_CAP) / COAUTHOR_CAP
        i, j = p_idx.get(int(a)), p_idx.get(int(b))
        if i is None or j is None:
            continue
        direct[(i, j)] = max(direct.get((i, j), 0.0), w)
        direct[(j, i)] = max(direct.get((j, i), 0.0), w)

    # Shared coauthors: |Γ(i) ∩ Γ(j)| / sqrt(|Γ(i)| |Γ(j)|)
    neighbors: dict[int, set[int]] = {i: set() for i in range(n)}
    for i, j in direct:
        if i != j:
            neighbors[i].add(j)

    for i in range(n):
        for j in range(i + 1, n):
            d = direct.get((i, j), direct.get((j, i), 0.0))
            if d > 0:
                # Direct coauthorship is the primary network signal.
                score = d
            else:
                shared = len(neighbors[i] & neighbors[j])
                if shared == 0:
                    continue
                denom = max((len(neighbors[i]) * len(neighbors[j])) ** 0.5, 1.0)
                score = min(shared / denom, 1.0) * 0.22
            sim[i, j] = score
            sim[j, i] = score

    return _normalize_similarity(sim)


def load_career_similarity(session: Session, people: list[int]) -> np.ndarray:
    """Years of overlapping affiliation at the same organization."""
    n = len(people)
    sim = np.zeros((n, n), dtype=np.float64)
    if n < 2:
        return np.eye(n)

    p_idx = {pid: i for i, pid in enumerate(people)}
    rows = session.execute(
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
              AND pa1.person_id = ANY(:ids)
              AND pa2.person_id = ANY(:ids)
            GROUP BY pa1.person_id, pa2.person_id
            """
        ),
        {"ids": people},
    ).all()
    for a, b, years in rows:
        i, j = p_idx.get(int(a)), p_idx.get(int(b))
        if i is None or j is None:
            continue
        score = min(float(years), CAREER_YEAR_CAP) / CAREER_YEAR_CAP
        sim[i, j] = score
        sim[j, i] = score
    return _normalize_similarity(sim)


def load_institution_similarity(session: Session, people: list[int]) -> np.ndarray:
    """Same university (org-tree ancestor), current or historical."""
    n = len(people)
    sim = np.zeros((n, n), dtype=np.float64)
    if n < 2:
        return np.eye(n)

    p_idx = {pid: i for i, pid in enumerate(people)}
    rows = session.execute(
        text(
            """
            SELECT pa.person_id, array_agg(DISTINCT u.univ_id) AS univ_ids
            FROM person_affiliations pa
            JOIN affiliation_org_assignments aoa ON aoa.affiliation_id = pa.id
            JOIN LATERAL (
              SELECT o.id AS univ_id
              FROM org_tree_current t
              JOIN organizations o ON o.id = ANY(t.ancestor_ids)
              WHERE t.organization_id = aoa.organization_id
                AND o.kind = 'university'
              LIMIT 1
            ) u ON TRUE
            WHERE pa.person_id = ANY(:ids)
            GROUP BY pa.person_id
            """
        ),
        {"ids": people},
    ).all()
    univs: dict[int, set[int]] = {i: set() for i in range(n)}
    for person_id, univ_ids in rows:
        i = p_idx.get(int(person_id))
        if i is None or not univ_ids:
            continue
        univs[i] = {int(u) for u in univ_ids if u is not None}

    for i in range(n):
        for j in range(i + 1, n):
            if not univs[i] or not univs[j]:
                continue
            overlap = len(univs[i] & univs[j])
            union = len(univs[i] | univs[j])
            if union == 0:
                continue
            score = overlap / union
            sim[i, j] = score
            sim[j, i] = score
    return _normalize_similarity(sim)


def load_department_similarity(session: Session, people: list[int]) -> np.ndarray:
    """Same primary anchor org today (department / lab level)."""
    n = len(people)
    sim = np.zeros((n, n), dtype=np.float64)
    if n < 2:
        return np.eye(n)

    p_idx = {pid: i for i, pid in enumerate(people)}
    anchor: dict[int, int] = {}
    rows = session.execute(
        text(
            """
            SELECT person_id, organization_id
            FROM person_anchor
            WHERE person_id = ANY(:ids)
              AND validity @> CURRENT_DATE
              AND is_primary
            """
        ),
        {"ids": people},
    ).all()
    for person_id, org_id in rows:
        i = p_idx.get(int(person_id))
        if i is not None:
            anchor[i] = int(org_id)

    by_org: dict[int, list[int]] = {}
    for i, org in anchor.items():
        by_org.setdefault(org, []).append(i)

    for members in by_org.values():
        for a in members:
            for b in members:
                if a != b:
                    sim[a, b] = 1.0
    return _normalize_similarity(sim)


def load_similarity_blocks(session: Session, people: list[int]) -> SimilarityBlocks:
    return SimilarityBlocks(
        research=load_research_similarity(session, people),
        network=load_network_similarity(session, people),
        career=load_career_similarity(session, people),
        institution=load_institution_similarity(session, people),
        department=load_department_similarity(session, people),
        weights=resolve_block_weights(session, people),
    )


def similarity_to_distance(sim: np.ndarray) -> np.ndarray:
    dist = 1.0 - np.clip(sim, 0.0, 1.0)
    np.fill_diagonal(dist, 0.0)
    # Symmetrize numerical noise.
    dist = 0.5 * (dist + dist.T)
    return dist


def block_feature_matrix(blocks: SimilarityBlocks) -> np.ndarray:
    """Per-person feature rows for PaCMAP — sqrt-weighted similarity blocks."""
    n = blocks.research.shape[0]
    if n == 0:
        return np.zeros((0, 0))

    wr, wn, wc, wi, wd = blocks.weights
    return np.hstack(
        [
            blocks.research * (wr**0.5),
            blocks.network * (wn**0.5),
            blocks.career * (wc**0.5),
            blocks.institution * (wi**0.5),
            blocks.department * (wd**0.5),
        ]
    )


def classical_mds(dist: np.ndarray, dim: int = 2) -> np.ndarray:
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


def layout_pacmap(features: np.ndarray, random_state: int = 42) -> np.ndarray:
    import pacmap

    n, dim = features.shape
    if n < 3:
        return classical_mds(np.ones((n, n)) - np.eye(n), dim=2)

    n_neighbors = max(5, min(30, (n - 1) // 4))
    reducer = pacmap.PaCMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        MN_ratio=0.5,
        FP_ratio=2.0,
        random_state=random_state,
        verbose=False,
    )
    return reducer.fit_transform(features.astype(np.float64))


def _euclidean_dist(pos: np.ndarray) -> np.ndarray:
    diff = pos[:, None, :] - pos[None, :, :]
    dist = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(dist, 0.0)
    return dist


def evaluate_layout(dist_truth: np.ndarray, pos: np.ndarray, k: int = 10) -> LayoutEvaluation:
    n = dist_truth.shape[0]
    k = min(k, max(n - 1, 1))
    dist_2d = _euclidean_dist(pos)

    recalls: list[float] = []
    for i in range(n):
        true_order = np.argsort(dist_truth[i])
        true_order = true_order[true_order != i][:k]
        map_order = np.argsort(dist_2d[i])
        map_order = map_order[map_order != i][:k]
        if true_order.size == 0:
            continue
        hit = len(set(true_order.tolist()) & set(map_order.tolist()))
        recalls.append(hit / k)

    recall = float(np.mean(recalls)) if recalls else 0.0

    iu = np.triu_indices(n, k=1)
    rho = 0.0
    if iu[0].size > 2:
        corr = spearmanr(dist_truth[iu], dist_2d[iu]).correlation
        rho = float(corr) if corr is not None and np.isfinite(corr) else 0.0

    score = 0.9 * recall + 0.1 * max(rho, 0.0)
    return LayoutEvaluation(
        method="",
        neighbor_recall_at_10=recall,
        global_spearman=rho,
        score=score,
    )


def refine_similarity_neighborhoods(
    pos: np.ndarray,
    sim: np.ndarray,
    steps: int = 60,
    top_k: int = 12,
    pull: float = 0.22,
) -> np.ndarray:
    """Pull each point toward its highest-similarity neighbors in 2D."""
    pos = pos.copy()
    n = pos.shape[0]
    if n < 3:
        return pos

    k = min(top_k, n - 1)
    for _ in range(steps):
        for i in range(n):
            order = np.argsort(-sim[i])
            nbrs = [j for j in order if j != i][:k]
            if not nbrs:
                continue
            weights = sim[i, nbrs]
            if float(weights.sum()) <= 0:
                continue
            centroid = np.average(pos[nbrs], axis=0, weights=weights)
            pos[i] += pull * (centroid - pos[i])
    return pos


def choose_layout(
    dist: np.ndarray,
    features: np.ndarray,
    random_state: int = 42,
) -> tuple[np.ndarray, str, list[LayoutEvaluation]]:
    """Benchmark MDS vs PaCMAP; return best 2D coordinates."""
    candidates: list[tuple[str, np.ndarray]] = []

    mds2 = classical_mds(dist, dim=2)
    candidates.append(("metric_mds", mds2))

    if features.shape[0] >= 3 and features.shape[1] >= 2:
        try:
            pac = layout_pacmap(features, random_state=random_state)
            candidates.append(("pacmap", pac))
        except Exception:
            pass

    if dist.shape[0] >= 3:
        mds_hd = classical_mds(dist, dim=min(15, dist.shape[0] - 1))
        try:
            pac_hd = layout_pacmap(mds_hd, random_state=random_state)
            candidates.append(("pacmap_mds15", pac_hd))
        except Exception:
            pass

    evals: list[LayoutEvaluation] = []
    best_name = "metric_mds"
    best_pos = mds2
    best_score = -1.0

    for name, pos in candidates:
        ev = evaluate_layout(dist, pos, k=10)
        evals.append(
            LayoutEvaluation(
                method=name,
                neighbor_recall_at_10=ev.neighbor_recall_at_10,
                global_spearman=ev.global_spearman,
                score=ev.score,
            )
        )
        if ev.score > best_score:
            best_score = ev.score
            best_name = name
            best_pos = pos

    return best_pos, best_name, evals
