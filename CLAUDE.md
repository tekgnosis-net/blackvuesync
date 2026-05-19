# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BlackVue Sync is a single-file Python utility that synchronizes recordings from BlackVue dashcams to a local directory over HTTP. The project emphasizes simplicity and portability with zero third-party dependencies, packaged both as a standalone script and a Docker container.

This project is a fork on GitHub: <https://github.com/tekgnosis-net/blackvuesync> (upstream: <https://github.com/acolomba/blackvuesync>)

## Claude Code

- Prefer using the LSP plugin over textual search.
- When a sandbox operation fails, stop to ask the user. Avoid disabling the sandbox.
- Create plans under the `docs/plans/` directory.

## Development Setup

The project uses `pyproject.toml` for dependency management. Development dependencies (black, Flask, pre-commit, pytest) are defined as optional dependencies.

### Setup

Create a virtual environment and install development dependencies:

```bash
# create virtual environment
python3 -m venv venv

# activate it
source venv/bin/activate

# install package in editable mode with dev dependencies
pip install -e ".[dev]"

# install pre-commit hooks
pre-commit install

# install commit-msg hook for gitlint
pre-commit install --hook-type commit-msg
```

The `-e` flag installs in editable mode, so changes to `blackvuesync.py` take effect immediately without reinstalling.

Pre-commit hooks will automatically run on `git commit` to check code quality, format code, and scan for secrets. The hooks include Black, shellcheck, yamllint, trufflehog, and others.

## Guidelines

### Comments

Python docstrings and inline code comments in Python, YAML, shell, etc. are lowercase. The word "TODO" remains all-caps. Entities such as file names etc. preserve their casing.

Comments must be in the third-person, e.g. "installs", not "install", because they are descriptive. Avoid the imperative.

Keep comments concise, and non-obvious. Avoid documenting what everybody is expected to know.

### Python

Prefer Python idiomatic ("pythonic") style.

Always use type annotations.

### Code Formatting

Code formatting is handled automatically by pre-commit hooks (Black for Python, yamlfmt for YAML).

### Git

- Git commit messages must be longer than 5 characters, and each line must be less than 80.
- You can expect pre-commit hooks to fail when attempting to commit. Fix the errors.
- NEVER use `--no-verify` to skip the hooks.

## Architecture

### Package Design

The application is a Python package under `blackvuesync/`. Core modules:

- `sync.py` -- filename regex, dashcam HTTP client, download/resume, retention,
  locking. The primary sync logic; kept self-contained for portability.
- `metrics.py` -- `SyncMetrics` dataclass and Prometheus text emission.
- `settings.py` -- `Settings` frozen-dataclass tree, `SettingsStore` with atomic
  JSON persistence, per-section validators, env-var bootstrap, and schema
  migration. See "Settings" section below.
- `__main__.py` -- CLI entry point; wires together the above at startup.

### Settings

`SettingsStore` persists configuration to `/config/settings.json` (override via
`BLACKVUESYNC_CONFIG_PATH` env var). Env vars are **seed-only**: they populate
the file on first run; subsequent runs read the file and ignore env vars. The
file has `0600` permissions and `SettingsStore` refuses to load if the mode is
wider than that.

Settings are organized into nine frozen-dataclass sections:

| Section | TIER | Key fields |
| --- | --- | --- |
| connection | restart | address, timeout_seconds |
| schedule | next_tick | cron_expression, timezone |
| sync | next_tick | priority, grouping, include, exclude, retry_failed_after, skip_metadata |
| retention | next_tick | keep, max_used_disk_percent |
| logging | immediate | verbose, quiet, format |
| metrics | immediate | file, pushgateway_url, state_file |
| web | restart | port, session_lifetime_hours |
| auth | immediate | mode, username, password_hash, session_secret, trusted_proxies |
| system | restart | destination, dry_run |

`TIER` (`immediate` / `next_tick` / `restart`) indicates how quickly a change
propagates once the web UI exists. All mutations go through `SettingsStore.update()`
which validates, saves atomically, and fires change-listener callbacks.

### Core Flow

1. **Lock acquisition**: Uses file locking (`fcntl`) to prevent concurrent runs on the same destination
2. **Destination preparation**: Creates directories, removes outdated recordings based on retention policy
3. **Dashcam communication**: HTTP requests to `blackvue_vod.cgi` endpoint to list recordings
4. **Recording parsing**: Filename-based extraction of metadata (date, type, direction)
5. **Download with resume**: Uses temporary dotfiles (`.filename.mp4`) for partial downloads
6. **Cleanup**: Removes temp files and empty grouping directories

### Recording Types

The filename regex (`filename_re`) parses BlackVue recording filenames to extract:

- **Timestamp**: `YYYYMMDD_HHMMSS`
- **Type**: N=Normal, E=Event, P=Parking, M=Manual, I=Impact, O=Overspeed, A=Acceleration, T=Cornering, B=Braking, R/X/G=Geofence, D/L/Y/F=DMS
- **Direction**: F=Front, R=Rear, I=Interior, O=Optional
- **Upload flag**: L=Live, S=Substream (optional)

