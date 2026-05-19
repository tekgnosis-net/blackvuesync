# Sub-Project #2 Phase 2A: Dashboard backend

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 MD060 -->

**Date:** 2026-05-19
**Spec:** [`2026-05-19-sub-project-2-dashboard-design.md`](./2026-05-19-sub-project-2-dashboard-design.md) (Sections 2, 3 -- backend components and data flow)
**Phase:** 2A of 2A/2B/2C (sub-project #2)

**Goal:** Deliver every backend surface the Dashboard UI will consume -- 6 new endpoints, 4 HTMX fragments, the sync.py cooperative stop flag, the scheduler pause hook, and the `settings.schedule.paused` field -- with full test coverage. No visible UI change ships in this PR; the placeholder dashboard route stays.

**Architecture:** Six new endpoints split across four route modules (`api_health.py`, `api_recordings.py`, `api_schedule.py`, `hx_dashboard.py`) plus an extension to `api_sync.py` for Stop. The data computations factor into private helpers (`_compute_storage`, `_compute_dashcam`, `_compute_recent`) so the HTMX fragments call the same Python functions instead of HTTP-to-self. `sync.py` gains a module-level `threading.Event` checked between chunks in `download_with_resume`; `scheduler.py` checks `settings.schedule.paused` in `_scheduled_run`. All new files follow Phase F's idiom (`@login_required`, CSRF on mutations, `_MIME_JSON` constant).

**Tech Stack:** Python 3.9+, Flask 3.1, Flask-WTF 1.2, frozen dataclasses, pytest 8.4, `threading.Event` for cooperative cancellation, `os.statvfs` + `urllib.request` for health probes, Jinja2 partials.

---

## Out of scope (this phase)

- **Frontend** -- no `dashboard.html` template, no CSS, no JS. The existing placeholder at `templates/_placeholders/dashboard.html` stays untouched in 2A. Phase 2B replaces it.
- **Active mode + Stop button UI** -- Phase 2C wires the sidebar to `POST /api/sync/stop`.
- **Behave scenarios for the dashboard** -- no UI means no end-to-end UI scenarios. The 6 BDD scenarios in the spec all land in 2B/2C.
- **Carry-forwards from foundation** -- `sync.py` cognitive-complexity decomposition (S3776) and multi-stage Dockerfile remain deferred.

## Implementer guidelines (karpathy discipline)

1. **Think before coding.** State assumptions explicitly. If a step is ambiguous, stop and report DONE_WITH_CONCERNS rather than picking silently.
2. **Simplicity first.** No features beyond what the plan asks for. The dashboard *UI* is Phase 2B's job -- do not start writing it in 2A even if it feels related.
3. **Surgical changes.** Touch only the files this plan lists. Do not refactor adjacent code in `sync.py` (the S3776 carry-forwards stay).
4. **Goal-driven execution.** Each task has a verification step. Run it before committing.

Process hygiene:

- Never use `git add -A` or `git add .`. Stage files by name.
- Never use `--no-verify`. Pre-commit hooks must pass.
- Never amend an existing commit after pre-commit auto-fixes. Create a NEW commit.
- Comments are lowercase, third-person, non-obvious. Entity names keep their casing.
- Commit-message titles ≤ 72 chars (gitlint).
- Use `venv/bin/pytest`, `venv/bin/python`, `venv/bin/black` -- the system pytest does not have the package installed.

---

## File Structure

### Files to create

- `blackvuesync/server/routes/api_health.py` -- storage + dashcam health probes (6 functions: 2 routes + 2 compute helpers + 2 small predicates)
- `blackvuesync/server/routes/api_recordings.py` -- recent recordings listing (2 functions: 1 route + 1 compute helper)
- `blackvuesync/server/routes/api_schedule.py` -- pause / resume (3 functions: 2 routes + 1 toggle helper)
- `blackvuesync/server/routes/hx_dashboard.py` -- 4 HTMX fragments (4 routes, each ~10 LoC)
- `blackvuesync/server/templates/_partials/storage_card.html`
- `blackvuesync/server/templates/_partials/dashcam_card.html`
- `blackvuesync/server/templates/_partials/next_scheduled_card.html`
- `blackvuesync/server/templates/_partials/recent_activity_card.html`
- `test/test_routes_api_health.py`
- `test/test_routes_api_recordings.py`
- `test/test_routes_api_schedule.py`
- `test/test_routes_hx_dashboard.py`
- `test/test_sync_stop_flag.py`
- `test/test_scheduler_pause.py`

### Files to modify

- `pyproject.toml` -- version bump `2.3.0` → `2.4.0a0`; add 6 new test-module mypy overrides
- `blackvuesync/settings.py` -- `ScheduleSettings.paused: bool = False` field
- `blackvuesync/sync.py` -- module-level `_stop_event`, `request_stop()`, `clear_stop()`, `is_stop_requested()` helpers; chunk-loop check in `download_with_resume`
- `blackvuesync/server/scheduler.py` -- `_scheduled_run` skips when `settings.schedule.paused`
- `blackvuesync/server/sync_runner.py` -- `trigger_sync` calls `clear_stop()` before spawning the daemon thread
- `blackvuesync/server/routes/api_sync.py` -- add `POST /api/sync/stop`
- `blackvuesync/server/__init__.py` -- register 4 new blueprints
- `test/test_routes_api_sync.py` -- add Stop endpoint tests (extends existing `TestTriggerNow` class with a new `TestStopSync`)
- `docs/api.md` -- document 6 new endpoints + 4 HTMX fragments

### Files explicitly NOT to modify

- `blackvuesync/metrics.py`
- `blackvuesync/server/auth.py`
- `blackvuesync/server/progress.py`
- `blackvuesync/server/routes/_helpers.py`
- `blackvuesync/server/routes/api_settings.py`, `api_auth.py`, `auth.py`, `health.py`, `ui.py`, `hx_sync.py`
- `blackvuesync/server/templates/dashboard.html` (does not exist yet; Phase 2B creates it)
- `blackvuesync/server/templates/_placeholders/dashboard.html` (Phase 2B retires it)
- Any other test file
- Dockerfile, entrypoint.sh, docker-compose.yml, run.sh
- `blackvuesync/sync.py`'s `download_file`, `download_recording` cognitive-complexity (S3776 carry-forward)

---

## Task 1: Bump version and add test-module mypy overrides

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Change version**

In `pyproject.toml`, change:

```toml
version = "2.3.0"
```

to:

```toml
version = "2.4.0a0"
```

The bump from `2.3.0` (Web Foundation complete) to `2.4.0a0` (Dashboard sub-project alpha) follows the foundation's `2.3.0a0` → `2.3.0` pattern.

- [ ] **Step 2: Add new test modules to the mypy override list**

In `pyproject.toml`, find the existing override block that contains `"test_auth"`, `"test_routes_auth"`, etc., and extend its `module = [...]` list with the 6 new test modules. The final list should include:

```toml
[[tool.mypy.overrides]]
module = [
    "test_auth",
    "test_routes_auth",
    "test_routes_health",
    "test_routes_ui",
    "test_security_headers",
    "test_sync_callback",
    "test_sync_runner",
    "test_routes_api_sync",
    "test_routes_hx_sync",
    "test_routes_api_settings",
    "test_routes_api_auth",
    "test_main_serve_logging",
    "test_routes_api_health",
    "test_routes_api_recordings",
    "test_routes_api_schedule",
    "test_routes_hx_dashboard",
    "test_sync_stop_flag",
    "test_scheduler_pause",
]
disallow_untyped_decorators = false
```

(Keep all existing entries; only add the 6 new ones.)

- [ ] **Step 3: Verify install still works**

Run: `pip install -e ".[dev]"`
Expected: clean install, no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "Phase 2A: bump to 2.4.0a0; add new test-module mypy overrides"
```

---

## Task 2: Add `schedule.paused` settings field

**Files:**

- Modify: `blackvuesync/settings.py`

- [ ] **Step 1: Write a failing test (extend existing test_settings.py? no -- create test_schedule_paused.py)**

Actually, the existing `test/test_settings.py` is the canonical home for settings round-trip tests. Read the file first to see its fixture conventions. Then **append** to it (do not modify existing tests, only add new ones):

Append at the end of `test/test_settings.py`:

```python
class TestSchedulePaused:
    """tests for the schedule.paused field added in sub-project #2."""

    def test_paused_defaults_to_false(self, tmp_path: Path) -> None:
        """new ScheduleSettings has paused=False by default."""
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store = SettingsStore(tmp_path / "settings.json")
        assert store.get().schedule.paused is False

    def test_paused_persists_round_trip(self, tmp_path: Path) -> None:
        """setting paused=True persists to disk and survives a reload."""
        path = tmp_path / "settings.json"
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store = SettingsStore(path)
            store.update(
                lambda s: dataclasses.replace(
                    s, schedule=dataclasses.replace(s.schedule, paused=True)
                )
            )
        # reload from disk
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store2 = SettingsStore(path)
        assert store2.get().schedule.paused is True

    def test_paused_validate_accepts_any_bool(self) -> None:
        """validate() returns no errors for either paused value."""
        from blackvuesync.settings import ScheduleSettings

        assert ScheduleSettings(paused=False).validate() == []
        assert ScheduleSettings(paused=True).validate() == []
```

Imports needed at the top of the existing `test_settings.py` (add only if missing):

```python
import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

from blackvuesync.settings import SettingsStore
```

- [ ] **Step 2: Run the failing test**

Run: `venv/bin/pytest test/test_settings.py::TestSchedulePaused -v`
Expected: FAIL -- `paused` attribute does not exist on `ScheduleSettings`.

- [ ] **Step 3: Add the field**

In `blackvuesync/settings.py`, find `class ScheduleSettings`:

```python
@dataclass(frozen=True)
class ScheduleSettings:
    """sync schedule settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    cron_expression: str = "*/15 * * * *"
    timezone: str = "UTC"

    def validate(self) -> list[str]:
        ...
```

Add the `paused` field after `timezone`:

```python
@dataclass(frozen=True)
class ScheduleSettings:
    """sync schedule settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    cron_expression: str = "*/15 * * * *"
    timezone: str = "UTC"
    paused: bool = False

    def validate(self) -> list[str]:
        ...
```

No validator change needed -- any `bool` is valid.

- [ ] **Step 4: Run the test**

Run: `venv/bin/pytest test/test_settings.py::TestSchedulePaused -v`
Expected: 3 PASS.

- [ ] **Step 5: Run full settings tests to confirm no regression**

Run: `venv/bin/pytest test/test_settings.py -v`
Expected: all tests pass (existing + new 3).

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/settings.py test/test_settings.py
git commit -m "Phase 2A: add schedule.paused settings field"
```

---

## Task 3: Wire scheduler to honor `schedule.paused`

**Files:**

- Modify: `blackvuesync/server/scheduler.py`
- Create: `test/test_scheduler_pause.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_scheduler_pause.py`:

```python
"""tests that _scheduled_run honors the schedule.paused flag."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


def _make_store(settings_path: Path) -> SettingsStore:
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


class TestScheduledRunPause:
    """tests for _scheduled_run skipping when settings.schedule.paused is True."""

    def test_skips_when_paused(self, settings_path: Path) -> None:
        """when paused=True, _scheduled_run logs and does not call trigger_sync."""
        from blackvuesync.server.scheduler import _scheduled_run

        store = _make_store(settings_path)
        store.update(
            lambda s: dataclasses.replace(
                s, schedule=dataclasses.replace(s.schedule, paused=True)
            )
        )
        publisher = ProgressPublisher()

        with patch(
            "blackvuesync.server.scheduler.trigger_sync"
        ) as mock_trigger:
            _scheduled_run(store, publisher)
            mock_trigger.assert_not_called()

    def test_runs_when_not_paused(self, settings_path: Path) -> None:
        """when paused=False (default), _scheduled_run calls trigger_sync."""
        from blackvuesync.server.scheduler import _scheduled_run

        store = _make_store(settings_path)
        publisher = ProgressPublisher()

        with patch(
            "blackvuesync.server.scheduler.trigger_sync",
            return_value={"status": "started", "job_id": "deadbeef"},
        ) as mock_trigger:
            _scheduled_run(store, publisher)
            mock_trigger.assert_called_once()

    def test_resume_after_pause(self, settings_path: Path) -> None:
        """toggling paused=True then False restores normal scheduling."""
        from blackvuesync.server.scheduler import _scheduled_run

        store = _make_store(settings_path)
        publisher = ProgressPublisher()

        # pause: should skip
        store.update(
            lambda s: dataclasses.replace(
                s, schedule=dataclasses.replace(s.schedule, paused=True)
            )
        )
        with patch("blackvuesync.server.scheduler.trigger_sync") as mock_trigger:
            _scheduled_run(store, publisher)
            assert mock_trigger.call_count == 0

        # resume: should run
        store.update(
            lambda s: dataclasses.replace(
                s, schedule=dataclasses.replace(s.schedule, paused=False)
            )
        )
        with patch(
            "blackvuesync.server.scheduler.trigger_sync",
            return_value={"status": "started", "job_id": "deadbeef"},
        ) as mock_trigger:
            _scheduled_run(store, publisher)
            assert mock_trigger.call_count == 1
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_scheduler_pause.py -v`
Expected: FAIL -- `_scheduled_run` does not check `paused` yet, so `test_skips_when_paused` will call `trigger_sync` and the assert_not_called fails.

- [ ] **Step 3: Add the pause check**

In `blackvuesync/server/scheduler.py`, find `_scheduled_run`:

```python
def _scheduled_run(store: SettingsStore, publisher: ProgressPublisher) -> None:
    """job function: triggers a sync via the shared trigger_sync entrypoint.

    settings are read fresh on each tick so updates to e.g. address or
    timeout apply on the next scheduled run without a restart.
    """
    settings = store.get()
    result = trigger_sync(settings, publisher)
    if result["status"] == "already_running":
        logger.info(
            "scheduled sync skipped: another sync is already running (job_id=%s)",
            result["job_id"],
        )
```

Add the pause check at the top of the body, right after reading settings:

```python
def _scheduled_run(store: SettingsStore, publisher: ProgressPublisher) -> None:
    """job function: triggers a sync via the shared trigger_sync entrypoint.

    settings are read fresh on each tick so updates to e.g. address, timeout,
    or schedule.paused apply on the next scheduled run without a restart.
    """
    settings = store.get()
    if settings.schedule.paused:
        logger.info("scheduled sync skipped: schedule is paused")
        return
    result = trigger_sync(settings, publisher)
    if result["status"] == "already_running":
        logger.info(
            "scheduled sync skipped: another sync is already running (job_id=%s)",
            result["job_id"],
        )
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest test/test_scheduler_pause.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Run the existing scheduler tests to confirm no regression**

Run: `venv/bin/pytest test/test_scheduler.py -v`
Expected: 7 existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/scheduler.py test/test_scheduler_pause.py
git commit -m "Phase 2A: scheduler skips ticks when schedule.paused"
```

---

## Task 4: Add `sync.py` cooperative stop flag

**Files:**

- Modify: `blackvuesync/sync.py`
- Modify: `blackvuesync/server/sync_runner.py`
- Create: `test/test_sync_stop_flag.py`

- [ ] **Step 1: Write the failing test (flag mechanism)**

Create `test/test_sync_stop_flag.py`:

```python
"""tests for the cooperative stop flag in blackvuesync.sync."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.sync_runner import trigger_sync


class TestStopFlag:
    """tests for request_stop / clear_stop / is_stop_requested."""

    def setup_method(self) -> None:
        """ensures each test starts with the flag cleared."""
        from blackvuesync.sync import clear_stop

        clear_stop()

    def test_initial_state_is_not_requested(self) -> None:
        from blackvuesync.sync import is_stop_requested

        assert is_stop_requested() is False

    def test_request_stop_sets_flag(self) -> None:
        from blackvuesync.sync import is_stop_requested, request_stop

        request_stop()
        assert is_stop_requested() is True

    def test_clear_stop_resets_flag(self) -> None:
        from blackvuesync.sync import clear_stop, is_stop_requested, request_stop

        request_stop()
        clear_stop()
        assert is_stop_requested() is False

    def test_request_stop_is_idempotent(self) -> None:
        """calling request_stop twice keeps the flag set."""
        from blackvuesync.sync import is_stop_requested, request_stop

        request_stop()
        request_stop()
        assert is_stop_requested() is True


class TestTriggerSyncClearsStopFlag:
    """tests that trigger_sync clears the stop flag before spawning the thread."""

    def test_trigger_sync_clears_stale_stop_flag(self) -> None:
        """if a previous run left the flag set, trigger_sync clears it before
        the next run so we don't immediately abort."""
        from blackvuesync.sync import is_stop_requested, request_stop

        # simulate a stale stop flag from a previous run
        request_stop()
        assert is_stop_requested() is True

        # trigger_sync should clear the flag before spawning
        publisher = ProgressPublisher()
        settings = MagicMock()
        settings.connection.address = "192.168.1.1"
        settings.connection.timeout_seconds = 5.0
        settings.system.destination = "/tmp/bvs-stop-test"
        settings.system.dry_run = False
        settings.sync.grouping = "none"
        settings.sync.priority = "date"
        settings.sync.include = None
        settings.sync.exclude = None
        settings.retention.max_used_disk_percent = 90

        def _check_then_noop(_s, p, *, job_id):
            """asserts the flag is clear inside the spawned thread."""
            assert is_stop_requested() is False
            p.begin_job(0, job_id=job_id)
            p.end_job(success=True)

        with patch(
            "blackvuesync.server.sync_runner._do_sync", side_effect=_check_then_noop
        ):
            result = trigger_sync(settings, publisher)
            # give the daemon thread a moment to run the assertion
            import time

            time.sleep(0.1)
            assert result["status"] == "started"
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_sync_stop_flag.py -v`
Expected: FAIL -- `request_stop`, `clear_stop`, `is_stop_requested` don't exist yet.

- [ ] **Step 3: Add the stop-flag helpers to `sync.py`**

In `blackvuesync/sync.py`, near the top of the module (after the existing imports and module-level constants, before any function definitions), add:

```python
# cooperative stop flag for the active sync. set by /api/sync/stop,
# checked by the download chunk loop between reads, cleared by trigger_sync
# at the start of each new sync.
_stop_event: threading.Event = threading.Event()


def request_stop() -> None:
    """requests cooperative stop of the active sync; the download loop will
    raise UserWarning("sync stopped by user") on its next chunk-boundary check."""
    _stop_event.set()


def clear_stop() -> None:
    """clears the stop flag; called by trigger_sync before each new sync run."""
    _stop_event.clear()


def is_stop_requested() -> bool:
    """returns True if request_stop has been called and clear_stop has not
    yet reset the flag."""
    return _stop_event.is_set()
```

Find the existing `import threading` line near the top of `sync.py`. If it does not exist (sync.py may not currently import threading), add `import threading` to the imports.

- [ ] **Step 4: Wire `clear_stop` into `trigger_sync`**

In `blackvuesync/server/sync_runner.py`, find `trigger_sync`:

```python
def trigger_sync(
    settings: Any,
    publisher: ProgressPublisher,
) -> dict[str, str]:
    ...
    if not _sync_lock.acquire(blocking=False):
        current_snap = publisher.snapshot()
        return {"status": "already_running", "job_id": current_snap.job_id}

    job_id = uuid.uuid4().hex
    ...
```

Add a `clear_stop()` call right after acquiring the lock and before the job_id assignment:

```python
def trigger_sync(
    settings: Any,
    publisher: ProgressPublisher,
) -> dict[str, str]:
    ...
    if not _sync_lock.acquire(blocking=False):
        current_snap = publisher.snapshot()
        return {"status": "already_running", "job_id": current_snap.job_id}

    # clears any leftover stop flag from a previous run; the next request to
    # /api/sync/stop sets it again on demand.
    # pylint: disable=import-outside-toplevel
    from blackvuesync.sync import clear_stop

    clear_stop()
    # pylint: enable=import-outside-toplevel

    job_id = uuid.uuid4().hex
    ...
```

- [ ] **Step 5: Run the failing tests again**

Run: `venv/bin/pytest test/test_sync_stop_flag.py -v`
Expected: all 5 PASS.

- [ ] **Step 6: Wire the chunk-loop check into `download_with_resume`**

Find `download_with_resume` in `blackvuesync/sync.py`. It contains a loop that reads chunks from the HTTP response and writes them to the temp file. The exact shape is:

```python
def download_with_resume(...):
    ...
    while True:
        chunk = response.read(chunk_size)
        if not chunk:
            break
        # write chunk to temp file
        ...
```

(Read the actual function first -- the variable names may differ; preserve them.)

Add a stop check **immediately after** reading each chunk and **before** writing it:

```python
while True:
    chunk = response.read(chunk_size)
    if not chunk:
        break
    if is_stop_requested():
        raise UserWarning("sync stopped by user")
    # existing write logic continues unchanged
    ...
```

(Replace `chunk_size` and `response` with the actual names used in the function.)

The `UserWarning` is caught by the existing exception classifier (`classify_run_failure`) and routes through the normal `failed` exit path -- no special-casing needed.

- [ ] **Step 7: Run all sync tests to confirm no regression**

Run: `venv/bin/pytest test/test_sync_stop_flag.py test/blackvuesync_test.py test/test_sync_callback.py test/test_sync_runner.py -v`
Expected: all existing tests pass + 5 new stop-flag tests pass.

- [ ] **Step 8: Commit**

```bash
git add blackvuesync/sync.py blackvuesync/server/sync_runner.py test/test_sync_stop_flag.py
git commit -m "Phase 2A: cooperative stop flag in sync.py + trigger_sync wiring"
```

---

## Task 5: Add `POST /api/sync/stop` endpoint

**Files:**

- Modify: `blackvuesync/server/routes/api_sync.py`
- Modify: `test/test_routes_api_sync.py`

- [ ] **Step 1: Append failing tests to `test_routes_api_sync.py`**

At the end of `test/test_routes_api_sync.py`, after the existing `TestProgressStream` class, append:

```python
class TestStopSync:
    """tests for POST /api/sync/stop."""

    def test_returns_202_when_sync_is_running(self, logged_in_client: Any) -> None:
        """when state is running, stop sets the flag and returns 202."""
        client, pub = logged_in_client

        # put the publisher into a running state
        pub.begin_job(5)

        from blackvuesync.sync import is_stop_requested, clear_stop

        clear_stop()
        resp = client.post("/api/sync/stop")
        assert resp.status_code == 202
        body = json.loads(resp.data)
        assert body["stopping"] is True
        assert body["job_id"] == pub.snapshot().job_id
        assert is_stop_requested() is True

        # clean up
        clear_stop()
        pub.end_job(success=False)

    def test_returns_404_when_no_sync_is_running(
        self, logged_in_client: Any
    ) -> None:
        """when state is idle, stop returns 404 SYNC_NOT_RUNNING."""
        client, _ = logged_in_client
        resp = client.post("/api/sync/stop")
        assert resp.status_code == 404
        body = json.loads(resp.data)
        assert body["code"] == "SYNC_NOT_RUNNING"

    def test_redirects_to_login_when_not_authenticated(
        self, settings_path: Path
    ) -> None:
        """/api/sync/stop requires authentication."""
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.post("/api/sync/stop")
        assert resp.status_code == 302

    def test_requires_csrf_token(self, settings_path: Path) -> None:
        """POST /api/sync/stop requires a CSRF token when CSRF is enabled."""
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
        app = create_app(store, testing=False)
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["TESTING"] = True
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.post("/api/sync/stop")
        assert resp.status_code == 400
```

(The `dataclasses` and `hash_password` imports already exist at the top of `test_routes_api_sync.py` from Phase D/F -- verify and add if missing.)

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_sync.py::TestStopSync -v`
Expected: FAIL -- the `/api/sync/stop` route does not exist (404 from Flask).

- [ ] **Step 3: Add the Stop endpoint**

In `blackvuesync/server/routes/api_sync.py`, after the existing `last_sync` route function (before the `__all__` declaration), append:

```python
@api_sync_bp.route("/stop", methods=["POST"])
@login_required
def stop_sync() -> Response:
    """requests cooperative stop of the active sync.

    returns 202 + {job_id, stopping: true} if a sync was running;
    404 + {code: 'SYNC_NOT_RUNNING'} if no sync is active. the actual
    stop happens between download chunks; the next snapshot will report
    state='failed' with reason="stopped by user" once the chunk loop
    raises UserWarning.
    """
    # pylint: disable=import-outside-toplevel
    from blackvuesync.sync import request_stop

    # pylint: enable=import-outside-toplevel

    snap = _publisher().snapshot()
    if snap.state != "running":
        body = json.dumps(
            {
                "error": "no sync is running",
                "code": "SYNC_NOT_RUNNING",
                "details": {},
            }
        )
        return Response(body, status=404, mimetype=_MIME_JSON)

    request_stop()
    body = json.dumps({"job_id": snap.job_id, "stopping": True})
    return Response(body, status=202, mimetype=_MIME_JSON)
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_sync.py::TestStopSync -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the full api_sync test file to confirm no regression**

Run: `venv/bin/pytest test/test_routes_api_sync.py -v`
Expected: all existing tests pass + 4 new Stop tests pass.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_sync.py test/test_routes_api_sync.py
git commit -m "Phase 2A: POST /api/sync/stop endpoint"
```

---

## Task 6: Add `/api/schedule/pause` and `/api/schedule/resume`

**Files:**

- Create: `blackvuesync/server/routes/api_schedule.py`
- Modify: `blackvuesync/server/__init__.py`
- Create: `test/test_routes_api_schedule.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_api_schedule.py`:

```python
"""tests for /api/schedule/pause and /api/schedule/resume."""

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


def _make_app(settings_path: Path):  # type: ignore[no-untyped-def]
    store = _make_store(settings_path)
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


class TestPause:
    """tests for POST /api/schedule/pause."""

    def test_pause_sets_paused_true(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        resp = client.post("/api/schedule/pause")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["paused"] is True
        assert store.get().schedule.paused is True

    def test_pause_is_idempotent(self, logged_in_client: Any) -> None:
        """calling pause twice keeps paused=True with no error."""
        client, store = logged_in_client
        client.post("/api/schedule/pause")
        resp = client.post("/api/schedule/pause")
        assert resp.status_code == 200
        assert store.get().schedule.paused is True

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.post("/api/schedule/pause")
        assert resp.status_code == 302


class TestResume:
    """tests for POST /api/schedule/resume."""

    def test_resume_sets_paused_false(self, logged_in_client: Any) -> None:
        client, store = logged_in_client
        # first pause
        client.post("/api/schedule/pause")
        assert store.get().schedule.paused is True
        # then resume
        resp = client.post("/api/schedule/resume")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["paused"] is False
        assert store.get().schedule.paused is False

    def test_resume_is_idempotent(self, logged_in_client: Any) -> None:
        """resuming an already-running schedule returns 200 with no change."""
        client, store = logged_in_client
        resp = client.post("/api/schedule/resume")
        assert resp.status_code == 200
        assert store.get().schedule.paused is False


class TestCsrf:
    """tests that pause and resume require CSRF when enabled."""

    def _csrf_app(self, settings_path: Path):  # type: ignore[no-untyped-def]
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
        app = create_app(store, testing=False)
        app.config["WTF_CSRF_ENABLED"] = True
        app.config["TESTING"] = True
        return app

    def test_pause_without_csrf_returns_400(self, settings_path: Path) -> None:
        app = self._csrf_app(settings_path)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.post("/api/schedule/pause")
        assert resp.status_code == 400

    def test_resume_without_csrf_returns_400(self, settings_path: Path) -> None:
        app = self._csrf_app(settings_path)
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["user"] = "admin"
            resp = client.post("/api/schedule/resume")
        assert resp.status_code == 400
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_schedule.py -v`
Expected: FAIL -- `/api/schedule/pause` does not exist (404).

- [ ] **Step 3: Create `api_schedule.py`**

Create `blackvuesync/server/routes/api_schedule.py`:

```python
"""api schedule routes: pause and resume the scheduler."""

from __future__ import annotations

import dataclasses
import json

from flask import Blueprint, Response, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore

api_schedule_bp = Blueprint("api_schedule_bp", __name__, url_prefix="/api/schedule")

_MIME_JSON = "application/json"


def _set_paused(paused: bool) -> Response:
    """sets schedule.paused to the given value and returns the new state."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    store.update(
        lambda s: dataclasses.replace(
            s, schedule=dataclasses.replace(s.schedule, paused=paused)
        )
    )
    body = json.dumps({"paused": paused})
    return Response(body, status=200, mimetype=_MIME_JSON)


@api_schedule_bp.route("/pause", methods=["POST"])
@login_required
def pause() -> Response:
    """pauses scheduled syncs. manual POST /api/sync/now still works."""
    return _set_paused(True)


@api_schedule_bp.route("/resume", methods=["POST"])
@login_required
def resume() -> Response:
    """resumes scheduled syncs."""
    return _set_paused(False)


__all__ = ["api_schedule_bp"]
```

- [ ] **Step 4: Register the blueprint**

In `blackvuesync/server/__init__.py`, find the deferred-import block and the corresponding `app.register_blueprint(...)` calls. Add the schedule blueprint:

```python
# inside create_app, near the other route imports
from blackvuesync.server.routes.api_schedule import api_schedule_bp
```

And:

```python
# inside create_app, near the other register_blueprint calls
app.register_blueprint(api_schedule_bp)
```

(Place alphabetically with the other `api_*` blueprints to keep the registration list orderly.)

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_schedule.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_schedule.py blackvuesync/server/__init__.py test/test_routes_api_schedule.py
git commit -m "Phase 2A: POST /api/schedule/pause and /api/schedule/resume"
```

---

## Task 7: Add `/api/health/storage`

**Files:**

- Create: `blackvuesync/server/routes/api_health.py`
- Modify: `blackvuesync/server/__init__.py`
- Create: `test/test_routes_api_health.py`

- [ ] **Step 1: Write the failing tests for storage**

Create `test/test_routes_api_health.py`:

```python
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
                s.system, destination=str(destination) if destination else s.system.destination
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

    def test_counts_recordings_in_destination(
        self, logged_in_client: Any
    ) -> None:
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

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path
    ) -> None:
        app, _ = _make_app(settings_path)
        with app.test_client() as client:
            resp = client.get("/api/health/storage")
        assert resp.status_code == 302
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_health.py::TestStorage -v`
Expected: FAIL -- `/api/health/storage` does not exist (404).

- [ ] **Step 3: Create `api_health.py` with the storage route**

Create `blackvuesync/server/routes/api_health.py`:

```python
"""api health routes: storage and dashcam health probes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, Response, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore
from blackvuesync.sync import filename_re

api_health_bp = Blueprint("api_health_bp", __name__, url_prefix="/api/health")

_MIME_JSON = "application/json"


def _count_recordings(destination: Path) -> int:
    """counts files in destination whose name matches the BlackVue regex."""
    count = 0
    for _, _, files in os.walk(destination):
        for name in files:
            if filename_re.match(name):
                count += 1
    return count


def _compute_storage(destination: Path) -> dict[str, object]:
    """computes storage stats for destination; returns a JSON-serializable dict.

    factored out so /api/health/storage and /hx/storage-card both share the
    same computation. when destination does not exist, returns
    {available: False, reason: ...} matching the structural-case contract.
    """
    if not destination.exists():
        return {"available": False, "reason": "destination not configured"}

    stats = os.statvfs(destination)
    total_bytes = stats.f_blocks * stats.f_frsize
    free_bytes = stats.f_bavail * stats.f_frsize
    used_bytes = total_bytes - free_bytes
    used_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0.0
    return {
        "available": True,
        "destination": str(destination),
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "used_bytes": used_bytes,
        "used_percent": used_percent,
        "recording_count": _count_recordings(destination),
    }


@api_health_bp.route("/storage", methods=["GET"])
@login_required
def storage() -> Response:
    """returns storage usage at the destination directory."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination)
    body = json.dumps(_compute_storage(destination))
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_health_bp"]
```

- [ ] **Step 4: Register the blueprint**

In `blackvuesync/server/__init__.py`, add the import and registration alongside the other `api_*` blueprints:

```python
from blackvuesync.server.routes.api_health import api_health_bp
```

```python
app.register_blueprint(api_health_bp)
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_health.py::TestStorage -v`
Expected: 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_health.py blackvuesync/server/__init__.py test/test_routes_api_health.py
git commit -m "Phase 2A: GET /api/health/storage with statvfs + recording count"
```

---

## Task 8: Add `/api/health/dashcam`

**Files:**

- Modify: `blackvuesync/server/routes/api_health.py`
- Modify: `test/test_routes_api_health.py`

- [ ] **Step 1: Append failing tests for dashcam**

Append at the end of `test/test_routes_api_health.py`:

```python
class TestDashcam:
    """tests for GET /api/health/dashcam."""

    def test_returns_reachable_true_on_success(
        self, logged_in_client: Any
    ) -> None:
        """when the HEAD probe succeeds, reachable=true with latency_ms set."""
        client, _, _ = logged_in_client
        # mock urlopen to simulate a successful HEAD response
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.status = 200
            resp = client.get("/api/health/dashcam")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["reachable"] is True
        assert "latency_ms" in body
        assert body["address"] == "192.168.0.1"

    def test_returns_reachable_false_on_timeout(
        self, logged_in_client: Any
    ) -> None:
        """when the HEAD probe times out, reachable=false with reason=timeout."""
        import socket

        client, _, _ = logged_in_client
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            resp = client.get("/api/health/dashcam")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["reachable"] is False
        assert body["reason"] == "timeout"

    def test_returns_reachable_false_on_connection_refused(
        self, logged_in_client: Any
    ) -> None:
        """when the HEAD probe is refused, reachable=false with a reason."""
        import urllib.error

        client, _, _ = logged_in_client
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            resp = client.get("/api/health/dashcam")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["reachable"] is False
        assert "reason" in body

    def test_compute_dashcam_returns_no_address_when_empty(self) -> None:
        """unit-test the _compute_dashcam helper with an empty address.

        we cannot exercise this through the route because
        ConnectionSettings.validate() rejects an empty address and
        SettingsStore.update would refuse the change. testing the helper
        directly is cleaner and covers the same code path.
        """
        from blackvuesync.server.routes.api_health import _compute_dashcam

        result = _compute_dashcam("")
        assert result["reachable"] is False
        assert result["reason"] == "no address configured"
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_health.py::TestDashcam -v`
Expected: FAIL -- `/api/health/dashcam` does not exist.

- [ ] **Step 3: Extend `api_health.py` with the dashcam route**

Add to `blackvuesync/server/routes/api_health.py`, near the bottom (before `__all__`):

```python
import socket
import time
import urllib.error
import urllib.request

# ... (the rest of the file stays the same; the imports below go to the
#      top of the file with the others; the routes below go before __all__)
```

Combined import line at the top of the file:

```python
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
```

Then add this route after the storage route:

```python
def _compute_dashcam(address: str, timeout: float = 2.0) -> dict[str, object]:
    """HEAD-probes http://<address>/blackvue_vod.cgi; returns reachability.

    factored out so /api/health/dashcam and /hx/dashcam-card share the same
    computation. blackvue dashcams expose http only (no https firmware).
    """
    if not address:
        return {"reachable": False, "reason": "no address configured"}

    url = f"http://{address}/blackvue_vod.cgi"  # NOSONAR (HTTP-only firmware)
    req = urllib.request.Request(url, method="HEAD")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout):  # NOSONAR (HTTP-only firmware)
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            return {
                "reachable": True,
                "address": address,
                "latency_ms": elapsed_ms,
            }
    except socket.timeout:
        return {"reachable": False, "address": address, "reason": "timeout"}
    except urllib.error.URLError as e:
        return {"reachable": False, "address": address, "reason": str(e.reason)}
    except OSError as e:
        return {"reachable": False, "address": address, "reason": type(e).__name__.lower()}


@api_health_bp.route("/dashcam", methods=["GET"])
@login_required
def dashcam() -> Response:
    """returns dashcam reachability via HEAD probe."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    address = store.get().connection.address
    body = json.dumps(_compute_dashcam(address))
    return Response(body, status=200, mimetype=_MIME_JSON)
```

- [ ] **Step 4: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_health.py -v`
Expected: all storage + dashcam tests pass (4 + 4 = 8).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/routes/api_health.py test/test_routes_api_health.py
git commit -m "Phase 2A: GET /api/health/dashcam HEAD probe"
```

---

## Task 9: Add `/api/recordings/recent`

**Files:**

- Create: `blackvuesync/server/routes/api_recordings.py`
- Modify: `blackvuesync/server/__init__.py`
- Create: `test/test_routes_api_recordings.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_api_recordings.py`:

```python
"""tests for GET /api/recordings/recent."""

from __future__ import annotations

import dataclasses
import json
import os
import time
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


def _make_store(settings_path: Path, destination: Path) -> SettingsStore:
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
    return store


@pytest.fixture()
def logged_in_client(settings_path: Path, tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    store = _make_store(settings_path, destination)
    app = create_app(store, testing=True)
    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "admin", "password": "test-password-1234"},
            follow_redirects=True,
        )
        yield client, destination


class TestRecent:
    """tests for GET /api/recordings/recent."""

    def test_returns_empty_for_empty_destination(
        self, logged_in_client: Any
    ) -> None:
        client, _ = logged_in_client
        resp = client.get("/api/recordings/recent")
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body["recordings"] == []
        assert body["total"] == 0

    def test_returns_newest_first(self, logged_in_client: Any) -> None:
        """newest files (highest mtime) appear first in the list."""
        client, destination = logged_in_client
        # create three valid recordings with controlled mtimes
        for name in (
            "20231015_115400_NF.mp4",
            "20231015_120000_NF.mp4",
            "20231015_113000_NF.mp4",
        ):
            (destination / name).write_text("x")
        # set mtimes so the 120000 file is newest
        now = time.time()
        os.utime(destination / "20231015_113000_NF.mp4", (now, now - 3600))
        os.utime(destination / "20231015_115400_NF.mp4", (now, now - 1800))
        os.utime(destination / "20231015_120000_NF.mp4", (now, now))

        resp = client.get("/api/recordings/recent")
        body = json.loads(resp.data)
        filenames = [r["filename"] for r in body["recordings"]]
        assert filenames == [
            "20231015_120000_NF.mp4",
            "20231015_115400_NF.mp4",
            "20231015_113000_NF.mp4",
        ]
        assert body["total"] == 3

    def test_default_limit_is_5(self, logged_in_client: Any) -> None:
        """without ?limit, returns at most 5 entries."""
        client, destination = logged_in_client
        for i in range(10):
            (destination / f"2023101{i}_120000_NF.mp4").write_text("x")
        resp = client.get("/api/recordings/recent")
        body = json.loads(resp.data)
        assert len(body["recordings"]) == 5
        assert body["total"] == 10

    def test_limit_query_param_respected(self, logged_in_client: Any) -> None:
        """?limit=3 returns 3 entries."""
        client, destination = logged_in_client
        for i in range(5):
            (destination / f"2023101{i}_120000_NF.mp4").write_text("x")
        resp = client.get("/api/recordings/recent?limit=3")
        body = json.loads(resp.data)
        assert len(body["recordings"]) == 3

    def test_non_matching_files_are_ignored(self, logged_in_client: Any) -> None:
        """files that don't match filename_re are not counted."""
        client, destination = logged_in_client
        (destination / "20231015_120000_NF.mp4").write_text("x")
        (destination / "README.txt").write_text("y")
        (destination / "random.bin").write_text("z")
        resp = client.get("/api/recordings/recent")
        body = json.loads(resp.data)
        assert body["total"] == 1

    def test_redirects_to_login_when_unauthenticated(
        self, settings_path: Path, tmp_path: Path
    ) -> None:
        destination = tmp_path / "recordings"
        destination.mkdir()
        store = _make_store(settings_path, destination)
        app = create_app(store, testing=True)
        with app.test_client() as client:
            resp = client.get("/api/recordings/recent")
        assert resp.status_code == 302
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_api_recordings.py -v`
Expected: FAIL -- `/api/recordings/recent` does not exist.

- [ ] **Step 3: Create `api_recordings.py`**

Create `blackvuesync/server/routes/api_recordings.py`:

```python
"""api routes for browsing downloaded recordings."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, Response, current_app, request

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore
from blackvuesync.sync import filename_re

api_recordings_bp = Blueprint(
    "api_recordings_bp", __name__, url_prefix="/api/recordings"
)

_MIME_JSON = "application/json"
_DEFAULT_LIMIT = 5
_MAX_LIMIT = 50


def _compute_recent(destination: Path, limit: int) -> dict[str, object]:
    """returns the N most recently modified BlackVue recordings at destination.

    factored out so /api/recordings/recent and /hx/recent-activity-card share
    the same computation. files that do not match filename_re are ignored.
    """
    if not destination.exists():
        return {"recordings": [], "total": 0}

    matches: list[tuple[float, str, str]] = []
    for root, _, files in os.walk(destination):
        for name in files:
            if not filename_re.match(name):
                continue
            path = os.path.join(root, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            matches.append((mtime, name, path))

    matches.sort(key=lambda m: m[0], reverse=True)
    head = matches[:limit]
    return {
        "recordings": [
            {"filename": name, "mtime": mtime, "path": path}
            for mtime, name, path in head
        ],
        "total": len(matches),
    }


@api_recordings_bp.route("/recent", methods=["GET"])
@login_required
def recent() -> Response:
    """returns the N most recently modified BlackVue recordings."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination)

    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))

    body = json.dumps(_compute_recent(destination, limit))
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_recordings_bp"]
```

- [ ] **Step 4: Register the blueprint**

In `blackvuesync/server/__init__.py`:

```python
from blackvuesync.server.routes.api_recordings import api_recordings_bp
```

```python
app.register_blueprint(api_recordings_bp)
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_routes_api_recordings.py -v`
Expected: 6 PASS.

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_recordings.py blackvuesync/server/__init__.py test/test_routes_api_recordings.py
git commit -m "Phase 2A: GET /api/recordings/recent with limit param"
```

