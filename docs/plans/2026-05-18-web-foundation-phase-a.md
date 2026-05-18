# Web Foundation -- Phase A: Package refactor

<!-- markdownlint-disable MD031 MD032 MD033 MD040 -->

**Date:** 2026-05-18
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md)
**Phase:** A of 7 (A-G). This plan covers Phase A only; subsequent phases each
get their own plan written when this one lands.

**Goal:** Restructure the single `blackvuesync.py` file into a package layout
under `blackvuesync/` so subsequent phases (settings store, web server,
scheduler, etc.) have stable module boundaries to hang code off. **No behavior
change.** All existing CLI flags, env vars, Docker invocation, and tests must
work identically afterward.

**Architecture:** Move code into modules organized by responsibility:
`sync.py` (dashcam HTTP, download, retention, filename regex), `metrics.py`
(SyncMetrics + Prometheus formatter + state file I/O), `__main__.py` (argparse,
cron-mode entry, signal handling), with empty `server/` package scaffolding
ready for Phase C. The script entry point becomes `python -m blackvuesync`
instead of `blackvuesync.py`.

**Tech Stack:** Same as today -- Python 3.9 stdlib only for runtime. No new
dependencies in this phase. Pytest + Behave unchanged.

**Out of scope for Phase A:**

- Any new functionality. Strictly mechanical decomposition.
- Settings store (Phase B), Flask server (Phase C), Progress publisher
  (Phase D), APScheduler (Phase E), web API (Phase F), Apple-design polish
  (Phase G).
- Removing `blackvuesync.sh` or the Docker cron daemon. They keep working
  via `python -m blackvuesync` in Phase A; replacement happens in Phase E.

---

## File structure after Phase A

```
blackvuesync/                       # NEW package directory
├── __init__.py                     # package version, public API re-exports
├── __main__.py                     # argparse, cron mode, signal handling, main()
├── sync.py                         # dashcam HTTP, download, retention, regex
├── metrics.py                      # SyncMetrics, Prometheus formatter, state I/O
└── server/                         # empty for Phase A; scaffolded for Phase C
    └── __init__.py                 # empty placeholder

blackvuesync.py                     # DELETED (replaced by package)

pyproject.toml                      # [project.scripts] entry point updated
                                    # [tool.setuptools] py-modules removed,
                                    #   packages = ["blackvuesync"]
                                    # max-module-lines constraint relaxed

blackvuesync.sh                     # invokes `python -m blackvuesync ...`
                                    #   instead of `blackvuesync.py ...`

test/blackvuesync_test.py           # imports updated to new module paths

features/                           # unchanged (uses subprocess; entry point
                                    #   is `python -m blackvuesync` via shell
                                    #   wrapper, transparent to Behave)
```

---

## How to work with this plan

Each task below is one logical unit. After every task, run the relevant tests
and commit before moving on. Branch off `main` after PR #3 (the design spec)
merges. Push the branch and open a PR after Task 9.

The pattern for each move-code task:

1. Open the source location and the destination file.
2. Move code (prefer copy + delete over cut + paste so you can spot-check).
3. Add necessary imports in the destination.
4. Replace the original with re-exports OR delete it, depending on the task.
5. Run the relevant subset of tests (`pytest test/blackvuesync_test.py -k '...'`
   for unit tests; `behave features/<file>.feature` for one Behave file).
6. If imports break, fix them in the failing site and re-run.
7. Commit with a tight message scoped to that task.

Pre-commit hooks will reformat with Black and run shellcheck/markdownlint/etc.
Hook failures are normal; re-stage and re-commit per the project's
`CLAUDE.md` guidance.

---

## Task 1: Scaffold the package directory

Files:

- Create: `blackvuesync/__init__.py`
- Create: `blackvuesync/__main__.py` (stub for now)
- Create: `blackvuesync/server/__init__.py` (empty placeholder for Phase C)

Steps:

1. Create the three files above. `blackvuesync/__init__.py` should contain only
   `__version__ = "2.3.0a0"` for now (bump from the current `2.2.0a4` to mark
   the start of the foundation work).
2. `blackvuesync/__main__.py` is a stub:

   ```python
   """entry point: re-routed to the existing blackvuesync.py for now."""
   from blackvuesync import sync_main

   if __name__ == "__main__":
       sync_main()
   ```

   This is temporary scaffolding. `sync_main` does not exist yet -- the import
   will fail when run. That is intentional: Task 7 wires it up.
3. Add the package to `pyproject.toml`. Change `[tool.setuptools]` from
   `py-modules = ["blackvuesync"]` to:

   ```toml
   [tool.setuptools]
   packages = ["blackvuesync", "blackvuesync.server"]
   ```
