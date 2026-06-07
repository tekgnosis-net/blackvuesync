"""playwright smoke for the /viewer page."""

from __future__ import annotations

import struct
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

pytestmark = pytest.mark.e2e


def _seed(dest: Any) -> None:
    (dest / "20260607_101500_NF.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_NR.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_NF.thm").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    (dest / "20260607_101500_N.gps").write_text(
        "[1000]$GNRMC,055056.00,A,3348.10000,S,15101.10000,E,0.000,,070626,,,A,V*06\r\n"
    )
    (dest / "20260607_101500_N.3gf").write_bytes(struct.pack(">Ihhh", 0, 130, 5, -20))


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')


def test_viewer_loads_lists_and_selects_no_js_errors(
    live_server: Any, page: Page
) -> None:
    _seed(live_server.destination)
    base = live_server.url
    _login(page, base)

    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    with page.expect_response(lambda r: "/api/viewer/recordings" in r.url):
        page.goto(f"{base}/viewer")
    expect(page.locator("#viewer-app")).to_be_visible()
    expect(page.locator(".viewer-rec").first).to_be_visible()

    with page.expect_response(lambda r: "/journey" in r.url):
        page.locator(".viewer-rec").first.click()
    expect(page.locator("#viewer-map.leaflet-container")).to_be_visible()

    page.wait_for_load_state("networkidle")
    assert errors == [], f"uncaught page errors: {errors}"