---

## Task 10: Add HTMX fragments and partials

**Files:**

- Create: `blackvuesync/server/routes/hx_dashboard.py`
- Create: `blackvuesync/server/templates/_partials/storage_card.html`
- Create: `blackvuesync/server/templates/_partials/dashcam_card.html`
- Create: `blackvuesync/server/templates/_partials/next_scheduled_card.html`
- Create: `blackvuesync/server/templates/_partials/recent_activity_card.html`
- Modify: `blackvuesync/server/__init__.py`
- Create: `test/test_routes_hx_dashboard.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_hx_dashboard.py`:

```python
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
```

- [ ] **Step 2: Run the failing tests**

Run: `venv/bin/pytest test/test_routes_hx_dashboard.py -v`
Expected: FAIL -- none of the routes exist yet.

- [ ] **Step 3: Create the partials**

Create `blackvuesync/server/templates/_partials/storage_card.html`:

```jinja2
{# storage card fragment. polled every 5s by HTMX. #}
<div class="card"
     id="storage-card"
     hx-get="/hx/storage-card"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Storage</div>
  {% if available %}
    <div class="card-value">{{ used_percent }}% used</div>
    <div class="card-sub">
      {{ (free_bytes / 1073741824) | round(1) }} GB free of
      {{ (total_bytes / 1073741824) | round(1) }} GB ·
      {{ recording_count }} recordings
    </div>
    <div class="storage-bar"><div style="width: {{ used_percent }}%"></div></div>
  {% else %}
    <div class="card-value">--</div>
    <div class="card-sub">{{ reason }}</div>
  {% endif %}
</div>
```

