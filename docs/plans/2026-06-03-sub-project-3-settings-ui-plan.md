# Sub-Project #3 -- Settings UI -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A `/settings` page (sidebar sections + pane) that edits all nine settings sections via the existing `PATCH /api/settings/<section>`, with per-section save, tier-aware feedback, type-correct JSON payloads, and dedicated auth password/session controls.

**Architecture:** A declarative field-descriptor table (`settings_form.py`) drives server-rendered forms. Alpine (`@alpinejs/csp` build) switches the visible section and owns saving: it reads each input's `data-field`/`data-type`, builds a **typed** JSON body, `fetch`-PATCHes, and renders the tier toast or the 422 error list. Secrets are never rendered; auth password/session use their dedicated endpoints. No backend changes.

**Tech Stack:** Jinja2 + Alpine `@alpinejs/csp` (vendored) + `fetch` (CSRF via `<meta>`), Flask test client (structure tests), pytest-playwright (browser smoke).

**Design:** `docs/plans/2026-06-03-sub-project-3-settings-ui-design.md`.

**Backend contract (exists; do not modify):**

- `GET /api/settings` → `{version, <section>: {<fields>, _tier}}`; `auth.password_hash`/`session_secret` are `"***"`.
- `PATCH /api/settings/<section>` (JSON body) → `200 {section, tier, applied:true}` | `422 {code:"SETTINGS_INVALID", details:{field_errors:[{path,message}]}}` (path=section) | `404 SECTION_NOT_FOUND`. Strips `"***"` values; coerces JSON arrays → tuples. **No numeric/bool coercion -- client must send correct JSON types.**
- `POST /api/auth/password` (JSON `{current_password,new_password}`) → `200 {applied:true}` | `401 INVALID_CURRENT_PASSWORD` | `422 WEAK_PASSWORD` | `429 RATE_LIMITED`.
- `DELETE /api/auth/sessions` → `200 {rotated:true, restart_required:true}`.
- CSRF: Flask-WTF validates `X-CSRFToken` on all mutating requests; read it from `<meta name="csrf-token">`.
- Reusable from `blackvuesync.settings`: `_SECTION_FIELDS` (section→class, ordered), `_TUPLE_FIELDS` (section→set of tuple field names), `_REDACTED_FIELDS` (`{"auth":{"password_hash","session_secret"}}`). Each section class has `.TIER`.

**CSP-build discipline (mandatory):** directives reference bare property/method names only -- no inline expressions, `@click="fn"` not `fn()`. Logic lives in the `settings.js` component. See `docs/plans` Phase 2C and the `alpine-csp` convention.

---

## File structure

**Create:**

- `blackvuesync/server/settings_form.py` -- `FieldSpec` + `SECTION_FIELD_SPECS` (per-section ordered field metadata) + `build_sections(settings_dict)` → ordered render-models.
- `blackvuesync/server/templates/settings.html` -- page shell (sidebar + panes); replaces the placeholder render.
- `blackvuesync/server/templates/_partials/settings_field.html` -- one field, by widget.
- `blackvuesync/server/static/js/settings.js` -- Alpine CSP component (nav, typed save, toast, mode-confirm, password dialog, rotate).
- `blackvuesync/server/static/css/settings.css` -- sidebar+pane, field rows, toast/banner, dialog.
- `test/test_settings_page.py` -- structure tests.
- `test/e2e/test_settings_active.py` -- Playwright smoke.

**Modify:**

- `blackvuesync/server/routes/ui.py` -- `settings()` renders the real page.
- `pyproject.toml` -- version bump; `test_settings_page` (+ e2e) mypy overrides.
- `docs/api.md` -- note the Settings UI drives the existing endpoints.

---

### Task 1: `settings_form.py` -- field descriptors + context builder

**Files:**

- Create: `blackvuesync/server/settings_form.py`
- Test: `test/test_settings_form.py`

- [ ] **Step 1: Write the failing test**

Create `test/test_settings_form.py`:

