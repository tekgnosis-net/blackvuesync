# Web Foundation -- Phase B: SettingsStore

<!-- markdownlint-disable MD031 MD032 MD033 MD040 -->

**Date:** 2026-05-18
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Section 2)
**Phase:** B of 7 (A done; C-G to follow).

**Goal:** Introduce `blackvuesync/settings.py` with a `SettingsStore` that
persists configuration to `/config/settings.json` and replaces the
env-var-direct config path. The CLI's behavior is unchanged from the user
perspective: env vars seed the file on first run; subsequent runs read the
file. Sets the foundation for Phase C (web UI) to edit the same store.

**Architecture:** Frozen dataclass tree mirroring the design spec's nested
sections (connection, schedule, sync, retention, logging, metrics, web, auth,
system). Per-section `TIER` class-var (`immediate` / `next_tick` / `restart`)
for the future Settings UI. `SettingsStore` owns the in-process state with
thread-safe `get()` / `update()` / `on_change()` API. Atomic writes via
temp-file → fsync(fd) → chmod 0600 → os.replace → fsync(dir). One-shot
bootstrap from env vars when `settings.json` is absent. Schema `version: 1`
in the JSON for future migrations.

**Tech Stack:** Python 3.9+ stdlib only (no new runtime deps in this phase).
Pytest for unit tests; `freezegun` is a stretch dependency only if a test
needs time control (none currently). Test isolation via `tmp_path` fixtures.

**Out of scope for Phase B:**

- Auth (login, password hashing, sessions) -- Phase C.
- The web server itself -- Phase C.
- Scheduler / APScheduler -- Phase E.
- Apple-design CSS scaffolding -- Phase G.
- Any UI for editing settings -- Phase C/D.

---

## File structure after Phase B

```
blackvuesync/
├── __init__.py                     (unchanged: __version__ only)
├── __main__.py                     MODIFIED: now creates SettingsStore at
│                                   startup and passes Settings into the
│                                   sync orchestrator instead of argparse.Namespace
├── settings.py                     NEW: dataclass tree + SettingsStore
├── sync.py                         MODIFIED: run_sync(settings, ...) accepts
│                                   a Settings object instead of argparse args
│                                   (or alongside, with adapter shim)
├── metrics.py                      MODIFIED: cleanup items + decouple from
│                                   sync.dry_run; SyncMetrics takes dry_run
│                                   as constructor arg
└── server/                         unchanged (empty placeholder)

test/
├── blackvuesync_test.py            unchanged
└── test_settings.py                NEW: SettingsStore + Settings tests
```

---

## How to work with this plan

