"""shared Server-Sent Events response helper."""

from __future__ import annotations

from collections.abc import Iterator

from flask import Response, stream_with_context


def sse_response(events: Iterator[bytes]) -> Response:
    """wraps an SSE byte-frame generator in a streaming text/event-stream
    response with the headers that defeat proxy buffering (Cache-Control,
    X-Accel-Buffering, Transfer-Encoding).
    """
    resp = Response(stream_with_context(events), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Transfer-Encoding"] = "chunked"
    return resp