Create `blackvuesync/server/templates/_partials/dashcam_card.html`:

```jinja2
{# dashcam card fragment. polled every 5s by HTMX. #}
<div class="card"
     id="dashcam-card"
     hx-get="/hx/dashcam-card"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Dashcam</div>
  {% if reachable %}
    <div class="card-value"><span class="dot green"></span>reachable</div>
    <div class="card-sub">{{ address }} · {{ latency_ms }} ms</div>
  {% else %}
    <div class="card-value"><span class="dot red"></span>unreachable</div>
    <div class="card-sub">{{ address or "no address configured" }} · {{ reason }}</div>
  {% endif %}
</div>
```

Create `blackvuesync/server/templates/_partials/next_scheduled_card.html`:

```jinja2
{# next-scheduled card fragment. polled every 5s by HTMX. #}
<div class="card"
     id="next-scheduled-card"
     hx-get="/hx/next-scheduled-card"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Next scheduled</div>
  {% if paused %}
    <div class="card-value">paused</div>
    <div class="card-sub">cron: {{ cron_expression }} · {{ timezone }}</div>
  {% else %}
    <div class="card-value">{{ next_human }}</div>
    <div class="card-sub">cron: {{ cron_expression }} · {{ timezone }}</div>
  {% endif %}
</div>
```

