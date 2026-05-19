# HTTP API Reference

This document describes the HTTP endpoints exposed by `blackvuesync serve`
(and the Docker container on port 8080).

---

## Health Endpoints

These endpoints are exempt from authentication and from the first-run redirect.

### `GET /healthz`

Liveness probe. Returns `200 OK` immediately.

```json
{"status": "ok"}
```

### `GET /readyz`

Readiness probe. Returns `200 OK` once the settings store has loaded.
Returns `503 Service Unavailable` while the process is still starting.

```json
{"status": "ready", "settings_loaded": true}
```

```json
{"status": "starting", "settings_loaded": false}
```

---

## Auth Endpoints

### `GET /first-run`

Displays the first-run setup wizard. Redirected to automatically when
`auth.password_hash` is empty.

### `POST /first-run`

Submits the initial password. Requires the `X-CSRFToken` header (or
`_csrf_token` form field). Password must be at least 12 characters.

| Field | Required | Description |
| --- | --- | --- |
| `password` | yes | Initial admin password (min 12 chars) |
| `password_confirm` | yes | Confirmation; must match `password` |

On success: redirects to `/`.

On error: re-renders the form with a validation message.

### `GET /login`

Renders the login form. Redirected to when a protected page is accessed
without a valid session (auth mode `login` only).

### `POST /login`

Authenticates the user. Requires the `X-CSRFToken` header.

| Field | Required | Description |
| --- | --- | --- |
| `username` | yes | Admin username (from `auth.username`) |
| `password` | yes | Admin password |

- On success: sets a session cookie and redirects to `/`.
- On failure: re-renders the form with a generic error after a minimum
  delay of 1.5 seconds (uniform-timing defence).
- After 10 failures from the same IP within 10 minutes: requests are
  rejected for 15 minutes.

### `POST /logout`

Clears the session and redirects to `/login`. Requires an active session
(or auth mode `none`/`proxy`).

---

## UI Endpoints

All UI endpoints require authentication (subject to `auth.mode`).

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Dashboard (recording stats overview) |
| `GET` | `/settings` | Settings editor |
| `GET` | `/logs` | Recent sync log viewer |
| `GET` | `/stats` | Metrics and charts |
| `GET` | `/viewer` | In-browser recording viewer |

> **Note:** these pages are placeholder stubs in Phase C. Full
> implementations will be added in subsequent phases.

---

## Sync API Endpoints

All endpoints below require authentication (subject to `auth.mode`).
CSRF protection applies to all `POST` requests (Flask-WTF global protection).

### `GET /api/sync/progress`

Returns the current sync progress snapshot as JSON.

**Response (200 OK):**

```json
{
  "job_id": "a1b2c3d4e5f6...",
  "started_at_wall": 1747603200.0,
  "state": "running",
  "current_file": {
    "filename": "20230101_120000_NF.mp4",
    "recording_base": "20230101_120000_NF",
    "artifact": "mp4",
    "direction": "F",
    "total_bytes": 52428800,
    "downloaded_bytes": 10485760,
    "bytes_per_second": 2097152.0,
    "eta_seconds": 20.0,
    "state": "downloading",
    "failure_reason": null
  },
  "files_total": 12,
  "files_completed": 3,
  "files_failed": 0,
  "bytes_downloaded_total": 157286400
}
```

When no sync has run, `state` is `"idle"` and most fields are zero.

### `GET /api/sync/progress/stream`

Server-Sent Events (SSE) stream of progress updates.

**Response headers:**

```http
Content-Type: text/event-stream
Cache-Control: no-store
X-Accel-Buffering: no
```

**Event format:**

```text
event: progress
data: {"state": "running", "files_completed": 3, ...}

```

Events are throttled to 5 Hz. When no state change occurs for 30 seconds,
a keepalive comment is emitted to keep the connection alive:

```text
: keepalive

```

**Example (curl):**

```bash
curl -N -H "Cookie: bvs_session=<token>" \
  http://localhost:8080/api/sync/progress/stream
```

### `POST /api/sync/now`

Triggers an on-demand sync. Requires the `X-CSRFToken` header.

**Response (202 Accepted) -- sync started:**

```json
{"job_id": "a1b2c3d4e5f6..."}
```

**Response (409 Conflict) -- sync already running:**

```json
{
  "error": "sync already running",
  "code": "SYNC_ALREADY_RUNNING",
  "details": {"current_job_id": "a1b2c3d4e5f6..."}
}
```

**Example (curl):**

```bash
curl -X POST \
  -H "Cookie: bvs_session=<token>" \
  -H "X-CSRFToken: <token>" \
  http://localhost:8080/api/sync/now
```

### `GET /api/sync/last`

Returns the most recently completed (or running) sync snapshot.

- **204 No Content** -- no sync has ever run in this server session.
- **200 OK** -- returns the same JSON body as `/api/sync/progress`.

