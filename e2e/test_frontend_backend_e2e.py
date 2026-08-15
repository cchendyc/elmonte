"""End-to-end tests through a real browser against the real stack.

These tests use the live Vite dev server and the live FastAPI/Postgres stack
from ``.env``. They exercise the actual rendered UI, the Apollo client, the
Vite proxy, GraphQL execution, and the Neon database in one path.
"""

from __future__ import annotations

import json
import os
import urllib.request

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:5173").rstrip("/")
E2E_API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8000").rstrip("/")


def graphql(query: str, variables: dict | None = None) -> dict:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        f"{E2E_API_URL}/api/graphql",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read())
    if "errors" in body:
        raise AssertionError(f"GraphQL errors: {body['errors']}")
    return body["data"]


def featured_person() -> dict:
    """Pick a real, well-populated person at runtime.

    The tests intentionally do not hardcode any researcher name from the live
    database; they select a high-impact projected person with ORCID,
    publications, and an award, then assert the UI agrees with the API.
    """
    projection = graphql(
        '{ projection(view: "topic") { points { id label impact } } }'
    )["projection"]
    candidates = sorted(
        (point for point in projection["points"] if point["label"]),
        key=lambda point: point["impact"],
        reverse=True,
    )[:10]

    fallback = None
    query = (
        "query Person($id: ID" + chr(33) + ") {"
        " person(id: $id) {"
        " id label role institution orcid"
        " awards { name }"
        " publications { id }"
        " }"
        "}"
    )
    for point in candidates:
        data = graphql(query, {"id": point["id"]})
        profile = data["person"]
        if not profile:
            continue
        if profile["publications"] and fallback is None:
            fallback = profile
        if profile["publications"] and profile["awards"] and profile["orcid"]:
            return profile

    assert fallback is not None, "live database has no usable featured person"
    return fallback


def search_for(page, query: str) -> None:
    input_box = page.get_by_role("combobox", name="Search people and organization units")
    input_box.fill(query)
    page.locator(".search-results").wait_for(state="visible")
    # Wait for the actual GraphQL response rather than the optimistic
    # "Searching…" panel; either hits or an explicit empty state are terminal.
    page.wait_for_timeout(500)
    results = page.locator(".search-result")
    empty = page.locator(".search-results__empty")
    page.wait_for_timeout(500)
    try:
        results.first.wait_for(state="visible", timeout=7_000)
    except Exception:
        empty.wait_for(state="visible", timeout=1_000)


def test_home_loads_with_real_atlas_and_no_errors(page, open_home):
    check = open_home()
    expect = page.locator
    page.get_by_test_id("investigation-trace").wait_for(state="visible")
    page.locator(".people-scatter").wait_for(state="visible")
    body = page.locator("body").inner_text()
    assert "Select university" in body
    assert "researchers" in body.lower()
    check()


def test_search_featured_person_opens_full_profile(page, open_home):
    featured = featured_person()
    check = open_home()
    search_for(page, featured["label"])
    hit = page.locator(".search-result").filter(has_text=featured["label"]).first
    assert hit.is_visible()

    # The search card itself must now carry disambiguation context.
    hit_text = hit.inner_text()
    if featured["institution"]:
        assert featured["institution"] in hit_text

    hit.click()
    panel = page.locator(".person-profile-pane")
    panel.wait_for(state="visible")
    page.get_by_text(featured["label"], exact=True).first.wait_for()
    if featured["awards"]:
        page.get_by_text(featured["awards"][0]["name"]).wait_for(timeout=10_000)
        assert featured["awards"][0]["name"] in panel.inner_text()
    if featured["institution"]:
        assert featured["institution"] in panel.inner_text()
    papers_shown = min(len(featured["publications"]), 100)
    assert f"Papers ({papers_shown})" in panel.inner_text()
    check()


def test_two_character_search_is_not_empty(page, open_home):
    featured = featured_person()
    first_token = next(
        (part for part in featured["label"].split() if len(part) >= 2),
        featured["label"][:2],
    )
    check = open_home()
    search_for(page, first_token[:2])
    assert page.locator(".search-result").count() > 0
    check()


def test_gdpr_json_export_downloads_real_person(page, open_home):
    featured = featured_person()
    check = open_home()
    search_for(page, featured["label"])
    page.locator(".search-result").filter(has_text=featured["label"]).first.click()
    panel = page.locator(".person-profile-pane")
    panel.wait_for(state="visible")

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Download data (JSON)").click()
    download = download_info.value
    path = download.path()
    assert path is not None
    payload = json.loads(path.read_text())
    assert payload["label"] == featured["label"]
    if featured["orcid"]:
        orcid = next(
            (item for item in payload["externalIdentifiers"] if item["provider"] == "orcid"),
            None,
        )
        assert orcid is not None and orcid["externalId"] == featured["orcid"]
    assert payload["publications"]
    check()


def test_institution_page_renders_visible_org_chart_without_flow_warnings(page, track_frontend_errors):
    check = track_frontend_errors(page)
    data = graphql(
        '{ universities { id label childCount rosterCount } }'
    )
    unit = next(
        (u for u in data["universities"] if u["childCount"] and u["rosterCount"]),
        data["universities"][0],
    )

    page.goto(f"{E2E_BASE_URL}/institution/{unit['id']}", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.locator(".institution-profile").wait_for(state="visible")
    profile_text = page.locator(".institution-profile").inner_text()
    assert unit["label"] in profile_text
    assert "PEOPLE IN SUBTREE" in profile_text.upper()

    flow = page.locator(".org-chart .react-flow")
    box = flow.bounding_box()
    assert box is not None and box["height"] > 100, (
        "React Flow must have a real (non-zero) viewport on institution pages"
    )

    # The previous min-height-only container produced exactly this warning.
    assert "parent container needs a width and a height" not in profile_text
    page.locator(".react-flow__node").first.wait_for(state="visible")
    check(allow_warnings=False)


def test_data_tables_live_directory_rows(page):
    page.goto(f"{E2E_BASE_URL}/data", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.locator(".data-tables").wait_for(state="visible")
    table = page.locator(".data-tables__table").first
    assert table.locator("tbody tr").count() > 0
    body = table.inner_text()
    projection = graphql(
        '{ projection(view: "topic") { points { label } } }'
    )["projection"]
    assert any(point["label"] in body for point in projection["points"][:50])

    page.get_by_role("tab", name="Organizations").click()
    assert page.locator(".data-tables__table").first.locator("tbody tr").count() > 0
