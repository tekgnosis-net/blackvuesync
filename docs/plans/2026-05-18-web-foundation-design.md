# Sub-Project #1: Web Foundation -- Design Spec

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD060 -->

**Date:** 2026-05-18
**Status:** Approved; implementation plan to follow as a separate document.
**Scope:** Sub-project #1 of a six-part transformation. This document covers only #1 (Web Foundation). Sub-projects #2-#6 will get their own design docs when each is brainstormed.

---

## Context

BlackVue Sync today is a 1,600-line single-file Python utility that synchronizes recordings from BlackVue dashcams to a local directory. It runs as a CLI driven by env vars, packaged in a Docker container with an internal cron daemon firing every 15 minutes. It deliberately has zero third-party runtime dependencies.

The fork owner (tekgnosis-net) wants to evolve this into a web-app: an Apple-design-styled dashboard with status cards, a UI-editable settings page, a log viewer with adjustable level, a statistics page consuming the existing Prometheus metrics, and a dashcam viewer that plays synchronized front+rear video with a GPS-driven map overlay (like the BlackVue PC Viewer). Docker becomes the primary deployment.

This is a platform-transformation that spans six largely-independent subsystems. The user agreed to decompose it into six sub-projects, each getting its own design + implementation cycle:

| # | Sub-project | Status |
|---|---|---|
| **1** | **Web Foundation** | **This spec** |
| 2 | Dashboard with status cards | Future cycle |
| 3 | Settings UI | Future cycle |
| 4 | Log viewer | Future cycle |
| 5 | Statistics page (with new SQLite time-series store) | Future cycle |
| 6 | Dashcam viewer (video + GPS map) | Future cycle |

Sub-project #1 is the foundation that every later sub-project consumes. It establishes the framework, the settings store, auth, sync orchestration, logging plumbing, progress emission, and the API conventions. Without it, the other five cannot start.

The existing CLI/cron model is **replaced**, not coexisted-with -- there will be one canonical entry point (`python -m blackvuesync serve`).

---

## Locked architectural choices

These were chosen during clarifying-questions and apply across the whole web-app series:

| Concern | Choice | Reason |
|---|---|---|
| Web framework | **Flask** | Already a dev dependency (Behave mock dashcam); sync-style codebase fits Flask's sync model; lowest learning curve |
| UI rendering | **Jinja2 + HTMX + Alpine.js**, no build step | No Node.js in Docker image; reactive feel without SPA complexity; fits "modern boring stack" |
| Settings store | **JSON file** at `/config/settings.json` with `0600` perms | Extends existing `metrics-state.json` pattern; one file for backup; SQLite introduced later for stats |
| Authentication | **Login form + signed session cookie**, single admin user, first-run wizard | Apple-design-friendly UX (no native browser prompt); modes for `none` / `proxy` also supported |
| Sync orchestration | **Flask + APScheduler** in a single process, ThreadPoolExecutor with `max_workers=1` | Cron expressions for future flexibility; `max_instances=1` prevents overlap; single process keeps in-memory state simple |
| Logging | **Stdout + RotatingFileHandler + in-process ring buffer** | Stdout for `docker logs`; file for restart-surviving history; ring buffer for live UI tail |
| Process model | **Replace the CLI/cron**, single long-running service | Removes `entrypoint.sh`, `crontab`, `blackvuesync.sh`; simpler Docker image |
| Apple-design depth in #1 | **Minimal**: tokens + base layout + 3 components (button, card shell, alert); login/first-run only | Defer rich UI to #2; foundation focuses on plumbing |

---

## Design Section 1: Process Model & Runtime Architecture

### Process layout

One Docker container, one Python process containing:

- **Flask app** served by Waitress WSGI (multi-threaded, single-process). Routes, templates, static assets.
- **APScheduler** (`BackgroundScheduler`) with a `ThreadPoolExecutor(max_workers=1)`. One job: `run_sync(settings)` triggered by `CronTrigger.from_crontab(settings.schedule.cron_expression, timezone=...)`. Cron is the only schedule representation.
- **Shared in-process state**: `SettingsStore`, `SyncMetrics`, `ProgressPublisher`, ring-buffer log handler, a `threading.Lock` guarding sync concurrency.
- **`run_sync(settings, publisher, metrics, logger)`** -- refactored from the current `blackvuesync.py` main flow into a callable function.

