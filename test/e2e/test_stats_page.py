"""playwright smoke for the /stats page."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')


def test_stats_page_loads_switches_range_no_js_errors(
    live_server: Any, page: Page
) -> None:  # type: ignore[no-untyped-def]
    base = live_server.url
    _login(page, base)

    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    # the initial page load triggers the 7d series fetch; wait for it so the
    # first chart render has run before interacting.
    with page.expect_response(lambda r: "/api/stats/series" in r.url):
        page.goto(f"{base}/stats")

    expect(page.locator(".stats-page")).to_be_visible()
    expect(page.locator('.stats-range-btn[data-range="30d"]')).to_be_visible()
    expect(page.locator('[data-chart="disk"]')).to_be_visible()

    # clicking 30d triggers a new series fetch; wait for that specific response.
    with page.expect_response(lambda r: "range=30d" in r.url):
        page.click('.stats-range-btn[data-range="30d"]')

    expect(page.locator('.stats-range-btn.active[data-range="30d"]')).to_be_visible()

    # let the synchronous renderCharts() that runs after the response settle,
    # then assert no uncaught js errors fired at any point.
    page.wait_for_load_state("networkidle")
    assert errors == [], f"uncaught page errors: {errors}"
