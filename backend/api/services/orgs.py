from __future__ import annotations

"""Org node-shape helpers (pure builders; SQL lives in repositories.orgs)."""

from typing import Any

from api.id_codec import encode
from db.models import Organization

def _org_label(unit: Organization, institution: Organization | None) -> str:
    if unit.short_name:
        return unit.short_name
    if institution and institution.id != unit.id:
        for pfx in (institution.short_name, institution.name):
            if pfx and unit.name.lower().startswith(pfx.lower() + " "):
                return unit.name[len(pfx):].lstrip()
    return unit.name
def _org_sublabel(kind: str, child_count: int, roster_count: int) -> str:
    parts: list[str] = []
    if child_count:
        parts.append(f"{child_count} unit" + ("" if child_count == 1 else "s"))
    if roster_count:
        parts.append(f"{roster_count} " + ("person" if roster_count == 1 else "people"))
    return f"{kind} · " + " · ".join(parts) if parts else kind
def _org_node(unit: Organization, institution: Organization | None, sublabel: str) -> dict[str, Any]:
    return {
        "id": encode("org", unit.id),
        "kind": "org",
        "label": _org_label(unit, institution),
        "sublabel": sublabel,
        "orgKind": unit.kind,
        "institution": None,
        "rank": None,
        "stub": False,
    }
def _org_unit(
    unit: Organization,
    institution: Organization | None,
    *,
    child_count: int = 0,
    roster_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": encode("org", unit.id),
        "label": _org_label(unit, institution),
        "orgKind": unit.kind,
        "sublabel": _org_sublabel(unit.kind, child_count, roster_count),
    }


# ---------------------------------------------------------------------------
