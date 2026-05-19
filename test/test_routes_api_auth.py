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
from blackvuesync.server.auth import hash_password, verify_password
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


class TestChangePassword:
    """tests for POST /api/auth/password."""

    def test_changes_password_when_current_is_correct(
        self, logged_in_client: Any
    ) -> None:
        client, store = logged_in_client
        resp = client.post(
            "/api/auth/password",
            json={
                "current_password": "test-password-1234",
                "new_password": "new-strong-password-9876",
            },
        )
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["applied"] is True
        # the new hash verifies against the new password
        assert verify_password(
            store.get().auth.password_hash, "new-strong-password-9876"
        )

    def test_rejects_wrong_current_password(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        original_hash = store.get().auth.password_hash
        resp = client.post(
            "/api/auth/password",
            json={
                "current_password": "wrong-password",
                "new_password": "new-strong-password-9876",
            },
        )
        assert resp.status_code == 401
        body = json.loads(resp.data)
        assert body["code"] == "INVALID_CURRENT_PASSWORD"
        # hash unchanged
        assert store.get().auth.password_hash == original_hash

    def test_rejects_weak_new_password(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        original_hash = store.get().auth.password_hash
        resp = client.post(
            "/api/auth/password",
            json={
                "current_password": "test-password-1234",
                "new_password": "short",
            },
        )
        assert resp.status_code == 422
        body = json.loads(resp.data)
        assert body["code"] == "WEAK_PASSWORD"
        assert "field_errors" in body["details"]
        assert store.get().auth.password_hash == original_hash

    def test_redirects_to_login_when_unauthenticated(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.post(
                "/api/auth/password",
                json={"current_password": "x", "new_password": "y"},
            )
        assert resp.status_code == 302


class TestRotateSessions:
    """tests for DELETE /api/auth/sessions."""

    def test_rotates_session_secret(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        original_secret = store.get().auth.session_secret
        resp = client.delete("/api/auth/sessions")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["rotated"] is True
        assert body["restart_required"] is True
        # the persisted secret changed
        assert store.get().auth.session_secret != original_secret
        assert len(store.get().auth.session_secret) >= 32

    def test_redirects_to_login_when_unauthenticated(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.delete("/api/auth/sessions")
        assert resp.status_code == 302