Per-task commits on `web-foundation-phase-b` branch. The plan is split into
**opening cleanup** (tasks C1-C4 -- the deferred items from Phase A's reviews)
and **main work** (tasks M1-M11). Each task ends with running the relevant
tests and committing.

The opening cleanup is independent of the main work and could in principle be
its own PR. Keeping it in the same PR is fine -- the diff context is small
and the changes are colocated by file (mostly `metrics.py` and `sync.py`).

Pre-commit hooks will reformat / lint on each commit. Hook auto-fixes get
re-staged and committed as new commits (no amends, no `--no-verify`), per the
project's CLAUDE.md.

---

## Opening cleanup (deferred from Phase A reviews)

### Task C1: Decouple `metrics.py` from `sync.dry_run`

Files: `blackvuesync/metrics.py`, `blackvuesync/sync.py`, `blackvuesync/__main__.py`,
`test/blackvuesync_test.py`.

Steps:

1. Currently `SyncMetrics.record_file_download` reads
   `blackvuesync.sync.dry_run` at runtime (via `import blackvuesync.sync as
   _sync`). This is a cross-module coupling on global state.
2. Change `SyncMetrics` to accept `dry_run: bool` as a constructor argument.
   Add a `dry_run: bool = False` field on the dataclass (or pass via the
   factory the existing code uses).
3. Update `SyncMetrics.record_file_download` to read `self.dry_run` instead
   of reaching into `_sync`.
4. Update the call sites in `__main__.py` (where `SyncMetrics` is
   instantiated) to pass `dry_run=args.dry_run`.
5. Remove the `import blackvuesync.sync as _sync` statement from
   `metrics.py` -- it should no longer be needed.
6. Run unit tests; verify metrics behavior unchanged.

### Task C2: Fix joke docstring and uppercase comments

Files: `blackvuesync/__main__.py`, `blackvuesync/sync.py`.

Steps:

1. In `__main__.py:202`, replace `"""run forrest run"""` with a descriptive
   third-person docstring such as `"""runs the sync workflow and returns
   the exit code."""`.
2. In `sync.py:1072-1078`, lowercase three comments in the `lock()` function:
   - `# Establish lock file settings` → `# establish lock file settings`
   - `# Create lock file` → `# create lock file`
   - `# Regarding umask, see ...` → `# regarding umask, see ...`
3. Run pre-commit hooks. They should reflow nothing; just verify clean.

### Task C3: Fix `metrics.py:409-410` empty except + redundant exceptions

Files: `blackvuesync/metrics.py`.

Steps:

1. Open `metrics.py` and inspect the `try/except` block around lines 409-410.
   Specifically the empty-block S108 and 3× S5713 findings.
2. The redundant exception classes likely look like
   `except (OSError, FileNotFoundError, PermissionError, IsADirectoryError):` --
   `FileNotFoundError`, `PermissionError`, and `IsADirectoryError` are all
   subclasses of `OSError`, so listing them is redundant.
3. Simplify the `except` clause to just `except OSError:`.
4. If the body is genuinely empty (just `pass`), add a brief lowercase
   comment explaining why the failure is intentionally swallowed (e.g.,
   `# best-effort cleanup; failures are non-fatal`). If the body is not
   strictly needed, document the no-op.
5. Run unit tests.

### Task C4: Fix `__main__.py:307` use `logging.exception()`

Files: `blackvuesync/__main__.py`.

Steps:

1. The line at `__main__.py:307` likely uses
   `logger.error("...", exc_info=True)` or `logger.error("...", exc_info=exc)`
   inside an except handler. SonarCloud's `python:S8572` says this should
   be `logger.exception("...")` which is idiomatic and automatically attaches
   the traceback.
2. Change `logger.error(..., exc_info=...)` to `logger.exception(...)`.
3. Run unit tests.

---

## Main work: SettingsStore introduction

### Task M1: Scaffold `blackvuesync/settings.py`

Files: `blackvuesync/settings.py` (create).

Steps:

1. Create the file with a lowercase module docstring:
   ```python
   """settings schema, validation, atomic persistence, and env-var bootstrap."""
   ```
2. Add imports:
   ```python
   from __future__ import annotations

   import json
   import logging
   import os
   import secrets
   import stat
   import threading
   from dataclasses import dataclass, field, replace
   from pathlib import Path
   from typing import Callable, ClassVar, Literal
   ```
3. Add `SCHEMA_VERSION = 1` constant.
4. Add the `PropagationTier` type alias:
   ```python
   PropagationTier = Literal["immediate", "next_tick", "restart"]
   ```
5. Add an empty `Settings` class placeholder. Subsequent tasks fill it.
6. Run `mypy blackvuesync/` to confirm the empty scaffold passes typing.

### Task M2: Implement the section dataclasses

Files: `blackvuesync/settings.py`.

Implement these in order (each is a `@dataclass(frozen=True)` with a
`TIER: ClassVar[PropagationTier]`):

1. `ConnectionSettings` (TIER=`restart`): `address: str`,
   `timeout_seconds: float = 10.0`.
2. `ScheduleSettings` (TIER=`next_tick`):
   `cron_expression: str = "*/15 * * * *"`, `timezone: str = "UTC"`.
3. `SyncSettings` (TIER=`next_tick`):
   `priority: Literal["date", "rdate", "type"] = "date"`,
   `grouping: Literal["none", "daily", "weekly", "monthly", "yearly"] = "none"`,
   `include: tuple[str, ...] = ()`,
   `exclude: tuple[str, ...] = ()`,
   `retry_failed_after: str = "1d"`,
   `skip_metadata: tuple[Literal["t", "3", "g"], ...] = ()`,
   `affinity_key: str | None = None`.
4. `RetentionSettings` (TIER=`next_tick`): `keep: str = "2w"`,
   `max_used_disk_percent: int = 90`.
5. `LoggingSettings` (TIER=`immediate`): `verbose: int = 0`, `quiet: bool = False`,
   `format: Literal["text", "json"] = "text"`,
   `file_max_bytes: int = 10 * 1024 * 1024`,
   `file_backup_count: int = 5`,
   `ring_buffer_capacity: int = 1000`.
6. `MetricsSettings` (TIER=`immediate`): `file: str | None = None`,
   `pushgateway_url: str | None = None`, `job: str = "blackvuesync"`,
   `instance: str | None = None`,
   `state_file: str = "/config/metrics-state.json"`.
7. `WebSettings` (TIER=`restart`): `port: int = 8080`,
   `session_lifetime_hours: int = 24`.
8. `AuthSettings` (TIER=`immediate`):
   `mode: Literal["login", "none", "proxy"] = "login"`,
   `username: str = "admin"`, `password_hash: str = ""`,
   `session_secret: str = ""`,
   `trusted_proxies: tuple[str, ...] = ()`,
   `proxy_user_header: str = "X-Remote-User"`.
9. `SystemSettings` (TIER=`restart`): `destination: str = "/recordings"`,
   `dry_run: bool = False`.
10. Top-level `Settings` (no TIER): `version: int = 1`,
    one field per section with default factory.

Run `mypy blackvuesync/` after each section. The Literal types catch
typos at type-check time.

### Task M3: Add `validate()` methods on each section

Files: `blackvuesync/settings.py`.

Steps:

1. Each section dataclass gets a `validate(self) -> list[str]` method that
   returns a list of human-readable error strings (accumulator pattern, not
   raise-fast). Examples:
   - `ConnectionSettings.validate`: `address` non-empty, `timeout_seconds` > 0.
   - `ScheduleSettings.validate`: `cron_expression` parses as a valid 5-field
     crontab (use a tiny inline validator since APScheduler is not yet a dep
     in Phase B; the validator just checks 5 whitespace-separated tokens
     containing only `0-9*/,-`).
   - `SyncSettings.validate`: `retry_failed_after` matches the existing
     duration grammar; `skip_metadata` tokens are within {"t", "3", "g"}.
   - `RetentionSettings.validate`: `keep` matches the duration grammar;
     `max_used_disk_percent` in [1, 100].
   - `LoggingSettings.validate`: `verbose` >= 0; `file_max_bytes` > 0;
     `file_backup_count` >= 0; `ring_buffer_capacity` > 0.
   - `WebSettings.validate`: `port` in [1, 65535]; `session_lifetime_hours` > 0.
   - `AuthSettings.validate`: when `mode == "proxy"`, `trusted_proxies` must
     be non-empty and `proxy_user_header` must be non-empty; when
     `mode == "login"`, no additional constraint (`password_hash` may be empty
     to trigger first-run wizard).
2. `Settings.validate(self) -> list[str]` aggregates errors from all sections.
3. Add unit tests in `test/test_settings.py` exercising one valid + one
   invalid case per section.

### Task M4: Implement `SettingsStore` class

Files: `blackvuesync/settings.py`.

Steps:

1. Define a `ValidationError(Exception)` class carrying a list of error
   strings.
2. Define `SettingsStore`:
   ```python
   class SettingsStore:
       def __init__(self, path: Path) -> None: ...
       def get(self) -> Settings: ...
       def update(
           self, mutation: Callable[[Settings], Settings]
       ) -> Settings: ...
       def on_change(
           self, listener: Callable[[Settings, Settings], None]
       ) -> None: ...
   ```
3. Use `threading.RLock` for thread safety. The `get()` returns the current
   frozen `Settings` (safe to share). The `update()` applies a functional
   mutation, validates the result, persists atomically, and fires change
   callbacks. `on_change()` registers a listener fired on every successful
   update.
4. Stub the persistence + bootstrap (next tasks fill them) -- for now,
   `update()` only validates and stores in memory; persistence is M5.
5. Unit tests: round-trip through `update()`, register and verify listener
   invocation, ValidationError on bad mutation.

### Task M5: Implement atomic JSON persistence

Files: `blackvuesync/settings.py`.

Steps:

1. Add `_save(self, settings: Settings) -> None` method:
   - Recursively convert dataclass to dict (handle `tuple` → `list`).
   - Write to temp file (`path.with_suffix(path.suffix + ".tmp")`).
   - `os.fsync(fd)` before close.
   - `os.chmod(tmp, 0o600)`.
   - `os.replace(tmp, path)`.
   - Open the parent directory and `os.fsync(dir_fd)` to make the rename
     durable.
2. Add `_load(self) -> Settings`:
   - Read the JSON; reverse the tuple/list conversion.
   - If `version < SCHEMA_VERSION`, call `migrate(raw)` (next task).
   - Construct `Settings` via dataclass replacement.
3. Wire `update()` to call `_save()` after validation, before notifying
   listeners.
4. On `__init__`, call `_load_or_bootstrap()` (M6 fills the bootstrap
   half; M5 just covers load-existing).
5. Unit tests for:
   - Round-trip: settings → save → load equals original.
   - Crash-resilience: monkeypatch `os.replace` to raise; assert the
     original file is intact.
   - Tuple/list serialization correctness.

### Task M6: Env-var bootstrap on first run

Files: `blackvuesync/settings.py`.

Steps:

1. Add `_bootstrap_from_env(self) -> Settings`:
   - Reads the same env vars `blackvuesync.sh` translates today: `ADDRESS`,
     `TIMEOUT`, `PRIORITY`, `GROUPING`, `KEEP`, `MAX_USED_DISK`,
     `RETRY_FAILED_AFTER`, `SKIP_METADATA`, `VERBOSE`, `QUIET`, `LOG_FORMAT`,
     `METRICS_FILE`, `METRICS_PUSHGATEWAY_URL`, `METRICS_JOB`,
     `METRICS_INSTANCE`, `METRICS_STATE_FILE`, `AFFINITY_KEY`.
   - Reads new Phase-B-introduced env vars: `BLACKVUESYNC_PORT`,
     `BLACKVUESYNC_ADMIN_USERNAME`, `BLACKVUESYNC_ADMIN_PASSWORD`,
     `BLACKVUESYNC_SCHEDULE` (default `*/15 * * * *`),
     `BLACKVUESYNC_TIMEZONE` (default `UTC`).
   - Generates `session_secret` via `secrets.token_hex(32)`.
   - If `BLACKVUESYNC_ADMIN_PASSWORD` is set, leaves `password_hash` empty
     for now (hashing happens in Phase C); record a TODO log line that
     first-run UI will hash it.
   - Logs a one-time warning for retired env vars: `CRON`, `RUN_ONCE`
     ("ignored; the new service is always-on and uses settings.schedule").
2. `_load_or_bootstrap` returns either the loaded settings or, when the
   file doesn't exist, the bootstrapped settings (persisted immediately
   via `_save`).