```python
"""tests for the settings-form field descriptors and context builder."""

from __future__ import annotations

from blackvuesync.server.settings_form import (
    SECTION_FIELD_SPECS,
    build_sections,
)
from blackvuesync.settings import _REDACTED_FIELDS, _SECTION_FIELDS


def test_every_section_has_specs() -> None:
    # one spec list per settings section, same names as the backend
    assert set(SECTION_FIELD_SPECS) == set(_SECTION_FIELDS)


def test_redacted_secrets_are_not_rendered_as_fields() -> None:
    # password_hash / session_secret must not appear as editable fields
    auth_field_names = {f.name for f in SECTION_FIELD_SPECS["auth"]}
    assert auth_field_names.isdisjoint(_REDACTED_FIELDS["auth"])


def test_build_sections_pairs_values_and_tier() -> None:
    settings_dict = {
        "connection": {"address": "192.168.0.1", "timeout_seconds": 10.0, "_tier": "restart"},
        "schedule": {"cron_expression": "*/15 * * * *", "timezone": "UTC", "paused": False, "_tier": "next_tick"},
        "sync": {"priority": "date", "grouping": "none", "include": [], "exclude": [],
                 "retry_failed_after": "1d", "skip_metadata": [], "affinity_key": None, "_tier": "next_tick"},
        "retention": {"keep": "2w", "max_used_disk_percent": 90, "_tier": "next_tick"},
        "logging": {"verbose": 0, "quiet": False, "format": "text", "file_max_bytes": 1024,
                    "file_backup_count": 5, "ring_buffer_capacity": 1000, "_tier": "immediate"},
        "metrics": {"file": None, "pushgateway_url": None, "job": "blackvuesync",
                    "instance": None, "state_file": "/config/metrics-state.json", "_tier": "immediate"},
        "web": {"port": 8080, "session_lifetime_hours": 24, "_tier": "restart"},
        "auth": {"mode": "login", "username": "admin", "password_hash": "***",
                 "session_secret": "***", "trusted_proxies": [], "proxy_user_header": "X-Remote-User", "_tier": "immediate"},
        "system": {"destination": "/recordings", "dry_run": False, "_tier": "restart"},
    }
    sections = build_sections(settings_dict)
    by_name = {s["name"]: s for s in sections}
    assert by_name["connection"]["tier"] == "restart"
    addr = next(f for f in by_name["connection"]["fields"] if f["name"] == "address")
    assert addr["value"] == "192.168.0.1"
    assert addr["widget"] == "text"
    # auth section flagged so the template renders the password/rotate controls
    assert by_name["auth"]["is_auth"] is True


def test_lines_widget_value_is_joined() -> None:
    settings_dict = {name: {"_tier": "immediate"} for name in _SECTION_FIELDS}
    settings_dict["sync"].update({"include": ["a*", "b*"], "exclude": [], "priority": "date",
                                  "grouping": "none", "retry_failed_after": "1d",
                                  "skip_metadata": [], "affinity_key": None})
    sections = {s["name"]: s for s in build_sections(settings_dict)}
    inc = next(f for f in sections["sync"]["fields"] if f["name"] == "include")
    assert inc["widget"] == "lines"
    assert inc["value"] == "a*\nb*"  # tuple-of-strings joined for the textarea
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_settings_form.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `settings_form.py`**

```python
"""declarative field descriptors for the settings ui.

maps each settings section to an ordered list of editable fields and the widget
to render. secret fields (auth.password_hash / session_secret) are intentionally
absent -- the auth section renders dedicated password/rotate controls instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from blackvuesync.settings import _SECTION_FIELDS, _TUPLE_FIELDS


@dataclass(frozen=True)
class FieldSpec:
    """describes how one settings field is rendered and typed in the form.

    widget is one of: text, number, toggle, select, checkboxes, lines.
    options applies to select/checkboxes. data_type drives client-side json
    coercion: text, number, bool, lines (newline-split -> list), letters
    (checkbox group -> list).
    """

    name: str
    label: str
    widget: str
    data_type: str
    options: tuple[str, ...] = ()
    help: str = ""


# ordered, per section. excludes redacted secrets (handled by auth controls).
SECTION_FIELD_SPECS: dict[str, tuple[FieldSpec, ...]] = {
    "connection": (
        FieldSpec("address", "Dashcam address", "text", "text", help="IP or hostname on the LAN"),
        FieldSpec("timeout_seconds", "Timeout (seconds)", "number", "number"),
    ),
    "schedule": (
        FieldSpec("cron_expression", "Schedule (cron)", "text", "text", help="5-field cron, e.g. */15 * * * *"),
        FieldSpec("timezone", "Timezone", "text", "text"),
        FieldSpec("paused", "Pause scheduled syncs", "toggle", "bool"),
    ),
    "sync": (
        FieldSpec("priority", "Download priority", "select", "text", options=("date", "rdate", "type")),
        FieldSpec("grouping", "Grouping", "select", "text", options=("none", "daily", "weekly", "monthly", "yearly")),
        FieldSpec("include", "Include patterns", "lines", "lines", help="one glob per line"),
        FieldSpec("exclude", "Exclude patterns", "lines", "lines", help="one glob per line"),
        FieldSpec("retry_failed_after", "Retry failed after", "text", "text", help="duration, e.g. 1d"),
        FieldSpec("skip_metadata", "Skip metadata", "checkboxes", "letters", options=("t", "3", "g")),
        FieldSpec("affinity_key", "Affinity key", "text", "text", help="reserved for test isolation"),
    ),
    "retention": (
        FieldSpec("keep", "Keep recordings for", "text", "text", help="duration, e.g. 2w"),
        FieldSpec("max_used_disk_percent", "Max used disk (%)", "number", "number"),
    ),
    "logging": (
        FieldSpec("verbose", "Verbosity", "number", "number"),
        FieldSpec("quiet", "Quiet (errors only)", "toggle", "bool"),
        FieldSpec("format", "Log format", "select", "text", options=("text", "json")),
        FieldSpec("file_max_bytes", "Log file max bytes", "number", "number"),
        FieldSpec("file_backup_count", "Log file backups", "number", "number"),
        FieldSpec("ring_buffer_capacity", "Ring buffer capacity", "number", "number"),
    ),
    "metrics": (
        FieldSpec("file", "Metrics file", "text", "text"),
        FieldSpec("pushgateway_url", "Pushgateway URL", "text", "text"),
        FieldSpec("job", "Job name", "text", "text"),
        FieldSpec("instance", "Instance", "text", "text"),
        FieldSpec("state_file", "State file", "text", "text"),
    ),
    "web": (
        FieldSpec("port", "Port", "number", "number"),
        FieldSpec("session_lifetime_hours", "Session lifetime (hours)", "number", "number"),
    ),
    "auth": (
        FieldSpec("mode", "Auth mode", "select", "text", options=("login", "none", "proxy")),
        FieldSpec("username", "Username", "text", "text"),
        FieldSpec("trusted_proxies", "Trusted proxies", "lines", "lines", help="one IP/CIDR per line"),
        FieldSpec("proxy_user_header", "Proxy user header", "text", "text"),
    ),
    "system": (
        FieldSpec("destination", "Destination", "text", "text", help="recordings directory"),
        FieldSpec("dry_run", "Dry run", "toggle", "bool"),
    ),
}

# human labels for the section nav.
SECTION_LABELS: dict[str, str] = {
    "connection": "Connection",
    "schedule": "Schedule",
    "sync": "Sync",
    "retention": "Retention",
    "logging": "Logging",
    "metrics": "Metrics",
    "web": "Web",
    "auth": "Auth",
    "system": "System",
}


def _field_value(section_name: str, spec: FieldSpec, raw: Any) -> Any:
    """shapes a raw settings value for its widget.

    tuple-of-strings (lines) join with newlines for the textarea; None becomes
    an empty string for text widgets; everything else passes through.
    """
    if spec.widget == "lines":
        return "\n".join(raw or ())
    if raw is None and spec.widget in ("text", "number"):
        return ""
    return raw


def build_sections(settings_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """builds the ordered render model for the settings page.

    pairs each section's field specs with the current (redacted) values from a
    GET /api/settings-shaped dict; carries the per-section tier and an is_auth
    flag (the template renders password/rotate controls for auth).
    """
    sections: list[dict[str, Any]] = []
    for name in _SECTION_FIELDS:  # preserves section order
        section_values = settings_dict.get(name, {})
        tier = section_values.get("_tier", "")
        fields = [
            {
                "name": spec.name,
                "label": spec.label,
                "widget": spec.widget,
                "data_type": spec.data_type,
                "options": spec.options,
                "help": spec.help,
                "value": _field_value(name, spec, section_values.get(spec.name)),
            }
            for spec in SECTION_FIELD_SPECS[name]
        ]
        sections.append(
            {
                "name": name,
                "label": SECTION_LABELS[name],
                "tier": tier,
                "fields": fields,
                "is_auth": name == "auth",
            }
        )
    return sections


__all__ = ["FieldSpec", "SECTION_FIELD_SPECS", "SECTION_LABELS", "build_sections"]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `venv/bin/pytest test/test_settings_form.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/settings_form.py test/test_settings_form.py
git commit -m "feat: settings-form field descriptors and context builder"
```

---

### Task 2: `ui.py` settings route + `settings.html` shell + sidebar

**Files:**

- Modify: `blackvuesync/server/routes/ui.py` (`settings()`)
- Create: `blackvuesync/server/templates/settings.html`
- Create: `blackvuesync/server/static/css/settings.css`
- Test: `test/test_settings_page.py`

- [ ] **Step 1: Write the failing structure test**

Create `test/test_settings_page.py` (reuses the `logged_in` fixture pattern from `test/test_dashboard_sse_handoff.py` -- copy it verbatim, it builds an app + logs in):

```python
"""structure tests for the settings page (server-rendered)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def logged_in(tmp_path: Path):  # type: ignore[no-untyped-def]
    destination = tmp_path / "recordings"
    destination.mkdir()
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth, username="admin", password_hash=hash_password("test-password-1234")
            ),
            system=dataclasses.replace(s.system, destination=str(destination)),
        )
    )
    app = create_app(store, testing=True)
    with app.test_client() as client:
        client.post("/login", data={"username": "admin", "password": "test-password-1234"},
                    follow_redirects=True)
        yield client, store


