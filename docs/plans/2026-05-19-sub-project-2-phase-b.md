# Sub-Project #2 Phase 2B: Dashboard idle UI + Dashcam info card

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

<!-- markdownlint-disable MD013 MD031 MD032 MD033 MD040 MD050 MD060 -->

**Goal:** Replace the placeholder dashboard at `/` with the real idle-mode dashboard -- a persistent sidebar plus a responsive grid of six status cards -- and add the read-only **Dashcam info** card (`GET /api/dashcam/info` + `/hx/dashcam-info-card`).

**Architecture:** The dashboard renders Layout C (220px sidebar + `repeat(auto-fit)` card grid) from `dashboard.html`, styled by a new `dashboard.css` that consumes the existing design tokens (so light/dark is automatic). Four fast, local cards (last sync, next scheduled, storage, recent activity) are server-rendered populated by the `dashboard()` route and self-refresh via HTMX `every 5s`. Two network-bound cards (dashcam reachability, dashcam info) render as lightweight shells and HTMX-load their content after the page paints, keeping server render fast and never blocking on a slow/offline dashcam. The new dashcam-info endpoint fetches `/Config/version.bin` and `/Config/config.ini` from the camera, parses them defensively, and degrades to `{available: false, reason: ...}` if anything is unreachable or unparseable.

**Tech Stack:** Flask + Jinja2 templates, HTMX (vendored, `hx-trigger`/`hx-swap`), Python stdlib `configparser` + `urllib`, the Apple-design token CSS from the foundation. No new runtime dependencies.

---

## Scope guards

**IN this phase:**

- `dashboard.html` template (replaces `_placeholders/dashboard.html` usage)
- `dashboard.css` -- sidebar + grid layout + all card sub-element styles
- The six idle cards wired into the grid (5 reused/restyled + 1 new)
- `GET /api/dashcam/info` + `_compute_dashcam_info` (read-only inspection of `/Config/version.bin` + `/Config/config.ini`)
- `GET /hx/dashcam-info-card` fragment + `dashcam_info_card.html` partial
- `last_run_card.html` restyled to the dashboard card idiom + self-polling
- `dashcam_card.html` tweaked to self-load on page load + a "checking" shell state
- A non-functional (disabled) "Quick actions" sidebar section -- buttons are styled placeholders; their wiring is Phase 2C
- Tests + `docs/api.md` updates

**NOT in this phase (deferred to Phase 2C):**

- Active-mode SSE wiring, the live-progress hero, `body[data-state]` mode switching
- Sidebar control wiring: Sync now / Pause / Resume / Stop POST actions (buttons appear disabled in 2B)
- Alpine.js state machine

**NOT in this phase (deferred elsewhere):**

- Tier 2+ dashcam settings *writes* (`POST /upload.cgi`) -- future sub-project #7
- BDD scenarios for the dashboard -- these land in Phase 2C with the full interactive flow
- `sync.py` S3776 decomposition, multi-stage Dockerfile, `create_app` blueprint-loop refactor -- foundation carry-forwards

---

## Implementer guidelines (karpathy discipline)

1. **Think before coding.** State assumptions explicitly; if a step is ambiguous, report DONE_WITH_CONCERNS rather than guessing.
2. **Simplicity first.** Do not pull Phase 2C control wiring forward. The sidebar buttons are `disabled` placeholders in 2B -- no `hx-post`, no JS. The dashcam-info card shows real config but does not attempt to interpret or write settings.
3. **Surgical changes.** Touch only the files each task lists. The two Phase 2A/D partials being modified (`last_run_card.html`, `dashcam_card.html`) are changed only as the dashboard requires -- do not restructure unrelated code.
4. **Goal-driven execution.** Each task has a verification command. Run it before committing.

Process hygiene:

- Never `git add -A` / `git add .` -- stage files by name (avoids the developer-local `supertool` symlink).
- Never `--no-verify`. Pre-commit hooks must pass; fix the underlying error.
- Never amend a commit after a pre-commit auto-fix -- create a NEW commit.
- Comments lowercase, third-person, non-obvious. Entity names keep their casing.
- Commit-message titles ≤ 72 chars (gitlint).
- Use `venv/bin/pytest`, `venv/bin/python` -- the system pytest does not have the package installed.

---

## File Structure

### Files to create

- `blackvuesync/server/routes/api_dashcam.py` -- `api_dashcam_bp` blueprint (`/api/dashcam`), `_fetch_text`, `_parse_version_bin`, `_parse_config_ini`, `_compute_dashcam_info`, `_config_preview`, `GET /api/dashcam/info`
- `blackvuesync/server/templates/_partials/dashcam_info_card.html` -- the dashcam-info card fragment
- `blackvuesync/server/static/css/dashboard.css` -- dashboard layout (sidebar + grid), card sub-element styles, sidebar styles
- `blackvuesync/server/templates/dashboard.html` -- the real dashboard page (extends `base.html`)
- `test/test_routes_api_dashcam.py` -- tests for `/api/dashcam/info` and the parse helpers
- `test/test_dashboard_render.py` -- tests for the `GET /` dashboard page render

### Files to modify

- `blackvuesync/server/routes/hx_dashboard.py` -- add the `dashcam_info_card` fragment route
- `blackvuesync/server/routes/ui.py` -- `dashboard()` assembles the 4 local-card contexts and renders `dashboard.html`
- `blackvuesync/server/__init__.py` -- register `api_dashcam_bp` (alphabetically, between `api_auth_bp` and `api_health_bp`)
- `blackvuesync/server/templates/base.html` -- add an `{% block extra_css %}` slot in `<head>`
- `blackvuesync/server/templates/_partials/last_run_card.html` -- restyle to the dashboard card idiom + self-poll
- `blackvuesync/server/templates/_partials/dashcam_card.html` -- add `load` to the trigger + a "checking" shell state
- `test/test_routes_hx_sync.py` -- update `last_run_card` expectations after the restyle
- `test/test_routes_hx_dashboard.py` -- add a `TestDashcamInfoCard` class + update the dashcam-card test for the shell state
- `docs/api.md` -- document `GET /api/dashcam/info` and the new HTMX fragment
- `pyproject.toml` -- bump version `2.4.0a0` → `2.4.0a1`; add `test_routes_api_dashcam` and `test_dashboard_render` to the mypy test-module override list