3. Unit tests:
   - Bootstrap from env vars produces the expected `Settings` field-by-field.
   - Bootstrap is idempotent: second call to `_load_or_bootstrap` returns
     the same settings without re-bootstrapping.
   - Retired env vars (`CRON`, `RUN_ONCE`) produce warning log entries.

### Task M7: Schema versioning + migration plumbing

Files: `blackvuesync/settings.py`.

Steps:

1. Add `def migrate(raw: dict, from_version: int) -> dict` as a placeholder.
   For Phase B there's only `version: 1`, so the function is essentially a
   pass-through that just verifies the version and returns the raw dict.
2. Wire `_load` to detect older schemas: if `raw.get("version", 1) < 1`,
   call `migrate(raw, raw["version"])`. This is no-op for now but
   establishes the seam.
3. Unit test: feed a synthetic dict with `version: 0` and verify
   `migrate()` is invoked.

### Task M8: File-perms safety check

Files: `blackvuesync/settings.py`.

Steps:

1. On `_load`, before reading the file, `os.stat()` it and verify the
   `st_mode & 0o077 == 0` (no group/other access). If the file has wider
   perms, raise a clear error and refuse to start.
2. Unit test: create a settings.json with 0o644 perms in `tmp_path`, attempt
   `_load_or_bootstrap`, assert the specific error.