class TestSettingsPage:
    def test_renders_not_placeholder(self, logged_in: Any) -> None:
        client, _ = logged_in
        body = client.get("/settings").data
        assert b"coming in sub-project" not in body

    def test_all_nine_sections_present(self, logged_in: Any) -> None:
        client, _ = logged_in
        body = client.get("/settings").data.decode()
        for name in ("connection", "schedule", "sync", "retention", "logging",
                     "metrics", "web", "auth", "system"):
            assert f'data-section="{name}"' in body

    def test_settings_js_and_css_loaded(self, logged_in: Any) -> None:
        client, _ = logged_in
        body = client.get("/settings").data
        assert b"js/settings.js" in body
        assert b"css/settings.css" in body
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_settings_page.py -q`
Expected: FAIL (still the placeholder).

- [ ] **Step 3: Rewrite the `settings()` route**

In `blackvuesync/server/routes/ui.py`, replace the `settings()` body with (imports at top of file: `from blackvuesync.server.settings_form import build_sections`; `from blackvuesync.server.routes.api_settings import _settings_to_dict`):

```python
@bp.route("/settings", methods=["GET"])
@login_required
def settings() -> str:
    """renders the settings page (sidebar sections + per-section forms)."""
    store = current_app.settings_store  # type: ignore[attr-defined]
    settings_dict = _settings_to_dict(store.get())  # redacted, per-section _tier
    return render_template(
        "settings.html",
        version=__version__,
        page="settings",
        sections=build_sections(settings_dict),
    )