Volumes:
- `/recordings` -- downloaded dashcam files (existing)
- `/config` -- `settings.json`, `metrics-state.json`, `logs/` directory

### Module decomposition (new package layout)

```
blackvuesync/
├── __init__.py
├── __main__.py              # entry: `python -m blackvuesync serve` (also: `sync-once`)
├── sync.py                  # refactored from current blackvuesync.py:
│                            #   filename regex, dashcam HTTP client, download/resume,
│                            #   retention, locking; exposes run_sync(...) -> SyncResult
├── metrics.py               # extracted: SyncMetrics + Prometheus formatter
├── settings.py              # NEW: Settings dataclass tree, JSON load/save, validation,
│                            #      env-var bootstrap, SettingsStore
├── server/
│   ├── __init__.py          # create_app() factory
│   ├── auth.py              # login_required decorator, password hashing, session helpers
│   ├── scheduler.py         # APScheduler setup, trigger builders, on-demand triggers
│   ├── log_buffer.py        # MemoryHandler-derived ring buffer, SSE publisher
│   ├── progress.py          # FileProgress, SyncProgress, ProgressPublisher
│   ├── routes/
│   │   ├── auth.py          # /login, /logout, /first-run
│   │   ├── api.py           # /api/* JSON endpoints
│   │   ├── hx.py            # /hx/* HTML fragment endpoints
│   │   └── ui.py            # /, /settings, /logs, /stats, /viewer (placeholders)
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── first_run.html
│   │   ├── _partials/       # HTMX fragment templates
│   │   └── _placeholders/   # one per deferred sub-project
│   └── static/
│       ├── css/             # Apple-design tokens + components
│       ├── js/              # vendored alpine.min.js, htmx.min.js, small app.js
│       └── fonts/           # Inter fallback (SF Pro likely licensing-restricted)
```

The current `pyproject.toml` has `[tool.pylint.design] max-module-lines = 1200` set "for single-file project by design." This constraint is relaxed in #1 since the project is no longer single-file. The *core sync logic* still concentrates in `sync.py`, preserving the spirit of portability for that file.

### Process startup sequence

```
python -m blackvuesync serve
  ├── Load /config/settings.json (or bootstrap from env vars if absent)
  ├── Configure logging (3 handlers: stdout, RotatingFileHandler, MemoryHandler)
  ├── Create Flask app via create_app(settings_store)
  ├── Initialize APScheduler with BackgroundScheduler + ThreadPoolExecutor(1)
  │     Job: run_sync, trigger from settings.schedule, max_instances=1, coalesce=True
  ├── Register SIGTERM/SIGINT handlers for graceful shutdown
  └── waitress.serve(app, host='0.0.0.0', port=settings.web.port)
```

### Graceful shutdown

On SIGTERM (Docker stop):
1. Waitress stops accepting new connections, waits up to 30s for in-flight requests
2. APScheduler `shutdown(wait=True)` -- finishes the current sync if running
3. Flush log handlers (file, stdout)
4. Exit

Mid-sync shutdown is safe: `download_with_resume()` already uses the `.filename.mp4` dotfile pattern; partial files persist and the next sync resumes them.

### Progress emitters

**`FileProgress`** (frozen dataclass): identity (filename, recording_base, artifact, direction), size (total_bytes, downloaded_bytes), timing (started_at_monotonic, started_at_wall, updated_at_monotonic), derived rate (bytes_per_second EWMA, eta_seconds), state (`starting`|`downloading`|`resumed`|`complete`|`failed`), failure_reason.

**`SyncProgress`** (frozen dataclass): identity (job_id uuid4, started_at_wall), state (`idle`|`running`|`complete`|`failed`), current_file (FileProgress | None), aggregate (files_total, files_completed, files_failed, bytes_downloaded_total), last_event_monotonic.

Properties on both for `percent` and `elapsed_seconds` so the UI does no math.

