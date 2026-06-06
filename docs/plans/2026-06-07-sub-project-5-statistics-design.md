# Sub-Project #5 -- Statistics Page -- Design Spec

**Date:** 2026-06-07
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design approved by user (pending spec review); awaiting implementation plan (writing-plans)
**Series:** fifth web-app sub-project, after #1 Web Foundation, #2 Dashboard, #3 Settings UI, #4 Log viewer (all merged).

---

## Context

The `/stats` route is a placeholder ("coming in sub-project #5"). This sub-project turns it into a statistics page visualizing BlackVue Sync metrics over time, backed by a new SQLite time-series store.

**Scope-defining finding (verified against the codebase):** serve mode -- the scheduler-driven sync the Docker deployment actually runs -- currently produces **no per-run metrics at all**. `SyncMetrics`, the Prometheus emission (`emit_metrics`), and the metrics-state save only happen in the legacy CLI `sync` path; `sync_runner._do_sync()` calls `sync.sync(...)` without a metrics object. So "metrics over time" cannot read existing history -- there is none, and nothing is capturing it. #5 must **capture** a per-run record at the end of each serve sync.

`sync.sync()` already accepts an optional `metrics` object and instruments downloads when given one (the CLI path uses it). Serve mode simply never passed one. So capture is mostly a matter of constructing and finalizing a `SyncMetrics` in the serve sync path.

`SyncMetrics` per-run fields available to chart: `last_run_timestamp_seconds`, `last_run_success`, `last_run_exit_code`, `run_duration_seconds`, `files_downloaded_last_run`, `bytes_downloaded_last_run`, `dashcam_recordings_seen`, `recordings_selected`, `destination_disk_used_ratio`, `failed_marker_files`, `file_download_failures_last_run{reason}`, `last_run_failures{reason}`, `dry_run`.

---

## Decisions (resolved during brainstorming)

1. **Data source:** wire a full `SyncMetrics` into the serve sync path and feed the time-series store from the finalized object. As a bonus this closes the serve-mode Prometheus + metrics-state gap (serve will now emit metrics like the CLI does).
2. **Chart rendering:** vendored **Chart.js v4** (canvas; dependency-free, CSP-clean). Client-rendered with a server-rendered fallback for progressive enhancement.
3. **Chart set + layout** (validated via visual companion): a time-range selector (24h / 7d / 30d / All), a summary row (runs, data pulled, avg duration, success rate), a **disk-usage forecast as the full-width primary chart**, and a 2-column grid of: data downloaded/run, files/run, run duration, failures-by-reason (stacked).
4. **Disk forecast:** show **actual** (solid) up to "now", then **projected** (dashed, distinct colour) that asymptotes toward a ceiling rather than running to 100% (because retention prunes). Draw **both** limit lines, whichever retention settings are configured: `retention.max_used_disk_percent` (hard cap) and the `retention.keep`-days steady-state (estimated from observed daily volume). The forecast is computed server-side in Python (unit-testable); Chart.js just plots.
5. **Housekeeping (configurable):** a new `stats` settings section with `retention_days` (default 365; `0` = keep forever) prunes old run records. DB path hardcoded at `/config/stats.db`.

---

## Design

### 1. Architecture

The root metrics flow gains a capture hook; three new decoupled pieces serve the page.

```text
serve sync run -> SyncMetrics (now created in serve) -> metrics.finalize()
                       |
      +----------------+----------------------------------+
      v                v                                  v
 StatsStore.record_run()   emit_metrics / save_metrics_state   (existing live
 -> /config/stats.db        (closes the serve metrics gap)      ProgressPublisher,
 -> prune(retention)                                            unchanged)
      |
 GET /api/stats/series?range=... -> JSON {summary, series, forecast}
      |
 /stats page: server-rendered summary + fallback table + <canvas>s
      |
 static/js/stats.js -> vendored Chart.js renders the cards
```

`metrics.py` stays stdlib-only. The store/forecast/route live on the server (Flask) side.

### 2. SQLite time-series store -- `blackvuesync/server/stats_store.py` (new)

`sqlite3` is stdlib (no new runtime dependency). DB at **`/config/stats.db`** (the bind-mounted volume; survives restarts; sibling to `settings.json`).

- **Schema -- one row per run (wide):**

  ```sql
  CREATE TABLE IF NOT EXISTS runs (
      ts_seconds        REAL PRIMARY KEY,   -- last_run_timestamp_seconds
      success           INTEGER NOT NULL,
      exit_code         INTEGER,
      duration_seconds  REAL    NOT NULL DEFAULT 0,
      files             INTEGER NOT NULL DEFAULT 0,
      bytes             INTEGER NOT NULL DEFAULT 0,
      recordings_seen   INTEGER NOT NULL DEFAULT 0,
      recordings_selected INTEGER NOT NULL DEFAULT 0,
      disk_used_ratio   REAL,
      failed_markers    INTEGER NOT NULL DEFAULT 0,
      failures_json     TEXT,               -- {reason: count} merged run + file failures
      dry_run           INTEGER NOT NULL DEFAULT 0
  );
  CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs (ts_seconds);
  ```