4. Bump version: in `pyproject.toml`, change `version = "2.2.0a4"` to
   `version = "2.3.0a0"`.
5. Reinstall in editable mode to pick up the new package layout:

   ```bash
   pip install -e ".[dev]"
   ```
6. Confirm `import blackvuesync` works at the Python REPL and exposes
   `__version__`.
7. Commit:

   ```bash
   git add blackvuesync/ pyproject.toml
   git commit -m "Scaffold blackvuesync package directory"
   ```

   (Hooks will run Black on the empty Python files; no diff expected.)

The existing `blackvuesync.py` still works at this point because nothing
imports the package yet. Tests still pass.

---

## Task 2: Extract metrics module

Files:

- Create: `blackvuesync/metrics.py`
- Modify: `blackvuesync.py` (remove the moved code, import from new module)
- Modify: `test/blackvuesync_test.py` (if it imports any of the moved
  symbols)

Steps:

1. In `blackvuesync.py`, identify the metrics code block. It includes:
   - The `SyncMetrics` dataclass and its helper methods.
   - The Prometheus text-format renderer (the function that produces the
     `# HELP` / `# TYPE` lines + samples).
   - `load_metrics_state` and `save_metrics_state`.
   - The failure-reason classifier helper used by the exception handlers.
2. Copy those symbols verbatim into `blackvuesync/metrics.py`. Add any
   necessary imports at the top (`dataclasses`, `json`, `logging`, `time`,
   `pathlib`, etc.).
3. Add lowercase module docstring per project guideline:

   ```python
   """metrics collection and Prometheus text-format rendering for sync runs."""
   ```
4. In the original `blackvuesync.py`, replace the moved code with re-exports
   so existing imports (in tests, in the same file) keep working:

   ```python
   from blackvuesync.metrics import (
       SyncMetrics,
       render_prometheus,
       load_metrics_state,
       save_metrics_state,
       classify_failure,
   )
   ```

   (Replace the symbol names with the actual names from the current code.)
5. Run the metrics-related unit tests:

   ```bash
   pytest test/blackvuesync_test.py -k metrics -v
   ```

   Expected: all pass with no source changes to the tests.
6. If a test imports a moved symbol directly (e.g.,
   `from blackvuesync import SyncMetrics`), keep that working via the
   re-export -- do not update the test in this task.
7. Commit:

   ```bash
   git add blackvuesync/metrics.py blackvuesync.py
   git commit -m "Extract metrics into blackvuesync.metrics"
   ```

---

## Task 3: Extract sync core into blackvuesync/sync.py

Files:

- Create: `blackvuesync/sync.py`
- Modify: `blackvuesync.py`

This task moves the *largest* chunk of code. Take it in subtasks; commit after
each subtask passes its tests.

### Task 3a: Filename regex and parsing helpers

Steps:

1. Identify the filename-related code in `blackvuesync.py`: `filename_re`
   constant; functions that parse `(timestamp, type, direction, upload_flag)`
   tuples; helper predicates like `is_normal_recording`, `is_event_recording`,
   etc.; the duration parser used by `--keep` and `--retry-failed-after`; the
   grouping path helpers (`group_dir_for_recording`, etc.).
2. Copy them into `blackvuesync/sync.py`. Module docstring:

   ```python
   """dashcam communication, recording parsing, download, and retention logic."""
   ```
3. Add re-exports in `blackvuesync.py`:

   ```python
   from blackvuesync.sync import (
       filename_re,
       parse_recording_filename,
       # ... add the actual names
   )
   ```
4. Run parsing/grouping tests:

   ```bash
   pytest test/blackvuesync_test.py -k 'parse or group or filter' -v
   ```

   Expected: all pass.
5. Commit:

   ```bash
   git add blackvuesync/sync.py blackvuesync.py
   git commit -m "Extract filename regex and parsing helpers into sync module"
   ```

### Task 3b: Dashcam HTTP client

Steps:

1. Identify the dashcam-communication code: HTTP request helpers that hit
   `blackvue_vod.cgi`, response parsing, the URL-error handler (`socket.timeout`
   etc. -- see the recent `Handle socket.timeout URL Error (#75)` commit for
   context).
2. Move those functions into `blackvuesync/sync.py`. Append to the file.
3. Re-export from `blackvuesync.py` as in 3a.
4. Run any HTTP-related unit tests (the project mostly tests HTTP via Behave;
   the unit suite may have only a few). Behave docker-mode in Task 9 will
   exercise the moved code end-to-end.

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
5. Commit:

   ```bash
   git add blackvuesync/sync.py blackvuesync.py
   git commit -m "Move dashcam HTTP client into sync module"
   ```

