"""sqlite time-series store of per-run sync metrics (serve mode only).

one row per sync run. sqlite3 is stdlib (no new dependency). the sync thread
is the sole writer; flask handlers are readers. WAL lets reads proceed during
a write. a short-lived connection is opened per call (never shared across
threads).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blackvuesync.metrics import SyncMetrics

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    ts_seconds          REAL PRIMARY KEY,
    success             INTEGER NOT NULL,
    exit_code           INTEGER,
    duration_seconds    REAL    NOT NULL DEFAULT 0,
    files               INTEGER NOT NULL DEFAULT 0,
    bytes               INTEGER NOT NULL DEFAULT 0,
    recordings_seen     INTEGER NOT NULL DEFAULT 0,
    recordings_selected INTEGER NOT NULL DEFAULT 0,
    disk_used_ratio     REAL,
    failed_markers      INTEGER NOT NULL DEFAULT 0,
    failures_json       TEXT,
    dry_run             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs (ts_seconds);
"""

_SECONDS_PER_DAY = 86400.0


@dataclasses.dataclass(frozen=True)
class RunRow:  # pylint: disable=too-many-instance-attributes
    """one stored sync run."""

    ts_seconds: float
    success: int
    exit_code: int | None
    duration_seconds: float
    files: int
    bytes: int
    recordings_seen: int
    recordings_selected: int
    disk_used_ratio: float | None
    failed_markers: int
    failures: dict[str, int]
    dry_run: int


class StatsStore:
    """sqlite-backed per-run metrics store."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with contextlib.closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        """opens a fresh WAL connection with row access by name."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def record_run(self, metrics: SyncMetrics) -> None:
        """inserts (or replaces) one run row from a finalized SyncMetrics.

        no-op if the metrics were never finalized (no run timestamp).
        """
        ts = metrics.last_run_timestamp_seconds
        if ts is None:
            return
        failures: dict[str, int] = dict(metrics.last_run_failures or {})
        for reason, count in (metrics.file_download_failures_last_run or {}).items():
            failures[reason] = failures.get(reason, 0) + count
        row = (
            float(ts),
            int(metrics.last_run_success),
            metrics.last_run_exit_code,
            float(metrics.run_duration_seconds),
            int(metrics.files_downloaded_last_run),
            int(metrics.bytes_downloaded_last_run),
            int(metrics.dashcam_recordings_seen),
            int(metrics.recordings_selected),
            metrics.destination_disk_used_ratio,
            int(metrics.failed_marker_files),
            json.dumps(failures, separators=(",", ":")),
            1 if metrics.dry_run else 0,
        )
        with contextlib.closing(self._connect()) as conn:  # noqa: SIM117
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO runs (ts_seconds, success, exit_code, "
                    "duration_seconds, files, bytes, recordings_seen, "
                    "recordings_selected, disk_used_ratio, failed_markers, "
                    "failures_json, dry_run) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )

    def query(self, since_ts: float | None = None) -> list[RunRow]:
        """returns rows ordered by timestamp ascending; optionally since since_ts."""
        sql = "SELECT * FROM runs"
        params: tuple[float, ...] = ()
        if since_ts is not None:
            sql += " WHERE ts_seconds >= ?"
            params = (since_ts,)
        sql += " ORDER BY ts_seconds ASC"
        with contextlib.closing(self._connect()) as conn:
            cursor = conn.execute(sql, params)
            return [self._to_row(r) for r in cursor.fetchall()]

    def prune(self, retention_days: int) -> int:
        """deletes rows older than retention_days; no-op when retention_days <= 0.

        returns the number of rows deleted.
        """
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * _SECONDS_PER_DAY
        with contextlib.closing(self._connect()) as conn:  # noqa: SIM117
            with conn:
                cursor = conn.execute(
                    "DELETE FROM runs WHERE ts_seconds < ?", (cutoff,)
                )
                return cursor.rowcount

    @staticmethod
    def _to_row(record: sqlite3.Row) -> RunRow:
        """maps a sqlite row to a RunRow, decoding the failures json."""
        try:
            failures = (
                json.loads(record["failures_json"]) if record["failures_json"] else {}
            )
        except (ValueError, TypeError):
            failures = {}
        return RunRow(
            ts_seconds=record["ts_seconds"],
            success=record["success"],
            exit_code=record["exit_code"],
            duration_seconds=record["duration_seconds"],
            files=record["files"],
            bytes=record["bytes"],
            recordings_seen=record["recordings_seen"],
            recordings_selected=record["recordings_selected"],
            disk_used_ratio=record["disk_used_ratio"],
            failed_markers=record["failed_markers"],
            failures=failures,
            dry_run=record["dry_run"],
        )


__all__ = ["RunRow", "StatsStore"]
