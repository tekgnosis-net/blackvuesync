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


_GPS_A = "[1000]$GNRMC,055056.00,A,1000.00000,N,02000.00000,E,1.0,,070626,,,A*00\r\n"
_GPS_B = "[1000]$GNRMC,055056.00,A,5000.00000,N,02000.00000,E,1.0,,070626,,,A*00\r\n"


def test_rear_only_recording_hides_rear_player(live_server: Any, page: Page) -> None:
    dest = live_server.destination
    (dest / "20260607_101500_NR.mp4").write_bytes(b"\x00")
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/viewer")
    with page.expect_response(lambda r: "/journey" in r.url):
        page.locator(".viewer-rec").first.click()
    front = page.locator("#viewer-front")
    expect(front).to_have_attribute("src", "/media/20260607_101500_NR.mp4")
    expect(page.locator("#viewer-rear")).to_be_hidden()
    assert page.locator("#viewer-rear").get_attribute("src") is None


def test_upload_flag_recording_uses_real_filenames(
    live_server: Any, page: Page
) -> None:
    dest = live_server.destination
    (dest / "20260607_101500_NFL.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_NF.thm").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/viewer")
    row = page.locator(".viewer-rec").first
    expect(row.locator("img")).to_have_attribute("src", "/media/20260607_101500_NF.thm")
    with page.expect_response(lambda r: "/journey" in r.url):
        row.click()
    expect(page.locator("#viewer-front")).to_have_attribute(
        "src", "/media/20260607_101500_NFL.mp4"
    )


def test_stale_telemetry_is_not_merged_into_next_selection(
    live_server: Any, page: Page
) -> None:
    dest = live_server.destination
    # 10 minutes apart: two separate journeys
    (dest / "20260607_101500_NF.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_N.gps").write_text(_GPS_A)
    (dest / "20260607_102500_NF.mp4").write_bytes(b"\x00")
    (dest / "20260607_102500_N.gps").write_text(_GPS_B)
    _login(page, live_server.url)

    held: list[Any] = []
    page.route("**/20260607_101500_N/gps", lambda route: held.append(route))
    page.goto(f"{live_server.url}/viewer")
    rows = page.locator(".viewer-rec")
    expect(rows).to_have_count(2)

    # newest first: row 1 is A (10:15), row 0 is B (10:25)
    with page.expect_request("**/20260607_101500_N/gps"):
        rows.nth(1).click()
    with page.expect_response("**/20260607_102500_N/gps"):
        rows.nth(0).click()
    page.wait_for_function("viewer.track.length === 1")

    # A's gps arrives late; it must not land in B's track
    with page.expect_response("**/20260607_101500_N/gps"):
        held[0].fulfill(
            status=200,
            content_type="application/json",
            body=('{"points": [{"t": 0, "lat": 10.0, "lon": 20.0, "speed": 1.0}]}'),
        )
    page.wait_for_timeout(200)
    lats = page.evaluate("viewer.track.map((p) => p.lat)")
    assert lats == [50.0]


def test_telemetry_error_is_shown(live_server: Any, page: Page) -> None:
    _seed(live_server.destination)
    _login(page, live_server.url)
    page.route(
        "**/api/viewer/recordings/*/gps",
        lambda r: r.fulfill(status=500, content_type="text/html", body="boom"),
    )
    page.goto(f"{live_server.url}/viewer")
    with page.expect_response(lambda r: "/gps" in r.url):
        page.locator(".viewer-rec").first.click()
    error = page.locator("#viewer-error")
    expect(error).to_be_visible()
    expect(error).to_contain_text("GPS HTTP 500")


def test_marker_lookup_stays_in_segment_and_offsets_use_video_duration(
    live_server: Any, page: Page
) -> None:
    _login(page, live_server.url)
    with page.expect_response(lambda r: "/api/viewer/recordings" in r.url):
        page.goto(f"{live_server.url}/viewer")
    result = page.evaluate(
        """() => {
          viewer.resetTelemetry();
          viewer.track = [
            { seg: 0, t: 59, lat: 1, lon: 1 },
            { seg: 1, t: 5, lat: 2, lon: 2 },
          ];
          const out = {
            inSeg1: viewer.nearest(1, 0).lat,
            inSeg0: viewer.nearest(0, 80).lat,
            none: viewer.nearest(2, 0),
            fallback: viewer.segmentOffset(1),
          };
          viewer.spans[0] = 45;
          out.span = viewer.segmentOffset(1);
          viewer.durations[0] = 30.5;
          out.video = viewer.segmentOffset(2);
          return out;
        }"""
    )
    assert result == {
        "inSeg1": 2,
        "inSeg0": 1,
        "none": None,
        "fallback": 60,
        "span": 45,
        "video": 30.5 + 60,
    }
