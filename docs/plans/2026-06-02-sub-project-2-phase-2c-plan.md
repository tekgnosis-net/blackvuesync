# Sub-Project #2 Phase 2C -- Active Mode + Controls -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the read-only dashboard interactive: a live SSE-driven hero during a running sync, `body[data-state]` mode switching, and working Sync-now / Stop (modal-confirmed) / Pause-Resume controls -- all wired to the already-built backend.

**Architecture:** `<body data-state="idle|running|complete">` is the single source of truth; CSS alone shows/hides the idle grid vs. the active hero. An Alpine.js component (`dashboard.js`) opens an `EventSource` to the existing `/api/sync/progress/stream` when running, feeds each JSON frame into a reactive `progress` object the hero renders, mutates `data-state`, and reconnects with exponential backoff. Controls POST to existing endpoints with the CSRF header. No backend endpoints are added.

**Tech Stack:** Jinja2 + Alpine.js (vendored) + HTMX (vendored), Flask SSE (existing), pytest + Flask test client (structure tests), pytest-playwright (browser smoke).

**Design:** `docs/plans/2026-06-01-sub-project-2-phase-2c-design.md`.

**Backend contract (already exists -- do not modify):**

- `POST /api/sync/now` → 202 `{job_id}` / 409 `{code:"SYNC_ALREADY_RUNNING"}`.
- `POST /api/sync/stop` → 202 `{job_id,stopping:true}` / 404 `{code:"SYNC_NOT_RUNNING"}`.
- `POST /api/schedule/pause` and `/api/schedule/resume` → 200 `{paused}`.
- `GET /api/sync/progress/stream` → SSE `event: progress\ndata: <json>\n\n`, `: keepalive\n\n` heartbeats. Headers `Cache-Control: no-store`, `X-Accel-Buffering: no`.
- Snapshot JSON keys (from `_snap_to_dict`): `job_id, started_at_wall, state, current_file, files_total, files_completed, files_failed, bytes_downloaded_total, last_event_monotonic, percent`. `state ∈ {idle,running,complete,failed}`. `current_file` is null or `{filename, artifact, direction, percent, elapsed_seconds, ...}`.
- CSRF: Flask-WTF validates `X-CSRFToken` on all POSTs. `app.js` already adds it to every HTMX request; `fetch()` calls must add it from `<meta name="csrf-token">`.

---

## File structure

**Create:**

- `blackvuesync/server/static/js/dashboard.js` -- Alpine component: SSE lifecycle, controls, `data-state`, backoff, 302 handling.
- `blackvuesync/server/templates/_partials/stop_confirm_modal.html` -- Stop confirm modal (Alpine-driven).
- `test/test_dashboard_sse_handoff.py` -- server-rendered structure tests (pytest + Flask client).
- `test/e2e/conftest.py` -- `live_server` fixture (werkzeug server in a daemon thread).
- `test/e2e/test_dashboard_active_mode.py` -- Playwright smoke (idle→running→complete; Stop modal).

**Modify:**

- `blackvuesync/server/templates/base.html` -- `data-state` on `<body>` (block-overridable) + `{% block extra_js %}`.
- `blackvuesync/server/templates/dashboard.html` -- `x-data` wrapper, wire the three controls, active-mode hero region, include the modal, set `data_state`, load `dashboard.js`.
- `blackvuesync/server/static/css/dashboard.css` -- `body[data-state]` show/hide, `.active-only`/`.idle-only`, hero, modal, control states.
- `blackvuesync/server/routes/ui.py:20-66` -- pass initial `sync_state` and `paused` to `dashboard.html`.
- `pyproject.toml` -- version bump; add `pytest-playwright` test dep; add new test modules to mypy overrides.
- `docs/api.md` -- note the dashboard now drives sync/stop/pause from the UI (no new endpoints).

---

### Task 1: `base.html` -- body `data-state` + `extra_js` block

**Files:**

