"""sync_runner: spawns run_sync in a daemon thread under a process-wide lock."""

from __future__ import annotations

import contextlib
import logging
import threading
import uuid
from typing import Any

from blackvuesync.server.progress import ProgressPublisher

logger = logging.getLogger(__name__)

# process-wide lock; held for the duration of an active sync; non-reentrant
# so a second trigger while running returns "already_running" immediately.
_sync_lock = threading.Lock()

# reference to the current sync thread; useful for diagnostics
_current_thread: threading.Thread | None = None


def trigger_sync(
    settings: Any,
    publisher: ProgressPublisher,
    metrics_state_file: str | None = None,
) -> dict[str, str]:
    """triggers a sync in a background daemon thread; returns a status dict.

    returns {"status": "started", "job_id": "<id>"} when the sync was
    successfully scheduled, or {"status": "already_running", "job_id": "<id>"}
    when a sync is already active. the caller maps "already_running" to 409.

    the publisher is the sole source of sync state for api consumers; the
    sync thread calls publisher.end_job() in its finally block so the
    snapshot transitions to complete/failed after the run.
    """
    global _current_thread  # pylint: disable=global-statement

    if not _sync_lock.acquire(blocking=False):  # pylint: disable=consider-using-with
        # a sync is already running; return the current job_id from the publisher
        current_snap = publisher.snapshot()
        return {"status": "already_running", "job_id": current_snap.job_id}

    # pre-generate job_id before spawning the thread so the 202 response and
    # the publisher state always agree on the same id.
    job_id = uuid.uuid4().hex

    def _run() -> None:
        """runs sync under the lock; releases lock in finally."""
        try:
            _do_sync(settings, publisher, metrics_state_file, job_id=job_id)
        finally:
            with contextlib.suppress(Exception):
                _sync_lock.release()

    t = threading.Thread(target=_run, name=f"sync-{job_id[:8]}", daemon=True)
    _current_thread = t
    t.start()
    return {"status": "started", "job_id": job_id}


def _do_sync(  # pylint: disable=too-many-locals
    settings: Any,
    publisher: ProgressPublisher,
    metrics_state_file: str | None,  # noqa: ARG001  # pylint: disable=unused-argument
    *,
    job_id: str,
) -> None:
    """performs the actual sync; called inside the daemon thread.

    currently calls sync() from blackvuesync.sync; in phase E this will
    be replaced by an APScheduler-driven invocation. sync() owns the full
    job lifecycle (begin_job … end_job) using the pre-generated job_id.
    """
    # pylint: disable=import-outside-toplevel
    import blackvuesync.sync as _sync
    from blackvuesync.sync import (
        clean_destination,
        ensure_destination,
        lock,
        sync,
        unlock,
    )

    # pylint: enable=import-outside-toplevel

    lf_fd = None
    try:
        address = settings.connection.address
        destination = settings.system.destination
        grouping = settings.sync.grouping
        priority = settings.sync.priority
        include = settings.sync.include or None
        exclude = settings.sync.exclude or None
        timeout = settings.connection.timeout_seconds

        _sync.socket_timeout = timeout
        _sync.affinity_key = None
        _sync.dry_run = settings.system.dry_run
        _sync.skip_metadata = set()
        _sync.max_disk_used_percent = settings.retention.max_used_disk_percent

        import socket  # pylint: disable=import-outside-toplevel,redefined-outer-name

        socket.setdefaulttimeout(timeout)

        ensure_destination(destination)
        lf_fd = lock(destination)

        try:
            sync(
                address,
                destination,
                grouping,
                priority,
                include,
                exclude,
                publisher=publisher,
                job_id=job_id,
            )
        finally:
            clean_destination(destination, grouping)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("sync_runner: sync failed")
    finally:
        if lf_fd is not None:
            with contextlib.suppress(Exception):
                unlock(lf_fd)


__all__ = ["trigger_sync"]