```

- [ ] **Step 4: Create `settings.html`**

```html
{% extends "base.html" %}
{% block title %}Settings -- BlackVue Sync{% endblock %}
{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/settings.css') }}">
{% endblock %}
{% block footer_version %}{{ version }}{% endblock %}
{% block extra_js %}
  <script src="{{ url_for('static', filename='js/settings.js') }}" defer></script>
{% endblock %}

{% block content %}
<div class="settings" x-data="settingsPage" data-initial="{{ sections[0].name }}">
  <nav class="settings-nav">
    {% for s in sections %}
      <button type="button" class="settings-nav-item" data-section-nav="{{ s.name }}"
              @click="select">{{ s.label }}</button>
    {% endfor %}
  </nav>

  <div class="settings-panes">
    {% for s in sections %}
      <section class="settings-pane" data-pane="{{ s.name }}">
        <header class="settings-pane-header">
          <h2 class="settings-pane-title">{{ s.label }}</h2>
          <span class="settings-tier settings-tier-{{ s.tier }}">{{ s.tier }}</span>
        </header>

        <div class="settings-errors" data-errors="{{ s.name }}" hidden></div>

        <form data-form="{{ s.name }}">
          {% for f in s.fields %}
            {% include "_partials/settings_field.html" %}
          {% endfor %}
          {% if s.is_auth %}
            {% include "_partials/auth_controls.html" %}
          {% endif %}
          <div class="settings-actions">
            <button type="button" class="button button-primary"
                    data-save="{{ s.name }}" @click="save">Save</button>
            <span class="settings-toast" data-toast="{{ s.name }}" hidden></span>
          </div>
        </form>
      </section>
    {% endfor %}
  </div>

  {% include "_partials/password_dialog.html" %}
</div>
{% endblock %}
```

Visibility is CSS-class-driven and toggled imperatively in `settings.js` (no
per-element Alpine getters -- the only directives are `x-data` on the root and
bare `@click="method"` refs, which the CSP build allows). Error/toast `<span>`s
start `hidden`; `settings.js` fills their text and unhides them. The
`auth_controls.html` and `password_dialog.html` partials come in Task 4.

- [ ] **Step 5: Create `settings.css`** (sidebar+pane mirroring dashboard.css idioms)

```css
/* settings: sidebar section list + content pane. tokens drive light/dark. */
.settings {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: var(--space-6);
  max-width: 1100px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-4);
}
.settings-nav { display: flex; flex-direction: column; gap: var(--space-1); }
.settings-nav-item {
  text-align: left;
  padding: var(--space-2) var(--space-3);
  border: none;
  background: transparent;
  border-radius: var(--radius-md);
  font-size: var(--text-subheadline);
  color: var(--color-label-secondary);
  cursor: pointer;
}
.settings-nav-item.active { background: var(--color-fill); color: var(--color-label); font-weight: 600; }
/* graceful degradation: without JS, all panes show (one scroll). settings.js
   adds .js-nav to the root, switching to show-only-the-active-pane. */
.settings.js-nav .settings-pane { display: none; }
.settings.js-nav .settings-pane.is-active { display: block; }
.settings-pane { background: var(--color-surface); border-radius: var(--radius-lg);
  box-shadow: var(--shadow-subtle); padding: var(--space-6); max-width: 640px; }
.settings-pane-header { display: flex; align-items: center; justify-content: space-between;
  margin-bottom: var(--space-4); }
.settings-pane-title { font-size: var(--text-title3); font-weight: 700; }
.settings-tier { font-size: var(--text-caption1); font-weight: 600; padding: 2px var(--space-2);
  border-radius: var(--radius-sm); }
.settings-tier-immediate { background: color-mix(in srgb, var(--color-success) 18%, transparent); color: var(--color-success); }
.settings-tier-next_tick { background: color-mix(in srgb, var(--color-warning) 18%, transparent); color: var(--color-warning); }
.settings-tier-restart { background: color-mix(in srgb, var(--color-error) 16%, transparent); color: var(--color-error); }
.settings-field { margin-bottom: var(--space-4); }
.settings-field label { display: block; font-size: var(--text-footnote); font-weight: 600;
  color: var(--color-label-secondary); margin-bottom: var(--space-1); }
.settings-field .field-help { font-size: var(--text-caption1); color: var(--color-label-tertiary); margin-top: var(--space-1); }
.settings-errors { background: color-mix(in srgb, var(--color-error) 10%, transparent);
  color: var(--color-error); border-radius: var(--radius-md); padding: var(--space-3);
  margin-bottom: var(--space-4); font-size: var(--text-footnote); }
