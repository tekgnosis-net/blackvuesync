# Resumable Downloads + `download_file` Decomposition -- Design Spec

**Date:** 2026-06-01
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design -- folded out of the carry-forward debt list ahead of Sub-Project #2 Phase 2C
**Carry-forward items addressed:** byte-range resume (new) + `sync.py` S3776 decomposition of `download_file` / `download_recording` (paired, one PR)

---

## Context

`blackvuesync/sync.py` downloads each dashcam artifact to a temporary dotfile
(`.{filename}`) and `os.rename`s it onto the final name once complete. The name
`download_with_resume` in the original foundation design implied that an
interrupted download continues where it left off. It never did. Two mechanisms
defeat resume today:

1. **Truncate, not resume** (`download_file`, `sync.py:573-621`): an existing
   partial dotfile is detected and logged ("Found incomplete download"), then
   the download opens it with `open(temp_filepath, "wb")` -- which truncates it
   to zero -- and issues a plain `urlopen` with **no `Range` header**. The
   transfer always starts at byte 0.

2. **Swept every run** (`clean_destination`, `sync.py:1116-1145`): runs in a
   `finally` after every sync (success *or* failure, including a cooperative
   stop) and `os.remove`s every `.{timestamp}_*` dotfile. A partial cannot even
   survive to the next run.

The dotfile pattern therefore provides **atomicity** (no half-written file ever
appears under its final name), not resume. On a flaky LAN or a large
`--priority date` backlog, every interruption re-downloads the in-flight file
from scratch.

`download_file` and `download_recording` also carry SonarCloud **S3776**
(cognitive complexity) debt and a stack of pylint `disable` pragmas
(`too-many-branches`, `too-many-locals`, `too-many-statements`). Adding resume
logic to `download_file` as-is would deepen that debt. The two items are
therefore paired: the decomposition creates the seams into which the resume
logic cleanly slots.

---

## Goals

- Resume an interrupted artifact download from the first un-downloaded byte when
  the dashcam supports HTTP range requests.
- Degrade safely to a full re-download when the dashcam does **not** support
  ranges -- with no configuration and no separate capability request.
- Stop discarding in-progress partials between runs, while still cleaning up
  genuinely orphaned partials so disk use stays bounded.
- Reduce `download_file` / `download_recording` cognitive complexity below the
  S3776 threshold and remove the `too-many-*` pragmas where the decomposition
  makes them unnecessary.
- No regression to the cooperative stop flag, the failure-marker retry
  suppression, dry-run, metrics, or progress-publisher behavior.

## Non-goals

- A user-facing setting to toggle resume. The mechanism is self-correcting, so a
  flag would be speculative configuration (YAGNI).
- A separate `HEAD` probe of dashcam range support. The resume `GET` is the
  probe.
- Parallel / multi-connection downloads, checksum verification of resumed bytes,
  or any change to the filename grammar or the dashcam listing protocol.
- Touching the pre-existing `speed_bps = int(10.0 * size / elapsed)` scaling
  factor -- odd but out of scope for this change.

---

## Capability findings (why a runtime probe, not an assumption)

BlackVue does not officially document HTTP range support for the LAN web server
(`/Record/<file>` served alongside `blackvue_vod.cgi`). Evidence is empirical:

- Community NAS/PC scripts on DashCamTalk use **`wget -c`** against
  `http://<dashcam>/Record/*.mp4`. `wget -c` sends `Range: bytes=N-` and expects
  `206 Partial Content`; its continued use implies many firmwares honor ranges.
- No endpoint reference (e.g. the `hackvue` URL list) mentions `Accept-Ranges`,
  `206`, or `Content-Range`, so support cannot be assumed across models/firmware.

Conclusion: probe at runtime per download and branch on the response. This is
robust to firmware variation and needs no model database.

The **mock dashcam** used by the Behave suite serves files via Flask
`send_file`, which defaults to `conditional=True` and therefore already answers
`Range` requests with `206` + `Content-Range`. End-to-end resume is testable in
CI without hardware.

---

## Design

### 1. Resume in `download_file`

The download decision tree, keyed on the partial dotfile and the HTTP response:

```text
resume_from = size of .{filename} on disk, else 0

build GET /Record/{filename}
  add X-Affinity-Key when configured (unchanged)
  if resume_from > 0: add header  Range: bytes={resume_from}-

open the response:
  status 206 (Partial Content) and Content-Range start == resume_from
        -> open temp "ab" (append); downloaded = resume_from
           total = parsed total from Content-Range, else resume_from + Content-Length
  status 200 (OK)                      # server ignored Range, or none sent
        -> open temp "wb" (truncate);  downloaded = 0; total = Content-Length
  status 206 but Content-Range start != resume_from   # server disagreed
        -> treat as full restart: open "wb"; downloaded = 0
  HTTPError 416 (Range Not Satisfiable)               # partial >= source size
        -> remove the partial; retry once from byte 0 (truncate)

stream chunks (unchanged loop): cooperative stop check between read and write,
  on_chunk(downloaded, total) after each write
rename temp -> final on success (unchanged)
```

