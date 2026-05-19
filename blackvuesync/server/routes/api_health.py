"""api health routes: storage and dashcam health probes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, Response, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore
from blackvuesync.sync import filename_re

api_health_bp = Blueprint("api_health_bp", __name__, url_prefix="/api/health")

_MIME_JSON = "application/json"


def _count_recordings(destination: Path) -> int:
    """counts files in destination whose name matches the BlackVue regex."""
    count = 0
    for _, _, files in os.walk(destination):
        for name in files:
            if filename_re.match(name):
                count += 1
    return count


def _compute_storage(destination: Path) -> dict[str, object]:
    """computes storage stats for destination; returns a JSON-serializable dict.

    factored out so /api/health/storage and /hx/storage-card both share the
    same computation. when destination does not exist, returns
    {available: False, reason: ...} matching the structural-case contract.
    """
    if not destination.exists():
        return {"available": False, "reason": "destination not configured"}

    stats = os.statvfs(destination)
    total_bytes = stats.f_blocks * stats.f_frsize
    free_bytes = stats.f_bavail * stats.f_frsize
    used_bytes = total_bytes - free_bytes
    used_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0.0
    return {
        "available": True,
        "destination": str(destination),
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "used_bytes": used_bytes,
        "used_percent": used_percent,
        "recording_count": _count_recordings(destination),
    }


@api_health_bp.route("/storage", methods=["GET"])
@login_required
def storage() -> Response:
    """returns storage usage at the destination directory."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    destination = Path(store.get().system.destination)
    body = json.dumps(_compute_storage(destination))
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_health_bp"]