.settings-actions { display: flex; align-items: center; gap: var(--space-3); margin-top: var(--space-4); }
.settings-toast { font-size: var(--text-footnote); }
.settings-toast.ok { color: var(--color-success); }
@media (max-width: 720px) { .settings { grid-template-columns: 1fr; } }
```

- [ ] **Step 6: Run the structure tests**

Run: `venv/bin/pytest test/test_settings_page.py -q`
Expected: the section/css/js-loaded tests PASS. (Field-widget tests come in Task 3; `auth_controls.html`/`password_dialog.html` are created in Task 5 -- create empty placeholder files now so the includes resolve, fill them in Task 5; or do Task 5 before re-running.)

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/settings.html blackvuesync/server/static/css/settings.css test/test_settings_page.py
git commit -m "feat: settings page shell with sidebar sections and panes"
```

---

### Task 3: `settings_field.html` -- widget rendering by type

**Files:**

- Create: `blackvuesync/server/templates/_partials/settings_field.html`
- Test: `test/test_settings_page.py` (extend)

- [ ] **Step 1: Write the failing widget tests**

Append to `test/test_settings_page.py`:

```python
class TestFieldWidgets:
    def test_select_renders_options(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        # sync.priority is a select with the three literal options
        assert 'data-field="priority"' in body
        assert "<select" in body and ">date<" in body and ">rdate<" in body

    def test_number_field_has_data_type(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert 'data-field="timeout_seconds"' in body
        assert 'data-type="number"' in body

    def test_toggle_field_is_checkbox(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert 'data-field="dry_run"' in body
        assert 'data-type="bool"' in body

    def test_lines_field_is_textarea(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert 'data-field="include"' in body
        assert 'data-type="lines"' in body
        assert "<textarea" in body
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_settings_page.py::TestFieldWidgets -q`
Expected: FAIL (field partial not created).

- [ ] **Step 3: Create `settings_field.html`**

The loop variable from `settings.html` is `f` (a field dict). Each input carries `data-field` (name) and `data-type` (for client coercion):

```html
<div class="settings-field">
  <label for="f-{{ s.name }}-{{ f.name }}">{{ f.label }}</label>

  {% if f.widget == "select" %}
    <select id="f-{{ s.name }}-{{ f.name }}" class="form-input"
            data-field="{{ f.name }}" data-type="{{ f.data_type }}">
      {% for opt in f.options %}
        <option value="{{ opt }}" {% if f.value == opt %}selected{% endif %}>{{ opt }}</option>
      {% endfor %}
    </select>

  {% elif f.widget == "toggle" %}
    <input type="checkbox" id="f-{{ s.name }}-{{ f.name }}"
           data-field="{{ f.name }}" data-type="{{ f.data_type }}"
           {% if f.value %}checked{% endif %}>

  {% elif f.widget == "checkboxes" %}
    <div class="field-checkboxes" data-field="{{ f.name }}" data-type="{{ f.data_type }}">
      {% for opt in f.options %}
        <label class="field-checkbox">
          <input type="checkbox" value="{{ opt }}"
                 {% if opt in f.value %}checked{% endif %}> {{ opt }}
        </label>
      {% endfor %}
    </div>

  {% elif f.widget == "lines" %}
    <textarea id="f-{{ s.name }}-{{ f.name }}" class="form-input" rows="3"
              data-field="{{ f.name }}" data-type="{{ f.data_type }}">{{ f.value }}</textarea>

  {% elif f.widget == "number" %}
    <input type="number" step="any" id="f-{{ s.name }}-{{ f.name }}" class="form-input"
           data-field="{{ f.name }}" data-type="{{ f.data_type }}" value="{{ f.value }}">

  {% else %}
    <input type="text" id="f-{{ s.name }}-{{ f.name }}" class="form-input"
           data-field="{{ f.name }}" data-type="{{ f.data_type }}" value="{{ f.value }}">
  {% endif %}

  {% if f.help %}<p class="field-help">{{ f.help }}</p>{% endif %}
</div>
```

(`s` is the enclosing section from `settings.html`'s loop; Jinja includes inherit the caller's context.)

- [ ] **Step 4: Run the widget tests**

Run: `venv/bin/pytest test/test_settings_page.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/templates/_partials/settings_field.html test/test_settings_page.py
git commit -m "feat: settings field widgets rendered by type"
```

---

### Task 4: Auth controls + password dialog partials

**Files:**

- Create: `blackvuesync/server/templates/_partials/auth_controls.html`
- Create: `blackvuesync/server/templates/_partials/password_dialog.html`
- Test: `test/test_settings_page.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_settings_page.py`:

```python
class TestAuthControls:
    def test_secrets_shown_as_set_not_hash(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert "Password: set" in body            # password_hash was set in the fixture
        assert "***" not in body                  # never leak the redaction sentinel
        # the bcrypt/argon hash must never appear
        assert "$argon2" not in body

    def test_change_password_and_rotate_controls(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert 'data-action="change-password"' in body
        assert 'data-action="rotate-sessions"' in body

    def test_password_dialog_present(self, logged_in: Any) -> None:
        body = logged_in[0].get("/settings").data.decode()
        assert 'data-dialog="password"' in body
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_settings_page.py::TestAuthControls -q`
Expected: FAIL.

