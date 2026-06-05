# Sub-Project #4 Log Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live `/logs` web viewer backed by a new in-memory ring-buffer log handler and a rotating file handler, both wired serve-side only.

**Architecture:** A `LogBuffer` (`logging.Handler` subclass) keeps the last N log records in RAM and fans new ones out to SSE subscribers as batches (don't-drop, unlike the latest-wins `ProgressPublisher`). A `RotatingFileHandler` writes durable logs to `/config/logs/`. Both attach to the root logger in `cmd_serve`; `sync.py` stays stdlib-only. New `/api/logs/recent` (snapshot) and `/api/logs/stream` (SSE) endpoints feed an `@alpinejs/csp` viewer that reuses the dashboard's `EventSource`+backoff lifecycle. Live verbosity reuses the existing `PATCH /api/settings/logging`.

**Tech Stack:** Python 3.9+ stdlib `logging`, Flask, Jinja2, vendored Alpine.js (csp build) + HTMX, pytest + Flask test client, pytest-playwright.

**Design:** `docs/plans/2026-06-05-sub-project-4-log-viewer-design.md`.

**Branch:** `sub-project-4-log-viewer` (already checked out; the design spec is committed there).

**Conventions (enforced):** comments lowercase/third-person/non-obvious; type annotations on all defs; `@alpinejs/csp` directives are bare property/method refs (no inline expressions); client log rows built with `textContent`, never `innerHTML`; stage commits by explicit path (never `git add -A`); never `--no-verify` (fix pre-commit failures and re-stage); commit titles <= 72 chars; squash-merge; target 0 SonarCloud findings.

---

## File structure

**Create:**

- `blackvuesync/server/log_buffer.py` -- `LogLine` dataclass + `LogBuffer(logging.Handler)` (deque, lock, subscriber fan-out, `snapshot`/`subscribe`/`set_capacity`).
- `blackvuesync/server/routes/api_logs.py` -- `api_logs_bp` blueprint: `GET /api/logs/recent`, `GET /api/logs/stream` (SSE), plus `verbosity_token()` helper.
- `blackvuesync/server/templates/logs.html` -- the viewer page (server-renders the initial snapshot).
- `blackvuesync/server/static/js/logs.js` -- `logsPage` Alpine csp component.
- `blackvuesync/server/static/css/logs.css` -- pane, rows, level colours, control states.
- `test/test_log_buffer.py` -- unit tests for `LogLine`/`LogBuffer`.
- `test/test_routes_api_logs.py` -- Flask test-client tests for the two endpoints + the `/logs` page.
- `test/e2e/test_logs_live.py` -- Playwright smoke (load, stream a line, filter, pause, clear).

**Modify:**

- `blackvuesync/server/__init__.py` -- `create_app` gains `log_buffer` + `log_file_path` params; registers `api_logs_bp`.
- `blackvuesync/__main__.py` -- `cmd_serve` builds + attaches the two handlers; new `_build_file_handler` and `_reconfigure_serve_logging` helpers; the reload listener resizes the buffer and rebuilds the file handler.
- `blackvuesync/server/routes/ui.py` -- `/logs` renders the real page + snapshot.
- `pyproject.toml` -- version bump; add new test modules to mypy overrides.
- `docs/api.md` -- document `/api/logs/recent` and `/api/logs/stream`.

**Delete:**

- `blackvuesync/server/templates/_placeholders/logs.html`

---

### Task 1: `LogBuffer` ring-buffer handler

**Files:**

- Create: `blackvuesync/server/log_buffer.py`
- Test: `test/test_log_buffer.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_log_buffer.py`:

```python
"""unit tests for the in-memory ring-buffer log handler."""

from __future__ import annotations

import logging
import threading
import time

from blackvuesync.server.log_buffer import LogBuffer, LogLine, verbosity_token


def _record(msg: str, level: int = logging.INFO, name: str = "test") -> logging.LogRecord:
    return logging.LogRecord(name, level, "path.py", 1, msg, None, None)


def test_emit_then_snapshot_returns_logline() -> None:
    buf = LogBuffer(capacity=10)
    buf.emit(_record("hello world", logging.WARNING, "blackvuesync"))
    lines = buf.snapshot()
    assert len(lines) == 1
    ln = lines[0]
    assert isinstance(ln, LogLine)
    assert ln.message == "hello world"
    assert ln.level == "WARNING"
    assert ln.level_no == logging.WARNING
    assert ln.logger == "blackvuesync"
    assert ln.seq == 1
    assert ln.ts.endswith("Z")


def test_deque_evicts_oldest_beyond_capacity() -> None:
    buf = LogBuffer(capacity=3)
    for i in range(5):
        buf.emit(_record(f"line {i}"))
    msgs = [ln.message for ln in buf.snapshot()]
    assert msgs == ["line 2", "line 3", "line 4"]


def test_seq_is_monotonic_and_matches_order() -> None:
    buf = LogBuffer(capacity=100)
    for i in range(10):
        buf.emit(_record(f"line {i}"))
    seqs = [ln.seq for ln in buf.snapshot()]
    assert seqs == list(range(1, 11))


def test_subscribe_yields_new_lines_in_batches_no_drops() -> None:
    buf = LogBuffer(capacity=100)
    gen = buf.subscribe()
    for i in range(3):
        buf.emit(_record(f"line {i}"))
    batch = next(gen)
    # all three queued lines arrive (possibly coalesced into one batch)
    collected = list(batch)
    while len(collected) < 3:
        collected += next(gen)
    assert [ln.message for ln in collected] == ["line 0", "line 1", "line 2"]
    gen.close()


def test_set_capacity_resizes_and_truncates() -> None:
    buf = LogBuffer(capacity=5)
    for i in range(5):
        buf.emit(_record(f"line {i}"))
    buf.set_capacity(2)
    assert [ln.message for ln in buf.snapshot()] == ["line 3", "line 4"]
    assert buf.capacity == 2


def test_emit_is_threadsafe_under_concurrency() -> None:
    buf = LogBuffer(capacity=10000)

    def worker() -> None:
        for i in range(500):
            buf.emit(_record(f"x{i}"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = buf.snapshot()
    assert len(snap) == 2000
    # seq values are unique and strictly increasing in storage order
    seqs = [ln.seq for ln in snap]
    assert len(set(seqs)) == 2000
    assert seqs == sorted(seqs)


def test_emit_never_raises_on_bad_format_args() -> None:
    buf = LogBuffer(capacity=10)
    bad = logging.LogRecord("t", logging.INFO, "p", 1, "%d and %d", (1,), None)
    buf.emit(bad)  # getMessage() would raise; emit must swallow via handleError
    # the buffer simply has no line (or a safe one); the call did not raise
    assert isinstance(buf.snapshot(), list)


def test_verbosity_token_maps_quiet_verbose() -> None:
    class _L:
        def __init__(self, quiet: bool, verbose: int) -> None:
            self.quiet = quiet
            self.verbose = verbose

    assert verbosity_token(_L(True, 0)) == "quiet"
    assert verbosity_token(_L(False, 0)) == "normal"
    assert verbosity_token(_L(False, 1)) == "verbose"
    assert verbosity_token(_L(False, 2)) == "debug"
    assert verbosity_token(_L(False, 5)) == "debug"


def test_subscribe_heartbeat_yields_empty_list_quickly() -> None:
    buf = LogBuffer(capacity=10)
    buf.HEARTBEAT_SECONDS = 0.05  # shrink for the test
    gen = buf.subscribe()
    start = time.monotonic()
    batch = next(gen)
    assert batch == []
    assert time.monotonic() - start < 1.0
    gen.close()
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_log_buffer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'blackvuesync.server.log_buffer'`.

- [ ] **Step 3: Implement `log_buffer.py`**

Create `blackvuesync/server/log_buffer.py`:

```python
"""in-memory ring-buffer logging handler for the live /logs viewer.

models ProgressPublisher's threading skeleton but with don't-drop delivery:
every log line matters, so subscribe() yields *batches of new lines* rather
than a latest-wins snapshot. serve-mode only; sync.py never imports this.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import logging
import queue
import threading
from collections import deque
from collections.abc import Iterator
from typing import ClassVar


@dataclasses.dataclass(frozen=True)
class LogLine:
    """immutable, json-serializable view of a single log record."""

    seq: int
    ts: str  # iso-8601 utc with a trailing Z
    level: str
    level_no: int
    logger: str
    message: str


def verbosity_token(logging_settings: object) -> str:
    """maps the logging settings (quiet/verbose) to a ui segmented-control token.

    quiet -> "quiet"; verbose 0 -> "normal"; 1 -> "verbose"; >=2 -> "debug".
    accepts any object exposing .quiet and .verbose (duck-typed to avoid a
    settings import here).
    """
    if getattr(logging_settings, "quiet", False):
        return "quiet"
    verbose = getattr(logging_settings, "verbose", 0)
    return {0: "normal", 1: "verbose"}.get(verbose, "debug")


class LogBuffer(logging.Handler):
    """thread-safe ring buffer of recent log lines with an SSE fan-out.

    attach to the root logger in serve mode. emit() stores a LogLine and
    offers it to every subscriber queue. subscribe() drains its queue in
    batches; a slow consumer that overflows its queue simply drops frames and
    re-syncs from snapshot() on its next reconnect.
    """

    HEARTBEAT_SECONDS: ClassVar[float] = 30.0
    _SUBSCRIBER_QUEUE_MAX: ClassVar[int] = 2048

    def __init__(self, capacity: int = 1000) -> None:
        super().__init__()
        self._capacity = max(1, capacity)
        self._lines: deque[LogLine] = deque(maxlen=self._capacity)
        self._lock = threading.RLock()
        self._subscribers: set[queue.Queue[LogLine]] = set()
        self._seq = 0

    @property
    def capacity(self) -> int:
        """returns the current ring-buffer capacity."""
        with self._lock:
            return self._capacity

    def emit(self, record: logging.LogRecord) -> None:
        """stores the record as a LogLine and fans it out to subscribers.

        never raises into the logging call site: a record whose getMessage()
        fails is routed to handleError and dropped.
        """
        try:
            message = record.getMessage()
            ts = (
                datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        except Exception:  # pylint: disable=broad-exception-caught
            self.handleError(record)
            return
        with self._lock:
            self._seq += 1
            line = LogLine(
                seq=self._seq,
                ts=ts,
                level=record.levelname,
                level_no=record.levelno,
                logger=record.name,
                message=message,
            )
            self._lines.append(line)
            for sub in list(self._subscribers):
                with contextlib.suppress(queue.Full):
                    sub.put_nowait(line)

    def snapshot(self) -> list[LogLine]:
        """returns a copy of the current ring contents, oldest first."""
        with self._lock:
            return list(self._lines)

    def set_capacity(self, capacity: int) -> None:
        """resizes the ring buffer in place, truncating to the newest lines."""
        capacity = max(1, capacity)
        with self._lock:
            if capacity == self._capacity:
                return
            self._capacity = capacity
            self._lines = deque(self._lines, maxlen=capacity)

    def subscribe(self) -> Iterator[list[LogLine]]:
        """yields batches of newly-emitted lines; an empty list is a heartbeat.

        blocks up to HEARTBEAT_SECONDS for the first line, then drains whatever
        else is queued so a burst coalesces into one batch.
        """
        q: queue.Queue[LogLine] = queue.Queue(maxsize=self._SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subscribers.add(q)
        try:
            while True:
                try:
                    first = q.get(timeout=self.HEARTBEAT_SECONDS)
                except queue.Empty:
                    yield []
                    continue
                batch = [first]
                while True:
                    try:
                        batch.append(q.get_nowait())
                    except queue.Empty:
                        break
                yield batch
        finally:
            with self._lock:
                self._subscribers.discard(q)


__all__ = ["LogLine", "LogBuffer", "verbosity_token"]
```

- [ ] **Step 4: Run to confirm pass**

Run: `venv/bin/pytest test/test_log_buffer.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/log_buffer.py test/test_log_buffer.py
git commit -m "feat: add in-memory LogBuffer ring-buffer log handler"
```

---

### Task 2: `/api/logs/*` endpoints + app wiring

**Files:**

- Create: `blackvuesync/server/routes/api_logs.py`
- Modify: `blackvuesync/server/__init__.py:17-31` (signature + attrs) and `:68-92` (register blueprint)
- Test: `test/test_routes_api_logs.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_api_logs.py`:

```python
"""flask test-client tests for /api/logs/* and the /logs page."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def app_and_client(tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth, username="admin", password_hash=hash_password("test-password-1234")
            ),
            system=dataclasses.replace(s.system, destination=str(destination)),
        )
    )
    app = create_app(store, testing=True, log_file_path="/config/logs/blackvuesync.log")
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = "admin"
        yield app, client


def _emit(app: Any, msg: str, level: int = logging.INFO, name: str = "blackvuesync") -> None:
    app.log_buffer.emit(logging.LogRecord(name, level, "p.py", 1, msg, None, None))


def test_recent_returns_buffered_lines_and_meta(app_and_client: Any) -> None:
    app, client = app_and_client
    _emit(app, "first line", logging.WARNING)
    resp = client.get("/api/logs/recent")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["file_path"] == "/config/logs/blackvuesync.log"
    assert data["capacity"] == app.log_buffer.capacity
    assert data["verbosity"] == "normal"
    assert data["lines"][-1]["message"] == "first line"
    assert data["lines"][-1]["level"] == "WARNING"


def test_recent_requires_login(app_and_client: Any) -> None:
    app, _client = app_and_client
    anon = app.test_client()
    resp = anon.get("/api/logs/recent")
    assert resp.status_code in (302, 401)


def test_stream_emits_sse_log_frames(app_and_client: Any) -> None:
    app, client = app_and_client
    _emit(app, "streamed line", logging.ERROR)
    resp = client.get("/api/logs/stream", buffered=False)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    assert resp.headers["X-Accel-Buffering"] == "no"
    assert resp.headers["Cache-Control"] == "no-store"
    chunk = next(resp.response)
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    assert text.startswith("event: logs\ndata: ")
    payload = json.loads(text.split("data: ", 1)[1].strip())
    assert payload["lines"][0]["message"] == "streamed line"
    resp.close()


def test_logs_page_renders_snapshot_server_side(app_and_client: Any) -> None:
    app, client = app_and_client
    _emit(app, "rendered-in-html", logging.INFO)
    resp = client.get("/logs")
    assert resp.status_code == 200
    assert b"rendered-in-html" in resp.data
    assert b"js/logs.js" in resp.data
    assert b"/config/logs/blackvuesync.log" in resp.data


def test_logs_page_escapes_message_text(app_and_client: Any) -> None:
    app, client = app_and_client
    _emit(app, "<script>alert(1)</script>", logging.INFO)
    resp = client.get("/logs")
    assert b"<script>alert(1)</script>" not in resp.data
    assert b"&lt;script&gt;" in resp.data
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_routes_api_logs.py -q`
Expected: FAIL -- `create_app() got an unexpected keyword argument 'log_file_path'` (and missing routes).

- [ ] **Step 3: Create `api_logs.py`**

Create `blackvuesync/server/routes/api_logs.py`:

```python
"""api logs routes: /api/logs/recent (snapshot) and /api/logs/stream (SSE)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator

from flask import Blueprint, Response, current_app, stream_with_context

from blackvuesync.server.auth import login_required
from blackvuesync.server.log_buffer import LogBuffer, verbosity_token

api_logs_bp = Blueprint("api_logs_bp", __name__, url_prefix="/api/logs")

_MIME_JSON = "application/json"


def _buffer() -> LogBuffer:
    """returns the app-level log buffer."""
    buf: LogBuffer = current_app.log_buffer  # type: ignore[attr-defined]
    return buf


def _current_verbosity() -> str:
    """returns the current verbosity token from the logging settings."""
    store = current_app.settings_store  # type: ignore[attr-defined]
    return verbosity_token(store.get().logging)


@api_logs_bp.route("/recent", methods=["GET"])
@login_required
def recent() -> Response:
    """returns the buffered log lines plus viewer metadata as JSON."""
    buf = _buffer()
    body = json.dumps(
        {
            "lines": [dataclasses.asdict(ln) for ln in buf.snapshot()],
            "file_path": current_app.log_file_path or "",  # type: ignore[attr-defined]
            "capacity": buf.capacity,
            "verbosity": _current_verbosity(),
        }
    )
    return Response(body, status=200, mimetype=_MIME_JSON)


@api_logs_bp.route("/stream", methods=["GET"])
@login_required
def stream() -> Response:
    """streams new log lines as Server-Sent Events.

    emits event: logs\\ndata: {"lines":[...]}\\n\\n per batch; a ": keepalive"
    comment every HEARTBEAT_SECONDS when no lines arrive.
    """
    buf = _buffer()

    def _sse_events() -> Iterator[bytes]:
        for batch in buf.subscribe():
            if not batch:
                yield b": keepalive\n\n"
            else:
                payload = json.dumps(
                    {"lines": [dataclasses.asdict(ln) for ln in batch]}
                )
                yield f"event: logs\ndata: {payload}\n\n".encode()

    resp = Response(stream_with_context(_sse_events()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Transfer-Encoding"] = "chunked"
    return resp


__all__ = ["api_logs_bp"]
```

- [ ] **Step 4: Wire into `create_app`**

In `blackvuesync/server/__init__.py`, add the import for `LogBuffer` near the top with the other server imports (after line 13 `from blackvuesync.server.progress import ProgressPublisher`):

```python
from blackvuesync.server.log_buffer import LogBuffer
```

Replace the `create_app` signature (lines 17-21) with:

```python
def create_app(  # pylint: disable=too-many-locals
    settings_store: SettingsStore,
    testing: bool = False,
    progress_publisher: Optional[ProgressPublisher] = None,
    log_buffer: Optional[LogBuffer] = None,
    log_file_path: Optional[str] = None,
) -> Flask:
```

After the `app.progress_publisher = ...` line (line 31), add:

```python
    # attaches or creates the log buffer; defaults to a new instance so route
    # tests have a live buffer even when serve mode did not supply one.
    app.log_buffer = log_buffer or LogBuffer()  # type: ignore[attr-defined]
    # absolute path of the rotating log file for the viewer to display; None
    # (rendered as "") when no file handler is configured (e.g. in tests).
    app.log_file_path = log_file_path  # type: ignore[attr-defined]
```

Add the blueprint import in the deferred import block (after line 74 `from blackvuesync.server.routes.api_sync import api_sync_bp`):

```python
    from blackvuesync.server.routes.api_logs import api_logs_bp
```

Register it (after line 92 `app.register_blueprint(api_sync_bp)`):

```python
    app.register_blueprint(api_logs_bp)
```

- [ ] **Step 5: Run to confirm pass**

Run: `venv/bin/pytest test/test_routes_api_logs.py -q`
Expected: the API tests PASS. `test_logs_page_*` still FAIL (the `/logs` route is still the placeholder -- fixed in Task 4). Confirm the three `test_recent_*` / `test_stream_*` pass:

Run: `venv/bin/pytest test/test_routes_api_logs.py -q -k "recent or stream or requires_login"`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/api_logs.py blackvuesync/server/__init__.py test/test_routes_api_logs.py
git commit -m "feat: add /api/logs/recent and /api/logs/stream endpoints"
```

---

### Task 3: serve-mode handler wiring + live reload

**Files:**

- Modify: `blackvuesync/__main__.py` (add imports; `_build_file_handler`, `_reconfigure_serve_logging`; wire into `cmd_serve:443-490`)
- Test: `test/test_log_buffer.py` (append serve-wiring tests -- they exercise the pure helpers without starting waitress)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_log_buffer.py`:

```python
def test_build_file_handler_creates_logs_dir(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from blackvuesync.__main__ import _build_file_handler

    class _L:
        format = "text"
        file_max_bytes = 1024
        file_backup_count = 2

    log_dir = tmp_path / "logs"
    handler = _build_file_handler(_L(), log_dir)
    try:
        assert log_dir.is_dir()
        assert handler.baseFilename == str(log_dir / "blackvuesync.log")
        assert handler.maxBytes == 1024
        assert handler.backupCount == 2
    finally:
        handler.close()


def test_reconfigure_serve_logging_resizes_buffer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import dataclasses as _dc
    import os as _os
    from unittest.mock import patch as _patch

    from blackvuesync.__main__ import _build_file_handler, _reconfigure_serve_logging
    from blackvuesync.settings import SettingsStore

    with _patch.dict(_os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    old = store.get()
    new = _dc.replace(
        old, logging=_dc.replace(old.logging, ring_buffer_capacity=7)
    )
    buf = LogBuffer(capacity=1000)
    log_dir = tmp_path / "logs"
    box = [_build_file_handler(old.logging, log_dir)]
    try:
        _reconfigure_serve_logging(old, new, buf, box, log_dir)
        assert buf.capacity == 7
    finally:
        box[0].close()


def test_reconfigure_serve_logging_swaps_file_handler(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import dataclasses as _dc
    import logging as _logging
    import os as _os
    from unittest.mock import patch as _patch

    from blackvuesync.__main__ import _build_file_handler, _reconfigure_serve_logging
    from blackvuesync.settings import SettingsStore

    with _patch.dict(_os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    old = store.get()
    new = _dc.replace(
        old, logging=_dc.replace(old.logging, file_backup_count=9)
    )
    buf = LogBuffer(capacity=10)
    log_dir = tmp_path / "logs"
    first = _build_file_handler(old.logging, log_dir)
    box = [first]
    root = _logging.getLogger()
    root.addHandler(first)
    try:
        _reconfigure_serve_logging(old, new, buf, box, log_dir)
        assert box[0] is not first
        assert box[0].backupCount == 9
        assert first not in root.handlers
        assert box[0] in root.handlers
    finally:
        root.removeHandler(box[0])
        box[0].close()
        first.close()
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_log_buffer.py -q -k "build_file_handler or reconfigure"`
Expected: FAIL -- `ImportError: cannot import name '_build_file_handler' from 'blackvuesync.__main__'`.

- [ ] **Step 3: Add the helpers and wire `cmd_serve`**

In `blackvuesync/__main__.py`, add these imports near the top (with the other stdlib imports):

```python
import logging
from logging.handlers import RotatingFileHandler
```

Add these two module-level functions just after `_register_logging_reload` (after line 290):

```python
def _build_file_handler(
    logging_settings: LoggingSettings, log_dir: Path
) -> RotatingFileHandler:
    """creates the rotating file handler under log_dir, making the dir if absent.

    serve mode only; the file survives restarts and is browsed on the host.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        log_dir.chmod(0o700)
    return RotatingFileHandler(
        str(log_dir / "blackvuesync.log"),
        maxBytes=logging_settings.file_max_bytes,
        backupCount=logging_settings.file_backup_count,
        encoding="utf-8",
    )


def _reconfigure_serve_logging(
    old: Settings,
    new: Settings,
    log_buffer: "LogBuffer",
    file_handler_box: "list[RotatingFileHandler]",
    log_dir: Path,
) -> None:
    """re-applies serve-only logging handlers when the logging section changes.

    resizes the ring buffer and rebuilds the rotating file handler in place,
    then re-applies format and level to every handler (including the new one).
    file_handler_box is a single-element holder so the swapped handler is
    visible to the caller across invocations.
    """
    if new.logging == old.logging:
        return
    if new.logging.ring_buffer_capacity != old.logging.ring_buffer_capacity:
        log_buffer.set_capacity(new.logging.ring_buffer_capacity)
    if (
        new.logging.file_max_bytes != old.logging.file_max_bytes
        or new.logging.file_backup_count != old.logging.file_backup_count
    ):
        root = logging.getLogger()
        old_handler = file_handler_box[0]
        root.removeHandler(old_handler)
        old_handler.close()
        new_handler = _build_file_handler(new.logging, log_dir)
        root.addHandler(new_handler)
        file_handler_box[0] = new_handler
    _apply_logging_settings(new.logging)
```

Add a `LogBuffer` type-only import so the annotations resolve; add this under the existing `if TYPE_CHECKING:` block, or add such a block near the top if none exists:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackvuesync.server.log_buffer import LogBuffer
```

In `cmd_serve`, add `LogBuffer` to the deferred server import block (after line 458 `from blackvuesync.server.scheduler import init_scheduler`):

```python
    from blackvuesync.server.log_buffer import LogBuffer
```

Replace the wiring block in `cmd_serve` (current lines 472-478, from `_apply_logging_settings(settings.logging)` through `_register_logging_reload(store)`) with:

```python
    _apply_logging_settings(settings.logging)

    # serve-only durable + live log sinks (sync.py stays stdlib-only / no file).
    root_logger = logging.getLogger()
    log_buffer = LogBuffer(capacity=settings.logging.ring_buffer_capacity)
    root_logger.addHandler(log_buffer)
    log_dir = config_path.parent / "logs"
    file_handler = _build_file_handler(settings.logging, log_dir)
    root_logger.addHandler(file_handler)
    log_file_path = str(log_dir / "blackvuesync.log")
    # re-applies the formatter to the two handlers just attached.
    configure_logging(settings.logging.format)

    publisher = ProgressPublisher()
    app = create_app(
        store,
        progress_publisher=publisher,
        log_buffer=log_buffer,
        log_file_path=log_file_path,
    )
    port = args.port if args.port is not None else settings.web.port

    scheduler = init_scheduler(store, publisher)
    _register_logging_reload(store)
    # second listener: resizes the ring buffer / rebuilds the file handler live.
    file_handler_box = [file_handler]
    store.on_change(
        lambda old, new: _reconfigure_serve_logging(
            old, new, log_buffer, file_handler_box, log_dir
        )
    )
```

> Note: `_register_logging_reload(store)` still handles format/level (unchanged, keeps its existing test green). The added `store.on_change(...)` listener handles the new buffer/file reconfiguration. Both fire on every settings change; the format/level path runs in both but is idempotent.

- [ ] **Step 4: Run to confirm pass**

Run: `venv/bin/pytest test/test_log_buffer.py -q`
Expected: PASS (12 tests).

Run: `venv/bin/pytest test/ -q -k "logging or main or serve"`
Expected: PASS (no regression in existing logging-reload / `__main__` tests).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/__main__.py test/test_log_buffer.py
git commit -m "feat: wire LogBuffer and rotating file handler into serve mode"
```

---

### Task 4: `/logs` page route + template

**Files:**

- Modify: `blackvuesync/server/routes/ui.py:1-15` (imports) and `:87-95` (the `logs` view)
- Create: `blackvuesync/server/templates/logs.html`
- Delete: `blackvuesync/server/templates/_placeholders/logs.html`
- Test: `test/test_routes_api_logs.py` (the `test_logs_page_*` tests from Task 2)

- [ ] **Step 1: Confirm the page tests currently fail**

Run: `venv/bin/pytest test/test_routes_api_logs.py -q -k "logs_page"`
Expected: FAIL (`js/logs.js` not in the placeholder; message not rendered).

- [ ] **Step 2: Update the `/logs` route**

In `blackvuesync/server/routes/ui.py`, add `dataclasses` to the imports (top of file):

```python
import dataclasses
```

and add this import alongside the other route-helper imports (after line 15 `from blackvuesync.server.settings_form import build_sections`):

```python
from blackvuesync.server.routes.api_logs import verbosity_token  # noqa: F401
```

Wait -- `verbosity_token` lives in `log_buffer.py`, not `api_logs.py`. Import it from `log_buffer`:

```python
from blackvuesync.server.log_buffer import verbosity_token
```

Replace the `logs()` view (lines 87-95) with:

```python
@bp.route("/logs", methods=["GET"])
@login_required
def logs() -> str:
    """renders the live log viewer, server-painting the current buffer snapshot."""
    buf = current_app.log_buffer  # type: ignore[attr-defined]
    logging_settings = current_app.settings_store.get().logging  # type: ignore[attr-defined]
    return render_template(
        "logs.html",
        version=__version__,
        page="logs",
        lines=[dataclasses.asdict(ln) for ln in buf.snapshot()],
        log_file_path=current_app.log_file_path or "",  # type: ignore[attr-defined]
        capacity=buf.capacity,
        verbosity=verbosity_token(logging_settings),
    )
```

- [ ] **Step 3: Create `logs.html`**

Create `blackvuesync/server/templates/logs.html`:

```html
{% extends "base.html" %}
{% block title %}Logs -- BlackVue Sync{% endblock %}
{% block footer_version %}{{ version }}{% endblock %}

{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/logs.css') }}">
{% endblock %}

{% block content %}
<div class="logs-page" x-data="logsPage"
     data-verbosity="{{ verbosity }}" data-capacity="{{ capacity }}">
  <div class="logs-toolbar">
    <div class="logs-levels" role="group" aria-label="Minimum level">
      <button type="button" class="logs-level-btn active" data-level="DEBUG" @click="setLevel">Debug</button>
      <button type="button" class="logs-level-btn" data-level="INFO" @click="setLevel">Info</button>
      <button type="button" class="logs-level-btn" data-level="WARNING" @click="setLevel">Warning</button>
      <button type="button" class="logs-level-btn" data-level="ERROR" @click="setLevel">Error</button>
    </div>
    <input type="search" class="logs-search" placeholder="Filter visible lines"
           aria-label="Filter log lines" @input="onSearch">
    <div class="logs-verbosity" role="group" aria-label="Capture verbosity">
      <button type="button" class="logs-verb-btn" data-verbosity="quiet" @click="setVerbosity">Quiet</button>
      <button type="button" class="logs-verb-btn" data-verbosity="normal" @click="setVerbosity">Normal</button>
      <button type="button" class="logs-verb-btn" data-verbosity="verbose" @click="setVerbosity">Verbose</button>
      <button type="button" class="logs-verb-btn" data-verbosity="debug" @click="setVerbosity">Debug</button>
    </div>
    <button type="button" class="button button-secondary button-sm" @click="togglePause" x-text="pauseLabel"></button>
    <button type="button" class="button button-secondary button-sm" @click="clearView">Clear</button>
  </div>

  <div class="logs-pane" data-pane>
    {% for ln in lines %}
    <div class="log-row" data-level="{{ ln.level }}" data-level-no="{{ ln.level_no }}">
      <span class="log-ts">{{ ln.ts }}</span>
      <span class="log-level log-level-{{ ln.level }}">{{ ln.level }}</span>
      <span class="log-logger">{{ ln.logger }}</span>
      <span class="log-msg">{{ ln.message }}</span>
    </div>
    {% endfor %}
  </div>
  <p class="logs-empty" data-empty {% if lines %}hidden{% endif %}>No log lines yet.</p>

  <p class="logs-filepath">
    Log file on host: <code>{{ log_file_path or "stdout only (file logging disabled)" }}</code>
  </p>
  <noscript>
    <p class="logs-note">JavaScript is required for live streaming; showing the most recent buffered lines.</p>
  </noscript>
</div>
{% endblock %}

{% block extra_js %}
  <script src="{{ url_for('static', filename='js/logs.js') }}" defer></script>
{% endblock %}
```

> Jinja2 autoescaping makes `{{ ln.message }}` safe server-side (the `test_logs_page_escapes_message_text` test asserts this). The client path uses `textContent` (Task 5).

- [ ] **Step 4: Delete the placeholder**

```bash
git rm blackvuesync/server/templates/_placeholders/logs.html
```

- [ ] **Step 5: Run to confirm pass**

Run: `venv/bin/pytest test/test_routes_api_logs.py -q`
Expected: PASS (all tests, including `test_logs_page_*`).

Run: `venv/bin/pytest test/test_routes_ui.py -q`
Expected: PASS (the existing `/logs returns 200` parametrized test still passes against the real page).

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/logs.html
git commit -m "feat: render the real /logs viewer page with server-side snapshot"
```

---

### Task 5: viewer client (JS + CSS)

**Files:**

- Create: `blackvuesync/server/static/js/logs.js`
- Create: `blackvuesync/server/static/css/logs.css`

(Browser behaviour is verified by the Task 6 e2e smoke; there is no unit test for the JS.)

- [ ] **Step 1: Create `logs.js`**

Create `blackvuesync/server/static/js/logs.js`:

```javascript
// logs.js: Alpine.js (csp build) component for the live /logs viewer. owns the
// SSE connection to /api/logs/stream, client-side level + text filtering, tail
// ergonomics (pause auto-scroll, clear), and the live verbosity control. log
// rows are built with textContent only -- never innerHTML -- because log
// messages are untrusted text.

const SSE_BACKOFF_START_MS = 2000;
const SSE_BACKOFF_MAX_MS = 30000;
const LEVEL_ORDER = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.content : "";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("logsPage", () => ({
    minLevelNo: 0,
    query: "",
    paused: false,
    verbosity: "normal",
    capacity: 1000,
    _source: null,
    _backoffMs: SSE_BACKOFF_START_MS,
    _reconnectTimer: null,
    _pane: null,

    get pauseLabel() {
      return this.paused ? "Resume" : "Pause";
    },

    init() {
      this.verbosity = this.$el.dataset.verbosity || "normal";
      const cap = parseInt(this.$el.dataset.capacity || "1000", 10);
      this.capacity = Number.isFinite(cap) && cap > 0 ? cap : 1000;
      this._pane = this.$el.querySelector("[data-pane]");
      this._highlightVerbosity();
      this.scrollToEnd();
      this.openStream();
    },

    // --- controls ---
    setLevel(ev) {
      const lvl = ev.currentTarget.dataset.level;
      this.minLevelNo = LEVEL_ORDER[lvl] || 0;
      this.$root.querySelectorAll("[data-level]").forEach((b) => {
        b.classList.toggle("active", b.dataset.level === lvl);
      });
      this.applyFilters();
    },

    onSearch(ev) {
      this.query = (ev.currentTarget.value || "").toLowerCase();
      this.applyFilters();
    },

    togglePause() {
      this.paused = !this.paused;
      if (!this.paused) this.scrollToEnd();
    },

    clearView() {
      while (this._pane.firstChild) this._pane.removeChild(this._pane.firstChild);
      this.updateEmpty();
    },

    async setVerbosity(ev) {
      const v = ev.currentTarget.dataset.verbosity;
      const body =
        v === "quiet"
          ? { quiet: true, verbose: 0 }
          : v === "normal"
            ? { quiet: false, verbose: 0 }
            : v === "verbose"
              ? { quiet: false, verbose: 1 }
              : { quiet: false, verbose: 2 };
      const resp = await this.send("/api/settings/logging", body, "PATCH");
      if (resp && resp.ok) {
        this.verbosity = v;
        this._highlightVerbosity();
      }
    },

    // --- rendering ---
    appendLines(lines) {
      const atEnd = this.isScrolledToEnd();
      const frag = document.createDocumentFragment();
      lines.forEach((ln) => frag.appendChild(this.buildRow(ln)));
      this._pane.appendChild(frag);
      this.trimToCapacity();
      this.updateEmpty();
      if (!this.paused && atEnd) this.scrollToEnd();
    },

    buildRow(ln) {
      const row = document.createElement("div");
      row.className = "log-row";
      row.dataset.level = ln.level;
      row.dataset.levelNo = String(ln.level_no);
      const ts = document.createElement("span");
      ts.className = "log-ts";
      ts.textContent = ln.ts;
      const lvl = document.createElement("span");
      lvl.className = "log-level log-level-" + ln.level;
      lvl.textContent = ln.level;
      const lg = document.createElement("span");
      lg.className = "log-logger";
      lg.textContent = ln.logger;
      const msg = document.createElement("span");
      msg.className = "log-msg";
      msg.textContent = ln.message;
      row.append(ts, lvl, lg, msg);
      this.applyRowVisibility(row);
      return row;
    },

    applyRowVisibility(row) {
      const levelNo = parseInt(row.dataset.levelNo || "0", 10);
      const text = row.textContent.toLowerCase();
      const visible =
        levelNo >= this.minLevelNo && (!this.query || text.includes(this.query));
      row.classList.toggle("hidden", !visible);
    },

    applyFilters() {
      this._pane.querySelectorAll(".log-row").forEach((r) => this.applyRowVisibility(r));
    },

    trimToCapacity() {
      let extra = this._pane.childElementCount - this.capacity;
      while (extra-- > 0 && this._pane.firstChild) {
        this._pane.removeChild(this._pane.firstChild);
      }
    },

    updateEmpty() {
      const empty = this.$root.querySelector("[data-empty]");
      if (empty) empty.hidden = this._pane.childElementCount > 0;
    },

    _highlightVerbosity() {
      this.$root.querySelectorAll("[data-verbosity]").forEach((b) => {
        if (b.dataset.verbosity) {
          b.classList.toggle("active", b.dataset.verbosity === this.verbosity);
        }
      });
    },

    isScrolledToEnd() {
      const p = this._pane;
      return p.scrollHeight - p.scrollTop - p.clientHeight < 40;
    },

    scrollToEnd() {
      this._pane.scrollTop = this._pane.scrollHeight;
    },

    // --- SSE lifecycle (mirrors dashboard.js) ---
    openStream() {
      if (this._source) return;
      const es = new EventSource("/api/logs/stream");
      this._source = es;
      es.addEventListener("logs", (ev) => {
        this._backoffMs = SSE_BACKOFF_START_MS;
        let data;
        try {
          data = JSON.parse(ev.data);
        } catch {
          /* malformed frame; the next event recovers */
          return;
        }
        if (data && Array.isArray(data.lines)) this.appendLines(data.lines);
      });
      es.onerror = () => {
        this.closeStream();
        this._reconnectTimer = setTimeout(
          this.scheduleReconnect.bind(this),
          this._backoffMs
        );
        this._backoffMs = Math.min(this._backoffMs * 2, SSE_BACKOFF_MAX_MS);
      };
    },

    scheduleReconnect() {
      this.openStream();
    },

    closeStream() {
      if (this._source) {
        this._source.close();
        this._source = null;
      }
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
    },

    async send(path, body, method) {
      try {
        return await fetch(path, {
          method: method,
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
          body: JSON.stringify(body),
        });
      } catch {
        /* network error; caller guards against null */
        return null;
      }
    },
  }));
});
```

- [ ] **Step 2: Create `logs.css`**

Create `blackvuesync/server/static/css/logs.css`:

```css
.logs-page {
  max-width: 1100px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-4);
}