- **API:** `StatsStore(db_path: str)` with:
  - `record_run(metrics: SyncMetrics) -> None` -- one `INSERT OR REPLACE` (keyed on `ts_seconds`).
  - `query(since_ts: float | None) -> list[RunRow]` -- ordered ascending by `ts_seconds`.
  - `prune(retention_days: int) -> int` -- `DELETE FROM runs WHERE ts_seconds < ?`; no-op when `retention_days <= 0`.
- **Concurrency:** `PRAGMA journal_mode=WAL`; a short-lived `sqlite3.connect` per call. The sync thread is the sole writer; Flask handlers are readers. WAL lets reads proceed during a write. `check_same_thread=False` is unnecessary since connections are not shared across threads.

### 3. Housekeeping -- new `stats` settings section

`blackvuesync/settings.py` gains:

```python
@dataclass(frozen=True)
class StatsSettings:
    """statistics time-series store settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"
    retention_days: int = 365  # prune run records older than this; 0 keeps all

    def validate(self) -> list[str]:
        if self.retention_days < 0:
            return ["stats.retention_days must be zero or greater"]
        return []
```

Added to the top-level `Settings` tree (`stats: StatsSettings = field(default_factory=StatsSettings)`). Existing `settings.json` files without a `stats` key load with the default (the loader builds sections from dataclass defaults -- no destructive migration). Optional env seed `STATS_RETENTION_DAYS` on first run. A `FieldSpec` in `settings_form.py` surfaces a "Statistics" pane with a numeric "History retention (days)" field in the Settings UI. The prune runs after each `record_run` using the current `stats.retention_days` (tier `next_tick`: a change takes effect at the next sync).

### 4. Capture path -- `blackvuesync/server/sync_runner.py`

`_do_sync()` constructs a `SyncMetrics`, passes `metrics=` to `sync.sync(...)`, and in the `finally` after the run:

1. `metrics.failed_marker_files = count_failed_marker_files(destination)` (best-effort).
2. `metrics.finalize(exit_code, sync_success)`.
3. `app_stats_store.record_run(metrics)` then `app_stats_store.prune(settings.stats.retention_days)`.
4. `save_metrics_state(state_file, metrics)` and `emit_metrics(...)` (closes the serve-mode Prometheus/state gap -- the same wiring the CLI already does).

Dry-run records a row flagged `dry_run=1`; volume charts exclude dry-run rows. The store is constructed in `cmd_serve` and reachable by the sync thread (passed into the runner / attached to the app, mirroring `app.progress_publisher` / `app.log_buffer`).

### 5. Query + forecast API -- `blackvuesync/server/routes/api_stats.py` (new)

`GET /api/stats/series?range=24h|7d|30d|all` (`@login_required`) -> JSON:

```json
{
  "range": "7d",
  "summary": {"runs": N, "bytes": N, "avg_duration_seconds": N, "success_rate": 0.996},
  "series": {
    "points": [{"ts": ..., "bytes": ..., "files": ..., "duration": ..., "disk": ..., "failures": {"http": 1}}, ...]
  },
  "forecast": {
    "projected": [{"ts": ..., "disk": ...}, ...],
    "limits": {"max_used_disk_percent": 0.85, "keep_steady_state": 0.62}
  }
}
```

The **forecast** is computed server-side by a small `blackvuesync/server/forecast.py`: a linear fit of the recent disk-used-ratio slope, then asymptote to whichever ceiling binds first. `limits.max_used_disk_percent` is included only when `retention.max_used_disk_percent` is set; `limits.keep_steady_state` only when `retention.keep` is set and enough history exists to estimate daily volume. With fewer than a minimum number of points, `forecast.projected` is empty (the chart omits the projection). Keeping the model in Python makes it unit-testable and keeps Chart.js a dumb renderer.

### 6. The `/stats` page

Replaces `_placeholders/stats.html`.

- `blackvuesync/server/templates/stats.html` -- extends `base.html`; server-renders the summary row + the range selector + a `<noscript>` fallback table of recent runs (progressive enhancement); the chart cards are `<canvas>` elements.
- `blackvuesync/server/static/js/stats.js` -- an `@alpinejs/csp`-friendly component (bare refs) or a small plain-JS module: on load and on range change, fetch `/api/stats/series?range=...`, then create/update Chart.js instances on the canvases (including the actual + projected + limit-line datasets for the disk chart).
- `blackvuesync/server/static/js/chart.umd.min.js` -- vendored Chart.js v4 (added to SonarCloud exclusions like `htmx.min.js` / `alpine.min.js`).
- `blackvuesync/server/static/css/stats.css` -- range chips, summary tiles, chart-card grid.