- [ ] **Step 3: Create `auth_controls.html`** (rendered inside the auth pane; secrets shown as state + dedicated buttons)

```html
<div class="settings-field">
  <label>Password</label>
  <div class="auth-secret-row">
    <span class="auth-secret-state">Password: set</span>
    <button type="button" class="button button-secondary button-sm"
            data-action="change-password" @click="openPasswordDialog">Change password</button>
  </div>
</div>
<div class="settings-field">
  <label>Session secret</label>
  <div class="auth-secret-row">
    <span class="auth-secret-state">Session secret: set</span>
    <button type="button" class="button button-secondary button-sm"
            data-action="rotate-sessions" @click="rotateSessions">Rotate sessions</button>
  </div>
  <p class="field-help">Rotating invalidates existing sessions on the next container restart.</p>
</div>
```

(Note: "Password: set" is static text -- the app always has a password once first-run completes; the GET redaction means we never know/!show the value. If you prefer truthful "set/not set", the route can pass an `auth_password_set` bool from `store.get().auth.password_hash != ""`; add it to the `settings()` context and template if desired. Keep "set" for now -- first-run guarantees it is set before the page is reachable.)

- [ ] **Step 4: Create `password_dialog.html`** (native `<dialog>`, CSP-safe via $refs -- same pattern as the 2C stop modal)

```html
<dialog data-dialog="password" class="modal" x-ref="pwDialog" aria-labelledby="pw-title">
  <h3 class="modal-title" id="pw-title">Change password</h3>
  <div class="settings-errors" data-errors="password" x-show="hasErrors"></div>
  <form data-form="password">
    <div class="settings-field">
      <label for="pw-current">Current password</label>
      <input type="password" id="pw-current" class="form-input" data-field="current_password">
    </div>
    <div class="settings-field">
      <label for="pw-new">New password</label>
      <input type="password" id="pw-new" class="form-input" data-field="new_password">
    </div>
    <div class="settings-field">
      <label for="pw-confirm">Confirm new password</label>
      <input type="password" id="pw-confirm" class="form-input" data-field="confirm_password">
    </div>
    <div class="modal-actions">
      <button type="button" class="button button-secondary" @click="closePasswordDialog">Cancel</button>
      <button type="button" class="button button-primary" @click="submitPassword">Change</button>
    </div>
  </form>
</dialog>
```

- [ ] **Step 5: Run the tests**

Run: `venv/bin/pytest test/test_settings_page.py -q`
Expected: PASS. (Reuse the `.modal`/`.modal-title`/`.modal-actions`/`::backdrop` CSS from `dashboard.css`; if not globally available, add equivalents to `settings.css`.)

- [ ] **Step 6: Commit**

```bash
git add blackvuesync/server/templates/_partials/auth_controls.html blackvuesync/server/templates/_partials/password_dialog.html test/test_settings_page.py
git commit -m "feat: auth secret controls and change-password dialog"
```

---

### Task 5: `settings.js` -- Alpine CSP component (nav, typed save, toast, dialogs)

**Files:**

- Create: `blackvuesync/server/static/js/settings.js`

- [ ] **Step 1: Create the component** (CSP build: bare refs only; logic in methods; coercion by `data-type`)

