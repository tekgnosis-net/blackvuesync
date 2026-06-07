# Sub-Project #6 -- Dashcam Viewer -- Design Spec

**Date:** 2026-06-08
**Repo:** tekgnosis-net/blackvuesync (fork of acolomba/blackvuesync)
**Status:** Design approved by user (pending spec review); awaiting implementation plan
**Series:** sixth and final UI sub-project, after #1 Web Foundation, #2
Dashboard, #3 Settings UI, #4 Log viewer, and #5 Statistics page (all merged).

---

## Context

The `/viewer` route is a placeholder. This sub-project turns it into a dashcam
viewer like the BlackVue PC Viewer: synchronized front/rear video, a GPS-driven map
path, and a telemetry panel (speed + G-sensor), browsing the recordings already
synced to `settings.system.destination`.

It is the largest single feature in the series: multi-video synchronization, NMEA
GPS parsing, a reverse-engineered binary accelerometer parser, map integration, a
cross-segment "journey" state machine, and path-safe file serving.

**Reusable today:** `sync.to_recording()` / `Recording` / `get_downloaded_recordings()`
parse filenames and enumerate the disk; an `/api/recordings` blueprint already exists;
the foundation CSP already allow-lists same-origin video (`media-src 'self'`) and OSM
tiles (`img-src ... https://*.tile.openstreetmap.org`); Chart.js is already vendored
(from #5). **Greenfield:** the `.gps`/`.3gf` parsers, the path-safe media route, and
vendoring Leaflet.

**Device-verified formats** (from a real DR-series sample) are documented in
`docs/reference/blackvue-file-formats.md`, created as part of this sub-project. Key
findings that shape the parsers: the real `.gps` uses the multi-GNSS `$GN` talker
(not `$GP`); `.3gf` is big-endian 10-byte records `[uint32 ms][int16 x][int16 y][int16 z]`
at ~10 Hz; `.thm` is JPEG; `.mp4` is browser-native H.264.

---

## Decisions (resolved during brainstorming)

1. **Playback continuity:** **auto-advance** through consecutive segments (BlackVue
   records ~1-minute segments). The unit of playback is a *journey* -- the selected
   segment plus the forward chain of contiguous segments.
2. **Player layout:** front + rear with a **runtime PIP ↔ side-by-side toggle**
   (PIP default), plus a swap control for which camera is primary.
3. **Telemetry:** map **+ speed + G-sensor** below the player (full BlackVue parity).
4. **Map/telemetry accumulation:** coherent and **continuous across the journey**
   (the path/telemetry do not reset at segment boundaries); reset only on a
   continuity break or a new selection. Two modes, **configurable**:
   `progressive` (default; grows lazily as you watch) and `full` (server merges the
   whole chain up front; the entire route is plotted with the marker showing the
   playing clip's position in the journey).
5. **Speed unit:** configurable `kmh` (default) / `mph`.
6. **Stack:** plain-JS single-page viewer + **vendored Leaflet** (map) + HTML5
   `<video>` + reused Chart.js (telemetry). Parsing is **server-side** (Python),
   the client fetches JSON. No CSP change.
7. **Out:** clip export/trim, sharing, click-to-seek across the journey, backward
   auto-advance, live streaming, G-sensor event analytics, deletion from the viewer,
   non-OSM map providers, and #7 dashcam settings writes (hardware-gated).

---

## Design

### 1. Architecture, components & data flow

**Server (new, under `blackvuesync/server/`):**

- **`gps.py`** -- pure parser: `.gps` text -> `list[GpsPoint]` (`t` elapsed seconds,
  `lat`, `lon`, `speed`). Talker-agnostic `$G?RMC`/`$G?GGA`; `DDMM.mmmmm`+hemisphere
  -> decimal; knots retained (unit conversion is a display concern). stdlib-only.
- **`gsensor.py`** -- pure parser: `.3gf` binary -> `list[GForce]` (`t` seconds,
  `x`, `y`, `z` in g). 10-byte big-endian records. stdlib-only.
- **`viewer_index.py`** -- recording enumeration + the **journey/continuity chain**:
  given the destination + grouping, list recordings (grouped by day, newest first,
  with directions present + `has_gps`/`has_3gf`/`has_thm`), and compute the forward
  chain of contiguous segments from a given base. Built on `sync.to_recording` /
  `get_downloaded_recordings`. Pure logic over a directory listing.
- **`routes/api_viewer.py`** (`@login_required`, `url_prefix="/api/viewer"`):
  - `GET /recordings` -- recordings grouped by day (newest first); per recording:
    base, datetime, type, directions, `has_gps`/`has_3gf`/`has_thm`, thumbnail URL.
  - `GET /recordings/<base>/journey` -- the ordered chain of contiguous segments
    from `<base>` (each: base, direction file URLs, start-epoch, `has_gps`/`has_3gf`).
  - `GET /recordings/<base>/gps` and `/gsensor` -- parsed telemetry JSON for one
    segment.
- **`routes/media.py`** (`@login_required`) -- `GET /media/<path:relpath>`: path-safe
  serving of `.mp4`/`.thm` with HTTP Range. Its own module (security-sensitive).

**Client (new):**

- **`templates/viewer.html`** -- the validated layout: recording sidebar, `<video>`×2
  player (PIP/side-by-side), transport bar, Leaflet map div, telemetry panel.
- **`static/js/viewer.js`** -- plain-JS journey state machine: load recording ->
  master/slave video sync -> `requestAnimationFrame` loop driving the map marker +
  telemetry cursor + speed readout off `video.currentTime` -> auto-advance on `ended`
  with accumulating map/telemetry -> PIP/side-by-side + swap toggles.
- **`static/css/viewer.css`** -- layout (dark player surface, sidebar, panels).
- **Vendored Leaflet** (`static/js/leaflet.js` + `static/css/leaflet.css` + marker
  images under `static/css/images/`). **Reuse the already-vendored Chart.js** for the
  speed + G-sensor graphs -- no new charting dependency.

**Data flow:** page load -> `GET /api/viewer/recordings` -> render sidebar (thumbnails
via `/media`). Select a recording -> `GET .../journey` + the first segment's `/gps`
and `/gsensor` -> point both `<video>`s at `/media`; Leaflet draws the path + marker;
Chart.js draws speed + G-sensor with a playback cursor. Play -> front is master, rear
slaved; the rAF loop maps `currentTime` -> marker + cursor + speed. On `ended` ->
advance to the next chain segment, **appending** its GPS/telemetry (progressive) or
having already merged the whole chain (full); reset only on chain end / new selection.

**CSP:** unchanged -- same-origin video (`media-src 'self'`), OSM tiles (`img-src`
already allow-listed; Leaflet raster tiles are `<img>`, so `connect-src` is not
involved), vendored Leaflet + Chart.js (`script-src 'self'`), no `eval`.

### 2. File serving & security (`routes/media.py`)

The media route turns a URL path into a filesystem read -- the one security-sensitive
new surface. Layered defenses:

- `@login_required` -- footage + GPS are sensitive; never anonymous. The browser sends
  the session cookie automatically on `<video>`/`<img>` requests (same-origin).
- **Path safety:** (1) `werkzeug.security.safe_join(destination, relpath)` -> `None`
  on traversal -> 404; (2) **realpath containment** -- the resolved target must be
  inside the resolved destination (rejects symlink escapes) -> 404 otherwise;
  (3) **extension allow-list** -- only `.mp4`/`.thm` (never `.json`/`.lock`/dotfiles).
- **Streaming:** `send_from_directory(destination, safe_rel, conditional=True)` emits
  `Accept-Ranges` and honors `Range` (206/304) -- required to scrub/stream large
  `.mp4` (the sample front file is ~197 MB). `.thm` served with explicit
  `image/jpeg`.
- **No directory listing:** the route only fetches a named file; enumeration is solely
  via the authenticated `/api/viewer/recordings` JSON (which exposes parsed metadata +
  relative paths, never absolute filesystem paths).

### 3. Player internals (`viewer.js`)

- **Master/slave video:** front is master (scrubber + audio); rear is slave (muted).
  Master `play`/`pause` mirror to slave; `seeking`/`seeked` -> `slave.currentTime =
  master.currentTime`; `ratechange` mirrors `playbackRate`. A **drift corrector** in
  the rAF loop re-pins the slave when `|slave.currentTime - master.currentTime| >
  0.15 s` (browsers do not frame-lock two videos). Mismatched durations clamp.
- **Transport bar** (bound to master): play/pause, seek scrubber, elapsed/total time,
  playback-rate (0.5×-2×), and the next-segment control. Scrubbing sets
  `master.currentTime`, propagating to rear + map + telemetry.
- **PIP ↔ side-by-side:** a CSS class on the player container; PIP = front fills + rear
  inset (corner) with a **swap** button; side-by-side = two-column grid. Layout-only,
  no effect on sync.
- **Auto-advance:** on master `ended`, read the next segment from the cached journey
  chain; if present, load it (swap both `<video>` srcs, fetch/append its GPS+G-sensor)
  and continue; else stop. The journey runs **forward from the selected segment**.

### 4. Map, telemetry & the journey model

**Journey model:** a *journey* is the selected segment + the forward chain of
**contiguous** segments. `viewer_index.py` computes the chain: the next recording by
timestamp whose start is ~contiguous with the prior segment's end (within a gap
threshold) and whose recording context matches. The map path and telemetry are
**continuous across the journey**, resetting only when the chain ends or a new,
non-contiguous recording is selected. Continuity is anchored on the GPS absolute
epoch-ms wall clock: "where is the car now" = `segment_start_epoch + video.currentTime`
-> nearest GPS point on the accumulated path.

**Map (Leaflet):** the GPS track renders as an `L.polyline`; a position marker
traverses it; `fitBounds` frames the route. As auto-advance enters each contiguous
segment, that segment's GPS is appended to the polyline (progressive) or it was merged
up front (full). Graceful degradation: offline -> polyline + marker still draw on a
blank backdrop; no `.gps`/no fix -> a "no GPS for this recording" panel state while
video + G-sensor still work; parked/stationary -> a single pinned point, no line.

**Telemetry (reused Chart.js):** a **speed** graph and a **G-sensor** (X/Y/Z) graph,
x-axis = cumulative journey time, accumulating across segments, with a vertical
playback cursor that sweeps as playback proceeds. Speed displayed in
`settings.viewer.speed_unit`.

**The two modes share one renderer** (same parse + plot code); they differ only in
*when* the chain's GPS/telemetry is fetched/merged:

- `progressive` (default) -- each segment's GPS/G-sensor is fetched + appended when
  auto-advance reaches it (lazy; only played segments loaded).
- `full` -- on selection the client loads the whole chain's GPS/G-sensor up front
  (GPS/3gf are KB-scale), plotting the entire route immediately; the marker shows the
  playing clip's position within the whole journey.

### 5. The `viewer` settings section

A new `viewer` settings section (the 11th), following the `stats`-section pattern:

```python
@dataclass(frozen=True)
class ViewerSettings:
    """dashcam viewer settings."""

    TIER: ClassVar[PropagationTier] = "immediate"

    journey_mode: Literal["progressive", "full"] = "progressive"
    speed_unit: Literal["kmh", "mph"] = "kmh"

    def validate(self) -> list[str]:
        """validates viewer settings; returns a list of error strings."""
        errors: list[str] = []
        if self.journey_mode not in ("progressive", "full"):
            errors.append("viewer.journey_mode must be 'progressive' or 'full'")
        if self.speed_unit not in ("kmh", "mph"):
            errors.append("viewer.speed_unit must be 'kmh' or 'mph'")
        return errors
```

Wired into the `Settings` tree, `_SECTION_FIELDS`, `Settings.validate`, env bootstrap,
and `settings_form.py` (a "Viewer" pane with two `select` fields). `immediate`-tier:
the viewer reads it on the next recording load; GET/PATCH `/api/settings` flow for
free. The viewer page passes the current values into `viewer.js` (e.g. a `data-`
attribute) so the client honors the mode + unit without an extra fetch.

### 6. Data formats & parsers

Per `docs/reference/blackvue-file-formats.md` (created here):

- **`gps.py`** -- split on lines; for lines matching `[<digits>]$G?(RMC|GGA),...`,
  extract the bracket epoch-ms + parse the sentence. RMC -> lat/lon/speed(knots);
  GGA -> lat/lon (fallback / altitude). `DDMM.mmmmm`+hemisphere -> decimal. `t` =
  `(epoch_ms - first_epoch_ms) / 1000`. Tolerates blank/garbage lines, missing fix
  (status `V`), and a single stationary point.
- **`gsensor.py`** -- `struct.iter_unpack(">Ihhh", data)` over the file (ignoring any
  trailing `< 10`-byte remainder); `t = ms / 1000`; `x/y/z = raw / SCALE` (g). `SCALE`
  defaults to the value confirmed against `bartbroere/blackvue-acc` during
  implementation (working value ≈ 128, validated so the sample's parked magnitude ≈ 1 g).

---

## Reference documentation deliverable

`docs/reference/blackvue-file-formats.md` -- the canonical, committed reference for the
filename grammar, type/direction codes, per-recording file set, and the decoded
`.gps`/`.3gf`/`.thm`/`.mp4` formats, with citations. The parser modules link to it.

---

## Testing

- **Pure parsers (unit):** `test_gps.py` (talker-agnostic `$GN`/`$GP`, RMC+GGA,
  DDMM->decimal w/ hemisphere sign, knots, `[epoch-ms]`->elapsed, blank/malformed-line
  tolerance, single-point + no-fix); `test_gsensor.py` (10-byte BE decode, g-scaling,
  truncated trailing bytes, empty file). Fixtures **synthetic/anonymized**, matching
  the real byte/line format.
- **Journey logic:** `test_viewer_index.py` -- chain/continuity (contiguous detection,
  gap threshold, disparate boundary, forward-only), day-grouping, directions-present.
- **Routes:** `test_routes_media.py` (traversal/symlink/extension rejection -> 404,
  `Range` -> 206, auth, `.thm` -> `image/jpeg`); `test_routes_api_viewer.py`
  (recordings/journey/gps/gsensor JSON shapes, newest-first, auth); `test_routes_ui.py`
  (extend: `/viewer` renders the real page); settings tests (extend: the `viewer`
  section defaults/validate + the "Viewer" Settings pane).
- **E2E (Playwright):** `test_viewer_page.py` -- seed tiny synthetic fixtures in the
  live-server destination, load `/viewer`, sidebar lists recordings, select one ->
  `<video>` srcs set + `.leaflet-container` + telemetry canvases render, **assert no
  `pageerror`** (real-browser net for the journey state machine + Leaflet + Chart.js).

---

## Files

**Create:** `server/gps.py`, `server/gsensor.py`, `server/viewer_index.py`,
`server/routes/api_viewer.py`, `server/routes/media.py`, `templates/viewer.html`,
`static/js/viewer.js`, `static/css/viewer.css`, vendored
`static/js/leaflet.js` + `static/css/leaflet.css` + Leaflet marker images,
`docs/reference/blackvue-file-formats.md`, and the test modules
(`test_gps.py`, `test_gsensor.py`, `test_viewer_index.py`, `test_routes_media.py`,
`test_routes_api_viewer.py`, `test/e2e/test_viewer_page.py`).

**Modify:** `settings.py` (+`ViewerSettings` wired into the tree/`_SECTION_FIELDS`/
validate/env bootstrap), `server/settings_form.py` (+ Viewer pane), `server/__init__.py`
(register `api_viewer_bp` + `media_bp`), `server/routes/ui.py` (`/viewer` real route),
`docs/api.md` (viewer + media endpoints), `pyproject.toml` (version bump + mypy
overrides), `sonar-project.properties` (exclude vendored `leaflet.js`),
`.pre-commit-config.yaml` (large-file-hook exclude for vendored Leaflet if >64 KB).

**Delete:** `templates/_placeholders/viewer.html`.

---

## Scope guards (YAGNI)

**OUT:** clip export / trim / download; sharing / public links; click-the-map/graph
to seek across the journey (v1 scrubs within the current segment + auto-advances
forward); backward auto-advance; live dashcam streaming; G-sensor event/impact
detection (graph only, not analytics); recording deletion from the viewer (read-only;
deletion stays retention's job); non-OSM / satellite / offline-bundled map tiles;
**#7 dashcam settings writes** (hardware-gated).

---

## Verification

1. `python -m blackvuesync serve`, log in, open `/viewer`; the sidebar lists synced
   recordings; selecting one plays front+rear synced, draws the GPS path with a moving
   marker, and shows speed + G-sensor; auto-advance continues to the next segment with
   a continuous (non-reset) path/telemetry.
2. Toggle PIP ↔ side-by-side + swap; scrub; confirm rear stays in sync.
3. Set `viewer.journey_mode = full` in Settings; reload a recording -> the whole
   journey route is plotted up front with the marker showing the clip's position.
   Set `speed_unit = mph` -> speed readout switches.
4. Path-safety: `GET /media/../settings.json` and a symlink escape both 404; a `Range`
   request returns 206.
5. `pytest test/ -q -m 'not e2e'` and `pytest test/e2e/test_viewer_page.py -m e2e` pass.
6. PR with all required checks green + **0 SonarCloud findings** (issues API, project
   key `tekgnosis-net_blackvuesync`). Squash-merge (linear history).

---

## Self-review

- **Placeholders/TBDs:** none. The `.3gf` g-scale is an empirically-anchored constant
  (≈128) to be confirmed against `bartbroere/blackvue-acc` in the parser task -- the
  format structure itself is device-verified, not a placeholder.
- **Internal consistency:** the journey model (Section 4) drives the auto-advance
  (Section 3) and the accumulation; the dual-mode setting (Section 5) is a fetch
  strategy over the same Section 1 endpoints; the parsers (Section 6) match the
  reference doc and the testing fixtures.
- **Scope:** one cohesive sub-project (enumerate -> serve -> parse -> play+map). The
  OUT list holds the line against export/sharing/cross-journey-seek/analytics.
- **Ambiguity:** stack (plain JS + Leaflet), parsing location (server-side Python),
  continuity definition (forward-from-selection, contiguous-by-epoch), accumulation
  (continuous, dual-mode), units (configurable), and security (authed, path-safe,
  allow-listed) are all explicit.

**End of design spec.**
