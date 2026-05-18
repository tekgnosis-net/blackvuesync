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
