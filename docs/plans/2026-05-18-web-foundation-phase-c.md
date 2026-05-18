# Web Foundation -- Phase C: Server skeleton, auth, first-run

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 -->

**Date:** 2026-05-18
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Sections 3, 4 primarily)
**Phase:** C of 7 (A, B done; D-G to follow).

**Goal:** Introduce the Flask web server as a long-running service alongside
(not replacing) the existing cron-driven sync. Ships login, logout, first-run
wizard, three auth modes (`login` / `none` / `proxy`), CSRF protection,
security headers, `ProxyFix`, `/healthz`, `/readyz`, and a minimal Apple-design
scaffold (tokens + base layout + 3 components). The cron-driven sync flow
remains the default container behavior; Phase E will retire cron and integrate
sync into the long-running service.

**Architecture:** A new `blackvuesync.server` package containing a
`create_app(settings_store)` factory that wires Flask, Flask-WTF, ProxyFix,
security headers, and the auth/UI/health routes. Auth is single-user with
argon2id password hashing, signed-cookie sessions (Flask built-in), CSRF on
state-changing methods, and per-IP failed-login rate limiting (in-memory).
The server runs under Waitress on port 8080. Invocation is via a new
`python -m blackvuesync serve` subcommand; the existing
`python -m blackvuesync <address>` CLI sync flow is unchanged.

