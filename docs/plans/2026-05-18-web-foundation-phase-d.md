# Web Foundation -- Phase D: Progress publisher + sync API

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 -->

**Date:** 2026-05-19
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Section 1 progress emitters; Section 4 sync API)
**Phase:** D of 7 (A, B, C done; E-G to follow).

**Goal:** Introduce a thread-safe `ProgressPublisher` that observes the
download lifecycle, plus the sync-related API endpoints (`/api/sync/progress`
snapshot, `/api/sync/progress/stream` SSE, `/api/sync/now` trigger,
`/api/sync/last` summary) and two HTMX fragment endpoints. The downloader
gains an optional `on_chunk` callback; the existing cron-driven sync flow is
unchanged for now (Phase E replaces cron). Lays the foundation for the
Dashboard sub-project (#2) to render a live progress card.

**Architecture:** `ProgressPublisher` owns frozen `FileProgress` and
`SyncProgress` dataclasses under a `threading.RLock`. Writer API
(`begin_job`, `start_file`, `update_bytes`, `finish_file`, `end_job`) is
called from the sync code; reader API (`snapshot`, `subscribe`) is called
from Flask handlers. Throttled at 5 Hz to subscribers; per-chunk updates to
in-memory state are unthrottled. Final snapshot retained for 10s after
sync completes. The downloader receives the callback via a new `on_chunk`
parameter on `download_with_resume`; `sync.py` stays free of Flask imports.

`POST /api/sync/now` is the new trigger surface. In Phase D it spawns
`run_sync(...)` in a daemon thread guarded by a process-wide `threading.Lock`
that returns 409 if a sync is already running. Phase E retires this in
favor of APScheduler's `max_instances=1` job dispatch; for Phase D the
threading.Lock is the simplest correct implementation.

**Tech Stack:** Python 3.9 stdlib + existing deps (Flask, Flask-WTF,
waitress, argon2-cffi). **No new runtime deps in this phase.** Test-only
deps unchanged.

**Out of scope for Phase D:**

- APScheduler / cron retirement -- Phase E.
- Dashboard cards consuming the new endpoints -- sub-project #2.
- Settings UI -- sub-project #3.
- Log viewer / Stats / Dashcam viewer -- sub-projects #4 / #5 / #6.
- Replacing `argparse.Namespace` with `Settings` in `run_sync` -- Phase E
  (the Phase B/C work deferred this).

---

## File structure after Phase D

```
blackvuesync/
├── __init__.py                      unchanged
├── __main__.py                      MODIFIED: decomposes main() into
│                                    cmd_sync()/cmd_serve() helpers
│                                    (resolves S3776 cognitive complexity);
│                                    serve mode now instantiates the
│                                    ProgressPublisher and exposes it on app
├── settings.py                      unchanged
├── sync.py                          MODIFIED: download_with_resume() gains
│                                    optional on_chunk parameter; sync code
│                                    threads the publisher through
├── metrics.py                       unchanged
└── server/
    ├── __init__.py                  MODIFIED: create_app accepts a
    │                                progress_publisher kwarg; registers
    │                                the new blueprints
    ├── auth.py                      unchanged
    ├── progress.py                  NEW: FileProgress, SyncProgress,
    │                                ProgressPublisher
    ├── sync_runner.py               NEW: thin wrapper that spawns run_sync
    │                                in a daemon thread under a process-wide
    │                                lock; surfaces "already running" -> 409
    ├── routes/
    │   ├── __init__.py              unchanged
    │   ├── auth.py                  unchanged
    │   ├── ui.py                    unchanged
    │   ├── health.py                unchanged
    │   ├── api_sync.py              NEW: /api/sync/progress (GET),
    │   │                            /api/sync/progress/stream (SSE),
    │   │                            /api/sync/now (POST),
    │   │                            /api/sync/last (GET)
    │   └── hx_sync.py               NEW: /hx/sync/status-card,
    │                                /hx/sync/last-run-card
    └── templates/
        └── _partials/
            ├── sync_status_card.html  NEW: htmx fragment template
            └── last_run_card.html     NEW: htmx fragment template

test/
├── test_progress.py                 NEW: FileProgress/SyncProgress state
│                                    machine; ProgressPublisher writer/reader;
│                                    throttle; post-complete retention
├── test_sync_runner.py              NEW: locking, daemon thread lifecycle,
│                                    409-on-already-running
├── test_routes_api_sync.py          NEW: /api/sync/* responses, SSE protocol
├── test_routes_hx_sync.py           NEW: /hx/sync/* fragment rendering
└── test_sync_callback.py            NEW: download_with_resume on_chunk
                                     invocations
```

---

## How to work with this plan

Per-task commits on `web-foundation-phase-d` branch. The plan is split into
**opening cleanup** (Task C1, the deferred Phase C S3776) and **main work**
(Tasks M1-M10). Each task ends with running the relevant tests and
committing.

Pre-commit hooks will reformat / lint on each commit. Hook auto-fixes get
re-staged and committed as NEW commits (no amends, no `--no-verify`).

---

## Opening cleanup (carry-forward from Phase C review)

### Task C1: Decompose `main()` into `cmd_sync` and `cmd_serve`

Files: `blackvuesync/__main__.py`, `test/test_app_factory.py` (if needed).

Steps:

1. The current `main()` in `__main__.py:269` has cognitive complexity 24
   (limit 15) due to the subcommand dispatch + the existing sync flow all
   in one function.
2. Extract two helpers:
   - `cmd_sync(args: argparse.Namespace) -> int` -- the existing sync flow
     (lines that today set `_sync.dry_run`, parse retention, instantiate
     SyncMetrics, call `run_sync`, write metrics state, etc.). Returns the
     exit code.
   - `cmd_serve(args: argparse.Namespace) -> int` -- the existing serve
     flow (instantiate SettingsStore, `create_app(store)`,
     `waitress.serve(...)`). Returns the exit code.
3. `main()` becomes a thin dispatcher:
   ```python
   def main() -> int:
       _try_load_settings_store(_DEFAULT_SETTINGS_PATH)  # bootstrap side effect
       args = parse_args()
       configure_logging(args.log_format)
       if getattr(args, "subcommand", None) == "serve":
           return cmd_serve(args)
       return cmd_sync(args)
   ```
4. Run `pylint blackvuesync/__main__.py` -- the S3776 finding should no
   longer fire (each new function's complexity should be well under 15).
5. Run full test suite; verify all existing scenarios still pass.

---

## Main work: Progress publisher + sync API

### Task M1: Create `blackvuesync/server/progress.py`

Files: `blackvuesync/server/progress.py` (create).

Steps:

1. Module docstring + imports:
   ```python
   """progress publisher: thread-safe state for sync run + per-file download progress."""
   from __future__ import annotations

   import dataclasses
   import queue
   import threading
   import time
   import uuid
   from typing import Iterator, Literal
   ```
2. Define `FileProgress` (`@dataclass(frozen=True)`):
   - filename: str
   - recording_base: str
   - artifact: `Literal["mp4", "thm", "3gf", "gps"]`
   - direction: `Literal["F", "R", "I", "O"] | None`
   - total_bytes: int
   - downloaded_bytes: int
   - started_at_monotonic: float
   - started_at_wall: float
   - updated_at_monotonic: float
   - bytes_per_second: float
   - eta_seconds: float | None
   - state: `Literal["starting", "downloading", "resumed", "complete", "failed"]`
   - failure_reason: str | None
   - properties: `percent` (0-100), `elapsed_seconds`
3. Define `SyncProgress` (`@dataclass(frozen=True)`):
   - job_id: str
   - started_at_wall: float
   - state: `Literal["idle", "running", "complete", "failed"]`
   - current_file: FileProgress | None
   - files_total: int
   - files_completed: int
   - files_failed: int
   - bytes_downloaded_total: int
   - last_event_monotonic: float
   - classmethod `idle()` returns a SyncProgress in idle state.
   - property `percent`

### Task M2: Implement `ProgressPublisher`

Files: `blackvuesync/server/progress.py`.

Steps:

1. Class with class constants:
   - `PUBLISH_HZ = 5.0`
   - `POST_COMPLETE_RETENTION = 10.0`
2. `__init__`: `_lock = threading.RLock()`, `_state = SyncProgress.idle()`,
   `_subscribers: set[queue.Queue] = set()`, `_last_publish_monotonic = 0.0`.
3. Writer API:
   - `begin_job(files_total: int) -> str` -- generates `uuid4().hex`,
     replaces state with `SyncProgress(job_id=..., state="running", ...)`,
     publishes.
   - `start_file(filename, artifact, total_bytes)` -- updates `current_file`
     to new FileProgress, publishes.
   - `update_bytes(downloaded: int)` -- updates `current_file.downloaded_bytes`
     plus EWMA rate; publishes only if `PUBLISH_HZ` rate-limit allows.
   - `finish_file(success: bool, reason: str | None = None)` -- transitions
     current_file to complete/failed, bumps aggregate counts, publishes.
   - `end_job(success: bool)` -- transitions state to complete/failed;
     keeps the final snapshot visible for `POST_COMPLETE_RETENTION` seconds
     (schedule a `threading.Timer` to clear back to idle).
4. Reader API:
   - `snapshot() -> SyncProgress` -- read under lock, return frozen state
     directly (safe to share).
   - `subscribe() -> Iterator[SyncProgress]` -- creates a bounded
     `queue.Queue(maxsize=2)`, adds to subscribers, yields snapshots. Drops
     intermediate frames when consumer is slow. 30-second heartbeat
     timeout (the Flask SSE handler will translate the timeout into an
     SSE keepalive comment).
5. Internal `_publish_to_subscribers(snapshot)` puts the snapshot in every
   subscriber queue with `put_nowait`, swallowing `queue.Full` (drop-frame
   semantics).

### Task M3: Wire `on_chunk` callback into `download_with_resume`

Files: `blackvuesync/sync.py`, `test/test_sync_callback.py` (create).

Steps:

1. Add optional parameter to `download_with_resume`:
   ```python
   def download_with_resume(
       url: str,
       dest_path: pathlib.Path,
       *,
       on_chunk: Callable[[int, int], None] | None = None,
       # ... existing parameters
   ) -> None:
   ```
2. Inside the chunked-write loop, call `on_chunk(downloaded, total_bytes)`
   after each chunk write if `on_chunk is not None`.
3. Thread the publisher through the call chain from `download_recording` /
   `sync()` to `download_with_resume`. The publisher hooks in at the
   `download_recording` site:
   ```python
   publisher.start_file(filename, artifact, total_bytes)
   try:
       download_with_resume(url, dest_path,
                             on_chunk=lambda dl, total: publisher.update_bytes(dl))
       publisher.finish_file(success=True)
   except Exception as exc:
       publisher.finish_file(success=False, reason=type(exc).__name__)
       raise
   ```
4. `sync.py` accepts an optional `publisher` parameter; if None, all the
   `publisher.X()` calls become no-ops (via a `_NoopPublisher` sentinel
   class with all the writer methods as `def x(self, *args, **kwargs): pass`).
   That keeps `sync.py` free of Flask imports and lets the existing CLI
   sync work without any publisher wired in.
5. Tests in `test_sync_callback.py`:
   - `download_with_resume` with `on_chunk=mock_callback` invokes the
     callback with expected `(downloaded, total)` tuples over a small
     mock-dashcam scenario.
   - The mock callback is called at least once per chunk written.
   - `download_with_resume` without `on_chunk` works exactly as before.

### Task M4: Implement `blackvuesync/server/sync_runner.py`

Files: `blackvuesync/server/sync_runner.py` (create), `test/test_sync_runner.py` (create).

Steps:

1. Module docstring + imports.
2. Module-level `_sync_lock = threading.Lock()` and `_current_thread: threading.Thread | None`.
3. Function `trigger_sync(settings, publisher, metrics_state_file) -> dict`:
   - Try `_sync_lock.acquire(blocking=False)`. If False, return
     `{"status": "already_running", "job_id": <existing_job_id>}`.
   - Generate `job_id = publisher.begin_job(files_total=0)` (we don't know
     count yet; `start_file` updates `files_total` incrementally as the
     sync code lists recordings).
   - Spawn a daemon thread that calls `run_sync(...)` with the publisher
     wired through (`publisher.end_job(success)` in finally).
   - Release the lock when the thread exits (use `threading.Event` /
     callback). Return `{"status": "started", "job_id": job_id}`.
4. Tests:
   - First call returns started + job_id; second concurrent call returns
     already_running.
   - After the first sync completes (mock the actual `run_sync` with a
     `time.sleep(0.1)` stub), a third call succeeds and gets a new
     job_id.

### Task M5: API endpoints in `blackvuesync/server/routes/api_sync.py`

Files: `blackvuesync/server/routes/api_sync.py` (create), `test/test_routes_api_sync.py` (create).

Steps:

1. Module: import Flask, JSON helpers, the publisher.
2. Blueprint `api_sync_bp = Blueprint("api_sync_bp", __name__, url_prefix="/api/sync")`.
3. Routes (all `@login_required`):
   - `GET /api/sync/progress` -- returns `progress.snapshot()` as JSON
     (use `dataclasses.asdict` for serialization; tuples become lists).
   - `GET /api/sync/progress/stream` -- SSE response with
     `Content-Type: text/event-stream`. Subscribe to the publisher, emit
     each snapshot as `event: progress\ndata: <json>\n\n`. Heartbeat
     comment every 30s. `X-Accel-Buffering: no` header so nginx-family
     proxies don't buffer.
   - `POST /api/sync/now` -- CSRF-protected (already global via
     Flask-WTF). Calls `trigger_sync(...)`. Returns 202 + `{"job_id": ...}`
     on success or 409 + `{"error": ..., "code": "SYNC_ALREADY_RUNNING",
     "details": {"current_job_id": ...}}`.
   - `GET /api/sync/last` -- returns the most recent completed snapshot
     (whatever the publisher retained during the 10s post-complete
     window) or 204 No Content if no sync has ever run.
4. Tests:
   - `GET /api/sync/progress` returns idle state initially.
   - `POST /api/sync/now` without CSRF -> 400.
   - `POST /api/sync/now` with CSRF -> 202 + job_id (mock sync_runner).
   - Two consecutive `POST /api/sync/now` -> 409 on the second.
   - SSE stream: subscribe via test client, inject events into publisher,
     assert events are received in order.
   - `GET /api/sync/last` initially returns 204; after a mock completion,
     returns 200 + summary.

### Task M6: HTMX fragment endpoints in `blackvuesync/server/routes/hx_sync.py`

Files: `blackvuesync/server/routes/hx_sync.py` (create),
`blackvuesync/server/templates/_partials/sync_status_card.html` (create),
`blackvuesync/server/templates/_partials/last_run_card.html` (create),
`test/test_routes_hx_sync.py` (create).

Steps:

1. Blueprint `hx_sync_bp = Blueprint("hx_sync_bp", __name__, url_prefix="/hx/sync")`.
2. Routes (`@login_required`):
   - `GET /hx/sync/status-card` -- renders `_partials/sync_status_card.html`
     with the current snapshot.
   - `GET /hx/sync/last-run-card` -- renders `_partials/last_run_card.html`
     with the last completed snapshot.
3. Templates: simple cards using existing Apple-design components. Show
   state, current file (filename + percent), aggregate (files_completed/
   files_total), bytes_downloaded_total.
4. Tests:
   - Both endpoints return 200 + HTML content when authenticated.
   - 302 to /login when not authenticated (login mode).

### Task M7: Wire publisher into `create_app` and `cmd_serve`

Files: `blackvuesync/server/__init__.py`, `blackvuesync/__main__.py`.

Steps:

1. In `create_app`, add `progress_publisher` kwarg with a default of
   `None`. If `None`, create one inside `create_app` (since the publisher
   is process-state, not test-state). Attach to `app.progress_publisher`.
2. Register the new blueprints (`api_sync_bp`, `hx_sync_bp`).
3. In `cmd_serve`, instantiate `ProgressPublisher` and pass it into
   `create_app(settings_store, progress_publisher=publisher)`.

### Task M8: SSE protocol tests + manual smoke

Files: `test/test_routes_api_sync.py` (already created in M5; this task
expands SSE-specific tests).

Steps:

1. Write tests that subscribe to `/api/sync/progress/stream`, inject events
   into the publisher (e.g., `publisher.start_file(...)`,
   `publisher.update_bytes(...)`), and assert the SSE-formatted bytes
   arriving at the test client match the expected `event: progress\ndata:
   {json}\n\n` shape.
2. Test the heartbeat: with no events for 30 seconds (mock time), a
   keepalive comment line should appear in the stream.
3. Document the smoke-test path in a comment block in the test file:
   ```bash
   # manual smoke: with the server running locally,
   #   curl -N -H "Cookie: bvs_session=..." http://localhost:8080/api/sync/progress/stream
   # then in another terminal trigger a sync via /api/sync/now
   # and watch the stream emit progress events.
   ```

### Task M9: Threading + correctness verification

Files: existing test files; targeted hardening.

Steps:

1. Add tests for the throttle:
   - Inject 1000 `update_bytes` calls in 1 real second; assert subscribers
     received <= 6 frames (PUBLISH_HZ * 1.2 tolerance).
2. Add tests for post-complete retention:
   - After `end_job(success=True)`, `snapshot()` returns `state="complete"`
     for ~10s, then transitions to `idle()` automatically.
   - Mock time to verify the transition.
3. Add a concurrency test:
   - Two threads call `update_bytes` rapidly; the snapshot is always
     internally consistent (no torn reads).

### Task M10: Documentation

Files: `CLAUDE.md`, `docs/api.md`.

Steps:

1. CLAUDE.md "Server Package" section gains a subsection on the progress
   publisher and the new endpoints.
2. docs/api.md adds the four new endpoints with request/response examples
   plus a section on SSE event format.
3. Note: the dashboard (`sub-project #2`) is the consumer of these
   endpoints. The placeholder dashboard.html will be updated by #2 to
   include HTMX `hx-get="/hx/sync/status-card"` + `hx-trigger="every 5s"`.

---

## Verification

Run before opening PR:

- `pytest test/ -v` -- all unit tests pass (~280+ -> ~330+ tests).
- `pytest test/test_progress.py --cov=blackvuesync/server/progress.py` -- ≥95%.
- `pytest test/test_routes_api_sync.py --cov=blackvuesync/server/routes/api_sync.py` -- ≥90%.
- `mypy blackvuesync/` -- clean.
- `pre-commit run --all-files` -- clean.
- `behave --no-capture` -- 21 in-process scenarios pass (sync behavior
  unchanged from the CLI user's perspective).
- `behave -D implementation=docker` -- 21 docker-mode scenarios pass.
- Manual smoke:
  ```bash
  rm -rf /tmp/phase-d
  BLACKVUESYNC_CONFIG_PATH=/tmp/phase-d/settings.json \
    python -m blackvuesync serve --port 8080 &
  # set admin password via /first-run, then login, then:
  curl -s http://localhost:8080/api/sync/progress  # state: idle
  curl -X POST -H "Cookie: bvs_session=..." \
       -H "X-CSRFToken: ..." \
       http://localhost:8080/api/sync/now  # 202 + job_id (will fail to
                                            # reach a real dashcam, that's fine)
  ```

## Branch protection workflow

Push to `web-foundation-phase-d`. Open PR titled
`Web Foundation Phase D: progress publisher and sync API`. All five
required checks must pass. **Squash and merge** (linear history rule).

## After Phase D merges

Phase E retires cron. APScheduler integrated; `python -m blackvuesync serve`
becomes the single-process container default; `entrypoint.sh` +
`blackvuesync.sh` + `crontab` are deleted.

---

## Self-review

Done inline:

- **Spec coverage:** Section 1 progress emitter design + Section 4 sync API
  fully covered.
- **Placeholder scan:** No TBDs.
- **Type consistency:** `FileProgress`/`SyncProgress` are frozen dataclasses;
  `ProgressPublisher` uses `dataclasses.replace` for state transitions.
- **Scope:** Strictly Phase D. APScheduler + cron retirement is Phase E.
  Dashboard cards consuming these endpoints are sub-project #2.
- **Ambiguity:** The "10-second post-complete retention" uses
  `threading.Timer` to auto-clear the snapshot. An alternative is
  retain-until-next-sync-starts; plan picks the timer approach since it
  matches the design spec.

Known limitation: `_sync_lock` in `sync_runner.py` is in-memory only --
multi-process deployments would need a shared lock store. Acceptable for
single-instance personal use.
