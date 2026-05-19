# Web Foundation -- Phase E: APScheduler + cron retirement

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 -->

**Date:** 2026-05-19
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Section 1 process model; Section 2 schedule settings)
**Phase:** E of 7 (A-D done; F-G to follow).

**Goal:** Retire the Alpine cron daemon and the env-var-to-CLI shell shims.
Run sync inside the long-running web-server process via APScheduler. The
container's PID 1 becomes Python; the only entry point is
`python -m blackvuesync serve`. Both the scheduled job and the on-demand
`POST /api/sync/now` funnel through `trigger_sync()` so the existing
`_sync_lock` is the single source of mutual exclusion.

**Architecture:** A new module `blackvuesync/server/scheduler.py` exposes
`init_scheduler(settings_store, publisher) -> BackgroundScheduler` that
builds a `BackgroundScheduler` with `ThreadPoolExecutor(max_workers=1)`, adds
one job that calls `trigger_sync(settings_store.get(), publisher)` using
`CronTrigger.from_crontab(cron_expression, timezone=...)`, and registers a
`SettingsStore.on_change` listener that calls `scheduler.reschedule_job(...)`
when `schedule.cron_expression` or `schedule.timezone` change. `cmd_serve`
starts the scheduler before Waitress and shuts it down on graceful exit.
The Dockerfile gets a 5KB `su-exec` package; `entrypoint.sh` shrinks to a
~10-line shim that does the PUID/PGID remap and execs Python as the
`dashcam` user. `blackvuesync.sh`, `crontab`, the `CRON` env var, and the
`RUN_ONCE` env var are deleted.

**Out of scope (do not touch):**

- `sync.py` cognitive-complexity decomposition of `download_file` and
  `download_recording`. The Phase D carry-forward findings stay; a separate
  cleanup PR after Phase E ships will handle them. (Karpathy guideline #3:
  touch only what you must.)
- Settings UI for editing the cron expression. The settings store already
  validates `cron_expression`; the UI lives in sub-project #3.
- Multi-stage Dockerfile rebuild, base-image swap, or USER directive.
  Phase E preserves the Alpine base and the `dashcam`-user model.
- Persistent APScheduler job store. The default in-memory store is correct
  for a single-process service; persistence is unnecessary because the cron
  schedule is regenerated from `settings.json` on every start.

---

## Implementer guidelines (karpathy discipline)

The implementer subagent for this phase must internalize:

1. **Think before coding.** State assumptions explicitly. If a step is
   ambiguous, stop and ask in DONE_WITH_CONCERNS rather than picking
   silently.
2. **Simplicity first.** Minimum code that solves the problem. No
   speculative configurability. No error handling for scenarios that
   cannot happen.
3. **Surgical changes.** Touch only the files this plan lists. Do not
   "improve" adjacent code, comments, formatting, or unrelated functions.
   If you notice unrelated dead code or smells, mention them in the
   completion report -- do not delete or refactor them.
4. **Goal-driven execution.** Each task has a verifiable check. Run the
   check before claiming the task is done.

Process hygiene (still mandatory):

- Never use `git add -A` or `git add .`. List files explicitly to avoid
  sweeping up the developer-local `supertool` symlink.
- Never use `--no-verify`. Pre-commit hooks must pass.
- Never amend an existing commit after a pre-commit auto-fix. Create a
  new commit.
- Comments are lowercase, third-person, non-obvious. Entity names keep
  their casing. "TODO" stays all-caps.

---

## File Structure

### Files to create

- `blackvuesync/server/scheduler.py` -- `init_scheduler()`, `_build_trigger()`,
  `_on_settings_change()` listener.
- `test/test_scheduler.py` -- unit tests for the scheduler module.
- `entrypoint.sh` will be rewritten in place (not strictly "created"; see
  Task 7).

### Files to modify

- `pyproject.toml` -- add `APScheduler~=3.10` to `dependencies`; bump
  version to `2.3.0a1`.
- `blackvuesync/__main__.py` -- `cmd_serve` starts the scheduler, blocks
  on Waitress, shuts the scheduler down on exit.
- `Dockerfile` -- `apk add` line adds `su-exec`; `ENV` block removes
  `CRON=1` and `RUN_ONCE=""`; the `COPY crontab` and `COPY blackvuesync.sh`
  lines are deleted; `ENTRYPOINT` unchanged (still `["/entrypoint.sh"]`).
- `entrypoint.sh` -- rewritten to ~10 lines: source `setuid.sh`, exec
  `su-exec dashcam python -m blackvuesync serve`.