Create `blackvuesync/server/templates/_partials/recent_activity_card.html`:

```jinja2
{# recent-activity card fragment. polled every 5s by HTMX. #}
<div class="card"
     id="recent-activity-card"
     hx-get="/hx/recent-activity-card"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  <div class="card-label">Recent activity</div>
  {% if recordings %}
    <div class="card-value">{{ total }} recordings</div>
    <div class="card-stack">
      {% for r in recordings %}
        <div class="row"><span class="file">{{ r.filename }}</span></div>
      {% endfor %}
    </div>
  {% else %}
    <div class="card-value">No recordings yet</div>
  {% endif %}
</div>
```

- [ ] **Step 4: Create `hx_dashboard.py`**

Create `blackvuesync/server/routes/hx_dashboard.py`:

```python
"""htmx fragment routes for the dashboard's 4 polled cards."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, render_template

from blackvuesync.server.auth import login_required
from blackvuesync.server.routes.api_health import _compute_dashcam, _compute_storage
from blackvuesync.server.routes.api_recordings import _DEFAULT_LIMIT, _compute_recent
from blackvuesync.settings import SettingsStore

hx_dashboard_bp = Blueprint("hx_dashboard_bp", __name__, url_prefix="/hx")


def _next_human(cron_expression: str, timezone: str) -> str:
    """returns a human-readable description of the next cron tick.

    uses apscheduler's CronTrigger to compute the next fire time; falls back
    to the raw cron expression if computation fails (e.g., invalid cron).
    """
    try:
        # pylint: disable=import-outside-toplevel
        from datetime import datetime, timezone as dt_timezone

        from apscheduler.triggers.cron import CronTrigger

        # pylint: enable=import-outside-toplevel

        trigger = CronTrigger.from_crontab(cron_expression, timezone=timezone)
        now = datetime.now(dt_timezone.utc)
        next_fire = trigger.get_next_fire_time(None, now)
        if next_fire is None:
            return "--"
        delta = next_fire - now
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"in {total_seconds} s"
        if total_seconds < 3600:
            return f"in {total_seconds // 60} min"
        return next_fire.strftime("%H:%M %Z")
    except Exception:  # pylint: disable=broad-exception-caught
        return cron_expression


@hx_dashboard_bp.route("/storage-card", methods=["GET"])
@login_required
def storage_card() -> Response:
    """renders the storage card fragment."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination)
    ctx = _compute_storage(destination)
    return Response(
        render_template("_partials/storage_card.html", **ctx),
        mimetype="text/html",
    )


@hx_dashboard_bp.route("/dashcam-card", methods=["GET"])
@login_required
def dashcam_card() -> Response:
    """renders the dashcam card fragment."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    address = store.get().connection.address
    ctx = _compute_dashcam(address)
    return Response(
        render_template("_partials/dashcam_card.html", **ctx),
        mimetype="text/html",
    )


@hx_dashboard_bp.route("/next-scheduled-card", methods=["GET"])
@login_required
def next_scheduled_card() -> Response:
    """renders the next-scheduled card fragment."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    schedule = store.get().schedule
    next_human = _next_human(schedule.cron_expression, schedule.timezone)
    return Response(
        render_template(
            "_partials/next_scheduled_card.html",
            paused=schedule.paused,
            cron_expression=schedule.cron_expression,
            timezone=schedule.timezone,
            next_human=next_human,
        ),
        mimetype="text/html",
    )


@hx_dashboard_bp.route("/recent-activity-card", methods=["GET"])
@login_required
def recent_activity_card() -> Response:
    """renders the recent-activity card fragment."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination)
    ctx = _compute_recent(destination, _DEFAULT_LIMIT)
    return Response(
        render_template("_partials/recent_activity_card.html", **ctx),
        mimetype="text/html",
    )


__all__ = ["hx_dashboard_bp"]
```

