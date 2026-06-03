# Sub-Project #3 -- Settings UI -- Design Spec

**Date:** 2026-06-03
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design -- approved by maintainer; awaiting spec review before writing-plans
**Series:** third web-app sub-project (follows #1 Web Foundation, #2 Dashboard).

---

## Context

The app's configuration lives in nine frozen-dataclass sections in
`blackvuesync/settings.py`, persisted to `settings.json` by `SettingsStore`.
Today the only way to change them is to edit the file (or seed via env vars on
first run). The `/settings` route is a placeholder. This sub-project makes the
settings UI-editable.

**Key scoping fact: the backend already exists** (built in the foundation):

| Capability | Endpoint | Location |
| --- | --- | --- |
| Read all settings (secrets redacted to `"***"`) | `GET /api/settings` | `routes/api_settings.py:57` |
| Update one section (validate → persist → notify) | `PATCH /api/settings/<section>` | `routes/api_settings.py:102` |
| Change admin password | `POST /api/auth/password` | `routes/api_auth.py:40` |
| Rotate session secret (invalidate sessions) | `DELETE /api/auth/sessions` | `routes/api_auth.py:105` |
| Current user info | `GET /api/auth/me` | `routes/api_auth.py` |

**#3 is therefore a frontend-wiring phase.** No new endpoints. **Out of scope:**
dashcam *device* config writes -- that is sub-project #7, gated on a controlled
DR900 hardware experiment. This page edits the app's `settings.json` only.

### Backend contract (confirmed, do not modify)

- `GET /api/settings` → full settings as nested JSON; `auth.password_hash` and
  `auth.session_secret` come back as the literal `"***"`.
- `PATCH /api/settings/<section>`:
  - success → `200 {section, tier, applied: true}` where `tier ∈
    {immediate, next_tick, restart}` (the section's `TIER`).
  - validation failure → `422 {code: "SETTINGS_INVALID", details:
    {field_errors: [{path, message}]}}`. `path` is the **section name**;
    `message` is a human-readable string that already names the offending field
    (e.g. `"schedule.cron_expression is not a valid 5-field cron expression"`).
  - unknown section → `404 {code: "SECTION_NOT_FOUND"}`.
  - the handler strips any redacted `"***"` values from the payload (so sending
    `"***"` for a secret means "leave unchanged") and coerces JSON arrays to the
    tuples the frozen dataclasses require.

### The nine sections and their tiers

| Section | TIER | Notable fields |
| --- | --- | --- |
| connection | restart | address, timeout_seconds |
| schedule | next_tick | cron_expression, timezone, paused |
| sync | next_tick | priority, grouping, include, exclude, retry_failed_after, skip_metadata |
| retention | next_tick | keep, max_used_disk_percent |
| logging | immediate | verbose, quiet, format |
| metrics | immediate | file, pushgateway_url, state_file, … |
| web | restart | port, session_lifetime_hours |
| auth | immediate | mode, username, password_hash*, session_secret*, trusted_proxies, proxy_user_header |
| system | restart | destination, dry_run |

`*` redacted secrets -- never rendered or PATCHed as text (see §4).

---

## Design

### 1. Layout & navigation -- sidebar sections + pane

A `/settings` page with a left list of the nine sections and a content pane
showing one section's form at a time (macOS System Settings style; consistent
with the dashboard's existing left sidebar). All nine section forms are rendered
server-side on load; an Alpine component (the `@alpinejs/csp` build -- see the
established CSP discipline) toggles which pane is visible by setting an
`activeSection` property. No round-trip on section switch.

**Graceful degradation:** without JS the page is a single scrollable column with
all nine sections stacked (the panes are visible by default; Alpine only *hides*
the inactive ones once it boots). Every form posts via standard means, so the
page remains usable without JS.

### 2. Per-section save + tier affordances

Each section pane has its own **Save** button that `PATCH`es just that section
(HTMX, with the existing `X-CSRFToken` header). Saving one section never touches
another. The `200` response's `tier` drives the feedback:

| tier | sections | feedback |
| --- | --- | --- |
| `immediate` | logging, metrics, auth | green "Saved" toast -- change is live now |
| `next_tick` | schedule, sync, retention | amber "Saved -- applies at the next sync" |
| `restart` | connection, web, system | red "Saved -- restart the container to take effect" banner |

The `restart` affordance is honest about the limitation: the app cannot restart
itself (it's a long-running container), so the banner tells the operator to
restart. (Same posture as the foundation's documented `TIER restart`.)

### 3. Field rendering by type

Field widgets are derived from the dataclass field types:

| Type | Widget |
| --- | --- |
| `Literal[...]` enum (priority, grouping, log format, auth mode, timezone-ish) | `<select>` of the literal options |
| `bool` (dry_run, quiet, verbose-as-flag) | toggle / checkbox |
| `int` (timeout_seconds, keep, max_used_disk_percent, port, session_lifetime_hours) | number input |
| `str` (address, cron_expression, destination, pushgateway_url, …) | text input |
| tuple of letters (`skip_metadata` ∈ {t,3,g}) | a checkbox per letter |
| tuple of strings (`include`, `exclude`, `trusted_proxies`) | textarea, one value per line (joined/split on save) |

**Validation errors:** on a `422`, render the `field_errors[]` messages as a
**section-level error list** at the top of that section's pane (the messages
already name the field). No per-field inline binding -- the API reports
section-scoped messages, so the UI matches that contract honestly.

### 4. Auth section -- special handling for secrets and access

Secrets are redacted by the API and must never be shown or sent as text:

- `mode` (login / none / proxy) → `<select>`, but changing it is guarded by a
  confirm step ("Changing the auth mode can affect your own access. Continue?")
  because switching to `none`/`proxy` or back changes who can reach the app.
- **Password** → not a field. A "Change password" control (inline form or small
  dialog) collecting current + new + confirm, POSTing to `POST /api/auth/password`.
  The stored value renders as "Password: set" (never the hash).
- **Session secret** → not a field. A "Rotate sessions" button calling
  `DELETE /api/auth/sessions`; shows the "restart required to fully take effect"
  note (the running process keeps the old secret until restart -- documented
  behavior). Renders as "Session secret: set".
- `username`, `trusted_proxies`, `proxy_user_header` → normal fields (username
  text; trusted_proxies textarea-per-line).

When the auth section is PATCHed (e.g. to change `mode` or `trusted_proxies`),
the form simply omits the secret fields; any `"***"` that slips through is
stripped server-side.

### 5. Tech stack

- **HTMX** for the per-section `PATCH` round-trips (reuses the `app.js`
  `htmx:configRequest` → `X-CSRFToken` wiring).
- **Alpine.js (`@alpinejs/csp` build)** for section navigation, toggles, the
  confirm step, and the change-password dialog. All directives are bare
  property/method references -- no inline expressions (the eval-free CSP build;
  `script-src` has no `unsafe-eval`). Logic lives in a `settings.js` component.
- New `settings.js` + `settings.css`; a real `settings.html` replacing the
  placeholder; `ui.py`'s `settings()` route renders it with the current settings
  (from `SettingsStore`, secrets pre-redacted). `base.html` already provides the
  `extra_js` block and `data-state` body from 2C.

### 6. Components / files (responsibilities)

- `templates/settings.html` -- page shell: sidebar section list + the nine panes.
- `templates/_partials/settings_section.html` -- one section's form (looped, or a
  small per-section include set); renders fields from a server-built field
  descriptor list so the template stays declarative.
- `static/js/settings.js` -- Alpine CSP component: `activeSection`, save handlers
  (or rely on HTMX for the POST and Alpine only for nav + dialogs), tier-toast
  rendering, confirm + password dialog.
- `static/css/settings.css` -- sidebar+pane layout, field rows, toast/banner,
  reusing the design tokens.
- `routes/ui.py` `settings()` -- render `settings.html` with the redacted current
  settings + a field-descriptor structure per section.

---

## Testing

- **Structure tests** (`test/test_settings_page.py`, pytest + Flask client): the
  page renders all nine sections; each section's fields render with the right
  widget for its type; CSRF token present; secrets render as "set"/"not set",
  never the hash/`"***"`; the restart-tier sections carry the restart affordance
  markup; `settings.js` is loaded.
- **Existing API coverage** (no change): `test/test_routes_api_settings.py`
  already covers GET/PATCH/validation/redaction; `test/test_routes_api_auth.py`
  covers password change + session rotation.
- **Playwright smoke** (`test/e2e/`, the `-m e2e` job): switch sections; edit a
  field in an `immediate` section, Save, see the green toast; trigger a `422`
  (invalid value) and see the section error list; open the change-password
  dialog. Validates the CSP-build directives end-to-end (eval-free).

---

## Scope guards

**In #3:** the nine-section sidebar+pane Settings page; per-section save with
tier affordances; type-driven field widgets; section-level validation display;
auth password-change + session-rotate controls; all nine sections editable.

**Not in #3:** any new backend endpoint; dashcam device-config writes (#7);
settings import/export UI (manual file copy remains); multi-user accounts;
schedule cron "preset chips" UX (a plain cron field is fine for now -- note it as
a possible #3-follow-up, not built here).

---

## Self-review

- **Placeholders:** none.
- **Consistency:** the tier table (§2) matches the section/tier table in Context;
  the validation display (§3) matches the confirmed `422` contract (section-level
  messages); auth secret handling (§4) matches the redaction + dedicated
  endpoints; tech (§5) matches the established `@alpinejs/csp` discipline.
- **Scope:** frontend only + `ui.py` render context; no endpoints; `settings.json`
  schema unchanged. All nine sections editable per maintainer decision.
- **Ambiguity:** save is per-section (one PATCH per Save); navigation is
  client-side Alpine over server-rendered panes; secrets are never rendered;
  restart-tier is informational (the container can't self-restart).
- **Risk:** the auth section can affect access -- mitigated by the mode-change
  confirm and the existing current-password requirement on password change. JS
  behavior (nav, dialogs, CSP-build correctness) is the pytest-blind layer,
  covered by the Playwright smoke.
