from __future__ import annotations

"""Research-impact scoring helpers (pure; no DB access)."""

import math

_RANK_IMPACT: dict[str, float] = {
    "dean": 1.0,
    "executive": 0.98,
    "board_member": 0.96,
    "department_head": 0.94,
    "principal_investigator": 0.92,
    "group_leader": 0.9,
    "full_professor": 0.88,
    "emeritus_professor": 0.86,
    "associate_professor": 0.78,
    "assistant_professor": 0.68,
    "adjunct_professor": 0.55,
    "lecturer": 0.5,
    "research_scientist": 0.48,
    "staff_scientist": 0.46,
    "research_fellow": 0.42,
    "postdoc": 0.32,
    "phd_student": 0.18,
    "masters_student": 0.12,
    "undergraduate": 0.08,
    "visiting_student": 0.15,
    "visiting": 0.4,
    "technician": 0.25,
    "engineer": 0.28,
}


# ---------------------------------------------------------------------------
# helpers — shape-only, no DB access
# ---------------------------------------------------------------------------
def _raw_person_impact(
    citation_count: int,
    publication_count: int,
    rank: str | None,
) -> float:
    """Bibliometric impact for dot size — rank only nudges when output exists."""
    cite_s = math.log1p(max(0, citation_count)) * 4.0
    pub_s = math.log1p(max(0, publication_count)) * 2.5
    bibliometric = cite_s + pub_s
    if bibliometric <= 0:
        return 0.0
    rank_s = _RANK_IMPACT.get(rank or "", 0.4)
    return bibliometric + rank_s * 0.6
def _normalize_impacts(raw: dict[str, float]) -> dict[str, float]:
    if not raw:
        return {}
    max_raw = max(raw.values())
    if max_raw <= 0:
        return {key: 0.0 for key in raw}
    return {key: min(1.0, value / max_raw) for key, value in raw.items()}
