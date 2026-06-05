# Sub-Project #4 -- Log Viewer -- Design Spec

**Date:** 2026-06-05
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design approved by user; awaiting implementation plan (writing-plans)
**Series:** fourth web-app sub-project, after #1 Web Foundation, #2 Dashboard
(2A/2B/2C), #3 Settings UI.

---

## Context

The `/logs` route is currently a placeholder (`_placeholders/logs.html`,
"coming in sub-project #4"). This sub-project turns it into a live log viewer.

**Scope-defining finding (verified against the codebase):** the in-memory ring
buffer the Web Foundation design promised was **never wired**, and neither was
the rotating file handler. The `logging` settings fields exist
(`ring_buffer_capacity`, `file_max_bytes`, `file_backup_count`) with validators
and a Settings-UI form field, but **no handler consumes them**:

- `sync.py:86` does `logging.basicConfig(format=TEXT_LOG_FORMAT)` -- a single
  stdout `StreamHandler` is the only handler.
- `configure_logging()` (`sync.py:73-83`) only calls `setFormatter()` on
  already-attached handlers; it adds none.
- The only references to `ring_buffer_capacity` are its dataclass definition
  (`settings.py:169`), its validator (`settings.py:185-186`), and its form field
  (`settings_form.py:112`).

Therefore #4 must **build the ring-buffer handler** (and finally make those
settings meaningful), **wire the rotating file handler**, and then build the
viewer on top. The live-update machinery (SSE endpoint shape, the `EventSource`
client with exponential backoff, `connect-src 'self'` CSP) and the page layout
(settings-style shell, `@alpinejs/csp` imperative components) are already shipped
and reused directly.

---

## Decisions (resolved during brainstorming)

1. **Backend scope:** ring buffer **plus** the rotating file handler. The file
   handler writes to `/config/logs/` (already a bind-mounted volume), so
   historical browsing happens on the host / in console. **No** rotated-file
   browsing UI in the app.
2. **Transport:** SSE live tail, reusing the dashboard pattern (chosen over HTMX
   polling and cursor long-poll).
3. **Viewer controls:** min-level display filter, live verbosity control,
   free-text search, tail ergonomics (pause/resume auto-scroll + clear view),
   and a read-only display of the active log file path.
4. **Serve-mode only:** both new handlers attach in `cmd_serve`. `sync.py` stays
   stdlib-only and the standalone CLI sync path keeps its single stdout handler,
   preserving the portability constraint.
5. **"Filter by module"** (a placeholder promise) is covered by free-text search
   -- only two loggers exist (`root`, `cron`), so a dedicated control is not
   worth it.

---

## Design

### 1. Architecture -- two sinks, one viewer

The root logger feeds three handlers; #4 adds the latter two (serve mode only):

| Handler | Lifetime | Role | Added by |
| --- | --- | --- | --- |
| stdout `StreamHandler` | always | `docker logs` | existing (`basicConfig`) |
| `RotatingFileHandler` | serve | durable, host-browsable history | #4 |
| `LogBuffer` | serve | ephemeral RAM ring for the live tail | #4 |

The two new sinks share the existing formatter and logger but never reference
each other: the viewer's read path (tail the buffer) is fully decoupled from the
durability path (append to file). What the UI shows and where old logs live are
answered independently.

### 2. Ring-buffer handler -- `blackvuesync/server/log_buffer.py` (new)

A `logging.Handler` subclass using `ProgressPublisher`'s threading skeleton but
with **don't-drop** delivery semantics (every line matters; progress is
latest-wins and may drop frames, logs may not).

- Stores **`LogLine`** frozen dataclasses, not `LogRecord` objects. Each record
  is rendered to plain data at `emit()` time:
  `LogLine(seq: int, ts: str, level: str, level_no: int, logger: str, message: str)`
  where `ts` is ISO-8601 with a `Z` suffix and `seq` is a monotonic counter.
  Rendering at emit time decouples the buffer from logging internals and avoids
  pinning `exc_info`/traceback objects in RAM.
- Backing store: `collections.deque(maxlen=ring_buffer_capacity)`.
- Thread-safe via an `RLock`; fan-out to subscribers via one bounded
  `queue.Queue` per subscriber.
- **Reader API:**
  - `snapshot() -> list[LogLine]` -- current ring contents (initial paint).
  - `subscribe() -> Iterator[list[LogLine]]` -- yields **batches of new lines**
    (never latest-wins); 30 s heartbeat like the progress stream.
