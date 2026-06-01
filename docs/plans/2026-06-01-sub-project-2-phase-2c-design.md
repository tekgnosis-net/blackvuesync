# Sub-Project #2 Phase 2C -- Active Mode + Controls -- Design Spec

**Date:** 2026-06-01
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design -- final phase of the Dashboard sub-project
**Parent design:** `docs/plans/2026-05-19-sub-project-2-dashboard-design.md` (this
doc consolidates the 2C-specific design and resolves its deferred open questions).

---

## Context

Phases 2A (backend) and 2B (idle UI) are merged. The dashboard currently renders
a read-only idle grid; the sidebar `Sync now` and `Pause schedule` buttons are
**disabled placeholders** ("Live controls arrive in the next update"), `<body>`
has no `data-state`, and there is no `dashboard.js`, no active-mode hero, and no
Stop control. 2C turns the dashboard interactive.

**Key scoping fact: the backend already exists** (built in 2A / the foundation):

| Capability | Endpoint / state | Location |
| --- | --- | --- |
| Trigger sync | `POST /api/sync/now` (202 + job_id, 409 if running) | `routes/api_sync.py` |
| Stop running sync | `POST /api/sync/stop` -> `request_stop()` | `routes/api_sync.py:137` |
| Pause / resume schedule | `POST /api/schedule/pause` \| `/resume` -> `schedule.paused` | `routes/api_schedule.py` |
| Scheduler honors pause | skips scheduled run when `schedule.paused` | `scheduler.py:36` |
| Progress snapshot / stream | `GET /api/sync/progress` + `/progress/stream` (SSE) | `routes/api_sync.py` |
| `paused` passed to templates | `ui.py:42`, `hx_dashboard.py:90` | -- |

**2C is therefore a frontend-wiring phase.** No new endpoints. The risk
concentrates in JavaScript (EventSource lifecycle, reconnect backoff, the Stop
modal) -- the layer pytest/behave cannot reach -- which drives the test strategy
below.

---

## Resolved open questions (deferred to 2C by the parent design)

1. **SSE reconnect-backoff curve** -- exponential `2s -> 4s -> 8s -> ... -> 30s`
   cap, supplementing (not replacing) `EventSource`'s built-in retry. (Confirmed;
   the parent design already specified these constants.)
2. **Stop-button confirmation UX** -- **modal confirm** ("Stop the running sync?
   The current file resumes next run."), then `POST /api/sync/stop`. The modal
   reassures rather than warns: PR #13 made stopping low-regret (the in-flight
   file resumes on the next run instead of restarting from zero).
3. **Card-fetch error logging** -- **not logged** to the structured handler. The
   dashcam enters power-save (to avoid draining the car battery), so an
   "unreachable" card is a *normal, expected* state, not an error; logging it
   (especially per 5s poll) would flood the log with legitimate-offline noise.
   The card's unreachable UI is the only signal. (Debug level reserved for
   genuinely unexpected fetch errors; off in normal operation.)

---

## Design

### 1. State machine -- `<body data-state="idle|running|complete">`

A single source of truth. `base.html` gains a `data-state` on `<body>` defaulting
to `idle` (overridable by a block); the dashboard route renders the initial state
from the current `SyncProgress.state`. CSS alone drives the visual swap --
`body[data-state="running"] .active-only { display: block }` and the inverse for
idle cards; **JavaScript never hides/shows elements directly, it only mutates the
attribute.** On `running`, the SSE-driven hero (reusing `sync_status_card.html`
from Phase D) replaces the Last-Sync card. On `complete`, the hero shows a ~10s
summary (matching the publisher's `POST_COMPLETE_RETENTION = 10.0`) then reverts
to idle. Fully reversible.

### 2. `dashboard.js` -- Alpine.js component (new, external file)

Owns the SSE connection lifecycle:

- Watches `data-state`; opens an `EventSource` to `/api/sync/progress/stream`
  when state becomes `running`, closes it on a terminal frame
  (`complete`/`failed`).
- Reflects `SyncProgress.state` onto `<body data-state>` from whichever source is
  newer -- the idle HTMX poll (`/hx/sync/last-run-card`) or the SSE stream -- using
  the monotonic `last_event_monotonic` to discard stale snapshots if the two
  disagree.
- **Reconnect backoff** `2s -> 4s -> 8s -> ... -> 30s` cap on stream break;
  resets on a clean frame.
- **Unified 302 -> /login**: an `htmx:beforeOnLoad` 302 detector and the SSE
  `EventSource.onerror` path both redirect to `/login?next=<currentPath>`
  (~20 LoC shared).

Loaded via a new `{% block extra_js %}` in `base.html`. The foundation CSP
(`script-src 'self' 'unsafe-inline'`) permits an external `/static/js/dashboard.js`
('self'); external keeps the inline surface minimal.

### 3. Controls (sidebar) -- wired to the existing endpoints, CSRF-protected

The two disabled 2B buttons become live, and a Stop control is added:

| Control | Action | Visibility |
| --- | --- | --- |
| **Sync now** | `POST /api/sync/now` -> 202 -> state `running` -> SSE opens | always (disabled while `running`) |
| **Stop** | **modal confirm** -> `POST /api/sync/stop` -> `_stop_event.set()` -> next chunk raises `UserWarning` -> publisher reports `failed` (reason "stopped by user") -> SSE closes | only when `running` |
| **Pause / Resume** | toggle reflecting `schedule.paused`: `POST /api/schedule/pause` or `/resume` | always; label/state reflects `paused` |

Pause skips **scheduled** runs only; manual `Sync now` still works while paused.
`schedule.paused` is persisted (survives restart) -- an intentional pause is not
silently undone by a container restart.

### 4. Stop confirmation modal

A lightweight, accessible modal (focus-trapped, `Esc`/backdrop to cancel,
`aria-modal`): title "Stop the running sync?", body "The current file resumes on
the next run.", actions `[Cancel]` / `[Stop]`. CSS-driven visibility (no new JS
framework); Alpine toggles an `open` flag. Confirm fires the `POST /api/sync/stop`.

