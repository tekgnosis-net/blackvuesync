# Sub-Project #5 -- Statistics Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/stats` page that visualizes per-run sync metrics over time (with a disk-usage forecast), backed by a new SQLite time-series store fed by wiring `SyncMetrics` into the serve sync path.

**Architecture:** Each scheduled serve sync now builds + finalizes a `SyncMetrics` (closing the serve-mode Prometheus/state gap), writes one row to a SQLite `StatsStore` (`/config/stats.db`), and prunes per a new `stats.retention_days` setting. A JSON API (`/api/stats/series`) returns summary + series + a server-computed disk forecast; the `/stats` page renders interactive vendored Chart.js charts with a server-rendered fallback.

**Tech Stack:** Python stdlib `sqlite3`, Flask, Jinja2, vendored Chart.js v4 (canvas), `@alpinejs/csp` + HTMX (existing), pytest + Flask test client, pytest-playwright.

**Design:** `docs/plans/2026-06-07-sub-project-5-statistics-design.md`.

**Branch:** `sub-project-5-statistics` (already checked out; spec committed as `1019384`).

**Conventions (enforced every task):** run tests via `venv/bin/pytest`; stage commits by explicit path (NEVER `git add -A`/`git add .` -- a developer-local `supertool` symlink must not be staged); NEVER `--no-verify` (fix pre-commit failures and re-stage; never amend past a hook auto-fix -- make a new commit); comments lowercase/third-person/non-obvious; full type annotations; `blackvuesync/metrics.py` stays stdlib-only (`sqlite3` is stdlib, so the store adds no dependency, but it lives server-side); `@alpinejs/csp` directives are bare property/method refs; CSP is unchanged (`script-src 'self'` covers vendored Chart.js -- no `eval`); target 0 SonarCloud findings (verify via the issues API). In committed markdown use `--` not em-dashes (the dash-normalizer hook rewrites them).

---

## File structure

**Create:**

- `blackvuesync/server/stats_store.py` -- `RunRow` dataclass + `StatsStore` (schema, `record_run`, `query`, `prune`; WAL; `/config/stats.db`).
- `blackvuesync/server/forecast.py` -- pure-Python disk-usage forecast (`compute_forecast`, `Forecast` dataclass, linear fit + clamp-to-ceiling).
- `blackvuesync/server/routes/api_stats.py` -- `api_stats_bp`: `GET /api/stats/series`.
- `blackvuesync/server/templates/stats.html` -- the page (summary + range selector + fallback table + canvases).
- `blackvuesync/server/static/js/stats.js` -- fetches the API, renders Chart.js charts.
- `blackvuesync/server/static/js/chart.umd.min.js` -- vendored Chart.js v4 (excluded from Sonar).
- `blackvuesync/server/static/css/stats.css` -- range chips, summary tiles, chart-card grid.
- `test/test_stats_store.py`, `test/test_forecast.py`, `test/test_routes_api_stats.py`, `test/e2e/test_stats_page.py`.

**Modify:**

- `blackvuesync/settings.py` -- add `StatsSettings`; register in `Settings`, `_SECTION_FIELDS`, `Settings.validate`, env bootstrap.
- `blackvuesync/server/settings_form.py` -- `FieldSpec` + label for `stats`.
- `blackvuesync/server/sync_runner.py` -- build/finalize `SyncMetrics` in `_do_sync`; emit/save; record+prune when a store is supplied; thread `stats_store` through `trigger_sync`/`_do_sync`.
- `blackvuesync/server/scheduler.py` -- thread `stats_store` through `init_scheduler`/`_scheduled_run`.
- `blackvuesync/server/routes/api_sync.py` -- pass `current_app.stats_store` to `trigger_sync`.
- `blackvuesync/server/__init__.py` -- `create_app(..., stats_store=None)` attaches `app.stats_store`; register `api_stats_bp`.
- `blackvuesync/__main__.py` -- construct `StatsStore` in `cmd_serve`; pass to `init_scheduler` and `create_app`.
- `blackvuesync/server/routes/ui.py` -- `/stats` renders the real page.
- `sonar-project.properties` -- exclude `chart.umd.min.js` from analysis.
- `docs/api.md`, `pyproject.toml` (version + mypy overrides).

**Delete:** `blackvuesync/server/templates/_placeholders/stats.html`.

---

### Task 1: SQLite time-series store

**Files:**

- Create: `blackvuesync/server/stats_store.py`
- Test: `test/test_stats_store.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_stats_store.py`:

```python
"""unit tests for the sqlite per-run stats store."""

from __future__ import annotations

import time
from pathlib import Path

from blackvuesync.metrics import SyncMetrics
from blackvuesync.server.stats_store import RunRow, StatsStore


def _metrics(ts: float, *, files: int = 2, byts: int = 100, success: int = 1) -> SyncMetrics:
    m = SyncMetrics(run_start_monotonic=0.0, run_start_timestamp=ts)
    m.last_run_timestamp_seconds = ts
    m.last_run_success = success
    m.last_run_exit_code = 0
    m.run_duration_seconds = 1.5
    m.files_downloaded_last_run = files
    m.bytes_downloaded_last_run = byts
    m.destination_disk_used_ratio = 0.42
    m.file_download_failures_last_run = {"http": 1, "network": 0, "timeout": 0, "disk": 0, "unknown": 0}
    m.last_run_failures = {"http": 0, "network": 0, "timeout": 0, "disk": 0, "unknown": 0}
    return m


def test_record_and_query_roundtrip(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    store.record_run(_metrics(1000.0))
    rows = store.query()
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, RunRow)
    assert row.ts_seconds == 1000.0
    assert row.files == 2
    assert row.bytes == 100
    assert row.disk_used_ratio == 0.42
    assert row.failures["http"] == 1


def test_query_orders_ascending_and_filters_since(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    for ts in (3000.0, 1000.0, 2000.0):
        store.record_run(_metrics(ts))
    assert [r.ts_seconds for r in store.query()] == [1000.0, 2000.0, 3000.0]
    assert [r.ts_seconds for r in store.query(since_ts=2000.0)] == [2000.0, 3000.0]


def test_record_run_skips_when_no_timestamp(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    m = SyncMetrics(run_start_monotonic=0.0, run_start_timestamp=0.0)
    m.last_run_timestamp_seconds = None  # not finalized
    store.record_run(m)
    assert store.query() == []


def test_record_is_idempotent_on_same_ts(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    store.record_run(_metrics(1000.0, files=2))
    store.record_run(_metrics(1000.0, files=9))  # same ts -> replace
    rows = store.query()
    assert len(rows) == 1
    assert rows[0].files == 9


def test_prune_deletes_old_rows(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    now = time.time()
    store.record_run(_metrics(now - 10 * 86400))  # 10 days old
    store.record_run(_metrics(now - 1 * 86400))  # 1 day old
    deleted = store.prune(retention_days=5)
    assert deleted == 1
    assert [round(r.ts_seconds) for r in store.query()] == [round(now - 86400)]


def test_prune_zero_keeps_all(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    store.record_run(_metrics(time.time() - 9999 * 86400))
    assert store.prune(retention_days=0) == 0
    assert len(store.query()) == 1


def test_dry_run_flag_persisted(tmp_path: Path) -> None:
    store = StatsStore(str(tmp_path / "stats.db"))
    m = _metrics(1000.0)
    m.dry_run = True
    store.record_run(m)
    assert store.query()[0].dry_run == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_stats_store.py -q`
Expected: FAIL -- `ModuleNotFoundError: No module named 'blackvuesync.server.stats_store'`.

- [ ] **Step 3: Implement `stats_store.py`**

Create `blackvuesync/server/stats_store.py`:

```python
"""sqlite time-series store of per-run sync metrics (serve mode only).

one row per sync run. sqlite3 is stdlib (no new dependency). the sync thread
is the sole writer; flask handlers are readers. WAL lets reads proceed during
a write. a short-lived connection is opened per call (never shared across
threads).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackvuesync.metrics import SyncMetrics

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    ts_seconds          REAL PRIMARY KEY,
    success             INTEGER NOT NULL,
    exit_code           INTEGER,
    duration_seconds    REAL    NOT NULL DEFAULT 0,
    files               INTEGER NOT NULL DEFAULT 0,
    bytes               INTEGER NOT NULL DEFAULT 0,
    recordings_seen     INTEGER NOT NULL DEFAULT 0,
    recordings_selected INTEGER NOT NULL DEFAULT 0,
    disk_used_ratio     REAL,
    failed_markers      INTEGER NOT NULL DEFAULT 0,
    failures_json       TEXT,
    dry_run             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs (ts_seconds);
"""

_SECONDS_PER_DAY = 86400.0


@dataclasses.dataclass(frozen=True)
class RunRow:
    """one stored sync run."""

    ts_seconds: float
    success: int
    exit_code: int | None
    duration_seconds: float
    files: int
    bytes: int
    recordings_seen: int
    recordings_selected: int
    disk_used_ratio: float | None
    failed_markers: int
    failures: dict[str, int]
    dry_run: int


class StatsStore:
    """sqlite-backed per-run metrics store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """opens a fresh WAL connection with row access by name."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def record_run(self, metrics: SyncMetrics) -> None:
        """inserts (or replaces) one run row from a finalized SyncMetrics.

        no-op if the metrics were never finalized (no run timestamp).
        """
        ts = metrics.last_run_timestamp_seconds
        if ts is None:
            return
        failures: dict[str, int] = dict(metrics.last_run_failures or {})
        for reason, count in (metrics.file_download_failures_last_run or {}).items():
            failures[reason] = failures.get(reason, 0) + count
        row = (
            float(ts),
            int(metrics.last_run_success),
            metrics.last_run_exit_code,
            float(metrics.run_duration_seconds),
            int(metrics.files_downloaded_last_run),
            int(metrics.bytes_downloaded_last_run),
            int(metrics.dashcam_recordings_seen),
            int(metrics.recordings_selected),
            metrics.destination_disk_used_ratio,
            int(metrics.failed_marker_files),
            json.dumps(failures, separators=(",", ":")),
            1 if metrics.dry_run else 0,
        )
        with contextlib.closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (ts_seconds, success, exit_code, "
                "duration_seconds, files, bytes, recordings_seen, "
                "recordings_selected, disk_used_ratio, failed_markers, "
                "failures_json, dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )

    def query(self, since_ts: float | None = None) -> list[RunRow]:
        """returns rows ordered by timestamp ascending; optionally since since_ts."""
        sql = "SELECT * FROM runs"
        params: tuple[float, ...] = ()
        if since_ts is not None:
            sql += " WHERE ts_seconds >= ?"
            params = (since_ts,)
        sql += " ORDER BY ts_seconds ASC"
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(sql, params)
            return [self._to_row(r) for r in cursor.fetchall()]

    def prune(self, retention_days: int) -> int:
        """deletes rows older than retention_days; no-op when retention_days <= 0.

        returns the number of rows deleted.
        """
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * _SECONDS_PER_DAY
        with contextlib.closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM runs WHERE ts_seconds < ?", (cutoff,))
            return cursor.rowcount

    @staticmethod
    def _to_row(record: sqlite3.Row) -> RunRow:
        """maps a sqlite row to a RunRow, decoding the failures json."""
        try:
            failures = json.loads(record["failures_json"]) if record["failures_json"] else {}
        except (ValueError, TypeError):
            failures = {}
        return RunRow(
            ts_seconds=record["ts_seconds"],
            success=record["success"],
            exit_code=record["exit_code"],
            duration_seconds=record["duration_seconds"],
            files=record["files"],
            bytes=record["bytes"],
            recordings_seen=record["recordings_seen"],
            recordings_selected=record["recordings_selected"],
            disk_used_ratio=record["disk_used_ratio"],
            failed_markers=record["failed_markers"],
            failures=failures,
            dry_run=record["dry_run"],
        )


__all__ = ["RunRow", "StatsStore"]
```

- [ ] **Step 4: Run to confirm pass**

Run: `venv/bin/pytest test/test_stats_store.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/stats_store.py test/test_stats_store.py
git commit -m "feat: add sqlite per-run stats time-series store"
```

---

### Task 2: `stats` settings section + housekeeping knob

**Files:**

- Modify: `blackvuesync/settings.py` (add `StatsSettings`; register in `Settings`, `_SECTION_FIELDS`, `Settings.validate`, `_bootstrap_from_env`)
- Modify: `blackvuesync/server/settings_form.py` (`SECTION_FIELD_SPECS["stats"]`, `SECTION_LABELS["stats"]`)
- Test: `test/test_settings.py` (append), `test/test_settings_form.py` (append), `test/test_routes_api_settings.py` (append a round-trip)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_settings.py`:

```python
def test_stats_section_defaults_and_validate() -> None:
    from blackvuesync.settings import Settings, StatsSettings

    s = Settings()
    assert isinstance(s.stats, StatsSettings)
    assert s.stats.retention_days == 365
    assert StatsSettings(retention_days=0).validate() == []
    assert StatsSettings(retention_days=-1).validate() == [
        "stats.retention_days must be zero or greater"
    ]


def test_stats_section_roundtrips_through_dict() -> None:
    import dataclasses

    from blackvuesync.settings import Settings, _settings_from_dict, _settings_to_dict

    s = Settings(stats=dataclasses.replace(Settings().stats, retention_days=30))
    raw = _settings_to_dict(s)
    assert raw["stats"] == {"retention_days": 30}
    assert _settings_from_dict(raw).stats.retention_days == 30


def test_stats_section_defaults_when_absent_from_file() -> None:
    from blackvuesync.settings import _settings_from_dict

    # an old settings file without a "stats" key still loads with the default
    settings = _settings_from_dict({"version": 1, "connection": {"address": "1.2.3.4"}})
    assert settings.stats.retention_days == 365