**`ProgressPublisher`** owns the shared state with thread-safe accessors:
- Writer API: `begin_job(files_total)`, `start_file(filename, artifact, total_bytes)`, `update_bytes(downloaded)`, `finish_file(success, reason=None)`, `end_job(success)`
- Reader API: `snapshot() -> SyncProgress` (lock-free read of frozen dataclass), `subscribe() -> Iterator[SyncProgress]` (bounded queue, drops intermediate frames)
- Throttling: SSE publisher emits at `PUBLISH_HZ = 5.0`; the writer can update at unbounded rate; bounded queue (`maxsize=2`) discards backlog for slow consumers
- Lifecycle: `current_file` clears when sync ends; final snapshot retained for `POST_COMPLETE_RETENTION = 10.0` seconds before clearing to idle

**Downloader integration**: `download_with_resume()` gains optional `on_chunk: Callable[[int, int], None]` parameter. `sync.py` stays free of Flask imports -- only the callback signature couples it to the web layer.

### Production WSGI server

**Waitress** (single dependency, threaded, ideal for low-traffic LAN apps). Not gunicorn (multi-process defeats in-memory state). Not Flask's dev server.

---

## Design Section 2: Settings Schema & Persistence

### JSON file layout (`/config/settings.json`, `0600` perms)

Nested-by-category structure with `version: 1` at top for future schema migrations.

Sections:
- **connection** -- address, timeout_seconds (TIER: `restart`)
- **schedule** -- cron_expression (default `*/15 * * * *`), timezone (TIER: `next_tick`). **Cron is the only representation** -- no `interval` type. Default preserves today's 15-minute cadence. Future Settings UI (#3) renders one input with preset chips ("15 min", "Hourly", "Daily 3 AM", "Custom").
- **sync** -- priority, grouping, include, exclude, retry_failed_after, skip_metadata, affinity_key (TIER: `next_tick`)
- **retention** -- keep, max_used_disk_percent (TIER: `next_tick`)
- **logging** -- verbose, quiet, format, file_max_bytes, file_backup_count, ring_buffer_capacity (TIER: `immediate`)
- **metrics** -- file, pushgateway_url, job, instance, state_file (TIER: `immediate`)
- **web** -- port, session_lifetime_hours (TIER: `restart`)
- **auth** -- mode, username, password_hash, session_secret, trusted_proxies, proxy_user_header (TIER: `immediate`)
- **system** -- destination, dry_run (TIER: `restart`)

### Dataclass model

- All sections are `@dataclass(frozen=True)`.
- `Literal` types for enum fields (priority, grouping, log format, auth mode, schedule type, skip_metadata letters).
- `tuple[str, ...]` not `list[str]` -- hashable, matches frozen story.
- `TIER` as `ClassVar[Literal["immediate", "next_tick", "restart"]]` -- class-level metadata, doesn't serialize.
- `validate()` method on each section returns `list[str]` of error messages (accumulator pattern, not raise-fast).
- Top-level `Settings.validate()` aggregates all section errors.

### `SettingsStore`

```python
class SettingsStore:
    def get(self) -> Settings
    def update(self, mutation: Callable[[Settings], Settings]) -> Settings  # validates, persists, notifies
    def on_change(self, listener: Callable[[Settings, Settings], None]) -> None
```

- Thread-safe via `threading.RLock`.
- Atomic writes: temp file → fsync(fd) → chmod 0600 → os.replace(temp, final) → fsync(dir_fd). Defends against partial writes on crash.
- On startup: load if file exists; otherwise bootstrap from env vars (one-shot migration).
- Change notification via simple callback list. Consumers (logging reconfigurator, scheduler re-builder, auth invalidator) register.

### Env-var bootstrap (one-shot, on first start only)

Reads the same env vars `blackvuesync.sh` reads today: `ADDRESS`, `TIMEOUT`, `PRIORITY`, `GROUPING`, `KEEP`, `MAX_USED_DISK`, `RETRY_FAILED_AFTER`, `SKIP_METADATA`, `VERBOSE`, `QUIET`, `LOG_FORMAT`, `METRICS_*`, `AFFINITY_KEY`. Plus new ones: `BLACKVUESYNC_PORT`, `BLACKVUESYNC_ADMIN_USERNAME`, `BLACKVUESYNC_ADMIN_PASSWORD`, `BLACKVUESYNC_SCHEDULE` (cron expression, default `*/15 * * * *`), `BLACKVUESYNC_TIMEZONE` (default `UTC`).

Generates a random `session_secret` via `secrets.token_hex(32)`. If `BLACKVUESYNC_ADMIN_PASSWORD` is provided, hashes it; otherwise leaves `password_hash = ""` to trigger the first-run wizard on first HTTP request.

