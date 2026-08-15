"""Map free-text job titles to `position_rank` enum values.

The Postgres `position_rank` enum is the source of truth here. The frontend
chart uses a coarser `visiting` filter bucket, but that label is not stored in
the database — visiting appointments are captured via `affiliation_kind`.
"""

from __future__ import annotations

import re

from db.models.enums import POSITION_RANK_VALUES

_VALID_RANKS = set(POSITION_RANK_VALUES)

# Ordered longest / most-specific first.
_RANK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"emeritus|emerita", re.IGNORECASE), "emeritus_professor"),
    (re.compile(r"adjunct", re.IGNORECASE), "adjunct_professor"),
    (re.compile(r"assistant\s+professor", re.IGNORECASE), "assistant_professor"),
    (re.compile(r"associate\s+professor", re.IGNORECASE), "associate_professor"),
    (re.compile(r"lecturer|instructor", re.IGNORECASE), "lecturer"),
    (re.compile(r"post[-\s]?doc(toral)?|postdoctoral", re.IGNORECASE), "postdoc"),
    (
        re.compile(r"predoctoral|predoc|research\s+fellow|senior\s+fellow", re.IGNORECASE),
        "research_fellow",
    ),
    (re.compile(r"research\s+scientist|staff\s+scientist", re.IGNORECASE), "research_scientist"),
    (re.compile(r"\bprofessor\b|\bprof\b", re.IGNORECASE), "full_professor"),
    (re.compile(r"ph\.?d\.?\s+student|doctoral\s+student", re.IGNORECASE), "phd_student"),
    (re.compile(r"visiting\s+student", re.IGNORECASE), "visiting_student"),
    (re.compile(r"\bph\.?d\.?\b", re.IGNORECASE), "phd_student"),
    (re.compile(r"\bm\.?a\.?\b|\bmasters?\b", re.IGNORECASE), "masters_student"),
    (re.compile(r"\bb\.?a\.?\b|\bb\.?s\.?\b|undergraduate", re.IGNORECASE), "undergraduate"),
    (re.compile(r"principal\s+investigator|\bpi\b", re.IGNORECASE), "principal_investigator"),
    (re.compile(r"board\s+member|advisory\s+board", re.IGNORECASE), "board_member"),
    (re.compile(r"\bdean\b", re.IGNORECASE), "dean"),
]


def classify_position_rank(title: str | None) -> str | None:
    if not title:
        return None
    for pattern, rank in _RANK_PATTERNS:
        if pattern.search(title) and rank in _VALID_RANKS:
            return rank
    return None


def infer_affiliation_kind(title: str, *, section: str) -> str:
    lowered = title.lower()
    if section == "education":
        return "education"
    if "founder" in lowered or "co-founder" in lowered:
        return "founding"
    if "board" in lowered or "director" in lowered and "managing" not in lowered:
        return "governance"
    if "visiting" in lowered:
        return "visiting"
    if any(
        token in lowered
        for token in ("ph.d", "phd", "m.a.", "m.s.", "b.a.", "b.s.", "degree", "student")
    ):
        return "education"
    return "employment"