**Tech Stack:** Python 3.9 stdlib + new runtime deps: `Flask`, `Flask-WTF`,
`waitress`, `argon2-cffi`. New test-only dep: `pytest-flask` (optional but
makes route testing tidier). Templates use Jinja2 (Flask's default); static
assets are CSS + vendored Alpine.js + HTMX (downloaded into the package at
build time, not from CDN, per design spec section 1's Apple-design scaffold).

**Out of scope for Phase C:**

- APScheduler / replacing cron -- Phase E.
- Dashboard with status cards -- Sub-project #2 (after #1 fully ships).
- Settings UI for editing config -- Sub-project #3.
- Log viewer / Stats / Dashcam viewer -- Sub-projects #4 / #5 / #6.
- Actually exposing `Settings` to `run_sync` (still argparse-backed in Phase C).
- Multi-user accounts, API tokens, in-app HTTPS termination -- explicitly
  out-of-scope per design spec section 6.

---

## File structure after Phase C

```
blackvuesync/
├── __init__.py                     (unchanged)
├── __main__.py                     MODIFIED: adds `serve` subcommand;
│                                   existing sync flow unchanged
├── settings.py                     MODIFIED: opening cleanup (Phase B carry-
│                                   forward items C1-C5; see below)
├── sync.py                         unchanged
├── metrics.py                      unchanged
└── server/
    ├── __init__.py                 NEW: create_app(settings_store) factory;
    │                               wires Flask + Flask-WTF + ProxyFix +
    │                               security-headers + blueprints
    ├── auth.py                     NEW: hash_password, verify_password,
    │                               needs_rehash, login_required decorator,
    │                               rate-limit helpers
    ├── routes/
    │   ├── __init__.py             NEW: empty
    │   ├── auth.py                 NEW: /login, /logout, /first-run blueprint
    │   ├── ui.py                   NEW: /, /settings, /logs, /stats, /viewer
    │   │                           placeholder blueprint (just renders
    │   │                           "coming in sub-project #N" pages)
    │   └── health.py               NEW: /healthz, /readyz (no auth)
    ├── templates/
    │   ├── base.html               NEW: Apple-design layout
    │   ├── login.html              NEW: login form
    │   ├── first_run.html          NEW: setup wizard
    │   └── _placeholders/
    │       ├── dashboard.html      NEW: sub-project #2 placeholder
    │       ├── settings.html       NEW: sub-project #3 placeholder
    │       ├── logs.html           NEW: sub-project #4 placeholder
    │       ├── stats.html          NEW: sub-project #5 placeholder
    │       └── viewer.html         NEW: sub-project #6 placeholder
    └── static/
        ├── css/
        │   ├── tokens.css          NEW: color / type / spacing / radii /
        │   │                       shadow tokens (Apple-design)
        │   ├── components.css      NEW: button, card shell, alert
        │   └── layout.css          NEW: base layout (header, nav, main)
        ├── js/
        │   ├── htmx.min.js         NEW: vendored from htmx.org
        │   ├── alpine.min.js       NEW: vendored from alpinejs.dev
        │   └── app.js              NEW: csrf-token wiring for htmx requests
        └── fonts/                  empty for now (SF Pro is licensing-
                                    restricted; Inter via system font stack)

test/
├── test_settings.py                MODIFIED: tests for new Literal validators
├── test_auth.py                    NEW: hash/verify/needs_rehash, lockout,
│                                   login_required modes (login/none/proxy)
├── test_routes_auth.py             NEW: /login flow, /logout, /first-run,
│                                   CSRF rejection, rate-limit lockout
├── test_routes_health.py           NEW: /healthz, /readyz
├── test_routes_ui.py               NEW: redirects, placeholder pages
└── test_security_headers.py        NEW: CSP, X-Frame-Options, etc.
```

---

## Opening cleanup (carry-forward from Phase B review)

### Task C1: Add Literal field validators in settings.py

Files: `blackvuesync/settings.py`, `test/test_settings.py`.

Steps:

1. In each section's `validate()` method, add explicit membership checks for
   the `Literal` fields (currently unchecked):
   - `SyncSettings.validate`: `priority in ("date", "rdate", "type")`,
     `grouping in ("none", "daily", "weekly", "monthly", "yearly")`,
     each entry of `skip_metadata in ("t", "3", "g")`.
   - `LoggingSettings.validate`: `format in ("text", "json")`.
   - `AuthSettings.validate`: `mode in ("login", "none", "proxy")`.
2. Add a unit test per Literal field exercising one invalid value.
3. Run `pytest test/test_settings.py -v` and confirm new tests pass.

### Task C2: Log validation errors on load

Files: `blackvuesync/settings.py`, `test/test_settings.py`.

Steps:

1. In `SettingsStore._load()`, after building the `Settings` instance, call
   `settings.validate()` and log each returned error at `WARNING` level. Do
   NOT raise -- the CLI/server must keep working even if the file is
   malformed (it just won't pass `.update()` validation until fixed).
2. Add a unit test that writes a settings.json with an invalid field, loads
   it, and asserts a WARNING log entry was emitted.

### Task C3: Converge `_settings_to_dict` and `_settings_from_dict` on `_SECTION_FIELDS`

Files: `blackvuesync/settings.py`.

Steps:

1. Refactor `_settings_to_dict` to iterate `_SECTION_FIELDS` (the same
   structure `_settings_from_dict` uses). Eliminates the drift risk where
   adding a future section would require touching both sites.
2. Run the existing test_settings.py round-trip tests; they should all still
   pass since serialization output is the same.

### Task C4: Remove redundant exception type in __main__.py:63

Files: `blackvuesync/__main__.py`.

Steps:

1. In `_try_load_settings_store`, change `except Exception:` (the broad
   catch from Phase B's regression fix) to keep the breadth -- but if you
   prefer narrower, use `except (OSError, ValueError):` instead of
   `(OSError, PermissionError)`. `PermissionError` is an OSError subclass;
   listing it is redundant. Either choice is fine; the goal is no redundant
   type.

### Task C5: Suppress python:S7504 false positive on settings.py:419

Files: `blackvuesync/settings.py`.

Steps:

1. In `SettingsStore.update()`, the line `for listener in list(self._listeners):`
   uses `list()` intentionally to snapshot the listener set for safe iteration
   under concurrent modification. SonarCloud's S7504 rule doesn't model
   concurrency. Add a brief comment + NOSONAR:

   ```python
   # snapshot the listeners list so a callback that registers a new listener
   # (via on_change()) cannot mutate the list mid-iteration. (suppresses S7504.)
   for listener in list(self._listeners):  # NOSONAR
   ```

---

## Main work: Server skeleton + auth + first-run

### Task M1: Add new runtime deps to pyproject.toml

Files: `pyproject.toml`.

Steps:

1. Add to `[project] dependencies`:

   ```toml
   dependencies = [
       "Flask~=3.1",
       "Flask-WTF~=1.2",
       "waitress~=3.0",
       "argon2-cffi~=23.1",
   ]
   ```

   (Pinning to compatible-release `~=` so minor version bumps work but major
   versions require a deliberate update.)
2. Run `pip install -e ".[dev]"` to install. Verify imports work:
   `python -c "import flask, flask_wtf, waitress, argon2; print('ok')"`.

### Task M2: Implement `blackvuesync/server/auth.py`

Files: `blackvuesync/server/auth.py` (create), `test/test_auth.py` (create).

Steps:

1. Create `auth.py` with:
   - `_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)`
   - `hash_password(plaintext: str) -> str`
   - `verify_password(stored_hash: str, plaintext: str) -> bool` using
     `_HASHER.verify` and catching `VerifyMismatchError`.
   - `needs_rehash(stored_hash: str) -> bool` via `_HASHER.check_needs_rehash`.
   - `login_required(view)` decorator: reads `current_app.settings_store.get()`
     and dispatches by `auth.mode`:
     - `"none"`: passthrough, sets `g.current_user = "anonymous"`.
     - `"proxy"`: validates `request.remote_addr in trusted_proxies`, reads
       `auth.proxy_user_header`, sets `g.current_user = header_value`. Aborts
       401 if conditions fail.
     - `"login"`: checks `session["user"]`; sets `g.current_user`; redirects
       to `/login?next=<request.path>` if absent.
   - `_is_locked_out(ip)`, `_record_failure(ip)`, `_clear_failures(ip)` --
     in-memory sliding window (10 failures in 10 min -> 15 min lockout). Use
     module-level dicts protected by `threading.Lock`.
2. Create `test_auth.py` with tests for:
   - `hash_password` -> `verify_password` round-trip works.
   - `verify_password` returns False for wrong password.
   - Two different calls to `hash_password` for the same plaintext produce
     different hashes (salts differ).
   - `needs_rehash` returns False for a fresh hash.
   - `login_required` redirects when `auth.mode="login"` and no session.
   - `login_required` passes through when `auth.mode="none"`.
   - `login_required` honors X-Remote-User when `auth.mode="proxy"` and IP is
     in trusted_proxies.
   - `login_required` returns 401 in `proxy` mode when IP is not trusted.
   - Lockout window: 10 failures don't lock, 11th does; clears after 15 min
     (use `freezegun` or monkeypatch time).

### Task M3: Implement security headers middleware

Files: `blackvuesync/server/__init__.py` (will be created in M4; preview the
content here for clarity).

Steps:

1. Add an `@app.after_request` function (registered inside `create_app`)
   that sets these headers on every response:
   - `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.tile.openstreetmap.org; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'`
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: same-origin`
   - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
   - `Strict-Transport-Security: max-age=63072000; includeSubDomains`
     (only when `request.is_secure`).

### Task M4: Implement `create_app` factory + middleware

Files: `blackvuesync/server/__init__.py`.

Steps:

1. Write `create_app(settings_store: SettingsStore) -> Flask`:

   ```python
   from flask import Flask
   from flask_wtf.csrf import CSRFProtect
   from werkzeug.middleware.proxy_fix import ProxyFix

   def create_app(settings_store):
       app = Flask(__name__)
       app.settings_store = settings_store
       settings = settings_store.get()
       app.config.update(
           SECRET_KEY=settings.auth.session_secret.encode(),
           SESSION_COOKIE_NAME="bvs_session",
           SESSION_COOKIE_HTTPONLY=True,
           SESSION_COOKIE_SAMESITE="Lax",
           PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.web.session_lifetime_hours),
           WTF_CSRF_HEADERS=["X-CSRFToken"],
       )
       CSRFProtect(app)
       app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

       from blackvuesync.server.routes.auth import bp as auth_bp
       from blackvuesync.server.routes.ui import bp as ui_bp
       from blackvuesync.server.routes.health import bp as health_bp
       app.register_blueprint(auth_bp)
       app.register_blueprint(ui_bp)
       app.register_blueprint(health_bp)

       @app.after_request
       def add_security_headers(response):
           # ... per M3
           return response

       return app
   ```
2. Note: `settings_store` is attached to the `Flask` app instance (not its
   config) so it can be type-checked. Routes access via
   `current_app.settings_store`.

### Task M5: Implement /healthz and /readyz

Files: `blackvuesync/server/routes/health.py`.

Steps:

1. Create the blueprint with two routes, no auth required:

   ```python
   from flask import Blueprint, jsonify, current_app

   bp = Blueprint("health", __name__)

   @bp.route("/healthz")
   def healthz():
       return jsonify(status="ok"), 200

   @bp.route("/readyz")
   def readyz():
       store_ok = current_app.settings_store is not None
       return (
           jsonify(status="ready" if store_ok else "starting",
                   settings_loaded=store_ok),
           200 if store_ok else 503,
       )
   ```
2. Tests: GET /healthz returns 200 with `{"status": "ok"}`; GET /readyz with
   loaded store returns 200; with `settings_store=None`, returns 503.

### Task M6: Implement /login, /logout, /first-run

Files: `blackvuesync/server/routes/auth.py`, `blackvuesync/server/templates/login.html`,
`blackvuesync/server/templates/first_run.html`, `test/test_routes_auth.py`.

Steps:

1. Create the `auth` blueprint with:
   - `GET /login` -> renders login.html. Redirects to /first-run if
     `auth.password_hash == ""`.
   - `POST /login` -> validates username + password; pads response to ~1.5s
     on failure (uniform timing); on success sets session and redirects to
     `?next=` or `/`. Increments rate-limit counter on failure; returns 429
     if locked out.
   - `POST /logout` (login_required) -> clears session, redirects to /login.
   - `GET /first-run` -> renders first_run.html iff `auth.password_hash == ""`,
     else redirects to /login.
   - `POST /first-run` -> validates username (default "admin") + password
     (>=12 chars, matches confirm); hashes password; updates settings store
     via `update(lambda s: replace(s, auth=replace(s.auth, ...)))`; redirects
     to /login.
2. Add a `before_request` handler that redirects to `/first-run` if
   `auth.password_hash` is empty AND request path is not in
   `(/first-run, /static, /healthz, /readyz)`. This makes the first-run
   wizard sticky until a password is set.
3. Templates use base.html (M9), CSRF token via `{{ csrf_token() }}`, and
   Apple-design components (button, card, alert).
4. Tests:
   - GET / when no password_hash -> redirects to /first-run.
   - POST /first-run with valid data -> updates store and redirects to /login.
   - POST /first-run with short password -> 400 + error message.
   - POST /first-run with mismatched confirm -> 400 + error message.
   - POST /login with valid creds -> 302 + session set.
   - POST /login with wrong creds -> 401 + same response time (within +/-100ms)
     as the wrong-username case (assert with `time.perf_counter`).
   - POST /login 11 times wrong -> 12th gets 429.
   - POST /login without CSRF token -> 400.
   - POST /logout -> clears session.

### Task M7: Implement placeholder UI blueprint

Files: `blackvuesync/server/routes/ui.py`,
`blackvuesync/server/templates/_placeholders/*.html`,
`test/test_routes_ui.py`.

Steps:

1. Create the `ui` blueprint with:
   - `GET /` -> renders dashboard.html (placeholder; "Dashboard coming in
     sub-project #2"). `@login_required`.
   - `GET /settings` -> settings.html placeholder. `@login_required`.
   - `GET /logs` -> logs.html placeholder. `@login_required`.
   - `GET /stats` -> stats.html placeholder. `@login_required`.
   - `GET /viewer` -> viewer.html placeholder. `@login_required`.
2. Each placeholder template extends base.html and shows:
   - A heading like "Dashboard"
   - A subtle "Coming in sub-project #2" subtitle
   - A small info-style card with a link to the sub-project's design doc path
     (relative path `docs/plans/...`)
   - A reference to the package version (`{{ version }}`) and current
     scheduler interval if available (Phase C just shows the cron expression
     from settings).
3. Tests: each route returns 200 when logged in; 302 to /login when not
   logged in (`auth.mode="login"`).

### Task M8: Apple-design CSS tokens + components + layout

Files: `blackvuesync/server/static/css/tokens.css`,
`blackvuesync/server/static/css/components.css`,
`blackvuesync/server/static/css/layout.css`,
`blackvuesync/server/templates/base.html`.

Steps:

1. `tokens.css` -- CSS custom properties for:
   - colors (system grays + accents, light + dark via `@media (prefers-color-scheme: dark)`)
   - type scale (matching iOS HIG: 11, 12, 13, 15, 17, 22, 28, 34px)
   - spacing scale (4, 8, 12, 16, 24, 32, 48, 64)
   - radii (4, 8, 12, 16)
   - shadows (subtle, medium, prominent)
2. `components.css` -- three components:
   - `.button` (primary/secondary variants, with `:hover` `:focus-visible`)
   - `.card` (rounded container with subtle shadow)
   - `.alert` (info/warning/error variants)
3. `layout.css` -- base layout (header, nav, main, footer).
4. `base.html` -- Jinja2 base template:
   - DOCTYPE, meta viewport, title
   - Links to tokens.css, layout.css, components.css (via the static-asset
     helper)
   - HTMX + Alpine.js script tags (vendored, from /static/js/)
   - app.js for CSRF token wiring on htmx requests
   - `<header>` with site title; `<nav>` with placeholder-disabled links;
     `<main>{% block content %}{% endblock %}</main>`; minimal `<footer>`.
   - Uses Inter font via system stack fallback (no font file shipped).
5. Use `Inter` only as a fallback; primary font stack is
   `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue",
   Helvetica, Arial, sans-serif`.

### Task M9: Vendored HTMX + Alpine.js

Files: `blackvuesync/server/static/js/htmx.min.js`,
`blackvuesync/server/static/js/alpine.min.js`,
`blackvuesync/server/static/js/app.js`.

Steps:

1. Download HTMX 2.0+ minified and place at
   `blackvuesync/server/static/js/htmx.min.js`. (No build step; just check
   in the file.) Note the version + sha256 in a comment at the top of the
   file or in a separate `VENDORED.md` doc.
2. Download Alpine.js 3.x minified at
   `blackvuesync/server/static/js/alpine.min.js`. Same versioning note.
3. Write `app.js` with the htmx CSRF wiring (~10 lines):

   ```javascript
   document.body.addEventListener('htmx:configRequest', (event) => {
     const tokenEl = document.querySelector('meta[name="csrf-token"]');
     if (tokenEl) {
       event.detail.headers['X-CSRFToken'] = tokenEl.content;
     }
   });
   ```
4. `base.html` includes a `<meta name="csrf-token" content="{{ csrf_token() }}">`
   so the script can pick it up.

### Task M10: Add `serve` subcommand to __main__.py

Files: `blackvuesync/__main__.py`.

Steps:

1. Restructure `main()` to dispatch on argparse subcommand:
   - Existing sync flow becomes the default (or `sync` subcommand).
   - New `serve` subcommand: instantiate `SettingsStore`, call
     `create_app(store)`, run `waitress.serve(app, host="0.0.0.0", port=settings.web.port)`.
2. Preserve backward-compat: invocations like `python -m blackvuesync <address>`
   still work as the sync flow (the positional `address` arg signals "sync mode").
   `python -m blackvuesync serve` invokes the server.
3. Add `--config-path` global option (already implicit via
   `BLACKVUESYNC_CONFIG_PATH` env var; expose as CLI for symmetry).
4. Add `serve --port PORT` override option so a quick local dev run can use a
   port other than the settings.json one.
5. Smoke-test:
   ```bash
   python -m blackvuesync --help     # subcommand list visible
   python -m blackvuesync sync --help
   python -m blackvuesync serve --help
   ```

### Task M11: Update Dockerfile + docker-compose.yml + blackvuesync.sh

Files: `Dockerfile`, `docker-compose.yml`, `blackvuesync.sh` (maybe).

Steps:

1. **Dockerfile**: expose port 8080 (`EXPOSE 8080`). Add a HEALTHCHECK
   pointing at `/healthz`:
   ```dockerfile
   HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
       CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz').read()" || exit 1
   ```
2. **docker-compose.yml**: add `ports: ["8080:8080"]` and `/config` volume
   binding (`./config:/config`).
3. **blackvuesync.sh**: unchanged. The cron-driven sync continues to work
   exactly as before; Phase C does NOT replace cron yet (Phase E does).
4. **Behave docker-mode**: the existing tests should still pass since
   the cron-driven sync is unchanged. Phase C's new test_routes_* tests are
   unit-level (Flask test client); no new Behave scenarios required for
   Phase C unless we want one.

### Task M12: Documentation

Files: `CLAUDE.md`, `README.md`, possibly `docs/api.md` (new).

Steps:

1. **CLAUDE.md** -- add a "Server" architecture section mentioning the
   `blackvuesync.server` package, `create_app`, auth modes, and the routes.
2. **README.md** -- add a "Web UI" section documenting:
   - First-run wizard at `http://host:8080/`
   - `BLACKVUESYNC_ADMIN_USERNAME` and `BLACKVUESYNC_ADMIN_PASSWORD` bootstrap
     env vars (password hashing handled at first run)
   - The three auth modes and how to configure them via settings.json
   - Reverse proxy example (Caddy snippet)
   - Recovery procedure: edit settings.json, clear `password_hash`, restart.
3. **docs/api.md** (new) -- short endpoint reference covering /healthz,
   /readyz, /login, /logout, /first-run, /, /settings, /logs, /stats,
   /viewer. ~100 lines.

---

## Verification

Run before opening PR:

- `pytest test/ -v` -- 204 + new tests pass (~250+ tests total expected).
- `pytest test/test_settings.py --cov=blackvuesync/settings.py` -- still 100%.
- `pytest test/test_auth.py test/test_routes_*.py --cov=blackvuesync/server` --
  target ≥85% coverage on the server package.
- `mypy blackvuesync/` -- clean.
- `pre-commit run --all-files` -- clean.
- `behave --no-capture` -- existing 21 scenarios still pass.
- `behave -D implementation=docker` -- existing 21 docker-mode scenarios
  still pass.
- Manual smoke:
  ```bash
  rm -rf /tmp/cfg && BLACKVUESYNC_CONFIG_PATH=/tmp/cfg/settings.json \
    python -m blackvuesync serve --port 8080 &
  curl http://localhost:8080/healthz       # {"status":"ok"}
  curl -L http://localhost:8080/           # redirects to /first-run
  # POST a password to /first-run; then GET / again -> redirects to /login
  ```

## Branch protection workflow

Push to `web-foundation-phase-c`. Open PR titled
`Web Foundation Phase C: server skeleton, auth, first-run`. All five required
checks must pass. **Squash and merge** or **Rebase and merge** (linear
history rule).

The 4 cognitive-complexity warnings carried forward from Phase A should drop
by 1-2 as `__main__.py`'s `main()` gets restructured into subcommand dispatch
(its complexity should fall below the 15 threshold once `serve` and `sync`
become separate functions).

## After Phase C merges

Phase D is "Progress publisher + downloader callback hook" per the design
spec's Section 1 (the progress emitters). It's a small phase relative to C.
Plan to be written when this lands.

---

## Self-review

Done inline:

- **Spec coverage:** Sections 3, 4 of the design spec covered.
- **Placeholder scan:** No TBDs.
- **Type consistency:** `create_app(settings_store)` factory; `settings_store`
  attached to `app` instance; routes access via `current_app.settings_store`.
- **Scope:** Strictly Phase C. Cron retirement is Phase E. Settings UI is
  sub-project #3. Dashboard is sub-project #2.
- **Ambiguity:** A few decisions deferred to implementer judgment:
  - Whether to ship a separate `app.config["TESTING"]=True` toggle in
    `create_app` (probably yes for test client convenience).
  - Whether to read `auth.mode` once at `create_app` time or fresh per
    request. Plan says fresh per request (changeable without restart),
    which is the spec's intent.
  - Whether to vendor the Apple-design Inter font file or rely on system
    font stack. Plan says system stack (no font file shipped) for licensing
    simplicity.

Known limitation: the rate-limit lockout is in-memory only -- resets on
process restart. Acceptable for single-instance personal-use; if a future
phase clusters the service, this would need a shared store.
