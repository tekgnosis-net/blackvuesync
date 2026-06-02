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

When a download is interrupted, the partial file is preserved and the next
sync resumes it via an HTTP range request (`Range: bytes=N-`). If the dashcam
responds with `200` instead of `206`, the sync falls back to a full
re-download.

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

---

## Health API Endpoints

### `GET /api/health/storage`

Returns storage usage at the destination directory. Uses `shutil.disk_usage`
so the `used_percent` value matches what the sync engine sees for its
`max_used_disk_percent` threshold check (root-reserved blocks count as free).

```json
{
  "available": true,
  "destination": "/recordings",
  "total_bytes": 137438953472,
  "free_bytes": 50725394432,
  "used_bytes": 86713559040,
  "used_percent": 63.1,
  "recording_count": 481
}
```

When the destination does not exist on disk:

```json
{"available": false, "reason": "destination not configured"}
```

### `GET /api/health/dashcam`

HEAD-probes `http://<settings.connection.address>/blackvue_vod.cgi` with a
2-second timeout. The fixed timeout intentionally diverges from
`connection.timeout_seconds` so a slow dashcam does not block the dashboard.

Success:

```json
{"reachable": true, "address": "192.168.1.50", "latency_ms": 38.0}
```

Failure (`URLError` wrapping a timeout is classified as `reason: "timeout"`
for ui consistency with `socket.timeout`):

```json
{"reachable": false, "address": "192.168.1.50", "reason": "timeout"}
```

When no address is configured:

```json
{"reachable": false, "reason": "no address configured"}
```

---

## Dashcam API Endpoints

### `GET /api/dashcam/info`

Read-only inspection of the dashcam's on-camera configuration. Fetches
`http://<address>/Config/version.bin` and `http://<address>/Config/config.ini`
(BlackVue firmware is HTTP-only), parses them defensively, and returns
structured JSON. Changing settings is deliberately out of scope (a future
sub-project); this endpoint never writes to the camera.

Available (firmware may be null if version.bin was unreachable while
config.ini succeeded -- partial availability still reports available: true):

```json
{
  "available": true,
  "address": "192.168.1.50",
  "firmware": "DR900X-2.013",
  "config": {"Tab1": {"Resolution": "4K"}, "Tab3": {"Voice": "ON"}},
  "setting_count": 2
}
```

Unreachable or no address configured:

```json
{"available": false, "reason": "dashcam unreachable"}
```

```json
{"available": false, "reason": "no address configured"}
```

---

## Recordings API Endpoints

### `GET /api/recordings/recent`

Returns the N most recently modified BlackVue recordings at the destination
(matched via `filename_re.fullmatch`). Default `limit` is 5; clamped to
`[1, 50]` via query param `?limit=N`.

```json
{
  "recordings": [
    {
      "filename": "20231015_120000_NF.mp4",
      "mtime": 1697371200.0,
      "path": "/recordings/20231015_120000_NF.mp4"
    }
  ],
  "total": 1
}
```

---

## Schedule API Endpoints

### `POST /api/schedule/pause`

Sets `settings.schedule.paused = true`. The next scheduled sync is skipped
(the scheduler logs `scheduled sync skipped: schedule is paused`). Manual
`POST /api/sync/now` is unaffected -- operators can still trigger ad-hoc
syncs.

```json
{"paused": true}
```

### `POST /api/schedule/resume`

Sets `settings.schedule.paused = false`. Idempotent: returns 200 even when
the schedule was already running.

```json
{"paused": false}
```

---

## Dashboard Controls (Phase 2C)

The dashboard UI added in Phase 2C drives Sync-now, Stop (modal-confirmed), and
Pause/Resume directly against the existing `/api/sync/*` and `/api/schedule/*`
endpoints listed in this document. No new endpoints were added in 2C. The
`dashboard.js` Alpine component opens an SSE subscription to
`/api/sync/progress/stream` on page load and reflects `SyncProgress.state` onto
`body[data-state]`; CSS drives the active/idle layout swap.

---

## Sync API Endpoints (additions)

### `POST /api/sync/stop`

Requests cooperative stop of the active sync. The download chunk loop in
`sync.py` checks the stop flag between chunks and raises
`UserWarning("sync stopped by user")` on its next boundary, which the
existing exception classifier routes through the normal `failed` exit path.
The partial `.filename.mp4` dotfile survives; the next sync resumes it
naturally.

Returns 202 when a sync was running:

```json
{"job_id": "deadbeef...", "stopping": true}
```

Returns 404 when no sync is active:

```json
{"error": "no sync is running", "code": "SYNC_NOT_RUNNING", "details": {}}
```

---

## HTMX Fragment Endpoints (additions)

Four new card fragments. Each fragment renders the matching
`_partials/*.html` template, which includes an `hx-trigger="every 5s"`
attribute so the card self-polls once a dashboard template (Phase 2B)
embeds it. No client currently mounts these fragments.

- `GET /hx/storage-card` -- renders `_partials/storage_card.html` with the
  same data as `/api/health/storage`
- `GET /hx/dashcam-card` -- renders `_partials/dashcam_card.html` with the
  same data as `/api/health/dashcam`
- `GET /hx/next-scheduled-card` -- renders
  `_partials/next_scheduled_card.html` with the next cron fire time, paused
  flag, cron expression, and timezone
- `GET /hx/recent-activity-card` -- renders
  `_partials/recent_activity_card.html` with the same data as
  `/api/recordings/recent` (default `limit=5`)
- `GET /hx/dashcam-info-card` -- renders `_partials/dashcam_info_card.html`.
  Loads once on page load and refreshes every 60s (config is near-static and
  the fetch is two files, so it polls slower than the 5s cards).