---

## HTMX Fragment Endpoints

These endpoints return HTML fragments intended for use with HTMX polling
(`hx-get`, `hx-trigger="every 5s"`). Both require authentication.

### `GET /hx/sync/status-card`

Returns the `sync-status-card` HTML partial showing current sync state,
progress bar, and current file information.

### `GET /hx/sync/last-run-card`

Returns the `last-run-card` HTML partial showing the most recently completed
sync run (files synced, bytes downloaded, completion state).

---

## Settings API Endpoints

All endpoints below require authentication (subject to `auth.mode`).
CSRF protection applies to all `PATCH` requests (Flask-WTF global protection).

### `GET /api/settings`

Returns the full settings object as JSON. Every section carries a `_tier`
annotation (`immediate`, `next_tick`, or `restart`) indicating how quickly
a change to that section propagates. Secret fields (currently
`auth.password_hash` and `auth.session_secret`) are always replaced with the
sentinel string `"***"` regardless of whether they are empty. This prevents
the redacted snapshot from leaking the first-run state (`password_hash == ""`)
and lets clients safely round-trip the response back through a `PATCH`.

**Response (200 OK), truncated example:**

```json
{
  "version": 1,
  "connection": {
    "address": "192.168.0.1",
    "timeout_seconds": 10.0,
    "_tier": "restart"
  },
  "auth": {
    "mode": "login",
    "username": "admin",
    "password_hash": "***",
    "session_secret": "***",
    "trusted_proxies": [],
    "_tier": "immediate"
  },
  "sync": {
    "priority": "date",
    "grouping": "none",
    "include": [],
    "exclude": [],
    "retry_failed_after": "1d",
    "skip_metadata": [],
    "_tier": "next_tick"
  }
}
```

### `PATCH /api/settings/<section>`

Updates a single settings section partially. The request body is a JSON
object containing only the fields to change; missing fields are left
unchanged. Fields whose value is the redaction sentinel `"***"` are stripped
before applying, so a client may post back the full GET response without
overwriting secrets. JSON arrays are coerced to tuples for the
`sync.include`, `sync.exclude`, and `sync.skip_metadata` fields so the
in-memory dataclass remains tuple-typed (JSON has no tuple).

**Request body example (`PATCH /api/settings/sync`):**

```json
{
  "priority": "rdate",
  "include": ["P", "NF"]
}
```

**Response (200 OK):**

```json
{"section": "sync", "tier": "next_tick", "applied": true}
```

**Error responses:**

- `400 Bad Request` -- `INVALID_BODY` when the request body is not a JSON object.
- `404 Not Found` -- `SECTION_NOT_FOUND` when `<section>` is not a known
  settings section.
- `422 Unprocessable Entity` -- `SETTINGS_INVALID` when the payload contains
  an unknown field or fails section-level validation. The `details.field_errors`
  array enumerates the failing paths and messages.

```json
{
  "error": "settings validation failed",
  "code": "SETTINGS_INVALID",
  "details": {
    "field_errors": [
      {"path": "sync.priority", "message": "must be one of date, rdate, type"}
    ]
  }
}
```

---

## Auth API Endpoints

All endpoints below require authentication (subject to `auth.mode`).
CSRF protection applies to all `POST` and `DELETE` requests.

### `GET /api/auth/me`

Returns the current authenticated user and the active auth mode. The mode is
read fresh from the settings store on every request, so a mode change in
`/config/settings.json` takes effect immediately without a restart.

**Response (200 OK):**

```json
{"username": "admin", "mode": "login"}
```

### `POST /api/auth/password`

Changes the current user's password. Requires the current password as well
as the new password (minimum 12 characters). Failures consume the same
rate-limit bucket as `POST /login` (10 failures from the same IP within
600 seconds triggers a 15-minute lockout).

**Request body:**

```json
{"current_password": "<old>", "new_password": "<new>"}
```

**Response (200 OK):**

```json
{"applied": true}
```

**Error responses:**

- `400 Bad Request` -- `INVALID_BODY` when the request body is not a JSON object.
- `401 Unauthorized` -- `INVALID_CURRENT_PASSWORD` when the current password
  does not match the stored hash; also increments the rate-limit bucket.
- `422 Unprocessable Entity` -- `WEAK_PASSWORD` when the new password is
  shorter than 12 characters.
- `429 Too Many Requests` -- `RATE_LIMITED` when the IP has exceeded the
  shared `/login` failure threshold.

### `DELETE /api/auth/sessions`

Rotates the session secret (`auth.session_secret`) to a fresh random value.
All existing sessions become invalid once the new secret is loaded. Flask
reads `SECRET_KEY` once at `create_app()` time, so the running process
continues using the old secret until restart; the response makes this
explicit with `restart_required: true`.

**Response (200 OK):**

```json
{"rotated": true, "restart_required": true}
```