```

Append to `test/test_settings_form.py` (match the existing import/test style in that file; if the file does not exist, create it with the import `from blackvuesync.server.settings_form import SECTION_FIELD_SPECS, SECTION_LABELS, build_sections`):

```python
def test_stats_section_has_retention_field() -> None:
    from blackvuesync.server.settings_form import SECTION_FIELD_SPECS, SECTION_LABELS

    assert SECTION_LABELS["stats"] == "Statistics"
    names = [f.name for f in SECTION_FIELD_SPECS["stats"]]
    assert names == ["retention_days"]
    spec = SECTION_FIELD_SPECS["stats"][0]
    assert spec.widget == "number" and spec.data_type == "number"
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_settings.py -q -k stats`
Expected: FAIL -- `ImportError: cannot import name 'StatsSettings'`.

- [ ] **Step 3: Add `StatsSettings` to `settings.py`**

Add the dataclass immediately after `MetricsSettings` (after its `validate`, around line 204):

```python
@dataclass(frozen=True)
class StatsSettings:
    """statistics time-series store settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    retention_days: int = 365  # prune run records older than this; 0 keeps all

    def validate(self) -> list[str]:
        """validates stats settings; returns a list of error strings."""
        if self.retention_days < 0:
            return ["stats.retention_days must be zero or greater"]
        return []
```

In the `Settings` dataclass, add the field after `metrics` (line 291):

```python
    stats: StatsSettings = field(default_factory=StatsSettings)
```

In `Settings.validate` (after the `metrics` line, ~line 304):

```python
        errors.extend(self.stats.validate())
```

In `_SECTION_FIELDS` (after `"metrics": MetricsSettings,`):

```python
    "stats": StatsSettings,
```

In `_bootstrap_from_env`, after the `metrics = MetricsSettings(...)` block (~line 577), add:

```python
        stats = StatsSettings(
            retention_days=int(_env("STATS_RETENTION_DAYS", "365")),
        )
```

and pass it in the `Settings(...)` constructor near the end of `_bootstrap_from_env` (add `stats=stats,` after `metrics=metrics,`).

- [ ] **Step 4: Add the form spec**

In `blackvuesync/server/settings_form.py`, add to `SECTION_FIELD_SPECS` after the `"metrics": (...)` block:

```python
    "stats": (
        FieldSpec(
            "retention_days",
            "History retention (days)",
            "number",
            "number",
            help="prune run history older than this; 0 keeps all",
        ),
    ),
```

and to `SECTION_LABELS` after `"metrics": "Metrics",`:

```python
    "stats": "Statistics",
```

- [ ] **Step 5: Run to confirm pass**

Run: `venv/bin/pytest test/test_settings.py test/test_settings_form.py -q`
Expected: PASS.

- [ ] **Step 6: Verify GET/PATCH wiring (round-trip test)**

Append to `test/test_routes_api_settings.py` (reuse that file's existing logged-in client fixture; match its fixture name):

```python
def test_stats_section_get_and_patch(logged_in_client) -> None:  # type: ignore[no-untyped-def]
    client = logged_in_client if not isinstance(logged_in_client, tuple) else logged_in_client[0]
    get = client.get("/api/settings")
    import json as _json

    body = _json.loads(get.data)
    assert "stats" in body
    assert body["stats"]["retention_days"] == 365
    assert body["stats"]["_tier"] == "next_tick"

    csrf = client.get("/api/auth/me")  # ensures session; CSRF disabled under testing
    patch = client.patch(
        "/api/settings/stats",
        json={"retention_days": 30},
    )
    assert patch.status_code == 200
    assert client.get("/api/settings").json["stats"]["retention_days"] == 30
```

Run: `venv/bin/pytest test/test_routes_api_settings.py -q -k stats`
Expected: PASS. If the GET response has no `_tier` for `stats` or the PATCH 404s, the api_settings route does not iterate `_SECTION_FIELDS` dynamically -- inspect `blackvuesync/server/routes/api_settings.py` and add `stats` wherever sections are enumerated (it should already iterate `_SECTION_FIELDS`; if so, no change is needed).

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/settings.py blackvuesync/server/settings_form.py test/test_settings.py test/test_settings_form.py test/test_routes_api_settings.py
git commit -m "feat: add stats settings section with retention_days"
```

---

### Task 3: disk-usage forecast

**Files:**

- Create: `blackvuesync/server/forecast.py`
- Test: `test/test_forecast.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_forecast.py`:

```python
"""unit tests for the disk-usage forecast."""

from __future__ import annotations

from blackvuesync.server.forecast import Forecast, compute_forecast


def _rising() -> list[tuple[float, float]]:
    # ts in seconds, ratio rising 0.40 -> 0.50 over 5 points one day apart
    return [(i * 86400.0, 0.40 + i * 0.025) for i in range(5)]


def test_too_few_points_no_projection() -> None:
    fc = compute_forecast(
        [(0.0, 0.4), (86400.0, 0.42)],
        horizon_seconds=7 * 86400.0,
        steps=7,
        max_used_disk_ratio=0.90,
        keep_steady_state_ratio=None,
    )
    assert isinstance(fc, Forecast)
    assert fc.projected == []
    assert fc.max_used_disk_percent == 0.90


def test_projection_rises_and_clamps_to_ceiling() -> None:
    fc = compute_forecast(
        _rising(),
        horizon_seconds=30 * 86400.0,
        steps=6,
        max_used_disk_ratio=0.55,
        keep_steady_state_ratio=None,
    )
    assert fc.projected, "expected projected points"
    ys = [y for _, y in fc.projected]
    assert ys == sorted(ys)  # non-decreasing
    assert max(ys) <= 0.55 + 1e-9  # clamped at the binding ceiling


def test_binding_ceiling_is_the_lower_of_two() -> None:
    fc = compute_forecast(
        _rising(),
        horizon_seconds=60 * 86400.0,
        steps=4,
        max_used_disk_ratio=0.90,
        keep_steady_state_ratio=0.52,
    )
    assert max(y for _, y in fc.projected) <= 0.52 + 1e-9


def test_projection_bounded_to_unit_interval() -> None:
    pts = [(i * 86400.0, 0.95 + i * 0.05) for i in range(5)]  # would exceed 1.0
    fc = compute_forecast(
        pts,
        horizon_seconds=10 * 86400.0,
        steps=5,
        max_used_disk_ratio=None,
        keep_steady_state_ratio=None,
    )
    assert all(0.0 <= y <= 1.0 for _, y in fc.projected)


def test_none_ratios_are_skipped_in_fit() -> None:
    pts = [(0.0, None), (86400.0, 0.4), (2 * 86400.0, 0.42), (3 * 86400.0, 0.44)]
    fc = compute_forecast(
        pts,  # type: ignore[arg-type]
        horizon_seconds=7 * 86400.0,
        steps=3,
        max_used_disk_ratio=0.9,
        keep_steady_state_ratio=None,
    )
    assert fc.projected  # 3 valid points -> projection produced
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_forecast.py -q`
Expected: FAIL -- module missing.

- [ ] **Step 3: Implement `forecast.py`**

Create `blackvuesync/server/forecast.py`:

```python
"""server-side disk-usage forecast for the statistics page.

projects the recent disk-used ratio forward with a least-squares line, clamped
to whichever configured ceiling binds first (the hard max-used-disk cap and/or
the keep-days steady state). pure functions -- unit-testable, no flask/sqlite.
"""

from __future__ import annotations

import dataclasses

_MIN_POINTS = 3


@dataclasses.dataclass(frozen=True)
class Forecast:
    """a disk-usage projection plus the limit lines to draw."""

    projected: list[tuple[float, float]]  # (ts_seconds, disk_ratio), ascending
    max_used_disk_percent: float | None  # 0..1 ratio, or None when unset
    keep_steady_state: float | None  # 0..1 ratio, or None when not estimable


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float]:
    """returns (slope, intercept) of the least-squares line through points."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return 0.0, mean_y
    slope = numerator / denominator
    return slope, mean_y - slope * mean_x


def compute_forecast(
    disk_points: list[tuple[float, float | None]],
    *,
    horizon_seconds: float,
    steps: int,
    max_used_disk_ratio: float | None,
    keep_steady_state_ratio: float | None,
) -> Forecast:
    """projects disk usage forward over horizon_seconds in `steps` points.

    points with a None ratio are ignored. with fewer than _MIN_POINTS valid
    points the projection is empty (the chart omits it). the projection is
    clamped to [0, 1] and to the lowest configured ceiling.
    """
    clean = [(x, y) for x, y in disk_points if y is not None]
    if len(clean) < _MIN_POINTS:
        return Forecast([], max_used_disk_ratio, keep_steady_state_ratio)

    slope, intercept = _linear_fit(clean)
    ceilings = [c for c in (max_used_disk_ratio, keep_steady_state_ratio) if c is not None]
    ceiling = min(ceilings) if ceilings else None
    last_ts = clean[-1][0]

    projected: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        ts = last_ts + horizon_seconds * i / steps
        value = intercept + slope * ts
        if ceiling is not None:
            value = min(value, ceiling)
        value = max(0.0, min(1.0, value))
        projected.append((ts, value))
    return Forecast(projected, max_used_disk_ratio, keep_steady_state_ratio)


