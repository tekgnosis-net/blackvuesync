# Web Foundation -- Phase G: cmd_serve logging, API docs, README polish

<!-- markdownlint-disable MD031 MD032 MD033 MD040 MD050 -->

**Date:** 2026-05-19
**Spec:** [`2026-05-18-web-foundation-design.md`](./2026-05-18-web-foundation-design.md) (Section "Next steps after this plan" -- Phase G description; Section 5 "Manual verification checklist")
**Phase:** G of 7 (A-F done; final foundation phase).

**Goal:** Close the Web Foundation sub-project by filling the three remaining
gaps: (1) the long-running `serve` process produces no startup logs because
`cmd_serve` never invokes `configure_logging` / `set_logging_levels`;
(2) `docs/api.md` is missing the 5 Phase F endpoints (`/api/settings/*` and
`/api/auth/*`); (3) the README is missing the foundation-complete pointers
the master design spec called for (sample reverse-proxy Caddyfile,
explicit Web Foundation completion banner).

**Why so small:** Phase C and D delivered the Apple-design scaffolding
ahead of the original plan (88-line `tokens.css`, 146-line
`components.css`, 201-line `layout.css`, all 5 placeholder pages styled,
htmx + alpine vendored, system font stack, security headers, ProxyFix).
Verification of those items lives in the test suite already and in the
spec self-review tables of Phases C/D. Phase G does not duplicate that
work; it ships only what is genuinely outstanding.

**Out of scope (carry-forward to post-foundation cleanup):**

- `sync.py` cognitive-complexity decomposition (`download_file`,
  `download_recording` -- S3776 carry-forward since Phase D). The diff
  for that refactor is pure restructuring with no behavior change and
  deserves its own focused review window. A "sync.py: reduce cognitive
  complexity" PR follows Phase G.
- Multi-stage Dockerfile (drop the uv binary from the final image). The
  current single-stage build with `rm /usr/local/bin/uv` keeps the
  binary in the COPY layer; a multi-stage refactor reclaims those bytes
  but is a structural Dockerfile change with its own platform-matrix
  risk. Separate post-foundation PR.
- New Apple-design components beyond the existing three (button, card,
  alert). Sub-project #2 (Dashboard) adds whatever else it needs.
- Light/dark mode toggle -- the master design spec defers this to
  sub-project #2.

---

## Implementer guidelines (karpathy discipline)

1. **Think before coding.** State assumptions explicitly. If a step is
   ambiguous, stop and report DONE_WITH_CONCERNS rather than picking
   silently.
2. **Simplicity first.** Minimum code that solves the problem. No
   speculative configurability. No "while I'm here" CSS polish, no
   docstring rewrites on unmodified functions, no README reorgs beyond
   what this plan asks for.
3. **Surgical changes.** Touch only the files this plan lists.
4. **Goal-driven execution.** Each task has a verifiable check.

Process hygiene:

- Never use `git add -A` or `git add .`. List files explicitly.
- Never use `--no-verify`.
- Never amend an existing commit after a pre-commit auto-fix.
- Comments lowercase, third-person, non-obvious. Entity names keep
  their casing.
- Commit-message titles ≤ 72 chars (gitlint).

---

## File Structure

### Files to modify

- `blackvuesync/__main__.py` -- `cmd_serve` calls `configure_logging` and
  `set_logging_levels` from the already-imported names; the existing
  `cmd_sync` pattern at lines 273-274 is the canonical reference.
- `docs/api.md` -- append two new sections (Settings API and Auth API
  beyond `/first-run` etc.) covering the 5 Phase F endpoints.
- `README.md` -- add a "Reverse proxy" subsection under "Docker" with a
  short Caddyfile snippet (per the master design spec) and a
  "Web Foundation complete" line near the top.
- `pyproject.toml` -- bump version to `2.3.0` (drop the alpha; the
  foundation is complete).

### Files to create

- `test/test_main_serve_logging.py` -- a single unit test that imports
  `cmd_serve`, runs it with mocked Waitress + scheduler, and asserts
  that `configure_logging` was called.

### Files explicitly NOT to modify