- `docker-compose.yml` -- remove `CRON` and `RUN_ONCE` env vars; add a
  comment noting that sync is now web-triggered or scheduler-driven.
- `run.sh` -- replace `RUN_ONCE=1 DRY_RUN=1` invocation with a CMD
  override that runs `python -m blackvuesync sync --dry-run ...` once
  and exits.
- `README.md` -- one paragraph noting removal of `CRON`/`RUN_ONCE` env
  vars, and that on-demand sync is now `POST /api/sync/now`.
- `crontab` -- DELETE.
- `blackvuesync.sh` -- DELETE.

### Files explicitly NOT to modify

- `blackvuesync/sync.py`
- `blackvuesync/metrics.py`
- `blackvuesync/server/auth.py`, `progress.py`, `sync_runner.py`,
  `__init__.py`, `routes/*` (any of them).
- Any test file other than `test/test_scheduler.py`.

---

## Task 1: Add APScheduler dependency

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Add APScheduler to dependencies**

In `pyproject.toml` `[project] dependencies =`, add a line so the block
becomes:

```toml
dependencies = [
    "Flask~=3.1",
    "Flask-WTF~=1.2",
    "waitress~=3.0",
    "argon2-cffi~=23.1",
    "APScheduler~=3.10",
]
```

- [ ] **Step 2: Bump version**

Change `version = "2.3.0a0"` to `version = "2.3.0a1"`.

- [ ] **Step 3: Update editable install**

Run: `pip install -e ".[dev]"`
Expected: APScheduler resolves and installs without conflicts.

- [ ] **Step 4: Confirm import**

Run: `python -c "from apscheduler.schedulers.background import BackgroundScheduler; from apscheduler.triggers.cron import CronTrigger; print(CronTrigger.from_crontab('*/15 * * * *'))"`
Expected: prints a `<CronTrigger ...>` repr; no exception.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "Phase E: add APScheduler dependency"
```

---

## Task 2: Write failing test for scheduler init

**Files:**

- Create: `test/test_scheduler.py`

- [ ] **Step 1: Create the test file with a single failing test**

```python
"""tests for the APScheduler integration in blackvuesync.server.scheduler."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server.scheduler import init_scheduler
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def _make_store(settings_path: Path) -> SettingsStore:
    """creates a SettingsStore with a dummy address."""
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


class TestInitScheduler:
    """tests for init_scheduler()."""

    def test_returns_running_scheduler_with_one_job(
        self, settings_path: Path
    ) -> None:
        store = _make_store(settings_path)
        publisher = object()  # any sentinel; scheduler should not call it at init
        scheduler = init_scheduler(store, publisher)
        try:
            assert scheduler.running is True
            jobs = scheduler.get_jobs()
            assert len(jobs) == 1
            assert jobs[0].id == "sync"
        finally:
            scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest test/test_scheduler.py::TestInitScheduler::test_returns_running_scheduler_with_one_job -v`
Expected: FAIL with `ImportError` -- `blackvuesync.server.scheduler` does not exist yet.

---

## Task 3: Create scheduler.py with init_scheduler

**Files:**

- Create: `blackvuesync/server/scheduler.py`

- [ ] **Step 1: Write the scheduler module**

```python
"""APScheduler integration: cron-triggered sync inside the long-running web service."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.sync_runner import trigger_sync
from blackvuesync.settings import Settings, SettingsStore

logger = logging.getLogger(__name__)

# the single job id; reused by reschedule_job and remove_job.
_JOB_ID = "sync"


def _build_trigger(settings: Settings) -> CronTrigger:
    """builds a CronTrigger from the schedule section of settings."""
    return CronTrigger.from_crontab(
        settings.schedule.cron_expression,
        timezone=settings.schedule.timezone,
    )


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