Drops two retired env vars with one-time warning log:
- `CRON` (replaced by always-on service + `schedule.type`)
- `RUN_ONCE` (no longer meaningful)

Writes `settings.json` with `0600` perms. From then on, the file is canonical; env vars are ignored.

### Defensive perms check at load time

If `settings.json` exists with weaker than `0600` perms, refuse to start with a clear error message. Saves users from accidentally `chmod 644`-ing the file during backup.

### Argon2id parameters (locked)

`time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16`. Comfortably above OWASP 2024 minimum. `argon2-cffi.check_needs_rehash()` enables silent upgrades on login if parameters are bumped later.

### New runtime dep

`argon2-cffi` (password hashing). +2 transitive packages.

---

## Design Section 3: Auth, First-Run, Security Model

### Auth modes

- **`login`** (default): username + password form, signed session cookie
- **`none`**: no auth, LAN trust. `login_required` becomes a passthrough setting `g.current_user = "anonymous"`
- **`proxy`**: reads `proxy_user_header` (default `X-Remote-User`) but ONLY if `request.remote_addr` is in `trusted_proxies` allowlist (CIDR or IP)

Mode read fresh on every request -- changes take effect immediately, no restart.

### Login flow

```
GET /  →  if auth.password_hash == ""        → redirect /first-run
        else if no session                    → redirect /login?next=/
        else                                  → render dashboard

POST /login  →  validate username + password via hmac.compare_digest + argon2.verify
              if invalid: rate-limit-record, uniform 1.5s response, render with error
              if valid: rehash if needs_rehash, session["user"] = ..., session["issued_at"] = ...
                        redirect to ?next= or /

POST /logout →  session.clear(); redirect /login
```

### CSRF

Flask-WTF `CSRFProtect(app)`. All POST/PUT/PATCH/DELETE require valid token (header or form field). Templates use `{{ csrf_token() }}`. HTMX configured to send `X-CSRFToken` automatically via `htmx:configRequest` event listener.

### Sessions

Flask built-in signed-cookie sessions (`itsdangerous` via Flask).
- Cookie name: `bvs_session`
- `HttpOnly=true`, `SameSite=Lax`, `Secure` per `request.is_secure` (proxied via `ProxyFix`)
- Lifetime: sliding window per `session_lifetime_hours`, with optional absolute cap via `issued_at` check

### Rate limiting

Per-source-IP sliding-window lockout on `/login` POST:
- 10 failures in 10 minutes → 15-minute lockout (429)
- In-memory only (resets on process restart)

### First-run wizard

`/first-run` accessible iff `auth.password_hash == ""`. Form fields: username (default `admin`), password, confirm. Validation: min 12 chars, must match confirm. On success: hash password, store via `SettingsStore.update()`, redirect to `/login`. Recovery: edit `settings.json` to clear `password_hash`, restart.

### Security headers (global `@after_request`)

- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.tile.openstreetmap.org; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: same-origin`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`
- `Strict-Transport-Security: max-age=63072000; includeSubDomains` (only when `request.is_secure`)

### HTTPS posture

App serves HTTP on port 8080. HTTPS via reverse proxy (Caddy/Traefik/nginx). `werkzeug.middleware.proxy_fix.ProxyFix` middleware wraps the WSGI app to honor `X-Forwarded-*` headers.

README documents a sample Caddyfile.

### New runtime dep

`Flask-WTF` (CSRF). +1 direct (WTForms transitive present via Flask).

---

## Design Section 4: API Surface & URL Layout

### URL space organization

```
/                            UI placeholder (Dashboard in #2)
/settings                    UI placeholder (Settings UI in #3)
/logs                        UI placeholder (Log viewer in #4)
/stats                       UI placeholder (Stats in #5)
/viewer                      UI placeholder (Dashcam viewer in #6)

/login                       UI + POST handler          (#1)
/logout                      POST                       (#1)
/first-run                   UI + POST handler          (#1)

/healthz                     GET liveness, no auth      (#1)
/readyz                      GET readiness, no auth     (#1)

/api/                        login_required, JSON
/api/sync/progress           GET   snapshot
/api/sync/progress/stream    GET   SSE stream
/api/sync/now                POST  trigger sync (202; 409 if already running)
/api/sync/last               GET   last completed run summary (204 if none)
/api/settings                GET   full settings (secrets redacted to "***")
/api/settings/<section>      PATCH partial section update
/api/auth/me                 GET   current user info
/api/auth/password           POST  change password (requires current_password)
/api/auth/sessions           DELETE rotate session_secret (invalidates all)

/hx/                         login_required, HTML fragments
/hx/sync/status-card         GET → Jinja2 partial
/hx/sync/last-run-card       GET → Jinja2 partial

/static/                     no auth, long Cache-Control + ETag
```