Key points:

- **No `Range` header when `resume_from == 0`.** The common (no-partial) path is
  byte-identical to today: a plain `GET`, zero overhead, zero behavior change.
- **The probe is the resume `GET`.** No extra round-trip. A `200` response means
  "server is sending the whole file from 0" -- we truncate and take it, exactly
  matching today's behavior, so unsupported firmware is transparently handled.
- **`urllib` treats `206` as success** (2xx) and raises `HTTPError` only for
  `>= 400`. `416` is the one range-specific error and is handled by discarding
  the (over-long, hence corrupt) partial and restarting once. Recordings on a
  dashcam are immutable per filename, so `416` indicates a bad local partial,
  not a changed source.
- **`Content-Range` parsing**: `bytes {start}-{end}/{total}`. `total` feeds the
  publisher so the progress bar reflects the *whole* file, not just the resumed
  tail. If the header is missing or unparseable on a `206`, fall back to
  `total = resume_from + Content-Length`.
- **Metrics**: `record_file_download` records **bytes transferred this run**
  (the remaining bytes on a resume, the full size on a fresh download), which is
  the correct semantic for a bandwidth counter. `record_file_download_failure`
  classifications (`http` / `network` / `timeout` / `disk`) are unchanged.
- **Cooperative stop unchanged**: a stop between read and write still raises
  `UserWarning`, leaving the partial on disk. With the cleanup change below, that
  partial now survives to be resumed on the next run -- which is the entire point.

### 2. Partial lifecycle: keep resumable, prune orphans

Removing the blanket sweep is necessary for cross-run resume; pruning orphans
keeps disk bounded.

- **`clean_destination` stops sweeping partials.** It keeps removing empty
  grouping directories (still wanted post-run). The `TEMP_FILENAME_GLOB` removal
  loop is deleted.
- **Orphan pruning moves to sync start**, where the dashcam listing is available.
  After `sync()` computes `current_dashcam_recordings` (post-retention,
  post-include/exclude), it builds the set of artifact filenames it intends to
  download this run and removes any partial dotfile whose filename is **not** in
  that set. A new helper `prune_orphan_partials(destination, expected_filenames)`
  encapsulates the glob + filter + remove.

Why this predicate is correct and leak-free:

| Partial for a recording that is… | In expected set? | Action |
| --- | --- | --- |
| a current target this run | yes | kept → resumed by `download_file` |
| rolled off the dashcam mid-download | no | pruned (true orphan) |
| now outside the retention window | no | pruned (we will not download it) |
| now excluded by include/exclude | no | pruned (we will not download it) |

A partial only ever exists for a recording `download_file` started, which only
happens for recordings that were current + selected at the time -- so the
"expected set" predicate prunes exactly the partials that have become
undownloadable, and keeps exactly those that will be retried.

`prune_orphan_partials` respects `dry_run` (logs, does not delete), mirroring the
existing cleanup helpers.

### 3. S3776 decomposition (paired)

`download_file` becomes a thin orchestrator delegating to focused module-level
helpers; the chunk/stop loop, response-mode decision, and exception handling
each move out:

- `_resume_offset(temp_filepath) -> int` -- size of the partial, or `0`.
- `_build_record_request(url, resume_from) -> urllib.request.Request` -- builds
  the `Request`, adds `X-Affinity-Key` and the conditional `Range` header. Owns
  the `# NOSONAR` for the `http://` literal if one is reintroduced here (the
  URL is currently joined from `base_url`, so no new clear-text literal is
  expected).
- `_stream_response(response, temp_filepath, resume_from, on_chunk) -> tuple[int, int]`
  -- selects append vs. truncate from `response.status` + `Content-Range`,
  validates the range start, runs the chunk loop with the cooperative stop
  check, returns `(bytes_transferred, total_bytes)`.
- `_log_download_success(filename, destination_filepath, transferred, elapsed_s)`
  -- the structured debug log + speed formatting block.
- The three `except` arms (`HTTPError` → failure marker; `URLError` → network,
  no marker; `socket.timeout` → re-raise as `UserWarning`) stay in the
  orchestrator but shrink to one-liners delegating to a shared
  `_log_download_failure(filename, error, *, marker)` helper, collapsing the
  duplicated `cron_logger.warning(...)` + `extra={...}` blocks (also clears the
  S1192 duplicate-literal risk).

`download_recording` becomes data-driven instead of three near-identical
metadata blocks. A small table drives the artifact loop:

```python
# (artifact_type, skip_metadata_key, filename_builder)
artifacts = [
    ("mp4", None, lambda r: r.filename),
    ("thm", "t",  lambda r: f"{r.base_filename}_{r.type}{r.direction}.thm"),
    ("3gf", "3",  lambda r: f"{r.base_filename}_{r.type}.3gf"),
    ("gps", "g",  lambda r: f"{r.base_filename}_{r.type}.gps"),
]
```