### Task M9: Wire `SettingsStore` into `__main__.py`

Files: `blackvuesync/__main__.py`, `blackvuesync/sync.py`.

Steps:

1. The current `__main__.main()` reads `argparse.Namespace` from
   `parse_args()`, then calls `run_sync(args)`. After this task:
   - At startup, instantiate `SettingsStore(Path("/config/settings.json"))`
     and call `settings = store.get()`.
   - Translate CLI args (if provided) into one-off overrides on top of
     `settings` (e.g., `--verbose` on the command line still works for ad-hoc
     diagnostic invocations).
   - Pass `settings` (the resolved `Settings` instance) to `run_sync`.
2. Add a new `run_sync(settings: Settings) -> int` overload in `sync.py`
   that accepts a `Settings` instance. The existing argparse-based code path
   stays as a thin adapter that converts `args` → `Settings` then calls
   `run_sync(settings)`. (This preserves existing test fixtures that build
   argparse namespaces; full Settings adoption happens in Phase E when
   APScheduler replaces cron.)
3. Verify CLI invocations work end-to-end:
   - `python -m blackvuesync --help` -- unchanged help text.
   - `python -m blackvuesync <address>` -- works against the mock dashcam.
   - First run with no `/config/settings.json` -- bootstrap kicks in and
     produces the file.
   - Second run -- reads the file, ignores env vars except where they're
     newly introduced (the design spec says env vars are seed-only after
     first run; document this in the README).

### Task M10: Comprehensive test suite

Files: `test/test_settings.py` (create), `test/conftest.py` (create or modify).

Steps:

1. Create `test/conftest.py` with a `settings_path` fixture that returns
   a fresh `tmp_path / "settings.json"`.