- [ ] **Step 5: Register the blueprint**

In `blackvuesync/server/__init__.py`:

```python
from blackvuesync.server.routes.hx_dashboard import hx_dashboard_bp
```

```python
app.register_blueprint(hx_dashboard_bp)
```

- [ ] **Step 6: Run the tests**

Run: `venv/bin/pytest test/test_routes_hx_dashboard.py -v`
Expected: 6 PASS.

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/server/routes/hx_dashboard.py blackvuesync/server/templates/_partials/storage_card.html blackvuesync/server/templates/_partials/dashcam_card.html blackvuesync/server/templates/_partials/next_scheduled_card.html blackvuesync/server/templates/_partials/recent_activity_card.html blackvuesync/server/__init__.py test/test_routes_hx_dashboard.py
git commit -m "Phase 2A: HTMX dashboard fragments and partials"
```

---

## Task 11: Update `docs/api.md`

**Files:**

- Modify: `docs/api.md`

- [ ] **Step 1: Read the current docs/api.md to confirm style and existing section ordering**

Run: `cat docs/api.md | head -50`
Expected: see the existing structure (Health Endpoints, Auth Endpoints, UI Endpoints, Sync API Endpoints, HTMX Fragment Endpoints, Settings API Endpoints, Auth API Endpoints).

- [ ] **Step 2: Add 3 new sections to `docs/api.md`**

Append at the bottom (after the existing "Auth API Endpoints" section):

```markdown

