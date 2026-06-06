"""tests for /api/settings/* endpoints: redaction, validation, tier."""

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


@pytest.fixture()
def logged_in_client(settings_path: Path):  # type: ignore[no-untyped-def]
    """returns a logged-in flask test client."""
    store = _make_store(settings_path)
    pw_hash = hash_password("test-password-1234")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
        )
    )
    app = create_app(store, testing=True)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, store


class TestGetSettings:
    """tests for GET /api/settings."""

    def test_redacts_password_hash_and_session_secret(
        self, logged_in_client: Any
    ) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["auth"]["password_hash"] == "***"
        assert body["auth"]["session_secret"] == "***"

    def test_redacts_empty_secrets_unconditionally(self, settings_path: Path) -> None:
        """secrets are redacted to '***' even when empty, so the first-run
        state (password_hash='') does not leak through the api."""
        store = _make_store(settings_path)
        pw_hash = hash_password("test-password-1234")
        # seeds the admin so we can log in, then clears the session_secret to
        # exercise the empty-secret branch of the redaction logic.
        store.update(
            lambda s: dataclasses.replace(
                s,
                auth=dataclasses.replace(
                    s.auth, username="admin", password_hash=pw_hash, session_secret=""
                ),
            )
        )
        app = create_app(store, testing=True)
        with app.test_client() as client:
            client.post(
                "/login",
                data={"username": "admin", "password": "test-password-1234"},
                follow_redirects=True,
            )
            resp = client.get("/api/settings")
        body = json.loads(resp.data)
        assert body["auth"]["session_secret"] == "***"

    def test_includes_tier_per_section(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/settings")
        body = json.loads(resp.data)
        # spot-check the tier on three sections with different tiers.
        assert body["connection"]["_tier"] == "restart"
        assert body["sync"]["_tier"] == "next_tick"
        assert body["logging"]["_tier"] == "immediate"

    def test_redirects_to_login_when_not_authenticated(
        self, settings_path: Path
    ) -> None:
        store = _make_store(settings_path)
        pw_hash = hash_password("test-password-1234")
        store.update(
            lambda s: dataclasses.replace(
                s,
                auth=dataclasses.replace(
                    s.auth, username="admin", password_hash=pw_hash
                ),
            )
        )
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.get("/api/settings")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_content_type_is_json(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/settings")
        assert "application/json" in resp.content_type


class TestPatchSettings:
    """tests for PATCH /api/settings/<section>."""

    def test_updates_section_and_returns_tier(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        resp = client.patch(
            "/api/settings/sync",
            json={"grouping": "daily"},
        )
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["section"] == "sync"
        assert body["tier"] == "next_tick"
        assert body["applied"] is True
        # verify persistence
        assert store.get().sync.grouping == "daily"

    def test_unknown_section_returns_404(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client
        resp = client.patch("/api/settings/nonexistent", json={"foo": 1})
        assert resp.status_code == 404
        body = json.loads(resp.data)
        assert body["code"] == "SECTION_NOT_FOUND"

    def test_invalid_value_returns_422_with_field_errors(
        self, logged_in_client: Any
    ) -> None:
        client, _ = logged_in_client
        resp = client.patch(
            "/api/settings/connection",
            json={"address": ""},
        )
        assert resp.status_code == 422
        body = json.loads(resp.data)
        assert body["code"] == "SETTINGS_INVALID"
        assert isinstance(body["details"]["field_errors"], list)
        assert len(body["details"]["field_errors"]) >= 1

    def test_redaction_sentinel_means_leave_unchanged(
        self, logged_in_client: Any
    ) -> None:
        """sending password_hash='***' must not overwrite the real hash."""
        client, store = logged_in_client
        before = store.get().auth.password_hash
        resp = client.patch(
            "/api/settings/auth",
            json={"password_hash": "***", "username": "operator"},
        )
        assert resp.status_code == 200
        after = store.get().auth
        assert after.password_hash == before
        assert after.username == "operator"

    def test_non_sentinel_value_for_redacted_field_overwrites(
        self, logged_in_client: Any
    ) -> None:
        """sending a real (non-sentinel) value for a redacted field overwrites.

        guards against a future refactor that turns _strip_redacted into
        'always strip password_hash regardless of value'.
        """
        client, store = logged_in_client
        new_hash = hash_password("a-fresh-and-different-pw")
        resp = client.patch(
            "/api/settings/auth",
            json={"password_hash": new_hash},
        )
        assert resp.status_code == 200
        assert store.get().auth.password_hash == new_hash

    def test_non_dict_body_returns_400(self, logged_in_client: Any) -> None:
        """a JSON array as body must return 400 INVALID_BODY, not 500."""
        client, _ = logged_in_client
        resp = client.patch("/api/settings/sync", json=[1, 2, 3])
        assert resp.status_code == 400
        body = json.loads(resp.data)
        assert body["code"] == "INVALID_BODY"

    def test_tuple_field_patch_stores_tuple(self, logged_in_client: Any) -> None:
        """JSON lists must be coerced to tuple for tuple-annotated fields.

        without coercion the in-memory dataclass would hold a list, silently
        violating its `tuple[str, ...]` annotation until the next process
        restart restored the type via the JSON round-trip.
        """
        client, store = logged_in_client
        resp = client.patch(
            "/api/settings/sync",
            json={"skip_metadata": ["t", "3"]},
        )
        assert resp.status_code == 200
        assert isinstance(store.get().sync.skip_metadata, tuple)


def test_stats_section_get_and_patch(logged_in_client: Any) -> None:
    import json as _json

    client, _ = logged_in_client
    body = _json.loads(client.get("/api/settings").data)
    assert body["stats"]["retention_days"] == 365
    assert body["stats"]["_tier"] == "next_tick"
    patch = client.patch("/api/settings/stats", json={"retention_days": 30})
    assert patch.status_code == 200
    after = _json.loads(client.get("/api/settings").data)
    assert after["stats"]["retention_days"] == 30


class TestCsrf:
    """tests that PATCH /api/settings/* requires a CSRF token."""

    def test_patch_without_csrf_returns_400(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        pw_hash = hash_password("test-password-1234")
        store.update(
            lambda s: dataclasses.replace(
                s,
                auth=dataclasses.replace(
                    s.auth, username="admin", password_hash=pw_hash
                ),
            )
        )
        # builds an app with CSRF enabled
        app = create_app(store, testing=False)
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["TESTING"] = True
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.patch("/api/settings/sync", json={"grouping": "daily"})
        assert resp.status_code == 400
