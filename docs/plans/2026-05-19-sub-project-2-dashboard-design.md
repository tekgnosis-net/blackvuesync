# Sub-Project #2: Dashboard -- Design Spec

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 MD060 -->

**Date:** 2026-05-19
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Master spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) -- table of sub-projects, locked architectural choices.
**Status:** Design approved through brainstorming session 2026-05-19; awaiting user review of this written spec before invoking writing-plans.

---

## Context

The Web Foundation (sub-project #1) shipped seven phases A-G into main between 2026-05-18 and 2026-05-19. It established the long-running Flask + APScheduler service, the settings store, the auth flow, the sync API, the Apple-design scaffolding, and a placeholder dashboard route.

Sub-project #2 replaces the placeholder at `/` with the operational dashboard the master design promised. It is the first sub-project to consume the foundation's API surface as a real user-facing interface; everything it needs is either already on `main` or is added as backend work in this sub-project's Phase 2A.

The dashboard has **two visual states**, driven by `SyncProgress.state` from the foundation's ProgressPublisher:

- **idle / complete / failed** -- status overview: 6 cards in a grid, polled every 5 s
- **running** -- live console: hero progress card driven by Server-Sent Events, five supporting cards remain

The transition between states is driven by a single source of truth (`<body data-state="...">`) and is fully reversible -- when a sync completes, the hero fades and the idle grid returns.

---

## Locked architectural choices

These were settled during the 2026-05-19 brainstorm and apply across all three phases:

| Concern | Choice | Reason |
|---|---|---|
| Primary use case | Both: status-at-a-glance + live ops console | Operators need both modes; one state machine swaps between them |
| Layout | Persistent left sidebar (220 px) + grid in main | Sidebar holds quick actions + identity; main is the data surface |
| Light palette | Apple canonical: bg `#f5f5f7`, cards `#fff`, border `#d2d2d7`, text `#1d1d1f`, accent `#007aff` | Validated by side-by-side comparison; softer-gray variant was tested and rejected |
| Dark palette | bg `#1c1c1e`, cards `#2c2c2e`, border `#3a3a3c`, text `#f5f5f7`, accent `#0a84ff` | Standard Apple system dark tokens |
| Mode switching | `@media (prefers-color-scheme: dark)` only | No manual toggle in #2; deferred to sub-project #3 (Settings UI) if requested |
| Refresh strategy | HTMX 5 s polling for idle cards; SSE only when sync is active | Reuses Phase D infrastructure exactly as designed; foundation's stored decision |
| Active-mode transformation | Hero banner replaces "Last sync" slot; other cards remain | Operator sees "what's happening now" + "rest of system" together |
| Operational controls | Sync now (exists) + Pause schedule + Stop sync | All three sidebar buttons functional in #2 |
| Pause semantics | Pause skips **scheduled** runs only; manual Sync now still works | Standard cron/manual split in operational tools |
| Stop semantics | Cooperative -- sets `threading.Event`; `download_with_resume` checks between chunks; partial `.filename.mp4` survives | Reuses existing dotfile pattern; next sync resumes naturally |
| JS dependency | Idle mode renders fully server-side on first load; SSE active mode requires JS | Graceful degradation: read-only state works without JS |
| Phase split | 2A backend → 2B idle UI → 2C active+controls | Each phase shippable; mirrors foundation A-G cadence |

---

## Design Section 1: Architecture

### Page structure

```
┌─────────────────────────────────────────────────────────────────┐
│ Top header (existing from Phase C base.html)                    │
│  logo · Dashboard | Settings | Logs | Stats | Viewer  · Sign out│
├─────────────────────────────────────────────────────────────────┤
│ Sidebar (220 px)    │ Main column                               │
│                     │ ┌────────────────────────────────────────┐│
│ ▶/⏹ Sync now/Stop   │ │ hero (active-only) -- SSE-driven        ││
│ ⏸ Pause schedule    │ └────────────────────────────────────────┘│
│                     │ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────┐ │
│ admin               │ │ Last/  │ │ Next   │ │Storage │ │Cam   │ │
│ login mode          │ │ Active │ │        │ │        │ │      │ │
│                     │ └────────┘ └────────┘ └────────┘ └──────┘ │
│                     │ ┌────────────────────────────────────────┐│
│                     │ │ Recent activity                        ││
│                     │ └────────────────────────────────────────┘│
└─────────────────────┴───────────────────────────────────────────┘
```

The sidebar collapses to a top action row below 720 px viewport width (single `@media (max-width: 720px)` rule). Cards stack to single-column at that breakpoint. No JavaScript involved in the responsive collapse.

### State machine

```
                  ┌────────────────────────────────────┐
   page load ────▶│  body[data-state="idle"]           │◀──┐
                  │  · 6 cards visible, HTMX polling   │   │
                  └──┬─────────────────────────────────┘   │
                     │ Sync now / cron tick                │
                     │ state → running                     │
                  ┌──▼─────────────────────────────────┐   │
                  │  body[data-state="running"]        │   │
                  │  · hero replaces Last sync         │   │
                  │  · SSE EventSource open            │   │
                  │  · Stop button visible             │   │
                  └──┬─────────────────────────────────┘   │
                     │ SSE reports terminal               │
                     │ state → complete/failed             │
                  ┌──▼─────────────────────────────────┐   │
                  │  body[data-state="complete"]       │   │
                  │  · SSE closes, hero shows summary  │   │
                  │  · 10 s retention (POST_COMPLETE_  │   │
                  │    RETENTION from ProgressPublisher)│  │
                  └──┬─────────────────────────────────┘   │
                     │ ProgressPublisher resets to idle    │
                     └─────────────────────────────────────┘
```

The transition timing is driven entirely by the existing ProgressPublisher's `POST_COMPLETE_RETENTION` (10 s); dashboard code does not implement its own timer.

### Three-phase delivery

| Phase | Scope | Approximate size |
|---|---|---|
| **2A** Backend | 6 new endpoints + 4 new HTMX fragments + sync.py stop flag + scheduler pause hook + new settings field | ~500 LoC + ~30 tests |
| **2B** Idle UI + dashcam info | `dashboard.html` template replaces placeholder, sidebar layout, 6 cards rendered, HTMX polling wired. Adds `GET /api/dashcam/info` + `/hx/dashcam-info-card` for the new dashcam info card (read-only inspection of `/Config/version.bin` and `/Config/config.ini`). | ~500 LoC + ~15 tests |
| **2C** Active mode + controls | SSE EventSource wiring, mode transitions, Sync now/Stop/Pause sidebar wiring, exponential backoff | ~400 LoC + ~16 tests |

Each phase ships as its own PR through the same branch-protection cadence the foundation used (5 required CI checks; spec-compliance review then code-quality review).

---

## Design Section 2: Components

### Backend additions (Phase 2A)

| New file | Responsibility |
|---|---|
| `blackvuesync/server/routes/api_health.py` | `GET /api/health/storage` (statvfs + walk to count recordings), `GET /api/health/dashcam` (HEAD probe to `http://<address>/blackvue_vod.cgi` with 2 s timeout) |
| `blackvuesync/server/routes/api_recordings.py` | `GET /api/recordings/recent?limit=N` -- newest N filenames from destination, parsed via `filename_re`, sorted by mtime descending |
| `blackvuesync/server/routes/api_schedule.py` | `POST /api/schedule/pause`, `POST /api/schedule/resume` -- toggle `settings.schedule.paused` |
| `blackvuesync/server/routes/hx_dashboard.py` | 4 fragments: `/hx/storage-card`, `/hx/dashcam-card`, `/hx/next-scheduled-card`, `/hx/recent-activity-card` -- each renders a Jinja2 partial |

### Backend modifications (Phase 2A)

| File | Change |
|---|---|
| `blackvuesync/sync.py` | Add module-level `_stop_event: threading.Event`. `download_with_resume`'s chunk loop calls `_stop_event.is_set()` after each `socket.recv()`; if set, raises `UserWarning("sync stopped by user")` -- caught by existing exception classifier which routes it through the normal `failed` exit path. Add `request_stop()` / `clear_stop()` helpers. `trigger_sync` calls `clear_stop()` before spawning. |
| `blackvuesync/server/scheduler.py` | `_scheduled_run` reads `store.get().schedule.paused`; if `True`, logs `"scheduled sync skipped: schedule is paused"` at INFO and returns without calling `trigger_sync`. Manual `trigger_sync` (called by `/api/sync/now`) ignores pause. |
| `blackvuesync/server/routes/api_sync.py` | Add `POST /api/sync/stop` -- calls `request_stop()`; returns 202 + `{job_id}` if a sync was running, 404 + `SYNC_NOT_RUNNING` if idle. |
| `blackvuesync/settings.py` | `ScheduleSettings` gains `paused: bool = False` field. TIER stays `next_tick` on the section. Validator: no constraint (any bool is valid). |
| `blackvuesync/server/routes/ui.py` | `dashboard()` view renders `templates/dashboard.html` instead of `templates/_placeholders/dashboard.html`. |

### Frontend additions (Phase 2B + 2C)

| New file | Phase | Responsibility |
|---|---|---|
| `blackvuesync/server/templates/dashboard.html` | 2B | Replaces the placeholder. Sidebar layout, card grid, Alpine.js `x-data` for state tracking. Server-renders all 6 cards on first load. |
| `blackvuesync/server/static/css/dashboard.css` | 2B | Sidebar grid, responsive collapse (<720 px → top bar), hero gradient (`linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%)`), mode-based show/hide via `body[data-state="..."]` |
| `blackvuesync/server/static/js/dashboard.js` | 2C | Alpine.js component: subscribes to `/api/sync/progress/stream` when state becomes `running`, closes EventSource when terminal. Updates `body[data-state]` attribute. Exponential backoff 2 s → 30 s on stream break. Handles 302 → `/login` redirect uniformly across HTMX, SSE, and API responses. |

### New templates (Phase 2A -- backend), consumed in 2B

| File | Lines | Cards |
|---|---|---|
| `blackvuesync/server/templates/_partials/storage_card.html` | ~30 | Storage card body (label, value, sub, progress bar) |
| `blackvuesync/server/templates/_partials/dashcam_card.html` | ~25 | Dashcam reachability with dot indicator |
| `blackvuesync/server/templates/_partials/next_scheduled_card.html` | ~25 | Next cron tick countdown + paused state |
| `blackvuesync/server/templates/_partials/recent_activity_card.html` | ~40 | Recent recordings list with elapsed time |

### Reused (no changes)

- `_partials/sync_status_card.html` (Phase D) → active-mode hero card
- `_partials/last_run_card.html` (Phase D) → idle-mode "Last sync" card
- `/api/sync/progress/stream` SSE endpoint (Phase D) → drives active mode
- `/api/sync/now` (Phase D) → wired to sidebar Sync now button
- `tokens.css`, `components.css`, `layout.css` (Phase C) → base styling
- `htmx.min.js`, `alpine.min.js` (Phase C) → vendored

---

## Design Section 3: Data flow

### Idle-mode polling (6 cards, every 5 seconds)

```
Browser
  │ hx-get="/hx/storage-card" hx-trigger="every 5s"
  ▼
Flask route (hx_dashboard.py)
  │ reads SettingsStore.get() / calls os.statvfs() / walks destination
  ▼
Jinja2 _partials/storage_card.html  →  rendered fragment
  ▼
HTMX swaps outerHTML on the card root
```

Each card polls independently. A slow `/api/health/dashcam` (2 s timeout) never blocks `/hx/storage-card`. No `hx-swap-oob` cross-card updates.

### Active-mode SSE

```
Alpine.js x-data watches body[data-state]
  │ on state="running" → new EventSource("/api/sync/progress/stream")
  ▼
Flask SSE generator (api_sync.py -- existing Phase D code)
  │ ProgressPublisher.subscribe() yields SyncProgress snapshots at 5 Hz
  │ keepalive comment (": keepalive") every 30 s
  ▼
event listener("progress", e) → JSON.parse(e.data)
  │ updates 5 DOM nodes: progress bar width, current file text,
  │ rate / ETA / files-done stats
  ▼
on state="complete"/"failed"/"idle" → EventSource.close()
```

The Alpine.js component owns the SSE connection lifecycle. Exponential backoff 2 s → 30 s if the stream breaks. The browser's built-in EventSource retry is supplemented (not replaced) by our explicit `close()` + reopen on schedule.

### Mode transition

A single source of truth: `SyncProgress.state` from either the idle poll (`/hx/sync/last-run-card`) or the SSE stream. Alpine.js reflects it on `<body data-state="...">`. CSS rules `body[data-state="running"] .active-only { display: block }` and the inverse drive the visual swap. No JavaScript directly hides/shows elements -- only the attribute mutates.

### User actions

| Action | Path |
|---|---|
| Sync now | `POST /api/sync/now` (CSRF) → 202 + `job_id` → state becomes `running` → Alpine.js opens SSE |
| Stop sync | `POST /api/sync/stop` (CSRF) → 202 → `_stop_event.set()` → next chunk raises `UserWarning` → SyncProgress reports `failed` with `reason="stopped by user"` → SSE closes |
| Pause schedule | `POST /api/schedule/pause` (CSRF) → 200 → settings persisted → `_scheduled_run` skips next cron tick. Manual Sync now unaffected. |
| Resume schedule | `POST /api/schedule/resume` (CSRF) → 200 → settings persisted → next cron tick fires normally |

---

## Design Section 4: Error handling

### Transient (auto-recover on next tick)

| Failure | Behavior |
|---|---|
| HTMX card fetch fails (5xx, network) | Card freezes at last value, shows `⚠` icon in corner. Next 5 s tick retries. No modal/toast. |
| SSE stream breaks mid-sync | Alpine.js exponential backoff 2 s → 4 s → 8 s → 30 s cap. Idle poll continues. |
| Dashcam probe times out | NOT a 5xx -- `/api/health/dashcam` returns `{reachable: false, reason: "timeout"}`. Card paints red dot + reason. Normal data, not error. |

### Structural (display as data, not error)

| Condition | Behavior |
|---|---|
| Storage probe on missing destination | `{available: false, reason: "destination not configured"}` → card shows `--` + reason |
| Recent activity on empty destination | Empty list, card shows "No recordings yet" |
| Sync now while running | 409 `SYNC_ALREADY_RUNNING` (existing) → inline sidebar message "Already syncing -- see progress" for ~3 s |
| Stop sync when idle | 404 `SYNC_NOT_RUNNING` → Stop button hidden when `state != running` (defensive only) |
| Pause when already paused / Resume when running | Idempotent -- 200 with current state |

### Session-fatal (force reload)

| Failure | Behavior |
|---|---|
| Any 302 to `/login` | `htmx:beforeOnLoad` detects 302 → `window.location = '/login?next=' + currentPath`. SSE `EventSource` close → Alpine.js same path. Unified ~20 LoC of JS. |
| 400 CSRF failure | Sidebar shows "Session expired" + "Reload" button; ~5 s auto-reload if no click |

### Graceful degradation

| Constraint | Behavior |
|---|---|
| JavaScript disabled | First-load page is fully server-rendered (all 6 cards present). Idle polling stops; active-mode shows static "JavaScript required for live progress" message |
| Idle poll and SSE disagree on `state` | `last_event_monotonic` is monotonic -- Alpine.js compares timestamps, only applies newer snapshot |

---

## Design Section 5: Testing

### Unit tests (pytest, ~56 new)

| Phase | New test file | Coverage |
|---|---|---|
| 2A | `test/test_routes_api_health.py` | statvfs success path, missing-destination structural case, dashcam reachable/timeout/refused, auth |
| 2A | `test/test_routes_api_recordings.py` | newest-N ordering, default limit (5) + query override, filename regex covers all type/direction codes |
| 2A | `test/test_routes_api_schedule.py` | pause persists, resume idempotent, CSRF + auth |
| 2A | `test/test_routes_hx_dashboard.py` | 4 fragments render valid HTML, auth coverage |
| 2A | `test/test_routes_api_sync_stop.py` (or extend existing) | 202 when running, 404 when idle, `_stop_event` set after call |
| 2A | `test/test_sync_stop_flag.py` | `request_stop`/`clear_stop` semantics; mocked chunk loop raises `UserWarning("stopped by user")` when flag set; `trigger_sync` clears on start |
| 2A | `test/test_scheduler_pause.py` | `_scheduled_run` skips when paused; resume → next tick runs; manual `trigger_sync` unaffected by pause |
| 2B | `test/test_dashboard_render.py` | template replaces placeholder; sidebar + 6 cards present; CSP-compliant inline script (or external); initial `data-state="idle"` |
| 2C | `test/test_dashboard_sse_handoff.py` | state transitions tracked via `body[data-state]`; SSE opens on running, closes on terminal |
| 2C | `test/test_sync_now_stop_pause_buttons.py` | sidebar actions wire to correct POST endpoints + CSRF tokens |

**Total: ~56 new unit tests → suite grows 395 → ~451.**

### Integration tests (Behave, ~6 new scenarios)

New feature file `features/dashboard.feature`:

- Scenario: idle dashboard renders all six status cards
- Scenario: sync now button kicks off a sync and the hero appears
- Scenario: stop button cleanly terminates a running sync; partial `.filename.mp4` dotfile survives
- Scenario: pause schedule survives a container restart (persistence)
- Scenario: pause schedule does not block manual sync now
- Scenario: dashcam unreachable surfaces as red-dot data, not as error chrome

All scenarios run in both `subprocess` and `docker` modes. No harness extension required.

### Coverage targets (matches foundation baseline)

| Surface | Target |
|---|---|
| `routes/api_health.py` | ≥90% |
| `routes/api_recordings.py` | ≥90% |
| `routes/api_schedule.py` | ≥90% |
| `routes/hx_dashboard.py` | ≥85% |
| `sync.py` stop-flag additions | ≥95% (no regression) |
| `scheduler.py` pause additions | ≥90% |
| Dashboard JS | not unit-tested -- BDD + manual verification |
| **Overall** | **≥85%** (no regression vs foundation baseline) |

### Manual verification (per-phase checklist)

**Phase 2A** (backend only):
- `curl /api/health/storage` → valid JSON
- `curl /api/health/dashcam` against unreachable address → reachable=false within 2 s
- `POST /api/schedule/pause` → settings.json on disk shows `paused: true`
- `POST /api/sync/stop` during a running sync → sync ends with `failed` + reason="stopped by user"

**Phase 2B** (UI + idle wiring):
- `docker run` → browser at `/` shows the dashboard, not the placeholder
- All 6 cards populate within 5 s
- Network tab shows independent fetches per card every 5 s
- Dark-mode OS toggle → page flips palette without reload

**Phase 2C** (active mode + controls):
- Sync now click → hero appears within 1 s, byte counter updates at 5 Hz
- Stop click → hero fades, returns to idle in <2 s
- Pause schedule → next cron tick log shows "scheduled sync skipped: schedule is paused"
- Disconnect dev server LAN → cards freeze at last value with `⚠` icon, no whole-page error

### What's NOT tested

- **Visual regression** -- no Percy/Chromatic. Visual companion archive at `.superpowers/brainstorm/` is the historical record.
- **Cross-browser** -- Chrome/Safari latest stable only. Firefox/Edge assumed-compatible.
- **Mobile devices** -- desktop-first; responsive collapse at 720 px tested in dev tools only.
- **Load testing** -- Waitress threaded model is fine for single-user self-hosted use.

---

## Design Section 6: Scope guards

### IN sub-project #2

- Dashboard at `/` with stateful idle/active modes
- 6 idle cards (Last sync, Next scheduled, Storage, Dashcam reachability, Recent activity, Dashcam info)
- 1 active-mode hero card (live progress)
- Sidebar with Sync now / Pause schedule / Stop sync
- 6 new backend endpoints: `/api/health/storage`, `/api/health/dashcam`, `/api/recordings/recent`, `/api/schedule/pause`, `/api/schedule/resume`, `/api/sync/stop`
- 5 new HTMX fragment endpoints (4 in 2A + 1 in 2B for dashcam info)
- 4 new Jinja2 partials
- `sync.py` cooperative stop flag
- `scheduler.py` pause skip hook
- `settings.schedule.paused: bool` field
- Dashboard CSS + JS (Alpine.js component)
- Responsive collapse at 720 px breakpoint
- ~56 new unit tests + 6 new Behave scenarios

### NOT in sub-project #2

| Feature | Goes in |
|---|---|
| Settings UI (forms, tier-aware affordances) | #3 |
| Manual light/dark toggle override | #3 |
| Log viewer (live tail UI, search, filters) | #4 |
| Statistics page + SQLite time-series store | #5 |
| Dashcam viewer (synchronized video, GPS map) | #6 |
| Multi-user accounts | out of scope (entire web-app series) |
| Browser-side visual regression CI | out of scope (entire series) |
| Mobile-first responsive design | out of scope (responsive but not mobile-optimized) |

### Carry-forwards from the foundation (resolved before 2C)

All four foundation carry-forwards were cleared in the pre-2C cleanup:

- `sync.py` cognitive-complexity decomposition of `download_file` and
  `download_recording` (S3776) -- done in #13 (paired with resume below).
- byte-range resume of interrupted downloads -- done in #13. the dotfile pattern
  now resumes via a runtime range probe (the resume `GET` itself) with a
  `200`/`416`/unconfirmed-`206` full-restart fallback; partials persist across
  runs and orphans are pruned at sync start. see
  `docs/plans/2026-06-01-download-resume-design.md`.
- Multi-stage Dockerfile to drop the uv binary from the final image -- done in
  #14 (image 169MB -> 88MB).
- `LoggingSettings.on_change` listener wiring -- done in #15 (live logging
  reload in `cmd_serve`, no longer deferred to sub-project #3).

### Open questions deferred to specific phases

| Question | Decided in |
|---|---|
| Exact card padding / typography weights | Phase 2B |
| SSE reconnect-backoff curve constants | Phase 2C |
| Stop-button confirmation UX (modal vs immediate) | Phase 2C |
| Whether to log card-fetch errors to the structured log handler | Phase 2C |

---

## Critical files to modify or create

### New Python source (Phase 2A)

- `blackvuesync/server/routes/api_health.py`
- `blackvuesync/server/routes/api_recordings.py`
- `blackvuesync/server/routes/api_schedule.py`
- `blackvuesync/server/routes/hx_dashboard.py`

### New templates (Phase 2A)

- `blackvuesync/server/templates/dashboard.html` (Phase 2B -- replaces `_placeholders/dashboard.html`)
- `blackvuesync/server/templates/_partials/storage_card.html`
- `blackvuesync/server/templates/_partials/dashcam_card.html`
- `blackvuesync/server/templates/_partials/next_scheduled_card.html`
- `blackvuesync/server/templates/_partials/recent_activity_card.html`

### New static assets (Phase 2B + 2C)

- `blackvuesync/server/static/css/dashboard.css`
- `blackvuesync/server/static/js/dashboard.js` (Phase 2C)

### New tests

- `test/test_routes_api_health.py`
- `test/test_routes_api_recordings.py`
- `test/test_routes_api_schedule.py`
- `test/test_routes_hx_dashboard.py`
- `test/test_sync_stop_flag.py`
- `test/test_scheduler_pause.py`
- `test/test_dashboard_render.py`
- `test/test_dashboard_sse_handoff.py`
- `test/test_sync_now_stop_pause_buttons.py`
- `features/dashboard.feature`

### To be modified

- `blackvuesync/sync.py` -- add `_stop_event`, `request_stop`, `clear_stop`; wire stop check into `download_with_resume`
- `blackvuesync/server/scheduler.py` -- skip when `settings.schedule.paused`
- `blackvuesync/server/routes/api_sync.py` -- add `POST /api/sync/stop`
- `blackvuesync/server/routes/ui.py` -- `dashboard()` renders `templates/dashboard.html`
- `blackvuesync/server/__init__.py` -- register `api_health_bp`, `api_recordings_bp`, `api_schedule_bp`, `hx_dashboard_bp` blueprints
- `blackvuesync/settings.py` -- `ScheduleSettings.paused: bool = False`
- `pyproject.toml` -- bump version to `2.4.0a0` (start of sub-project #2 alpha series)
- `docs/api.md` -- document the 6 new endpoints + 4 HTMX fragments

---

## Verification

### Per-phase verification

Each phase has its own PR with the same five required CI checks as the foundation (`pre-commit`, `unit-tests`, `integration-tests`, `test`, `SonarCloud Code Analysis`) and the same two-stage review (spec compliance → code quality).

### Foundation-style cadence

1. `git checkout -b sub-project-2-phase-a` off main
2. Write phase plan in `docs/plans/2026-05-19-sub-project-2-phase-a.md`
3. Commit plan
4. Dispatch implementer subagent with karpathy discipline embedded
5. Spec-compliance review subagent
6. Code-quality review subagent
7. Open PR via `gh pr create --repo tekgnosis-net/blackvuesync`
8. Watch CI; address findings
9. Squash-merge once green
10. Repeat for 2B, 2C

### End-to-end success criteria

After Phase 2C merges to main:

- `docker run` produces a running container; browser at `/` shows the dashboard (not a placeholder)
- Within 5 s of page load, all 6 cards populate with real data
- `Sync now` button click → hero appears with live byte counter, stats update at 5 Hz
- `Stop` button click → sync terminates cleanly, partial dotfile survives, idle grid returns
- `Pause schedule` → next cron tick logs "scheduled sync skipped"; `Resume schedule` → next tick fires normally
- Dark-mode OS toggle → palette flips without reload
- Network disconnect → cards freeze at last value with `⚠` icon; no whole-page error chrome
- All ~451 unit tests pass; 21+6 Behave scenarios pass; SonarCloud reports zero new findings

---

## Next steps after this spec

1. **User reviews this written spec** and approves or requests changes
2. After approval, invoke `superpowers:writing-plans` to produce the **first phase's** detailed implementation plan (`docs/plans/2026-05-19-sub-project-2-phase-a.md`)
3. Each phase gets its own writing-plans invocation before implementation, following the foundation pattern
4. Implementation follows the established branch-protection + two-stage review cycle

---

## Spec self-review

Self-review pass: no TBDs or placeholders remain. The "open questions deferred to specific phases" table makes punted decisions explicit and assigns them. Each section is internally consistent: the 6 idle cards in Section 1 match the 5 new HTMX fragments + 1 reused (`last_run_card.html`) in Section 2; the active-mode hero in Section 1 reuses `sync_status_card.html` from Phase D as called out in Section 2. Coverage targets in Section 5 line up with the file inventory in Section 2. Scope guards in Section 6 do not contradict any in/out claim made elsewhere.

One known limitation: the dashboard JS is not unit-tested. Coverage relies on BDD + manual verification, same as the foundation's Phase G placeholder pages. Acceptable given the JS surface is ~60-80 LoC of state-machine plus event handlers; integration tests catch the user-visible behavior end-to-end.

---

## Amendment 2026-05-20: Dashcam info card

After Phase 2A merged, a research investigation of the BlackVue DR900 HTTP
API revealed that the dashcam exposes `GET /Config/version.bin` (firmware
identification) and `GET /Config/config.ini` (a parseable settings blob)
with no authentication. Read-only inspection is trivially safe and adds
real operational value (operators can see audio on/off, parking thresholds,
time zone, etc.) without any write risk.

Phase 2B's scope is extended to include a 6th idle card, **Dashcam info**,
plus its backing endpoint and HTMX fragment:

- `GET /api/dashcam/info` -- fetches `/Config/version.bin` + `/Config/config.ini`
  from the dashcam, parses, returns structured JSON
- `GET /hx/dashcam-info-card` -- renders the card fragment
- `templates/_partials/dashcam_info_card.html` -- Jinja2 partial

The new card sits alongside the other 5 in the dashboard layout (Phase 2B
finalises the 6-card arrangement -- options include a 3x2 grid or a 2x3
grid; the existing layout C spec stays valid in either form).

**Tier 2+ writes (changing dashcam settings via `POST /upload.cgi`) are
deferred to a future sub-project #7.** Research findings: `POST /upload.cgi`
is dual-purpose (settings AND firmware uploads) with no content-type
gating, the config schema is undocumented and version-fragile across
DR900X / DR900S firmware lines, and no open-source library implements
writes despite many reading. The risk profile justifies a separate scoping
conversation, after sub-project #2 ships, gated on a controlled experiment
with the user's actual hardware.

Key research citations: `Digital-Nebula/hackvue`,
`DavidMetcalfe/BlackVue-DR900S-config`, `DoctorMcKay/node-blackvue`,
manuals at `manual.blackvue.com`.

---

**End of design spec.**
