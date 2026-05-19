"""tests for /hx/storage-card, /hx/dashcam-card, /hx/next-scheduled-card,
/hx/recent-activity-card HTMX fragments."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


def _make_app(settings_path: Path, destination: Path):  # type: ignore[no-untyped-def]
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(settings_path)
    pw_hash = hash_password("test-password-1234")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
            system=dataclasses.replace(s.system, destination=str(destination)),
        )
    )
    return create_app(store, testing=True), store


@pytest.fixture()
def logged_in_client(settings_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    app, store = _make_app(settings_path, destination)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, store, destination


class TestStorageCard:
    """tests for GET /hx/storage-card."""

    def test_renders_html(self, logged_in_client: Any) -> None:
        client, _, _ = logged_in_client
        resp = client.get("/hx/storage-card")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type
        assert b"storage-card" in resp.data
        assert b"hx-trigger" in resp.data  # has polling trigger

    def test_redirects_when_unauthenticated(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        destination = tmp_path / "recordings"
        destination.mkdir()
        app, _ = _make_app(settings_path, destination)
        with app.test_client() as client:
            resp = client.get("/hx/storage-card")
        assert resp.status_code == 302


class TestDashcamCard:
    """tests for GET /hx/dashcam-card."""

    def test_renders_html(self, logged_in_client: Any) -> None:
        import socket

        client, _, _ = logged_in_client
        # mock the urlopen call to avoid hitting a real network
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            resp = client.get("/hx/dashcam-card")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type
        assert b"dashcam-card" in resp.data


class TestNextScheduledCard:
    """tests for GET /hx/next-scheduled-card."""

    def test_renders_html_when_not_paused(self, logged_in_client: Any) -> None:
        client, _, _ = logged_in_client
        resp = client.get("/hx/next-scheduled-card")
        assert resp.status_code == 200
        assert b"next-scheduled-card" in resp.data

    def test_renders_paused_state(self, logged_in_client: Any) -> None:
        client, store, _ = logged_in_client
        store.update(
            lambda s: dataclasses.replace(
                s, schedule=dataclasses.replace(s.schedule, paused=True)
            )
        )
        resp = client.get("/hx/next-scheduled-card")
        assert resp.status_code == 200
        assert b"paused" in resp.data.lower()


class TestRecentActivityCard:
    """tests for GET /hx/recent-activity-card."""

    def test_renders_html(self, logged_in_client: Any) -> None:
        client, _, destination = logged_in_client
        (destination / "20231015_120000_NF.mp4").write_text("x")
        resp = client.get("/hx/recent-activity-card")
        assert resp.status_code == 200
        assert b"recent-activity-card" in resp.data
        assert b"20231015_120000_NF.mp4" in resp.data
