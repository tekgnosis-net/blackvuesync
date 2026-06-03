"""playwright smoke: settings section nav, save->toast, validation, password dialog."""

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


def test_section_nav_and_save_toast(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    # switch to logging (immediate tier) and save -> green toast
    page.click('[data-section-nav="logging"]')
    expect(page.locator('[data-pane="logging"]')).to_be_visible()
    page.click('[data-save="logging"]')
    expect(page.locator('[data-toast="logging"]')).to_contain_text(
        "Saved", timeout=5000
    )


def test_validation_error_list(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.click('[data-section-nav="web"]')
    port = page.locator('[data-pane="web"] [data-field="port"]')
    port.fill("0")  # invalid: must be 1..65535
    page.click('[data-save="web"]')
    expect(page.locator('[data-errors="web"]')).to_contain_text("port", timeout=5000)


def test_password_dialog_opens(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.click('[data-section-nav="auth"]')
    page.click('[data-action="change-password"]')
    expect(page.locator('dialog[data-dialog="password"]')).to_be_visible()
