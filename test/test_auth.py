"""tests for blackvuesync.server.auth module."""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask.testing import FlaskClient

from blackvuesync.server import create_app
from blackvuesync.server.auth import (
    _clear_failures,
    _failure_timestamps,
    _is_locked_out,
    _record_failure,
    hash_password,
    needs_rehash,
    verify_password,
)
from blackvuesync.settings import SettingsStore

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a path inside tmp_path for the settings file."""
    return tmp_path / "settings.json"


def _make_store(
    settings_path: Path, env: dict[str, str] | None = None
) -> SettingsStore:
    """creates a SettingsStore with optional env overrides; always adds ADDRESS."""
    base_env = {"ADDRESS": "192.168.0.1"}
    base_env.update(env or {})
    with patch.dict(os.environ, base_env, clear=False):
        return SettingsStore(settings_path)


@pytest.fixture()
def app(settings_path: Path) -> Flask:
    """returns a Flask test app with testing=True (CSRF disabled)."""
    store = _make_store(settings_path)
    return create_app(store, testing=True)


@pytest.fixture()
def client(app: Flask) -> FlaskClient:
    """returns a Flask test client."""
    return app.test_client()


@pytest.fixture(autouse=True)
def clear_rate_limit_state() -> Generator[None, None, None]:
    """clears in-memory rate-limit state before and after each test."""
    _failure_timestamps.clear()
    yield
    _failure_timestamps.clear()


# ---------------------------------------------------------------------------
# hash / verify / needs_rehash
# ---------------------------------------------------------------------------


def test_hash_verify_round_trip() -> None:
    """verifies hash_password produces a hash that verify_password accepts."""
    pw = "correct-horse-battery-staple"
    h = hash_password(pw)
    assert verify_password(h, pw) is True


def test_verify_wrong_password_returns_false() -> None:
    """verifies verify_password returns False for a wrong password."""
    h = hash_password("secret123456")
    assert verify_password(h, "wrong_password") is False


def test_two_hashes_of_same_plaintext_differ() -> None:
    """verifies two separate calls to hash_password produce different hashes (salts)."""
    pw = "same-password-1234"
    h1 = hash_password(pw)
    h2 = hash_password(pw)
    assert h1 != h2


def test_needs_rehash_false_for_fresh_hash() -> None:
    """verifies needs_rehash returns False for a hash produced with current params."""
    h = hash_password("my-secure-password")
    assert needs_rehash(h) is False


# ---------------------------------------------------------------------------
# rate-limit helpers
# ---------------------------------------------------------------------------


def test_is_locked_out_false_initially() -> None:
    """verifies an ip with no failures is not locked out."""
    assert _is_locked_out("10.0.0.1") is False


def test_nine_failures_do_not_lock() -> None:
    """verifies 9 failures (below threshold) do not trigger a lockout."""
    ip = "10.0.0.2"
    for _ in range(9):
        _record_failure(ip)
    assert _is_locked_out(ip) is False


def test_ten_failures_lock_out() -> None:
    """verifies 10 failures (at threshold) lock out the ip."""
    ip = "10.0.0.3"
    for _ in range(10):
        _record_failure(ip)
    assert _is_locked_out(ip) is True


def test_clear_failures_removes_lockout() -> None:
    """verifies _clear_failures removes a lockout."""
    ip = "10.0.0.4"
    for _ in range(11):
        _record_failure(ip)
    assert _is_locked_out(ip) is True
    _clear_failures(ip)
    assert _is_locked_out(ip) is False


def test_old_failures_expire_after_window() -> None:
    """verifies failures outside the sliding window do not contribute to lockout."""
    ip = "10.0.0.5"
    # record 11 failures at time 0
    base_time = 1000.0
    with patch("blackvuesync.server.auth.time") as mock_time:
        mock_time.monotonic.return_value = base_time
        for _ in range(11):
            _record_failure(ip)

    # advance time past the window (600s)
    with patch("blackvuesync.server.auth.time") as mock_time:
        mock_time.monotonic.return_value = base_time + 700
        assert _is_locked_out(ip) is False


# ---------------------------------------------------------------------------
# login_required decorator -- mode=login
# ---------------------------------------------------------------------------


def test_login_required_redirects_when_no_session(client: FlaskClient) -> None:
    """verifies unauthenticated GET / redirects to /login in login mode."""
    r = client.get("/")
    # first-run redirect takes priority when no password set
    assert r.status_code == 302
    assert "/first-run" in r.headers["Location"] or "/login" in r.headers["Location"]


def test_login_required_passes_when_session_set(app: Flask) -> None:
    """verifies a logged-in session accesses protected routes."""
    # set a password first
    from blackvuesync.server.auth import hash_password as hp

    pw_hash = hp("test-password-1234")
    app.settings_store.update(  # type: ignore[attr-defined]
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, password_hash=pw_hash),
        )
    )
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user"] = "admin"
        r = c.get("/")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# login_required decorator -- mode=none
# ---------------------------------------------------------------------------


def test_login_required_passthrough_for_auth_mode_none(
    settings_path: Path,
) -> None:
    """verifies mode=none allows access without a session."""
    store = _make_store(settings_path)
    pw_hash = hash_password("some-password-123")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth,
                mode="none",
                password_hash=pw_hash,
            ),
        )
    )
    app = create_app(store, testing=True)
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# login_required decorator -- mode=proxy
# ---------------------------------------------------------------------------


def test_login_required_honors_proxy_header_when_ip_trusted(
    settings_path: Path,
) -> None:
    """verifies mode=proxy grants access when IP is trusted and header present."""
    store = _make_store(settings_path)
    pw_hash = hash_password("some-password-123")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth,
                mode="proxy",
                password_hash=pw_hash,
                trusted_proxies=("127.0.0.1",),
                proxy_user_header="X-Remote-User",
            ),
        )
    )
    app = create_app(store, testing=True)
    with app.test_client() as c:
        r = c.get(
            "/",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Remote-User": "bob"},
        )
        assert r.status_code == 200


def test_login_required_returns_401_when_ip_not_trusted(
    settings_path: Path,
) -> None:
    """verifies mode=proxy returns 401 when remote IP is not in trusted_proxies."""
    store = _make_store(settings_path)
    pw_hash = hash_password("some-password-123")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth,
                mode="proxy",
                password_hash=pw_hash,
                trusted_proxies=("192.168.1.1",),
                proxy_user_header="X-Remote-User",
            ),
        )
    )
    app = create_app(store, testing=True)
    with app.test_client() as c:
        r = c.get(
            "/",
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
            headers={"X-Remote-User": "bob"},
        )
        assert r.status_code == 401
