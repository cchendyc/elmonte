from __future__ import annotations

"""Small naming / role helpers (no DB access)."""


def _full_name(first: str | None, middle: str | None, last: str | None) -> str:
    parts = [p for p in (first, middle, last) if p]
    return " ".join(parts) if parts else "(unnamed)"
def _person_role(title: str | None) -> str | None:
    return title.strip() if title and title.strip() else None
