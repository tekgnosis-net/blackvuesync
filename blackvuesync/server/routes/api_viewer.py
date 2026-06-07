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
from blackvuesync.server.viewer_index import (
    RecordingEntry,
    journey_chain,
    list_recordings,
)
from blackvuesync.settings import Settings, SettingsStore

api_viewer_bp = Blueprint("api_viewer_bp", __name__, url_prefix="/api/viewer")

_MIME_JSON = "application/json"


def _settings() -> Settings:
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
            _media_url(
                entry.rel_dir,
                f"{entry.base_filename}_{entry.type}{entry.directions[0]}.thm",
            )
            if entry.has_thm and entry.directions
            else None
        ),
    }


def _all_entries() -> list[RecordingEntry]:
    settings = _settings()
    return list_recordings(settings.system.destination, settings.sync.grouping)


def _find(entries: list[RecordingEntry], key: str) -> RecordingEntry | None:
    """resolves a `<base>_<type>` key (e.g. 20260607_101500_N) to an entry."""
    base, _, rtype = key.rpartition("_")
    for entry in entries:
        if entry.base_filename == base and entry.type == rtype:
            return entry
    return None


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
        days.setdefault(entry.datetime.date().isoformat(), []).append(
            _segment_dict(entry)
        )
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