Two URL spaces: `/api/*` (always JSON) and `/hx/*` (always HTML fragments). No content negotiation on the same URL.

No API versioning (`/api/v1/`) -- single self-hosted consumer, YAGNI.

### Error envelope (all `/api/*` non-2xx)

```json
{
  "error": "human-readable message",
  "code": "STABLE_SLUG",
  "details": { "...": "varies by code" }
}
```

Known codes: `SETTINGS_INVALID`, `SYNC_ALREADY_RUNNING`, `AUTH_REQUIRED`, `RATE_LIMITED`, `INTERNAL_ERROR`. `details.field_errors[]` shape (with `path` + `message`) for validation responses.

### Status code conventions

`200` reads/updates; `202` async-triggered sync; `204` no-content; `303` redirect after login; `400` malformed; `401` no/invalid session; `403` reserved (future multi-user); `409` sync already running; `422` validation; `429` rate-limited; `500` unhandled.

### PATCH /api/settings/<section> response

Includes `tier` field telling the future Settings UI what affordance to render:
- `"immediate"` → green "Saved" toast
- `"next_tick"` → yellow "Will apply at next sync at HH:MM" with `next_application` timestamp
- `"restart"` → red "Restart required" with `restart_required: true`

### Secrets redaction sentinel

`GET /api/settings` returns `password_hash` and `session_secret` as the literal string `"***"`. Frontend treats `"***"` as "leave unchanged" on PATCH (never sends it back as a real value).

### HTMX fragment endpoints

Two examples in #1 prove the pattern:
- `/hx/sync/status-card` -- current state card
- `/hx/sync/last-run-card` -- last run summary

