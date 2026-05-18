"""tests for sync_runner: locking, daemon thread lifecycle, 409-on-already-running."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.sync_runner import _sync_lock, trigger_sync

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_sync_lock() -> Generator[None, None, None]:
    """ensures the sync lock is released between tests."""
    yield
    # release the lock if a test left it acquired
    if _sync_lock.locked():
        with contextlib.suppress(RuntimeError):
            _sync_lock.release()


def _make_settings(**overrides: Any) -> MagicMock:
    """creates a minimal mock settings object for sync_runner tests."""
    s = MagicMock()
    s.connection.address = "192.168.1.1"
    s.connection.timeout_seconds = 5.0
    s.system.destination = "/tmp/bvs-test-dest"
    s.system.dry_run = False
    s.sync.grouping = "none"
    s.sync.priority = "date"
    s.sync.include = None
    s.sync.exclude = None
    s.retention.max_used_disk_percent = 90
    for key, val in overrides.items():
        setattr(s, key, val)
    return s


def _noop(
    _settings: Any,
    pub: ProgressPublisher,
    _msf: Any,
    *,
    job_id: str,
) -> None:
    """no-op _do_sync stub; simulates sync.py owning begin_job/end_job."""
    pub.begin_job(0, job_id=job_id)
    pub.end_job(success=True)


def _slow_noop(
    _settings: Any,
    pub: ProgressPublisher,
    _msf: Any,
    *,
    job_id: str,
    started: threading.Event,
    proceed: threading.Event,
) -> None:
    """slow _do_sync stub; waits for proceed before ending the job."""
    pub.begin_job(0, job_id=job_id)
    started.set()
    proceed.wait(timeout=5.0)
    pub.end_job(success=True)


# ---------------------------------------------------------------------------
# trigger_sync: basic behavior
# ---------------------------------------------------------------------------


class TestTriggerSync:
    """tests for trigger_sync return values and locking behavior."""

    def test_first_call_returns_started(self) -> None:
        pub = ProgressPublisher()
        settings = _make_settings()

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_noop):
            result = trigger_sync(settings, pub)

        time.sleep(0.05)  # let the thread finish
        assert result["status"] == "started"
        assert len(result["job_id"]) == 32

    def test_second_concurrent_call_returns_already_running(self) -> None:
        pub = ProgressPublisher()
        settings = _make_settings()
        started = threading.Event()
        proceed = threading.Event()

        def _slow(
            s: Any,
            p: ProgressPublisher,
            msf: Any,
            *,
            job_id: str,  # noqa: ARG001
        ) -> None:
            _slow_noop(s, p, msf, job_id=job_id, started=started, proceed=proceed)

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_slow):
            result1 = trigger_sync(settings, pub)
            started.wait(timeout=2.0)  # wait until the thread is running
            result2 = trigger_sync(settings, pub)

        proceed.set()  # let the first sync complete
        time.sleep(0.1)

        assert result1["status"] == "started"
        assert result2["status"] == "already_running"

    def test_already_running_returns_current_job_id(self) -> None:
        pub = ProgressPublisher()
        settings = _make_settings()
        started = threading.Event()
        proceed = threading.Event()

        def _slow(
            s: Any,
            p: ProgressPublisher,
            msf: Any,
            *,
            job_id: str,  # noqa: ARG001
        ) -> None:
            _slow_noop(s, p, msf, job_id=job_id, started=started, proceed=proceed)

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_slow):
            result1 = trigger_sync(settings, pub)
            started.wait(timeout=2.0)
            result2 = trigger_sync(settings, pub)

        proceed.set()
        time.sleep(0.1)

        assert result1["job_id"] == result2["job_id"]

    def test_after_completion_new_trigger_succeeds(self) -> None:
        pub = ProgressPublisher()
        settings = _make_settings()

        def _fast(
            _s: Any,
            p: ProgressPublisher,
            _msf: Any,
            *,
            job_id: str,  # noqa: ARG001
        ) -> None:
            p.begin_job(0, job_id=job_id)
            time.sleep(0.05)
            p.end_job(success=True)

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_fast):
            result1 = trigger_sync(settings, pub)
            assert result1["status"] == "started"

            # wait for the first sync to complete and release the lock
            time.sleep(0.2)

            pub2 = ProgressPublisher()
            result2 = trigger_sync(settings, pub2)

        assert result2["status"] == "started"
        assert result1["job_id"] != result2["job_id"]

    def test_spawned_thread_is_daemon(self) -> None:
        pub = ProgressPublisher()
        settings = _make_settings()
        thread_ref: list[threading.Thread] = []
        proceed = threading.Event()

        def _record(
            _s: Any,
            p: ProgressPublisher,
            _msf: Any,
            *,
            job_id: str,  # noqa: ARG001
        ) -> None:
            p.begin_job(0, job_id=job_id)
            thread_ref.append(threading.current_thread())
            proceed.wait(timeout=5.0)
            p.end_job(success=True)

        with patch("blackvuesync.server.sync_runner._do_sync", side_effect=_record):
            trigger_sync(settings, pub)
            time.sleep(0.1)  # give the thread time to start

        proceed.set()
        time.sleep(0.1)

        assert len(thread_ref) == 1
        assert thread_ref[0].daemon is True