Each recording consists of multiple files: `.mp4` (video), `.thm` (thumbnail), `.3gf` (accelerometer), `.gps` (GPS data).

### Grouping

Recordings can be organized into date-based directories (`--grouping`):

- `daily`: YYYY-MM-DD
- `weekly`: YYYY-MM-DD (Monday of week)
- `monthly`: YYYY-MM
- `yearly`: YYYY

Grouping speeds up loading in BlackVue Viewer and keeps directories manageable.

### Server Package

The web server lives under `blackvuesync/server/`. It is a standard Flask
application, structured as follows:

- `__init__.py` -- `create_app(settings_store, testing=False)` factory.
  Configures Flask-WTF CSRF protection, ProxyFix middleware, session cookie
  settings, and attaches `settings_store` to the app instance. Registers the
  three blueprints and adds the `add_security_headers` after-request hook that
  injects CSP, X-Frame-Options, HSTS, and related headers on every response.
- `auth.py` -- Argon2id password hashing helpers (`hash_password`,
  `verify_password`, `needs_rehash`) and the `login_required` decorator.
  Maintains an in-memory sliding-window rate limiter (10 failures per 600 s →
  15-minute lockout) guarded by a `threading.Lock`. Auth mode is read fresh from
  `current_app.settings_store` on every request, so a mode change takes effect
  immediately without a restart.
- `routes/health.py` -- `GET /healthz` (always 200) and `GET /readyz` (200 when
  settings store is loaded, 503 while starting).
- `routes/auth.py` -- `GET|POST /login`, `POST /logout`, `GET|POST /first-run`.
  A `before_app_request` hook redirects every non-exempt path to `/first-run`
  while `auth.password_hash == ""` (sticky first-run flow).
- `routes/ui.py` -- Placeholder `GET` routes for `/`, `/settings`, `/logs`,
  `/stats`, `/viewer`; all protected by `@login_required`.
- `routes/api_sync.py` -- JSON API routes at `/api/sync/*`; see "Sync API"
  subsection below.
- `routes/hx_sync.py` -- HTMX fragment routes at `/hx/sync/*`; renders
  `_partials/sync_status_card.html` and `_partials/last_run_card.html`.
- `progress.py` -- `FileProgress` / `SyncProgress` frozen dataclasses and
  `ProgressPublisher`; see "Progress Publisher" subsection below.
- `sync_runner.py` -- thin wrapper that spawns `sync.sync()` on a daemon
  thread guarded by a module-level `threading.Lock`; surfaces a 409 when a
  sync is already running.

**Auth modes** (set via `settings.json` `auth.mode`):

| Mode | Behavior |
| --- | --- |
| `login` | Password auth enforced; session cookie issued on success. |
| `none` | All routes accessible without credentials (trusted-LAN use). |
| `proxy` | Reverse proxy is expected to authenticate; request is trusted. |

**Argon2 parameters are locked**: `time_cost=3, memory_cost=65536,
parallelism=4, hash_len=32, salt_len=16`. Do not change without a migration
plan.

### Progress Publisher

`ProgressPublisher` (in `server/progress.py`) owns the thread-safe sync
progress state. It is instantiated once per server process in `cmd_serve` and
attached to the Flask app as `app.progress_publisher`.

**Writer API** (called from the sync thread):

| Method | Description |
| --- | --- |
| `begin_job(files_total) -> str` | starts a job; returns `job_id` (uuid4 hex) |
| `start_file(filename, artifact, total_bytes)` | marks start of a file download |
| `update_bytes(downloaded, total_bytes=0)` | progress tick; throttled to 5 Hz for subscribers |
| `finish_file(success, reason=None)` | closes a file; bumps aggregate counts |
| `end_job(success)` | closes the job; retained for 10 s, then resets to idle |

**Reader API** (called from Flask handlers):

| Method | Description |
| --- | --- |
| `snapshot() -> SyncProgress` | returns current frozen state; safe from any thread |
| `subscribe() -> Iterator[SyncProgress]` | yields state changes; 30-second heartbeat timeout |

State transitions: `idle → running → complete/failed → idle`.
`SyncProgress` and `FileProgress` are frozen dataclasses; mutations use
`dataclasses.replace`. The `_NoopPublisher` sentinel is used in the CLI sync
path so `sync.py` stays free of Flask imports.

### Sync API

Sync-related endpoints are split between JSON API and HTMX fragments:

**JSON API** (`/api/sync/*`, all `@login_required`):

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/sync/progress` | current snapshot as JSON |
| `GET` | `/api/sync/progress/stream` | SSE stream of progress events |
| `POST` | `/api/sync/now` | trigger an on-demand sync |
| `GET` | `/api/sync/last` | last completed snapshot; 204 if never run |

`POST /api/sync/now` is CSRF-protected globally by Flask-WTF. It returns
202 + `{"job_id": ...}` or 409 + `{"code": "SYNC_ALREADY_RUNNING", ...}`.

The SSE stream emits `event: progress\ndata: <json>\n\n` frames, throttled
to 5 Hz. When no state change occurs for 30 seconds the generator emits
`": keepalive"` instead of a redundant data frame. Set `X-Accel-Buffering: no`
to prevent nginx-family proxy buffering.

**HTMX Fragments** (`/hx/sync/*`, all `@login_required`):

| Method | Path | Template |
| --- | --- | --- |
| `GET` | `/hx/sync/status-card` | `_partials/sync_status_card.html` |
| `GET` | `/hx/sync/last-run-card` | `_partials/last_run_card.html` |

### Logging

Two logger hierarchies:

- `logger`: Root logger, respects verbosity and quiet flags
- `cron_logger`: Remains active in cron mode for Normal/Manual recordings and errors

## Testing

### Test Structure

- `test/blackvuesync_test.py` -- Pytest unit tests for parsing, grouping, filtering
- `test/test_settings.py` -- unit tests for SettingsStore (100% coverage target)
- `test/test_auth.py` -- unit tests for Argon2 helpers and rate-limiter
- `test/test_routes_auth.py` -- Flask test-client tests for /login, /logout,
  /first-run (timing, rate-limit, CSRF)
- `test/test_routes_health.py` -- tests for /healthz and /readyz
- `test/test_routes_ui.py` -- tests for authenticated UI placeholder routes
- `test/test_security_headers.py` -- tests for CSP and other security headers
- `test/test_progress.py` -- unit tests for `FileProgress`, `SyncProgress`,
  `ProgressPublisher` state machine, throttle, retention, concurrency
- `test/test_sync_callback.py` -- tests for `download_file` `on_chunk` callback
- `test/test_sync_runner.py` -- tests for `trigger_sync` locking and daemon thread
- `test/test_routes_api_sync.py` -- tests for `/api/sync/*` endpoints and SSE
- `test/test_routes_hx_sync.py` -- tests for `/hx/sync/*` htmx fragment endpoints
- `features/` -- Behave BDD integration tests against a mock BlackVue dashcam

### Running Tests

```bash
# all unit tests
pytest test/blackvuesync_test.py -v

# single unit test (by node id or keyword expression)
pytest test/blackvuesync_test.py::test_name -v
pytest test/blackvuesync_test.py -k "retention and weekly" -v

# all integration tests (Behave BDD against an in-process mock dashcam)
behave

# a single feature or scenario
behave features/sync_basic.feature
behave -n "scenario name substring"

# integration tests against the Docker image instead of a subprocess
behave -D implementation=docker

# combined unit+integration coverage (writes coverage_report/index.html)
./coverage.sh
```

`features/CLAUDE.md` documents the BDD harness (mock dashcam, step library, userdata
flags). Read it before changing anything under `features/`.

## Important Constraints

### Python Version

Requires Python 3.9+ for modern type hints (`str | None`, walrus operator `:=`).

### External Dependencies

`sync.py` and `metrics.py` use only the Python standard library -- this
constraint must be maintained for portability (the cron-based sync path must
work without pip-installed packages).

The web server (`blackvuesync/server/`) depends on Flask, Flask-WTF, waitress,
and argon2-cffi. These are listed as runtime dependencies in `pyproject.toml`
and installed via `pip install -e ".[dev]"` in the development setup.

### Backwards Compatibility

Recording filename patterns must remain compatible with existing BlackVue firmware. The filename regex is based on official BlackVue documentation.

### File Locking

Lock files are stored in the destination directory (`.blackvuesync.lock`). The destination must be on a local filesystem (not NFS) for `fcntl.lockf()` to work correctly.

## Docker-Specific Notes

The Docker image (`Dockerfile`):

- Uses Alpine Linux for minimal size
- Runs as `dashcam` user (UID/GID set via `PUID`/`PGID` env vars)
- Sync is scheduler-driven from inside the long-running web service; cadence
  comes from `settings.schedule.cron_expression` (default `*/15 * * * *`)
- `entrypoint.sh` remaps the dashcam user via `setuid.sh`, then execs
  `python -m blackvuesync` with the CMD passed by Docker; defaults to `serve`
- Runtime pip deps (Flask, Flask-WTF, waitress, argon2-cffi, APScheduler) are
  installed in the image via `uv` (binary copied from `ghcr.io/astral-sh/uv`)
- `EXPOSE 8080` documents the web server port; map it in `docker-compose.yml`
- `HEALTHCHECK` polls `GET /healthz` via Python `urllib.request` (no curl needed)
- `/config` volume: mount a host directory here; `settings.json` is stored inside
- `run.sh` is a local smoke-test that overrides the CMD with `sync --dry-run` to
  exercise the image against a real dashcam without standing up the web server