__all__ = ["Forecast", "compute_forecast"]
```

- [ ] **Step 4: Run to confirm pass**

Run: `venv/bin/pytest test/test_forecast.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add blackvuesync/server/forecast.py test/test_forecast.py
git commit -m "feat: add disk-usage forecast (linear fit, clamped to ceilings)"
```

---

### Task 4: capture path -- wire SyncMetrics into serve + thread the store

**Files:**

- Modify: `blackvuesync/server/sync_runner.py`
- Modify: `blackvuesync/server/scheduler.py`
- Modify: `blackvuesync/server/routes/api_sync.py`
- Test: `test/test_sync_runner.py` (append)

This wiring threads an optional `stats_store` to `trigger_sync`/`_do_sync`, and -- regardless of the store -- now builds, finalizes, saves, and emits a `SyncMetrics` in serve mode (closing the serve Prometheus/state gap). `sync()` already records into the metrics object when passed one.

- [ ] **Step 1: Write the failing test**

Append to `test/test_sync_runner.py` (reuse its existing imports/fakes; this test drives `_do_sync` directly with fakes so no real dashcam is needed):

```python
def test_do_sync_records_a_row_and_finalizes_metrics(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import types

    import blackvuesync.server.sync_runner as runner
    from blackvuesync.server.progress import ProgressPublisher
    from blackvuesync.server.stats_store import StatsStore

    destination = tmp_path / "rec"
    destination.mkdir()

    # fake sync internals: lock/unlock/ensure/clean no-op; sync() populates metrics
    import blackvuesync.sync as _sync

    monkeypatch.setattr(_sync, "ensure_destination", lambda d: None)
    monkeypatch.setattr(_sync, "lock", lambda d: 1)
    monkeypatch.setattr(_sync, "unlock", lambda fd: None)
    monkeypatch.setattr(_sync, "clean_destination", lambda d, g: None)

    def fake_sync(address, dest, grouping, prio, include, exclude, metrics=None, publisher=None, job_id=None):  # type: ignore[no-untyped-def]
        if metrics is not None:
            metrics.record_file_download(123)
            metrics.record_destination_disk_usage(50, 100)

    monkeypatch.setattr(_sync, "sync", fake_sync)

    settings = types.SimpleNamespace(
        connection=types.SimpleNamespace(address="1.2.3.4", timeout_seconds=10.0),
        system=types.SimpleNamespace(destination=str(destination), dry_run=False),
        sync=types.SimpleNamespace(grouping="none", priority="date", include=(), exclude=()),
        retention=types.SimpleNamespace(max_used_disk_percent=90),
        metrics=types.SimpleNamespace(
            file=None, pushgateway_url=None, job="blackvuesync", instance=None,
            state_file=str(tmp_path / "metrics-state.json"),
        ),
        stats=types.SimpleNamespace(retention_days=365),
    )
    store = StatsStore(str(tmp_path / "stats.db"))
    pub = ProgressPublisher()

    runner._do_sync(settings, pub, job_id="abc", stats_store=store)

    rows = store.query()
    assert len(rows) == 1
    assert rows[0].files == 1
    assert rows[0].bytes == 123
    assert rows[0].disk_used_ratio == 0.5
    assert rows[0].success == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_sync_runner.py -q -k records_a_row`
Expected: FAIL -- `_do_sync()` has no `stats_store` kwarg / does not build metrics.

- [ ] **Step 3: Update `sync_runner.py`**

Add imports near the top (after the existing imports):

```python
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackvuesync.server.stats_store import StatsStore
```

Change `trigger_sync` to accept and forward the store. Replace its signature and the `_run`/`_do_sync` call:

```python
def trigger_sync(
    settings: Any,
    publisher: ProgressPublisher,
    stats_store: "StatsStore | None" = None,
) -> dict[str, str]:
```

and inside `_run`:

```python
        try:
            _do_sync(settings, publisher, job_id=job_id, stats_store=stats_store)
        finally:
            with contextlib.suppress(Exception):
                _sync_lock.release()
```

Replace `_do_sync`'s signature with:

```python
def _do_sync(  # pylint: disable=too-many-locals,too-many-statements
    settings: Any,
    publisher: ProgressPublisher,
    *,
    job_id: str,
    stats_store: "StatsStore | None" = None,
) -> None:
```

Add the metrics import to the existing deferred-import block:

```python
    from blackvuesync.metrics import (
        SyncMetrics,
        count_failed_marker_files,
        emit_metrics,
        load_metrics_state,
        save_metrics_state,
    )
```

Build the metrics object before the `lock(...)` call (after the `_sync.*` assignments, before `lf_fd = lock(destination)`):

```python
        state_file = settings.metrics.state_file
        metrics = SyncMetrics(
            run_start_monotonic=time.perf_counter(),
            run_start_timestamp=time.time(),
            dry_run=settings.system.dry_run,
            metrics_job=settings.metrics.job,
            metrics_instance=settings.metrics.instance or address,
            last_successful_file_pull_timestamp_seconds=load_metrics_state(state_file),
        )
        sync_success = False
```

Pass `metrics=metrics` to `sync(...)` and set `sync_success = True` after it returns:

```python
        try:
            sync(
                address,
                destination,
                grouping,
                priority,
                include,
                exclude,
                metrics=metrics,
                publisher=publisher,
                job_id=job_id,
            )
            sync_success = True
        finally:
            clean_destination(destination, grouping)
```

In the outer `finally` (after the `unlock`), finalize + persist + emit + record:

```python
    finally:
        if lf_fd is not None:
            with contextlib.suppress(Exception):
                unlock(lf_fd)
        with contextlib.suppress(Exception):
            metrics.failed_marker_files = count_failed_marker_files(destination)
        metrics.finalize(0 if sync_success else 1, sync_success)
        with contextlib.suppress(Exception):
            save_metrics_state(state_file, metrics)
        with contextlib.suppress(Exception):
            emit_metrics(
                metrics,
                settings.metrics.file,
                settings.metrics.pushgateway_url,
                settings.connection.timeout_seconds,
            )
        if stats_store is not None:
            with contextlib.suppress(Exception):
                stats_store.record_run(metrics)
                stats_store.prune(settings.stats.retention_days)
```

> `metrics` and `sync_success` are defined before `lock()`, so they exist in the `finally` even if `lock()` raises. The `contextlib.suppress(Exception)` guards keep metrics/stats bookkeeping from ever masking a sync error or crashing the daemon thread.

- [ ] **Step 4: Thread the store through the scheduler**

In `blackvuesync/server/scheduler.py`, update `_scheduled_run` and `init_scheduler` to carry an optional `stats_store`. Read the file; change `_scheduled_run(store, publisher)` to also accept `stats_store` and pass it to `trigger_sync`:

```python
def _scheduled_run(
    store: SettingsStore,
    publisher: ProgressPublisher,
    stats_store: "StatsStore | None" = None,
) -> None:
    ...
    result = trigger_sync(settings, publisher, stats_store)
```

Add the matching `TYPE_CHECKING` import of `StatsStore` at the top, and give `init_scheduler` a `stats_store: "StatsStore | None" = None` parameter that it binds into the scheduled job (it already passes `store, publisher` as the job args -- add `stats_store` to that args tuple).

- [ ] **Step 5: Pass the store from the on-demand route**

In `blackvuesync/server/routes/api_sync.py` `trigger_now()`, pass the app store:

```python
    stats_store = getattr(current_app, "stats_store", None)
    result = trigger_sync(settings, pub, stats_store)
```

- [ ] **Step 6: Run to confirm pass**

Run: `venv/bin/pytest test/test_sync_runner.py -q`
Expected: PASS (incl. the new test). Then:
Run: `venv/bin/pytest test/test_routes_api_sync.py test/test_scheduler.py -q`
Expected: PASS (no regression; `stats_store` defaults to None where not supplied).

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/server/sync_runner.py blackvuesync/server/scheduler.py blackvuesync/server/routes/api_sync.py test/test_sync_runner.py
git commit -m "feat: capture per-run SyncMetrics in serve mode into the stats store"
```

---

### Task 5: `/api/stats/series` endpoint + app wiring

**Files:**

- Create: `blackvuesync/server/routes/api_stats.py`
- Modify: `blackvuesync/server/__init__.py` (`create_app` gains `stats_store`; attaches `app.stats_store`; registers `api_stats_bp`)
- Modify: `blackvuesync/__main__.py` (construct `StatsStore`; pass to `init_scheduler` + `create_app`)
- Test: `test/test_routes_api_stats.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_routes_api_stats.py`:

```python
"""flask test-client tests for /api/stats/series and the /stats page."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.metrics import SyncMetrics
from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.server.stats_store import StatsStore
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def app_and_client(tmp_path: Path):  # type: ignore[no-untyped-def]
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
    stats = StatsStore(str(tmp_path / "stats.db"))
    app = create_app(store, testing=True, stats_store=stats)
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = "admin"
        yield app, client, stats


def _seed(stats: StatsStore, n: int = 5) -> None:
    now = time.time()
    for i in range(n):
        m = SyncMetrics(run_start_monotonic=0.0, run_start_timestamp=now)
        m.last_run_timestamp_seconds = now - (n - i) * 3600
        m.last_run_success = 1
        m.last_run_exit_code = 0
        m.run_duration_seconds = 2.0
        m.files_downloaded_last_run = i
        m.bytes_downloaded_last_run = i * 1000
        m.destination_disk_used_ratio = 0.40 + i * 0.01
        stats.record_run(m)


def test_series_requires_login(app_and_client: Any) -> None:
    app, _client, _stats = app_and_client
    resp = app.test_client().get("/api/stats/series?range=7d")
    assert resp.status_code in (302, 401)


def test_series_returns_summary_series_forecast(app_and_client: Any) -> None:
    app, client, stats = app_and_client
    _seed(stats, 5)
    resp = client.get("/api/stats/series?range=all")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["range"] == "all"
    assert body["summary"]["runs"] == 5
    assert len(body["series"]["points"]) == 5
    assert "forecast" in body
    # max_used_disk_percent comes from retention default (90 -> 0.9)
    assert body["forecast"]["limits"]["max_used_disk_percent"] == pytest.approx(0.9)


def test_series_empty_store_ok(app_and_client: Any) -> None:
    app, client, _stats = app_and_client
    resp = client.get("/api/stats/series?range=24h")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["summary"]["runs"] == 0
    assert body["series"]["points"] == []
    assert body["forecast"]["projected"] == []


def test_series_rejects_unknown_range(app_and_client: Any) -> None:
    app, client, _stats = app_and_client
    resp = client.get("/api/stats/series?range=bogus")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run to confirm failure**

Run: `venv/bin/pytest test/test_routes_api_stats.py -q`
Expected: FAIL -- `create_app()` has no `stats_store` kwarg / route missing.

- [ ] **Step 3: Create `api_stats.py`**

Create `blackvuesync/server/routes/api_stats.py`:

```python
"""api stats route: GET /api/stats/series (summary + series + disk forecast)."""

from __future__ import annotations

import json
import re
import time

from flask import Blueprint, Response, current_app, request

from blackvuesync.server.auth import login_required
from blackvuesync.server.forecast import compute_forecast
from blackvuesync.server.stats_store import RunRow, StatsStore

api_stats_bp = Blueprint("api_stats_bp", __name__, url_prefix="/api/stats")

_MIME_JSON = "application/json"
_SECONDS_PER_DAY = 86400.0

# range token -> lookback window in seconds (None = all history)
_RANGES: dict[str, float | None] = {
    "24h": 1 * _SECONDS_PER_DAY,
    "7d": 7 * _SECONDS_PER_DAY,
    "30d": 30 * _SECONDS_PER_DAY,
    "all": None,
}

_DURATION_RE = re.compile(r"^(\d+)([shdw]?)$")
_DURATION_DAYS = {"s": 1 / 86400.0, "h": 1 / 24.0, "d": 1.0, "w": 7.0, "": 1.0}


def _store() -> StatsStore:
    """returns the app-level stats store."""
    store: StatsStore = current_app.stats_store  # type: ignore[attr-defined]
    return store


def _keep_days(keep: str) -> float | None:
    """parses a retention.keep duration (e.g. '2w') to days; None if unparseable."""
    match = _DURATION_RE.match(keep or "")
    if not match:
        return None
    return int(match.group(1)) * _DURATION_DAYS[match.group(2)]


def _point(row: RunRow) -> dict[str, object]:
    """serializes one run row for the series."""
    return {
        "ts": row.ts_seconds,
        "bytes": row.bytes,
        "files": row.files,
        "duration": row.duration_seconds,
        "disk": row.disk_used_ratio,
        "success": row.success,
        "failures": row.failures,
    }


def _summary(rows: list[RunRow]) -> dict[str, object]:
    """computes the summary tiles over non-dry-run rows where it matters."""
    runs = len(rows)
    total_bytes = sum(r.bytes for r in rows if not r.dry_run)
    avg_duration = (sum(r.duration_seconds for r in rows) / runs) if runs else 0.0
    successes = sum(1 for r in rows if r.success)
    success_rate = (successes / runs) if runs else 0.0
    return {
        "runs": runs,
        "bytes": total_bytes,
        "avg_duration_seconds": avg_duration,
        "success_rate": success_rate,
    }


def _keep_steady_state(rows: list[RunRow], keep_days: float | None) -> float | None:
    """estimates the retention plateau as the mean disk ratio over the last
    keep_days, when that much history exists; otherwise None."""
    if keep_days is None or not rows:
        return None
    cutoff = rows[-1].ts_seconds - keep_days * _SECONDS_PER_DAY
    if rows[0].ts_seconds > cutoff:
        return None  # not enough history to cover a full retention window
    recent = [r.disk_used_ratio for r in rows if r.ts_seconds >= cutoff and r.disk_used_ratio is not None]
    if not recent:
        return None
    return sum(recent) / len(recent)


@api_stats_bp.route("/series", methods=["GET"])
@login_required
def series() -> Response:
    """returns {range, summary, series, forecast} for the requested window."""
    range_token = request.args.get("range", "7d")
    if range_token not in _RANGES:
        body = json.dumps(
            {"error": "unknown range", "code": "BAD_RANGE", "details": {"range": range_token}}
        )
        return Response(body, status=400, mimetype=_MIME_JSON)

    window = _RANGES[range_token]
    since = (time.time() - window) if window is not None else None
    rows = _store().query(since_ts=since)

    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    max_used_ratio = settings.retention.max_used_disk_percent / 100.0
    keep_days = _keep_days(settings.retention.keep)
    steady = _keep_steady_state(rows, keep_days)

    disk_points = [(r.ts_seconds, r.disk_used_ratio) for r in rows]
    forecast = compute_forecast(
        disk_points,
        horizon_seconds=(window or 7 * _SECONDS_PER_DAY),
        steps=12,
        max_used_disk_ratio=max_used_ratio,
        keep_steady_state_ratio=steady,
    )

    body = json.dumps(
        {
            "range": range_token,
            "summary": _summary(rows),
            "series": {"points": [_point(r) for r in rows]},
            "forecast": {
                "projected": [{"ts": ts, "disk": disk} for ts, disk in forecast.projected],
                "limits": {
                    "max_used_disk_percent": forecast.max_used_disk_percent,
                    "keep_steady_state": forecast.keep_steady_state,
                },
            },
        },
        default=str,
    )
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_stats_bp"]
```

- [ ] **Step 4: Wire `create_app`**

In `blackvuesync/server/__init__.py`, add the import near the other server imports:

```python
from blackvuesync.server.stats_store import StatsStore
```

Add a parameter to `create_app` (after `log_file_path`):

```python
    stats_store: Optional[StatsStore] = None,
```

After the `app.log_file_path = log_file_path` line, add:

```python
    # attaches or creates the stats store; defaults to a temp-path store so
    # route tests have a live store even when serve mode did not supply one.
    app.stats_store = stats_store or StatsStore(":memory:")  # type: ignore[attr-defined]
```

> Note: `StatsStore` supports `":memory:"` via a persistent connection, so the default in-memory store is a working empty store for tests / non-serve reads. Tests that need data pass a real file-backed `StatsStore`.

In the deferred blueprint-import block, add after `api_logs_bp`:

```python
    from blackvuesync.server.routes.api_stats import api_stats_bp
```

and register it after `app.register_blueprint(api_logs_bp)`:

```python
    app.register_blueprint(api_stats_bp)
```

- [ ] **Step 5: Construct + thread the store in `cmd_serve`**

In `blackvuesync/__main__.py` `cmd_serve`, add `StatsStore` to the deferred server-import block:

```python
    from blackvuesync.server.stats_store import StatsStore
```

After the log-handler wiring and before `publisher = ProgressPublisher()`, construct the store at `/config/stats.db` (sibling of the settings file):

```python
    stats_store = StatsStore(str(config_path.parent / "stats.db"))
```

Pass it to `create_app` and `init_scheduler`:

```python
    app = create_app(
        store,
        progress_publisher=publisher,
        log_buffer=log_buffer,
        log_file_path=log_file_path,
        stats_store=stats_store,
    )
    ...
    scheduler = init_scheduler(store, publisher, stats_store)
```

- [ ] **Step 6: Run to confirm pass**

Run: `venv/bin/pytest test/test_routes_api_stats.py -q -k "series or requires_login or empty or range"`
Expected: PASS (the 4 series tests). The `/stats` page test is added in Task 6.
Run: `venv/bin/pytest test/ -q -m 'not e2e'`
Expected: PASS (no regression).

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/server/routes/api_stats.py blackvuesync/server/__init__.py blackvuesync/__main__.py test/test_routes_api_stats.py
git commit -m "feat: add /api/stats/series endpoint and wire the stats store"
```

---

### Task 6: `/stats` page route + template

**Files:**

- Modify: `blackvuesync/server/routes/ui.py` (`stats()` view)
- Create: `blackvuesync/server/templates/stats.html`
- Delete: `blackvuesync/server/templates/_placeholders/stats.html`
- Test: `test/test_routes_api_stats.py` (append page tests)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_routes_api_stats.py`:

```python
def test_stats_page_renders(app_and_client: Any) -> None:
    app, client, _stats = app_and_client
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert b"js/stats.js" in resp.data
    assert b"js/chart.umd.min.js" in resp.data
    assert b'data-range' in resp.data  # range selector present


def test_stats_page_has_noscript_fallback(app_and_client: Any) -> None:
    app, client, stats = app_and_client
    _seed(stats, 3)
    resp = client.get("/stats")
    assert b"<noscript>" in resp.data
```

- [ ] **Step 2: Confirm failure**

Run: `venv/bin/pytest test/test_routes_api_stats.py -q -k "stats_page or noscript"`
Expected: FAIL (placeholder lacks `js/stats.js`).

- [ ] **Step 3: Update the `/stats` route**

In `blackvuesync/server/routes/ui.py`, replace the placeholder `stats()` view:

```python
@bp.route("/stats", methods=["GET"])
@login_required
def stats() -> str:
    """renders the statistics page; charts hydrate client-side from the API."""
    store = current_app.stats_store  # type: ignore[attr-defined]
    recent = store.query()[-20:]  # newest 20 for the no-JS fallback table
    return render_template(
        "stats.html",
        version=__version__,
        page="stats",
        recent=list(reversed(recent)),
    )
```

(`current_app` is already imported in `ui.py`.)

- [ ] **Step 4: Create `stats.html`**

Create `blackvuesync/server/templates/stats.html`:

```html
{% extends "base.html" %}
{% block title %}Stats -- BlackVue Sync{% endblock %}
{% block footer_version %}{{ version }}{% endblock %}

{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/stats.css') }}">
{% endblock %}

{% block content %}
<div class="stats-page" x-data="statsPage" data-initial-range="7d">
  <div class="stats-toolbar" role="group" aria-label="Time range">
    <button type="button" class="stats-range-btn active" data-range="24h" @click="setRange">24h</button>
    <button type="button" class="stats-range-btn" data-range="7d" @click="setRange">7d</button>
    <button type="button" class="stats-range-btn" data-range="30d" @click="setRange">30d</button>
    <button type="button" class="stats-range-btn" data-range="all" @click="setRange">All</button>
  </div>

  <div class="stats-summary" data-summary>
    <div class="stat-tile"><div class="stat-label">Runs</div><div class="stat-value" data-summary-runs>--</div></div>
    <div class="stat-tile"><div class="stat-label">Data pulled</div><div class="stat-value" data-summary-bytes>--</div></div>
    <div class="stat-tile"><div class="stat-label">Avg duration</div><div class="stat-value" data-summary-duration>--</div></div>
    <div class="stat-tile"><div class="stat-label">Success rate</div><div class="stat-value" data-summary-success>--</div></div>
  </div>

  <div class="stats-primary card">
    <h3 class="chart-title">Destination disk used -- actual &amp; projected</h3>
    <canvas data-chart="disk" height="120"></canvas>
  </div>

  <div class="stats-grid">
    <div class="card"><h3 class="chart-title">Data downloaded / run</h3><canvas data-chart="bytes" height="90"></canvas></div>
    <div class="card"><h3 class="chart-title">Files downloaded / run</h3><canvas data-chart="files" height="90"></canvas></div>
    <div class="card"><h3 class="chart-title">Run duration (s)</h3><canvas data-chart="duration" height="90"></canvas></div>
    <div class="card"><h3 class="chart-title">Failures by reason</h3><canvas data-chart="failures" height="90"></canvas></div>
  </div>

  <noscript>
    <p class="stats-note">JavaScript is required for charts. Most recent runs:</p>
    <table class="stats-fallback">
      <thead><tr><th>Time</th><th>OK</th><th>Files</th><th>Bytes</th><th>Disk</th></tr></thead>
      <tbody>
        {% for r in recent %}
        <tr>
          <td>{{ r.ts_seconds | int }}</td>
          <td>{{ "yes" if r.success else "no" }}</td>
          <td>{{ r.files }}</td>
          <td>{{ r.bytes }}</td>
          <td>{{ "%.0f%%"|format((r.disk_used_ratio or 0) * 100) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </noscript>
</div>
{% endblock %}

{% block extra_js %}
  <script src="{{ url_for('static', filename='js/chart.umd.min.js') }}"></script>
  <script src="{{ url_for('static', filename='js/stats.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 5: Delete the placeholder**

```bash
git rm blackvuesync/server/templates/_placeholders/stats.html
```

- [ ] **Step 6: Run to confirm pass**

Run: `venv/bin/pytest test/test_routes_api_stats.py test/test_routes_ui.py -q`
Expected: PASS (the page tests + the existing parametrized `/stats` 200 test). `js/stats.js` and `js/chart.umd.min.js` are referenced by URL even though the files arrive in Task 7 -- Flask resolves the static URL regardless.

- [ ] **Step 7: Commit**

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/stats.html
git commit -m "feat: render the real /stats page with server-side fallback"
```

---

### Task 7: vendor Chart.js + stats.js + stats.css + Sonar exclusion

**Files:**

- Create: `blackvuesync/server/static/js/chart.umd.min.js` (vendored)
- Create: `blackvuesync/server/static/js/stats.js`
- Create: `blackvuesync/server/static/css/stats.css`
- Modify: `sonar-project.properties`

- [ ] **Step 1: Vendor Chart.js v4 (pinned)**

```bash
curl -fsSL https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js \
  -o blackvuesync/server/static/js/chart.umd.min.js
test -s blackvuesync/server/static/js/chart.umd.min.js && head -c 80 blackvuesync/server/static/js/chart.umd.min.js
```

Expected: a non-empty file beginning with the Chart.js v4 UMD banner. If the download is blocked by the sandbox/network, STOP and ask the human to place `chart.umd.min.js` (Chart.js v4.4.x UMD, minified) at that path -- do not hand-write or stub it.

- [ ] **Step 2: Exclude the vendored file from Sonar analysis**

In `sonar-project.properties`, change the exclusions line:

```properties
sonar.exclusions=venv/**,build/**,dist/**,.venv/**,**/chart.umd.min.js
```

- [ ] **Step 3: Create `stats.js`**

Create `blackvuesync/server/static/js/stats.js`:

```javascript
// stats.js: Alpine.js (csp build) component for the /stats page. fetches
// /api/stats/series on load and on range change, then renders vendored
// Chart.js charts (incl. the disk actual + projected + limit datasets).
// no eval / inline expressions -- csp-clean.

const RANGE_DEFAULT = "7d";

function fmtBytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(n) || 0;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return value.toFixed(unit === 0 ? 0 : 1) + " " + units[unit];
}

function tsLabel(ts) {
  return new Date(Number(ts) * 1000).toLocaleString();
}

document.addEventListener("alpine:init", () => {
  Alpine.data("statsPage", () => ({
    range: RANGE_DEFAULT,
    _charts: {},

    init() {
      this.range = this.$el.dataset.initialRange || RANGE_DEFAULT;
      this.load();
    },

    setRange(ev) {
      this.range = ev.currentTarget.dataset.range;
      this.$root.querySelectorAll("[data-range]").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.range === this.range);
      });
      this.load();
    },

    async load() {
      const data = await this.fetchSeries(this.range);
      if (!data) return;
      this.renderSummary(data.summary);
      this.renderCharts(data);
    },

    async fetchSeries(range) {
      try {
        const resp = await fetch("/api/stats/series?range=" + encodeURIComponent(range), {
          headers: { Accept: "application/json" },
        });
        if (!resp.ok) return null;
        return await resp.json();
      } catch {
        /* network error; charts keep their last state */
        return null;
      }
    },

    renderSummary(summary) {
      this.setText("[data-summary-runs]", String(summary.runs));
      this.setText("[data-summary-bytes]", fmtBytes(summary.bytes));
      this.setText("[data-summary-duration]", (summary.avg_duration_seconds || 0).toFixed(1) + " s");
      this.setText("[data-summary-success]", (summary.success_rate * 100).toFixed(1) + "%");
    },

    setText(selector, text) {
      const el = this.$root.querySelector(selector);
      if (el) el.textContent = text;
    },

    renderCharts(data) {
      const points = data.series.points;
      const labels = points.map((p) => tsLabel(p.ts));
      this.drawLine("bytes", labels, points.map((p) => p.bytes), "Bytes");
      this.drawBar("files", labels, points.map((p) => p.files), "Files");
      this.drawLine("duration", labels, points.map((p) => p.duration), "Seconds");
      this.drawFailures("failures", labels, points);
      this.drawDisk("disk", points, data.forecast);
    },

    canvas(name) {
      return this.$root.querySelector('[data-chart="' + name + '"]');
    },

    upsert(name, config) {
      if (this._charts[name]) this._charts[name].destroy();
      this._charts[name] = new window.Chart(this.canvas(name), config);
    },

    drawLine(name, labels, values, label) {
      this.upsert(name, {
        type: "line",
        data: { labels: labels, datasets: [{ label: label, data: values, tension: 0.3 }] },
        options: { responsive: true, plugins: { legend: { display: false } } },
      });
    },

    drawBar(name, labels, values, label) {
      this.upsert(name, {
        type: "bar",
        data: { labels: labels, datasets: [{ label: label, data: values }] },
        options: { responsive: true, plugins: { legend: { display: false } } },
      });
    },

    drawFailures(name, labels, points) {
      const reasons = ["http", "network", "timeout", "disk", "unknown"];
      const datasets = reasons.map((reason) => ({
        label: reason,
        data: points.map((p) => (p.failures && p.failures[reason]) || 0),
      }));
      this.upsert(name, {
        type: "bar",
        data: { labels: labels, datasets: datasets },
        options: { responsive: true, scales: { x: { stacked: true }, y: { stacked: true } } },
      });
    },

    drawDisk(name, points, forecast) {
      const actual = points.map((p) => ({ x: tsLabel(p.ts), y: p.disk }));
      const projected = forecast.projected.map((p) => ({ x: tsLabel(p.ts), y: p.disk }));
      const datasets = [
        { label: "actual", data: actual, borderColor: "#0a84ff", tension: 0.3 },
        { label: "projected", data: projected, borderColor: "#5e5ce6", borderDash: [6, 5] },
      ];
      const cap = forecast.limits.max_used_disk_percent;
      const steady = forecast.limits.keep_steady_state;
      if (cap !== null && cap !== undefined) {
        datasets.push(this.limitDataset("max cap", cap, points, forecast));
      }
      if (steady !== null && steady !== undefined) {
        datasets.push(this.limitDataset("retention", steady, points, forecast));
      }
      this.upsert(name, {
        type: "line",
        data: { datasets: datasets },
        options: { responsive: true, parsing: false, scales: { x: { type: "category" } } },
      });
    },

    limitDataset(label, ratio, points, forecast) {
      const xs = points
        .map((p) => tsLabel(p.ts))
        .concat(forecast.projected.map((p) => tsLabel(p.ts)));
      return {
        label: label,
        data: xs.map((x) => ({ x: x, y: ratio })),
        borderColor: "#ff453a",
        borderDash: [4, 4],
        pointRadius: 0,
      };
    },
  }));
});
```

> Notes for SonarCloud cleanliness: no nested ternary; `Number.parseInt` is not used here (we use `Number(...)`); catch blocks carry a comment; optional access uses explicit `!== null && !== undefined` guards. The disk chart uses Chart.js category parsing for simplicity (labels shared across actual/projected/limit datasets).

- [ ] **Step 4: Create `stats.css`**

Create `blackvuesync/server/static/css/stats.css`:

```css
.stats-page {
  max-width: 1100px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-4);
}

