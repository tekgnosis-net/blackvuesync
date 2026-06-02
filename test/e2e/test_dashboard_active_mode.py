"""playwright smoke: the idle -> running -> complete handoff and the stop modal."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

# deselected from default runs; only the dedicated ci e2e job runs `-m e2e`.
pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')
    expect(page.locator("body")).to_have_attribute("data-state", "idle")


def test_sync_handoff_idle_running_complete(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    pub = live_server.app.progress_publisher

    # simulate a sync starting from the server side
    pub.begin_job(2)
    pub.start_file("20230101_120000_NF.mp4", "mp4", 1000)
    pub.update_bytes(500, 1000)
    expect(page.locator("body")).to_have_attribute(
        "data-state", "running", timeout=8000
    )
    expect(page.locator("#active-hero")).to_be_visible()

    # finish the job -> hero shows complete, then reverts to idle after linger
    pub.finish_file(success=True)
    pub.end_job(success=True)
    expect(page.locator("body")).to_have_attribute(
        "data-state", "complete", timeout=8000
    )


def test_stop_modal_confirm_posts_stop(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    pub = live_server.app.progress_publisher
    pub.begin_job(1)
    expect(page.locator("body")).to_have_attribute(
        "data-state", "running", timeout=8000
    )

    page.click('[data-action="stop"]')
    dialog = page.locator("dialog.modal")
    expect(dialog).to_be_visible()

    with page.expect_response("**/api/sync/stop") as resp_info:
        dialog.locator(".button-primary").click()
    assert resp_info.value.status in (202, 404)