### 5. Card-fetch errors -- not logged

HTMX card fetches that fail (notably dashcam-unreachable during power-save) render
the card's existing error/unreachable state and are **not** logged to the
structured handler. No new logging code is added for the expected-unreachable
path; if any diagnostic is wanted for genuinely unexpected fetch failures it stays
at debug level (off in normal operation).

### 6. Graceful degradation (JS disabled)

First-load is fully server-rendered: all idle cards present and readable without
JS (the dashboard route renders the current state server-side). With JS disabled,
idle polling and SSE simply don't run; the active-mode region shows a static
"JavaScript required for live progress" notice. Read-only state remains usable.

### 7. Edge cases (from the parent design, carried in)

| Situation | Handling |
| --- | --- |
| SSE stream breaks mid-sync | Alpine backoff `2s->4s->8s->30s`; idle poll continues |
| Stop clicked when idle | endpoint is a no-op/`SYNC_NOT_RUNNING`; Stop hidden when `state != running` (defensive) |
| Pause when paused / Resume when running | idempotent -- 200 with current state |
| Idle poll and SSE disagree on `state` | newer `last_event_monotonic` wins |
| Any 302 to `/login` | unified redirect across HTMX, SSE, fetch |

---

## Files

**Create:**

- `blackvuesync/server/static/js/dashboard.js` -- Alpine SSE/control component.
- `blackvuesync/server/templates/_partials/stop_confirm_modal.html` -- the modal.
- `test/test_dashboard_sse_handoff.py` -- server-rendered structure tests (see Testing).
- `test/e2e/test_dashboard_active_mode.py` (+ Playwright config/fixtures) -- browser smoke.

**Modify:**

- `blackvuesync/server/templates/base.html` -- `data-state` on `<body>` (default
  `idle`, block-overridable); add `{% block extra_js %}`.
- `blackvuesync/server/templates/dashboard.html` -- enable + wire the sidebar
  controls (Sync now / Stop / Pause-Resume), add the active-mode hero region
  (`.active-only`, reusing `sync_status_card.html`), include the Stop modal, set
  initial `data-state` from the current progress state, load `dashboard.js`.
- `blackvuesync/server/static/css/dashboard.css` -- `body[data-state]` show/hide
  rules, hero gradient (already specced in 2B), modal styles, control states.
- `blackvuesync/server/routes/ui.py` -- pass the initial `SyncProgress.state` (and
  any control context) to `dashboard.html`.
- `pyproject.toml` -- version bump; add any new test modules to the mypy override
  list; add Playwright as a test-only dependency.
- `docs/api.md` -- note the dashboard now drives sync/stop/pause from the UI (no
  new endpoints).

---

## Testing

Per the resolved decision: **Playwright browser smoke + server-rendered structure
tests**, layered over the already-tested backend.

- **Structure tests** (`test_dashboard_sse_handoff.py`, pytest + Flask client):
  initial `data-state="idle"`; hero region present with `.active-only`; the three
  controls render with correct `hx-post`/CSRF and the Stop control hidden when not
  running; Pause/Resume label reflects `schedule.paused`; `dashboard.js` is loaded
  via `extra_js`; CSP unaffected.
- **Existing endpoint coverage** (no change): `test_routes_api_sync.py` already
  covers `/api/sync/now`, `/stop`, `/progress`, `/progress/stream`;
  `test_routes_api_schedule.py` covers pause/resume; `test_scheduler_pause.py`
  covers the scheduler skip.
- **Playwright smoke** (`test/e2e/`): drive a real browser against a running
  server -- trigger Sync now, assert `body[data-state]` becomes `running` and the
  hero + SSE appear, then `complete`, then idle; and the Stop modal confirm path.
  Run against the app with a mock/fast sync. Wire into CI as its own job (browser
  install); keep the smoke minimal (1-2 scenarios) to bound CI cost. If the CI
  browser proves flaky, the structure tests still gate; the e2e is the
  behavior-confidence layer.

The exact phase coverage target and LoC (~400 LoC + ~16 tests) come from the
parent design's phase table.

---

## Self-review

- **Placeholders:** none -- every control maps to an existing endpoint; every file
  has a stated responsibility.
- **Consistency:** the three resolved decisions (modal Stop, no card-fetch
  logging, Playwright+structure tests) are reflected in the Controls, Card-fetch,
  and Testing sections respectively; the "backend exists" scoping matches the
  Files list (no route creation, only `ui.py` context wiring).
- **Scope:** focused on a single phase; frontend-only plus minimal `ui.py`
  context. No backend endpoints, no settings-schema changes (`paused` already
  exists).
- **Ambiguity:** state ownership is pinned to `body[data-state]` mutated only by
  JS; visibility is CSS-only; the idle-poll-vs-SSE tie-break is the monotonic
  timestamp. Stop is modal-confirmed; unreachable is a neutral, unlogged state.
- **Risk:** JS is the untestable-by-pytest layer; mitigated by the Playwright
  smoke plus server-rendered structure tests and the already-tested endpoints.