### Task 3c: Download with resume

Steps:

1. Identify `download_with_resume`, `download_file`, `download_recording`,
   the temp-dotfile handling, the chunked-write loop, the per-recording
   exception classifier.
2. Move them into `blackvuesync/sync.py`.
3. Re-export from `blackvuesync.py`.
4. Run tests:

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
5. Commit:

   ```bash
   git add blackvuesync/sync.py blackvuesync.py
   git commit -m "Move download functions into sync module"
   ```

### Task 3d: Retention, locking, run_sync wrapper

Steps:

1. Identify the retention logic (the function that walks the destination,
   compares filenames against the `--keep` cutoff, removes outdated recording
   groups), the lock file management (`fcntl.lockf` acquisition + release),
   and the overall `sync()` function that orchestrates list-from-dashcam +
   filter + download + retention.
2. Move them into `blackvuesync/sync.py`.
3. Add a thin `run_sync(args) -> int` wrapper that mirrors the current
   `sync()` function's flow but returns the exit code instead of calling
   `sys.exit()`. This is the call site Phase C will eventually use. For now,
   `__main__.py` will call it.
4. Re-export from `blackvuesync.py`.
5. Run all unit tests:

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
6. Commit:

   ```bash
   git add blackvuesync/sync.py blackvuesync.py
   git commit -m "Move retention and locking into sync module; add run_sync wrapper"
   ```

---

## Task 4: Move CLI dispatch into `__main__.py`

Files:

- Modify: `blackvuesync/__main__.py` (replace the stub from Task 1)
- Modify: `blackvuesync.py`

Steps:

1. Identify the CLI-dispatch code in `blackvuesync.py`: `parse_args()` (the
   full argparse setup with all 22 settings flags), the `main()` function, the
   cron-mode exit-code translator, signal-handler setup (if any), the
   logging configuration block that responds to `--verbose`/`--quiet`/
   `--log-format`.
2. Move them into `blackvuesync/__main__.py`. Replace the Task-1 stub. The
   file should now be the real entry point:

   ```python
   """command-line entry point for blackvuesync."""
   import argparse
   import logging
   import signal
   import sys

   from blackvuesync.sync import run_sync
   # ... other imports as needed

   def parse_args() -> argparse.Namespace:
       # ... full argparse setup moved here
       ...

   def configure_logging(args: argparse.Namespace) -> None:
       # ... existing logging setup
       ...

   def main() -> int:
       args = parse_args()
       configure_logging(args)
       # ... cron-mode handling, signal handlers, etc.
       return run_sync(args)

   if __name__ == "__main__":
       sys.exit(main())
   ```

3. Update the argparse `epilog` URL to the fork's issues page (this was
   already done in PR #2, so it should carry over -- but verify).
4. In `blackvuesync.py`, the file should now be reduced to a thin shim that
   re-exports for backward compatibility plus the legacy entry point:

   ```python
   """legacy entry point shim; the canonical module is `blackvuesync` (package)."""
   from blackvuesync.__main__ import main
   from blackvuesync.sync import *  # noqa: F401,F403 -- backward-compat re-exports
   from blackvuesync.metrics import *  # noqa: F401,F403

   if __name__ == "__main__":
       import sys
       sys.exit(main())
   ```

   This keeps `./blackvuesync.py ...` working as a transitional invocation
   while the Dockerfile and `blackvuesync.sh` get rewired in Task 6.
5. Run the full unit test suite:

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
6. Smoke-test the CLI entry point:

   ```bash
   python -m blackvuesync --help
   ```

   Expected: the same help text as `./blackvuesync.py --help`.
7. Smoke-test the legacy entry point:

   ```bash
   python ./blackvuesync.py --help
   ```

   Expected: same output.
8. Commit:

   ```bash
   git add blackvuesync/__main__.py blackvuesync.py
   git commit -m "Move CLI dispatch into blackvuesync.__main__"
   ```

---

## Task 5: Update pyproject.toml entry points

Files:

- Modify: `pyproject.toml`

Steps:

1. Change the `[project.scripts]` section. Currently:

   ```toml
   [project.scripts]
   blackvuesync = "blackvuesync:main"
   ```

   Update to:

   ```toml
   [project.scripts]
   blackvuesync = "blackvuesync.__main__:main"
   ```