2. Create `test/test_settings.py` with tests for:
   - Dataclass defaults match expected values per section.
   - Validators reject specific invalid inputs (one per section).
   - `Settings.validate()` aggregates errors from all sections.
   - Frozen dataclass: attempts to mutate fields raise `FrozenInstanceError`.
   - `SettingsStore.get()` returns a frozen Settings.
   - `SettingsStore.update()` applies a mutation, validates, persists.
   - `SettingsStore.update()` raises `ValidationError` on bad input.
   - `SettingsStore.on_change()` invokes registered listeners.
   - Atomic write survives a simulated crash (`os.replace` raises).
   - Env-var bootstrap matches expected mappings.
   - File-perms check rejects 0o644.
   - Schema migration is invoked for older versions.
3. Target ≥90% line coverage on `settings.py`. Run
   `pytest test/test_settings.py --cov=blackvuesync/settings.py -v`.

### Task M11: Documentation

Files: `CLAUDE.md`, `README.md`.

Steps:

1. Update `CLAUDE.md` "Architecture" section to mention `settings.py`
   alongside `sync.py` and `metrics.py`. Note that env vars are seed-only
   after first run; `/config/settings.json` is canonical.
2. Update `README.md` to document `/config/settings.json` for Docker users.
   Add a note that env-var changes after first run require either editing
   the file or deleting it (and the container re-bootstraps).
3. Add a brief "Recovery" section: if you forget the admin password
   (relevant once Phase C lands), edit `auth.password_hash` to `""` and
   restart. (Phase B itself doesn't have admin login yet but the recovery
   procedure is the same.)

---

## Verification

Run each before opening the PR:

- `pytest test/ -v` -- all unit tests pass, including new `test_settings.py`.
- `pytest test/test_settings.py --cov=blackvuesync/settings.py` -- ≥90%.
- `mypy blackvuesync/` -- clean.
- `behave --no-capture` -- all in-process Behave scenarios pass (no changes
  expected; sync behavior unchanged).
- `behave -D implementation=docker` -- all docker-mode scenarios pass.
- `pre-commit run --all-files` -- clean.
- `python -m blackvuesync --help` -- unchanged help text.
- `python -m blackvuesync <mock-dashcam-addr>` against the test mock dashcam
  -- one successful sync end-to-end, with `/config/settings.json` created on
  first run.
- Force-test the file-perms check: `chmod 644 /tmp/test/settings.json`,
  attempt to start, confirm the clear error.

## Branch protection workflow

Each commit pushes to `web-foundation-phase-b`. After M11 (or batched
periodically), push and open PR titled
`Web Foundation Phase B: SettingsStore (env-var-seed → file-canonical)`.

All five required checks must pass (`pre-commit`, `unit-tests`,
`integration-tests`, `test`, `SonarCloud Code Analysis`). The 8 carry-forward
code smells from Phase A should drop to 4 after this phase's opening cleanup
(C1-C4 directly address: redundant exceptions, empty except, logging.error
→ exception, joke docstring, uppercase comments, metrics-sync coupling).
Use **Squash and merge** or **Rebase and merge** (linear-history rule).

## After Phase B merges

Phase C is up next: the Flask app skeleton, login/first-run wizard, auth
modes (login/none/proxy), CSRF, security headers. The SettingsStore from
Phase B becomes the canonical source for the auth settings the Flask routes
consult.

---

## Self-review

Done inline during drafting:

- **Spec coverage:** Section 2 of the design spec is fully covered. Section
  2 mentioned `argon2-cffi` as a Phase 2 dep; deferred to Phase C where it's
  actually used for password hashing during login. Phase B leaves
  `password_hash` empty until Phase C wires up the first-run wizard.
- **Placeholder scan:** No TBDs. Specific symbol names are used where the
  current code is the reference; the engineer reads `metrics.py` and
  `__main__.py` to find exact line numbers since they may drift slightly.
- **Type consistency:** `Settings` and the section dataclasses have
  consistent naming; `TIER` is `ClassVar` (not a field); `PropagationTier`
  is the type alias used throughout.
- **Scope:** Strictly Phase B (settings store + opening cleanup). No auth,
  no web server, no scheduler. Marks where Phase C/E will pick up.

One known limitation: the schedule cron-expression validator in Task M3 is
a hand-rolled token sanity check, not a full crontab parser. APScheduler's
`CronTrigger.from_crontab` (introduced in Phase E) is the real validator;
Phase B's check is "rejects obviously malformed" rather than "rejects every
invalid cron." Acceptable since the only Phase B consumer is the bootstrap
and the validator on `update()`.