### Files explicitly NOT to modify

- `blackvuesync/sync.py`, `blackvuesync/metrics.py`, `blackvuesync/settings.py`
- `blackvuesync/server/scheduler.py`, `sync_runner.py`, `progress.py`, `auth.py`, `_helpers.py`
- `blackvuesync/server/routes/api_health.py`, `api_recordings.py`, `api_schedule.py`, `api_sync.py`, `api_auth.py`, `api_settings.py`, `health.py`
- `blackvuesync/server/templates/_partials/sync_status_card.html` (active-mode hero -- Phase 2C)
- `blackvuesync/server/templates/_partials/{storage,next_scheduled,recent_activity}_card.html` (already correct from 2A)
- `Dockerfile`, `entrypoint.sh`, `docker-compose.yml`, `run.sh`

---

## Reference: existing patterns (read before starting)

- **Card partial idiom** (`_partials/storage_card.html`): root `<div class="card" id="<name>-card" hx-get="/hx/<name>-card" hx-trigger="every 5s" hx-swap="outerHTML">`, then `card-label` / `card-value` / `card-sub`. The `{% else %}` branch shows `--` + a `reason`.
- **HTMX fragment route idiom** (`hx_dashboard.py`): `@hx_dashboard_bp.route(...)` + `@login_required`, compute a context dict via a shared `_compute_*` helper, `render_template(partial, **ctx)`, return `Response(..., mimetype=_MIME_HTML)`.
- **Compute helper sharing** (`api_health.py`): the JSON route and the HTMX route both call the same `_compute_*` helper. The structural-unavailable contract is `{"available": False, "reason": "..."}`.
- **Defensive network probe** (`api_health.py:_compute_dashcam`): 2s timeout, `# NOSONAR` on the `http://` URL + `urlopen` (S5332, HTTP-only firmware), classify `URLError(reason=TimeoutError)` as a timeout.
- **Test fixture idiom** (`test/test_routes_hx_dashboard.py`): `_make_app(settings_path, destination)` seeds `ADDRESS` env + admin password; `logged_in_client` yields `(client, store, destination)`.
- **Design tokens** (`tokens.css`): use `var(--color-surface)`, `var(--color-label)`, `var(--color-label-secondary)`, `var(--color-separator)`, `var(--color-accent)`, `var(--color-success)`, `var(--color-error)`, `var(--space-N)`, `var(--radius-lg)`, `var(--shadow-subtle)`, `var(--text-*)`, `var(--font-family-mono)`. Dark mode is handled by the token file's media query -- never hardcode colors.

---

## Task 1: Version bump and mypy overrides

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Bump the version**

In `pyproject.toml`, change:

```toml
version = "2.4.0a0"
```

to:

```toml
version = "2.4.0a1"
```

- [ ] **Step 2: Add the two new test modules to the mypy override list**

Find the `[[tool.mypy.overrides]]` block whose `module = [...]` list contains entries like `"test_routes_api_health"` and `"test_routes_hx_dashboard"`. Append two entries to that list:

```toml
    "test_routes_api_dashcam",
    "test_dashboard_render",
```

(Keep every existing entry; only add these two.)

- [ ] **Step 3: Verify the editable install still resolves**

Run: `pip install -e ".[dev]"`
Expected: clean install, no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Phase 2B: bump to 2.4.0a1; add dashcam/dashboard test mypy overrides"
```

---

## Task 2: Dashcam-info backend (`/api/dashcam/info`)

**Files:**

- Create: `blackvuesync/server/routes/api_dashcam.py`
- Modify: `blackvuesync/server/__init__.py`
- Create: `test/test_routes_api_dashcam.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_api_dashcam.py`:

```python
"""tests for /api/dashcam/info and its parse helpers."""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import urllib.error
from io import BytesIO
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


def _make_app(settings_path: Path):  # type: ignore[no-untyped-def]
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(settings_path)
    pw_hash = hash_password("test-password-1234")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username="admin", password_hash=pw_hash),
        )
    )
    return create_app(store, testing=True), store


@pytest.fixture()
def logged_in_client(settings_path: Path):  # type: ignore[no-untyped-def]
    app, store = _make_app(settings_path)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, store


def _fake_response(body: bytes):  # type: ignore[no-untyped-def]
    """builds a context-manager stand-in for urlopen returning the given bytes."""

    class _Ctx:
        def __enter__(self) -> Any:
            return BytesIO(body)

        def __exit__(self, *a: Any) -> None:
            return None

    return _Ctx()


class TestParseHelpers:
    """unit tests for the version.bin and config.ini parsers."""

    def test_parse_version_bin_strips_control_chars(self) -> None:
        from blackvuesync.server.routes.api_dashcam import _parse_version_bin

        assert _parse_version_bin("DR900X-2.013\x00\x01") == "DR900X-2.013"

    def test_parse_config_ini_returns_nested_dict(self) -> None:
        from blackvuesync.server.routes.api_dashcam import _parse_config_ini

        text = "[Tab1]\nResolution=4K\n[Tab3]\nVoice=ON\n"
        parsed = _parse_config_ini(text)
        assert parsed["Tab1"]["Resolution"] == "4K"
        assert parsed["Tab3"]["Voice"] == "ON"

    def test_parse_config_ini_handles_missing_section_header(self) -> None:
        """legacy firmware may omit a leading section header; parser recovers."""
        from blackvuesync.server.routes.api_dashcam import _parse_config_ini

        parsed = _parse_config_ini("Resolution=4K\nVoice=ON\n")
        # the synthetic default section captures the header-less keys
        assert any("Resolution" in keys for keys in parsed.values())

    def test_config_preview_flattens_and_limits(self) -> None:
        from blackvuesync.server.routes.api_dashcam import _config_preview

        config = {"Tab1": {"A": "1", "B": "2"}, "Tab2": {"C": "3"}}
        preview = _config_preview(config, limit=2)
        assert preview == [("Tab1.A", "1"), ("Tab1.B", "2")]


