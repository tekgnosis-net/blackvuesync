"""tests for /api/health/storage and /api/health/dashcam."""

from __future__ import annotations

import dataclasses
import json
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


def _make_store(settings_path: Path) -> SettingsStore:
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


def _make_app(settings_path: Path, destination: Path | None = None):  # type: ignore[no-untyped-def]
    store = _make_store(settings_path)
    pw_hash = hash_password("test-password-1234")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
            system=dataclasses.replace(
                s.system,
                destination=str(destination) if destination else s.system.destination,
            ),
        )
    )
    return create_app(store, testing=True), store


@pytest.fixture()
def logged_in_client(settings_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    """returns a logged-in flask test client with a real destination directory."""
    destination = tmp_path / "recordings"
    destination.mkdir()
    app, store = _make_app(settings_path, destination=destination)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, store, destination


class TestStorage:
    """tests for GET /api/health/storage."""

    def test_returns_available_true_for_existing_destination(
        self, logged_in_client: Any
    ) -> None:
        client, _, _ = logged_in_client
        resp = client.get("/api/health/storage")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["available"] is True
        assert "total_bytes" in body
        assert "free_bytes" in body
        assert "used_bytes" in body
        assert "used_percent" in body
        assert "recording_count" in body
        assert body["recording_count"] == 0

    def test_counts_recordings_in_destination(self, logged_in_client: Any) -> None:
        """recording_count reflects files matching the BlackVue filename regex."""
        client, _, destination = logged_in_client
        # create 2 valid recordings and 1 non-matching file
        (destination / "20231015_120000_NF.mp4").write_text("x")
        (destination / "20231015_115400_NR.mp4").write_text("y")
        (destination / "notes.txt").write_text("ignored")

        resp = client.get("/api/health/storage")
        body = json.loads(resp.data)
        assert body["recording_count"] == 2

    def test_returns_unavailable_for_missing_destination(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        """when destination does not exist on disk, returns available=false."""
        missing = tmp_path / "does-not-exist"
        app, _ = _make_app(settings_path, destination=missing)
        with app.test_client() as client:
            client.post(
                "/login",
                data={"username": "admin", "password": "test-password-1234"},
                follow_redirects=True,
            )
            resp = client.get("/api/health/storage")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["available"] is False
        assert body["reason"] == "destination not configured"

    def test_redirects_to_login_when_unauthenticated(self, settings_path: Path) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.get("/api/health/storage")
        assert resp.status_code == 302