def init_scheduler(
    store: SettingsStore, publisher: ProgressPublisher
) -> BackgroundScheduler:
    """initializes and starts a BackgroundScheduler with one cron-triggered job.

    the scheduler uses a single-thread executor so concurrent fires (e.g. on
    schedule transitions) cannot overlap. `max_instances=1` and
    `coalesce=True` further enforce that a backlog of missed runs collapses
    to one. the cron expression and timezone are read from settings at init,
    and a SettingsStore on_change listener reschedules the job in-place when
    those fields change.
    """
    scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        timezone=store.get().schedule.timezone,
    )
    scheduler.add_job(
        _scheduled_run,
        trigger=_build_trigger(store.get()),
        id=_JOB_ID,
        args=(store, publisher),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    def _on_change(old: Settings, new: Settings) -> None:
        """reschedules the sync job when schedule settings change."""
        if old.schedule == new.schedule:
            return
        logger.info(
            "rescheduling sync job: %r/%s -> %r/%s",
            old.schedule.cron_expression,
            old.schedule.timezone,
            new.schedule.cron_expression,
            new.schedule.timezone,
        )
        scheduler.reschedule_job(_JOB_ID, trigger=_build_trigger(new))

    store.on_change(_on_change)
    scheduler.start()
    return scheduler


__all__ = ["init_scheduler"]
```

- [ ] **Step 2: Run the first test to confirm it passes**

Run: `pytest test/test_scheduler.py::TestInitScheduler::test_returns_running_scheduler_with_one_job -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add blackvuesync/server/scheduler.py test/test_scheduler.py
git commit -m "Phase E: add APScheduler-backed scheduler module"
```

---

## Task 4: Add reschedule-on-settings-change test

**Files:**

- Modify: `test/test_scheduler.py`

- [ ] **Step 1: Add the test**

Append to the `TestInitScheduler` class:

```python
    def test_on_change_reschedules_when_cron_changes(
        self, settings_path: Path
    ) -> None:
        """changing schedule.cron_expression rebuilds the job trigger."""
        store = _make_store(settings_path)
        publisher = object()
        scheduler = init_scheduler(store, publisher)
        try:
            original_next = scheduler.get_job(_JOB_ID).trigger
            # changes cron expression from every-15-min default to every-5-min
            store.update(
                lambda s: dataclasses.replace(
                    s,
                    schedule=dataclasses.replace(
                        s.schedule, cron_expression="*/5 * * * *"
                    ),
                )
            )
            new_trigger = scheduler.get_job(_JOB_ID).trigger
            assert str(new_trigger) != str(original_next)
            assert "minute='*/5'" in str(new_trigger)
        finally:
            scheduler.shutdown(wait=False)

    def test_on_change_noop_when_schedule_unchanged(
        self, settings_path: Path
    ) -> None:
        """changing a non-schedule field does not reschedule the job."""
        store = _make_store(settings_path)
        publisher = object()
        scheduler = init_scheduler(store, publisher)
        try:
            original = scheduler.get_job(_JOB_ID).trigger
            # changes a non-schedule field
            store.update(
                lambda s: dataclasses.replace(
                    s,
                    sync=dataclasses.replace(s.sync, grouping="daily"),
                )
            )
            assert str(scheduler.get_job(_JOB_ID).trigger) == str(original)
        finally:
            scheduler.shutdown(wait=False)
```

Add the import at the top of `test/test_scheduler.py`:

```python
from blackvuesync.server.scheduler import _JOB_ID, init_scheduler
```

- [ ] **Step 2: Run the tests**

Run: `pytest test/test_scheduler.py -v`
Expected: 3 tests, all PASS.

- [ ] **Step 3: Commit**

```bash
git add test/test_scheduler.py
git commit -m "Phase E: cover reschedule-on-settings-change"
```

---

## Task 5: Add scheduled-run-skips-when-busy test

**Files:**

- Modify: `test/test_scheduler.py`

- [ ] **Step 1: Add the test**

Append to the file (top-level, not inside the class):

```python
class TestScheduledRun:
    """tests for _scheduled_run job function."""

    def test_skips_when_sync_already_running(self, settings_path: Path) -> None:
        """when trigger_sync returns already_running, _scheduled_run logs and
        does not raise."""
        from blackvuesync.server.scheduler import _scheduled_run

        store = _make_store(settings_path)
        publisher = object()

        with patch(
            "blackvuesync.server.scheduler.trigger_sync",
            return_value={"status": "already_running", "job_id": "abc123"},
        ):
            # must not raise
            _scheduled_run(store, publisher)

    def test_calls_trigger_sync_with_current_settings(
        self, settings_path: Path
    ) -> None:
        """_scheduled_run reads settings fresh from the store on each tick."""
        from blackvuesync.server.scheduler import _scheduled_run

        store = _make_store(settings_path)
        publisher = object()

        with patch(
            "blackvuesync.server.scheduler.trigger_sync",
            return_value={"status": "started", "job_id": "deadbeef"},
        ) as mock_trigger:
            _scheduled_run(store, publisher)
            mock_trigger.assert_called_once()
            settings_arg, publisher_arg = mock_trigger.call_args.args
            assert settings_arg.connection.address == "192.168.0.1"
            assert publisher_arg is publisher
```

- [ ] **Step 2: Run all scheduler tests**

Run: `pytest test/test_scheduler.py -v`
Expected: 5 tests, all PASS.

- [ ] **Step 3: Commit**

```bash
git add test/test_scheduler.py
git commit -m "Phase E: cover _scheduled_run busy-skip and settings-pull"
```

---

## Task 6: Wire scheduler into cmd_serve

**Files:**

- Modify: `blackvuesync/__main__.py`

- [ ] **Step 1: Update cmd_serve**

Replace the existing `cmd_serve` body with:

```python
def cmd_serve(args: argparse.Namespace) -> int:
    """starts the web server and the cron scheduler; blocks until interrupted."""
    # deferred imports keep these optional at module load time; the sync
    # subcommand does not need flask, waitress, or apscheduler.
    # pylint: disable=import-outside-toplevel
    import waitress

    from blackvuesync.server import create_app
    from blackvuesync.server.progress import ProgressPublisher
    from blackvuesync.server.scheduler import init_scheduler

    # pylint: enable=import-outside-toplevel

    config_path = Path(args.config_path) if args.config_path else _DEFAULT_SETTINGS_PATH
    store = SettingsStore(config_path)
    publisher = ProgressPublisher()
    app = create_app(store, progress_publisher=publisher)
    settings = store.get()
    port = args.port if args.port is not None else settings.web.port

    scheduler = init_scheduler(store, publisher)
    logger.info(
        "scheduler started: %r (%s)",
        settings.schedule.cron_expression,
        settings.schedule.timezone,
    )
    logger.info("starting web server on 0.0.0.0:%d", port)
    try:
        waitress.serve(app, host="0.0.0.0", port=port)
    finally:
        # waits for the active sync (if any) to finish gracefully on SIGTERM.
        scheduler.shutdown(wait=True)
    return 0
```

- [ ] **Step 2: Confirm imports are still ordered correctly**

Run: `pre-commit run --files blackvuesync/__main__.py`
Expected: PASS (ruff isort may need a re-run; if so, accept the auto-fix).

- [ ] **Step 3: Smoke-test the serve command**

Run, in one terminal:

```bash
BLACKVUESYNC_CONFIG_PATH=/tmp/bvs-test-settings.json \
ADDRESS=192.168.0.1 \
python -m blackvuesync serve
```

Expected: log line `scheduler started: '*/15 * * * *' (UTC)` followed by
`starting web server on 0.0.0.0:8080`. Curl `http://localhost:8080/healthz`
in another terminal -> `ok`. `Ctrl-C` shuts down cleanly with no traceback.

Then clean up: `rm -f /tmp/bvs-test-settings.json`.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/__main__.py
git commit -m "Phase E: start scheduler from cmd_serve"
```

---

## Task 7: Rewrite entrypoint.sh and delete the cron shell shims

**Files:**

- Modify: `entrypoint.sh`
- Delete: `blackvuesync.sh`, `crontab`

- [ ] **Step 1: Rewrite entrypoint.sh**

Replace the contents of `entrypoint.sh` with exactly:

```bash
#!/usr/bin/env bash
# entrypoint: remaps the dashcam user to PUID/PGID if set, then execs the
# long-running web service as the dashcam user via su-exec (Alpine's lighter
# alternative to gosu).
set -eu

/setuid.sh

exec su-exec dashcam python -m blackvuesync serve
```

- [ ] **Step 2: Delete the cron-era shims**

```bash
git rm blackvuesync.sh crontab
```

- [ ] **Step 3: Commit**

```bash
git add entrypoint.sh
git commit -m "Phase E: rewrite entrypoint, delete cron-era shell shims"
```

---

## Task 8: Update Dockerfile

**Files:**

- Modify: `Dockerfile`

- [ ] **Step 1: Add `su-exec` to the apk install line**

Change:

```dockerfile
RUN apk add --update bash python3 shadow tzdata \
    && rm -rf /var/cache/apk/* \
    && useradd -UMr dashcam
```

To:

```dockerfile
RUN apk add --update bash python3 shadow su-exec tzdata \
    && rm -rf /var/cache/apk/* \
    && useradd -UMr dashcam
```

- [ ] **Step 2: Remove deleted-file COPY lines**

Delete these two lines:

```dockerfile
COPY entrypoint.sh /entrypoint.sh
COPY crontab /var/spool/cron/crontabs/dashcam
```

...and replace with:

```dockerfile
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
```

(setuid.sh is still copied; that line stays unchanged.)

Also delete:

```dockerfile
COPY --chown=dashcam blackvuesync.sh /blackvuesync.sh
RUN chmod +x /blackvuesync.sh
```

- [ ] **Step 3: Trim the ENV block**

Remove the `CRON=""` and `RUN_ONCE=""` lines from the `ENV` block. (Keep
the other env vars; they still seed settings.json on first run.)

The trailing `AFFINITY_KEY=""` line needs its preceding backslash either
preserved or removed depending on position; ensure the block remains
syntactically valid Docker.

- [ ] **Step 4: Verify the image builds**

Run: `docker build -t blackvuesync-phase-e-local .`
Expected: build completes; no errors. The image now contains `su-exec` and
no longer contains `blackvuesync.sh` or `/var/spool/cron/crontabs/dashcam`.

- [ ] **Step 5: Smoke-test the image**

```bash
docker run --rm \
  -e ADDRESS=192.0.2.1 \
  -p 18080:8080 \
  -v "$(mktemp -d):/recordings" \
  -v "$(mktemp -d):/config" \
  --name bvs-e-smoke \
  blackvuesync-phase-e-local
```

In another terminal: `curl -fs http://localhost:18080/healthz` -> `ok`.
Then `docker stop bvs-e-smoke`. The container must exit cleanly within
~5 seconds (Waitress shuts down, scheduler shuts down).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile
git commit -m "Phase E: add su-exec, drop cron-era image artifacts"
```

---

## Task 9: Update docker-compose.yml

**Files:**

- Modify: `docker-compose.yml`

- [ ] **Step 1: Remove `CRON` and `RUN_ONCE` env vars**

Delete the `CRON: 1` line and the comment block above it. Delete any
`RUN_ONCE` reference. Add a one-line comment near the `environment:` block:

```yaml
      # Sync is scheduler-driven via settings.schedule.cron_expression (default
      # */15 * * * *). On-demand triggers go through POST /api/sync/now.
