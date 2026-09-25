"""playwright: page scripts surface api failures and send expired sessions to /login."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, Route, expect  # noqa: E402

pytestmark = pytest.mark.e2e

_AUTH_REQUIRED = json.dumps(
    {"error": "authentication required", "code": "AUTH_REQUIRED"}
)


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')
    expect(page).not_to_have_url(re.compile(r"/login"))


def _unauthorized(route: Route) -> None:
    route.fulfill(status=401, content_type="application/json", body=_AUTH_REQUIRED)


def _server_error(route: Route) -> None:
    route.fulfill(status=500, content_type="text/html", body="<h1>boom</h1>")


def test_sync_now_400_shows_error(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route(
        "**/api/sync/now",
        lambda r: r.fulfill(status=400, content_type="text/html", body="bad csrf"),
    )
    page.click('[data-action="sync-now"]')
    error = page.locator("[data-action-error]")
    expect(error).to_be_visible()
    expect(error).to_contain_text("Sync could not start: HTTP 400")
    expect(page.locator("body")).to_have_attribute("data-state", "idle")


def test_pause_json_error_message_is_shown(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route(
        "**/api/schedule/pause",
        lambda r: r.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"error": "disk full", "code": "X"}),
        ),
    )
    page.click('[data-action="pause"]')
    expect(page.locator("[data-action-error]")).to_contain_text(
        "Pause failed: disk full"
    )


def test_sync_now_401_redirects_to_login(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route("**/api/sync/now", _unauthorized)
    page.click('[data-action="sync-now"]')
    expect(page).to_have_url(re.compile(r"/login\?next=%2F$"))


def test_settings_save_401_redirects_to_login(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.route("**/api/settings/logging", _unauthorized)
    page.click('[data-section-nav="logging"]')
    page.click('[data-save="logging"]')
    expect(page).to_have_url(re.compile(r"/login\?next=%2Fsettings$"))


def test_settings_save_500_shows_error(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.route("**/api/settings/logging", _server_error)
    page.click('[data-section-nav="logging"]')
    page.click('[data-save="logging"]')
    expect(page.locator('[data-errors="logging"]')).to_contain_text("HTTP 500")


def test_wrong_current_password_is_not_a_logout(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.click('[data-section-nav="auth"]')
    page.click('[data-action="change-password"]')
    dialog = page.locator('dialog[data-dialog="password"]')
    dialog.locator('[data-field="current_password"]').fill("wrong-password")
    dialog.locator('[data-field="new_password"]').fill("pw-5678-test-new")
    dialog.locator('[data-field="confirm_password"]').fill("pw-5678-test-new")
    dialog.locator(".button-primary").click()
    expect(page.locator('[data-errors="password"]')).to_contain_text("incorrect")
    expect(page).to_have_url(re.compile(r"/settings$"))


def test_stats_401_redirects_to_login(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route("**/api/stats/series*", _unauthorized)
    page.goto(f"{live_server.url}/stats")
    expect(page).to_have_url(re.compile(r"/login\?next=%2Fstats$"))


def test_stats_500_shows_error(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route("**/api/stats/series*", _server_error)
    page.goto(f"{live_server.url}/stats")
    error = page.locator("[data-stats-error]")
    expect(error).to_be_visible()
    expect(error).to_contain_text("HTTP 500")


def test_viewer_401_redirects_to_login(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.route("**/api/viewer/recordings", _unauthorized)
    page.goto(f"{live_server.url}/viewer")
    expect(page).to_have_url(re.compile(r"/login\?next=%2Fviewer$"))


def test_viewer_redirected_to_login_page_is_detected(
    live_server: Any, page: Page
) -> None:
    # a legacy 302 to /login that fetch follows must still count as logged out
    _login(page, live_server.url)
    page.route(
        "**/api/viewer/recordings",
        lambda r: r.fulfill(status=302, headers={"Location": "/login?next=/x"}),
    )
    page.goto(f"{live_server.url}/viewer")
    expect(page).to_have_url(re.compile(r"/login\?next=%2Fviewer$"))
