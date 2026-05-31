# Resumable Downloads + `download_file` Decomposition -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Resume interrupted dashcam downloads via HTTP range requests with a safe full-restart fallback, keep resumable partials across runs while pruning orphans, and decompose `download_file` / `download_recording` to clear S3776.

**Architecture:** See `docs/plans/2026-06-01-download-resume-design.md`. The resume `GET` is itself the capability probe (no extra round-trip): send `Range: bytes=N-` only when a partial exists, then branch on the response (`206` → append, `200` → truncate, `416` → discard+restart).

**Tech Stack:** Python 3.9+ stdlib only in `sync.py` (`urllib`, `os`, `re`); pytest with an in-process `http.server` harness; Behave against the Flask mock dashcam.

**Critical compatibility note:** read the HTTP status with `response.getcode()`, **not** `response.status` -- the existing `FakeResponse` double in `test_download_file_streams_response_in_chunks` (`test/blackvuesync_test.py:790`) implements `getcode()` only.

---

## File structure

- `blackvuesync/sync.py` -- all production changes (helpers, resume, cleanup, sync wiring).
- `test/test_sync_resume.py` (new) -- resume unit tests with a range-aware in-process server.
- `test/test_clean_destination.py` (new) -- cleanup + orphan-prune unit tests.
- `features/sync_resume.feature` (new) -- end-to-end resume scenario.
- `pyproject.toml` -- version bump + mypy override entries.
- `docs/api.md` -- one-line note on resume behavior.

---

### Task 1: Make `download_recording` data-driven (pure refactor, no behavior change)

**Files:**

- Modify: `blackvuesync/sync.py` (`download_recording`, ~687-831)
- Test: existing `test/test_sync_callback.py`, `test/blackvuesync_test.py` (must stay green)

Collapse the four near-identical artifact blocks (mp4 + thm/3gf/gps with their
`skip_metadata` guards) into one loop driven by a table. Preserve every log
message, the `any_downloaded` accumulation, the `speed_bps` captured from the
mp4 download, and the final recording-level log.

- [ ] **Step 1: Run the existing suite to capture the green baseline**

Run: `venv/bin/pytest test/test_sync_callback.py test/blackvuesync_test.py -q`
Expected: PASS.

- [ ] **Step 2: Refactor `download_recording` to the table form**

```python
# (artifact_type, skip_key, builds the artifact filename for a recording)
_ARTIFACTS: list[tuple[str, str | None, Callable[[Recording], str]]] = [
    ("mp4", None, lambda r: r.filename),
    ("thm", "t", lambda r: f"{r.base_filename}_{r.type}{r.direction}.thm"),
    ("3gf", "3", lambda r: f"{r.base_filename}_{r.type}.3gf"),
    ("gps", "g", lambda r: f"{r.base_filename}_{r.type}.gps"),
]
```

In the loop: when `skip_key` is set and present in `skip_metadata`, emit the
existing "Skipping <thumbnail|accelerometer|gps>" debug log and continue;
otherwise call `_dl(filename, artifact_type)`, OR into `any_downloaded`, and for
the mp4 entry keep its returned `speed_bps`. Keep `_dl` as-is. Map the skip-log
wording from the artifact type so messages are byte-identical to today.

- [ ] **Step 3: Run the suite; confirm still green**

Run: `venv/bin/pytest test/test_sync_callback.py test/blackvuesync_test.py -q`
Expected: PASS, no message/coverage regressions.

- [ ] **Step 4: Confirm pylint no longer needs `too-many-locals` on the function**

Run: `venv/bin/pylint blackvuesync/sync.py --disable=all --enable=R0914,R0912,R0915`
If the warning is gone for `download_recording`, remove its pragma; else keep and note why.

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/sync.py
git commit -m "refactor: make download_recording artifact loop data-driven"
```

---

### Task 2: Decompose `download_file` and add range-resume

**Files:**

- Modify: `blackvuesync/sync.py` (`download_file`, ~521-684; add module helpers + `content_range_re`)
- Test: `test/test_sync_resume.py` (new)

**Step 1: Write the failing tests** -- `test/test_sync_resume.py`:

```python
"""tests for byte-range resume in download_file."""

from __future__ import annotations

import os
import re
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

import blackvuesync.sync as _sync
from blackvuesync.sync import download_file

