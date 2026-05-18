"""blackvuesync package: synchronizes recordings from BlackVue dashcams."""

__version__ = "2.3.0a0"

from blackvuesync.metrics import (
    SyncMetrics as SyncMetrics,
)
from blackvuesync.metrics import (
    classify_run_failure as classify_run_failure,
)
from blackvuesync.metrics import (
    count_failed_marker_files as count_failed_marker_files,
)
from blackvuesync.metrics import (
    default_metrics_state_file as default_metrics_state_file,
)
from blackvuesync.metrics import (
    emit_metrics as emit_metrics,
)
from blackvuesync.metrics import (
    get_pushgateway_metrics_url as get_pushgateway_metrics_url,
)
from blackvuesync.metrics import (
    load_metrics_state as load_metrics_state,
)
from blackvuesync.metrics import (
    metrics_enabled as metrics_enabled,
)
from blackvuesync.metrics import (
    parse_pushgateway_url as parse_pushgateway_url,
)
from blackvuesync.metrics import (
    push_metrics as push_metrics,
)
from blackvuesync.metrics import (
    render_metrics as render_metrics,
)
from blackvuesync.metrics import (
    save_metrics_state as save_metrics_state,
)
from blackvuesync.metrics import (
    write_metrics_file as write_metrics_file,
)
from blackvuesync.sync import (
    DOWNLOAD_CHUNK_SIZE as DOWNLOAD_CHUNK_SIZE,
)
from blackvuesync.sync import (
    RECORDING_DIRECTIONS as RECORDING_DIRECTIONS,
)
from blackvuesync.sync import (
    RECORDING_TYPES as RECORDING_TYPES,
)
from blackvuesync.sync import (
    VALID_METADATA_TYPES as VALID_METADATA_TYPES,
)
from blackvuesync.sync import (
    DownloadedRecording as DownloadedRecording,
)
from blackvuesync.sync import (
    Recording as Recording,
)
from blackvuesync.sync import (
    StructuredLogFormatter as StructuredLogFormatter,
)
from blackvuesync.sync import (
    apply_recording_filters as apply_recording_filters,
)
from blackvuesync.sync import (
    calc_cutoff_date as calc_cutoff_date,
)
from blackvuesync.sync import (
    clean_destination as clean_destination,
)
from blackvuesync.sync import (
    configure_logging as configure_logging,
)
from blackvuesync.sync import (
    download_file as download_file,
)
from blackvuesync.sync import (
    download_recording as download_recording,
)
from blackvuesync.sync import (
    ensure_destination as ensure_destination,
)
from blackvuesync.sync import (
    flush_logs as flush_logs,
)
from blackvuesync.sync import (
    get_dashcam_filenames as get_dashcam_filenames,
)
from blackvuesync.sync import (
    get_failed_marker_filepath as get_failed_marker_filepath,
)
from blackvuesync.sync import (
    get_group_name as get_group_name,
)
from blackvuesync.sync import (
    is_download_blocked_by_failure as is_download_blocked_by_failure,
)
from blackvuesync.sync import (
    lock as lock,
)
from blackvuesync.sync import (
    mark_download_failed as mark_download_failed,
)
from blackvuesync.sync import (
    parse_duration as parse_duration,
)
from blackvuesync.sync import (
    parse_filter as parse_filter,
)
from blackvuesync.sync import (
    parse_skip_metadata as parse_skip_metadata,
)
from blackvuesync.sync import (
    remove_download_failed_marker as remove_download_failed_marker,
)
from blackvuesync.sync import (
    set_logging_levels as set_logging_levels,
)
from blackvuesync.sync import (
    sort_recordings as sort_recordings,
)
from blackvuesync.sync import (
    to_downloaded_recording as to_downloaded_recording,
)
from blackvuesync.sync import (
    to_recording as to_recording,
)
from blackvuesync.sync import (
    unlock as unlock,
)


def main() -> int:
    """runs the blackvuesync CLI; delegates to the __main__ entry point."""
    from blackvuesync.__main__ import main as _main

    return _main()