---

## Health API Endpoints

### `GET /api/health/storage`

Returns storage usage at the destination directory.

```json
{
  "available": true,
  "destination": "/recordings",
  "total_bytes": 137438953472,
  "free_bytes": 50725394432,
  "used_bytes": 86713559040,
  "used_percent": 63.1,
  "recording_count": 481
}
```

When the destination does not exist on disk:

```json
{"available": false, "reason": "destination not configured"}
```

### `GET /api/health/dashcam`

HEAD-probes `http://<settings.connection.address>/blackvue_vod.cgi` with a
2-second timeout.

Success:

```json
{"reachable": true, "address": "192.168.1.50", "latency_ms": 38.0}
```

Failure:

```json
{"reachable": false, "address": "192.168.1.50", "reason": "timeout"}
```

When no address is configured:

```json
{"reachable": false, "reason": "no address configured"}
```

---

## Recordings API Endpoints

### `GET /api/recordings/recent`

Returns the N most recently modified BlackVue recordings at the destination.
Default `limit` is 5; clamped to `[1, 50]` via query param `?limit=N`.

```json
{
  "recordings": [
    {"filename": "20231015_120000_NF.mp4", "mtime": 1697371200.0, "path": "/recordings/20231015_120000_NF.mp4"}
  ],
  "total": 1
}
```