- Modify: `blackvuesync/server/templates/base.html:13` and `:43-45`

- [ ] **Step 1: Add the overridable `data-state` to `<body>`**

Replace `<body>` (line 13) with:

```html
<body data-state="{% block data_state %}idle{% endblock %}">
```

- [ ] **Step 2: Add the `extra_js` block after the vendored scripts**

After the `app.js` script tag (line 45), before `</body>`, add:

```html
  {% block extra_js %}{% endblock %}
```

- [ ] **Step 3: Verify the suite still renders**

Run: `venv/bin/pytest test/test_dashboard_render.py test/test_routes_ui.py -q`
Expected: PASS (no behavior change; default `data-state="idle"`).

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/templates/base.html
git commit -m "feat: add body data-state and extra_js block to base template"
```

---

### Task 2: `ui.py` + `dashboard.html` -- initial state + extra_js wiring

**Files:**

- Modify: `blackvuesync/server/routes/ui.py:31-66`
- Modify: `blackvuesync/server/templates/dashboard.html:1-9`
- Test: `test/test_dashboard_sse_handoff.py` (new)

- [ ] **Step 1: Write the failing structure test**

Create `test/test_dashboard_sse_handoff.py`:

```python
"""structure tests for the phase 2c interactive dashboard (server-rendered)."""

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
def logged_in(settings_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    app, store = _make_app(settings_path, destination)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, app, store


class TestInitialState:
    def test_idle_data_state_when_no_sync(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'data-state="idle"' in resp.data

    def test_running_data_state_when_sync_active(self, logged_in: Any) -> None:
        client, app, _store = logged_in
        app.progress_publisher.begin_job(3)  # marks state running
        resp = client.get("/")
        assert b'data-state="running"' in resp.data

    def test_dashboard_js_loaded(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        resp = client.get("/")
        assert b"js/dashboard.js" in resp.data
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestInitialState -q`
Expected: FAIL (`data-state="running"` not emitted; `dashboard.js` not loaded yet).

- [ ] **Step 3: Pass initial state + paused from the route**

In `ui.py` `dashboard()`, after `schedule = current.schedule` add:

```python
    snap = publisher.snapshot()
    sync_state = "running" if snap.state == "running" else "idle"
```

(`publisher` is already defined at line ~34.) Then add to the final `render_template("dashboard.html", ...)` kwargs:

```python
        sync_state=sync_state,
        paused=schedule.paused,
```

- [ ] **Step 4: Set the data_state block + load dashboard.js in `dashboard.html`**

After the `{% block footer_version %}` line, add:

```html
{% block data_state %}{{ sync_state }}{% endblock %}

{% block extra_js %}
  <script src="{{ url_for('static', filename='js/dashboard.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestInitialState -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/dashboard.html test/test_dashboard_sse_handoff.py
git commit -m "feat: render initial dashboard data-state and load dashboard.js"
```

---

### Task 3: `dashboard.html` -- wire the three controls + active hero

**Files:**

- Modify: `blackvuesync/server/templates/dashboard.html:11-42`
- Test: `test/test_dashboard_sse_handoff.py`

- [ ] **Step 1: Write the failing structure tests**

Append to `test/test_dashboard_sse_handoff.py`:

```python
class TestControls:
    def test_sync_now_and_stop_buttons_present(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        body = client.get("/").data
        assert b'data-action="sync-now"' in body
        assert b'data-action="stop"' in body

    def test_no_disabled_placeholder_controls(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        body = client.get("/").data
        assert b"Live controls arrive in the next update" not in body

    def test_pause_button_reflects_not_paused(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        body = client.get("/").data.decode()
        assert "Pause schedule" in body  # not paused -> offers Pause

    def test_pause_button_reflects_paused(self, logged_in: Any) -> None:
        client, _app, store = logged_in
        store.update(
            lambda s: dataclasses.replace(
                s, schedule=dataclasses.replace(s.schedule, paused=True)
            )
        )
        body = client.get("/").data.decode()
        assert "Resume schedule" in body  # paused -> offers Resume

    def test_active_hero_region_present(self, logged_in: Any) -> None:
        client, _app, _store = logged_in
        body = client.get("/").data
        assert b'class="active-only' in body
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestControls -q`
Expected: FAIL.

- [ ] **Step 3: Rewrite the dashboard content block**

Replace the `{% block content %}` body (lines 11-42) with:

```html
{% block content %}
<div class="dashboard" x-data="dashboardSync">
  <aside class="dashboard-sidebar">
    <div class="sidebar-section sidebar-identity">
      <span class="sidebar-label">Signed in</span>
      <span class="identity-name">{{ g.current_user }}</span>
      <span class="identity-mode">{{ auth_mode }} mode</span>
    </div>
    <div class="sidebar-section">
      <span class="sidebar-label">Quick actions</span>
      <div class="sidebar-actions">
        <button type="button" class="button button-primary button-full"
                data-action="sync-now" @click="syncNow()"
                :disabled="progress.state === 'running'">Sync now</button>
        <button type="button" class="button button-secondary button-full active-only"
                data-action="stop" @click="confirmStop()">Stop sync</button>
        <button type="button" class="button button-secondary button-full"
                data-action="pause"
                @click="togglePause({{ paused | tojson }})">
          {{ "Resume schedule" if paused else "Pause schedule" }}
        </button>
      </div>
    </div>
  </aside>

  <div class="dashboard-grid">
    {# active-mode hero: alpine-bound, reuses the sync-status-card look. visible
       only when body[data-state="running"|"complete"] via css. #}
    <div class="card sync-status-card active-only" id="active-hero">
      <div class="card-header">
        <h3 class="card-title">Sync in progress</h3>
        <span class="badge" :class="'badge-' + progress.state" x-text="progress.state"></span>
      </div>
      <div class="card-body">
        <div class="progress-bar-container">
          <div class="progress-bar" :style="'width: ' + (progress.percent || 0) + '%'"></div>
        </div>
        <p class="sync-files">
          <span x-text="progress.files_completed"></span> /
          <span x-text="progress.files_total"></span> files
          <span x-show="progress.files_failed > 0">
            (<span x-text="progress.files_failed"></span> failed)</span>
        </p>
        <p class="sync-current-file" x-show="progress.current_file"
           x-text="progress.current_file ? progress.current_file.filename : ''"></p>
      </div>
    </div>

    {# idle cards: server-rendered populated, then self-poll. hidden in active
       mode via body[data-state] css. #}
    <div class="idle-only" style="display: contents">
      {{ last_run_html | safe }}
      {{ next_scheduled_html | safe }}
      {{ storage_html | safe }}
      {{ recent_activity_html | safe }}
      {% include "_partials/dashcam_card.html" %}
      {% include "_partials/dashcam_info_card.html" %}
    </div>
  </div>

  {% include "_partials/stop_confirm_modal.html" %}
</div>
{% endblock %}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestControls -q`
Expected: PASS. (The modal include will fail until Task 4 -- if so, do Task 4 first then re-run; the tasks are ordered so Task 4's file exists. To keep this task green standalone, create an empty `stop_confirm_modal.html` placeholder now and fill it in Task 4. Prefer doing Task 4 before re-running.)

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/templates/dashboard.html test/test_dashboard_sse_handoff.py
git commit -m "feat: wire dashboard controls and active-mode hero"
```

---

### Task 4: Stop confirmation modal partial

**Files:**

- Create: `blackvuesync/server/templates/_partials/stop_confirm_modal.html`
- Test: `test/test_dashboard_sse_handoff.py`

- [ ] **Step 1: Write the failing structure test**

Append to `test/test_dashboard_sse_handoff.py`:

```python
class TestStopModal:
    def test_modal_markup_present(self, logged_in: Any) -> None:
        body = logged_in[0].get("/").data
        assert b'data-modal="stop-confirm"' in body
        assert b"Stop the running sync?" in body

    def test_modal_confirm_calls_dostop(self, logged_in: Any) -> None:
        body = logged_in[0].get("/").data
        assert b'@click="doStop()"' in body
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestStopModal -q`
Expected: FAIL.

- [ ] **Step 3: Create the modal partial**

```html
<div class="modal-backdrop" data-modal="stop-confirm"
     x-show="stopModalOpen" x-cloak
     @click.self="cancelStop()" @keydown.escape.window="cancelStop()">
  <div class="modal" role="dialog" aria-modal="true"
       aria-labelledby="stop-modal-title">
    <h3 class="modal-title" id="stop-modal-title">Stop the running sync?</h3>
    <p class="modal-body">The current file resumes on the next run.</p>
    <div class="modal-actions">
      <button type="button" class="button button-secondary"
              @click="cancelStop()" x-ref="stopCancel">Cancel</button>
      <button type="button" class="button button-primary"
              @click="doStop()">Stop</button>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py::TestStopModal -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/templates/_partials/stop_confirm_modal.html test/test_dashboard_sse_handoff.py
git commit -m "feat: add stop-sync confirmation modal partial"
```

---

### Task 5: `dashboard.css` -- active-mode show/hide, hero, modal

**Files:**

- Modify: `blackvuesync/server/static/css/dashboard.css` (append)

- [ ] **Step 1: Append the mode and modal styles**

Append to `dashboard.css`:

```css
/* ---------------------------------------------------------------------------
   active-mode switching: css is the only thing that shows/hides; dashboard.js
   only mutates body[data-state]. default (idle): hide active-only, show idle.
   --------------------------------------------------------------------------- */

[x-cloak] {
  display: none !important;
}

.active-only {
  display: none;
}

body[data-state="running"] .active-only,
body[data-state="complete"] .active-only {
  display: block;
}

body[data-state="running"] .idle-only,
body[data-state="complete"] .idle-only {
  display: none;
}

/* ---------------------------------------------------------------------------
   active-mode hero
   --------------------------------------------------------------------------- */

.sync-status-card .card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-3);
}

.sync-status-card .card-title {
  font-size: var(--text-subheadline);
  font-weight: 600;
  color: var(--color-label);
}

.progress-bar-container {
  width: 100%;
  height: 8px;
  background-color: var(--color-fill);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-bottom: var(--space-3);
}

.progress-bar {
  height: 100%;
  background-color: var(--color-accent);
  transition: width 0.3s ease;
}

.sync-files,
.sync-current-file {
  font-size: var(--text-footnote);
  color: var(--color-label-secondary);
}

/* ---------------------------------------------------------------------------
   stop confirmation modal
   --------------------------------------------------------------------------- */

.modal-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: var(--space-4);
}

.modal {
  background-color: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-prominent);
  padding: var(--space-6);
  max-width: 400px;
  width: 100%;
}

.modal-title {
  font-size: var(--text-subheadline);
  font-weight: 700;
  margin-bottom: var(--space-2);
}

.modal-body {
  font-size: var(--text-footnote);
  color: var(--color-label-secondary);
  margin-bottom: var(--space-4);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
```

- [ ] **Step 2: Verify CSS is well-formed (no syntax errors) and the suite is green**

Run: `venv/bin/pytest test/test_dashboard_sse_handoff.py -q`
Expected: PASS (CSS is not unit-tested; behavior is verified by the Playwright smoke in Task 7).

- [ ] **Step 3: Commit**

```bash
git add blackvuesync/server/static/css/dashboard.css
git commit -m "style: active-mode show/hide, hero, and stop modal"
```

---

### Task 6: `dashboard.js` -- Alpine SSE/controls component

**Files:**

- Create: `blackvuesync/server/static/js/dashboard.js`

- [ ] **Step 1: Create the component**

```javascript
// dashboard.js: Alpine.js component for phase 2c active mode. owns the SSE
// connection, the sidebar controls, and the body[data-state] machine. all
// visibility is CSS-driven off data-state; this only mutates the attribute and
// feeds the hero its reactive progress snapshot.

const SSE_BACKOFF_START_MS = 2000;
const SSE_BACKOFF_MAX_MS = 30000;
const COMPLETE_LINGER_MS = 10000; // matches publisher POST_COMPLETE_RETENTION

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.content : "";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("dashboardSync", () => ({
    progress: {
      state: "idle",
      percent: 0,
      files_completed: 0,
      files_total: 0,
      files_failed: 0,
      current_file: null,
    },
    stopModalOpen: false,
    _source: null,
    _backoffMs: SSE_BACKOFF_START_MS,
    _reconnectTimer: null,
    _lastMonotonic: -1,

    init() {
      this.progress.state = document.body.dataset.state || "idle";
      if (this.progress.state === "running") {
        this.openStream();
      }
    },

    // single writer of body[data-state]; css does the rest
    setState(state) {
      document.body.dataset.state =
        state === "running"
          ? "running"
          : state === "complete" || state === "failed"
            ? "complete"
            : "idle";
    },

    async syncNow() {
      const resp = await this.post("/api/sync/now");
      if (resp && (resp.status === 202 || resp.status === 409)) {
        this.setState("running");
        this.openStream();
      }
    },

    confirmStop() {
      this.stopModalOpen = true;
    },
    cancelStop() {
      this.stopModalOpen = false;
    },
    async doStop() {
      this.stopModalOpen = false;
      await this.post("/api/sync/stop"); // SSE will report the terminal state
    },

    async togglePause(currentlyPaused) {
      const path = currentlyPaused
        ? "/api/schedule/resume"
        : "/api/schedule/pause";
      const resp = await this.post(path);
      if (resp && resp.ok) {
        window.location.reload(); // reflect the new Pause/Resume label
      }
    },

    openStream() {
      if (this._source) return;
      const es = new EventSource("/api/sync/progress/stream");
      this._source = es;
      es.addEventListener("progress", (ev) => {
        this._backoffMs = SSE_BACKOFF_START_MS; // healthy frame resets backoff
        let snap;
        try {
          snap = JSON.parse(ev.data);
        } catch (err) {
          return;
        }
        if (snap.last_event_monotonic <= this._lastMonotonic) return; // stale
        this._lastMonotonic = snap.last_event_monotonic;
        this.progress = snap;
        this.setState(snap.state);
        if (snap.state === "complete" || snap.state === "failed") {
          this.closeStream();
          window.setTimeout(() => {
            if (!this._source) this.setState("idle");
          }, COMPLETE_LINGER_MS);
        }
      });
      es.onerror = () => {
        this.closeStream();
        this._reconnectTimer = window.setTimeout(() => {
          if (document.body.dataset.state === "running") this.openStream();
        }, this._backoffMs);
        this._backoffMs = Math.min(this._backoffMs * 2, SSE_BACKOFF_MAX_MS);
      };
    },

    closeStream() {
      if (this._source) {
        this._source.close();
        this._source = null;
      }
      if (this._reconnectTimer) {
        window.clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
    },

    async post(path) {
      try {
        return await fetch(path, {
          method: "POST",
          headers: { "X-CSRFToken": csrfToken() },
        });
      } catch (err) {
        return null;
      }
    },
  }));
});

// unified 302 -> /login for htmx-driven idle polls: flask login_required
// redirects to /login; htmx would otherwise swap the login page into a card.
document.body.addEventListener("htmx:beforeSwap", (event) => {
  const xhr = event.detail.xhr;
  if (xhr && xhr.responseURL && xhr.responseURL.indexOf("/login") !== -1) {
    event.detail.shouldSwap = false;
    window.location =
      "/login?next=" + encodeURIComponent(window.location.pathname);
  }
});
```

- [ ] **Step 2: Lint/format check (no test -- JS is browser-only here)**

Run: `git diff --stat` and visually confirm the file. (Behavior is verified by the Playwright smoke in Task 7.)

- [ ] **Step 3: Commit**

```bash
git add blackvuesync/server/static/js/dashboard.js
git commit -m "feat: dashboard.js alpine component for active-mode SSE and controls"
```

---

### Task 7: Playwright browser smoke test

**Files:**

- Create: `test/e2e/conftest.py`, `test/e2e/test_dashboard_active_mode.py`
- Modify: `pyproject.toml` (add `pytest-playwright` to the dev/test extra)

- [ ] **Step 1: Add the test dependency**

In `pyproject.toml`, add `"pytest-playwright"` to the `[project.optional-dependencies]` `dev` list (alongside `pytest`). Then install the browser:

Run: `venv/bin/pip install -e ".[dev]" && venv/bin/playwright install chromium`
Expected: chromium installed.

- [ ] **Step 2: Create the live-server fixture**

`test/e2e/conftest.py`:

```python
"""live-server fixture for browser e2e: runs the flask app in a daemon thread."""

from __future__ import annotations

import dataclasses
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


class _LiveServer:
    def __init__(self, app: Any, host: str, port: int) -> None:
        self.app = app
        self.url = f"http://{host}:{port}"
        self._srv = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._srv.shutdown()


@pytest.fixture()
def live_server(tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth, username="admin", password_hash=hash_password("pw-1234-test")
            ),
            system=dataclasses.replace(s.system, destination=str(destination)),
        )
    )
    app = create_app(store, testing=False)
    server = _LiveServer(app, "127.0.0.1", 0)
    # make_server with port 0 picks a free port; read it back
    server.url = f"http://127.0.0.1:{server._srv.server_port}"
    server.start()
    yield server
    server.stop()
```

- [ ] **Step 3: Write the smoke test**

`test/e2e/test_dashboard_active_mode.py`:

```python
"""playwright smoke: the idle -> running -> complete handoff and the stop modal."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')
    expect(page.locator("body")).to_have_attribute("data-state", "idle")


def test_sync_handoff_idle_running_complete(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    pub = live_server.app.progress_publisher

    # simulate a sync starting from the server side
    job = pub.begin_job(2)
    pub.start_file("20230101_120000_NF.mp4", "mp4", 1000)
    pub.update_bytes(500, 1000)
    expect(page.locator("body")).to_have_attribute("data-state", "running", timeout=8000)
    expect(page.locator("#active-hero")).to_be_visible()

    # finish the job -> hero shows complete, then reverts to idle after linger
    pub.finish_file(success=True)
    pub.end_job(success=True)
    expect(page.locator("body")).to_have_attribute("data-state", "complete", timeout=8000)


def test_stop_modal_confirm_posts_stop(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    pub = live_server.app.progress_publisher
    pub.begin_job(1)
    expect(page.locator("body")).to_have_attribute("data-state", "running", timeout=8000)

    page.click('[data-action="stop"]')
    expect(page.locator('[data-modal="stop-confirm"]')).to_be_visible()

    with page.expect_response("**/api/sync/stop") as resp_info:
        page.click('[data-modal="stop-confirm"] .button-primary')
    assert resp_info.value.status in (202, 404)
```

- [ ] **Step 4: Run the smoke test**

Run: `venv/bin/pytest test/e2e/ -q`
Expected: PASS (2 tests). If the browser is flaky/unavailable, the structure tests (Task 2-4) still gate; mark e2e to skip when `playwright` import fails (add a `pytest.importorskip("playwright")` at the top of the test module).

- [ ] **Step 5: Wire e2e into CI (its own job)**

Add a CI step (in `.github/workflows/ci.yml`, `unit-tests` job or a new `e2e` job): install `.[dev]`, run `playwright install --with-deps chromium`, then `pytest test/e2e/`. Keep it non-blocking-optional only if the team agrees; default to gating. (Read `.github/workflows/ci.yml` first and mirror the existing job structure.)

- [ ] **Step 6: Commit**

```bash
git add test/e2e/conftest.py test/e2e/test_dashboard_active_mode.py pyproject.toml .github/workflows/ci.yml
git commit -m "test: playwright smoke for dashboard active-mode handoff"
```

---

### Task 8: Housekeeping -- version, mypy overrides, docs

**Files:**

- Modify: `pyproject.toml`, `docs/api.md`

- [ ] **Step 1:** Bump `version` `2.4.0a3` → `2.4.0b0` (first beta -- dashboard feature-complete) in `pyproject.toml`.
- [ ] **Step 2:** Add `test_dashboard_sse_handoff` to the mypy per-module override list (match the existing pattern). The `test/e2e/` modules import `playwright`; add `test.e2e.*` (or the bare module names) to the mypy overrides as well so missing browser stubs don't fail mypy.
- [ ] **Step 3:** Add a short note to `docs/api.md` (dashboard/sync section): the dashboard UI now drives Sync-now, Stop (modal-confirmed), and Pause/Resume against the existing `/api/sync/*` and `/api/schedule/*` endpoints; no new endpoints were added in 2C.
- [ ] **Step 4: Full verification**

Run: `venv/bin/pytest test/ -q && venv/bin/mypy blackvuesync && venv/bin/pylint blackvuesync`
Expected: green (the `test/e2e/` browser tests run only when chromium is present; otherwise importorskip).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/api.md
git commit -m "chore: bump version, mypy overrides, document 2c dashboard controls"
```

---

## Final verification (before PR)

- [ ] `venv/bin/pytest test/ -q` -- structure tests green; full unit suite green.
- [ ] `venv/bin/pytest test/e2e/ -q` -- Playwright smoke green (idle→running→complete; Stop modal).
- [ ] `behave` -- existing integration scenarios unaffected.
- [ ] `pre-commit run --files <changed files>` -- all hooks pass.
- [ ] Manual: `venv/bin/python -m blackvuesync serve`, log in, click Sync now → hero appears and updates live; Stop → modal → confirm; Pause → label flips to Resume; reload survives.
- [ ] Push branch, open PR; all five required checks green.
- [ ] After CI: query `sonarcloud.io/api/issues/search?...&pullRequest=<N>&resolved=false` -- confirm **0 findings** (do not trust the gate alone).
- [ ] Squash- or rebase-merge (linear history).

## Self-review

- **Spec coverage:** state machine (Tasks 1-2, 5) · dashboard.js SSE+backoff+302 (Task 6) · controls incl. modal-confirmed Stop (Tasks 3-4) · card-fetch errors not logged (no code added -- confirmed by omission; the existing cards already render unreachable without logging) · JS-disabled degradation (idle cards server-rendered in Task 3; active-only hidden by default CSS in Task 5) · edge cases: SSE break backoff + monotonic stale-drop + 302 (Task 6), Stop hidden when not running (`.active-only` CSS, Task 5), pause idempotent (server-side, existing) · testing: structure (Tasks 2-4) + Playwright (Task 7).
- **Placeholders:** none -- all new files have complete code; modifications cite exact files/lines.
- **Type/name consistency:** `data-action` values (`sync-now`/`stop`/`pause`), `data-modal="stop-confirm"`, Alpine methods (`syncNow`/`confirmStop`/`cancelStop`/`doStop`/`togglePause`), `progress` keys (match `_snap_to_dict`), and `body[data-state]` values (`idle`/`running`/`complete`) are consistent across `dashboard.html`, `dashboard.js`, `dashboard.css`, the modal, and the tests.
- **Scope:** frontend only + `ui.py` context; no backend endpoints; `paused` setting already exists.
- **Note for executor:** Task 3's modal include means Task 4 must land before Task 3's structure tests pass cleanly -- execute Task 4 immediately after Task 3's template edit (or create the modal file first). Flagged in Task 3 Step 4.
