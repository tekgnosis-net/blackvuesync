"""playwright smoke for the live /logs viewer."""

from __future__ import annotations

import logging
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


def test_logs_stream_filter_pause_clear(live_server: Any, page: Page) -> None:  # type: ignore[no-untyped-def]
    base = live_server.url
    _login(page, base)
    page.goto(f"{base}/logs")
    expect(page.locator(".logs-page")).to_be_visible()

    live_server.app.log_buffer.emit(
        logging.LogRecord(
            "blackvuesync", logging.ERROR, "p.py", 1, "boom-token-xyz", None, None
        )
    )
    row = page.locator(".log-row", has_text="boom-token-xyz")
    expect(row).to_be_visible(timeout=5000)

    live_server.app.log_buffer.emit(
        logging.LogRecord(
            "blackvuesync", logging.DEBUG, "p.py", 1, "quiet-debug-line", None, None
        )
    )
    debug_row = page.locator(".log-row", has_text="quiet-debug-line")
    expect(debug_row).to_be_visible(timeout=5000)

    page.click('.logs-level-btn[data-level="WARNING"]')
    expect(debug_row).to_be_hidden()
    expect(row).to_be_visible()

    page.click('.logs-level-btn[data-level="DEBUG"]')
    page.fill(".logs-search", "boom-token")
    expect(row).to_be_visible()
    expect(debug_row).to_be_hidden()

    page.fill(".logs-search", "")
    page.click("text=Clear")
    expect(page.locator(".log-row")).to_have_count(0)
