"""tests for /api/auth/* endpoints: me, password change, session rotation."""

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
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def _make_store(settings_path: Path) -> SettingsStore:
    """creates a SettingsStore with a dummy address."""
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


def _seed_admin(store: SettingsStore, password: str = "test-password-1234") -> None:
    """seeds the admin user with the given password."""
    pw_hash = hash_password(password)
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
        )
    )


@pytest.fixture()
def logged_in_client(settings_path: Path):  # type: ignore[no-untyped-def]
    """returns a logged-in flask test client."""
    store = _make_store(settings_path)
    _seed_admin(store)
    app = create_app(store, testing=True)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, store


class TestAuthMe:
    """tests for GET /api/auth/me."""

    def test_returns_current_user_in_login_mode(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["username"] == "admin"
        assert body["mode"] == "login"

    def test_redirects_to_login_when_unauthenticated(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.get("/api/auth/me")
        assert resp.status_code == 302
