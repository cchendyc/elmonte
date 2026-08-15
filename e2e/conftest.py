"""Playwright fixtures for the frontend → backend E2E suite.

Expected local stack (the one ``npm run dev`` starts):

    * frontend  http://localhost:5173  (Vite dev server, proxies /api)
    * backend   http://127.0.0.1:8000  (FastAPI, real .env database)

Override the URLs with ``E2E_BASE_URL`` / ``E2E_API_URL`` when testing a
deployed preview. Run: ``python3 -m pytest e2e -q``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:5173").rstrip("/")
E2E_API_URL = os.environ.get("E2E_API_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    yield page
    page.close()


@pytest.fixture
def assert_no_frontend_errors():
    def check(page: Page, *, allow_warnings: bool = True) -> None:
        # The context object below is injected by the page fixture helper in
        # each test through `track_frontend_errors`.
        raise AssertionError("use track_frontend_errors(page) before navigation")

    return check


@pytest.fixture
def track_frontend_errors():
    """Attach console/pageerror collectors to a page.

    Returns a callable that asserts no ``console.error`` or uncaught page
    error was emitted since the collector was attached.
    """

    def attach(page: Page):
        errors: list[str] = []
        warnings: list[str] = []

        def on_console(message):
            if message.type == "error":
                errors.append(f"console.error: {message.text}")
            elif message.type == "warning":
                warnings.append(f"console.warning: {message.text}")

        def on_pageerror(error):
            errors.append(f"pageerror: {error}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)

        def check(*, allow_warnings: bool = True):
            assert errors == [], "frontend emitted errors:\n" + "\n".join(errors)
            if not allow_warnings:
                assert warnings == [], "frontend emitted warnings:\n" + "\n".join(warnings)

        check.warnings = warnings
        return check

    return attach


@pytest.fixture(scope="session", autouse=True)
def assert_stack_is_up():
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{E2E_API_URL}/api/health", timeout=5) as resp:
            assert resp.status == 200
    except Exception as exc:  # noqa: BLE001 - clear startup error for operator
        pytest.fail(
            f"Backend not reachable at {E2E_API_URL}/api/health — start it with "
            f"`npm run dev:api` before running E2E tests. ({exc})"
        )

    try:
        with urllib.request.urlopen(E2E_BASE_URL, timeout=5) as resp:
            assert resp.status == 200
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"Frontend not reachable at {E2E_BASE_URL} — start it with "
            f"`npm run dev:web` before running E2E tests. ({exc})"
        )


@pytest.fixture
def open_home(page, track_frontend_errors):
    def _open(query: str = ""):
        check = track_frontend_errors(page)
        page.goto(f"{E2E_BASE_URL}/{query}", wait_until="networkidle")
        page.wait_for_timeout(300)
        return check

    return _open
