"""tests for byte-range resume in download_file."""

from __future__ import annotations

import re
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

import blackvuesync.sync as _sync
from blackvuesync.sync import download_file

_PAYLOAD = b"".join(bytes([i % 256]) for i in range(1024 * 7))  # 7 KiB
_FILENAME = "20230101_120000_NF.mp4"
_RANGE_RE = re.compile(r"bytes=(\d+)-")


class _RangeHandler(BaseHTTPRequestHandler):
    """serves _PAYLOAD honoring a single open-ended Range with 206."""

    seen_range: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        rng = self.headers.get("Range")
        type(self).seen_range = rng
        if rng and (m := _RANGE_RE.fullmatch(rng.strip())):
            start = int(m.group(1))
            body = _PAYLOAD[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Range", f"bytes {start}-{len(_PAYLOAD) - 1}/{len(_PAYLOAD)}"
            )
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(_PAYLOAD)))
        self.end_headers()
        self.wfile.write(_PAYLOAD)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        """silences test server logging."""


class _NoRangeHandler(BaseHTTPRequestHandler):
    """ignores Range; always returns the whole payload with 200."""

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(_PAYLOAD)))
        self.end_headers()
        self.wfile.write(_PAYLOAD)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        """silences test server logging."""


def _serve(handler: type[BaseHTTPRequestHandler]) -> Generator[str, None, None]:
    handler.seen_range = None  # type: ignore[attr-defined]
    server = HTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()


@pytest.fixture()
def range_server() -> Generator[str, None, None]:
    yield from _serve(_RangeHandler)


@pytest.fixture()
def norange_server() -> Generator[str, None, None]:
    yield from _serve(_NoRangeHandler)


def _seed_partial(destination: Path, nbytes: int) -> None:
    (destination / f".{_FILENAME}").write_bytes(_PAYLOAD[:nbytes])


def _final(destination: Path) -> bytes:
    return (destination / _FILENAME).read_bytes()


class TestResume:
    def test_resumes_from_partial_with_206(
        self, range_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 3000)
        ok, _ = download_file(range_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        assert _final(tmp_path) == _PAYLOAD
        assert _RangeHandler.seen_range == "bytes=3000-"

    def test_only_tail_transferred_on_resume(
        self, range_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 5000)
        totals: list[int] = []
        download_file(
            range_server,
            _FILENAME,
            str(tmp_path),
            None,
            on_chunk=lambda _d, t: totals.append(t),
        )
        # on_chunk total reflects the whole file, not the 2 KiB tail
        assert totals and all(t == len(_PAYLOAD) for t in totals)

    def test_no_range_header_when_no_partial(
        self, range_server: str, tmp_path: Path
    ) -> None:
        ok, _ = download_file(range_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        assert _final(tmp_path) == _PAYLOAD
        assert _RangeHandler.seen_range is None

    def test_falls_back_to_full_download_when_server_ignores_range(
        self, norange_server: str, tmp_path: Path
    ) -> None:
        _seed_partial(tmp_path, 4000)
        ok, _ = download_file(norange_server, _FILENAME, str(tmp_path), None)
        assert ok is True
        # a 200 truncates the partial and rewrites cleanly
        assert _final(tmp_path) == _PAYLOAD

    def test_stop_mid_resume_keeps_partial(
        self, range_server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_partial(tmp_path, 1000)
        monkeypatch.setattr(_sync, "is_stop_requested", lambda: True)
        with pytest.raises(UserWarning):
            download_file(range_server, _FILENAME, str(tmp_path), None)
        # partial survives (and is at least the seeded size); no final file
        assert (tmp_path / f".{_FILENAME}").exists()
        assert not (tmp_path / _FILENAME).exists()