class TestDashcamInfo:
    """tests for GET /api/dashcam/info."""

    def test_returns_available_with_parsed_data(self, logged_in_client: Any) -> None:
        client, _ = logged_in_client

        def _fake_urlopen(req: Any, timeout: float = 0) -> Any:
            url = req.full_url if hasattr(req, "full_url") else req
            if url.endswith("version.bin"):
                return _fake_response(b"DR900X-2.013")
            return _fake_response(b"[Tab1]\nResolution=4K\n")

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            resp = client.get("/api/dashcam/info")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["available"] is True
        assert body["firmware"] == "DR900X-2.013"
        assert body["config"]["Tab1"]["Resolution"] == "4K"
        assert body["setting_count"] == 1

    def test_returns_unavailable_when_unreachable(
        self, logged_in_client: Any
    ) -> None:
        client, _ = logged_in_client
        with patch(
            "urllib.request.urlopen", side_effect=socket.timeout("timed out")
        ):
            resp = client.get("/api/dashcam/info")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["available"] is False
        assert "reason" in body

    def test_returns_no_address_when_unconfigured(self) -> None:
        """unit-test the helper directly: ConnectionSettings rejects empty
        address so the route path cannot be exercised with one."""
        from blackvuesync.server.routes.api_dashcam import _compute_dashcam_info

        result = _compute_dashcam_info("")
        assert result["available"] is False
        assert result["reason"] == "no address configured"

    def test_partial_data_when_only_config_reachable(
        self, logged_in_client: Any
    ) -> None:
        """if version.bin fails but config.ini succeeds, still available."""
        client, _ = logged_in_client

        def _fake_urlopen(req: Any, timeout: float = 0) -> Any:
            url = req.full_url if hasattr(req, "full_url") else req
            if url.endswith("version.bin"):
                raise urllib.error.URLError("refused")
            return _fake_response(b"[Tab3]\nVoice=ON\n")

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            resp = client.get("/api/dashcam/info")
        body = json.loads(resp.data)
        assert body["available"] is True
        assert body.get("firmware") in (None, "")
        assert body["config"]["Tab3"]["Voice"] == "ON"

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.get("/api/dashcam/info")
        assert resp.status_code == 302
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_dashcam.py -v`
Expected: FAIL -- `blackvuesync.server.routes.api_dashcam` does not exist.

- [ ] **Step 3: Create `api_dashcam.py`**

Create `blackvuesync/server/routes/api_dashcam.py`:

```python
"""api dashcam routes: read-only inspection of on-camera config.

fetches and parses /Config/version.bin and /Config/config.ini from the
dashcam over http (blackvue firmware is http-only). all writes (changing
settings) are deliberately out of scope; that is a future sub-project.
"""

from __future__ import annotations

import configparser
import json
import socket
import urllib.error
import urllib.request

from flask import Blueprint, Response, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore

api_dashcam_bp = Blueprint("api_dashcam_bp", __name__, url_prefix="/api/dashcam")

_MIME_JSON = "application/json"

# default per-file timeout for the two config fetches; deliberately short so a
# slow or offline dashcam does not stall the dashboard card.
_FETCH_TIMEOUT = 2.0

# how many flattened config entries the card preview surfaces.
_PREVIEW_LIMIT = 8


def _fetch_text(url: str, timeout: float) -> str | None:
    """GETs url and returns its decoded body, or None on any failure.

    decodes with errors='replace' because version.bin is a binary-ish blob;
    callers clean it further. blackvue firmware is http-only, hence NOSONAR.
    """
    try:
        # NOSONAR suppresses python:S5332 (http-only firmware).
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # NOSONAR
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, socket.timeout, OSError):
        return None


def _parse_version_bin(text: str) -> str:
    """extracts a clean firmware/model string from version.bin content.

    keeps only printable characters and whitespace, then strips; the raw file
    can carry trailing nulls or control bytes.
    """
    cleaned = "".join(c for c in text if c.isprintable() or c == " ")
    return cleaned.strip()


def _parse_config_ini(text: str) -> dict[str, dict[str, str]]:
    """parses config.ini text into a {section: {key: value}} dict.

    uses a permissive parser (strict=False, no interpolation). legacy firmware
    may omit a leading section header; if so the text is retried under a
    synthetic [General] section so header-less keys are still captured.
    returns an empty dict if parsing fails entirely.
    """
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.MissingSectionHeaderError:
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            parser.read_string("[General]\n" + text)
        except configparser.Error:
            return {}
    except configparser.Error:
        return {}
    return {section: dict(parser.items(section)) for section in parser.sections()}


def _config_preview(
    config: dict[str, dict[str, str]], limit: int = _PREVIEW_LIMIT
) -> list[tuple[str, str]]:
    """flattens config to up to `limit` (section.key, value) pairs for display."""
    entries: list[tuple[str, str]] = []
    for section, keys in config.items():
        for key, value in keys.items():
            entries.append((f"{section}.{key}", value))
            if len(entries) >= limit:
                return entries
    return entries


def _compute_dashcam_info(
    address: str, timeout: float = _FETCH_TIMEOUT
) -> dict[str, object]:
    """fetches and parses the dashcam's version.bin + config.ini.

    factored out so /api/dashcam/info and /hx/dashcam-info-card share the same
    computation. returns {available: False, reason: ...} when no address is
    configured or both files are unreachable; otherwise returns the parsed
    firmware string and config dict (either may be partial).
    """
    if not address:
        return {"available": False, "reason": "no address configured"}

    # NOSONAR suppresses python:S5332 (http-only firmware).
    firmware_raw = _fetch_text(f"http://{address}/Config/version.bin", timeout)  # NOSONAR
    config_raw = _fetch_text(f"http://{address}/Config/config.ini", timeout)  # NOSONAR

    if firmware_raw is None and config_raw is None:
        return {"available": False, "reason": "dashcam unreachable"}

    config = _parse_config_ini(config_raw) if config_raw else {}
    return {
        "available": True,
        "address": address,
        "firmware": _parse_version_bin(firmware_raw) if firmware_raw else None,
        "config": config,
        "setting_count": sum(len(keys) for keys in config.values()),
    }


