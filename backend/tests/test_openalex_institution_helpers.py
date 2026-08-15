"""Pure helper tests for the OpenAlex institution backfill.

These protect two real-data regressions found during the live backfill:

* long org names were being truncated to four words and displayed as labels
* OpenAlex lineages were re-parenting existing university roots
* hospital/company roots were being classified as universities
"""

from __future__ import annotations

from scripts.backfill.openalex_institutions import _infer_org_kind, _short_name


def test_short_name_returns_none_for_long_name():
    assert _short_name("University of California, San Francisco") is None


def test_short_name_keeps_genuine_short_names():
    assert _short_name("MIT") == "MIT"
    assert _short_name("Demo Lab") == "Demo Lab"


def test_infer_org_kind_keeps_higher_education_roots_as_university():
    assert _infer_org_kind(
        {"type": "education", "display_name": "University of Coimbra"},
        is_root=True,
        is_leaf=True,
    ) == "university"
    assert _infer_org_kind(
        {"type": "education", "display_name": "ETH Zurich"},
        is_root=True,
        is_leaf=True,
    ) == "university"


def test_infer_org_kind_does_not_call_hospital_a_university():
    assert _infer_org_kind(
        {"type": "education", "display_name": "Cleveland Clinic"},
        is_root=True,
        is_leaf=True,
    ) == "institute"


def test_infer_org_kind_non_education_root_defaults_to_institute():
    assert _infer_org_kind(
        {"type": "other", "display_name": "Anthrologica"},
        is_root=True,
        is_leaf=True,
    ) == "institute"


def test_openalex_name_helpers_unescape_html_entities():
    from scripts.backfill.publications import (
        _normalize_openalex_name,
        _split_display_name,
    )

    assert _normalize_openalex_name("Andr&eacute;s Escalera") == "andrés escalera"
    first, middle, last = _split_display_name("Ana&iuml;s Le Rhun")
    assert first == "Anaïs"
    assert middle == "Le"
    assert last == "Rhun"
