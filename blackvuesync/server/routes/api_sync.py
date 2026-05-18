"""api sync routes: /api/sync/progress, /api/sync/progress/stream, /api/sync/now, /api/sync/last."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator

from flask import Blueprint, Response, current_app, stream_with_context

from blackvuesync.server.auth import login_required
from blackvuesync.server.progress import FileProgress, ProgressPublisher, SyncProgress
from blackvuesync.server.sync_runner import trigger_sync

# manual smoke test:
#   curl -N -H "Cookie: bvs_session=..." http://localhost:8080/api/sync/progress/stream
# then in another terminal trigger a sync via /api/sync/now and watch the stream.

api_sync_bp = Blueprint("api_sync_bp", __name__, url_prefix="/api/sync")


def _publisher() -> ProgressPublisher:
    """returns the app-level progress publisher."""
    pub: ProgressPublisher = current_app.progress_publisher  # type: ignore[attr-defined]
    return pub


def _file_progress_to_dict(fp: FileProgress) -> dict[str, object]:
    """converts a FileProgress snapshot to a dict, including computed properties."""
    d: dict[str, object] = dataclasses.asdict(fp)
    d["percent"] = fp.percent
    d["elapsed_seconds"] = fp.elapsed_seconds
    return d


def _snap_to_dict(snap: SyncProgress) -> dict[str, object]:
    """converts a SyncProgress snapshot to a JSON-serializable dict.

    includes computed properties (percent, elapsed_seconds) so the ui
    does not need to recalculate them from raw fields.
    """
    d: dict[str, object] = dataclasses.asdict(snap)
    d["percent"] = snap.percent
    # replaces the nested current_file dict with one that also has computed props
    if snap.current_file is not None:
        d["current_file"] = _file_progress_to_dict(snap.current_file)
    return d


@api_sync_bp.route("/progress", methods=["GET"])
@login_required
def progress_snapshot() -> Response:
    """returns the current sync progress as JSON."""
    snap = _publisher().snapshot()
    return Response(
        json.dumps(_snap_to_dict(snap), default=str),
        status=200,
        mimetype="application/json",
    )


@api_sync_bp.route("/progress/stream", methods=["GET"])
@login_required
def progress_stream() -> Response:
    """streams sync progress as Server-Sent Events.

    emits event: progress\\ndata: <json>\\n\\n on each state change
    (throttled to PUBLISH_HZ). emits a keepalive comment (: keepalive)
    every 30 seconds when no events arrive.
    """
    pub = _publisher()

    def _sse_events() -> Iterator[bytes]:
        last_event_monotonic: float = -1.0
        for snap in pub.subscribe():
            if snap.last_event_monotonic == last_event_monotonic:
                # same snapshot repeated -- no new events; emit keepalive comment
                yield b": keepalive\n\n"
            else:
                last_event_monotonic = snap.last_event_monotonic
                snap_dict = _snap_to_dict(snap)
                payload = json.dumps(snap_dict, default=str)
                yield f"event: progress\ndata: {payload}\n\n".encode()

    resp = Response(stream_with_context(_sse_events()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Transfer-Encoding"] = "chunked"
    return resp


@api_sync_bp.route("/now", methods=["POST"])
@login_required
def trigger_now() -> Response:
    """triggers an on-demand sync; returns 202 or 409 if already running.

    flask-wtf csrfprotect validates the X-CSRFToken header globally for all
    post requests; a missing or invalid token causes a 400 before this handler
    runs.
    """
    pub = _publisher()
    settings_store = current_app.settings_store  # type: ignore[attr-defined]
    settings = settings_store.get()

    result = trigger_sync(settings, pub)

    if result["status"] == "already_running":
        body = json.dumps(
            {
                "error": "sync already running",
                "code": "SYNC_ALREADY_RUNNING",
                "details": {"current_job_id": result["job_id"]},
            }
        )
        return Response(body, status=409, mimetype="application/json")

    body = json.dumps({"job_id": result["job_id"]})
    return Response(body, status=202, mimetype="application/json")


@api_sync_bp.route("/last", methods=["GET"])
@login_required
def last_sync() -> Response:
    """returns the most recently completed sync snapshot; 204 if none."""
    snap = _publisher().snapshot()
    if snap.state == "idle":
        return Response(status=204)
    body = json.dumps(_snap_to_dict(snap), default=str)
    return Response(body, status=200, mimetype="application/json")


__all__ = ["api_sync_bp"]