.logs-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.logs-levels,
.logs-verbosity {
  display: inline-flex;
  gap: var(--space-1);
}

.logs-level-btn,
.logs-verb-btn {
  border: 1px solid var(--color-separator, #d1d1d6);
  background: var(--color-surface, #fff);
  color: var(--color-label, #1d1d1f);
  border-radius: var(--radius-md, 8px);
  padding: 4px 10px;
  font-size: 13px;
  cursor: pointer;
}

.logs-level-btn.active,
.logs-verb-btn.active {
  background: var(--color-accent, #0071e3);
  color: #fff;
  border-color: var(--color-accent, #0071e3);
}

.logs-search {
  flex: 1 1 200px;
  min-width: 160px;
  padding: 5px 10px;
  border: 1px solid var(--color-separator, #d1d1d6);
  border-radius: var(--radius-md, 8px);
  font-size: 13px;
}

.logs-pane {
  height: 60vh;
  overflow-y: auto;
  background: var(--color-surface, #1d1d1f);
  color: var(--color-label-inverse, #f5f5f7);
  border-radius: var(--radius-lg, 12px);
  padding: var(--space-3);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}

.log-row {
  display: grid;
  grid-template-columns: 200px 72px 160px 1fr;
  gap: var(--space-2);
  white-space: pre-wrap;
  word-break: break-word;
  padding: 1px 0;
}

.log-row.hidden {
  display: none;
}

.log-ts {
  color: #98989d;
}

.log-level {
  font-weight: 600;
}

.log-level-DEBUG {
  color: #8e8e93;
}
.log-level-INFO {
  color: #64d2ff;
}
.log-level-WARNING {
  color: #ffd60a;
}
.log-level-ERROR,
.log-level-CRITICAL {
  color: #ff453a;
}

.log-logger {
  color: #aeaeb2;
}

.logs-empty {
  color: var(--color-secondary-label, #6e6e73);
  padding: var(--space-3);
}

.logs-filepath {
  margin-top: var(--space-3);
  color: var(--color-secondary-label, #6e6e73);
  font-size: 13px;
}

.logs-note {
  color: var(--color-secondary-label, #6e6e73);
}
```

- [ ] **Step 3: Manual smoke (optional, recommended)**

Run: `venv/bin/python -m blackvuesync serve --config-path /tmp/bvs-smoke.json` (Ctrl-C to stop), open `http://localhost:8080/logs` after logging in, trigger a sync, and confirm lines stream in, the level buttons filter, search filters, Pause freezes auto-scroll, Clear empties the pane. (This step is not gated by CI.)

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/server/static/js/logs.js blackvuesync/server/static/css/logs.css
git commit -m "feat: add live log viewer client (logs.js + logs.css)"
```

---

### Task 6: end-to-end Playwright smoke

**Files:**

- Create: `test/e2e/test_logs_live.py`

- [ ] **Step 1: Write the e2e test**

Create `test/e2e/test_logs_live.py`:

```python
"""playwright smoke for the live /logs viewer."""

from __future__ import annotations

import logging

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')
    expect(page).not_to_have_url(f"{base_url}/login")


def test_logs_stream_filter_pause_clear(live_server: object, page: Page) -> None:  # type: ignore[valid-type]
    base = live_server.url  # type: ignore[attr-defined]
    _login(page, base)
    page.goto(f"{base}/logs")
    expect(page.locator(".logs-page")).to_be_visible()

    # inject a log line via the same buffer instance the app streams from
    live_server.app.log_buffer.emit(  # type: ignore[attr-defined]
        logging.LogRecord("blackvuesync", logging.ERROR, "p.py", 1, "boom-token-xyz", None, None)
    )
    row = page.locator(".log-row", has_text="boom-token-xyz")
    expect(row).to_be_visible(timeout=5000)

    # min-level filter: hide ERROR by requiring CRITICAL-only is not offered,
    # so instead verify a DEBUG line is hidden when the WARNING filter is active.
    live_server.app.log_buffer.emit(  # type: ignore[attr-defined]
        logging.LogRecord("blackvuesync", logging.DEBUG, "p.py", 1, "quiet-debug-line", None, None)
    )
    debug_row = page.locator(".log-row", has_text="quiet-debug-line")
    expect(debug_row).to_be_visible(timeout=5000)
    page.click('.logs-level-btn[data-level="WARNING"]')
    expect(debug_row).to_be_hidden()
    expect(row).to_be_visible()  # ERROR still shown under the WARNING floor

    # search filter narrows visible rows
    page.click('.logs-level-btn[data-level="DEBUG"]')  # reset floor
    page.fill(".logs-search", "boom-token")
    expect(row).to_be_visible()
    expect(debug_row).to_be_hidden()

    # clear empties the pane
    page.fill(".logs-search", "")
    page.click("text=Clear")
    expect(page.locator(".log-row")).to_have_count(0)
```

- [ ] **Step 2: Run the e2e test**

Run: `venv/bin/pytest test/e2e/test_logs_live.py -m e2e -v`
Expected: PASS. (Requires `playwright install chromium`; CI's `e2e-tests` job runs `playwright install --with-deps chromium` first.)

- [ ] **Step 3: Commit**

```bash
git add test/e2e/test_logs_live.py
git commit -m "test: add e2e smoke for the live log viewer"
```

---

### Task 7: docs, version, mypy overrides, full verification

**Files:**

- Modify: `docs/api.md`
- Modify: `pyproject.toml`

- [ ] **Step 1: Document the endpoints**

In `docs/api.md`, under the sync/HTMX sections, add a logs section:

```markdown
## Logs API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/logs/recent` | JSON snapshot: `{lines, file_path, capacity, verbosity}` |
| GET | `/api/logs/stream` | SSE stream of new log lines (`event: logs`) |

Both require login. The stream emits `event: logs\ndata: {"lines":[...]}\n\n`
per batch and a `: keepalive` comment when idle. Each line is
`{seq, ts, level, level_no, logger, message}`. The viewer changes capture
verbosity through the existing `PATCH /api/settings/logging`; there is no
dedicated verbosity endpoint.
```

- [ ] **Step 2: Bump version + mypy overrides**

Open `pyproject.toml`. Bump the `version` field to the next alpha for sub-project #4 (e.g. if it currently reads `2.5.0a0`, set `2.6.0a0`; if it already advanced, increment the alpha). Then, following the existing `[[tool.mypy.overrides]]` pattern used for other `test/test_*` modules, add `test.test_log_buffer` and `test.test_routes_api_logs` to the module list (the e2e dir is already excluded via the pre-commit mypy `exclude: ^test/e2e/`). Mirror exactly how the prior test modules are listed.

- [ ] **Step 3: Full local verification**

```bash
venv/bin/pytest test/ -q -m 'not e2e'
```

Expected: PASS (full unit + route suite; default deselects e2e).

```bash
venv/bin/pytest test/e2e/test_logs_live.py -m e2e -q
```

Expected: PASS.

```bash
pre-commit run --all-files
```

Expected: PASS (Black/ruff/mypy/markdownlint etc.). If a hook reformats a file, re-stage the changed files and re-run; never use `--no-verify`.

- [ ] **Step 4: Commit**

```bash
git add docs/api.md pyproject.toml
git commit -m "docs: document logs API; bump version and mypy overrides"
```

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin sub-project-4-log-viewer
```

Open a PR to `main`. Wait for the 5 required checks (pre-commit, unit-tests, integration-tests, test, SonarCloud Code Analysis) plus the `e2e-tests` job. After CI, query the SonarCloud issues API directly and require 0 findings before merging (do not trust the green gate alone):

```bash
curl -s "https://sonarcloud.io/api/issues/search?componentKeys=tekgnosis-net_blackvuesync&pullRequest=<N>&resolved=false&ps=100"
```

Merge via squash (linear history).

---

## Self-Review

**1. Spec coverage:**

- Two sinks (ring buffer + rotating file), serve-only → Tasks 1, 3.
- `LogBuffer` don't-drop `subscribe()` + `snapshot()` + `set_capacity()` + `LogLine` → Task 1.
- Live reload makes `ring_buffer_capacity` / `file_max_bytes` / `file_backup_count` real → Task 3.
- `/api/logs/recent` + `/api/logs/stream` (SSE, keepalive, headers) → Task 2.
- Viewer with all five controls (min-level filter, live verbosity via existing PATCH, search, pause/clear, file-path display) → Tasks 4 (template) + 5 (js).
- Server-rendered snapshot / graceful degradation + `<noscript>` → Task 4.
- `textContent` not `innerHTML`; Jinja autoescape → Tasks 5 + 4 (`test_logs_page_escapes_message_text`).
- Tests: `test_log_buffer.py`, `test_routes_api_logs.py`, `e2e/test_logs_live.py` → Tasks 1/3, 2/4, 6.
- Delete placeholder; docs; version; mypy → Tasks 4, 7.
- CSP unchanged (`connect-src 'self'` already covers SSE) → confirmed in `__init__.py`; no task needed.

**2. Placeholder scan:** none -- every code step contains complete code; commands have expected output.

**3. Type/name consistency:** `LogLine` fields (`seq, ts, level, level_no, logger, message`) are identical across `log_buffer.py`, the `/recent` + SSE payloads, the template loop, and `logs.js` `buildRow`. `verbosity_token` is defined once in `log_buffer.py` and imported by `api_logs.py` and `ui.py`. `app.log_buffer` / `app.log_file_path` are attached in `create_app` (Task 2) and read in `api_logs.py` + `ui.py`. `_build_file_handler` / `_reconfigure_serve_logging` signatures match between Task 3's helper definitions, the `cmd_serve` wiring, and the tests. The SSE event name `logs` matches between `api_logs.py` (`event: logs`) and `logs.js` (`addEventListener("logs", ...)`).

**End of plan.**