2. Relax the `max-module-lines` constraint that was set for the single-file
   project. Current:

   ```toml
   [tool.pylint.design]
   max-module-lines = 1200  # single-file project by design
   ```

   Update to:

   ```toml
   [tool.pylint.design]
   max-module-lines = 800  # per-module ceiling; package layout
   ```

   (800 is a generous ceiling; `sync.py` should land well under that.)
3. Re-install in editable mode so the new entry point is registered:

   ```bash
   pip install -e ".[dev]"
   ```
4. Smoke-test the console-script entry:

   ```bash
   blackvuesync --help
   ```

   Expected: same help text as before.
5. Commit:

   ```bash
   git add pyproject.toml
   git commit -m "Update entry points and pylint constraints for package layout"
   ```

---

## Task 6: Update Docker invocation

Files:

- Modify: `blackvuesync.sh`
- Modify: `Dockerfile` (if any path changes are needed; usually not)

Steps:

1. In `blackvuesync.sh`, find the line that invokes `blackvuesync.py` with the
   translated CLI flags. Change the invocation from:

   ```sh
   exec /usr/local/bin/blackvuesync.py "${args[@]}"
   ```

   (or whatever the current path is) to:

   ```sh
   exec python3 -m blackvuesync "${args[@]}"
   ```
2. If the `Dockerfile` `COPY blackvuesync.py ...` line references the file by
   path, change it to copy the package directory:

   ```dockerfile
   COPY blackvuesync /app/blackvuesync
   ```

   instead of:

   ```dockerfile
   COPY blackvuesync.py /usr/local/bin/blackvuesync.py
   ```

   and remove the chmod step. The `WORKDIR /app` (or equivalent) plus
   `PYTHONPATH=/app` will make the package importable.
3. Build the image locally:

   ```bash
   docker build -t blackvuesync:phase-a .
   ```
4. Smoke-test the image runs and the entry point is callable:

   ```bash
   docker run --rm blackvuesync:phase-a python -m blackvuesync --help
   ```

   Expected: help text.
5. Run the Behave docker-mode suite (this is the most important verification --
   it exercises the full container including cron):

   ```bash
   behave -D implementation=docker -D image_name=blackvuesync:phase-a
   ```

   Expected: all scenarios pass.
6. Commit:

   ```bash
   git add blackvuesync.sh Dockerfile
   git commit -m "Invoke the package entry point in Docker and shell wrapper"
   ```

---

## Task 7: Update test imports to point at the package

Files:

- Modify: `test/blackvuesync_test.py`

Steps:

1. The tests currently import from the top-level `blackvuesync` module. With
   the re-exports in `blackvuesync.py`, they keep working but reach the wrong
   symbol address (going through the shim). For clarity and future
   maintenance, update imports to point at the package modules directly.
2. Find the import block(s) at the top of `test/blackvuesync_test.py`. Change
   things like:

   ```python
   from blackvuesync import (
       parse_recording_filename,
       SyncMetrics,
       # ...
   )
   ```

   to module-aware imports:

   ```python
   from blackvuesync.sync import parse_recording_filename
   from blackvuesync.metrics import SyncMetrics
   ```

   (Use the actual symbol names; the example is illustrative.)
3. Run the full unit test suite:

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
4. Commit:

   ```bash
   git add test/blackvuesync_test.py
   git commit -m "Point unit-test imports at package submodules"
   ```

---

## Task 8: Delete the legacy `blackvuesync.py` shim

Files:

- Delete: `blackvuesync.py`

Steps:

1. Confirm nothing imports from `blackvuesync.py` anymore except via the
   `blackvuesync` package (which is now a directory, not the file):

   ```bash
   grep -rn 'import blackvuesync' --include='*.py' .
   grep -rn 'from blackvuesync ' --include='*.py' .
   ```

   Both should show only imports of the package or its submodules, not of
   the legacy file directly.
2. Confirm the Behave features don't `exec` the script by absolute path or
   `python blackvuesync.py`:

   ```bash
   grep -rn 'blackvuesync.py' features/
   ```

   If matches exist in step definitions, update them to use
   `python -m blackvuesync` or the `blackvuesync` console script.
3. Delete the shim:

   ```bash
   git rm blackvuesync.py
   ```
4. Run the full unit suite:

   ```bash
   pytest test/blackvuesync_test.py -v
   ```

   Expected: all pass.
5. Run the in-process Behave suite:

   ```bash
   behave
   ```

   Expected: all scenarios pass.
6. Commit:

   ```bash
   git commit -m "Delete legacy blackvuesync.py shim"
   ```

---

## Task 9: Full verification, push, and PR

Steps:

1. Confirm the working tree is clean and all commits are in order:

   ```bash
   git status
   git log --oneline -10
   ```

   Expected: clean tree; last ~9 commits each describe one task.
2. Run the full combined coverage script:

   ```bash
   ./coverage.sh
   ```

   Expected: HTML report generated; no failed tests; combined coverage at or
   above the current baseline (~95% on the moved code).
3. Push the branch:

   ```bash
   git push -u origin web-foundation-phase-a
   ```

   (Branch name suggested: `web-foundation-phase-a`. Pre-commit's
   `no-commit-to-branch` permits this since it doesn't match `main`,
   `features/*`, or `releases/*`.)
4. Open the PR via `gh pr create --repo tekgnosis-net/blackvuesync ...`
   with this title:

   ```
   Web Foundation Phase A: package refactor (no behavior change)
   ```

   Body should mention:
   - Implements Phase A of [`web-foundation-phase-a.md`](docs/plans/2026-05-18-web-foundation-phase-a.md)
   - Strictly mechanical: every test passes without modification to test logic
   - The CLI surface is unchanged
   - The Docker image is unchanged in behavior (cron still drives sync)
5. Wait for all five required CI checks to pass:
   `pre-commit`, `unit-tests`, `integration-tests`, `test`,
   `SonarCloud Code Analysis`.
6. After review, **Squash** or **Rebase** merge (the branch-protection rule
   requires linear history; merge-commit is disabled).

---

## Verification checklist (before opening PR)

Run each manually and confirm:

- [ ] `python -m blackvuesync --help` shows the same help text as
  `./blackvuesync.py --help` did before the refactor (compare with
  `git show HEAD~9:blackvuesync.py | head -100` if needed).
- [ ] `blackvuesync --help` (console script) shows the same help text.
- [ ] `pytest test/blackvuesync_test.py -v` -- all green.
- [ ] `behave --no-capture` -- all green.
- [ ] `behave -D implementation=docker` -- all green (this is the most
  load-bearing check; it builds the image and runs the full integration
  suite against the container).
- [ ] `./coverage.sh` -- generates a report; combined line coverage at or
  above the baseline. Inspect `coverage_report/index.html` for any
  unexpected gaps.
- [ ] `grep -rn 'blackvuesync.py' .` returns no references in source code
  (only in `docs/` or `.git/`-internal paths).
- [ ] `pip show blackvuesync` shows version `2.3.0a0`.
- [ ] No new runtime dependencies in `pyproject.toml` (this phase adds none).

---

## After Phase A merges

1. **Write the Phase B plan** (`docs/plans/2026-05-18-web-foundation-phase-b.md`)
   covering the `SettingsStore` introduction. Phase B's plan should treat the
   package layout as the baseline.
2. Subsequent phases (C: server skeleton, D: ProgressPublisher,
   E: APScheduler and Dockerfile, F: API surface, G: polish) each get their
   own plan written when that phase is approached. The reason for the
   per-phase approach: learnings from each phase often invalidate speculative
   detail in subsequent phases.
3. The design spec
   ([`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md))
   remains the single source of truth for architectural choices across all
   phases.

---

## Self-review

Done inline during drafting:

- **Spec coverage:** Phase A's job per the design spec was "refactor
  `blackvuesync.py` into `blackvuesync/` package without behavior change."
  Every part of that scope is covered: package skeleton (Task 1), metrics
  extraction (Task 2), sync core extraction (Task 3a-d), CLI dispatch
  (Task 4), entry-point rewiring (Tasks 5-6), test imports (Task 7),
  legacy shim removal (Task 8), verification (Task 9).
- **Placeholder scan:** Tasks reference *categories* of code (e.g.,
  "the failure-reason classifier helper") without inventing function names
  that may not exist verbatim today. This is intentional -- the engineer
  is expected to read the current `blackvuesync.py` to find the actual
  symbol names. The plan is detailed enough to execute from but doesn't
  invent type signatures that should be discovered.
- **Type consistency:** Module names (`sync`, `metrics`, `__main__`),
  the package name (`blackvuesync`), and the entry-point name
  (`blackvuesync.__main__:main`) are consistent across tasks.
- **Scope:** Strictly Phase A. No anticipatory work for Phase B-G is
  included. The "out of scope" list at the top makes this explicit.

One known limitation: the existing `blackvuesync.py` symbols are not
enumerated in this plan because doing so would require reading the file
exhaustively at plan-write time and could go stale. The plan trusts the
engineer to read the current file when each task starts. This is acceptable
for a refactor where the source is the canonical reference.