```javascript
// settings.js: alpine (@alpinejs/csp) component for the settings page. owns
// section navigation, typed per-section save (json with correct types), the
// tier toast, the auth-mode confirm, and the change-password dialog.

const TOAST_MS = 4000;

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.content : "";
}

// builds a typed json object from a section/form's [data-field] inputs.
function collectFields(root) {
  const out = {};
  root.querySelectorAll("[data-field]").forEach((el) => {
    const name = el.dataset.field;
    const type = el.dataset.type;
    if (type === "bool") {
      out[name] = el.checked;
    } else if (type === "number") {
      out[name] = el.value === "" ? null : Number(el.value);
    } else if (type === "lines") {
      out[name] = el.value.split("\n").map((s) => s.trim()).filter((s) => s.length > 0);
    } else if (type === "letters") {
      out[name] = Array.from(el.querySelectorAll("input:checked")).map((c) => c.value);
    } else {
      out[name] = el.value;
    }
  });
  return out;
}

document.addEventListener("alpine:init", () => {
  Alpine.data("settingsPage", () => ({
    init() {
      // without js, all panes show (one scroll); js-nav switches to single-pane.
      this.$root.classList.add("js-nav");
      this.activate(this.$root.dataset.initial || "");
    },

    // toggles the .is-active pane and the .active nav item imperatively (no
    // per-element getters -- the csp build only sees bare @click="method" refs).
    activate(section) {
      this.$root.querySelectorAll("[data-pane]").forEach((p) => {
        p.classList.toggle("is-active", p.dataset.pane === section);
      });
      this.$root.querySelectorAll("[data-section-nav]").forEach((n) => {
        n.classList.toggle("active", n.dataset.sectionNav === section);
      });
    },

    select(ev) {
      this.activate(ev.currentTarget.dataset.sectionNav);
    },

    showToast(section, text) {
      const el = this.$root.querySelector(`[data-toast="${section}"]`);
      if (!el) return;
      el.textContent = text;
      el.classList.add("ok");
      el.hidden = false;
      setTimeout(() => { el.hidden = true; }, TOAST_MS);
    },

    setErrors(key, messages) {
      const el = this.$root.querySelector(`[data-errors="${key}"]`);
      if (!el) return;
      el.textContent = messages.join("; ");
      el.hidden = messages.length === 0;
    },

    async save(ev) {
      const section = ev.currentTarget.dataset.save;
      const form = this.$root.querySelector(`[data-form="${section}"]`);
      const payload = collectFields(form);
      if (section === "auth" && !this.confirmModeChange(payload)) return;
      const resp = await this.send(`/api/settings/${section}`, payload, "PATCH");
      if (!resp) { this.setErrors(section, ["save failed; please retry"]); return; }
      if (resp.status === 200) {
        const data = await resp.json();
        this.setErrors(section, []);
        this.showToast(section, this.tierMessage(data.tier));
      } else if (resp.status === 422) {
        const data = await resp.json();
        this.setErrors(section, (data.details?.field_errors || []).map((e) => e.message));
      } else {
        this.setErrors(section, ["save failed; please retry"]);
      }
    },

    tierMessage(tier) {
      if (tier === "immediate") return "Saved.";
      if (tier === "next_tick") return "Saved -- applies at the next sync.";
      return "Saved -- restart the container to take effect.";
    },

    confirmModeChange(payload) {
      // only guard when the auth mode actually differs from the rendered value
      const select = this.$root.querySelector('[data-form="auth"] [data-field="mode"]');
      const original = select ? select.dataset.original : null;
      if (original !== null && payload.mode !== original) {
        return globalThis.confirm("Changing the auth mode can affect your own access. Continue?");
      }
      return true;
    },

    openPasswordDialog() { this.$refs.pwDialog.showModal(); },
    closePasswordDialog() { this.setErrors("password", []); this.$refs.pwDialog.close(); },

    async submitPassword() {
      const form = this.$root.querySelector('[data-form="password"]');
      const f = collectFields(form);
      if (f.new_password !== f.confirm_password) {
        this.setErrors("password", ["new password and confirmation do not match"]);
        return;
      }
      const resp = await this.send("/api/auth/password",
        { current_password: f.current_password, new_password: f.new_password }, "POST");
      if (!resp) { this.setErrors("password", ["could not change password"]); return; }
      if (resp.status === 200) { this.setErrors("password", []); this.$refs.pwDialog.close(); }
      else if (resp.status === 422) {
        const data = await resp.json();
        this.setErrors("password", (data.details?.field_errors || []).map((e) => e.message));
      } else if (resp.status === 401) { this.setErrors("password", ["current password is incorrect"]); }
      else { this.setErrors("password", ["could not change password"]); }
    },

    async rotateSessions() {
      if (!globalThis.confirm("Rotate the session secret? Existing sessions end on the next restart.")) return;
      await this.send("/api/auth/sessions", null, "DELETE");
    },

    async send(path, payload, method) {
      try {
        return await fetch(path, {
          method: method,
          headers: { "X-CSRFToken": csrfToken(), "Content-Type": "application/json" },
          body: payload === null ? undefined : JSON.stringify(payload),
        });
      } catch {
        // network error; caller surfaces a retry message
        return null;
      }
    },
  }));
});
```

The error/toast `<span>`/`<div>` start with the `hidden` attribute; `setErrors`/
`showToast` set their text and flip `hidden`. No Alpine getters -- the only
directives are `x-data` on the root, `x-ref="pwDialog"` on the dialog, and bare
`@click="method"` handlers (CSP-build safe).

Add `data-original="{{ f.value }}"` to the auth `mode` select in `settings_field.html` only when `s.name == "auth"` and `f.name == "mode"` (so `confirmModeChange` can compare). Simplest: in the select branch add `{% if f.name == 'mode' %}data-original="{{ f.value }}"{% endif %}`.

- [ ] **Step 2: Lint check (no JS unit test; validated by e2e in Task 6)**

Run: `git diff --stat`. Confirm no `window.` (use `globalThis`), no inline expressions in templates, `@click="fn"` bare.

- [ ] **Step 3: Commit**

```bash
git add blackvuesync/server/static/js/settings.js blackvuesync/server/templates/_partials/settings_field.html
git commit -m "feat: settings.js alpine component for nav, save, and dialogs"
```

---

### Task 6: Playwright smoke

**Files:**

- Create: `test/e2e/test_settings_active.py` (reuses `test/e2e/conftest.py` `live_server`)

- [ ] **Step 1: Write the smoke test**