```

- [ ] **Step 2: Sanity-check the compose file parses**

Run: `docker compose -f docker-compose.yml config > /dev/null`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "Phase E: drop CRON/RUN_ONCE from docker-compose.yml"
```

---

## Task 10: Update run.sh local smoke test

**Files:**

- Modify: `run.sh`

- [ ] **Step 1: Read the current run.sh**

(The implementer should `cat run.sh` first to confirm structure.)

- [ ] **Step 2: Replace the run invocation**

The current invocation sets `DRY_RUN=1 RUN_ONCE=1` and relies on
`entrypoint.sh` exiting after one sync. After Phase E, `RUN_ONCE` is
meaningless. Replace the `docker run` line so it overrides the CMD:

```bash
docker run --rm \
  -e ADDRESS="$ADDRESS" \
  -e DRY_RUN=1 \
  -v "$RECORDINGS_DIR:/recordings" \
  -v "$CONFIG_DIR:/config" \
  "$IMAGE" \
  python -m blackvuesync sync --dry-run "$ADDRESS" --destination /recordings
```

(The exact env-var and volume names depend on what `run.sh` currently
declares; preserve those names.)

Add a comment near the override:

```bash
# overrides the default CMD (`python -m blackvuesync serve`) so this smoke
# test exits after a single sync attempt, replacing the retired RUN_ONCE=1
# entrypoint shortcut.
```