@api_dashcam_bp.route("/info", methods=["GET"])
@login_required
def info() -> Response:
    """returns read-only dashcam firmware + config information."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    address = store.get().connection.address
    body = json.dumps(_compute_dashcam_info(address))
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_dashcam_bp"]
```

- [ ] **Step 4: Register the blueprint**

In `blackvuesync/server/__init__.py`, add the import alphabetically (after the `api_auth` import, before `api_health`):

```python
from blackvuesync.server.routes.api_dashcam import api_dashcam_bp
```

And add the registration alphabetically (after `app.register_blueprint(api_auth_bp)`, before `api_health_bp`):

```python
    app.register_blueprint(api_dashcam_bp)
```

If pylint reports `too-many-locals` on `create_app` again after this addition, the suppression comment added in Phase 2A is already present -- confirm it still covers the function; do not refactor `create_app` in this task.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_dashcam.py -v`
Expected: 9 PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_dashcam.py blackvuesync/server/__init__.py test/test_routes_api_dashcam.py
git commit -m "Phase 2B: GET /api/dashcam/info read-only config inspection"
```

---

## Task 3: Dashcam-info HTMX fragment + partial

**Files:**

- Modify: `blackvuesync/server/routes/hx_dashboard.py`
- Create: `blackvuesync/server/templates/_partials/dashcam_info_card.html`
- Modify: `test/test_routes_hx_dashboard.py`

- [ ] **Step 1: Add the failing test**

Append to `test/test_routes_hx_dashboard.py` (after the existing `TestRecentActivityCard` class):

```python
class TestDashcamInfoCard:
    """tests for GET /hx/dashcam-info-card."""

    def test_renders_available(self, logged_in_client: Any) -> None:
        from io import BytesIO

        client, _, _ = logged_in_client

        class _Ctx:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return BytesIO(b"[Tab1]\nResolution=4K\n")

            def __exit__(self, *a):  # type: ignore[no-untyped-def]
                return None

        def _fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
            url = req.full_url if hasattr(req, "full_url") else req
            if url.endswith("version.bin"):
                class _V:
                    def __enter__(self_inner):  # type: ignore[no-untyped-def]
                        return BytesIO(b"DR900X-2.013")

                    def __exit__(self_inner, *a):  # type: ignore[no-untyped-def]
                        return None

                return _V()
            return _Ctx()

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            resp = client.get("/hx/dashcam-info-card")
        assert resp.status_code == 200
        assert "text/html" in resp.content_type
        assert b"dashcam-info-card" in resp.data
        assert b"DR900X-2.013" in resp.data
        assert b"Tab1.Resolution" in resp.data

    def test_renders_unavailable(self, logged_in_client: Any) -> None:
        import socket

        client, _, _ = logged_in_client
        with patch(
            "urllib.request.urlopen", side_effect=socket.timeout("timed out")
        ):
            resp = client.get("/hx/dashcam-info-card")
        assert resp.status_code == 200
        assert b"dashcam-info-card" in resp.data
```

- [ ] **Step 2: Run the failing test**

Run: `venv/bin/pytest test/test_routes_hx_dashboard.py::TestDashcamInfoCard -v`
Expected: FAIL -- `/hx/dashcam-info-card` does not exist (404).

- [ ] **Step 3: Create the partial**

Create `blackvuesync/server/templates/_partials/dashcam_info_card.html`:

```jinja2
{# dashcam-info card fragment. loads once on page load, refreshes every 60s
   (config is near-static and the fetch is two files, so it polls slower than
   the 5s cards). when rendered as a server-side shell, `available` is
   undefined and the card shows a loading state. #}
<div class="card"
     id="dashcam-info-card"
     hx-get="/hx/dashcam-info-card"
     hx-trigger="load, every 60s"
     hx-swap="outerHTML">
  <div class="card-label">Dashcam info</div>
  {% if available is defined and available %}
    <div class="card-value">{{ firmware or "connected" }}</div>
    {% if entries %}
      <div class="card-sub">{{ setting_count }} settings</div>
      <dl class="dashcam-config">
        {% for label, value in entries %}
          <div class="row"><dt class="file">{{ label }}</dt><dd>{{ value }}</dd></div>
        {% endfor %}
      </dl>
    {% else %}
      <div class="card-sub">no readable config</div>
    {% endif %}
  {% elif available is defined %}
    <div class="card-value">--</div>
    <div class="card-sub">{{ reason }}</div>
  {% else %}
    <div class="card-value">checking…</div>
  {% endif %}
</div>
```

- [ ] **Step 4: Add the fragment route**

In `blackvuesync/server/routes/hx_dashboard.py`, add this import near the top (with the other `from blackvuesync.server.routes...` imports):

```python
from blackvuesync.server.routes.api_dashcam import _compute_dashcam_info, _config_preview
```

Then append this route before the `__all__` line:

```python
@hx_dashboard_bp.route("/dashcam-info-card", methods=["GET"])
@login_required
def dashcam_info_card() -> Response:
    """renders the dashcam-info card fragment (firmware + config preview)."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    address = store.get().connection.address
    ctx = _compute_dashcam_info(address)
    entries = _config_preview(ctx["config"]) if ctx.get("available") else []
    return Response(
        render_template("_partials/dashcam_info_card.html", entries=entries, **ctx),
        mimetype=_MIME_HTML,
    )