- `set_capacity(n: int) -> None` -- rebuilds the deque under lock (a deque's
  `maxlen` is immutable, so resize means a new deque seeded from the old,
  truncated to the new capacity).

`emit()` must never raise into the logging call site: it formats, appends under
lock, and offers the line to each subscriber queue, discarding for a queue that
is full (that subscriber re-syncs from `snapshot()` on its next reconnect using
the last seen `seq`).

### 3. Durable file handler

A stdlib `logging.handlers.RotatingFileHandler` writing
`/config/logs/blackvuesync.log`, `maxBytes=file_max_bytes`,
`backupCount=file_backup_count`, sharing the active formatter (text or json).
The `logs/` subdirectory is created at startup with `0700` perms. Rotated files
(`blackvuesync.log`, `blackvuesync.log.1`, ...) land on the bind-mounted host
volume for manual/console browsing.

### 4. Startup wiring + live reload -- `blackvuesync/__main__.py`

`cmd_serve` constructs both handlers once, attaches them to the root logger,
attaches the `LogBuffer` to the app (`app.log_buffer`, mirroring
`app.progress_publisher`), and keeps references for reconfiguration. The existing
`_apply_logging_settings` / `_register_logging_reload` path is extended so the
previously-inert settings take effect live (TIER `immediate`, no restart):

| Setting change | Action |
| --- | --- |
| `format` | `setFormatter` on all handlers (existing behavior) |
| `verbose` / `quiet` | `set_logging_levels(...)` (existing) -- changes capture |
| `ring_buffer_capacity` | `log_buffer.set_capacity(n)` (newly meaningful) |
| `file_max_bytes` / `file_backup_count` | swap in a freshly-configured `RotatingFileHandler` (newly meaningful) |

The reconfiguration logic for the new (serve-only) handlers lives in the serve
wiring, not in `sync.py`.

### 5. API -- `blackvuesync/server/routes/api_logs.py` (new)

