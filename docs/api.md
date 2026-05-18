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