```

Note: `ctx` already contains `available`, `firmware`, `config`, `setting_count` (or `available`/`reason`), so `**ctx` supplies the template variables; `entries` is added on top.

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_routes_hx_dashboard.py::TestDashcamInfoCard -v`
Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/hx_dashboard.py blackvuesync/server/templates/_partials/dashcam_info_card.html test/test_routes_hx_dashboard.py
git commit -m "Phase 2B: /hx/dashcam-info-card fragment and partial"
```

---

## Task 4: Restyle the Last-sync card + self-poll

**Files:**

- Modify: `blackvuesync/server/templates/_partials/last_run_card.html`
- Modify: `test/test_routes_hx_sync.py`

- [ ] **Step 1: Read the current test expectations**

Run: `venv/bin/pytest test/test_routes_hx_sync.py -v`
Expected: PASS (record what's currently asserted; you will keep the route behavior but the markup changes).

Read `test/test_routes_hx_sync.py` and note any assertions on the old markup (`last-run-card`, `Last Sync Run`, `badge`, `last-run-details`, `no completed sync recorded`).

- [ ] **Step 2: Rewrite the partial to the dashboard card idiom + self-poll**

Replace the entire contents of `blackvuesync/server/templates/_partials/last_run_card.html` with:

```jinja2
{# last-sync card fragment. self-polls every 5s against the same route that
   renders it, matching the other dashboard cards' idiom. #}
<div class="card"
     id="last-run-card"
     hx-get="/hx/sync/last-run-card"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Last sync</div>
  {% if snap.state == "idle" %}
    <div class="card-value">--</div>
    <div class="card-sub">no completed sync recorded</div>
  {% else %}
    <div class="card-value">
      <span class="badge badge-{{ snap.state }}">{{ snap.state }}</span>
    </div>
    <div class="card-sub">
      {{ snap.files_completed }} files ·
      {{ snap.bytes_downloaded_total | filesizeformat }}
      {% if snap.files_failed > 0 %} · {{ snap.files_failed }} failed{% endif %}
    </div>
  {% endif %}
</div>
```

The route (`/hx/sync/last-run-card` in `hx_sync.py`) is unchanged -- it still passes `snap`. Only the markup changes: `card`/`card-label`/`card-value`/`card-sub` now match the other cards, the root `id` stays `last-run-card`, and `hx-trigger="every 5s"` makes it self-refresh.

- [ ] **Step 3: Update the test expectations**

In `test/test_routes_hx_sync.py`, update the `last-run-card` assertions to match the new markup. Keep assertions that still hold (`id="last-run-card"`, the route returns 200, login redirect). Replace any assertion on removed text (e.g. `Last Sync Run`, `last-run-details`, `no completed sync recorded` text in a `<p>`) with assertions on the new structure. The minimum the test should assert:

- `resp.status_code == 200`
- `b"last-run-card" in resp.data`
- `b"card-label" in resp.data`
- when idle: `b"no completed sync recorded" in resp.data`
- `b"hx-trigger" in resp.data` (now self-polls)

If the existing test seeds a non-idle snapshot, update it to assert the badge/state appears (`b"badge-" in resp.data`).

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest test/test_routes_hx_sync.py -v`
Expected: PASS (all existing tests, adjusted to the new markup).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/templates/_partials/last_run_card.html test/test_routes_hx_sync.py
git commit -m "Phase 2B: restyle last-sync card to dashboard idiom; self-poll"
```

---

## Task 5: Dashcam reachability card -- load trigger + shell state

**Files:**

- Modify: `blackvuesync/server/templates/_partials/dashcam_card.html`
- Modify: `test/test_routes_hx_dashboard.py`

- [ ] **Step 1: Add the failing test for the shell state**

In `test/test_routes_hx_dashboard.py`, the existing `TestDashcamCard.test_renders_html` already covers the populated path (it mocks `urlopen` to a timeout). Add one test to the `TestDashcamCard` class for the server-side shell (no context → "checking"):

```python
    def test_partial_renders_checking_shell_without_context(self) -> None:
        """rendered with no context (the SSR shell case), the card shows a
        'checking' state rather than a misleading 'unreachable'."""
        from flask import render_template

        from blackvuesync.server import create_app
        from blackvuesync.settings import SettingsStore
        import dataclasses
        import os
        from unittest.mock import patch
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
                store = SettingsStore(path)
            app = create_app(store, testing=True)
            with app.app_context():
                html = render_template("_partials/dashcam_card.html")
        assert "checking" in html.lower()
        assert "dashcam-card" in html
```

- [ ] **Step 2: Run the failing test**

Run: `venv/bin/pytest "test/test_routes_hx_dashboard.py::TestDashcamCard::test_partial_renders_checking_shell_without_context" -v`
Expected: FAIL -- the current partial renders the "unreachable" branch (or errors) when `reachable` is undefined.

- [ ] **Step 3: Update `dashcam_card.html`**

Replace the entire contents of `blackvuesync/server/templates/_partials/dashcam_card.html` with:

```jinja2
{# dashcam reachability card. loads on page load, then polls every 5s. when
   rendered as a server-side shell (no context), shows a 'checking' state. #}
<div class="card"
     id="dashcam-card"
     hx-get="/hx/dashcam-card"
     hx-trigger="load, every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Dashcam</div>
  {% if reachable is defined and reachable %}
    <div class="card-value"><span class="dot green"></span>reachable</div>
    <div class="card-sub">{{ address }} · {{ latency_ms }} ms</div>
  {% elif reachable is defined %}
    <div class="card-value"><span class="dot red"></span>unreachable</div>
    <div class="card-sub">{{ address or "no address configured" }} · {{ reason }}</div>
  {% else %}
    <div class="card-value">checking…</div>
  {% endif %}
</div>
```

The only changes from the Phase 2A version: `hx-trigger="load, every 5s"` (was `every 5s`), and a third `{% else %}` branch for the shell state (`reachable` undefined).

- [ ] **Step 4: Run the dashcam-card tests**

Run: `venv/bin/pytest test/test_routes_hx_dashboard.py::TestDashcamCard -v`
Expected: all PASS (the existing populated test + the new shell test).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/templates/_partials/dashcam_card.html test/test_routes_hx_dashboard.py
git commit -m "Phase 2B: dashcam card loads on page load; add checking shell"
```

---

## Task 6: Dashboard CSS

**Files:**

- Create: `blackvuesync/server/static/css/dashboard.css`

This task has no unit test (CSS); it is verified visually in Task 9's manual step and structurally by the dashboard-render test in Task 8. Write the file, confirm it parses (no test), commit.

- [ ] **Step 1: Create `dashboard.css`**

Create `blackvuesync/server/static/css/dashboard.css`:

```css
/* dashboard layout: sidebar + responsive card grid, plus card sub-elements.
   all colors come from tokens.css, so light/dark mode is automatic. */

/* ---------------------------------------------------------------------------
   layout: sidebar + grid
   --------------------------------------------------------------------------- */

.dashboard {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: var(--space-6);
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-4);
}

.dashboard-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--space-4);
  align-content: start;
}

@media (max-width: 720px) {
  .dashboard {
    grid-template-columns: 1fr;
  }
}

/* ---------------------------------------------------------------------------
   sidebar
   --------------------------------------------------------------------------- */

.dashboard-sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.sidebar-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.sidebar-label {
  font-size: var(--text-caption1);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-label-secondary);
}

.sidebar-identity .identity-name {
  font-size: var(--text-subheadline);
  font-weight: 600;
  color: var(--color-label);
}

.sidebar-identity .identity-mode {
  font-size: var(--text-footnote);
  color: var(--color-label-secondary);
}

/* quick-action buttons are disabled placeholders in phase 2b; phase 2c wires
   them. the disabled style signals "coming soon" honestly. */
.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.sidebar-hint {
  font-size: var(--text-caption1);
  color: var(--color-label-tertiary);
}

/* ---------------------------------------------------------------------------
   card sub-elements (referenced by the card partials; styled here)
   --------------------------------------------------------------------------- */

.card-label {
  font-size: var(--text-caption1);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-label-secondary);
}

.card-value {
  font-size: var(--text-title3);
  font-weight: 600;
  color: var(--color-label);
  margin-top: var(--space-1);
}

.card-sub {
  font-size: var(--text-footnote);
  color: var(--color-label-secondary);
  margin-top: var(--space-1);
}

/* storage usage bar */
.storage-bar {
  height: 6px;
  border-radius: var(--radius-sm);
  background-color: var(--color-fill);
  margin-top: var(--space-3);
  overflow: hidden;
}

.storage-bar > div {
  height: 100%;
  background-color: var(--color-accent);
}

/* reachability dot */
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: var(--space-2);
  vertical-align: middle;
}

.dot.green {
  background-color: var(--color-success);
}

.dot.red {
  background-color: var(--color-error);
}

/* recent-activity + dashcam-config stacked rows */
.card-stack,
.dashcam-config {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: var(--space-3);
}

.card-stack .row,
.dashcam-config .row {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--text-footnote);
  color: var(--color-label-secondary);
}

.file {
  font-family: var(--font-family-mono);
  font-size: var(--text-caption1);
}

/* status badge on the last-sync card */
.badge {
  display: inline-block;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-caption1);
  font-weight: 600;
}

.badge-complete {
  background-color: color-mix(in srgb, var(--color-success) 18%, transparent);
  color: var(--color-success);
}

.badge-failed {
  background-color: color-mix(in srgb, var(--color-error) 16%, transparent);
  color: var(--color-error);
}

.badge-running {
  background-color: color-mix(in srgb, var(--color-accent) 16%, transparent);
  color: var(--color-accent);
}
```

- [ ] **Step 2: Confirm the file is valid CSS (no syntax errors)**

Run: `venv/bin/python -c "import pathlib; t = pathlib.Path('blackvuesync/server/static/css/dashboard.css').read_text(); assert t.count('{') == t.count('}'), 'unbalanced braces'; print('braces balanced:', t.count('{'))"`
Expected: prints `braces balanced: <n>` with no assertion error.

- [ ] **Step 3: Commit**

```bash
git add blackvuesync/server/static/css/dashboard.css
git commit -m "Phase 2B: dashboard CSS (sidebar, grid, card sub-elements)"
```

---

## Task 7: base.html extra-CSS slot + dashboard.html template

**Files:**

- Modify: `blackvuesync/server/templates/base.html`
- Create: `blackvuesync/server/templates/dashboard.html`

- [ ] **Step 1: Add an `extra_css` block to `base.html`**

In `blackvuesync/server/templates/base.html`, the `<head>` currently ends with the three stylesheet links then `</head>`. Add an `{% block extra_css %}{% endblock %}` immediately after the `components.css` link and before `</head>`:

```html
  <link rel="stylesheet" href="{{ url_for('static', filename='css/components.css') }}">
  {% block extra_css %}{% endblock %}
</head>
```

This keeps `dashboard.css` off the login / first-run / placeholder pages (only the dashboard fills the block).

- [ ] **Step 2: Create `dashboard.html`**

Create `blackvuesync/server/templates/dashboard.html`:

```jinja2
{% extends "base.html" %}

{% block title %}Dashboard -- BlackVue Sync{% endblock %}

{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/dashboard.css') }}">
{% endblock %}

{% block content %}
<div class="dashboard">
  <aside class="dashboard-sidebar">
    <div class="sidebar-section sidebar-identity">
      <span class="sidebar-label">Signed in</span>
      <span class="identity-name">{{ g.current_user }}</span>
      <span class="identity-mode">{{ auth_mode }} mode</span>
    </div>
    <div class="sidebar-section">
      <span class="sidebar-label">Quick actions</span>
      <div class="sidebar-actions">
        <button type="button" class="button button-secondary button-full" disabled>Sync now</button>
        <button type="button" class="button button-secondary button-full" disabled>Pause schedule</button>
      </div>
      <span class="sidebar-hint">Live controls arrive in the next update.</span>
    </div>
  </aside>

  <div class="dashboard-grid">
    {# local cards: pre-rendered populated in the route (each with its own
       isolated context, so the shared `available` key cannot collide across
       cards), injected here as already-escaped html. they then self-poll. #}
    {{ last_run_html | safe }}
    {{ next_scheduled_html | safe }}
    {{ storage_html | safe }}
    {{ recent_activity_html | safe }}
    {# network cards: shells; htmx loads them after the page paints #}
    {% include "_partials/dashcam_card.html" %}
    {% include "_partials/dashcam_info_card.html" %}
  </div>
</div>
{% endblock %}
```

The four local cards are pre-rendered to HTML strings by the `dashboard()` route (Task 8) -- each via its own `render_template(partial, **ctx)` call, so each partial sees only its own context and the shared `available` key never collides between the storage card and the dashcam cards. The strings are injected with `| safe` (they were already auto-escaped during their own render). The two network-card partials are `{% include %}`d without context, so they render their shell states (`dashcam_card.html` → "checking…", `dashcam_info_card.html` → "checking…") and HTMX fetches their real content on `load`.

- [ ] **Step 3: Confirm both templates parse (Jinja syntax)**

Run:

```bash
venv/bin/python -c "
import os
from unittest.mock import patch
with patch.dict(os.environ, {'ADDRESS': '192.168.0.1'}, clear=False):
    from blackvuesync.settings import SettingsStore
    import tempfile
    d = tempfile.mkdtemp()
    store = SettingsStore(os.path.join(d, 'settings.json'))
    from blackvuesync.server import create_app
    app = create_app(store, testing=True)
    with app.app_context():
        app.jinja_env.get_template('dashboard.html')
        print('dashboard.html parses')
"
```

Expected: prints `dashboard.html parses` (template compiles; rendering with full context is tested in Task 8).

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/templates/base.html blackvuesync/server/templates/dashboard.html
git commit -m "Phase 2B: dashboard.html template + base extra_css slot"
```

---

## Task 8: Wire `dashboard()` route to render the real dashboard

**Files:**

- Modify: `blackvuesync/server/routes/ui.py`
- Create: `test/test_dashboard_render.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_dashboard_render.py`:

```python
"""tests for the GET / dashboard page render (idle mode, server-side)."""

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


class TestDashboardRender:
    """tests for GET / rendering the real dashboard, not the placeholder."""

    def test_renders_dashboard_not_placeholder(self, logged_in_client: Any) -> None:
        client, _, _ = logged_in_client
        resp = client.get("/")
        assert resp.status_code == 200
        # the placeholder said "coming in sub-project #2"; the real one does not
        assert b"coming in sub-project" not in resp.data
        assert b"dashboard-grid" in resp.data
        assert b"dashboard-sidebar" in resp.data

    def test_includes_all_six_card_ids(self, logged_in_client: Any) -> None:
        client, _, _ = logged_in_client
        resp = client.get("/")
        for card_id in (
            b"last-run-card",
            b"next-scheduled-card",
            b"storage-card",
            b"recent-activity-card",
            b"dashcam-card",
            b"dashcam-info-card",
        ):
            assert card_id in resp.data, f"missing {card_id!r}"

    def test_local_cards_are_server_rendered_populated(
        self, logged_in_client: Any
    ) -> None:
        """the four local cards render with real data on first paint (no JS),
        so the storage card shows a percentage and the recent card shows the
        seeded recording."""
        client, _, destination = logged_in_client
        (destination / "20231015_120000_NF.mp4").write_text("x")
        resp = client.get("/")
        # storage card SSR-populated: shows "% used"
        assert b"% used" in resp.data
        # recent-activity card SSR-populated: shows the seeded file
        assert b"20231015_120000_NF.mp4" in resp.data

    def test_network_cards_render_as_shells(self, logged_in_client: Any) -> None:
        """the two dashcam cards are shells on first paint (no network call in
        the route); they show 'checking' and self-load via htmx."""
        client, _, _ = logged_in_client
        resp = client.get("/")
        # both dashcam cards present and in the checking shell state
        assert resp.data.lower().count(b"checking") >= 2

    def test_does_no_network_io_on_render(self, logged_in_client: Any) -> None:
        """rendering the dashboard must not probe the dashcam (the cards do
        that asynchronously). if urlopen were called, this patch would raise."""
        client, _, _ = logged_in_client

        def _boom(*a: Any, **k: Any) -> None:
            raise AssertionError("dashboard render must not call urlopen")

        with patch("urllib.request.urlopen", side_effect=_boom):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        destination = tmp_path / "recordings"
        destination.mkdir()
        app, _ = _make_app(settings_path, destination)
        with app.test_client() as client:
            resp = client.get("/")
        assert resp.status_code == 302
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_dashboard_render.py -v`
Expected: FAIL -- `dashboard()` still renders the placeholder (`coming in sub-project #2`).

- [ ] **Step 3: Update `dashboard()` in `ui.py`**

The card partials read top-level variables (`available`, `used_percent`, `reachable`, …). If `dashboard()` spread all card contexts into one template scope, the shared `available` key would collide -- the storage card's `available` would leak into the dashcam shells and make them render the wrong branch. To avoid this entirely, `dashboard()` pre-renders each local card to an HTML string in its OWN `render_template(partial, **ctx)` call (so each partial sees only its own context), and the page template injects those strings with `| safe` (they were already auto-escaped during their own render -- no double-escape, no XSS, since filenames and config values were escaped at partial-render time).

In `blackvuesync/server/routes/ui.py`, add these imports near the top (after the existing imports):

```python
from pathlib import Path

from flask import current_app

from blackvuesync.server.routes.api_health import _compute_storage
from blackvuesync.server.routes.api_recordings import _DEFAULT_LIMIT, _compute_recent
from blackvuesync.server.routes.hx_dashboard import _next_human
```

(`_next_human` is a cross-route private import, consistent with the existing pattern in `hx_dashboard.py`, which imports `_compute_storage` etc. from `api_health`.)

Replace the existing `dashboard()` function with:

```python
@bp.route("/", methods=["GET"])
@login_required
def dashboard() -> str:
    """renders the real dashboard.

    the four local cards (last sync, next scheduled, storage, recent activity)
    are pre-rendered populated -- each via its own render_template call so the
    shared `available` key cannot collide across cards -- and injected into the
    page with | safe. the page is therefore useful without javascript and
    paints instantly. the two network cards (dashcam reachability, dashcam
    info) are included as shells and fetched by htmx after load, so a slow or
    offline dashcam never blocks the page render.
    """
    store = current_app.settings_store  # type: ignore[attr-defined]
    settings = store.get()
    destination = Path(settings.system.destination)
    publisher = current_app.progress_publisher  # type: ignore[attr-defined]
    schedule = settings.schedule

    last_run_html = render_template(
        "_partials/last_run_card.html", snap=publisher.snapshot()
    )
    next_scheduled_html = render_template(
        "_partials/next_scheduled_card.html",
        paused=schedule.paused,
        cron_expression=schedule.cron_expression,
        timezone=schedule.timezone,
        next_human=_next_human(schedule.cron_expression, schedule.timezone),
    )
    storage_html = render_template(
        "_partials/storage_card.html", **_compute_storage(destination)
    )
    recent_activity_html = render_template(
        "_partials/recent_activity_card.html",
        **_compute_recent(destination, _DEFAULT_LIMIT),
    )

    return render_template(
        "dashboard.html",
        version=__version__,
        page="dashboard",
        auth_mode=settings.auth.mode,
        last_run_html=last_run_html,
        next_scheduled_html=next_scheduled_html,
        storage_html=storage_html,
        recent_activity_html=recent_activity_html,
    )
```

The page template (Task 7) injects `{{ last_run_html | safe }}` etc. for the four local cards and `{% include %}`s the two dashcam shells. Because each local card was rendered in isolation, no `available` collision occurs, and the dashcam shells receive no `available` at all → they render their "checking" state.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest test/test_dashboard_render.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Run the full server test suite to confirm no regression**

Run: `venv/bin/pytest test/ -q`
Expected: all pass (the new tests plus everything from Phase 2A and the foundation).

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/dashboard.html test/test_dashboard_render.py
git commit -m "Phase 2B: render real dashboard with SSR local cards"
```

---

## Task 9: Manual smoke verification

**Files:** none (manual)

- [ ] **Step 1: Start the server**

```bash
BLACKVUESYNC_CONFIG_PATH=/tmp/bvs-2b-settings.json \
ADDRESS=192.0.2.1 \
venv/bin/python -m blackvuesync serve
```

- [ ] **Step 2: Walk the dashboard in a browser**

Open `http://localhost:8080/`, complete first-run if prompted, then verify:

- The sidebar shows "Signed in / admin / login mode" and two disabled "Quick actions" buttons with the hint text.
- The grid shows six cards. The four local cards (last sync, next scheduled, storage, recent activity) are populated immediately.
- The two dashcam cards briefly show "checking…", then resolve. Against the unroutable `192.0.2.1` they settle on "unreachable" / "--" within a couple of seconds -- and the rest of the page was usable the entire time (no blocking).
- Toggle the OS into dark mode: the palette flips without a reload (system-aware tokens).
- Narrow the window below 720px: the sidebar stacks above the grid; cards reflow to one column.

- [ ] **Step 3: Stop the server and clean up**

`Ctrl-C`, then `rm -f /tmp/bvs-2b-settings.json`.

(No commit -- this task is verification only.)

---

## Task 10: Document the new endpoints in `docs/api.md`

**Files:**

- Modify: `docs/api.md`

- [ ] **Step 1: Append a Dashcam API section**

After the existing "Health API Endpoints" section in `docs/api.md`, add:

```markdown

---

## Dashcam API Endpoints

### `GET /api/dashcam/info`

Read-only inspection of the dashcam's on-camera configuration. Fetches
`http://<address>/Config/version.bin` and `http://<address>/Config/config.ini`
(BlackVue firmware is HTTP-only), parses them defensively, and returns
structured JSON. Changing settings is deliberately out of scope (a future
sub-project); this endpoint never writes to the camera.

Available:

```json
{
  "available": true,
  "address": "192.168.1.50",
  "firmware": "DR900X-2.013",
  "config": {"Tab1": {"Resolution": "4K"}, "Tab3": {"Voice": "ON"}},
  "setting_count": 2
}
```

`firmware` may be `null` if `version.bin` was unreachable while `config.ini`
succeeded (partial availability still reports `available: true`).

Unreachable or no address configured:

```json
{"available": false, "reason": "dashcam unreachable"}
```

```json
{"available": false, "reason": "no address configured"}
```
```

- [ ] **Step 2: Add the HTMX fragment to the fragments section**

In the "HTMX Fragment Endpoints" area of `docs/api.md`, add the new card to the dashboard-fragments list:

```markdown
- `GET /hx/dashcam-info-card` -- renders `_partials/dashcam_info_card.html`. Loads once on page load and refreshes every 60s (config is near-static and the fetch is two files, so it polls slower than the 5s cards).
```

- [ ] **Step 3: Sanity-check the markdown**

Run: `pre-commit run markdownlint-cli2 --files docs/api.md`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/api.md
git commit -m "Phase 2B: document /api/dashcam/info and dashcam-info fragment"
```

---

## Task 11: Final sweep

**Files:** none (verification + fixups)

- [ ] **Step 1: Full unit suite**

Run: `venv/bin/pytest test/ -q`
Expected: all pass. Phase 2A ended at 439 tests; Phase 2B adds ~17 (9 dashcam + 2 hx dashcam-info + 1 dashcam-card shell + 6 dashboard-render, minus any that replace existing assertions). Verify a clean run with no failures.

- [ ] **Step 2: Behave (subprocess)**

Run: `venv/bin/behave`
Expected: 21/21 scenarios. Phase 2B adds no BDD; this is a regression guard.

- [ ] **Step 3: Behave (docker)**

Run: `venv/bin/behave -D implementation=docker`
Expected: 21/21 scenarios. The dashboard UI is not exercised by the existing docker scenarios; this confirms the new blueprint registration and template changes did not break container startup.

- [ ] **Step 4: Pre-commit on all files**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 5: Confirm the commit sequence**

Run: `git log --oneline sub-project-2-phase-b ^main`
Expected: the spec-amendment commit (`6d11929`) plus the Phase 2B task commits, in order.

---

## Task 12: Open the PR (controlling agent only)

This task is **reserved for the controlling agent**. The implementer must NOT push or open a PR. The controlling agent:

1. `git push -u origin sub-project-2-phase-b`
2. `gh pr create --repo tekgnosis-net/blackvuesync --base main --head sub-project-2-phase-b --title "Sub-Project #2 Phase 2B: dashboard idle UI + dashcam info" --body ...`
3. Watches the 5 required CI checks; verifies 0 SonarCloud findings.
4. Squash-merges once green.

---

## Self-review against spec

| Spec requirement (design + 2026-05-20 amendment) | Plan task |
| --- | --- |
| `dashboard.html` replaces the placeholder | Tasks 7 + 8 |
| Sidebar (220px) + responsive card grid (Layout C) | Tasks 6 + 7 |
| Six idle cards rendered | Task 7 (grid) + Tasks 3/4/5 (cards) |
| System-aware light/dark | Task 6 (consumes tokens.css media query) |
| HTMX 5s polling for idle cards | Tasks 4 + 5 (and existing 2A partials) |
| `GET /api/dashcam/info` (read-only `version.bin` + `config.ini`) | Task 2 |
| `GET /hx/dashcam-info-card` + partial | Task 3 |
| Last-sync card consistent + self-poll | Task 4 |
| Network cards do not block SSR | Tasks 5 + 8 (shells + no-network-IO render) |
| JS-disabled shows populated local cards | Task 8 (SSR local cards; `test_local_cards_are_server_rendered_populated`) |
| Tier 2+ writes deferred to sub-project #7 | Out-of-scope guard; not implemented |
| Sidebar controls wired | Deferred to Phase 2C (disabled placeholders in 2B) |
| `docs/api.md` updated | Task 10 |
| Version bump | Task 1 |

## What is NOT done in Phase 2B (recap)

- Active-mode SSE, live-progress hero, `body[data-state]` switching -- Phase 2C
- Sidebar Sync now / Pause / Resume / Stop wiring -- Phase 2C
- Dashcam settings writes -- sub-project #7
- BDD dashboard scenarios -- Phase 2C
- `sync.py` S3776, multi-stage Dockerfile, `create_app` blueprint-loop refactor -- foundation carry-forwards
