# Sub-Project #6 -- Dashcam Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `/viewer` page that plays synchronized front/rear dashcam video with a GPS-driven map path and speed + G-sensor telemetry, auto-advancing through consecutive segments as a coherent "journey".

**Architecture:** Server-side pure parsers (`.gps` NMEA, `.3gf` binary) + a recording/journey index + JSON API + a path-safe media route feed a plain-JS single-page viewer (vendored Leaflet map, HTML5 `<video>` master/slave sync, reused Chart.js telemetry). A new `viewer` settings section toggles the journey accumulation mode + speed unit.

**Tech Stack:** Python stdlib (`re`, `struct`, `glob`, `os`), Flask, Jinja2, vendored Leaflet v1.9, reused Chart.js v4, plain ES2020 JS, pytest + pytest-playwright.

**Spec:** `docs/plans/2026-06-08-sub-project-6-viewer-design.md`. **Reference:** `docs/reference/blackvue-file-formats.md` (already committed). **Branch:** `sub-project-6-viewer` (spec + ref committed as `1cf41f3`).

**Conventions (every task):** tests via `venv/bin/pytest`; stage commits by EXPLICIT path (NEVER `git add -A`/`.` -- the repo's `tmp/` holds a 276 MB real sample zip and a developer-local `supertool` symlink, neither of which may ever be staged); NEVER `--no-verify` (fix hooks, re-stage; never amend past an auto-fix -- new commit); comments lowercase/third-person/non-obvious; full type annotations; `metrics.py`/`sync.py` stay stdlib-only (the new `server/` parsers may use stdlib `struct`/`re`); CSP unchanged; `viewer.js` is plain JS but must pass the SonarCloud JS rules noted in Task 9/10; markdown code fences need a language and no line may start with `#`. The real sample at `/tmp/blackvue-sample/` is READ-ONLY ground truth for hand-validation -- never commit it; committed fixtures are synthetic/anonymized.

---

## File structure

**Create (server, Python):** `server/gps.py`, `server/gsensor.py`, `server/viewer_index.py`, `server/routes/api_viewer.py`, `server/routes/media.py`.
**Create (client):** `templates/viewer.html`, `static/js/viewer.js`, `static/css/viewer.css`, vendored `static/js/leaflet.js` + `static/css/leaflet.css` + `static/css/images/*`.
**Create (tests):** `test/test_gps.py`, `test/test_gsensor.py`, `test/test_viewer_index.py`, `test/test_routes_media.py`, `test/test_routes_api_viewer.py`, `test/e2e/test_viewer_page.py`.
**Modify:** `settings.py` (+`ViewerSettings`), `server/settings_form.py` (+Viewer pane), `server/__init__.py` (register 2 blueprints), `server/routes/ui.py` (`/viewer` real), `sonar-project.properties` + `.pre-commit-config.yaml` (vendored leaflet excludes), `docs/api.md`, `pyproject.toml` (version + mypy).
**Delete:** `templates/_placeholders/viewer.html`.

---

### Task 1: GPS parser (`server/gps.py`)

**Files:** Create `blackvuesync/server/gps.py`, `test/test_gps.py`.

- [ ] **Step 1: Write the failing tests.** Create `test/test_gps.py`:

```python
"""unit tests for the blackvue .gps NMEA parser."""

from __future__ import annotations

from blackvuesync.server.gps import GpsPoint, parse_gps

# synthetic, anonymized -- matches the real $GN framing (NOT $GP) and the
# [epoch-ms] line prefix, with made-up coordinates.
_TWO_POINTS = (
    "[1000]$GNRMC,055056.00,A,3348.10000,S,15101.10000,E,0.000,,070626,,,A,V*06\r\n"
    "[1000]$GNGGA,055056.00,3348.10000,S,15101.10000,E,1,12,0.68,52.8,M,19.4,M,,*6B\r\n"
    "\n"
    "[2000]$GNRMC,055057.00,A,3348.20000,N,15101.20000,W,12.340,,070626,,,A,V*07\r\n"
)
_NO_FIX = "[1000]$GNRMC,055056.00,V,,,,,,,070626,,,N*53\r\n"
_GGA_ONLY = "[5000]$GNGGA,055056.00,1234.50000,N,12345.60000,W,1,07,1.2,5.0,M,,M,,*40\r\n"


def test_parses_rmc_points_with_elapsed_time_and_decimal_coords() -> None:
    points = parse_gps(_TWO_POINTS)
    assert len(points) == 2
    assert isinstance(points[0], GpsPoint)
    # t is elapsed seconds from the first sentence's epoch-ms
    assert points[0].t == 0.0
    assert points[1].t == 1.0
    # 3348.1 S -> -(33 + 48.1/60); 15101.1 E -> +(151 + 1.1/60)
    assert points[0].lat == -(33 + 48.1 / 60)
    assert points[0].lon == 151 + 1.1 / 60
    # second point N/W flips signs
    assert points[1].lat == 33 + 48.2 / 60
    assert points[1].lon == -(151 + 1.2 / 60)
    assert points[0].speed == 0.0
    assert points[1].speed == 12.34  # knots, unconverted


def test_skips_no_fix_sentences() -> None:
    assert parse_gps(_NO_FIX) == []


def test_falls_back_to_gga_when_no_rmc() -> None:
    points = parse_gps(_GGA_ONLY)
    assert len(points) == 1
    assert points[0].speed is None  # gga has no speed
    assert points[0].lat == 12 + 34.5 / 60


def test_empty_and_garbage_tolerated() -> None:
    assert parse_gps("") == []
    assert parse_gps("not nmea\n[bad]\n\n") == []
```

- [ ] **Step 2: Run to confirm failure.** `venv/bin/pytest test/test_gps.py -q` -> module missing.

- [ ] **Step 3: Implement `blackvuesync/server/gps.py`:**

```python
"""parser for blackvue .gps sidecar files: timestamped NMEA-0183 text.

format (see docs/reference/blackvue-file-formats.md): each line is
`[epoch-ms]$G?RMC,...` or `[epoch-ms]$G?GGA,...`. the talker is multi-gnss
($GN), so matching is talker-agnostic on the sentence type. stdlib-only.
"""

from __future__ import annotations

import dataclasses
import re

# [epoch-ms] + $ + G + any talker letter + RMC|GGA + comma + the field body.
_SENTENCE_RE = re.compile(r"\[(?P<ms>\d+)\]\$G[A-Z](?P<kind>RMC|GGA),(?P<fields>[^*\r\n]*)")


@dataclasses.dataclass(frozen=True)
class GpsPoint:
    """one GPS fix: elapsed seconds from start, decimal lat/lon, speed in knots."""

    t: float
    lat: float
    lon: float
    speed: float | None


def _dm_to_decimal(value: str, hemisphere: str) -> float | None:
    """converts a DDMM.mmmmm / DDDMM.mmmmm + hemisphere string to decimal degrees."""
    if not value:
        return None
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    decimal = degrees + minutes / 60.0
    return -decimal if hemisphere in ("S", "W") else decimal


def _parse_rmc(fields: list[str]) -> tuple[float, float, float | None] | None:
    """returns (lat, lon, speed_knots) from RMC fields, or None when no fix."""
    # RMC: time, status, lat, N/S, lon, E/W, speed, course, date, ...
    if len(fields) < 7 or fields[1] != "A":
        return None
    lat = _dm_to_decimal(fields[2], fields[3])
    lon = _dm_to_decimal(fields[4], fields[5])
    if lat is None or lon is None:
        return None
    speed = float(fields[6]) if fields[6] else None
    return lat, lon, speed


def _parse_gga(fields: list[str]) -> tuple[float, float, float | None] | None:
    """returns (lat, lon, None) from GGA fields, or None when no fix."""
    # GGA: time, lat, N/S, lon, E/W, fix-quality, ...
    if len(fields) < 6 or fields[5] in ("", "0"):
        return None
    lat = _dm_to_decimal(fields[1], fields[2])
    lon = _dm_to_decimal(fields[3], fields[4])
    if lat is None or lon is None:
        return None
    return lat, lon, None


def parse_gps(text: str) -> list[GpsPoint]:
    """parses .gps text into GpsPoints, ascending by time, one per epoch-ms.

    RMC is preferred (it carries speed); a GGA-only timestamp is used as a
    position fallback. invalid / no-fix / unparseable lines are skipped.
    """
    by_ms: dict[int, tuple[float, float, float | None]] = {}
    rmc_ms: set[int] = set()
    for match in _SENTENCE_RE.finditer(text):
        ms = int(match.group("ms"))
        fields = match.group("fields").split(",")
        if match.group("kind") == "RMC":
            parsed = _parse_rmc(fields)
            if parsed is not None:
                by_ms[ms] = parsed
                rmc_ms.add(ms)
        elif ms not in rmc_ms:  # GGA only fills positions without an RMC
            parsed = _parse_gga(fields)
            if parsed is not None:
                by_ms[ms] = parsed
    if not by_ms:
        return []
    first_ms = min(by_ms)
    return [
        GpsPoint((ms - first_ms) / 1000.0, lat, lon, speed)
        for ms, (lat, lon, speed) in sorted(by_ms.items())
    ]


__all__ = ["GpsPoint", "parse_gps"]
```

- [ ] **Step 4: Run to confirm pass.** `venv/bin/pytest test/test_gps.py -q` -> 4 passed.

- [ ] **Step 5: Hand-validate against the real sample** (not committed): `venv/bin/python -c "from blackvuesync.server.gps import parse_gps; print(parse_gps(open('/tmp/blackvue-sample/20260607_181156_P.gps').read()))"` -> expect one point near `lat=-33.80..., lon=151.02..., speed=0.0`. If empty, the talker regex is wrong -- fix before committing.

- [ ] **Step 6: Commit.**

```bash
git add blackvuesync/server/gps.py test/test_gps.py
git commit -m "feat: add blackvue .gps NMEA parser"
```

---

### Task 2: G-sensor parser (`server/gsensor.py`)

**Files:** Create `blackvuesync/server/gsensor.py`, `test/test_gsensor.py`.

- [ ] **Step 1: Write the failing tests.** Create `test/test_gsensor.py`:

```python
"""unit tests for the blackvue .3gf accelerometer parser."""

from __future__ import annotations

import struct

from blackvuesync.server.gsensor import GForce, SCALE_G, parse_gsensor


def _record(ms: int, x: int, y: int, z: int) -> bytes:
    return struct.pack(">Ihhh", ms, x, y, z)


def test_parses_big_endian_10_byte_records() -> None:
    data = _record(735, 130, 5, -20) + _record(840, 129, 5, -20)
    points = parse_gsensor(data)
    assert len(points) == 2
    assert isinstance(points[0], GForce)
    assert points[0].t == 0.735
    assert points[0].x == 130 / SCALE_G
    assert points[0].z == -20 / SCALE_G
    assert points[1].t == 0.840


def test_trailing_partial_record_ignored() -> None:
    data = _record(1000, 1, 2, 3) + b"\x00\x00\x00"  # 3 dangling bytes
    points = parse_gsensor(data)
    assert len(points) == 1


def test_empty_returns_empty() -> None:
    assert parse_gsensor(b"") == []
```

- [ ] **Step 2: Run to confirm failure.** `venv/bin/pytest test/test_gsensor.py -q` -> module missing.

- [ ] **Step 3: Implement `blackvuesync/server/gsensor.py`:**

```python
"""parser for blackvue .3gf accelerometer sidecar files (binary).

format (see docs/reference/blackvue-file-formats.md): packed big-endian 10-byte
records `[uint32 ms-from-start][int16 x][int16 y][int16 z]`, ~10 Hz, no header.
stdlib-only.
"""

from __future__ import annotations

import dataclasses
import struct

_RECORD = struct.Struct(">Ihhh")  # 10 bytes: uint32 ms + 3x int16

# raw int16 units per g. a stationary recording reads magnitude ~= 1 g, which
# matches ~128. confirm the canonical divisor against bartbroere/blackvue-acc;
# relative magnitude is correct regardless of the exact value.
SCALE_G = 128.0


@dataclasses.dataclass(frozen=True)
class GForce:
    """one accelerometer sample: elapsed seconds, x/y/z in g."""

    t: float
    x: float
    y: float
    z: float


def parse_gsensor(data: bytes) -> list[GForce]:
    """parses .3gf bytes into GForce samples; ignores a trailing partial record."""
    usable = len(data) - (len(data) % _RECORD.size)
    return [
        GForce(ms / 1000.0, x / SCALE_G, y / SCALE_G, z / SCALE_G)
        for ms, x, y, z in _RECORD.iter_unpack(data[:usable])
    ]


__all__ = ["GForce", "SCALE_G", "parse_gsensor"]
```

- [ ] **Step 4: Run to confirm pass.** `venv/bin/pytest test/test_gsensor.py -q` -> 3 passed.

- [ ] **Step 5: Hand-validate + confirm SCALE_G against the real sample + bartbroere/blackvue-acc.** Run `venv/bin/python -c "from blackvuesync.server.gsensor import parse_gsensor; d=open('/tmp/blackvue-sample/20260607_181156_P.3gf','rb').read(); pts=parse_gsensor(d); import math; m=[math.sqrt(p.x*p.x+p.y*p.y+p.z*p.z) for p in pts[:50]]; print(len(pts), sum(m)/len(m))"` -> expect ~562 samples and a mean magnitude near 1.0 g (parked). If the magnitude is far from 1.0, cross-check the divisor in `bartbroere/blackvue-acc` (raw source) and adjust `SCALE_G` + the test's expected values to match the canonical constant. Document the confirmed value in `docs/reference/blackvue-file-formats.md` (replace the "confirm" note).

- [ ] **Step 6: Commit.**

```bash
git add blackvuesync/server/gsensor.py test/test_gsensor.py
git commit -m "feat: add blackvue .3gf accelerometer parser"
```

(If `docs/reference/blackvue-file-formats.md` was updated with the confirmed scale, add it to this commit too.)

---

### Task 3: Recording index + journey chain (`server/viewer_index.py`)

**Files:** Create `blackvuesync/server/viewer_index.py`, `test/test_viewer_index.py`.

- [ ] **Step 1: Write the failing tests.** Create `test/test_viewer_index.py`:

```python
"""unit tests for recording enumeration + journey chaining."""

from __future__ import annotations

from pathlib import Path

from blackvuesync.server.viewer_index import RecordingEntry, journey_chain, list_recordings


def _touch(root: Path, name: str) -> None:
    (root / name).write_bytes(b"x")


def test_groups_directions_and_detects_sidecars(tmp_path: Path) -> None:
    _touch(tmp_path, "20260607_101500_NF.mp4")
    _touch(tmp_path, "20260607_101500_NR.mp4")
    _touch(tmp_path, "20260607_101500_N.gps")
    _touch(tmp_path, "20260607_101500_NF.thm")
    entries = list_recordings(str(tmp_path), "none")
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, RecordingEntry)
    assert e.base_filename == "20260607_101500"
    assert e.type == "N"
    assert e.directions == ("F", "R")
    assert e.has_gps is True
    assert e.has_3gf is False
    assert e.has_thm is True
    assert e.rel_dir == ""


def test_newest_first_and_grouping_subdir(tmp_path: Path) -> None:
    day = tmp_path / "2026-06-07"
    day.mkdir()
    _touch(day, "20260607_101500_NF.mp4")
    _touch(day, "20260607_101600_NF.mp4")
    entries = list_recordings(str(tmp_path), "daily")
    assert [e.base_filename for e in entries] == ["20260607_101600", "20260607_101500"]
    assert entries[0].rel_dir == "2026-06-07"


def test_journey_chain_links_contiguous_same_type_only() -> None:
    def entry(ts: str, typ: str = "N") -> RecordingEntry:
        import datetime

        dt = datetime.datetime.strptime(ts, "%Y%m%d_%H%M%S")
        return RecordingEntry(ts, typ, dt, ("F",), False, False, False, "")

    a, b, c = entry("20260607_101500"), entry("20260607_101600"), entry("20260607_101700")
    far = entry("20260607_120000")  # >2 min later -> breaks the chain
    parking = entry("20260607_101800", "P")  # different type -> not chained
    chain = journey_chain([a, b, c, far, parking], "20260607_101500", "N")
    assert [e.base_filename for e in chain] == ["20260607_101500", "20260607_101600", "20260607_101700"]
```

- [ ] **Step 2: Run to confirm failure.** `venv/bin/pytest test/test_viewer_index.py -q` -> module missing.

- [ ] **Step 3: Implement `blackvuesync/server/viewer_index.py`:**

```python
"""enumerates downloaded recordings and computes auto-advance journey chains.

a recording instant is keyed by (base_filename, type); front/rear .mp4 share it
and differ by direction, and share one .gps/.3gf. built on sync.to_recording.
"""

from __future__ import annotations

import dataclasses
import datetime
import os

from blackvuesync.sync import to_recording

# two same-type segments are part of one journey when the next starts within
# this window of the prior (blackvue writes ~1-minute back-to-back segments).
_CONTIGUOUS_GAP = datetime.timedelta(seconds=120)


@dataclasses.dataclass(frozen=True)
class RecordingEntry:
    """one recording instant (base_filename + type) with its available artifacts."""

    base_filename: str
    type: str
    datetime: datetime.datetime
    directions: tuple[str, ...]
    has_gps: bool
    has_3gf: bool
    has_thm: bool
    rel_dir: str  # directory relative to destination ("" when ungrouped)


def list_recordings(destination: str, grouping: str) -> list[RecordingEntry]:
    """walks destination and returns recording instants, newest first."""
    if not os.path.isdir(destination):
        return []

    # group .mp4 files by (rel_dir, base_filename, type) -> set of directions
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    present: set[str] = set()
    for root, _dirs, files in os.walk(destination):
        rel_dir = os.path.relpath(root, destination)
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in files:
            present.add(os.path.join(rel_dir, name))
            rec = to_recording(name, grouping)
            if rec is None:
                continue  # only .mp4 names match to_recording
            key = (rel_dir, rec.base_filename, rec.type)
            slot = grouped.setdefault(
                key, {"dt": rec.datetime, "dirs": set()}
            )
            slot["dirs"].add(rec.direction)  # type: ignore[union-attr]

    entries: list[RecordingEntry] = []
    for (rel_dir, base, rtype), slot in grouped.items():
        dirs = sorted(slot["dirs"])  # type: ignore[arg-type]
        entries.append(
            RecordingEntry(
                base_filename=base,
                type=rtype,
                datetime=slot["dt"],  # type: ignore[arg-type]
                directions=tuple(dirs),
                has_gps=os.path.join(rel_dir, f"{base}_{rtype}.gps") in present,
                has_3gf=os.path.join(rel_dir, f"{base}_{rtype}.3gf") in present,
                has_thm=any(
                    os.path.join(rel_dir, f"{base}_{rtype}{d}.thm") in present
                    for d in dirs
                ),
                rel_dir=rel_dir,
            )
        )
    entries.sort(key=lambda e: e.datetime, reverse=True)
    return entries


def journey_chain(
    entries: list[RecordingEntry], base_filename: str, rtype: str
) -> list[RecordingEntry]:
    """returns the forward chain of contiguous same-type segments from a start."""
    same_type = sorted(
        (e for e in entries if e.type == rtype), key=lambda e: e.datetime
    )
    chain: list[RecordingEntry] = []
    started = False
    prev: RecordingEntry | None = None
    for entry in same_type:
        if not started:
            if entry.base_filename == base_filename:
                started, prev, chain = True, entry, [entry]
            continue
        assert prev is not None
        if 0 < (entry.datetime - prev.datetime).total_seconds() <= _CONTIGUOUS_GAP.total_seconds():
            chain.append(entry)
            prev = entry
        else:
            break
    return chain


__all__ = ["RecordingEntry", "journey_chain", "list_recordings"]
```

- [ ] **Step 4: Run to confirm pass.** `venv/bin/pytest test/test_viewer_index.py -q` -> 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add blackvuesync/server/viewer_index.py test/test_viewer_index.py
git commit -m "feat: add recording index and journey-chain logic"
```

---

### Task 4: `viewer` settings section

**Files:** Modify `blackvuesync/settings.py`, `blackvuesync/server/settings_form.py`; tests in `test/test_settings.py`, `test/test_settings_form.py`.

- [ ] **Step 1: Write the failing tests.** Append to `test/test_settings.py`:

```python
def test_viewer_section_defaults_and_validate() -> None:
    from blackvuesync.settings import Settings, ViewerSettings

    s = Settings()
    assert isinstance(s.viewer, ViewerSettings)
    assert s.viewer.journey_mode == "progressive"
    assert s.viewer.speed_unit == "kmh"
    assert ViewerSettings().validate() == []
    assert ViewerSettings(journey_mode="bogus").validate() == [  # type: ignore[arg-type]
        "viewer.journey_mode must be 'progressive' or 'full'"
    ]
    assert ViewerSettings(speed_unit="kn").validate() == [  # type: ignore[arg-type]
        "viewer.speed_unit must be 'kmh' or 'mph'"
    ]


def test_viewer_section_roundtrips_and_defaults_when_absent() -> None:
    import dataclasses

    from blackvuesync.settings import Settings, _settings_from_dict, _settings_to_dict

    s = Settings(viewer=dataclasses.replace(Settings().viewer, journey_mode="full"))
    raw = _settings_to_dict(s)
    assert raw["viewer"] == {"journey_mode": "full", "speed_unit": "kmh"}
    assert _settings_from_dict(raw).viewer.journey_mode == "full"
    assert _settings_from_dict({"version": 1}).viewer.journey_mode == "progressive"
```

Append to `test/test_settings_form.py`:

```python
def test_viewer_section_has_two_select_fields() -> None:
    from blackvuesync.server.settings_form import SECTION_FIELD_SPECS, SECTION_LABELS

    assert SECTION_LABELS["viewer"] == "Viewer"
    specs = {f.name: f for f in SECTION_FIELD_SPECS["viewer"]}
    assert specs["journey_mode"].widget == "select"
    assert specs["journey_mode"].options == ("progressive", "full")
    assert specs["speed_unit"].options == ("kmh", "mph")
```

- [ ] **Step 2: Confirm failure.** `venv/bin/pytest test/test_settings.py -q -k viewer` -> `ImportError: ViewerSettings`.

- [ ] **Step 3: Add `ViewerSettings` to `blackvuesync/settings.py`.** Add the dataclass immediately after `StatsSettings` (mirror its structure):

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

Add the field to `Settings` after `stats`:

```python
    viewer: ViewerSettings = field(default_factory=ViewerSettings)
```

Add to `Settings.validate` after the stats line: `errors.extend(self.viewer.validate())`.
Add to `_SECTION_FIELDS` after `"stats": StatsSettings,`: `"viewer": ViewerSettings,`.
In `_bootstrap_from_env`, after the `stats = StatsSettings(...)` block add `viewer = ViewerSettings()` and pass `viewer=viewer,` in the `Settings(...)` constructor.

- [ ] **Step 4: Add the form spec in `blackvuesync/server/settings_form.py`.** Add to `SECTION_FIELD_SPECS` after the `"stats": (...)` block:

```python
    "viewer": (
        FieldSpec(
            "journey_mode",
            "Journey accumulation",
            "select",
            "text",
            options=("progressive", "full"),
            help="progressive grows the map as you watch; full plots the whole route up front",
        ),
        FieldSpec("speed_unit", "Speed unit", "select", "text", options=("kmh", "mph")),
    ),
```

Add to `SECTION_LABELS` after `"stats": "Statistics",`: `"viewer": "Viewer",`.

- [ ] **Step 5: Run to confirm pass.** `venv/bin/pytest test/test_settings.py test/test_settings_form.py -q` -> PASS.

- [ ] **Step 6: Commit.**

```bash
git add blackvuesync/settings.py blackvuesync/server/settings_form.py test/test_settings.py test/test_settings_form.py
git commit -m "feat: add viewer settings section (journey_mode, speed_unit)"
```

---

### Task 5: Path-safe media route (`server/routes/media.py`)

**Files:** Create `blackvuesync/server/routes/media.py`, `test/test_routes_media.py`. Modify `server/__init__.py` (register the blueprint -- done here so the route tests can reach it).

- [ ] **Step 1: Write the failing tests.** Create `test/test_routes_media.py`:

```python
"""tests for the path-safe /media file route."""

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
def client_and_dest(tmp_path: Path):  # type: ignore[no-untyped-def]
    dest = tmp_path / "recordings"
    dest.mkdir()
    (dest / "20260607_101500_NF.mp4").write_bytes(b"video-bytes-here")
    (dest / "20260607_101500_NF.thm").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    (dest / "secret.json").write_text("{}")
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, password_hash=hash_password("pw-1234-test")),
            system=dataclasses.replace(s.system, destination=str(dest)),
        )
    )
    app = create_app(store, testing=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "admin"
    return client, dest


def test_serves_mp4(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.mp4")
    assert resp.status_code == 200
    assert resp.data == b"video-bytes-here"


def test_thm_served_as_jpeg(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.thm")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"


def test_range_request_returns_206(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.mp4", headers={"Range": "bytes=0-4"})
    assert resp.status_code == 206
    assert resp.data == b"video"


def test_traversal_rejected(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    assert client.get("/media/../settings.json").status_code == 404
    assert client.get("/media/%2e%2e%2fsettings.json").status_code == 404


def test_disallowed_extension_rejected(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    assert client.get("/media/secret.json").status_code == 404


def test_requires_login(client_and_dest: Any) -> None:
    _, dest = client_and_dest
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        anon = create_app(SettingsStore(dest.parent / "settings.json"), testing=True)
    resp = anon.test_client().get("/media/20260607_101500_NF.mp4")
    assert resp.status_code in (302, 401)
```

- [ ] **Step 2: Confirm failure.** `venv/bin/pytest test/test_routes_media.py -q` -> 404s everywhere / route missing.

- [ ] **Step 3: Implement `blackvuesync/server/routes/media.py`:**

```python
"""path-safe serving of recording files (.mp4/.thm) from the destination."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, send_from_directory
from werkzeug.security import safe_join

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore

media_bp = Blueprint("media_bp", __name__, url_prefix="/media")

_ALLOWED_SUFFIXES = (".mp4", ".thm")


@media_bp.route("/<path:relpath>", methods=["GET"])
@login_required
def media(relpath: str) -> Response:
    """serves a .mp4/.thm file under the destination; 404 on anything unsafe.

    layered defenses: extension allow-list, werkzeug safe_join (traversal),
    and a realpath-containment check (symlink escape). send_from_directory
    with conditional=True handles HTTP Range so the browser can stream/seek.
    """
    if not relpath.lower().endswith(_ALLOWED_SUFFIXES):
        abort(404)

    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination).resolve()

    joined = safe_join(str(destination), relpath)
    if joined is None:
        abort(404)
    resolved = Path(joined).resolve()
    if destination not in resolved.parents or not resolved.is_file():
        abort(404)

    mimetype = "image/jpeg" if resolved.suffix.lower() == ".thm" else None
    return send_from_directory(
        destination,
        os.path.relpath(resolved, destination),
        mimetype=mimetype,
        conditional=True,
    )


__all__ = ["media_bp"]
```

- [ ] **Step 4: Register the blueprint in `blackvuesync/server/__init__.py`.** In the deferred blueprint-import block add `from blackvuesync.server.routes.media import media_bp` and register it alongside the others: `app.register_blueprint(media_bp)`.

- [ ] **Step 5: Run to confirm pass.** `venv/bin/pytest test/test_routes_media.py -q` -> 6 passed.

- [ ] **Step 6: Commit.**

```bash
git add blackvuesync/server/routes/media.py blackvuesync/server/__init__.py test/test_routes_media.py
git commit -m "feat: add path-safe /media file route with range support"
```

---

### Task 6: Viewer JSON API (`server/routes/api_viewer.py`)

**Files:** Create `blackvuesync/server/routes/api_viewer.py`, `test/test_routes_api_viewer.py`. Modify `server/__init__.py` (register).

- [ ] **Step 1: Write the failing tests.** Create `test/test_routes_api_viewer.py`:

```python
"""tests for the /api/viewer JSON endpoints."""

from __future__ import annotations

import dataclasses
import json
import os
import struct
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def client_and_dest(tmp_path: Path):  # type: ignore[no-untyped-def]
    dest = tmp_path / "recordings"
    dest.mkdir()
    for name in ("20260607_101500_NF.mp4", "20260607_101500_NR.mp4", "20260607_101600_NF.mp4"):
        (dest / name).write_bytes(b"x")
    (dest / "20260607_101500_N.gps").write_text(
        "[1000]$GNRMC,055056.00,A,3348.10000,S,15101.10000,E,0.000,,070626,,,A,V*06\r\n"
    )
    (dest / "20260607_101500_N.3gf").write_bytes(struct.pack(">Ihhh", 0, 130, 5, -20))
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, password_hash=hash_password("pw-1234-test")),
            system=dataclasses.replace(s.system, destination=str(dest)),
        )
    )
    app = create_app(store, testing=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "admin"
    return client, dest


def test_recordings_grouped_newest_first(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    body = json.loads(client.get("/api/viewer/recordings").data)
    # two instants; newest (101600) first
    bases = [r["base_filename"] for day in body["days"] for r in day["recordings"]]
    assert bases == ["20260607_101600", "20260607_101500"]
    first = next(r for day in body["days"] for r in day["recordings"] if r["base_filename"] == "20260607_101500")
    assert first["directions"] == ["F", "R"]
    assert first["has_gps"] is True and first["has_3gf"] is True


def test_journey_chain(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    body = json.loads(client.get("/api/viewer/recordings/20260607_101500_N/journey").data)
    assert [s["base_filename"] for s in body["segments"]] == ["20260607_101500", "20260607_101600"]


def test_gps_and_gsensor_json(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    gps = json.loads(client.get("/api/viewer/recordings/20260607_101500_N/gps").data)
    assert gps["points"][0]["lat"] == -(33 + 48.1 / 60)
    g = json.loads(client.get("/api/viewer/recordings/20260607_101500_N/gsensor").data)
    assert g["samples"][0]["x"] == 130 / 128.0


def test_requires_login(client_and_dest: Any) -> None:
    _, dest = client_and_dest
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        anon = create_app(SettingsStore(dest.parent / "s2.json"), testing=True)
    assert anon.test_client().get("/api/viewer/recordings").status_code in (302, 401)
```

- [ ] **Step 2: Confirm failure.** `venv/bin/pytest test/test_routes_api_viewer.py -q` -> route missing.

- [ ] **Step 3: Implement `blackvuesync/server/routes/api_viewer.py`:**

```python
"""api routes for the dashcam viewer: recordings, journey chain, gps, gsensor."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from flask import Blueprint, Response, abort, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.server.gps import parse_gps
from blackvuesync.server.gsensor import parse_gsensor
from blackvuesync.server.viewer_index import RecordingEntry, journey_chain, list_recordings
from blackvuesync.settings import SettingsStore

api_viewer_bp = Blueprint("api_viewer_bp", __name__, url_prefix="/api/viewer")

_MIME_JSON = "application/json"


def _settings():  # type: ignore[no-untyped-def]
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    return store.get()


def _media_url(rel_dir: str, filename: str) -> str:
    """builds the /media URL for a file, respecting the grouping subdir."""
    rel = f"{rel_dir}/{filename}" if rel_dir else filename
    return f"/media/{rel}"


def _segment_dict(entry: RecordingEntry) -> dict[str, object]:
    """serializes one recording instant for the API."""
    return {
        "base_filename": entry.base_filename,
        "type": entry.type,
        "datetime": entry.datetime.isoformat(),
        "directions": list(entry.directions),
        "has_gps": entry.has_gps,
        "has_3gf": entry.has_3gf,
        "has_thm": entry.has_thm,
        "videos": {
            d: _media_url(entry.rel_dir, f"{entry.base_filename}_{entry.type}{d}.mp4")
            for d in entry.directions
        },
        "thumb": (
            _media_url(entry.rel_dir, f"{entry.base_filename}_{entry.type}{entry.directions[0]}.thm")
            if entry.has_thm and entry.directions
            else None
        ),
    }


def _find(entries: list[RecordingEntry], key: str) -> RecordingEntry | None:
    """resolves a `<base>_<type>` key (e.g. 20260607_101500_N) to an entry."""
    base, _, rtype = key.rpartition("_")
    for entry in entries:
        if entry.base_filename == base and entry.type == rtype:
            return entry
    return None


def _all_entries() -> list[RecordingEntry]:
    settings = _settings()
    return list_recordings(settings.system.destination, settings.sync.grouping)


def _sidecar_path(entry: RecordingEntry, suffix: str) -> Path:
    settings = _settings()
    rel = f"{entry.base_filename}_{entry.type}{suffix}"
    if entry.rel_dir:
        rel = os.path.join(entry.rel_dir, rel)
    return Path(settings.system.destination) / rel


@api_viewer_bp.route("/recordings", methods=["GET"])
@login_required
def recordings() -> Response:
    """returns recordings grouped by calendar day, newest day + item first."""
    entries = _all_entries()
    days: dict[str, list[dict[str, object]]] = {}
    for entry in entries:  # already newest-first
        days.setdefault(entry.datetime.date().isoformat(), []).append(_segment_dict(entry))
    body = json.dumps(
        {"days": [{"date": d, "recordings": recs} for d, recs in days.items()]}
    )
    return Response(body, status=200, mimetype=_MIME_JSON)


@api_viewer_bp.route("/recordings/<key>/journey", methods=["GET"])
@login_required
def journey(key: str) -> Response:
    """returns the forward chain of contiguous same-type segments from <key>."""
    entries = _all_entries()
    start = _find(entries, key)
    if start is None:
        abort(404)
    chain = journey_chain(entries, start.base_filename, start.type)
    body = json.dumps({"segments": [_segment_dict(e) for e in chain]})
    return Response(body, status=200, mimetype=_MIME_JSON)


@api_viewer_bp.route("/recordings/<key>/gps", methods=["GET"])
@login_required
def gps(key: str) -> Response:
    """returns the parsed GPS track for one recording instant."""
    entry = _find(_all_entries(), key)
    if entry is None or not entry.has_gps:
        abort(404)
    text = _sidecar_path(entry, ".gps").read_text(encoding="utf-8", errors="replace")
    points = [dataclasses.asdict(p) for p in parse_gps(text)]
    return Response(json.dumps({"points": points}), status=200, mimetype=_MIME_JSON)


@api_viewer_bp.route("/recordings/<key>/gsensor", methods=["GET"])
@login_required
def gsensor(key: str) -> Response:
    """returns the parsed G-sensor samples for one recording instant."""
    entry = _find(_all_entries(), key)
    if entry is None or not entry.has_3gf:
        abort(404)
    data = _sidecar_path(entry, ".3gf").read_bytes()
    samples = [dataclasses.asdict(s) for s in parse_gsensor(data)]
    return Response(json.dumps({"samples": samples}), status=200, mimetype=_MIME_JSON)


__all__ = ["api_viewer_bp"]
```

- [ ] **Step 4: Register in `blackvuesync/server/__init__.py`.** Add `from blackvuesync.server.routes.api_viewer import api_viewer_bp` to the blueprint-import block and `app.register_blueprint(api_viewer_bp)`.

- [ ] **Step 5: Run to confirm pass.** `venv/bin/pytest test/test_routes_api_viewer.py -q` -> 4 passed. Then `venv/bin/pytest test/ -q -m 'not e2e'` -> no regression.

- [ ] **Step 6: Commit.**

```bash
git add blackvuesync/server/routes/api_viewer.py blackvuesync/server/__init__.py test/test_routes_api_viewer.py
git commit -m "feat: add /api/viewer recordings, journey, gps, gsensor endpoints"
```

---

### Task 7: `/viewer` page route + template shell

**Files:** Modify `blackvuesync/server/routes/ui.py`; create `blackvuesync/server/templates/viewer.html`; delete `templates/_placeholders/viewer.html`; test in `test/test_routes_api_viewer.py` (append) or `test/test_routes_ui.py`.

- [ ] **Step 1: Write the failing test.** Append to `test/test_routes_api_viewer.py`:

```python
def test_viewer_page_renders(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/viewer")
    assert resp.status_code == 200
    assert b"js/viewer.js" in resp.data
    assert b"js/leaflet.js" in resp.data
    assert b'id="viewer-app"' in resp.data
    assert b"data-journey-mode" in resp.data  # settings passed to the client
```

- [ ] **Step 2: Confirm failure.** `venv/bin/pytest test/test_routes_api_viewer.py -q -k viewer_page` -> placeholder lacks these.

- [ ] **Step 3: Replace the `/viewer` route in `blackvuesync/server/routes/ui.py`:**

```python
@bp.route("/viewer", methods=["GET"])
@login_required
def viewer() -> str:
    """renders the dashcam viewer; recordings + telemetry hydrate client-side."""
    viewer_settings = current_app.settings_store.get().viewer  # type: ignore[attr-defined]
    return render_template(
        "viewer.html",
        version=__version__,
        page="viewer",
        journey_mode=viewer_settings.journey_mode,
        speed_unit=viewer_settings.speed_unit,
    )
```

- [ ] **Step 4: Create `blackvuesync/server/templates/viewer.html`:**

```html
{% extends "base.html" %}
{% block title %}Viewer -- BlackVue Sync{% endblock %}
{% block footer_version %}{{ version }}{% endblock %}

{% block extra_css %}
  <link rel="stylesheet" href="{{ url_for('static', filename='css/leaflet.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/viewer.css') }}">
{% endblock %}

{% block content %}
<div id="viewer-app" class="viewer" data-journey-mode="{{ journey_mode }}" data-speed-unit="{{ speed_unit }}">
  <aside class="viewer-sidebar" id="viewer-recordings" aria-label="Recordings"></aside>

  <section class="viewer-main">
    <div class="viewer-player" data-layout="pip">
      <video id="viewer-front" class="viewer-video viewer-video-primary" playsinline></video>
      <video id="viewer-rear" class="viewer-video viewer-video-secondary" playsinline muted></video>
    </div>

    <div class="viewer-transport">
      <button type="button" id="viewer-play" class="viewer-btn">Play</button>
      <input type="range" id="viewer-seek" class="viewer-seek" min="0" max="1000" value="0" aria-label="Seek">
      <span id="viewer-time" class="viewer-time">0:00 / 0:00</span>
      <button type="button" id="viewer-layout" class="viewer-btn">Side-by-side</button>
      <button type="button" id="viewer-swap" class="viewer-btn">Swap</button>
      <button type="button" id="viewer-next" class="viewer-btn">Next segment</button>
    </div>

    <div class="viewer-telemetry">
      <div id="viewer-map" class="viewer-map" aria-label="GPS map"></div>
      <div class="viewer-gauges">
        <div class="viewer-speed"><span id="viewer-speed-value">--</span> <span id="viewer-speed-unit">{{ speed_unit }}</span></div>
        <canvas id="viewer-gsensor" class="viewer-gsensor" height="90" aria-label="G-sensor chart"></canvas>
      </div>
    </div>
  </section>

  <noscript><p class="viewer-note">The dashcam viewer requires JavaScript.</p></noscript>
</div>
{% endblock %}

{% block extra_js %}
  <script src="{{ url_for('static', filename='js/leaflet.js') }}" defer></script>
  <script src="{{ url_for('static', filename='js/chart.umd.min.js') }}" defer></script>
  <script src="{{ url_for('static', filename='js/viewer.js') }}" defer></script>
{% endblock %}
```

- [ ] **Step 5: Delete the placeholder.** `git rm blackvuesync/server/templates/_placeholders/viewer.html`

- [ ] **Step 6: Run + commit.** `venv/bin/pytest test/test_routes_api_viewer.py test/test_routes_ui.py -q` -> PASS (`js/viewer.js` etc. resolve as static URLs even before Task 8/9 create the files).

```bash
git add blackvuesync/server/routes/ui.py blackvuesync/server/templates/viewer.html test/test_routes_api_viewer.py
git commit -m "feat: render the real /viewer page shell"
```

---

### Task 8: Vendor Leaflet

**Files:** Create `static/js/leaflet.js`, `static/css/leaflet.css`, `static/css/images/*`; modify `sonar-project.properties`, `.pre-commit-config.yaml`, `static/js/VENDORED.md`.

- [ ] **Step 1: Fetch Leaflet 1.9.4 (pinned).**

```bash
cd blackvuesync/server/static
curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.js -o js/leaflet.js
curl -fsSL https://unpkg.com/leaflet@1.9.4/dist/leaflet.css -o css/leaflet.css
mkdir -p css/images
for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
  curl -fsSL "https://unpkg.com/leaflet@1.9.4/dist/images/$img" -o "css/images/$img"
done
test -s js/leaflet.js && head -c 60 js/leaflet.js
ls -la css/images
```

Expected: a ~140 KB `leaflet.js` beginning with the Leaflet banner and 5 marker PNGs. Leaflet's CSS references `images/` relatively, so placing them under `css/images/` makes them resolve. **If the download is blocked**, STOP and ask the human to place Leaflet 1.9.4 `dist/` files at those paths -- do not hand-write them.

- [ ] **Step 2: Exclude vendored leaflet from Sonar.** In `sonar-project.properties` append `,**/leaflet.js` to the `sonar.exclusions=` line (it already excludes `**/chart.umd.min.js`).

- [ ] **Step 3: Exclude from the large-file pre-commit hook.** In `.pre-commit-config.yaml`, the `check-added-large-files` hook's `exclude` already lists `chart\.umd\.min\.js$`; extend it to also match `leaflet\.js$` and the marker PNGs (add `|leaflet\.js$|leaflet/.*\.png$|css/images/.*\.png$`). Verify the exact existing regex first and extend it minimally.

- [ ] **Step 4: Note the vendoring.** Append Leaflet 1.9.4 + source URL to `blackvuesync/server/static/js/VENDORED.md`.

- [ ] **Step 5: Commit.**

```bash
git add blackvuesync/server/static/js/leaflet.js blackvuesync/server/static/css/leaflet.css blackvuesync/server/static/css/images sonar-project.properties .pre-commit-config.yaml blackvuesync/server/static/js/VENDORED.md
git commit -m "build: vendor Leaflet 1.9.4 for the viewer map"
```

---

### Task 9: Viewer client part 1 -- sidebar, player, sync, transport

**Files:** Create `blackvuesync/server/static/js/viewer.js` (part 1) + `blackvuesync/server/static/css/viewer.css`.

**SonarCloud JS rules (apply throughout viewer.js):** prefer `globalThis` over `window`; no nested ternary; hoist helpers to module scope so no function nests >4 deep (S2004); optional chaining (`?.`/`??`); `Number.parseInt`/`Number.parseFloat`; `el.remove()` not `parent.removeChild`; non-empty `catch`; build DOM via `textContent`/`createElement` (never `innerHTML` for server data); no `role="img"` (use `aria-label`).

- [ ] **Step 1: Create `viewer.css`** (dark player, sidebar, telemetry grid). Full content:

```css
.viewer { display: grid; grid-template-columns: 220px 1fr; gap: var(--space-3, 12px); max-width: 1200px; margin: 0 auto; padding: var(--space-4, 16px); }
.viewer-sidebar { background: var(--color-surface, #fff); border-radius: var(--radius-lg, 12px); padding: var(--space-2, 8px); max-height: 80vh; overflow-y: auto; }
.viewer-day-label { font-size: 11px; text-transform: uppercase; color: var(--color-label-secondary, #6e6e73); margin: var(--space-2, 8px) 0 4px; }
.viewer-rec { display: flex; gap: 8px; align-items: center; width: 100%; border: 0; background: none; text-align: left; padding: 4px; border-radius: 6px; cursor: pointer; font-size: 12px; color: var(--color-label, #1d1d1f); }
.viewer-rec.active { background: var(--color-accent, #0071e3); color: #fff; }
.viewer-rec img { width: 48px; height: 30px; object-fit: cover; border-radius: 3px; background: #2c2c2e; }
.viewer-badge { margin-left: auto; background: #3a3a3c; color: #fff; border-radius: 4px; padding: 0 5px; font-size: 10px; }
.viewer-player { position: relative; background: #000; border-radius: 10px; overflow: hidden; aspect-ratio: 16 / 9; }
.viewer-player[data-layout="pip"] .viewer-video-primary { width: 100%; height: 100%; object-fit: contain; }
.viewer-player[data-layout="pip"] .viewer-video-secondary { position: absolute; right: 10px; bottom: 10px; width: 28%; border: 2px solid #fff; border-radius: 5px; }
.viewer-player[data-layout="sbs"] { display: grid; grid-template-columns: 1fr 1fr; aspect-ratio: auto; }
.viewer-player[data-layout="sbs"] .viewer-video { width: 100%; height: 100%; object-fit: contain; position: static; }
.viewer-transport { display: flex; align-items: center; gap: 8px; margin: 10px 0; }
.viewer-seek { flex: 1; }
.viewer-btn { border: 1px solid var(--color-separator, #d1d1d6); background: var(--color-surface, #fff); border-radius: 8px; padding: 4px 10px; font-size: 13px; cursor: pointer; }
.viewer-time { font-variant-numeric: tabular-nums; font-size: 12px; color: var(--color-label-secondary, #6e6e73); }
.viewer-telemetry { display: grid; grid-template-columns: 1.4fr 1fr; gap: var(--space-3, 12px); }
.viewer-map { height: 260px; border-radius: 10px; background: #2c2c2e; }
.viewer-gauges { background: var(--color-surface, #fff); border-radius: 10px; padding: var(--space-3, 12px); }
.viewer-speed { font-size: 28px; font-weight: 600; }
.viewer-note { color: var(--color-label-secondary, #6e6e73); }
@media (max-width: 720px) { .viewer { grid-template-columns: 1fr; } .viewer-telemetry { grid-template-columns: 1fr; } }
```

- [ ] **Step 2: Create `viewer.js` part 1** -- the module skeleton, recordings sidebar, segment loading, master/slave sync, transport, PIP/side-by-side. (Part 2 in Task 10 adds the map + telemetry + journey accumulation + auto-advance; it extends the same object.)

```javascript
// viewer.js: plain-JS dashcam viewer. loads recordings, plays front+rear in
// lockstep (front master, rear slaved), and (part 2) drives a Leaflet map +
// Chart.js telemetry off video.currentTime, accumulating across an
// auto-advanced journey. csp-clean: no eval, no innerHTML for server data.

const KMH_PER_KNOT = 1.852;
const MPH_PER_KNOT = 1.15078;
const DRIFT_TOLERANCE = 0.15; // seconds before re-pinning the slave video

function fmtTime(seconds) {
  const total = Math.floor(Number(seconds) || 0);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  return mins + ":" + String(secs).padStart(2, "0");
}

async function fetchJson(url) {
  try {
    const resp = await fetch(url, { headers: { Accept: "application/json" } });
    return resp.ok ? await resp.json() : null;
  } catch {
    // network error: caller keeps the current state
    return null;
  }
}

function recordingKey(rec) {
  return rec.base_filename + "_" + rec.type;
}

const viewer = {
  el: null,
  front: null,
  rear: null,
  player: null,
  speedUnit: "kmh",
  journeyMode: "progressive",
  chain: [], // the journey's segments
  index: 0, // current segment index within chain

  init() {
    this.el = document.getElementById("viewer-app");
    if (!this.el) return;
    this.front = document.getElementById("viewer-front");
    this.rear = document.getElementById("viewer-rear");
    this.player = this.el.querySelector(".viewer-player");
    this.speedUnit = this.el.dataset.speedUnit || "kmh";
    this.journeyMode = this.el.dataset.journeyMode || "progressive";
    this.bindTransport();
    this.bindSync();
    this.loadRecordings();
    this.initTelemetry(); // defined in part 2
  },

  async loadRecordings() {
    const data = await fetchJson("/api/viewer/recordings");
    const side = document.getElementById("viewer-recordings");
    side.replaceChildren();
    if (!data) return;
    for (const day of data.days) {
      const label = document.createElement("div");
      label.className = "viewer-day-label";
      label.textContent = day.date;
      side.append(label);
      for (const rec of day.recordings) {
        side.append(this.recRow(rec));
      }
    }
  },

  recRow(rec) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "viewer-rec";
    row.dataset.key = recordingKey(rec);
    if (rec.thumb) {
      const img = document.createElement("img");
      img.src = rec.thumb;
      img.alt = "";
      row.append(img);
    }
    const time = document.createElement("span");
    time.textContent = rec.datetime.slice(11, 16);
    const badge = document.createElement("span");
    badge.className = "viewer-badge";
    badge.textContent = rec.type;
    row.append(time, badge);
    row.addEventListener("click", () => this.selectRecording(rec));
    return row;
  },

  markActive(key) {
    this.el.querySelectorAll(".viewer-rec").forEach((row) => {
      row.classList.toggle("active", row.dataset.key === key);
    });
  },

  async selectRecording(rec) {
    this.markActive(recordingKey(rec));
    const journey = await fetchJson("/api/viewer/recordings/" + recordingKey(rec) + "/journey");
    this.chain = journey?.segments ?? [rec];
    this.index = 0;
    this.resetTelemetry(); // part 2
    await this.loadSegment(0, true);
  },

  async loadSegment(i, autoplay) {
    const seg = this.chain[i];
    if (!seg) return;
    this.index = i;
    this.front.src = seg.videos.F || seg.videos[seg.directions[0]];
    if (seg.videos.R) {
      this.rear.src = seg.videos.R;
      this.rear.style.display = "";
    } else {
      this.rear.removeAttribute("src");
      this.rear.style.display = "none";
    }
    await this.loadSegmentTelemetry(seg, i); // part 2
    if (autoplay) {
      this.front.play().catch(() => {
        // autoplay may be blocked until a user gesture; ignore
      });
    }
  },

  bindSync() {
    const sync = () => {
      if (!this.rear.src) return;
      if (Math.abs(this.rear.currentTime - this.front.currentTime) > DRIFT_TOLERANCE) {
        this.rear.currentTime = this.front.currentTime;
      }
    };
    this.front.addEventListener("play", () => {
      if (this.rear.src) this.rear.play().catch(() => { /* slave play blocked; ignore */ });
    });
    this.front.addEventListener("pause", () => this.rear.pause());
    this.front.addEventListener("seeking", sync);
    this.front.addEventListener("ratechange", () => {
      this.rear.playbackRate = this.front.playbackRate;
    });
    this.front.addEventListener("timeupdate", () => {
      this.updateTimeUi();
      sync();
      this.onTick(); // part 2: map marker + telemetry cursor
    });
    this.front.addEventListener("ended", () => this.onSegmentEnded()); // part 2
  },

  bindTransport() {
    document.getElementById("viewer-play").addEventListener("click", () => {
      if (this.front.paused) this.front.play().catch(() => { /* ignore */ });
      else this.front.pause();
    });
    document.getElementById("viewer-seek").addEventListener("input", (ev) => {
      const frac = Number(ev.currentTarget.value) / 1000;
      if (this.front.duration) this.front.currentTime = frac * this.front.duration;
    });
    document.getElementById("viewer-layout").addEventListener("click", () => {
      const pip = this.player.dataset.layout === "pip";
      this.player.dataset.layout = pip ? "sbs" : "pip";
    });
    document.getElementById("viewer-swap").addEventListener("click", () => {
      this.front.classList.toggle("viewer-video-primary");
      this.front.classList.toggle("viewer-video-secondary");
      this.rear.classList.toggle("viewer-video-primary");
      this.rear.classList.toggle("viewer-video-secondary");
    });
    document.getElementById("viewer-next").addEventListener("click", () => this.onSegmentEnded());
  },

  updateTimeUi() {
    const seek = document.getElementById("viewer-seek");
    if (this.front.duration) {
      seek.value = String(Math.round((this.front.currentTime / this.front.duration) * 1000));
    }
    document.getElementById("viewer-time").textContent =
      fmtTime(this.front.currentTime) + " / " + fmtTime(this.front.duration);
  },

  // part-2 hooks (defined in Task 10); harmless no-ops until then
  initTelemetry() {},
  resetTelemetry() {},
  loadSegmentTelemetry() {},
  onTick() {},
  onSegmentEnded() {},
};

document.addEventListener("DOMContentLoaded", () => viewer.init());
```

- [ ] **Step 3: Syntax check + commit.** `node --check blackvuesync/server/static/js/viewer.js` (if node exists) -> clean.

```bash
git add blackvuesync/server/static/js/viewer.js blackvuesync/server/static/css/viewer.css
git commit -m "feat: viewer client part 1 (sidebar, player, master/slave sync)"
```

---

### Task 10: Viewer client part 2 -- map, telemetry, journey accumulation, auto-advance

**Files:** Modify `blackvuesync/server/static/js/viewer.js` (replace the part-2 no-op hooks with real implementations).

- [ ] **Step 1: Replace the part-2 hook stubs** at the bottom of the `viewer` object with these methods (delete the five no-op stubs `initTelemetry/resetTelemetry/loadSegmentTelemetry/onTick/onSegmentEnded` and add the implementations + supporting state fields `map`, `pathLayer`, `marker`, `gsChart`, `track`, `segmentOffsets`):

```javascript
  // --- telemetry state ---
  map: null,
  pathLayer: null,
  marker: null,
  gsChart: null,
  track: [], // accumulated {st: session-time s, lat, lon, speed} across the journey
  gforce: [], // accumulated {st, mag} for the g-sensor chart
  offsets: [], // cumulative session-time offset (s) at the start of each segment

  initTelemetry() {
    const L = globalThis.L;
    this.map = L.map("viewer-map");
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
    }).addTo(this.map);
    this.map.setView([0, 0], 2);
    this.gsChart = new globalThis.Chart(document.getElementById("viewer-gsensor"), {
      type: "line",
      data: { labels: [], datasets: [{ label: "G", data: [], pointRadius: 0, borderColor: "#ff9f0a" }] },
      options: { responsive: true, animation: false, plugins: { legend: { display: false } }, scales: { x: { display: false } } },
    });
  },

  resetTelemetry() {
    this.track = [];
    this.gforce = [];
    this.offsets = [];
    if (this.pathLayer) { this.pathLayer.remove(); this.pathLayer = null; }
    if (this.marker) { this.marker.remove(); this.marker = null; }
  },

  segmentOffset(i) {
    // cumulative session time at the start of segment i (uses prior durations)
    let total = 0;
    for (let k = 0; k < i; k += 1) total += this.offsets[k] || 0;
    return total;
  },

  async loadSegmentTelemetry(seg, i) {
    const key = seg.base_filename + "_" + seg.type;
    const offset = this.segmentOffset(i);
    if (seg.has_gps) {
      const gps = await fetchJson("/api/viewer/recordings/" + key + "/gps");
      for (const p of gps?.points ?? []) {
        this.track.push({ st: offset + p.t, lat: p.lat, lon: p.lon, speed: p.speed });
      }
    }
    if (seg.has_3gf) {
      const gs = await fetchJson("/api/viewer/recordings/" + key + "/gsensor");
      for (const s of gs?.samples ?? []) {
        this.gforce.push({ st: offset + s.t, mag: Math.hypot(s.x, s.y, s.z) });
      }
    }
    this.redrawTrack();
    // record this segment's duration once known, for the next offset
    const recordDuration = () => { this.offsets[i] = this.front.duration || 60; };
    if (this.front.readyState >= 1) recordDuration();
    else this.front.addEventListener("loadedmetadata", recordDuration, { once: true });
    if (this.journeyMode === "full") this.prefetchRest(i);
  },

  async prefetchRest(fromIndex) {
    // full mode: eagerly load the remaining chain's telemetry up front
    for (let i = fromIndex + 1; i < this.chain.length; i += 1) {
      this.offsets[i] = this.offsets[i] || 60;
      await this.loadSegmentTelemetry(this.chain[i], i);
    }
  },

  redrawTrack() {
    const L = globalThis.L;
    const latlngs = this.track.filter((p) => p.lat != null).map((p) => [p.lat, p.lon]);
    if (!latlngs.length) return;
    if (this.pathLayer) this.pathLayer.remove();
    this.pathLayer = L.polyline(latlngs, { color: "#0a84ff", weight: 3 }).addTo(this.map);
    this.map.fitBounds(this.pathLayer.getBounds(), { padding: [20, 20] });
    if (!this.marker) this.marker = L.circleMarker(latlngs[0], { radius: 6, color: "#fff", fillColor: "#0a84ff", fillOpacity: 1 }).addTo(this.map);
    const labels = this.gforce.map(() => "");
    this.gsChart.data.labels = labels;
    this.gsChart.data.datasets[0].data = this.gforce.map((g) => g.mag);
    this.gsChart.update("none");
  },

  nearest(sessionTime) {
    // nearest accumulated track point to a session time (linear scan; tracks are small)
    let best = null;
    let bestDelta = Infinity;
    for (const p of this.track) {
      const delta = Math.abs(p.st - sessionTime);
      if (delta < bestDelta) { bestDelta = delta; best = p; }
    }
    return best;
  },

  onTick() {
    const sessionTime = this.segmentOffset(this.index) + this.front.currentTime;
    const point = this.nearest(sessionTime);
    if (point && this.marker) this.marker.setLatLng([point.lat, point.lon]);
    if (point) {
      const knots = point.speed ?? 0;
      const factor = this.speedUnit === "mph" ? MPH_PER_KNOT : KMH_PER_KNOT;
      document.getElementById("viewer-speed-value").textContent = String(Math.round(knots * factor));
    }
  },

  onSegmentEnded() {
    const next = this.index + 1;
    if (next < this.chain.length) this.loadSegment(next, true);
  },
```

- [ ] **Step 2: Remove the five no-op stub methods** from part 1 (they are now real). Ensure the object has no duplicate keys (`node --check` will not catch duplicate object keys, so grep: `grep -n "initTelemetry\|resetTelemetry\|loadSegmentTelemetry\|onTick\|onSegmentEnded" blackvuesync/server/static/js/viewer.js` -> each name should appear exactly twice: once as the call site and once as the definition; if a stub remains, delete it).

- [ ] **Step 3: Syntax check.** `node --check blackvuesync/server/static/js/viewer.js` -> clean.

- [ ] **Step 4: Commit.**

```bash
git add blackvuesync/server/static/js/viewer.js
git commit -m "feat: viewer client part 2 (map, telemetry, journey accumulation)"
```

---

### Task 11: End-to-end Playwright smoke

**Files:** Create `test/e2e/test_viewer_page.py`. Possibly extend `test/e2e/conftest.py` to seed viewer fixtures.

- [ ] **Step 1: Write the e2e test.** Read `test/e2e/conftest.py` (the `live_server` fixture) + `test/e2e/test_stats_page.py` for the login helper. Seed a couple of tiny synthetic recordings in the live-server destination before navigating. A minimal valid MP4 is hard to fabricate; the smoke asserts the page wires up + no JS error rather than actual playback:

```python
"""playwright smoke for the /viewer page."""

from __future__ import annotations

import struct

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _seed(dest) -> None:  # type: ignore[no-untyped-def]
    (dest / "20260607_101500_NF.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_NR.mp4").write_bytes(b"\x00")
    (dest / "20260607_101500_NF.thm").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    (dest / "20260607_101500_N.gps").write_text(
        "[1000]$GNRMC,055056.00,A,3348.10000,S,15101.10000,E,0.000,,070626,,,A,V*06\r\n"
    )
    (dest / "20260607_101500_N.3gf").write_bytes(struct.pack(">Ihhh", 0, 130, 5, -20))


def test_viewer_loads_lists_and_selects_no_js_errors(live_server, page: Page) -> None:  # type: ignore[no-untyped-def]
    _seed(live_server.destination)  # conftest exposes the destination path on the fixture
    base = live_server.url
    # log in exactly as test_stats_page.py does
    page.goto(f"{base}/login")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "pw-1234-test")
    page.click('button[type="submit"]')

    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    with page.expect_response(lambda r: "/api/viewer/recordings" in r.url):
        page.goto(f"{base}/viewer")
    expect(page.locator("#viewer-app")).to_be_visible()
    expect(page.locator(".viewer-rec").first).to_be_visible()

    with page.expect_response(lambda r: "/journey" in r.url):
        page.locator(".viewer-rec").first.click()
    expect(page.locator(".viewer-map .leaflet-container")).to_be_visible()

    page.wait_for_load_state("networkidle")
    assert errors == [], f"uncaught page errors: {errors}"
```

If `live_server` does not expose `destination`, extend the fixture in `conftest.py` to set `server.destination = destination` (mirror how it sets `server.url`); keep existing e2e tests green. If the login helper differs, mirror the sibling exactly.

- [ ] **Step 2: Run.** `venv/bin/pytest test/e2e/test_viewer_page.py -m e2e -v` -> PASS. A real `pageerror` is a genuine bug in viewer.js/Leaflet -- STOP and fix, do not silence.

- [ ] **Step 3: Commit.**

```bash
git add test/e2e/test_viewer_page.py test/e2e/conftest.py
git commit -m "test: add e2e smoke for the /viewer page"
```

---

### Task 12: Docs, version, mypy, full verification

**Files:** Modify `docs/api.md`, `pyproject.toml`.

- [ ] **Step 1: Document the endpoints in `docs/api.md`.** Read the file for its heading/table style, then add a "Viewer API" section (matching the Stats/Logs sections):

```markdown
## Viewer API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/viewer/recordings` | recordings grouped by day, newest first |
| GET | `/api/viewer/recordings/<base>_<type>/journey` | forward chain of contiguous segments |
| GET | `/api/viewer/recordings/<base>_<type>/gps` | parsed GPS points `[{t, lat, lon, speed}]` |
| GET | `/api/viewer/recordings/<base>_<type>/gsensor` | parsed G-sensor `[{t, x, y, z}]` |
| GET | `/media/<path>` | path-safe `.mp4`/`.thm` serving (HTTP Range) |

Login required. See `docs/reference/blackvue-file-formats.md` for the underlying
file formats. `viewer.journey_mode` / `viewer.speed_unit` settings tune the page.
```

- [ ] **Step 2: Bump version + mypy.** In `pyproject.toml` bump `version` to the next alpha (read current `2.7.x` and bump the minor to `2.8.0a0`). Run `venv/bin/pre-commit run mypy --all-files`; if it flags any new test module (`test_gps`/`test_gsensor`/`test_viewer_index`/`test_routes_media`/`test_routes_api_viewer`), add only the flagged ones to the existing `[[tool.mypy.overrides]]` test-module list, following the pattern.

- [ ] **Step 3: Full verification.**

```bash
venv/bin/pytest test/ -q -m 'not e2e'
venv/bin/pytest test/e2e/test_viewer_page.py -m e2e -q
venv/bin/pre-commit run --all-files
```

All must pass. Fix + re-stage on any hook failure (never `--no-verify`). Report any pre-existing unrelated failure without fixing unrelated code.

- [ ] **Step 4: Commit.**

```bash
git add docs/api.md pyproject.toml
git commit -m "docs: document viewer API; bump version for sub-project #6"
```

- [ ] **Step 5: Push + PR (via REST API; gh defaults to upstream).**

```bash
git push -u origin sub-project-6-viewer
gh api repos/tekgnosis-net/blackvuesync/pulls -X POST -f title="Sub-Project #6: Dashcam viewer" -f head="sub-project-6-viewer" -f base="main" -f body="<summary>"
```

Monitor the 5 required checks + the e2e job (`gh run list -R tekgnosis-net/blackvuesync`). After CI, verify **0 SonarCloud findings** via the issues API (`curl "https://sonarcloud.io/api/issues/search?componentKeys=tekgnosis-net_blackvuesync&pullRequest=<N>&resolved=false&ps=100"`); fix any findings (new commit) and re-verify. Squash-merge (linear history).

---

## Self-Review

**1. Spec coverage:** `.gps` parser -> Task 1; `.3gf` parser -> Task 2; enumeration + journey chain -> Task 3; `viewer` settings section + form -> Task 4; path-safe media route -> Task 5; `/api/viewer/*` -> Task 6; `/viewer` page + template -> Task 7; vendored Leaflet + reused Chart.js + excludes -> Task 8; player/sync/transport/PIP-SBS -> Task 9; map + telemetry + journey accumulation (progressive + full) + auto-advance -> Task 10; e2e -> Task 11; reference doc -> already committed (validated/confirmed in Tasks 1-2); docs/version/mypy -> Task 12. CSP unchanged (no task). Speed-unit conversion -> Task 10 `onTick`. Graceful degradation (no gps/offline/stationary) -> Task 10 (`redrawTrack` returns early on empty `latlngs`; map still inits; video plays regardless).

**2. Placeholder scan:** none -- every code step has complete code; the `.3gf` `SCALE_G` is a concrete constant validated in Task 2 Step 5.

**3. Type/name consistency:** `RecordingEntry` fields (Task 3) match `_segment_dict` (Task 6) and the client's `rec.*`/`seg.*` access (Tasks 9-10). `GpsPoint`/`GForce` field names (`t,lat,lon,speed` / `t,x,y,z`) match the API JSON (Task 6) and the client (`p.t/p.lat/...`, `s.x/...`) in Task 10. The recording key format `<base>_<type>` is consistent across `_find` (Task 6), the route URLs, and `recordingKey()` (Task 9). `journey_chain(entries, base_filename, rtype)` signature matches its call in Task 6. The part-1 no-op hooks (Task 9) are replaced by real methods of the same names (Task 10) -- Task 10 Step 2 explicitly removes the stubs to avoid duplicate object keys.

**End of plan.**
