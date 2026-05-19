# Web Foundation -- Phase F: Settings API + Auth API

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 -->

**Date:** 2026-05-19
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Section 4: API surface)
**Phase:** F of 7 (A-E done; G to follow).

**Goal:** Expose the settings store and auth-management surface via JSON HTTP
endpoints so the future Settings UI (sub-project #3) and Dashboard
(sub-project #2) have a stable backend to consume. No new behavior beyond
what `SettingsStore` and the argon2 helpers already provide; Phase F is a
thin, well-tested HTTP veneer.

**Architecture:** Two new Flask blueprints under
`blackvuesync/server/routes/`. The `api_settings_bp` blueprint exposes a
single GET (full settings, secrets redacted to the literal string `"***"`)
and a per-section PATCH that validates, persists via `SettingsStore.update()`,
and returns the section's `TIER` (`immediate` / `next_tick` / `restart`) so
the UI can choose the right affordance. The `api_auth_bp` blueprint exposes
the current user (no secrets), a password-change endpoint that requires
`current_password`, and a session-secret rotation endpoint that invalidates
every active session (including the caller's). All POST/PATCH/DELETE inherit
global CSRF protection from `Flask-WTF`. Rate-limiting on password change
shares the same per-IP sliding-window state that the login flow already uses.

**Out of scope (do not touch):**

- `blackvuesync/sync.py` -- the carry-forward S3776 findings stay; the
  cleanup PR after Phase E is the right home, not this PR.
- `blackvuesync/server/scheduler.py` -- the schedule section is reachable
  via `PATCH /api/settings/schedule` and the existing `on_change` listener
  picks up the reschedule; no scheduler-side change.
- Settings UI templates -- placeholder pages stay placeholder; Phase G or
  sub-project #3.
- `cmd_serve` logging configuration -- carry-forward to Phase G.
- HX fragment endpoints -- Phase D already wired two; the spec only
  requires "examples" for Phase F.

---

## Implementer guidelines (karpathy discipline)

1. **Think before coding.** State assumptions explicitly. If a step is
   ambiguous, stop and report DONE_WITH_CONCERNS rather than picking
   silently.
2. **Simplicity first.** Minimum code that solves the problem. No
   speculative configurability. No error handling for impossible scenarios.
3. **Surgical changes.** Touch only the files this plan lists. The plan
   explicitly enumerates files NOT to modify -- respect that list.
4. **Goal-driven execution.** Each task has a verifiable check. Run it
   before declaring the task done.

Process hygiene (still mandatory):

- Never use `git add -A` or `git add .`. List files explicitly.
- Never use `--no-verify`. Pre-commit hooks must pass.
- Never amend an existing commit after a pre-commit auto-fix. Create a
  new commit.
- Comments are lowercase, third-person, non-obvious.

---

## File Structure

### Files to create

- `blackvuesync/server/routes/api_settings.py` -- two routes, redaction
  helper, validation envelope helper.
- `blackvuesync/server/routes/api_auth.py` -- three routes (me, password,
  sessions).
- `test/test_routes_api_settings.py` -- unit tests via Flask test client.
- `test/test_routes_api_auth.py` -- unit tests via Flask test client.

### Files to modify

- `blackvuesync/server/__init__.py` -- register the two new blueprints.
- `pyproject.toml` -- bump version to `2.3.0a2`.
- `pyproject.toml` `[tool.mypy.overrides]` module list -- add
  `test_routes_api_settings`, `test_routes_api_auth` (matching the
  existing test-module override pattern).

### Files explicitly NOT to modify

- `blackvuesync/sync.py`
- `blackvuesync/metrics.py`
- `blackvuesync/settings.py` (the redaction lives in the route, not in
  the settings layer; the settings layer is the source of truth and
  should not know about HTTP response shapes)
- `blackvuesync/server/auth.py` (no auth helpers need to change; just
  consumed by the new routes)
- `blackvuesync/server/scheduler.py`
- `blackvuesync/server/sync_runner.py`
- `blackvuesync/server/progress.py`
- Any other route file under `routes/`.
- Any other test file.
- Dockerfile, entrypoint.sh, docker-compose.yml, run.sh.

---

## API contract reference

### `GET /api/settings`

- Requires login (decorator: `@login_required`).
- Returns 200 with a JSON body whose top level is the same shape as
  `dataclasses.asdict(Settings)`, with two specific keys replaced by the
  literal string `"***"`:
  - `auth.password_hash`
  - `auth.session_secret`
- Each top-level section dict gets an extra key `_tier` whose value is the
  section's `TIER` ClassVar (one of `"immediate"`, `"next_tick"`,
  `"restart"`). The leading underscore avoids collision with any future
  user-facing field.

### `PATCH /api/settings/<section>`

- Requires login. CSRF token required.
- Request body: JSON object with a partial dict of fields to update. Any
  field whose value is the literal string `"***"` is treated as
  "leave unchanged" (preserves the redaction round-trip contract).
- Validates the resulting section. On validation failure, returns 422
  with the standard error envelope:
  ```json
  {
    "error": "settings validation failed",
    "code": "SETTINGS_INVALID",
    "details": {
      "field_errors": [
        {"path": "connection.address", "message": "..."}
      ]
    }
  }
  ```
- On success, returns 200 with:
  ```json
  {
    "section": "connection",
    "tier": "restart",
    "applied": true
  }
  ```
- Unknown section name -> 404 with `code: "SECTION_NOT_FOUND"`.

### `GET /api/auth/me`

- Requires login. Returns 200 with:
  ```json
  {"username": "admin", "mode": "login"}
  ```
- In `auth.mode == "none"` returns `{"username": "anonymous", "mode": "none"}`.
- In `auth.mode == "proxy"` returns the header-supplied username.

### `POST /api/auth/password`

- Requires login. CSRF token required.
- Request body: `{"current_password": "...", "new_password": "..."}`.
- If `current_password` is wrong: 401, code `INVALID_CURRENT_PASSWORD`,
  records a failure against the per-IP rate-limiter (same one as login).
- If the IP is locked out: 429, code `RATE_LIMITED`.
- If the new password is < 12 chars: 422, code `WEAK_PASSWORD`, with
  `details.field_errors[]` shape.
- On success: hashes the new password, persists via `SettingsStore.update`,
  clears the IP's failure history, returns 200 with `{"applied": true}`.

### `DELETE /api/auth/sessions`

- Requires login. CSRF token required.
- Rotates `auth.session_secret` to a new `secrets.token_hex(32)`. Every
  existing signed-cookie session (including the caller's) is invalidated
  because Flask validates the cookie against `app.config["SECRET_KEY"]`,
  which is set from `auth.session_secret` at `create_app()` time. **Note:**
  the running process holds the old secret in `app.config["SECRET_KEY"]`
  until restart, so the caller's session technically remains valid in
  this process; the documented behavior is "rotated, will fully invalidate
  on next restart". This is a known limitation acknowledged in the
  master design spec ("TIER `restart` for web section"). Returns 200
  with `{"rotated": true, "restart_required": true}`.

### Error envelope (every non-2xx)

```json
{
  "error": "human-readable message",
  "code": "STABLE_SLUG",
  "details": { ... }
}
```

Known codes used in Phase F: `SETTINGS_INVALID`, `SECTION_NOT_FOUND`,
`INVALID_CURRENT_PASSWORD`, `WEAK_PASSWORD`, `RATE_LIMITED`, plus the
existing `AUTH_REQUIRED` (Flask redirect from `login_required`) and
`CSRF_FAILED` (Flask-WTF default on missing token).

---

## Task 1: Bump version and add test-module mypy overrides

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version**

Change `version = "2.3.0a1"` to `version = "2.3.0a2"`.

- [ ] **Step 2: Add new test modules to mypy override list**

In the existing override block (currently lists `test_auth`,
`test_routes_auth`, `test_routes_health`, etc.), append
`"test_routes_api_settings"` and `"test_routes_api_auth"`.

- [ ] **Step 3: Verify**

Run: `pip install -e ".[dev]"` -- expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Phase F: bump to 2.3.0a2 and prep test-module mypy overrides"
```

---

## Task 2: Write failing test for GET /api/settings redaction

**Files:**

- Create: `test/test_routes_api_settings.py`

- [ ] **Step 1: Write the test file with a single failing test**

```python
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
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest test/test_routes_api_settings.py::TestGetSettings::test_redacts_password_hash_and_session_secret -v`
Expected: FAIL with 404 (the route does not exist yet).

---

## Task 3: Implement GET /api/settings with redaction

**Files:**

- Create: `blackvuesync/server/routes/api_settings.py`
- Modify: `blackvuesync/server/__init__.py`

- [ ] **Step 1: Write api_settings.py**

```python
"""api settings routes: GET /api/settings, PATCH /api/settings/<section>."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from flask import Blueprint, Response, current_app, request

from blackvuesync.server.auth import login_required
from blackvuesync.settings import _SECTION_FIELDS, Settings, SettingsStore

api_settings_bp = Blueprint("api_settings_bp", __name__, url_prefix="/api/settings")

_MIME_JSON = "application/json"

# fields whose values must never be sent to the client; the literal "***"
# sentinel is returned instead. the same sentinel is also the signal on
# PATCH that means "leave this field unchanged".
_REDACTED_FIELDS: dict[str, set[str]] = {
    "auth": {"password_hash", "session_secret"},
}

_REDACTED_SENTINEL = "***"


def _section_to_dict(name: str, section: Any) -> dict[str, Any]:
    """converts a section dataclass to a dict, redacting secret fields and
    adding the _tier annotation."""
    d: dict[str, Any] = dataclasses.asdict(section)
    redact = _REDACTED_FIELDS.get(name, set())
    for field_name in redact:
        if field_name in d and d[field_name]:
            d[field_name] = _REDACTED_SENTINEL
    d["_tier"] = section.__class__.TIER
    return d


def _settings_to_dict(s: Settings) -> dict[str, Any]:
    """converts the full Settings to a redacted dict with per-section tier."""
    out: dict[str, Any] = {"version": s.version}
    for name in _SECTION_FIELDS:
        out[name] = _section_to_dict(name, getattr(s, name))
    return out


@api_settings_bp.route("", methods=["GET"])
@login_required
def get_settings() -> Response:
    """returns the full settings as JSON; secrets redacted to '***'."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    return Response(
        json.dumps(_settings_to_dict(store.get())),
        status=200,
        mimetype=_MIME_JSON,
    )


__all__ = ["api_settings_bp"]
```

- [ ] **Step 2: Register the blueprint**

In `blackvuesync/server/__init__.py`, inside the deferred-import block,
add the import and the registration:

```python
from blackvuesync.server.routes.api_settings import api_settings_bp
# ...
app.register_blueprint(api_settings_bp)
```

Place both lines next to the other `api_*` and `hx_*` registrations.

- [ ] **Step 3: Run the redaction test**

Run: `pytest test/test_routes_api_settings.py::TestGetSettings::test_redacts_password_hash_and_session_secret -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/routes/api_settings.py blackvuesync/server/__init__.py test/test_routes_api_settings.py
git commit -m "Phase F: GET /api/settings with secret redaction"
```

---

## Task 4: Cover GET /api/settings tier annotation and auth

**Files:**

- Modify: `test/test_routes_api_settings.py`

- [ ] **Step 1: Add tests**

Append to `TestGetSettings`:

```python
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
                auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
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
```

- [ ] **Step 2: Run all api_settings tests**

Run: `pytest test/test_routes_api_settings.py -v`
Expected: 4 PASS.

- [ ] **Step 3: Commit**

```bash
git add test/test_routes_api_settings.py
git commit -m "Phase F: cover GET /api/settings tier annotation and auth"
```

---

## Task 5: Implement PATCH /api/settings/<section>

**Files:**

- Modify: `blackvuesync/server/routes/api_settings.py`
- Modify: `test/test_routes_api_settings.py`

- [ ] **Step 1: Write failing test**

Append to `test/test_routes_api_settings.py`:

```python
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
```

- [ ] **Step 2: Add PATCH route to `api_settings.py`**

Append to `blackvuesync/server/routes/api_settings.py`:

```python
def _strip_redacted(payload: dict[str, Any], section_name: str) -> dict[str, Any]:
    """removes fields whose value is the redaction sentinel.

    a client that re-submits the redacted snapshot from GET must not
    overwrite the real secret with '***'. callers treat absent keys as
    'leave unchanged'.
    """
    redact = _REDACTED_FIELDS.get(section_name, set())
    return {
        k: v
        for k, v in payload.items()
        if not (k in redact and v == _REDACTED_SENTINEL)
    }


@api_settings_bp.route("/<string:section_name>", methods=["PATCH"])
@login_required
def patch_section(section_name: str) -> Response:
    """updates a section partially; validates; returns the tier on success."""
    if section_name not in _SECTION_FIELDS:
        body = json.dumps(
            {
                "error": f"unknown settings section: {section_name!r}",
                "code": "SECTION_NOT_FOUND",
                "details": {"section": section_name},
            }
        )
        return Response(body, status=404, mimetype=_MIME_JSON)

    payload = request.get_json(silent=True) or {}
    payload = _strip_redacted(payload, section_name)

    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    section_cls = _SECTION_FIELDS[section_name]

    current = store.get()
    current_section = getattr(current, section_name)
    try:
        new_section = dataclasses.replace(current_section, **payload)
    except TypeError as e:
        # unknown field name in payload; treat as a validation failure.
        body = json.dumps(
            {
                "error": "settings validation failed",
                "code": "SETTINGS_INVALID",
                "details": {
                    "field_errors": [
                        {"path": f"{section_name}.?", "message": str(e)},
                    ]
                },
            }
        )
        return Response(body, status=422, mimetype=_MIME_JSON)

    errors = new_section.validate()
    if errors:
        body = json.dumps(
            {
                "error": "settings validation failed",
                "code": "SETTINGS_INVALID",
                "details": {
                    "field_errors": [
                        {"path": section_name, "message": msg} for msg in errors
                    ]
                },
            }
        )
        return Response(body, status=422, mimetype=_MIME_JSON)

    store.update(
        lambda s: dataclasses.replace(s, **{section_name: new_section})
    )

    body = json.dumps(
        {
            "section": section_name,
            "tier": section_cls.TIER,
            "applied": True,
        }
    )
    return Response(body, status=200, mimetype=_MIME_JSON)
```

- [ ] **Step 3: Run tests**

Run: `pytest test/test_routes_api_settings.py -v`
Expected: 8 PASS.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/routes/api_settings.py test/test_routes_api_settings.py
git commit -m "Phase F: PATCH /api/settings/<section> with tier-aware response"
```

---

## Task 6: Write failing test for GET /api/auth/me

**Files:**

- Create: `test/test_routes_api_auth.py`

- [ ] **Step 1: Write the test file**

```python
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

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.get("/api/auth/me")
        assert resp.status_code == 302
```

- [ ] **Step 2: Run the test**

Run: `pytest test/test_routes_api_auth.py -v`
Expected: FAIL with 404.

---

## Task 7: Implement GET /api/auth/me

**Files:**

- Create: `blackvuesync/server/routes/api_auth.py`
- Modify: `blackvuesync/server/__init__.py`

- [ ] **Step 1: Write api_auth.py**

```python
"""api auth routes: GET /api/auth/me, POST /api/auth/password, DELETE /api/auth/sessions."""

from __future__ import annotations

import dataclasses
import json
import secrets
from typing import Any

from flask import Blueprint, Response, current_app, g, request

from blackvuesync.server.auth import (
    clear_login_failures,
    hash_password,
    is_login_locked_out,
    login_required,
    record_login_failure,
    verify_password,
)
from blackvuesync.settings import SettingsStore

api_auth_bp = Blueprint("api_auth_bp", __name__, url_prefix="/api/auth")

_MIME_JSON = "application/json"
_MIN_PASSWORD_LENGTH = 12


@api_auth_bp.route("/me", methods=["GET"])
@login_required
def me() -> Response:
    """returns the current authenticated user and auth mode."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    mode = store.get().auth.mode
    body = json.dumps({"username": g.current_user, "mode": mode})
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_auth_bp"]
```

- [ ] **Step 2: Register the blueprint**

In `blackvuesync/server/__init__.py`, add the import and registration
alongside the api_settings registration:

```python
from blackvuesync.server.routes.api_auth import api_auth_bp
# ...
app.register_blueprint(api_auth_bp)
```

- [ ] **Step 3: Run the tests**

Run: `pytest test/test_routes_api_auth.py -v`
Expected: 2 PASS.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/routes/api_auth.py blackvuesync/server/__init__.py test/test_routes_api_auth.py
git commit -m "Phase F: GET /api/auth/me"
```

---

## Task 8: Implement POST /api/auth/password

**Files:**

- Modify: `blackvuesync/server/routes/api_auth.py`
- Modify: `test/test_routes_api_auth.py`

- [ ] **Step 1: Append tests**

Append to `test/test_routes_api_auth.py`:

```python
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

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.post(
                "/api/auth/password",
                json={"current_password": "x", "new_password": "y"},
            )
        assert resp.status_code == 302
```

- [ ] **Step 2: Add the route**

Append to `blackvuesync/server/routes/api_auth.py`:

```python
@api_auth_bp.route("/password", methods=["POST"])
@login_required
def change_password() -> Response:
    """changes the current user's password; requires the current password."""
    ip = request.remote_addr or "unknown"
    if is_login_locked_out(ip):
        body = json.dumps(
            {
                "error": "too many failures; try again later",
                "code": "RATE_LIMITED",
                "details": {},
            }
        )
        return Response(body, status=429, mimetype=_MIME_JSON)

    payload: dict[str, Any] = request.get_json(silent=True) or {}
    current = payload.get("current_password", "")
    new = payload.get("new_password", "")

    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    stored_hash = store.get().auth.password_hash

    if not verify_password(stored_hash, current):
        record_login_failure(ip)
        body = json.dumps(
            {
                "error": "current password is incorrect",
                "code": "INVALID_CURRENT_PASSWORD",
                "details": {},
            }
        )
        return Response(body, status=401, mimetype=_MIME_JSON)

    if len(new) < _MIN_PASSWORD_LENGTH:
        body = json.dumps(
            {
                "error": "new password too short",
                "code": "WEAK_PASSWORD",
                "details": {
                    "field_errors": [
                        {
                            "path": "new_password",
                            "message": f"must be at least {_MIN_PASSWORD_LENGTH} characters",
                        }
                    ]
                },
            }
        )
        return Response(body, status=422, mimetype=_MIME_JSON)

    new_hash = hash_password(new)
    store.update(
        lambda s: dataclasses.replace(
            s, auth=dataclasses.replace(s.auth, password_hash=new_hash)
        )
    )
    clear_login_failures(ip)

    body = json.dumps({"applied": True})
    return Response(body, status=200, mimetype=_MIME_JSON)
```

- [ ] **Step 3: Run tests**

Run: `pytest test/test_routes_api_auth.py -v`
Expected: 6 PASS.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/routes/api_auth.py test/test_routes_api_auth.py
git commit -m "Phase F: POST /api/auth/password with rate limit"
```

---

## Task 9: Implement DELETE /api/auth/sessions

**Files:**

- Modify: `blackvuesync/server/routes/api_auth.py`
- Modify: `test/test_routes_api_auth.py`

- [ ] **Step 1: Append test**

Append to `test/test_routes_api_auth.py`:

```python
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

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.delete("/api/auth/sessions")
        assert resp.status_code == 302
```

- [ ] **Step 2: Add the route**

Append to `blackvuesync/server/routes/api_auth.py`:

```python
@api_auth_bp.route("/sessions", methods=["DELETE"])
@login_required
def rotate_sessions() -> Response:
    """rotates the session secret. all existing sessions invalidate on next
    restart; the running process keeps using the old secret until cmd_serve
    re-runs create_app. this matches TIER='restart' for the web section."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    new_secret = secrets.token_hex(32)
    store.update(
        lambda s: dataclasses.replace(
            s, auth=dataclasses.replace(s.auth, session_secret=new_secret)
        )
    )
    body = json.dumps({"rotated": True, "restart_required": True})
    return Response(body, status=200, mimetype=_MIME_JSON)
```

- [ ] **Step 3: Run tests**

Run: `pytest test/test_routes_api_auth.py -v`
Expected: 8 PASS.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/routes/api_auth.py test/test_routes_api_auth.py
git commit -m "Phase F: DELETE /api/auth/sessions rotates session secret"
```

---

## Task 10: CSRF coverage tests

**Files:**

- Modify: `test/test_routes_api_settings.py`
- Modify: `test/test_routes_api_auth.py`

- [ ] **Step 1: Append CSRF test to api_settings tests**

Append a new class:

```python
class TestCsrf:
    """tests that PATCH /api/settings/* requires a CSRF token."""

    def test_patch_without_csrf_returns_400(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        pw_hash = hash_password("test-password-1234")
        store.update(
            lambda s: dataclasses.replace(
                s,
                auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
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
```

- [ ] **Step 2: Append CSRF tests to api_auth tests**

```python
class TestCsrf:
    """tests that POST /api/auth/password and DELETE /api/auth/sessions
    require a CSRF token."""

    def _csrf_app(self, settings_path: Path):  # type: ignore[no-untyped-def]
        store = _make_store(settings_path)
        _seed_admin(store)
        app = create_app(store, testing=False)
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["TESTING"] = True
        return app

    def test_password_post_without_csrf_returns_400(
        self, settings_path: Path
    ) -> None:
        app = self._csrf_app(settings_path)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.post(
                "/api/auth/password",
                json={"current_password": "x", "new_password": "y" * 20},
            )
        assert resp.status_code == 400

    def test_sessions_delete_without_csrf_returns_400(
        self, settings_path: Path
    ) -> None:
        app = self._csrf_app(settings_path)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.delete("/api/auth/sessions")
        assert resp.status_code == 400
```

- [ ] **Step 3: Run all new tests**

Run: `pytest test/test_routes_api_settings.py test/test_routes_api_auth.py -v`
Expected: 12 PASS (9 settings + 3 csrf-related ... actually: 4 GET settings + 4 PATCH settings + 1 settings CSRF = 9; 2 me + 4 password + 2 sessions + 2 csrf = 10. Total 19. Adjust the counts based on actual run.).

- [ ] **Step 4: Commit**

```bash
git add test/test_routes_api_settings.py test/test_routes_api_auth.py
git commit -m "Phase F: cover CSRF requirement on settings PATCH and auth POST/DELETE"
```

---

## Task 11: Final test sweep

- [ ] **Step 1: Full unit suite**

Run: `pytest test/ -v`
Expected: all tests pass; the ~19 new tests join the existing 364 from
Phase E, yielding 380+ total.

- [ ] **Step 2: Behave (subprocess)**

Run: `behave`
Expected: 21/21 scenarios.

- [ ] **Step 3: Behave (docker)**

Run: `behave -D implementation=docker`
Expected: 21/21 scenarios. Phase F adds no Docker behavior; this is a
regression guard.

- [ ] **Step 4: Pre-commit**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 5: Coverage**

Run: `./coverage.sh`
Expected: report generated; overall coverage stays >= 85%.

---

## Task 12: Open the PR

- [ ] **Step 1: Push**

```bash
git push -u origin web-foundation-phase-f
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --repo tekgnosis-net/blackvuesync \
  --base main \
  --head web-foundation-phase-f \
  --title "Web Foundation Phase F: settings API + auth API" \
  --body "$(cat <<'EOF'
## Summary
- Adds `GET /api/settings` (full settings with secrets redacted to '***' and per-section `_tier`).
- Adds `PATCH /api/settings/<section>` (partial update, tier-aware response, field_errors envelope on 422).
- Adds `GET /api/auth/me`, `POST /api/auth/password` (rate-limited, current-password check), `DELETE /api/auth/sessions` (rotates session_secret).
- Tests the redaction round-trip (sending '***' back means 'leave unchanged'), the tier annotation per section, and CSRF coverage on all mutating endpoints.

## Out of scope (carry-forward)
- sync.py cognitive-complexity findings (S3776) -- cleanup PR.
- cmd_serve logging configuration -- Phase G.
- Multi-stage Dockerfile -- Phase G.

## Test plan
- [ ] Unit: `pytest test/`
- [ ] Behave subprocess: `behave`
- [ ] Behave docker: `behave -D implementation=docker`
- [ ] CI: 5 required checks green
EOF
)"
```

- [ ] **Step 3: Wait for the 5 required checks**

`pre-commit`, `unit-tests`, `integration-tests`, `test`,
`SonarCloud Code Analysis`.

- [ ] **Step 4: Squash-merge once green** (controlling agent does this; the
  implementer does NOT push or merge).

---

## Self-review against spec

| Spec requirement (Section 4) | Plan task |
| --- | --- |
| GET /api/settings full + redacted | Task 3 |
| PATCH /api/settings/<section> partial | Task 5 |
| tier field on PATCH response | Task 5 |
| field_errors[] shape on 422 | Task 5 |
| redaction sentinel '***' round-trip | Tasks 3 + 5 |
| GET /api/auth/me | Task 7 |
| POST /api/auth/password requires current_password | Task 8 |
| DELETE /api/auth/sessions rotates session_secret | Task 9 |
| Error envelope (error, code, details) on every non-2xx | Tasks 5 + 8 + 9 |
| CSRF on POST/PATCH/DELETE | Task 10 |

## What is NOT done in Phase F (recap)

- HX fragments beyond the two from Phase D -- not required by Phase F.
- Static asset fingerprinting helper -- Phase G.
- `/api/auth/sessions` is documented as TIER `restart` (running process
  keeps the old secret until create_app re-runs). The endpoint records
  the rotation but does not force-restart the server. This matches the
  spec exactly.
