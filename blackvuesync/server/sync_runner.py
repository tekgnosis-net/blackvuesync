"""sync_runner: spawns run_sync in a daemon thread under a process-wide lock."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

from blackvuesync.server.progress import ProgressPublisher

if TYPE_CHECKING:
    from blackvuesync.server.stats_store import StatsStore

logger = logging.getLogger(__name__)

# process-wide lock; held for the duration of an active sync; non-reentrant
# so a second trigger while running returns "already_running" immediately.
_sync_lock = threading.Lock()

# reference to the current sync thread; useful for diagnostics
_current_thread: threading.Thread | None = None


def trigger_sync(
    settings: Any,
    publisher: ProgressPublisher,
    stats_store: StatsStore | None = None,
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

    # clears any leftover stop flag from a previous run; the next request to
    # /api/sync/stop sets it again on demand.
    # pylint: disable=import-outside-toplevel
    from blackvuesync.sync import clear_stop

    clear_stop()
    # pylint: enable=import-outside-toplevel

    # pre-generate job_id before spawning the thread so the 202 response and
    # the publisher state always agree on the same id.
    job_id = uuid.uuid4().hex

    def _run() -> None:
        """runs sync under the lock; releases lock in finally."""
        try:
            _do_sync(settings, publisher, job_id=job_id, stats_store=stats_store)
        finally:
            with contextlib.suppress(Exception):
                _sync_lock.release()

    t = threading.Thread(target=_run, name=f"sync-{job_id[:8]}", daemon=True)
    _current_thread = t
    t.start()
    return {"status": "started", "job_id": job_id}


def _do_sync(  # pylint: disable=too-many-locals,too-many-statements
    settings: Any,
    publisher: ProgressPublisher,
    *,
    job_id: str,
    stats_store: StatsStore | None = None,
) -> None:
    """performs the actual sync on the daemon thread.

    builds a SyncMetrics, passes it to sync() (which records downloads into
    it), then finalizes and persists it: saves metrics state and emits
    prometheus metrics (this is what gives serve mode metrics at all), and
    records the run into the stats store + prunes when a store is supplied.
    """
    # pylint: disable=import-outside-toplevel
    import socket

    import blackvuesync.sync as _sync
    from blackvuesync.metrics import (
        SyncMetrics,
        count_failed_marker_files,
        emit_metrics,
        load_metrics_state,
        save_metrics_state,
    )
    from blackvuesync.sync import (
        clean_destination,
        ensure_destination,
        lock,
        sync,
        unlock,
    )

    # pylint: enable=import-outside-toplevel

    destination = settings.system.destination
    state_file = settings.metrics.state_file
    lf_fd = None
    metrics: SyncMetrics | None = None
    sync_success = False
    try:
        address = settings.connection.address
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

        socket.setdefaulttimeout(timeout)

        metrics = SyncMetrics(
            run_start_monotonic=time.perf_counter(),
            run_start_timestamp=time.time(),
            dry_run=settings.system.dry_run,
            metrics_job=settings.metrics.job,
            metrics_instance=settings.metrics.instance or address,
            last_successful_file_pull_timestamp_seconds=load_metrics_state(state_file),
        )

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
                metrics=metrics,
                publisher=publisher,
                job_id=job_id,
            )
            sync_success = True
        finally:
            clean_destination(destination, grouping)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("sync_runner: sync failed")
    finally:
        if lf_fd is not None:
            with contextlib.suppress(Exception):
                unlock(lf_fd)
        if metrics is not None:
            with contextlib.suppress(Exception):
                metrics.failed_marker_files = count_failed_marker_files(destination)
            metrics.finalize(0 if sync_success else 1, sync_success)
            with contextlib.suppress(Exception):
                save_metrics_state(state_file, metrics)
            with contextlib.suppress(Exception):
                emit_metrics(
                    metrics,
                    settings.metrics.file,
                    settings.metrics.pushgateway_url,
                    settings.connection.timeout_seconds,
                )
            if stats_store is not None:
                with contextlib.suppress(Exception):
                    stats_store.record_run(metrics)
                    stats_store.prune(settings.stats.retention_days)


__all__ = ["trigger_sync"]