---

## Schedule API Endpoints

### `POST /api/schedule/pause`

Sets `settings.schedule.paused = true`. The next scheduled sync is skipped
(the scheduler logs `scheduled sync skipped: schedule is paused`). Manual
`POST /api/sync/now` is unaffected -- operators can still trigger ad-hoc syncs.

```json
{"paused": true}
```

### `POST /api/schedule/resume`

Sets `settings.schedule.paused = false`. Idempotent: returns 200 even when
the schedule was already running.

```json
{"paused": false}
```

---

## Sync API Endpoints (additions)

### `POST /api/sync/stop`

Requests cooperative stop of the active sync. The download chunk loop in
`sync.py` checks the stop flag between chunks and raises
`UserWarning("sync stopped by user")` on its next boundary, which the
existing exception classifier routes through the normal `failed` exit path.
The partial `.filename.mp4` dotfile survives; the next sync resumes it
naturally.

Returns 202 when a sync was running:

```json
{"job_id": "deadbeef...", "stopping": true}
```

Returns 404 when no sync is active:

```json
{"error": "no sync is running", "code": "SYNC_NOT_RUNNING", "details": {}}
```

---

## HTMX Fragment Endpoints (additions)

Four new card fragments, each polled every 5 seconds by HTMX. Each fragment
renders the matching `_partials/*.html` template.

