"""cli entry point for blackvuesync."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import socket
import sys
import time

import blackvuesync.sync as _sync
from blackvuesync.metrics import (
    METRICS_DEFAULT_JOB,
    SyncMetrics,
    classify_run_failure,
    count_failed_marker_files,
    default_metrics_state_file,
    emit_metrics,
    load_metrics_state,
    metrics_enabled,
    parse_pushgateway_url,
    save_metrics_state,
)
from blackvuesync.sync import (
    LOG_FORMATS,
    calc_cutoff_date,
    clean_destination,
    configure_logging,
    ensure_destination,
    flush_logs,
    lock,
    parse_duration,
    parse_filter,
    parse_skip_metadata,
    set_logging_levels,
    sync,
    unlock,
)

# module-level loggers
logger = logging.getLogger()
cron_logger = logging.getLogger("cron")


def parse_args() -> argparse.Namespace:
    """parses the command-line arguments"""
    from blackvuesync import __version__

    arg_parser = argparse.ArgumentParser(
        description="Synchronizes BlackVue dashcam recordings with a local directory.",
        epilog="Bug reports: https://github.com/tekgnosis-net/blackvuesync/issues",
    )
    arg_parser.add_argument(
        "address", metavar="ADDRESS", help="dashcam IP address or name"
    )
    arg_parser.add_argument(
        "-d",
        "--destination",
        metavar="DEST",
        help="sets the destination directory to DEST; defaults to the current directory",
    )
    arg_parser.add_argument(
        "-g",
        "--grouping",
        metavar="GROUPING",
        default="none",
        choices=["none", "daily", "weekly", "monthly", "yearly"],
        help="groups recording by day, week, month or year under a directory named after the date; so respectively 2019-06-15, 2019-06-09 (Mon), 2019-07 or 2019; defaults to none, indicating no grouping",
    )
    arg_parser.add_argument(
        "-k",
        "--keep",
        metavar="KEEP_RANGE",
        help="""keeps recordings in the given range, removing the rest; defaults to days, but can suffix with d, w for days or weeks respectively""",
    )
    arg_parser.add_argument(
        "-p",
        "--priority",
        metavar="DOWNLOAD_PRIORITY",
        default="date",
        choices=["date", "rdate", "type"],
        help="sets the recording download priority; date: downloads in chronological order from oldest to newest; rdate: downloads in chronological order from newest to oldest; type: prioritizes manual, event, normal and then parkingrecordings; defaults to date",
    )
    arg_parser.add_argument(
        "-i",
        "--include",
        default=None,
        type=parse_filter,
        help="downloads only recordings matching the given codes; each code"
        " is a recording type optionally followed by a camera direction;"
        " e.g. --include P,NF downloads all Parking and Normal Front"
        " recordings",
    )
    arg_parser.add_argument(
        "-e",
        "--exclude",
        default=None,
        type=parse_filter,
        help="excludes recordings matching the given codes; takes priority"
        " over --include; e.g. --include N,E --exclude NR downloads all"
        " Normal and Event recordings except Normal Rear",
    )
    arg_parser.add_argument(
        "-u",
        "--max-used-disk",
        metavar="DISK_USAGE_PERCENT",
        default=90,
        type=int,
        choices=range(5, 99),
        help="stops downloading recordings if disk is over DISK_USAGE_PERCENT used; defaults to 90",
    )
    arg_parser.add_argument(
        "-t",
        "--timeout",
        metavar="TIMEOUT",
        default=10.0,
        type=float,
        help="sets the connection timeout in seconds (float); defaults to 10.0 seconds",
    )
    arg_parser.add_argument(
        "--retry-failed-after",
        metavar="DURATION",
        default="1d",
        help="waits at least the given duration before retrying a failed download; defaults to days, but can suffix with s, h, d, w for seconds, hours, days or weeks respectively; defaults to 1d",
    )
    arg_parser.add_argument(
        "--skip-metadata",
        metavar="TYPES",
        default=set(),
        type=parse_skip_metadata,
        help="skips downloading metadata file types; t=thumbnail (.thm),"
        " 3=accelerometer (.3gf), g=gps (.gps); e.g. --skip-metadata t3g"
        " skips all metadata files",
    )
    arg_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="increases verbosity"
    )
    arg_parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="quiets down output messages; overrides verbosity options",
    )
    arg_parser.add_argument(
        "--log-format",
        default="text",
        choices=LOG_FORMATS,
        help="sets log output format; defaults to text",
    )
    arg_parser.add_argument(
        "--metrics-file",
        metavar="PATH",
        help="writes Prometheus metrics text format to PATH",
    )
    arg_parser.add_argument(
        "--metrics-pushgateway-url",
        metavar="URL",
        type=parse_pushgateway_url,
        help="pushes Prometheus metrics to the Pushgateway URL",
    )
    arg_parser.add_argument(
        "--metrics-job",
        metavar="NAME",
        default=METRICS_DEFAULT_JOB,
        help=f"sets the Pushgateway metrics job; defaults to {METRICS_DEFAULT_JOB}",
    )
    arg_parser.add_argument(
        "--metrics-instance",
        metavar="NAME",
        help="sets the Pushgateway metrics instance; defaults to ADDRESS",
    )
    arg_parser.add_argument(
        "--metrics-state-file",
        metavar="PATH",
        help="persists cross-run metrics state at PATH",
    )
    arg_parser.add_argument(
        "--cron",
        action="store_true",
        help="cron mode, only logs normal recordings at default verbosity",
    )
    arg_parser.add_argument(
        "--dry-run", action="store_true", help="shows what the program would do"
    )
    arg_parser.add_argument(
        "--affinity-key",
        metavar="AFFINITY_KEY",
        help="affinity key; reserved for test isolation",
    )
    arg_parser.add_argument(
        "--version",
        action="version",
        default=__version__,
        version=f"%(prog)s {__version__}",
        help="shows the version and exits",
    )

    return arg_parser.parse_args()


def main() -> int:
    """run forrest run"""
    # pylint: disable=too-many-branches,too-many-statements
    args = parse_args()

    configure_logging(args.log_format)
    set_logging_levels(-1 if args.quiet else args.verbose, args.cron)

    _sync.dry_run = args.dry_run
    _sync.affinity_key = args.affinity_key
    _sync.skip_metadata = args.skip_metadata
    if _sync.skip_metadata:
        logger.info(
            "Skipping metadata types : %s",
            ", ".join(sorted(_sync.skip_metadata)),
            extra={
                "event": "skip_metadata_configured",
                "metadata_types": sorted(_sync.skip_metadata),
            },
        )
    if _sync.dry_run:
        logger.info(
            "DRY RUN No action will be taken.",
            extra={"event": "dry_run_enabled"},
        )

    _sync.max_disk_used_percent = args.max_used_disk

    # sets socket timeout
    timeout: float = args.timeout
    if timeout <= 0:
        raise argparse.ArgumentTypeError("TIMEOUT must be greater than zero.")
    _sync.socket_timeout = timeout
    socket.setdefaulttimeout(timeout)

    destination = args.destination or os.getcwd()
    grouping = args.grouping
    lf_fd = None
    exit_code = 0
    sync_success = False
    metrics = None
    metrics_state_file = None

    if metrics_enabled(args):
        metrics_state_file = args.metrics_state_file or default_metrics_state_file(
            destination
        )
        metrics = SyncMetrics(
            run_start_monotonic=time.perf_counter(),
            run_start_timestamp=time.time(),
            metrics_job=args.metrics_job,
            metrics_instance=args.metrics_instance or args.address,
        )
        metrics.last_successful_file_pull_timestamp_seconds = load_metrics_state(
            metrics_state_file
        )

    try:
        if args.keep:
            _sync.cutoff_date = calc_cutoff_date(args.keep)
            logger.info(
                "Recording cutoff date : %s",
                _sync.cutoff_date,
                extra={
                    "event": "recording_cutoff_configured",
                    "cutoff_date": _sync.cutoff_date,
                },
            )

        _sync.retry_failed_after = parse_duration(
            args.retry_failed_after, label="RETRY_FAILED_AFTER"
        )

        # prepares the local file destination
        ensure_destination(destination)

        lf_fd = lock(destination)

        try:
            sync(
                args.address,
                destination,
                grouping,
                args.priority,
                args.include,
                args.exclude,
                metrics,
            )
            sync_success = True
        finally:
            # removes temporary files (if we synced successfully, these are temp files from lost recordings)
            clean_destination(destination, grouping)
    except UserWarning as e:
        logger.warning(
            e.args[0],
            extra={
                "event": "sync_warning",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        if metrics:
            metrics.record_run_failure(classify_run_failure(e))
        exit_code = 0 if args.cron else 1
    except RuntimeError as e:
        logger.error(
            e.args[0],
            extra={
                "event": "sync_error",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        if metrics:
            metrics.record_run_failure(classify_run_failure(e))
        exit_code = 2
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception(
            e,
            extra={
                "event": "sync_unexpected_error",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        if metrics:
            metrics.record_run_failure(classify_run_failure(e))
        exit_code = 3
    finally:
        if lf_fd is not None:
            unlock(lf_fd)

        if metrics:
            with contextlib.suppress(OSError):
                metrics.failed_marker_files = count_failed_marker_files(destination)
            metrics.finalize(exit_code, sync_success)
            if metrics_state_file:
                save_metrics_state(metrics_state_file, metrics)
            emit_metrics(
                metrics,
                args.metrics_file,
                args.metrics_pushgateway_url,
                timeout,
            )

        flush_logs()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