Blueprint at `/api/logs`, both routes `@login_required`:

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/api/logs/recent` | JSON `{lines: [LogLine...], file_path, capacity, level}` -- initial paint, the file-path display, and the current verbosity |
| `GET` | `/api/logs/stream` | SSE: `event: logs\ndata: {"lines":[...]}\n\n`; `: keepalive` heartbeat; headers `Cache-Control: no-store`, `X-Accel-Buffering: no` |

The SSE generator blocks on the subscriber queue, then does a non-blocking sweep
to drain whatever else is queued, so bursts **coalesce into one frame** without a
timer and without dropping lines.

**Verbosity uses no new endpoint:** the viewer's verbosity control reuses the
existing CSRF-protected `PATCH /api/settings/logging` (immediate tier).

### 6. Viewer -- templates/js/css

Replaces `_placeholders/logs.html`. Files:

- `templates/logs.html` -- controls bar + scrolling monospace log pane, reusing
  the settings-page shell. The `/logs` route **server-renders the initial
  snapshot** (graceful degradation: without JS, recent lines are still readable;
  only live streaming needs JS, matching the dashboard).
- `static/js/logs.js` -- an `@alpinejs/csp` component `logsPage` (bare
  method/property refs, no inline expressions). Reuses the dashboard's
  `EventSource` lifecycle verbatim: exponential backoff `2s -> 4s -> ... -> 30s`,
  reset on a healthy frame, unified `302 -> /login`.
- `static/css/logs.css` -- pane, rows, level colors, control states.

Controls:

- **Min-level display filter** (DEBUG/INFO/WARNING/ERROR) -- client-side,
  compares `level_no`, toggles row visibility via a CSS class.
- **Free-text search** -- client-side substring over visible rows (also serves
  "filter by module": type `cron`).
- **Tail ergonomics** -- pause/resume auto-scroll + clear view.
- **Live verbosity** -- a segmented control (Quiet / Normal / Verbose / Debug ->
  `quiet=true` / `verbose=0` / `verbose=1` / `verbose=2`) that PATCHes
  `/api/settings/logging` and reflects the current value from `/recent`.
- **Log file path** -- read-only line showing `file_path`.

**Two hard requirements baked into the render path:**

1. Rows are built with `textContent` / `createElement`, **never** `innerHTML`.
   Log messages are effectively untrusted text (filenames, dashcam responses,
   error strings); `innerHTML` would be a DOM-XSS vector and would also violate
   the CSP posture.
2. The DOM is **capacity-bounded**: appending a new row evicts the oldest so the
   pane never exceeds `ring_buffer_capacity`, preventing memory growth in a
   long-lived tab.

The display filter narrows what is **seen**; the verbosity control changes what
is **captured**. Lowering verbosity to Debug is what makes DEBUG lines start
flowing into all sinks at once.

### 7. Edge cases

| Situation | Handling |
| --- | --- |
| SSE stream breaks | dashboard backoff `2s -> 30s`; on reopen, re-sync from `/recent` and discard lines at or below the last seen `seq` |
| Burst during a sync | queue-drain coalesces into one SSE frame; capacity bounds memory |
| Empty buffer (fresh start) | pane shows a quiet "no log lines yet" state |
| Very long line | CSS wraps/scrolls; stored data is not truncated |
| Capacity lowered live | deque rebuilt under lock; pane trims oldest |
| Multiple tabs | each is an independent subscriber (own queue) |
| JS disabled | server-rendered snapshot remains readable; no live updates |
| 302 to `/login` | unified redirect shared with HTMX/fetch |

### 8. Testing

- **Unit** `test/test_log_buffer.py`: emit then snapshot; deque cap eviction;
  `subscribe()` batching with **no dropped lines**; `seq` monotonicity;
  `set_capacity` resize; thread-safety under concurrent emit.
- **Routes** `test/test_routes_api_logs.py`: `/recent` JSON shape including
  `file_path`; SSE frame format; `@login_required` on both; verbosity PATCH
  round-trip.
- **Live-reload** (extend existing logging-reload tests): capacity and file
  settings actually reconfigure the handlers.
- **E2E** `test/e2e/test_logs_live.py` (Playwright, `-m e2e`): load `/logs`, emit
  a line, see it stream in, apply the level filter and search, pause/resume,
  change verbosity. Reuses the `live_server` fixture.

### 9. Files

**Create:**

- `blackvuesync/server/log_buffer.py`
- `blackvuesync/server/routes/api_logs.py`
- `blackvuesync/server/templates/logs.html`
- `blackvuesync/server/static/js/logs.js`
- `blackvuesync/server/static/css/logs.css`
- `test/test_log_buffer.py`
- `test/test_routes_api_logs.py`
- `test/e2e/test_logs_live.py`

**Modify:**

- `blackvuesync/__main__.py` -- attach + reconfigure the two handlers in serve.
- `blackvuesync/server/routes/ui.py` -- `/logs` renders the real page + snapshot.
- `blackvuesync/server/__init__.py` -- register `api_logs_bp` (CSP unchanged;
  `connect-src 'self'` already covers SSE).
- `docs/api.md` -- document `/api/logs/recent` and `/api/logs/stream`.
- `pyproject.toml` -- version bump; add new test modules to mypy overrides.

**Delete:**

- `blackvuesync/server/templates/_placeholders/logs.html`

### 10. Scope guards (YAGNI)

**OUT:** rotated-file/history browsing UI, in-app log download/export, a
dedicated per-module filter, server-side search, structured-field column view
(lines render as formatted text). These are deferred or covered by host-side
file access.

---

## Verification

1. `pip install -e ".[dev]"`, `python -m blackvuesync serve`, log in, open
   `/logs` -- recent lines render server-side; new lines stream in live.
2. Trigger a sync (`POST /api/sync/now`) and watch the lines appear in real time.
3. Apply the level filter and search; pause/resume auto-scroll; clear the view.
4. Change verbosity to Debug from the page; confirm DEBUG lines begin to appear.
5. Confirm `/config/logs/blackvuesync.log` exists on the host and rotates at the
   configured size; the displayed file path matches.
6. `pytest test/test_log_buffer.py test/test_routes_api_logs.py -v` and
   `pytest test/e2e/test_logs_live.py -m e2e -v` pass.
7. PR with all required checks green and **0 SonarCloud findings** (query the
   issues API directly, not just the gate).

---

## Self-review

- **Placeholders/TBDs:** none.
- **Internal consistency:** the serve-mode-only decision is reflected in both the
  architecture table and the wiring section; the verbosity control consistently
  reuses `PATCH /api/settings/logging` (no new endpoint) in the API and viewer
  sections; the `LogLine` shape defined in section 2 matches the `/recent` and
  SSE payloads in section 5.
- **Scope:** focused on one sub-project; the OUT list keeps file-history browsing
  and export from bleeding in.
- **Ambiguity:** the display-filter vs verbosity-control distinction is made
  explicit; "filter by module" is explicitly resolved to free-text search.

**End of design spec.**