- `GET /hx/storage-card` -- renders `_partials/storage_card.html` with the same data as `/api/health/storage`
- `GET /hx/dashcam-card` -- renders `_partials/dashcam_card.html` with the same data as `/api/health/dashcam`
- `GET /hx/next-scheduled-card` -- renders `_partials/next_scheduled_card.html` with the next cron fire time, paused flag, cron expression, and timezone
- `GET /hx/recent-activity-card` -- renders `_partials/recent_activity_card.html` with the same data as `/api/recordings/recent` (default `limit=5`)

```

- [ ] **Step 3: Sanity-check markdown**

Run: `pre-commit run markdownlint-cli2 --files docs/api.md`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/api.md
git commit -m "Phase 2A: document new endpoints and HTMX fragments"
```

---

## Task 12: Final test sweep

- [ ] **Step 1: Full unit test suite**

Run: `venv/bin/pytest test/`
Expected: 395 (foundation baseline) + ~30 (Phase 2A new) ≈ **~425 passed**. Exact count may vary by a handful depending on parameterization; verify no regressions.

- [ ] **Step 2: Behave subprocess mode**

Run: `behave`
Expected: 21/21 scenarios (no new BDD in 2A; this is a regression guard).

- [ ] **Step 3: Behave docker mode**

Run: `behave -D implementation=docker`
Expected: 21/21 scenarios. Phase 2A adds backend routes but no UI; existing scenarios should not be affected.

- [ ] **Step 4: Pre-commit on all files**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 5: Coverage**

Run: `./coverage.sh`
Expected: `coverage_report/index.html` generated; overall coverage stays ≥85% with no regression vs. foundation baseline.

- [ ] **Step 6: Smoke test the new endpoints manually**

In one terminal:

```bash
BLACKVUESYNC_CONFIG_PATH=/tmp/bvs-2a-test-settings.json \
ADDRESS=192.0.2.1 \
venv/bin/python -m blackvuesync serve
```

In another terminal, complete the first-run wizard at `http://localhost:8080/first-run`, then:

```bash
COOKIE="bvs_session=...(copy from browser devtools)..."
curl -s -b "$COOKIE" http://localhost:8080/api/health/storage | jq .
curl -s -b "$COOKIE" http://localhost:8080/api/health/dashcam | jq .
curl -s -b "$COOKIE" http://localhost:8080/api/recordings/recent | jq .

# pause / resume require CSRF; easier to verify via the unit tests.
```

Expected: each endpoint returns valid JSON matching its shape in `docs/api.md`. The dashcam endpoint will report `reachable: false` since `192.0.2.1` is unroutable; that's the expected timeout path.

Clean up: `rm -f /tmp/bvs-2a-test-settings.json` and `Ctrl-C` the server.

---

## Task 13: Open the PR (controlling agent only)

This task is **reserved for the controlling agent**. The implementer must NOT push the branch or open a PR. The controlling agent handles:

1. `git push -u origin sub-project-2-phase-a`
2. `gh pr create --repo tekgnosis-net/blackvuesync --base main --head sub-project-2-phase-a --title "Sub-Project #2 Phase 2A: Dashboard backend" --body ...`
3. Watch the 5 required CI checks
4. Squash-merge once green

---

## Self-review against spec

| Spec requirement (Section 2 / Section 3) | Plan task |
| --- | --- |
| `GET /api/health/storage` (statvfs + walk) | Task 7 |
| `GET /api/health/dashcam` (HEAD probe with 2s timeout) | Task 8 |
| `GET /api/recordings/recent?limit=N` | Task 9 |
| `POST /api/schedule/pause` | Task 6 |
| `POST /api/schedule/resume` | Task 6 |
| `POST /api/sync/stop` | Task 5 |
| 4 HTMX fragments | Task 10 |
| 4 Jinja2 partials | Task 10 |
| `sync.py` cooperative stop via `threading.Event` | Task 4 |
| `download_with_resume` chunk-loop check raises `UserWarning("sync stopped by user")` | Task 4 |
| `scheduler.py` `_scheduled_run` skips when `settings.schedule.paused` | Task 3 |
| `settings.schedule.paused: bool = False` (TIER stays `next_tick`) | Task 2 |
| `trigger_sync` clears stop flag before spawning | Task 4 |
| Computation helpers (`_compute_storage`, `_compute_dashcam`, `_compute_recent`) shared between API and HTMX | Tasks 7 + 8 + 9 + 10 |
| All routes `@login_required` | Tasks 5 + 6 + 7 + 8 + 9 + 10 |
| CSRF on POST endpoints | Tasks 5 + 6 |
| ~30 new tests | Tasks 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10 (estimated ~36 total) |
| No UI changes in 2A | Verified -- task list does not touch templates beyond the 4 new partials |
| Carry-forwards remain deferred | Explicit in "Files explicitly NOT to modify" |

---

## What is NOT done in Phase 2A (recap)

- Dashboard UI template (`dashboard.html`) -- Phase 2B
- Sidebar CSS, hero gradient, mode transitions -- Phase 2B / 2C
- SSE EventSource wiring on the client -- Phase 2C
- BDD scenarios for dashboard behavior -- Phase 2B / 2C
- `sync.py` S3776 decomposition -- separate cleanup PR
- Multi-stage Dockerfile -- separate cleanup PR