CSP is unchanged: `script-src 'self'` covers vendored Chart.js (dependency-free, canvas, no `eval`); `img-src 'self' data:` covers any canvas/data-URI use; `connect-src 'self'` covers the same-origin fetch.

### 7. Edge cases

| Situation | Handling |
| --- | --- |
| Empty store (fresh deploy) | summary zeros; charts show a "no runs yet" state |
| < min points for a trend | forecast omitted; actual line still drawn |
| `retention.max_used_disk_percent` unset | omit that limit line |
| `retention.keep` unset / too little history | omit the steady-state line |
| range with no rows | empty charts with a note |
| DB locked | single writer (sync thread) + WAL; readers use a fresh connection |
| dry-run runs | recorded with `dry_run=1`; excluded from volume charts |
| JS disabled | server-rendered summary + fallback table remain readable |

### 8. Testing

- **Unit:** `test/test_stats_store.py` (insert/query/prune, schema, WAL, dry-run flag); `test/test_forecast.py` (slope fit, asymptote-to-ceiling, conditional limits, too-few-points -> no projection).
- **Routes:** `test/test_routes_api_stats.py` (JSON shape per range, `@login_required`, `/stats` renders summary + fallback + `js/stats.js` + canvases + the vendored chart script tag).
- **Capture:** a test that a serve sync writes one row and emits metrics (extends the sync_runner tests).
- **E2E:** `test/e2e/test_stats_page.py` (Playwright: load `/stats`, charts render, switch range refetches).
- **Settings:** extend settings/settings-form tests for the new `stats` section (default, validate, form field).

### 9. Files

**Create:**

- `blackvuesync/server/stats_store.py`
- `blackvuesync/server/forecast.py`
- `blackvuesync/server/routes/api_stats.py`
- `blackvuesync/server/templates/stats.html`
- `blackvuesync/server/static/js/stats.js`
- `blackvuesync/server/static/js/chart.umd.min.js` (vendored Chart.js v4)
- `blackvuesync/server/static/css/stats.css`
- `test/test_stats_store.py`, `test/test_forecast.py`, `test/test_routes_api_stats.py`, `test/e2e/test_stats_page.py`

**Modify:**

- `blackvuesync/settings.py` -- add `StatsSettings` + wire into the `Settings` tree (+ optional env seed).
- `blackvuesync/server/settings_form.py` -- `FieldSpec` for `stats.retention_days`.
- `blackvuesync/server/sync_runner.py` -- construct + finalize `SyncMetrics`; record to the store + prune; emit metrics / save state.
- `blackvuesync/__main__.py` -- construct `StatsStore` in `cmd_serve`, pass to the runner / `create_app`.
- `blackvuesync/server/__init__.py` -- register `api_stats_bp`; attach the store.
- `blackvuesync/server/routes/ui.py` -- `/stats` renders the real page.
- `sonar-project.properties` -- exclude `chart.umd.min.js` from analysis.
- `docs/api.md` -- document `/api/stats/series`.
- `pyproject.toml` -- version bump; add new test modules to mypy overrides as needed.

**Delete:** `blackvuesync/server/templates/_placeholders/stats.html`.

### 10. Scope guards (YAGNI)

**OUT:** real-time streaming (the page is load/refresh; an optional periodic summary poll is the most it does), CSV/data export, custom/configurable dashboards, per-run drill-down beyond the charts, alerting/notifications on thresholds.

---

## Verification

1. `pip install -e ".[dev]"`, `python -m blackvuesync serve`, log in, let a scheduled sync (or `POST /api/sync/now`) run, open `/stats` -- summary + charts populate; the disk chart shows actual + projected + limit line(s).
2. Switch range chips (24h / 7d / 30d / All) -- charts refetch and redraw.
3. Set `stats.retention_days` low in Settings; confirm old rows prune at the next run.
4. Confirm serve mode now writes `/config/metrics-state.json` and emits Prometheus metrics (the bonus gap-closure).
5. `pytest test/test_stats_store.py test/test_forecast.py test/test_routes_api_stats.py -v` and `pytest test/e2e/test_stats_page.py -m e2e -v` pass.
6. PR with all required checks green and **0 SonarCloud findings** (query the issues API directly, not just the gate). Squash-merge (linear history).

---

## Self-review

- **Placeholders/TBDs:** none.
- **Internal consistency:** the `SyncMetrics`-into-serve decision is reflected in the architecture, capture path, and the bonus gap-closure; the forecast's "both limit lines, conditional" matches the API `limits` object and the edge-case table; the `stats.retention_days` housekeeping is consistent across the settings section, the store `prune`, and the capture path.
- **Scope:** one cohesive sub-project (capture -> store -> page); the OUT list holds the line against export/alerting/custom dashboards.
- **Ambiguity:** the data source (serve `SyncMetrics`), forecast location (server-side Python), DB path (hardcoded `/config/stats.db`), and housekeeping (configurable `stats.retention_days`) are all made explicit.

**End of design spec.**