- [ ] **Step 3: Commit**

```bash
git add run.sh
git commit -m "Phase E: fix run.sh smoke test for CMD-override mode"
```

---

## Task 11: Update README

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Find the env-var reference table**

(The implementer should `grep -n CRON\\|RUN_ONCE README.md` first.)

- [ ] **Step 2: Remove the `CRON` and `RUN_ONCE` rows**

Delete the table rows describing `CRON` and `RUN_ONCE`. If those env vars
are referenced anywhere else in prose (examples, troubleshooting), edit
those references too.

- [ ] **Step 3: Add a brief note**

Near the top of the "Configuration" or equivalent section, add:

> Sync is now scheduler-driven inside the long-running web service. The
> `CRON` and `RUN_ONCE` environment variables of the cron-era image have
> been retired. To trigger an on-demand sync, POST to `/api/sync/now`.
> To change the schedule, edit `settings.schedule.cron_expression` in
> `settings.json` (default `*/15 * * * *`).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Phase E: document cron-env-var retirement in README"
```

---

## Task 12: Final test sweep

- [ ] **Step 1: Full unit test suite**

Run: `pytest test/ -v`
Expected: all tests pass, including the 5 new tests in `test_scheduler.py`.

- [ ] **Step 2: Behave (subprocess mode)**

Run: `behave`
Expected: all scenarios pass.

- [ ] **Step 3: Behave (docker mode)**

Run: `behave -D implementation=docker`
Expected: all scenarios pass against the Phase E image. The cron-era
docker scenarios that exercised the 15-minute cron tick should either
continue to pass (because the scheduler still fires at that cadence) or
have been updated upstream in Phase A/B to not depend on cron firing.
If any docker scenario fails because it expected the container to exit
after one sync (RUN_ONCE behavior), report it -- do not silently change
behavior to match.

- [ ] **Step 4: Pre-commit**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 5: Coverage**

Run: `./coverage.sh`
Expected: `coverage_report/index.html` generated; total coverage stays
>= 85% with no regression vs. Phase D baseline.

---

## Task 13: Open the PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin web-foundation-phase-e
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --base main \
  --head web-foundation-phase-e \
  --title "Web Foundation Phase E: APScheduler + cron retirement" \
  --body "$(cat <<'EOF'
## Summary
- Adds APScheduler 3.10 dep; new `blackvuesync/server/scheduler.py` runs sync inside the long-running web service via `BackgroundScheduler` + `CronTrigger.from_crontab(...)`, sharing `_sync_lock` with `/api/sync/now`.
- Reschedules the job in-place when `settings.schedule.cron_expression` or `schedule.timezone` change via SettingsStore.on_change (TIER `next_tick`).
- Retires the Alpine cron daemon: deletes `blackvuesync.sh`, `crontab`; rewrites `entrypoint.sh` to a 10-line PUID/PGID shim that execs `python -m blackvuesync serve` via `su-exec`.
- Removes the `CRON` and `RUN_ONCE` env vars from the Dockerfile and docker-compose.yml; updates `run.sh` smoke test to override the CMD instead of relying on `RUN_ONCE`.

## Out of scope (deferred)
- `sync.py:download_file` and `sync.py:download_recording` S3776 cognitive-complexity findings carry forward to a separate cleanup PR.

## Test plan
- [ ] Unit tests: `pytest test/ -v` (5 new tests in `test_scheduler.py`)
- [ ] Behave subprocess: `behave`
- [ ] Behave docker: `behave -D implementation=docker`
- [ ] Manual: `docker run` the new image, hit `/healthz`, watch a scheduled tick fire, hit `POST /api/sync/now` and confirm 202; second immediate POST -> 409; SIGTERM -> clean shutdown.
EOF
)"
```

