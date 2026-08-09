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
    (re.compile(r"emeritus|emerita", re.I), "emeritus_professor"),
    (re.compile(r"adjunct", re.I), "adjunct_professor"),
    (re.compile(r"assistant\s+professor", re.I), "assistant_professor"),
    (re.compile(r"associate\s+professor", re.I), "associate_professor"),
    (re.compile(r"lecturer|instructor", re.I), "lecturer"),
    (re.compile(r"post[-\s]?doc(toral)?|postdoctoral", re.I), "postdoc"),
    (
        re.compile(r"predoctoral|predoc|research\s+fellow|senior\s+fellow", re.I),
        "research_fellow",
    ),
    (re.compile(r"research\s+scientist|staff\s+scientist", re.I), "research_scientist"),
    (re.compile(r"\bprofessor\b|\bprof\b", re.I), "full_professor"),
    (re.compile(r"ph\.?d\.?\s+student|doctoral\s+student", re.I), "phd_student"),
    (re.compile(r"visiting\s+student", re.I), "visiting_student"),
    (re.compile(r"\bph\.?d\.?\b", re.I), "phd_student"),
    (re.compile(r"\bm\.?a\.?\b|\bmasters?\b", re.I), "masters_student"),
    (re.compile(r"\bb\.?a\.?\b|\bb\.?s\.?\b|undergraduate", re.I), "undergraduate"),
    (re.compile(r"principal\s+investigator|\bpi\b", re.I), "principal_investigator"),
    (re.compile(r"board\s+member|advisory\s+board", re.I), "board_member"),
    (re.compile(r"\bdean\b", re.I), "dean"),
]


def classify_position_rank(title: str | None) -> str | None:
    if not title:
        return None
    for pattern, rank in _RANK_PATTERNS:
        if pattern.search(title):
            if rank in _VALID_RANKS:
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