The mp4 entry (`skip_metadata_key=None`) is always downloaded and yields
`speed_bps`; metadata entries are skipped when their key is in `skip_metadata`,
emitting the existing "Skipping …" debug log. This removes the repeated blocks
and the bulk of the `too-many-locals` pressure.

After decomposition, the `# pylint: disable=too-many-branches,too-many-locals,
too-many-statements` and the S3776 suppression are removed from both functions;
any that remain are justified inline.

---

## Behavioral compatibility

| Concern | Before | After |
| --- | --- | --- |
| No partial present | plain GET, write `wb` | **identical** (no `Range` sent) |
| Dashcam supports ranges, partial present | re-download from 0 | resume from `resume_from` via `206` |
| Dashcam ignores `Range` (`200`) | n/a | truncate + full download (= today) |
| Corrupt over-long partial (`416`) | n/a | discard partial, restart once |
| Cooperative stop mid-file | partial deleted by `clean_destination` | partial **kept**, resumed next run |
| Recording rolled off dashcam | partial deleted | partial pruned at next sync start |
| dry-run | no writes/removes | no writes/removes (prune respects dry-run) |
| Already-downloaded final file | skipped | skipped (unchanged) |
| Failure-marker retry suppression | as-is | unchanged |

---

## Testing

### Unit (`pytest`)

Extend the in-process HTTP harness in `test/test_sync_callback.py` with a
range-aware handler (`_ResumableHandler`) that honors `Range` with `206` +
`Content-Range`, plus a `_NoRangeHandler` that ignores `Range` and always
returns `200`. New `test/test_sync_resume.py`:

- resume from a pre-seeded partial against a `206` server → final file equals
  full payload; server saw `Range: bytes=N-`; only the tail was transferred.
- `200`-only server with a partial present → partial is truncated; final file is
  correct (graceful fallback).
- `206` with a mismatched `Content-Range` start → full restart; final file
  correct.
- `416` from the server → partial removed, restart, final file correct.
- no partial present → no `Range` header sent (byte-identical to current path).
- `on_chunk` `total` reflects the **whole** file size on a resume, not the tail.
- cooperative stop mid-resume leaves a (larger) partial on disk.

New `test/test_clean_destination.py` (currently untested):

- `clean_destination` no longer removes partial dotfiles; still removes empty
  grouping directories.
- `prune_orphan_partials` removes partials absent from the expected set, keeps
  those present, and respects `dry_run`.

### Integration (Behave)

New scenario in a `features/sync_resume.feature` (or appended to
`sync_basic.feature`): given a recording on the dashcam and a pre-existing
partial dotfile in the destination, when sync runs, then the recording is fully
downloaded and its bytes match the source. Exercises real `206` handling through
the Flask mock.

### Coverage / gate

`sync.py` stays at its ≥95% baseline. SonarCloud must show **0 findings** (the
project standard) and S3776 cleared on both functions -- verified by querying the
issues API after CI, not by trusting the gate alone.

---

## Files

**Modify:**

- `blackvuesync/sync.py` -- `download_file` decomposition + resume; new
  `_resume_offset`, `_build_record_request`, `_stream_response`,
  `_log_download_success`, `_log_download_failure`; data-driven
  `download_recording`; `clean_destination` drops the partial sweep; new
  `prune_orphan_partials`; `sync()` calls it after computing current recordings.
- `pyproject.toml` -- version bump (`2.4.0a1` → `2.4.0a2`); add
  `test_sync_resume`, `test_clean_destination` to the mypy override list.
- `docs/plans/2026-05-19-sub-project-2-dashboard-design.md` -- carry-forward entry
  points at this design doc.
- `docs/api.md` -- no API change; note resume behavior under sync if relevant.

**Create:**

- `test/test_sync_resume.py`
- `test/test_clean_destination.py`
- `features/sync_resume.feature` (+ any step definitions, reusing existing ones
  where possible)

---

## Self-review

- **Spec coverage**: each goal maps to a design section (resume → §1; safe
  fallback → §1 `200`/`416` arms; partial persistence + orphan bound → §2;
  S3776 → §3; no-regression → the compatibility table + cooperative-stop note).
- **Placeholders**: none.
- **Ambiguity**: the orphan predicate is stated as a truth table; the response
  branching is stated as an explicit decision tree; metrics semantics on resume
  are pinned to "bytes transferred this run".
- **Consistency**: helper names here match the file inventory and the test
  names; the `clean_destination` change (drop partial sweep, keep dir cleanup)
  is consistent between §2 and the Files list.
- **Scope**: one PR -- resume + decomposition only. Dockerfile multi-stage and
  `LoggingSettings.on_change` wiring are the other two carry-forward items and
  ship as their own PRs.