```python
"""playwright smoke: settings section nav, save->toast, validation, password dialog."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page, expect  # noqa: E402

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')


def test_section_nav_and_save_toast(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    # switch to logging (immediate tier) and save -> green toast
    page.click('[data-section-nav="logging"]')
    expect(page.locator('[data-pane="logging"]')).to_be_visible()
    page.click('[data-save="logging"]')
    expect(page.locator('[data-toast="logging"]')).to_contain_text("Saved", timeout=5000)


def test_validation_error_list(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.click('[data-section-nav="web"]')
    port = page.locator('[data-pane="web"] [data-field="port"]')
    port.fill("0")  # invalid: must be 1..65535
    page.click('[data-save="web"]')
    expect(page.locator('[data-errors="web"]')).to_contain_text("port", timeout=5000)


def test_password_dialog_opens(live_server: Any, page: Page) -> None:
    _login(page, live_server.url)
    page.goto(f"{live_server.url}/settings")
    page.click('[data-section-nav="auth"]')
    page.click('[data-action="change-password"]')
    expect(page.locator('dialog[data-dialog="password"]')).to_be_visible()
```

- [ ] **Step 2: Run it**

Run: `venv/bin/pytest test/e2e/test_settings_active.py -m e2e -q`
Expected: PASS against Chromium (the CSP build proves nav/save/dialog work eval-free). If a binding silently fails, a test fails -- fix until green.

- [ ] **Step 3: Commit**

```bash
git add test/e2e/test_settings_active.py
git commit -m "test: playwright smoke for settings nav, save, and password dialog"
```

---

### Task 7: Housekeeping

**Files:**

- Modify: `pyproject.toml`, `docs/api.md`

- [ ] **Step 1:** Bump `version` `2.4.0b0` -> `2.5.0a0` in `pyproject.toml` (new minor feature line).
- [ ] **Step 2:** Add `test_settings_form` and `test_settings_page` to the mypy per-module override list (match the existing pattern; `test_settings_active` is under `test.e2e.*` already covered).
- [ ] **Step 3:** `docs/api.md` -- note the `/settings` page now drives `GET/PATCH /api/settings` and the auth password/session endpoints from the UI (no new endpoints in #3).
- [ ] **Step 4: Full verification**

Run: `venv/bin/pytest test/ -q && venv/bin/mypy blackvuesync && venv/bin/pylint blackvuesync`
Expected: green (e2e auto-deselected by the default `-m 'not e2e'`).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/api.md
git commit -m "chore: bump version, mypy overrides, document settings ui"
```

---

## Final verification (before PR)

- [ ] `venv/bin/pytest test/ -q` -- all green.
- [ ] `venv/bin/pytest test/e2e/ -m e2e -q` -- Playwright settings + dashboard smokes green (real Chromium).
- [ ] `behave` -- unaffected.
- [ ] `pre-commit run --files <changed>` -- green (incl. mypy across all files: no duplicate-conftest; e2e excluded from the mypy hook already).
- [ ] Manual: serve, log in, `/settings`, switch sections, edit + save each tier (toast/banner), change password, rotate sessions, hit a validation error.
- [ ] Push branch, open PR; all required checks green.
- [ ] After CI: query `sonarcloud.io/api/issues/search?...&pullRequest=<N>&resolved=false` -- confirm **0 findings** (do not trust the gate; check the JS smells especially -- globalThis, optional chaining, no-void, optional catch binding, nested-function depth).
- [ ] Squash- or rebase-merge.

## Self-review

- **Spec coverage:** sidebar+pane layout (Task 2) · per-section save + tier affordance (Tasks 4/5: `tierMessage`) · type-driven widgets (Task 3) · section-level validation list (Task 5: `errors` + `hasErrors`) · auth password/rotate/mode-confirm + secret masking (Tasks 4/5) · all nine sections (Task 1 `SECTION_FIELD_SPECS` covers every section) · CSP-build discipline (Task 5) · structure + Playwright tests (Tasks 1-4, 6).
- **Placeholders:** none -- `settings_form.py`, the partials, `settings.js`, and tests are complete. The one judgment call (`Password: set` static vs. a route-passed `auth_password_set` bool) is documented with the alternative.
- **Type/name consistency:** `data-field`/`data-type`/`data-pane`/`data-section-nav`/`data-form`/`data-save`/`data-toast`/`data-errors`/`data-dialog`/`data-original` and the Alpine methods (`activate`/`select`/`save`/`showToast`/`setErrors`/`tierMessage`/`confirmModeChange`/`openPasswordDialog`/`closePasswordDialog`/`submitPassword`/`rotateSessions`/`send`) are used consistently across `settings.html`, `settings_field.html`, the auth/dialog partials, `settings.js`, and the tests. `collectFields` coercion matches the `data_type` values from `FieldSpec`. No Alpine getters (visibility/toast/errors are imperative).
- **Scope:** frontend + `ui.py` render context only; no backend endpoints; `settings.json` schema unchanged; all nine sections editable.
- **CSP-build safety:** the only directives are `x-data` (root), `x-ref="pwDialog"`, and bare `@click="method"` handlers -- no inline expressions, no parens. Visibility is CSS-class-driven (`.is-active`, gated by `.js-nav` for no-JS graceful degradation) and toggled imperatively in `activate()`; toast/errors are set imperatively. Validated end-to-end by the Playwright smoke (Task 6), which fails if any binding silently breaks under the eval-free build.