- [ ] **Step 3: Wait for the 5 required checks**

`pre-commit`, `unit-tests`, `integration-tests`, `test`,
`SonarCloud Code Analysis`. Address any failures before merge.

- [ ] **Step 4: Squash-merge once green**

(The controlling agent shepherds this; do not auto-merge from the
implementer subagent.)

---

## Self-review against spec

| Spec requirement (Section 1/2) | Plan task |
| --- | --- |
| BackgroundScheduler + ThreadPoolExecutor(max_workers=1) | Task 3 |
| One job, CronTrigger.from_crontab(cron_expression, timezone=...) | Task 3 |
| max_instances=1, coalesce=True | Task 3 |
| `python -m blackvuesync serve` is the only container entry | Task 6 + Task 7 |
| Removes `entrypoint.sh`, `crontab`, `blackvuesync.sh` | Tasks 7 + 8 |
| Drops `CRON` and `RUN_ONCE` env vars with one-time warning | Tasks 8 + 9 + 11 (warning is in the README note; no runtime warning needed since env vars no longer touched) |
| Graceful shutdown: scheduler.shutdown(wait=True) | Task 6 |
| TIER `next_tick` for `schedule.*` -> reschedule on change | Task 3 (on_change listener) |
| `cron_expression` is the only schedule representation | Task 3 + already in settings.py |
| Settings.json remains canonical; env vars seed only | already in Phase B; unchanged |

## What is NOT done in Phase E (recap)

- `sync.py` decomposition (S3776 carry-forward) -- separate PR after this.
- Web UI for editing the cron schedule -- sub-project #3.
- Persistent APScheduler job store -- not needed (schedule regenerates from settings.json).
- Dockerfile base-image swap or multi-stage rebuild -- Phase G if it happens at all.