Both ~30 lines of Python + Jinja2 partial. Dashboard (#2) extends the pattern.

### Static asset fingerprinting

Tiny helper computes content hashes at app boot. `{{ asset('css/app.css') }}` resolves to `/static/css/app.a1b2c3d4.css`. Long Cache-Control + immutable.

### Documentation

`docs/api.md` -- handwritten endpoint reference. ~100 lines for foundation; grows per sub-project. OpenAPI deferred.

### New runtime deps

None. Reuses Flask + Flask-WTF + Werkzeug from earlier sections.

---

## Design Section 5: Testing Approach

### Test pyramid

- **Unit (pytest)** -- ~200 tests covering SettingsStore, ProgressPublisher, auth helpers, scheduler config, log handlers, validators, the existing sync internals (parsing/grouping/filtering, retained)
- **Integration (pytest + Flask test client)** -- ~80 tests at HTTP level: routes, status codes, error envelopes, CSRF, SSE, session handling
- **End-to-end (Behave)** -- ~30 scenarios via existing mock dashcam + extended `service_mode` userdata. New feature file `features/web_foundation.feature`
- **Browser smoke (Playwright)** -- optional, deferred to #2 unless time permits

### New test directory structure

```
test/
├── conftest.py                      # shared fixtures
├── test_sync.py                     # renamed from blackvuesync_test.py
├── test_settings.py                 # NEW
├── test_progress.py                 # NEW
├── test_auth.py                     # NEW
├── test_scheduler.py                # NEW
├── test_log_handlers.py             # NEW
├── test_app_factory.py              # NEW
└── integration/                     # NEW directory
    ├── conftest.py
    ├── test_auth_routes.py
    ├── test_sync_routes.py
    ├── test_settings_routes.py
    └── test_sse.py
```

### Behave changes

- New `features/web_foundation.feature` with scenarios for first-run, login, settings PATCH, sync trigger via API, healthz/readyz.
- New userdata var `service_mode = subprocess | docker | external` to choose how the service is launched per scenario.
- Mock dashcam in `features/mock_dashcam/` gains an optional "slow mode" for testing progress emission visibly. Small extension; not core code change.
- Existing CLI-style scenarios continue to pass (the sync core is still exercised end-to-end).

### Time control

`freezegun` controls `time.time()` for session-expiry, lockout-window, scheduler-timing, and post-completion progress-retention tests. Tests complete in milliseconds instead of waiting real time.

### Coverage targets

| Area | Target |
|---|---|
| `blackvuesync/sync.py` | ≥95% (existing baseline; no regression) |
| `blackvuesync/settings.py` | ≥90% |
| `blackvuesync/server/progress.py` | ≥90% |
| `blackvuesync/server/auth.py` | ≥85% |
| `blackvuesync/server/scheduler.py` | ≥80% |
| `blackvuesync/server/__init__.py` | ≥70% |
| `blackvuesync/__main__.py` | manual |
| **Overall foundation** | **≥85%** |

### CI integration

All five existing required PR checks continue to gate:
- `pre-commit`, `unit-tests`, `integration-tests`, `test`, `SonarCloud Code Analysis`

New tests run within existing jobs:
- `test/` (including `test/integration/`) → `unit-tests` job
- `features/web_foundation.feature` → `integration-tests` job
- Docker image rebuild + Behave docker-mode against it → `test` job (already does this; just now tests the web service)

No new CI workflows needed.

### Manual verification checklist (for foundation-complete gate)

- [ ] `docker run` produces running container on port 8080
- [ ] First-run wizard appears at root; setting password redirects to login
- [ ] Login with correct password works; wrong password fails with uniform timing
- [ ] After 11 wrong logins from one IP, 12th returns 429
- [ ] `GET /api/sync/progress` returns `state: "idle"` post-login
- [ ] `POST /api/sync/now` returns 202; second immediate POST returns 409
- [ ] `GET /api/sync/progress/stream` streams events during sync
- [ ] `PATCH /api/settings/logging {"verbose": 2}` returns 200 + `tier: "immediate"`; new log lines reflect level
- [ ] `PATCH /api/settings/connection {"address": "..."}` returns `tier: "restart"`
- [ ] SIGTERM finishes in-flight sync and exits cleanly within timeout
- [ ] `/config/logs/blackvuesync.log` exists and rotates at configured size

### New test-only dep

`freezegun`. +1 package. `pytest-flask` and `Playwright` deferred to "when needed."

---

## Design Section 6: Scope Guards

### IN this sub-project (#1)

- Long-running web service replacing CLI/cron
- JSON settings store with validation, atomic writes, env-var bootstrap, change notification
- Auth (login form + session cookies + CSRF + first-run + rate limiting + 3 modes)
- APScheduler-driven sync with `max_instances=1`, on-demand `/api/sync/now`
- Progress emission (`ProgressPublisher`, frozen dataclasses, ≤5 Hz publish)
- Logging plumbing (3 handlers: stdout + rotating file + ring buffer)
- Foundation API surface (`/api/*`, `/hx/*` examples, `/healthz`, `/readyz`)
- Single-process Docker image (no cron, no shell wrappers)
- Minimal Apple-design scaffolding: tokens, base layout, 3 components, styled login + first-run only
- Five placeholder routes (`/`, `/settings`, `/logs`, `/stats`, `/viewer`) with "coming in sub-project #N" content using the base layout
- Test infrastructure across all three layers + Behave extensions + `freezegun`

### NOT in this sub-project (deferred to #2-#6)

| Feature | Lives in |
|---|---|
| Dashboard layout + all status cards | #2 |
| Settings UI (forms, tier-aware affordances) | #3 |
| Log viewer (live tail UI, search, filters) | #4 |
| Statistics page + SQLite time-series store | #5 |
| Dashcam viewer (synchronized video, GPS map) | #6 |

### Out of scope for the entire web-app series

- Multi-user accounts (single admin)
- Programmatic API tokens (browser session only)
- In-app HTTPS termination (reverse proxy expected)
- Internationalization (English only)
- Mobile-first responsive design (responsive but not mobile-optimized)
- Plugin/extension system
- OpenAPI generation (handwritten `docs/api.md` only)
- Settings export/import UI (manual file copy)

### Open questions deferred to specific sub-projects

| Question | Decided in |
|---|---|
| Light/dark mode toggle | #2 |
| Card layout (grid/masonry/responsive) | #2 |
| Settings form structure (single-page/tabs/wizard) | #3 |
| Log search UX (substring/regex/structured) | #4 |
| Chart library (Chart.js/Observable Plot/SVG) | #5 |
| SQLite schema for metrics history | #5 |
| Map tile provider (OSM/Stamen/Mapbox/offline) | #6 |
| Video sync mechanism | #6 |

---

## Critical files to modify or create

### To be created

**New Python source:**
- `blackvuesync/__init__.py`
- `blackvuesync/__main__.py`
- `blackvuesync/settings.py`
- `blackvuesync/metrics.py` (extracted from current single file)
- `blackvuesync/server/__init__.py`
- `blackvuesync/server/auth.py`
- `blackvuesync/server/scheduler.py`
- `blackvuesync/server/log_buffer.py`
- `blackvuesync/server/progress.py`
- `blackvuesync/server/routes/auth.py`
- `blackvuesync/server/routes/api.py`
- `blackvuesync/server/routes/hx.py`
- `blackvuesync/server/routes/ui.py`

**New templates and assets:**
- `blackvuesync/server/templates/base.html`
- `blackvuesync/server/templates/login.html`
- `blackvuesync/server/templates/first_run.html`
- `blackvuesync/server/templates/_partials/sync_status_card.html`
- `blackvuesync/server/templates/_partials/last_run_card.html`
- `blackvuesync/server/templates/_placeholders/dashboard.html` (one per deferred sub-project)
- `blackvuesync/server/static/css/tokens.css` (Apple-design tokens)
- `blackvuesync/server/static/css/components.css` (button, card, alert)
- `blackvuesync/server/static/css/layout.css`
- `blackvuesync/server/static/js/htmx.min.js` (vendored)
- `blackvuesync/server/static/js/alpine.min.js` (vendored)
- `blackvuesync/server/static/js/app.js` (small bootstrap)

**New tests:**
- `test/conftest.py`
- `test/test_settings.py`
- `test/test_progress.py`
- `test/test_auth.py`
- `test/test_scheduler.py`
- `test/test_log_handlers.py`
- `test/test_app_factory.py`
- `test/integration/conftest.py`
- `test/integration/test_auth_routes.py`
- `test/integration/test_sync_routes.py`
- `test/integration/test_settings_routes.py`
- `test/integration/test_sse.py`
- `features/web_foundation.feature`
- `features/steps/web_steps.py`
- New step definitions in `features/steps/` for HTTP-driven scenarios

**New docs:**
- `docs/api.md` (endpoint reference)
- README sections covering: GHCR image pull, port mapping, volume mounts, sample Caddyfile, recovery procedure

### To be modified

- `blackvuesync.py` → refactored and split. The new `blackvuesync/sync.py` retains the core download logic. The old file becomes `__main__.py` with subcommand dispatch.
- `Dockerfile` -- single-stage, no cron daemon, `CMD ["python", "-m", "blackvuesync", "serve"]`, expose port 8080, HEALTHCHECK
- `docker-compose.yml` -- env vars become bootstrap defaults; deprecated `CRON` and `RUN_ONCE` removed; ports added
- `pyproject.toml` -- add runtime deps: `Flask`, `Flask-WTF`, `waitress`, `APScheduler`, `argon2-cffi`. Add test dep: `freezegun`. Update `[project.scripts]` if console entry point needs adjustment. Bump version to `2.3.0a0` or similar. Relax `[tool.pylint.design] max-module-lines` constraint.
- `entrypoint.sh`, `blackvuesync.sh`, `crontab` -- DELETED (no longer needed)
- `features/mock_dashcam/` -- add optional slow-mode (bandwidth throttle) for progress testing
- `.dockerignore` -- ensure `venv/` and `__pycache__/` excluded; add `coverage_report/`
- `behave.ini` -- add `service_mode` userdata default
- `coverage.sh` -- unchanged; will continue to combine pytest + behave coverage

### To be reused (existing utilities)

- Filename regex and parsing logic in `blackvuesync.py` (moves to `sync.py`)
- Existing metrics state file format and `load_metrics_state` / `save_metrics_state` functions
- Existing `download_with_resume` function (gets `on_chunk` parameter added)
- Existing structured JSON log formatter (gets re-attached to the new handler set)
- Behave mock dashcam server (`features/mock_dashcam/`)
- Existing `features/lib/docker.py` test helpers

---

## Verification

### How to verify the foundation end-to-end

1. **Local Python dev loop:**
   ```bash
   pip install -e ".[dev]"
   python -m blackvuesync serve
   # browser → http://localhost:8080/  → first-run wizard
   ```

2. **Unit + integration tests:**
   ```bash
   pytest test/ -v --cov=blackvuesync --cov-report=term-missing
   ```
   Confirm ≥85% line coverage; no regression from current baseline.

3. **Behave end-to-end:**
   ```bash
   behave features/web_foundation.feature --no-capture
   behave -D implementation=docker
   ```

4. **Combined coverage report:**
   ```bash
   ./coverage.sh
   ```
   Inspect `coverage_report/index.html`. SonarCloud picks this up via the existing workflow.

5. **Manual smoke (from Section 5 checklist):**
   - Build image, run container, walk the checklist.

6. **Branch-protection-gated PR:**
   - Push branch, open PR against `main`
   - All five required checks (`pre-commit`, `unit-tests`, `integration-tests`, `test`, `SonarCloud Code Analysis`) must pass
   - Use Squash or Rebase merge (linear history required by branch protection)

7. **Post-merge GHCR validation:**
   - Confirm new image at `ghcr.io/tekgnosis-net/blackvuesync:latest`
   - `docker pull` from a clean host succeeds
   - Container starts, exposes 8080, first-run flow works

---

## Next steps after this plan

1. **Copy this spec to `docs/superpowers/specs/2026-05-18-blackvuesync-web-foundation-design.md`** as the first execution action. Commit it to the repository (separate small PR before implementation begins).

2. **Invoke the `superpowers:writing-plans` skill** to turn this design into an executable implementation plan with phases, ordering, and review checkpoints. The implementation plan lives separately from this design.

3. **Implementation cycle** for sub-project #1 follows the implementation plan. Likely structured in phases:
   - Phase A: Refactor `blackvuesync.py` into `blackvuesync/` package without behavior change; old CLI still works
   - Phase B: Add `settings.py` with `SettingsStore`; CLI continues to read env vars but via new module
   - Phase C: Add `server/` skeleton -- Flask app, base template, login, first-run, healthz/readyz
   - Phase D: Add `ProgressPublisher` + downloader callback hook
   - Phase E: Add APScheduler + replace cron in Dockerfile; settings.json becomes authoritative
   - Phase F: Add full API surface (`/api/*` + `/hx/*` examples)
   - Phase G: Polish -- Apple-design scaffolding, placeholder pages, security headers, ProxyFix, docs

   Each phase ships as its own PR through the now-established branch-protection workflow.

4. **After #1 ships and is verified**, the user selects which sub-project to brainstorm next from #2-#6. Each gets its own design spec + implementation plan.

---

## Spec self-review

Done inline during drafting. Reviewed for:

- **Placeholders / TBDs** -- none remaining. The "Open questions deferred to specific sub-projects" table makes punted decisions explicit and assigns them; nothing is left ambiguous within #1's scope.
- **Internal consistency** -- checked: `AuthSettings.trusted_proxies` added in Section 3 was retroactively reflected in the Section 2 schema (this was a real inconsistency caught during drafting); `Settings.validate()` returning a list rather than raising is consistent with how the API surface returns 422 with field_errors; secrets-redaction sentinel `***` is declared in Section 4 and assumed in Section 3 (`/api/settings` GET).
- **Scope** -- focused on a single sub-project. The 6-table of deferrals makes the boundary explicit; nothing in the IN list bleeds into #2-#6.
- **Ambiguity** -- concrete defaults specified throughout (Argon2 parameters, publish rate, ring buffer capacity, lockout thresholds, file rotation sizes, port 8080, retention windows). Where flexibility is needed, it goes into `settings.json` schema rather than hardcoded.

One known limitation: the **font question** is not fully resolved -- SF Pro is the canonical Apple-design font but is licensing-restricted. The spec calls for an Inter fallback, which is fine but means the visual reference isn't pixel-identical to Apple's. This is a tactical call to be made during implementation by the person doing the Apple-design work; spec-level it's acceptable.

---

**End of design spec.**
