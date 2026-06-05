"""api logs routes: /api/logs/recent (snapshot) and /api/logs/stream (SSE)."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator

from flask import Blueprint, Response, current_app, stream_with_context

from blackvuesync.server.auth import login_required
from blackvuesync.server.log_buffer import LogBuffer, verbosity_token

api_logs_bp = Blueprint("api_logs_bp", __name__, url_prefix="/api/logs")

_MIME_JSON = "application/json"


def _buffer() -> LogBuffer:
    """returns the app-level log buffer."""
    buf: LogBuffer = current_app.log_buffer  # type: ignore[attr-defined]
    return buf


def _current_verbosity() -> str:
    """returns the current verbosity token from the logging settings."""
    store = current_app.settings_store  # type: ignore[attr-defined]
    return verbosity_token(store.get().logging)


@api_logs_bp.route("/recent", methods=["GET"])
@login_required
def recent() -> Response:
    """returns the buffered log lines plus viewer metadata as JSON."""
    buf = _buffer()
    body = json.dumps(
        {
            "lines": [dataclasses.asdict(ln) for ln in buf.snapshot()],
            "file_path": current_app.log_file_path or "",  # type: ignore[attr-defined]
            "capacity": buf.capacity,
            "verbosity": _current_verbosity(),
        }
    )
    return Response(body, status=200, mimetype=_MIME_JSON)


@api_logs_bp.route("/stream", methods=["GET"])
@login_required
def stream() -> Response:
    """streams new log lines as Server-Sent Events.

    emits event: logs\\ndata: {"lines":[...]}\\n\\n per batch; a ": keepalive"
    comment every HEARTBEAT_SECONDS when no lines arrive.
    """
    buf = _buffer()

    def _sse_events() -> Iterator[bytes]:
        # register the subscriber first, then snapshot: a line emitted in the
        # gap then lands in the subscriber queue (never lost) and merely
        # duplicates the snapshot, which the client de-duplicates by seq.
        batches = buf.subscribe()
        initial = buf.snapshot()
        if initial:
            payload = json.dumps({"lines": [dataclasses.asdict(ln) for ln in initial]})
            yield f"event: logs\ndata: {payload}\n\n".encode()
        for batch in batches:
            if not batch:
                yield b": keepalive\n\n"
            else:
                payload = json.dumps(
                    {"lines": [dataclasses.asdict(ln) for ln in batch]}
                )
                yield f"event: logs\ndata: {payload}\n\n".encode()

    resp = Response(stream_with_context(_sse_events()), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Transfer-Encoding"] = "chunked"
    return resp


__all__ = ["api_logs_bp"]