_PAYLOAD = b"".join(bytes([i % 256]) for i in range(1024 * 7))  # 7 KiB
_FILENAME = "20230101_120000_NF.mp4"
_RANGE_RE = re.compile(r"bytes=(\d+)-")


class _RangeHandler(BaseHTTPRequestHandler):
    """serves _PAYLOAD honoring a single open-ended Range with 206."""

    seen_range: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        rng = self.headers.get("Range")
        type(self).seen_range = rng
        if rng and (m := _RANGE_RE.fullmatch(rng.strip())):
            start = int(m.group(1))
            body = _PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Range", f"bytes {start}-{len(_PAYLOAD) - 1}/{len(_PAYLOAD)}"
            )
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_PAYLOAD)))
        self.end_headers()
        self.wfile.write(_PAYLOAD)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        """silences test server logging."""


class _NoRangeHandler(BaseHTTPRequestHandler):
    """ignores Range; always returns the whole payload with 200."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(_PAYLOAD)))
        self.end_headers()
        self.wfile.write(_PAYLOAD)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        """silences test server logging."""


def _serve(handler: type[BaseHTTPRequestHandler]) -> Generator[str, None, None]:
    handler.seen_range = None  # type: ignore[attr-defined]
    server = HTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()


@pytest.fixture()
def range_server() -> Generator[str, None, None]:
    yield from _serve(_RangeHandler)


@pytest.fixture()
def norange_server() -> Generator[str, None, None]:
    yield from _serve(_NoRangeHandler)


def _seed_partial(destination: Path, nbytes: int) -> None:
    (destination / f".{_FILENAME}").write_bytes(_PAYLOAD[:nbytes])


def _final(destination: Path) -> bytes:
    return (destination / _FILENAME).read_bytes()


class TestResume:
    def test_resumes_from_partial_with_206(
        self, range_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 3000)
        ok, _ = download_file(range_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        assert _final(tmp_path) == _PAYLOAD
        assert _RangeHandler.seen_range == "bytes=3000-"

    def test_only_tail_transferred_on_resume(
        self, range_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 5000)
        totals: list[int] = []
        download_file(
            range_server,
            _FILENAME,
            str(tmp_path),
            None,
            on_chunk=lambda d, t: totals.append(t),
        )
        # on_chunk total reflects the whole file, not the 2 KiB tail
        assert totals and all(t == len(_PAYLOAD) for t in totals)

    def test_no_range_header_when_no_partial(
        self, range_server: str, tmp_path: Path
    ) -> None:
        ok, _ = download_file(range_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        assert _final(tmp_path) == _PAYLOAD
        assert _RangeHandler.seen_range is None

    def test_falls_back_to_full_download_when_server_ignores_range(
        self, norange_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 4000)
        ok, _ = download_file(norange_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        # a 200 truncates the partial and rewrites cleanly
        assert _final(tmp_path) == _PAYLOAD

    def test_stop_mid_resume_keeps_partial(
        self, range_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_partial(tmp_path, 1000)
        monkeypatch.setattr(_sync, "is_stop_requested", lambda: True)
        with pytest.raises(UserWarning):
            download_file(range_server, _FILENAME, str(tmp_path), None)
        # partial survives (and is at least the seeded size); no final file
        assert (tmp_path / f".{_FILENAME}").exists()
        assert not (tmp_path / _FILENAME).exists()
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_sync_resume.py -q`
Expected: FAIL (resume not yet implemented -- `seen_range` is `None`, partial gets truncated).

- [ ] **Step 3: Add the helpers and rewrite `download_file` as an orchestrator**

Add near the other module regexes:

```python
# parses a Content-Range value such as "bytes 100-199/500" (total may be "*")
content_range_re = re.compile(r"bytes\s+(?P<start>\d+)-\d+/(?P<total>\d+|\*)")
```

Add module-level helpers (all stdlib, type-annotated, lowercase third-person docstrings):

```python
def _resume_offset(temp_filepath: str) -> int:
    """returns the byte size of an existing partial download, or 0 if absent."""
    try:
        return os.path.getsize(temp_filepath)
    except OSError:
        return 0


def _build_record_request(url: str, resume_from: int) -> urllib.request.Request:
    """builds the record GET, adding the affinity key and a Range header when resuming."""
    request = urllib.request.Request(url)
    if affinity_key:
        request.add_header("X-Affinity-Key", affinity_key)
    if resume_from > 0:
        request.add_header("Range", f"bytes={resume_from}-")
    return request


def _stream_response(
    response: object,
    temp_filepath: str,
    resume_from: int,
    on_chunk: Callable[[int, int], None] | None,
) -> tuple[int, int]:
    """writes the body to the temp file, appending when the server confirms a 206
    resume and truncating otherwise; returns (bytes_transferred, total_bytes)."""
    status = response.getcode()
    headers = response.info()
    content_length = int(headers.get("Content-Length") or 0)

    resume_ok = False
    total_bytes = content_length
    if status == 206 and resume_from > 0:
        m = content_range_re.fullmatch((headers.get("Content-Range") or "").strip())
        if m is not None and int(m.group("start")) == resume_from:
            resume_ok = True
            total = m.group("total")
            total_bytes = int(total) if total != "*" else resume_from + content_length

    mode = "ab" if resume_ok else "wb"
    downloaded = resume_from if resume_ok else 0
    start_at = downloaded
    with open(temp_filepath, mode) as f:
        while True:
            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            if is_stop_requested():
                raise UserWarning("sync stopped by user")
            f.write(chunk)
            downloaded += len(chunk)
            if on_chunk is not None:
                on_chunk(downloaded, total_bytes)
    return downloaded - start_at, total_bytes


def _log_download_failure(filename: str, error: object, *, marker: bool) -> None:
    """emits the shared structured warning for a failed file download."""
    cron_logger.warning(
        "Could not download file : %s; error : %s; ignoring.",
        filename,
        error,
        extra={
            "event": "file_download_failed",
            "recording_filename": filename,
            "error_type": type(error).__name__,
            "error": str(error),
            "failure_marker_created": marker,
        },
    )
```

Rewrite `download_file`'s body (keep the early-return guards for
already-downloaded / dry-run / failure-marker exactly as-is). The transfer block
becomes:

```python
    temp_filepath = os.path.join(destination, f".{filename}")
    resume_from = _resume_offset(temp_filepath)
    if resume_from:
        logger.debug(
            "Found incomplete download : %s",
            temp_filepath,
            extra={
                "event": "incomplete_download_found",
                "recording_filename": filename,
                "temp_path": temp_filepath,
                "resume_from_bytes": resume_from,
            },
        )

    try:
        url = urllib.parse.urljoin(base_url, f"Record/{filename}")
        start = time.perf_counter()
        try:
            request = _build_record_request(url, resume_from)
            with urllib.request.urlopen(request) as response:
                transferred, _total = _stream_response(
                    response, temp_filepath, resume_from, on_chunk
                )
        finally:
            elapsed_s = time.perf_counter() - start

        os.rename(temp_filepath, destination_filepath)

        speed_bps = int(10.0 * transferred / elapsed_s) if transferred else None
        # ... existing success debug log, using transferred for content_length_bytes ...
        if metrics:
            metrics.record_file_download(transferred)
        return True, speed_bps
    except urllib.error.HTTPError as e:
        if e.code == 416:
            # the local partial is larger than the source (corrupt); discards and
            # restarts once from byte 0
            with contextlib.suppress(OSError):
                os.remove(temp_filepath)
            return download_file(
                base_url, filename, destination, group_name, metrics, on_chunk
            )
        if metrics:
            metrics.record_file_download_failure("http")
        _log_download_failure(filename, e, marker=True)
        mark_download_failed(destination, group_name, filename)
        return False, None
    except urllib.error.URLError as e:
        if metrics:
            metrics.record_file_download_failure("network")
        _log_download_failure(filename, e, marker=False)
        return False, None
    except socket.timeout as e:
        if metrics:
            metrics.record_file_download_failure("timeout")
        raise UserWarning(
            f"Timeout communicating with dashcam at address : {base_url}; error : {e}"
        ) from e
```

Keep the existing success-path debug log (`event: file_downloaded`) intact,
substituting `transferred` for the old `content_length_bytes`. Remove the
`too-many-branches`/`too-many-statements` pragmas if the decomposition clears
them; keep `too-many-arguments`/`too-many-positional-arguments` (signature
unchanged). Ensure `contextlib` is imported (it already is, `sync.py:6`).

**416 recursion guard:** the single retry cannot loop -- after `os.remove`,
`_resume_offset` returns 0, so the retry sends no `Range` header and a `200`/`416`
path that returns `416` again would require the server to reject a non-range GET,
which it cannot for an existing file. If defensiveness is preferred, thread an
internal `_retry: bool = False` parameter instead of relying on this argument;
implementer's choice, but document it.

- [ ] **Step 4: Run resume tests + the full existing suite**

Run: `venv/bin/pytest test/test_sync_resume.py test/test_sync_callback.py test/blackvuesync_test.py -q`
Expected: PASS (including `test_download_file_streams_response_in_chunks`, which relies on `getcode()`).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/sync.py test/test_sync_resume.py
git commit -m "feat: resume interrupted downloads via HTTP range requests"
```

---

### Task 3: Keep partials across runs; prune orphans at sync start

**Files:**

- Modify: `blackvuesync/sync.py` (`clean_destination` ~1116; `sync` ~1056; add `prune_orphan_partials`)
- Test: `test/test_clean_destination.py` (new)

**Step 1: Write the failing tests** -- `test/test_clean_destination.py`:

```python
"""tests for clean_destination and prune_orphan_partials."""

from __future__ import annotations

import os
from pathlib import Path

import blackvuesync.sync as _sync
from blackvuesync.sync import clean_destination, prune_orphan_partials


def _touch(p: Path) -> None:
    p.write_bytes(b"x")


class TestCleanDestinationKeepsPartials:
    def test_partial_dotfiles_are_not_removed(self, tmp_path: Path) -> None:
        partial = tmp_path / ".20230101_120000_NF.mp4"
        _touch(partial)
        clean_destination(str(tmp_path), "none")
        assert partial.exists()

    def test_empty_group_directories_still_removed(self, tmp_path: Path) -> None:
        group = tmp_path / "2023-01-01"
        group.mkdir()
        clean_destination(str(tmp_path), "daily")
        assert not group.exists()


class TestPruneOrphanPartials:
    def test_removes_partials_not_in_expected_set(self, tmp_path: Path) -> None:
        keep = tmp_path / ".20230101_120000_NF.mp4"
        orphan = tmp_path / ".20220101_120000_NF.mp4"
        _touch(keep)
        _touch(orphan)
        prune_orphan_partials(str(tmp_path), {"20230101_120000_NF.mp4"})
        assert keep.exists()
        assert not orphan.exists()

    def test_dry_run_keeps_everything(
        self, tmp_path: Path, monkeypatch: "object"
    ) -> None:
        orphan = tmp_path / ".20220101_120000_NF.mp4"
        _touch(orphan)
        monkeypatch.setattr(_sync, "dry_run", True)  # type: ignore[attr-defined]
        prune_orphan_partials(str(tmp_path), set())
        assert orphan.exists()
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_clean_destination.py -q`
Expected: FAIL (`prune_orphan_partials` undefined; `clean_destination` still removes the partial).

- [ ] **Step 3: Implement**

In `clean_destination`, delete the `TEMP_FILENAME_GLOB` removal loop (lines
~1118-1127); keep the empty-grouping-directory removal. Add:

```python
def prune_orphan_partials(destination: str, expected_filenames: set[str]) -> None:
    """removes partial dotfiles whose recording is no longer downloadable this run.

    a partial is kept when its filename is in expected_filenames (it will be
    resumed); otherwise it is an orphan (rolled off the dashcam, out of the
    retention window, or now filtered out) and is removed."""
    temp_glob = os.path.join(destination, TEMP_FILENAME_GLOB)
    for temp_filepath in glob.glob(temp_glob):
        filename = os.path.basename(temp_filepath)[1:]  # strips leading dot
        if filename in expected_filenames:
            continue
        if dry_run:
            logger.debug("DRY RUN Would remove orphan partial : %s", temp_filepath)
            continue
        logger.debug("Removing orphan partial : %s", temp_filepath)
        os.remove(temp_filepath)
```

In `sync()`, after `current_dashcam_recordings` is finalized (after filters,
before/after sort), build the expected set and prune:

```python
    expected_filenames = {
        builder(r)
        for r in current_dashcam_recordings
        for (_artifact, _skip, builder) in _ARTIFACTS
    }
    prune_orphan_partials(destination, expected_filenames)
```

(`_ARTIFACTS` from Task 1 provides every artifact filename, so a partial for any
artifact of a still-current recording is preserved regardless of `skip_metadata`.)

- [ ] **Step 4: Run cleanup tests + full suite**

Run: `venv/bin/pytest test/test_clean_destination.py test/blackvuesync_test.py -q`
Expected: PASS. Update/remove any existing test that asserted `clean_destination` deletes partials (search: `grep -rn "TEMP_FILENAME\|clean_destination" test/`).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/sync.py test/test_clean_destination.py
git commit -m "feat: keep resumable partials across runs, prune orphans"
```

---

### Task 4: End-to-end resume scenario (Behave)

**Files:**

- Create: `features/sync_resume.feature`
- Possibly add steps: `features/steps/` (reuse existing where possible -- read `features/CLAUDE.md` first)

The Flask mock serves via `send_file` (`conditional=True`), so it already
answers `Range` with `206`. Scenario shape (sentence-case, lowercase steps, no
"And", present tense, "these" for tables -- per `features/CLAUDE.md`):

```gherkin
Feature: resume interrupted downloads

  Scenario: resume a partially downloaded recording
    Given these recordings on the dashcam:
      | filename                  |
      | 20230101_120000_NF.mp4    |
    Given a partial download of "20230101_120000_NF.mp4"
    When blackvuesync runs
    Then the recording "20230101_120000_NF.mp4" is fully downloaded
```

- [ ] **Step 1: Read `features/CLAUDE.md` and the existing step library** to find reusable Given/When/Then and the mock-recordings setup step.
- [ ] **Step 2: Add a `given` step that writes a `.{filename}` partial** (a prefix of the mock payload) into the destination, in the appropriate steps module by function (not by feature).
- [ ] **Step 3: Add a `then` step (or reuse) asserting the final file exists and equals the mock source bytes.**
- [ ] **Step 4: Run** `behave features/sync_resume.feature --no-capture` → PASS.
- [ ] **Step 5: Commit**

```bash
git add features/sync_resume.feature features/steps/
git commit -m "test: end-to-end resume of an interrupted download"
```

---

### Task 5: Housekeeping -- version, mypy overrides, API note

**Files:**

- Modify: `pyproject.toml`, `docs/api.md`

- [ ] **Step 1:** Bump `version` `2.4.0a1` → `2.4.0a2` in `pyproject.toml`.
- [ ] **Step 2:** Add `test_sync_resume` and `test_clean_destination` to the mypy per-module override list (match the existing pattern used for other test modules).
- [ ] **Step 3:** Add a one-line note to `docs/api.md` (sync section): interrupted downloads resume via HTTP range when the dashcam supports it, else restart.
- [ ] **Step 4: Run the full unit suite + mypy + pylint**

Run: `venv/bin/pytest test/ -q && venv/bin/mypy blackvuesync && venv/bin/pylint blackvuesync/sync.py`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/api.md
git commit -m "chore: bump version, mypy overrides, document resume"
```

---

## Final verification (before PR)

- [ ] `venv/bin/pytest test/ -q` -- all green; `sync.py` coverage ≥ 95%.
- [ ] `behave` -- all scenarios pass, including `sync_resume.feature`.
- [ ] `venv/bin/pylint blackvuesync/sync.py` -- no S3776-equivalent complexity warnings; `download_file`/`download_recording` pragmas trimmed.
- [ ] `pre-commit run --files blackvuesync/sync.py test/test_sync_resume.py test/test_clean_destination.py` -- all hooks pass.
- [ ] Push branch, open PR; confirm all five required checks green.
- [ ] After CI: query `sonarcloud.io/api/issues/search` for the project -- confirm **0 findings** and S3776 cleared on both functions (do not trust the gate alone).
- [ ] Squash- or rebase-merge (linear history).

## Self-review

- **Spec coverage:** resume (Task 2 §1) · 200/416 fallback (Task 2 except arms) · partials persist + orphan bound (Task 3) · S3776 decomposition (Tasks 1-2) · no-regression (existing suites kept green each task) · BDD (Task 4).
- **Placeholders:** none -- test files and helper bodies are complete.
- **Type consistency:** `_ARTIFACTS` defined in Task 1 reused in Task 3; `_stream_response` returns `(transferred, total)`; `download_file` keeps its `tuple[bool, int | None]` signature; `prune_orphan_partials(destination, expected_filenames: set[str])` matches its test and its `sync()` call site.
- **getcode() vs status:** pinned in Task 2 to preserve the existing `FakeResponse` double.