- `blackvuesync/sync.py`
- `blackvuesync/metrics.py`
- `blackvuesync/settings.py`
- Any file under `blackvuesync/server/` (auth, scheduler, sync_runner,
  progress, routes/*, templates/*, static/*).
- Any test file other than the one new `test_main_serve_logging.py`.
- Dockerfile, entrypoint.sh, docker-compose.yml, run.sh.

---

## Task 1: Bump version

**Files:**

- Modify: `pyproject.toml`

- [ ] **Step 1: Change version**

`version = "2.3.0a2"` -> `version = "2.3.0"`.

The Web Foundation sub-project is feature-complete after Phase G; the
2.3.0 release tag marks the cutover from cron-era 2.2.x to web-service
2.3.0.

- [ ] **Step 2: Verify install**

Run: `pip install -e ".[dev]"` -- expected: clean install.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "Phase G: drop alpha tag for 2.3.0 Web Foundation release"
```

---

## Task 2: Write failing test for cmd_serve logging

**Files:**

- Create: `test/test_main_serve_logging.py`

- [ ] **Step 1: Write the test**

```python
"""tests that cmd_serve wires up logging on startup."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def test_cmd_serve_configures_logging(settings_path: Path) -> None:
    """cmd_serve must call configure_logging so scheduler / waitress info
    messages are visible in docker logs and the log-viewer."""
    from blackvuesync.__main__ import cmd_serve

    args = argparse.Namespace(
        port=None,
        config_path=str(settings_path),
    )

    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False), \
         patch("blackvuesync.__main__.configure_logging") as mock_cfg, \
         patch("blackvuesync.__main__.set_logging_levels") as mock_set, \
         patch("waitress.serve") as mock_waitress, \
         patch("blackvuesync.server.scheduler.init_scheduler") as mock_init:
        mock_waitress.return_value = None
        cmd_serve(args)

    mock_cfg.assert_called_once()
    mock_set.assert_called_once()
    mock_init.assert_called_once()
    mock_waitress.assert_called_once()
```

- [ ] **Step 2: Add the test module to the mypy override list**

In `pyproject.toml`, append `"test_main_serve_logging"` to the existing
`[[tool.mypy.overrides]]` block that lists the per-test-module
disable-untyped-decorators entries (the same block updated in Phase F
Task 1).

- [ ] **Step 3: Run the test to confirm it fails**

Run: `venv/bin/pytest test/test_main_serve_logging.py -v`
Expected: FAIL -- `configure_logging` is not called by `cmd_serve` in
the current code.

---

## Task 3: Wire logging into cmd_serve

**Files:**

- Modify: `blackvuesync/__main__.py`

- [ ] **Step 1: Update cmd_serve**

Add the two existing-already-imported calls at the top of `cmd_serve`,
before the deferred Flask/Waitress imports. The two function names
(`configure_logging`, `set_logging_levels`) are already imported at
module level (lines 33 and 40); only the call sites are missing.

Insert directly after the docstring on the `cmd_serve` function:

```python
def cmd_serve(args: argparse.Namespace) -> int:
    """starts the web server and APScheduler; blocks until interrupted."""
    # configures logging using the settings.logging section once we have
    # loaded the settings store below. for now configure with defaults so
    # startup messages (including settings-load errors) are visible.
    configure_logging("text")
    set_logging_levels(verbose=1, cron=False)
    ...
```

Then after `store = SettingsStore(config_path)` is loaded, re-apply the
log level from the settings:

```python
    store = SettingsStore(config_path)
    settings = store.get()
    # re-applies log level from settings now that the store is loaded;
    # the format itself stays whatever was set at startup (a format change
    # is TIER='immediate' but requires re-attaching the handler, which
    # the future LogSettings on_change listener will own; for phase g we
    # rely on the startup-time default and only update verbosity).
    set_logging_levels(
        verbose=-1 if settings.logging.quiet else settings.logging.verbose,
        cron=False,
    )
    publisher = ProgressPublisher()
    ...
```

(Match indentation and surrounding lines exactly; do not re-format the
rest of `cmd_serve`.)

- [ ] **Step 2: Run the test**

Run: `venv/bin/pytest test/test_main_serve_logging.py -v`
Expected: PASS.

- [ ] **Step 3: Smoke-test the server**

Run in one terminal:

```bash
BLACKVUESYNC_CONFIG_PATH=/tmp/bvs-g-test-settings.json \
ADDRESS=192.168.0.1 \
venv/bin/python -m blackvuesync serve
```

Expected: the terminal prints at least two log lines:
`scheduler started: '*/15 * * * *' (UTC)` and `starting web server on
0.0.0.0:8080`. Without Phase G's fix, the terminal is silent on
stdout until Waitress emits its own message.

Then `Ctrl-C`. Clean up: `rm -f /tmp/bvs-g-test-settings.json`.

- [ ] **Step 4: Commit**

```bash
git add blackvuesync/__main__.py test/test_main_serve_logging.py pyproject.toml
git commit -m "Phase G: configure logging in cmd_serve"
```

(pyproject.toml carries the mypy override added in Task 2 Step 2.)

---

## Task 4: Document /api/settings endpoints in docs/api.md

**Files:**

- Modify: `docs/api.md`

- [ ] **Step 1: Append the Settings API section**

After the existing "HTMX Fragment Endpoints" section, add a new top-level
`## Settings API Endpoints` section with these subsections:

- `### GET /api/settings` -- describe the redacted response, the
  per-section `_tier` annotation, and the `***` sentinel meaning. Show a
  truncated example response.
- `### PATCH /api/settings/<section>` -- describe the request body
  semantics (partial dict), the `***` round-trip, JSON-list-to-tuple
  coercion for `sync.include` / `sync.exclude` / `sync.skip_metadata`,
  the 200 response shape (`{section, tier, applied}`), and the four
  error codes used (`INVALID_BODY`, `SECTION_NOT_FOUND`,
  `SETTINGS_INVALID`).

Each subsection follows the existing pattern in `docs/api.md`: an h3
heading, one paragraph of prose, and one fenced JSON block per request
or response shape. Match the style of the existing `GET /api/sync/progress`
entry as the canonical reference.

- [ ] **Step 2: Append the Auth API section**

After Settings API, add `## Auth API Endpoints`:

- `### GET /api/auth/me` -- response shape; mention that mode is read
  fresh on every request.
- `### POST /api/auth/password` -- request body, the four response
  codes (`INVALID_BODY`, `INVALID_CURRENT_PASSWORD`, `WEAK_PASSWORD`,
  `RATE_LIMITED`), the success body, and a note that the rate-limit
  bucket is shared with `/login`.
- `### DELETE /api/auth/sessions` -- response shape, restart-required
  semantics (Flask reads SECRET_KEY at create_app time).

- [ ] **Step 3: Sanity-check markdown**

Run: `pre-commit run markdownlint-cli2 --files docs/api.md`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/api.md
git commit -m "Phase G: document Phase F endpoints in docs/api.md"
```

---

## Task 5: README polish

**Files:**

- Modify: `README.md`

- [ ] **Step 1: Add a "Reverse proxy" subsection**

The master design spec calls for a sample Caddyfile snippet. Locate the
existing "Docker" section in README.md; add a new `##### Reverse Proxy`
subsection (peer of `##### Overview` and `##### Quick Start`) with:

```caddyfile
blackvuesync.example.com {
    encode zstd gzip
    reverse_proxy localhost:8080
}
```

and a short paragraph (2-3 sentences) noting that:
- The Flask service serves HTTP on 8080; HTTPS terminates at the proxy.
- Set `BLACKVUESYNC_TRUST_PROXY=1` so the session cookie is marked
  `Secure` (already documented elsewhere; cross-reference, do not
  duplicate the env var explanation).

- [ ] **Step 2: Add a "Web Foundation complete" banner**

Near the top of README.md (under the project description), add one short
sentence pointing to `docs/api.md`:

> The HTTP API surface is documented in [docs/api.md](docs/api.md). The
> long-running web service started shipping in 2.3.0; older releases run
> the cron-era CLI.

- [ ] **Step 3: Sanity-check markdown**

Run: `pre-commit run markdownlint-cli2 --files README.md`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Phase G: README reverse-proxy example and api.md pointer"
```

---

## Task 6: Final sweep

- [ ] **Step 1: Full unit suite**

Run: `venv/bin/pytest test/`
Expected: 391 tests pass (390 from Phase F + 1 new).

- [ ] **Step 2: Behave subprocess**

Run: `behave`
Expected: 21/21.

- [ ] **Step 3: Behave docker**

Run: `behave -D implementation=docker`
Expected: 21/21. (Phase G adds no Docker behavior; this is a regression
guard.)

- [ ] **Step 4: Pre-commit**

Run: `pre-commit run --all-files`
Expected: all hooks pass.

---

## Task 7: Open the PR

- [ ] **Step 1: Push**

```bash
git push -u origin web-foundation-phase-g
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --repo tekgnosis-net/blackvuesync \
  --base main \
  --head web-foundation-phase-g \
  --title "Web Foundation Phase G: cmd_serve logging, API docs, README" \
  --body "$(cat <<'EOF'
## Summary
- Wires `configure_logging` and `set_logging_levels` into `cmd_serve` so scheduler / server startup messages actually appear in `docker logs`.
- Documents the 5 Phase F endpoints (`GET /api/settings`, `PATCH /api/settings/<section>`, `GET /api/auth/me`, `POST /api/auth/password`, `DELETE /api/auth/sessions`) in `docs/api.md`.
- Adds a `##### Reverse Proxy` subsection to the README with a sample Caddyfile and a `docs/api.md` cross-reference.
- Drops the alpha tag for the 2.3.0 release.

## Out of scope (carry-forward to post-foundation cleanup)
- `sync.py` cognitive-complexity decomposition (S3776) -- separate refactor PR.
- Multi-stage Dockerfile to drop the uv binary -- separate Docker cleanup PR.

## Test plan
- [ ] Unit: `pytest test/` -- expects 391 passed
- [ ] Behave subprocess: `behave`
- [ ] Behave docker: `behave -D implementation=docker`
- [ ] CI: 5 required checks green
EOF
)"
```

- [ ] **Step 3: Wait for 5 required checks.**

- [ ] **Step 4: Squash-merge once green** (controlling agent does this).

---

## Self-review against spec

| Spec requirement (Phase G description) | Plan task |
| --- | --- |
| Apple-design scaffolding | Verified already in place (Phase C). No-op. |
| Placeholder pages with base layout | Verified already in place (Phase C). No-op. |
| Security headers | Verified already in place (Phase C). No-op. |
| ProxyFix | Verified already in place (Phase C). No-op. |
| Docs (handwritten `docs/api.md`) | Tasks 4 + 5 |
| `cmd_serve` logging | Tasks 2 + 3 |
| Version 2.3.0 release tag | Task 1 |

## What is NOT done in Phase G (recap)

- `sync.py` decomposition -- separate PR.
- Multi-stage Dockerfile -- separate PR.
- Dashboard, Settings UI, Logs UI, Stats, Viewer -- sub-projects #2-#6.
