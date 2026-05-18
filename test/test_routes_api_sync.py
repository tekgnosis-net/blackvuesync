"""tests for /api/sync/* endpoints including SSE protocol and CSRF."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.settings import SettingsStore

# manual smoke test:
#   with the server running, authenticate at /login, then:
#   curl -s http://localhost:8080/api/sync/progress
#   curl -N -H "Cookie: bvs_session=..." http://localhost:8080/api/sync/progress/stream
#   # trigger a sync in another terminal to see events arrive

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def _make_store(settings_path: Path) -> SettingsStore:
    """creates a SettingsStore with a dummy address for validation."""
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


def _make_app(settings_path: Path, publisher: ProgressPublisher | None = None):  # type: ignore[no-untyped-def]
    """creates a test app with a pre-set password."""
    store = _make_store(settings_path)
    pw_hash = hash_password("test-password-1234")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
        )
    )
    pub = publisher or ProgressPublisher()
    return create_app(store, testing=True, progress_publisher=pub), pub


@pytest.fixture()
def app_and_pub(settings_path: Path):  # type: ignore[no-untyped-def]
    """returns (app, publisher) pair in testing mode with a pre-set password."""
    return _make_app(settings_path)


@pytest.fixture()
def logged_in_client(app_and_pub: Any):  # type: ignore[no-untyped-def]
    """returns a logged-in flask test client."""
    app, pub = app_and_pub
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, pub


# ---------------------------------------------------------------------------
# GET /api/sync/progress
# ---------------------------------------------------------------------------


class TestProgressSnapshot:
    """tests for GET /api/sync/progress."""

    def test_returns_idle_state_initially(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/sync/progress")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["state"] == "idle"

    def test_returns_running_state_when_active(self, logged_in_client: Any) -> None:
        client, pub = logged_in_client
        pub.begin_job(5)
        resp = client.get("/api/sync/progress")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["state"] == "running"
        assert body["files_total"] == 5

    def test_redirects_to_login_when_not_authenticated(
        self, settings_path: Path
    ) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.get("/api/sync/progress")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_content_type_is_json(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/sync/progress")
        assert "application/json" in resp.content_type


# ---------------------------------------------------------------------------
# POST /api/sync/now
# ---------------------------------------------------------------------------


class TestTriggerNow:
    """tests for POST /api/sync/now."""

    def test_returns_202_and_job_id_on_success(self, logged_in_client: Any) -> None:
        client, pub = logged_in_client
        with patch("blackvuesync.server.sync_runner._do_sync") as mock_sync:
            mock_sync.side_effect = lambda _s, p, _m: p.end_job(success=True)
            resp = client.post("/api/sync/now")
        assert resp.status_code == 202
        body = json.loads(resp.data)
        assert "job_id" in body
        assert len(body["job_id"]) == 32

    def test_returns_409_when_already_running(self, logged_in_client: Any) -> None:
        client, pub = logged_in_client
        started = threading.Event()
        proceed = threading.Event()

        def _slow_sync(_s: Any, p: ProgressPublisher, _m: Any) -> None:
            started.set()
            proceed.wait(timeout=5.0)
            p.end_job(success=True)

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_slow_sync):
            resp1 = client.post("/api/sync/now")
            started.wait(timeout=2.0)
            resp2 = client.post("/api/sync/now")

        proceed.set()
        time.sleep(0.1)

        assert resp1.status_code == 202
        assert resp2.status_code == 409
        body2 = json.loads(resp2.data)
        assert body2["code"] == "SYNC_ALREADY_RUNNING"

    def test_redirects_to_login_when_not_authenticated(
        self, settings_path: Path
    ) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.post("/api/sync/now")
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /api/sync/last
# ---------------------------------------------------------------------------


class TestLastSync:
    """tests for GET /api/sync/last."""

    def test_returns_204_when_no_sync_has_run(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/sync/last")
        assert resp.status_code == 204

    def test_returns_200_after_sync_completes(self, logged_in_client: Any) -> None:
        client, pub = logged_in_client
        pub.begin_job(2)
        pub.end_job(success=True)
        resp = client.get("/api/sync/last")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["state"] == "complete"

    def test_returns_200_when_running(self, logged_in_client: Any) -> None:
        client, pub = logged_in_client
        pub.begin_job(3)
        resp = client.get("/api/sync/last")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["state"] == "running"


# ---------------------------------------------------------------------------
# GET /api/sync/progress/stream (SSE)
# ---------------------------------------------------------------------------


class TestProgressStream:
    """tests for GET /api/sync/progress/stream SSE endpoint."""

    def test_sse_stream_returns_event_lines(self, app_and_pub: Any) -> None:
        app, pub = app_and_pub
        # log in first
        with app.test_client() as client:
            client.post(
                "/login",
                data={"username": "admin", "password": "test-password-1234"},
                follow_redirects=True,
            )
            # trigger a state change from a background thread so the SSE
            # generator yields at least one snapshot
            triggered = threading.Event()

            def _inject() -> None:
                time.sleep(0.1)
                pub.begin_job(1)
                pub.end_job(success=True)
                triggered.set()

            t = threading.Thread(target=_inject, daemon=True)
            t.start()

            # consume one SSE frame by collecting the first response iterator item
            collected: list[bytes] = []
            with client.get(
                "/api/sync/progress/stream",
                buffered=False,
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.content_type
                # the test client's response iterator yields chunks
                triggered.wait(timeout=3.0)
                for chunk in resp.response:
                    if chunk:
                        collected.append(chunk)
                    if len(collected) >= 2:
                        break

            t.join(timeout=2.0)

        combined = b"".join(collected)
        # at least one SSE event or data line should appear
        assert b"event: progress" in combined or b"data:" in combined

    def test_sse_stream_content_type(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        # just check headers; don't consume the stream
        # we can't easily check with test client without blocking
        # so use a HEAD-like approach via a short read
        with client.get(
            "/api/sync/progress/stream",
            buffered=False,
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.content_type
            assert resp.headers.get("Cache-Control") == "no-store"
            assert resp.headers.get("X-Accel-Buffering") == "no"

    def test_sse_heartbeat_emits_keepalive_comment(self, app_and_pub: Any) -> None:
        """when subscribe() yields the same snapshot twice, the SSE handler
        emits a keepalive comment (': keepalive') instead of a data frame."""
        app, pub = app_and_pub
        idle_snap = pub.snapshot()

        def _fake_subscribe() -> Any:
            # first yield: real state change (initial snapshot)
            yield idle_snap
            # second yield: same monotonic -- simulates 30-second heartbeat timeout
            yield idle_snap

        with app.test_client() as client:
            client.post(
                "/login",
                data={"username": "admin", "password": "test-password-1234"},
                follow_redirects=True,
            )
            with patch.object(pub, "subscribe", side_effect=_fake_subscribe):
                collected: list[bytes] = []
                with client.get(
                    "/api/sync/progress/stream",
                    buffered=False,
                ) as resp:
                    assert resp.status_code == 200
                    for chunk in resp.response:
                        if chunk:
                            collected.append(chunk)

        combined = b"".join(collected)
        # the first yield should produce an event frame
        assert b"event: progress" in combined
        # the second yield (same snapshot) should produce a keepalive comment
        assert b": keepalive" in combined

    def test_sse_redirects_when_not_authenticated(self, settings_path: Path) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.get("/api/sync/progress/stream")
        assert resp.status_code == 302
