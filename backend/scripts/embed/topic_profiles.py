"""Load per-person OpenAlex topic profiles from the DB.

Returns TF-IDF-weighted, L2-normalized matrices over topics and fields.
Zero rows (people with no topics) stay zero — callers route them to the
Unknown cluster.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session


def load_topic_profiles(
    session: Session, people: list[int]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """→ (topic_profile n×T, field_profile n×F, field_names)."""
    n = len(people)
    if n == 0:
        return np.zeros((0, 0)), np.zeros((0, 0)), []
    rows = session.execute(
        text(
            """
            SELECT pt.person_id, pt.topic_id, pt.score, t.field_name
            FROM person_topics pt
            JOIN topics t ON t.openalex_topic_id = pt.topic_id
            WHERE pt.person_id = ANY(:ids)
            """
        ),
        {"ids": people},
    ).all()
    if not rows:
        return np.zeros((n, 0)), np.zeros((n, 0)), []

    p_idx = {pid: i for i, pid in enumerate(people)}
    topic_ids = sorted({r[1] for r in rows})
    t_idx = {tid: j for j, tid in enumerate(topic_ids)}
    mat = np.zeros((n, len(topic_ids)), dtype=np.float64)
    for person_id, tid, score, _field in rows:
        i, j = p_idx.get(int(person_id)), t_idx.get(tid)
        if i is not None and j is not None:
            mat[i, j] = max(mat[i, j], float(score))

    # IDF downweights ubiquitous topics (same trick as load_research_similarity).
    df = np.count_nonzero(mat > 0, axis=0).astype(np.float64)
    idf = np.log((n + 1.0) / (df + 1.0)) + 1.0
    mat *= idf
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-9)
    mat = mat / norms

    fields = sorted({r[3] for r in rows if r[3]})
    f_idx = {f: j for j, f in enumerate(fields)}
    fmat = np.zeros((n, len(fields)), dtype=np.float64)
    for person_id, _tid, score, field in rows:
        i, j = p_idx.get(int(person_id)), f_idx.get(field)
        if i is not None and j is not None and field:
            fmat[i, j] += float(score)
    fn = np.linalg.norm(fmat, axis=1, keepdims=True)
    fn = np.maximum(fn, 1e-9)
    fmat = fmat / fn
    return mat, fmat, fields