.stats-toolbar {
  display: inline-flex;
  gap: var(--space-1);
  margin: 0 0 var(--space-4);
  padding: 0;
  border: 0;
}

.stats-range-btn {
  border: 1px solid var(--color-separator, #d1d1d6);
  background: var(--color-surface, #fff);
  color: var(--color-label, #1d1d1f);
  border-radius: var(--radius-md, 8px);
  padding: 4px 12px;
  font-size: 13px;
  cursor: pointer;
}

.stats-range-btn.active {
  background: var(--color-accent, #0071e3);
  color: #fff;
  border-color: var(--color-accent, #0071e3);
}

.stats-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.stat-tile {
  background: var(--color-surface, #fff);
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-subtle, 0 1px 2px rgba(0, 0, 0, 0.08));
  padding: var(--space-3) var(--space-4);
}

.stat-label {
  font-size: 11px;
  text-transform: uppercase;
  color: var(--color-label-secondary, #6e6e73);
}

.stat-value {
  font-size: 20px;
  font-weight: 600;
}

.stats-primary {
  margin-bottom: var(--space-3);
  padding: var(--space-4);
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

.chart-title {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 var(--space-2);
}

.stats-fallback {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.stats-fallback th,
.stats-fallback td {
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid var(--color-separator, #d1d1d6);
}

.stats-note {
  color: var(--color-label-secondary, #6e6e73);
}
```

- [ ] **Step 5: Smoke + commit**

Run: `venv/bin/pytest test/test_routes_api_stats.py -q`
Expected: PASS (page references resolve). Then commit:

```bash
git add blackvuesync/server/static/js/chart.umd.min.js blackvuesync/server/static/js/stats.js blackvuesync/server/static/css/stats.css sonar-project.properties
git commit -m "feat: add stats viewer client (Chart.js, stats.js, stats.css)"
```

---

### Task 8: end-to-end Playwright smoke

**Files:**

- Create: `test/e2e/test_stats_page.py`

- [ ] **Step 1: Write the e2e test**

Read `test/e2e/conftest.py` (the `live_server` fixture) and an existing e2e test (e.g. `test/e2e/test_logs_live.py`) for the login pattern; the fixture's app must be created with a real stats store. The `live_server` fixture calls `create_app(store, testing=False)` -- so the app's default stats store is the `":memory:"` one, which won't persist across the fetch. To seed data the test injects rows via `live_server.app.stats_store` ONLY if that store is file-backed. Since the default is in-memory-per-connection, this e2e asserts the page and an empty-state render rather than seeded charts:

```python
"""playwright smoke for the /stats page."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _login(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')
    expect(page).not_to_have_url(f"{base_url}/login")


def test_stats_page_loads_and_switches_range(live_server, page: Page) -> None:  # type: ignore[no-untyped-def]
    base = live_server.url
    _login(page, base)
    page.goto(f"{base}/stats")
    expect(page.locator(".stats-page")).to_be_visible()
    # the four range buttons exist and clicking one marks it active
    expect(page.locator('.stats-range-btn[data-range="30d"]')).to_be_visible()
    page.click('.stats-range-btn[data-range="30d"]')
    expect(page.locator('.stats-range-btn[data-range="30d"]')).to_have_class(lambda c: "active" in c)
    # the disk canvas is present and Chart.js initialized without a console error
    expect(page.locator('[data-chart="disk"]')).to_be_visible()
```

> If `expect(...).to_have_class(lambda ...)` is unsupported in the installed Playwright, assert via `page.locator('.stats-range-btn.active[data-range="30d"]')` instead.

To make the e2e exercise real charts, update the `live_server` fixture in `test/e2e/conftest.py` to construct a file-backed stats store and pass it to `create_app`, then seed a few rows before `yield`. If you change the shared fixture, keep existing e2e tests passing. (Minimal acceptable scope: the smoke above, which validates load + range switch + canvas presence.)

- [ ] **Step 2: Run it**

Run: `venv/bin/pytest test/e2e/test_stats_page.py -m e2e -v`
Expected: PASS (chromium installed locally). If the active-class assertion API mismatches, switch to the `.active` selector form noted above.

- [ ] **Step 3: Commit**

```bash
git add test/e2e/test_stats_page.py
git commit -m "test: add e2e smoke for the /stats page"
```

---

### Task 9: docs, version, mypy, full verification

**Files:**

- Modify: `docs/api.md`, `pyproject.toml`

- [ ] **Step 1: Document the endpoint**

In `docs/api.md`, add (matching the file's heading/table style):

```markdown
## Statistics API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/stats/series?range=24h\|7d\|30d\|all` | JSON `{range, summary, series, forecast}` |

Login required. `summary` = `{runs, bytes, avg_duration_seconds, success_rate}`;
`series.points[]` = `{ts, bytes, files, duration, disk, success, failures{reason}}`;
`forecast` = `{projected[{ts, disk}], limits{max_used_disk_percent, keep_steady_state}}`.
Per-run rows are captured in serve mode and stored in the SQLite stats DB
(`/config/stats.db`); `stats.retention_days` prunes old rows.
```

- [ ] **Step 2: Bump version + mypy overrides**

In `pyproject.toml`, bump `version` to the next alpha for this sub-project (e.g. `2.6.0a0` -> `2.7.0a0`; read the current value and bump the minor, reset to `a0`). Add `test.test_stats_store`, `test.test_forecast`, and `test.test_routes_api_stats` to the mypy per-module override list following the existing pattern (only those that mypy flags -- run `venv/bin/pre-commit run mypy --all-files` and add the ones it complains about).

- [ ] **Step 3: Full verification**

```bash
venv/bin/pytest test/ -q -m 'not e2e'
```

Expected: PASS (full unit + route suite).

```bash
venv/bin/pytest test/e2e/test_stats_page.py -m e2e -q
```

Expected: PASS.

```bash
venv/bin/pre-commit run --all-files
```

Expected: all hooks pass. If pylint reports R0801 duplicate-code between `api_stats.py` and another route's response setup, prefer the existing `blackvuesync/server/sse.py`-style shared helper only if it is plain JSON (it is not SSE) -- otherwise the small `Response(body, status=..., mimetype=_MIME_JSON)` pattern is already shared via the `_MIME_JSON` constant and is below the duplication threshold. If a hook reformats a file, re-stage and re-run; never `--no-verify`.

- [ ] **Step 4: Commit**

```bash
git add docs/api.md pyproject.toml
git commit -m "docs: document stats API; bump version for sub-project #5"
```

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin sub-project-5-statistics
```

Open a PR to `main` via the REST API (the GraphQL-prone `gh pr create` intermittently fails on this fork):

```bash
gh api repos/tekgnosis-net/blackvuesync/pulls -X POST -f title="Sub-Project #5: Statistics page" -f head="sub-project-5-statistics" -f base="main" -f body="<summary>"
```

Wait for the 5 required checks (pre-commit, unit-tests, integration-tests, test, SonarCloud Code Analysis) + the `e2e-tests` job. After CI, query the SonarCloud issues API directly and require 0 findings before merging:

```bash
curl -s "https://sonarcloud.io/api/issues/search?componentKeys=tekgnosis-net_blackvuesync&pullRequest=<N>&resolved=false&ps=100"
```

Merge via squash (linear history).

---

## Self-Review

**1. Spec coverage:**

- SQLite store + one-row-per-run + record/query/prune + WAL + `/config/stats.db` -> Task 1, Task 5 (construction).
- `stats` settings section + `retention_days` housekeeping + Settings UI pane -> Task 2; prune invoked in Task 4.
- Wire `SyncMetrics` into serve + finalize + emit/save (close the gap) + record/prune -> Task 4.
- Server-side forecast (linear fit, clamp to both conditional ceilings) -> Task 3; ceilings computed in Task 5 (`max_used_disk_percent` always; `keep_steady_state` conditional).
- `/api/stats/series` (range, summary, series, forecast) -> Task 5.
- `/stats` page: server-rendered summary + range selector + `<noscript>` fallback + canvases -> Task 6; Chart.js client + vendored lib + css -> Task 7.
- Sonar exclusion of the vendored lib -> Task 7. Docs + version + mypy -> Task 9. Delete placeholder -> Task 6.
- CSP unchanged (confirmed in spec) -> no task needed.

**2. Placeholder scan:** none -- every code step has complete code; commands have expected output.

**3. Type/name consistency:** `RunRow` fields match the `runs` schema columns and `StatsStore._to_row`. `SyncMetrics` field names used in `record_run`/the capture path match `metrics.py` (`last_run_timestamp_seconds`, `files_downloaded_last_run`, `bytes_downloaded_last_run`, `destination_disk_used_ratio`, `file_download_failures_last_run`, `last_run_failures`, `failed_marker_files`, `dry_run`). `compute_forecast`'s signature is identical between Task 3 (definition) and Task 5 (call). `app.stats_store` is attached in Task 5's `create_app` change and read in `api_stats.py`, `ui.py`, and `api_sync.py`. `trigger_sync`/`_do_sync`/`init_scheduler`/`_scheduled_run` all gain a matching optional `stats_store` parameter. The `/api/stats/series` JSON keys (`summary`, `series.points`, `forecast.projected`, `forecast.limits`) match what `stats.js` consumes.

**End of plan.**
