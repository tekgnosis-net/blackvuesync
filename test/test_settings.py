"""tests for blackvuesync.settings module."""

from __future__ import annotations

import dataclasses
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.settings import (
    SCHEMA_VERSION,
    AuthSettings,
    ConnectionSettings,
    LoggingSettings,
    MetricsSettings,
    RetentionSettings,
    ScheduleSettings,
    Settings,
    SettingsStore,
    SyncSettings,
    SystemSettings,
    ValidationError,
    WebSettings,
    _settings_from_dict,
    _settings_to_dict,
    migrate,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_store(
    settings_path: Path, env: dict[str, str] | None = None
) -> SettingsStore:
    """creates a SettingsStore at settings_path with optional env overrides."""
    env = env or {}
    with patch.dict(os.environ, env, clear=False):
        return SettingsStore(settings_path)


# ---------------------------------------------------------------------------
# section defaults
# ---------------------------------------------------------------------------


def test_connection_defaults() -> None:
    """verifies ConnectionSettings has expected defaults."""
    s = ConnectionSettings()
    assert s.address == ""
    assert s.timeout_seconds == 10.0
    assert ConnectionSettings.TIER == "restart"


def test_schedule_defaults() -> None:
    """verifies ScheduleSettings has expected defaults."""
    s = ScheduleSettings()
    assert s.cron_expression == "*/15 * * * *"
    assert s.timezone == "UTC"
    assert ScheduleSettings.TIER == "next_tick"


def test_sync_defaults() -> None:
    """verifies SyncSettings has expected defaults."""
    s = SyncSettings()
    assert s.priority == "date"
    assert s.grouping == "none"
    assert s.include == ()
    assert s.exclude == ()
    assert s.retry_failed_after == "1d"
    assert s.skip_metadata == ()
    assert s.affinity_key is None
    assert SyncSettings.TIER == "next_tick"


def test_retention_defaults() -> None:
    """verifies RetentionSettings has expected defaults."""
    s = RetentionSettings()
    assert s.keep == "2w"
    assert s.max_used_disk_percent == 90
    assert RetentionSettings.TIER == "next_tick"


def test_logging_defaults() -> None:
    """verifies LoggingSettings has expected defaults."""
    s = LoggingSettings()
    assert s.verbose == 0
    assert s.quiet is False
    assert s.format == "text"
    assert s.file_max_bytes == 10 * 1024 * 1024
    assert s.file_backup_count == 5
    assert s.ring_buffer_capacity == 1000
    assert LoggingSettings.TIER == "immediate"


def test_metrics_defaults() -> None:
    """verifies MetricsSettings has expected defaults."""
    s = MetricsSettings()
    assert s.file is None
    assert s.pushgateway_url is None
    assert s.job == "blackvuesync"
    assert s.instance is None
    assert s.state_file == "/config/metrics-state.json"
    assert MetricsSettings.TIER == "immediate"


def test_web_defaults() -> None:
    """verifies WebSettings has expected defaults."""
    s = WebSettings()
    assert s.port == 8080
    assert s.session_lifetime_hours == 24
    assert WebSettings.TIER == "restart"


def test_auth_defaults() -> None:
    """verifies AuthSettings has expected defaults."""
    s = AuthSettings()
    assert s.mode == "login"
    assert s.username == "admin"
    assert s.password_hash == ""
    assert s.session_secret == ""
    assert s.trusted_proxies == ()
    assert s.proxy_user_header == "X-Remote-User"
    assert AuthSettings.TIER == "immediate"


def test_system_defaults() -> None:
    """verifies SystemSettings has expected defaults."""
    s = SystemSettings()
    assert s.destination == "/recordings"
    assert s.dry_run is False
    assert SystemSettings.TIER == "restart"


def test_settings_defaults() -> None:
    """verifies top-level Settings has version=1 and section instances."""
    s = Settings()
    assert s.version == SCHEMA_VERSION
    assert isinstance(s.connection, ConnectionSettings)
    assert isinstance(s.schedule, ScheduleSettings)
    assert isinstance(s.sync, SyncSettings)
    assert isinstance(s.retention, RetentionSettings)
    assert isinstance(s.logging, LoggingSettings)
    assert isinstance(s.metrics, MetricsSettings)
    assert isinstance(s.web, WebSettings)
    assert isinstance(s.auth, AuthSettings)
    assert isinstance(s.system, SystemSettings)


# ---------------------------------------------------------------------------
# frozen dataclass behavior
# ---------------------------------------------------------------------------


def test_connection_settings_is_frozen() -> None:
    """verifies ConnectionSettings raises FrozenInstanceError on mutation."""
    s = ConnectionSettings(address="192.168.1.1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.address = "10.0.0.1"  # type: ignore[misc]


def test_settings_is_frozen() -> None:
    """verifies Settings raises FrozenInstanceError on mutation."""
    s = Settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.version = 2  # type: ignore[misc]


def test_dataclasses_replace_works_on_frozen() -> None:
    """verifies dataclasses.replace() creates a new instance with changed fields."""
    original = ConnectionSettings(address="192.168.1.1", timeout_seconds=5.0)
    updated = dataclasses.replace(original, timeout_seconds=20.0)
    assert updated.timeout_seconds == 20.0
    assert updated.address == "192.168.1.1"
    assert original.timeout_seconds == 5.0  # original unchanged


# ---------------------------------------------------------------------------
# validators: valid cases
# ---------------------------------------------------------------------------


def test_connection_validate_valid() -> None:
    """verifies valid ConnectionSettings produces no errors."""
    s = ConnectionSettings(address="192.168.1.99", timeout_seconds=5.0)
    assert s.validate() == []


def test_schedule_validate_valid() -> None:
    """verifies valid ScheduleSettings produces no errors."""
    s = ScheduleSettings(cron_expression="0 */6 * * *", timezone="UTC")
    assert s.validate() == []


def test_sync_validate_valid() -> None:
    """verifies valid SyncSettings produces no errors."""
    s = SyncSettings(
        retry_failed_after="2d",
        skip_metadata=("t", "g"),
    )
    assert s.validate() == []


def test_retention_validate_valid() -> None:
    """verifies valid RetentionSettings produces no errors."""
    s = RetentionSettings(keep="7d", max_used_disk_percent=80)
    assert s.validate() == []


def test_logging_validate_valid() -> None:
    """verifies valid LoggingSettings produces no errors."""
    s = LoggingSettings(verbose=2, file_max_bytes=1024, ring_buffer_capacity=100)
    assert s.validate() == []


def test_web_validate_valid() -> None:
    """verifies valid WebSettings produces no errors."""
    s = WebSettings(port=8080, session_lifetime_hours=12)
    assert s.validate() == []


def test_auth_validate_proxy_mode_valid() -> None:
    """verifies proxy mode with required fields produces no errors."""
    s = AuthSettings(
        mode="proxy",
        trusted_proxies=("127.0.0.1",),
        proxy_user_header="X-Remote-User",
    )
    assert s.validate() == []


def test_system_validate_valid() -> None:
    """verifies valid SystemSettings produces no errors."""
    s = SystemSettings(destination="/data/recordings")
    assert s.validate() == []


# ---------------------------------------------------------------------------
# validators: invalid cases
# ---------------------------------------------------------------------------


def test_connection_validate_empty_address() -> None:
    """verifies empty address is rejected."""
    s = ConnectionSettings(address="")
    errors = s.validate()
    assert any("address" in e for e in errors)


def test_connection_validate_nonpositive_timeout() -> None:
    """verifies non-positive timeout is rejected."""
    s = ConnectionSettings(address="192.168.1.1", timeout_seconds=0.0)
    errors = s.validate()
    assert any("timeout_seconds" in e for e in errors)


def test_schedule_validate_bad_cron() -> None:
    """verifies a malformed cron expression is rejected."""
    s = ScheduleSettings(cron_expression="not a cron")
    errors = s.validate()
    assert any("cron_expression" in e for e in errors)


def test_schedule_validate_cron_wrong_field_count() -> None:
    """verifies a 3-field cron expression is rejected (must be 5 fields)."""
    s = ScheduleSettings(cron_expression="* * *")
    errors = s.validate()
    assert any("cron_expression" in e for e in errors)


def test_sync_validate_bad_duration() -> None:
    """verifies an invalid retry_failed_after duration is rejected."""
    s = SyncSettings(retry_failed_after="bad")
    errors = s.validate()
    assert any("retry_failed_after" in e for e in errors)


def test_retention_validate_bad_keep() -> None:
    """verifies an invalid keep duration is rejected."""
    s = RetentionSettings(keep="xyz")
    errors = s.validate()
    assert any("keep" in e for e in errors)


def test_retention_validate_disk_percent_out_of_range() -> None:
    """verifies max_used_disk_percent outside [1, 100] is rejected."""
    s = RetentionSettings(max_used_disk_percent=0)
    errors = s.validate()
    assert any("max_used_disk_percent" in e for e in errors)

    s2 = RetentionSettings(max_used_disk_percent=101)
    errors2 = s2.validate()
    assert any("max_used_disk_percent" in e for e in errors2)


def test_logging_validate_negative_verbose() -> None:
    """verifies negative verbose level is rejected."""
    s = LoggingSettings(verbose=-1)
    errors = s.validate()
    assert any("verbose" in e for e in errors)


def test_logging_validate_zero_file_max_bytes() -> None:
    """verifies zero file_max_bytes is rejected."""
    s = LoggingSettings(file_max_bytes=0)
    errors = s.validate()
    assert any("file_max_bytes" in e for e in errors)


def test_web_validate_port_out_of_range() -> None:
    """verifies port outside [1, 65535] is rejected."""
    s = WebSettings(port=0)
    errors = s.validate()
    assert any("port" in e for e in errors)

    s2 = WebSettings(port=65536)
    errors2 = s2.validate()
    assert any("port" in e for e in errors2)


def test_auth_validate_proxy_mode_missing_trusted_proxies() -> None:
    """verifies proxy mode without trusted_proxies is rejected."""
    s = AuthSettings(mode="proxy", trusted_proxies=())
    errors = s.validate()
    assert any("trusted_proxies" in e for e in errors)


def test_auth_validate_proxy_mode_missing_proxy_header() -> None:
    """verifies proxy mode without proxy_user_header is rejected."""
    s = AuthSettings(mode="proxy", trusted_proxies=("127.0.0.1",), proxy_user_header="")
    errors = s.validate()
    assert any("proxy_user_header" in e for e in errors)


def test_system_validate_empty_destination() -> None:
    """verifies empty destination is rejected."""
    s = SystemSettings(destination="")
    errors = s.validate()
    assert any("destination" in e for e in errors)


def test_settings_validate_aggregates_section_errors() -> None:
    """verifies Settings.validate() collects errors from all sections."""
    s = Settings(
        connection=ConnectionSettings(address=""),  # error
        schedule=ScheduleSettings(cron_expression="bad cron"),  # error
        retention=RetentionSettings(max_used_disk_percent=200),  # error
    )
    errors = s.validate()
    assert len(errors) >= 3
    error_text = " ".join(errors)
    assert "address" in error_text
    assert "cron_expression" in error_text
    assert "max_used_disk_percent" in error_text


# ---------------------------------------------------------------------------
# serialization round-trip
# ---------------------------------------------------------------------------


def test_settings_to_dict_tuple_to_list() -> None:
    """verifies tuples are serialized as JSON lists."""
    s = Settings(
        sync=SyncSettings(include=("NF", "EF"), skip_metadata=("t",)),
        auth=AuthSettings(trusted_proxies=("127.0.0.1",)),
    )
    d = _settings_to_dict(s)
    assert isinstance(d["sync"]["include"], list)
    assert d["sync"]["include"] == ["NF", "EF"]
    assert isinstance(d["sync"]["skip_metadata"], list)
    assert d["auth"]["trusted_proxies"] == ["127.0.0.1"]


def test_settings_from_dict_list_to_tuple() -> None:
    """verifies JSON lists are restored as tuples on load."""
    d: dict[str, Any] = {
        "version": 1,
        "sync": {"include": ["NF", "EF"], "skip_metadata": ["t"]},
        "auth": {"trusted_proxies": ["127.0.0.1"]},
    }
    s = _settings_from_dict(d)
    assert isinstance(s.sync.include, tuple)
    assert s.sync.include == ("NF", "EF")
    assert isinstance(s.auth.trusted_proxies, tuple)


def test_settings_round_trip_via_dict() -> None:
    """verifies settings survive a to_dict → from_dict round-trip."""
    original = Settings(
        connection=ConnectionSettings(address="192.168.1.1", timeout_seconds=15.0),
        sync=SyncSettings(
            priority="type",
            include=("NF",),
            skip_metadata=("t", "g"),
        ),
        web=WebSettings(port=9090),
    )
    restored = _settings_from_dict(_settings_to_dict(original))
    assert restored.connection.address == "192.168.1.1"
    assert restored.connection.timeout_seconds == 15.0
    assert restored.sync.include == ("NF",)
    assert restored.sync.skip_metadata == ("t", "g")
    assert restored.web.port == 9090


# ---------------------------------------------------------------------------
# SettingsStore: basic API
# ---------------------------------------------------------------------------


def test_store_get_returns_frozen_settings(settings_path: Path) -> None:
    """verifies get() returns a frozen Settings instance."""
    store = _make_store(settings_path)
    s = store.get()
    assert isinstance(s, Settings)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.version = 99  # type: ignore[misc]


def test_store_update_applies_mutation(settings_path: Path) -> None:
    """verifies update() applies a mutation and returns new settings."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    new = store.update(
        lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=9999))
    )
    assert new.web.port == 9999
    assert store.get().web.port == 9999


def test_store_update_raises_validation_error(settings_path: Path) -> None:
    """verifies update() raises ValidationError for invalid mutations."""
    store = _make_store(settings_path)
    with pytest.raises(ValidationError) as exc_info:
        store.update(
            lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=0))
        )
    assert exc_info.value.errors
    assert any("port" in e for e in exc_info.value.errors)


def test_store_on_change_listener_invoked(settings_path: Path) -> None:
    """verifies on_change listeners are called after successful update."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    received: list[tuple[Settings, Settings]] = []
    store.on_change(lambda old, new: received.append((old, new)))

    store.update(
        lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=7777))
    )

    assert len(received) == 1
    old, new = received[0]
    assert old.web.port != 7777
    assert new.web.port == 7777


def test_store_on_change_not_called_on_validation_failure(settings_path: Path) -> None:
    """verifies on_change listeners are not called when validation fails."""
    store = _make_store(settings_path)
    received: list[tuple[Settings, Settings]] = []
    store.on_change(lambda old, new: received.append((old, new)))

    with pytest.raises(ValidationError):
        store.update(
            lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=99999))
        )
    assert received == []


# ---------------------------------------------------------------------------
# SettingsStore: persistence (atomic write + crash resilience)
# ---------------------------------------------------------------------------


def test_store_persists_to_disk(settings_path: Path) -> None:
    """verifies settings are written to disk after update."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    store.update(
        lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=5555))
    )
    assert settings_path.exists()
    with open(settings_path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["web"]["port"] == 5555


def test_store_settings_file_has_restrictive_permissions(settings_path: Path) -> None:
    """verifies the settings file is created with mode 0o600."""
    _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    file_stat = os.stat(settings_path)
    assert stat.S_IMODE(file_stat.st_mode) == 0o600


def test_store_second_load_reads_from_file(settings_path: Path) -> None:
    """verifies a second SettingsStore instance reads from the existing file."""
    store1 = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    store1.update(
        lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=6666))
    )

    store2 = _make_store(settings_path)
    assert store2.get().web.port == 6666


def test_store_atomic_write_crash_resilience(
    settings_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verifies original file is intact when os.replace raises during save."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    # get the initial settings (persisted on bootstrap)
    initial_port = store.get().web.port

    original_replace = os.replace

    def failing_replace(_src: str, _dst: str) -> None:
        raise OSError("simulated crash during os.replace")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        store.update(
            lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=9876))
        )

    monkeypatch.setattr(os, "replace", original_replace)

    # the original file should still have the initial port
    with open(settings_path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["web"]["port"] == initial_port

    # no .tmp file should remain if os.replace raised before it could run
    # the important thing is the original file is intact
    assert raw["web"]["port"] == initial_port


def test_store_save_failure_during_write_cleans_up_tmp(
    settings_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verifies tmp file is removed when the write itself raises."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    original_fdopen = os.fdopen

    def failing_fdopen(_fd: int, *_args: object, **_kwargs: object) -> object:
        raise OSError("simulated write failure in fdopen")

    monkeypatch.setattr(os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="simulated"):
        store.update(
            lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=7654))
        )

    monkeypatch.setattr(os, "fdopen", original_fdopen)

    # tmp file should not linger
    tmp_path = settings_path.with_suffix(settings_path.suffix + ".tmp")
    assert not tmp_path.exists()


def test_store_round_trip_serialization(settings_path: Path) -> None:
    """verifies settings survive a save → load cycle."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    store.update(
        lambda s: dataclasses.replace(
            s,
            sync=dataclasses.replace(
                s.sync,
                include=("NF", "NR"),
                skip_metadata=("t",),
            ),
        )
    )

    store2 = _make_store(settings_path)
    assert store2.get().sync.include == ("NF", "NR")
    assert store2.get().sync.skip_metadata == ("t",)


# ---------------------------------------------------------------------------
# env-var bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_from_env_basic(settings_path: Path) -> None:
    """verifies env-var bootstrap produces expected settings."""
    env = {
        "ADDRESS": "192.168.1.100",
        "TIMEOUT": "20.0",
        "PRIORITY": "rdate",
        "GROUPING": "daily",
        "KEEP": "7d",
        "MAX_USED_DISK": "85",
        "RETRY_FAILED_AFTER": "2h",
        "VERBOSE": "1",
        "BLACKVUESYNC_PORT": "9090",
        "BLACKVUESYNC_SCHEDULE": "0 */4 * * *",
        "BLACKVUESYNC_TIMEZONE": "America/New_York",
        "BLACKVUESYNC_ADMIN_USERNAME": "boss",
    }
    store = _make_store(settings_path, env=env)
    s = store.get()

    assert s.connection.address == "192.168.1.100"
    assert s.connection.timeout_seconds == 20.0
    assert s.sync.priority == "rdate"
    assert s.sync.grouping == "daily"
    assert s.retention.keep == "7d"
    assert s.retention.max_used_disk_percent == 85
    assert s.sync.retry_failed_after == "2h"
    assert s.logging.verbose == 1
    assert s.web.port == 9090
    assert s.schedule.cron_expression == "0 */4 * * *"
    assert s.schedule.timezone == "America/New_York"
    assert s.auth.username == "boss"


def test_bootstrap_treats_empty_env_vars_as_absent(settings_path: Path) -> None:
    """verifies empty-string env vars are treated as unset.

    regression test: the project Dockerfile sets ENV X="" as a sentinel for
    "not configured" (matching blackvuesync.sh's `[ -n "${X:-}" ]` pattern).
    bootstrap must use defaults for empty-string values to avoid int("")
    and float("") ValueError crashes during container startup.
    """
    # simulates the Dockerfile ENV section: many vars set to empty string
    env = {
        "ADDRESS": "192.168.1.100",
        "TIMEOUT": "",
        "MAX_USED_DISK": "",
        "KEEP": "",
        "GROUPING": "",
        "PRIORITY": "",
        "LOG_FORMAT": "",
        "METRICS_FILE": "",
        "METRICS_JOB": "",
        "METRICS_INSTANCE": "",
        "METRICS_STATE_FILE": "",
        "RETRY_FAILED_AFTER": "",
        "INCLUDE": "",
        "EXCLUDE": "",
        "SKIP_METADATA": "",
        "AFFINITY_KEY": "",
        "VERBOSE": "",
        "QUIET": "",
    }
    store = _make_store(settings_path, env=env)
    s = store.get()

    # defaults must apply for empty-string numeric env vars (not ValueError)
    assert s.connection.address == "192.168.1.100"
    assert s.connection.timeout_seconds == 10.0
    assert s.retention.keep == "2w"
    assert s.retention.max_used_disk_percent == 90
    assert s.sync.priority == "date"
    assert s.sync.grouping == "none"
    assert s.sync.retry_failed_after == "1d"
    assert s.sync.include == ()
    assert s.sync.exclude == ()
    assert s.sync.skip_metadata == ()
    assert s.sync.affinity_key is None
    assert s.logging.verbose == 0
    assert s.logging.format == "text"
    assert s.metrics.file is None
    assert s.metrics.instance is None


def test_bootstrap_default_schedule(settings_path: Path) -> None:
    """verifies default schedule is */15 * * * * when BLACKVUESYNC_SCHEDULE unset."""
    store = _make_store(settings_path, env={})
    assert store.get().schedule.cron_expression == "*/15 * * * *"


def test_bootstrap_generates_session_secret(settings_path: Path) -> None:
    """verifies session_secret is a non-empty token generated on bootstrap."""
    store = _make_store(settings_path)
    secret = store.get().auth.session_secret
    assert isinstance(secret, str)
    assert len(secret) == 64  # secrets.token_hex(32) yields 64 hex chars


def test_bootstrap_is_idempotent(settings_path: Path) -> None:
    """verifies second load returns same settings without re-bootstrapping."""
    store1 = _make_store(settings_path, env={"ADDRESS": "10.0.0.1"})
    secret1 = store1.get().auth.session_secret

    # second load: env vars would produce different values but file is canonical
    with patch.dict(os.environ, {"ADDRESS": "10.0.0.99"}, clear=False):
        store2 = SettingsStore(settings_path)
    # address from file, not from env on second load
    assert store2.get().connection.address == "10.0.0.1"
    assert store2.get().auth.session_secret == secret1


def test_bootstrap_warns_retired_env_vars(
    settings_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """verifies CRON and RUN_ONCE env vars produce warning log entries."""
    import logging

    with caplog.at_level(logging.WARNING, logger="blackvuesync.settings"):
        _make_store(settings_path, env={"CRON": "true", "RUN_ONCE": "1"})

    messages = [r.message for r in caplog.records]
    assert any("CRON" in m for m in messages)
    assert any("RUN_ONCE" in m for m in messages)


def test_bootstrap_skip_metadata_from_env(settings_path: Path) -> None:
    """verifies SKIP_METADATA env var populates skip_metadata tuple."""
    store = _make_store(settings_path, env={"SKIP_METADATA": "tg"})
    skip = store.get().sync.skip_metadata
    assert set(skip) == {"t", "g"}


def test_bootstrap_metrics_env_vars(settings_path: Path) -> None:
    """verifies METRICS_* env vars populate metrics settings."""
    env = {
        "METRICS_FILE": "/tmp/metrics.prom",
        "METRICS_JOB": "myjob",
        "METRICS_INSTANCE": "myhost",
        "METRICS_STATE_FILE": "/tmp/state.json",
    }
    store = _make_store(settings_path, env=env)
    m = store.get().metrics
    assert m.file == "/tmp/metrics.prom"
    assert m.job == "myjob"
    assert m.instance == "myhost"
    assert m.state_file == "/tmp/state.json"


# ---------------------------------------------------------------------------
# file-perms safety check
# ---------------------------------------------------------------------------


def test_load_rejects_wide_permissions(tmp_path: Path) -> None:
    """verifies SettingsStore refuses to load a settings file with mode 0o644."""
    settings_path = tmp_path / "settings.json"
    # create a valid settings file with insecure permissions
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump({"version": 1}, f)
    os.chmod(settings_path, 0o644)

    with pytest.raises(PermissionError, match="insecure permissions"):
        SettingsStore(settings_path)


def test_load_accepts_strict_permissions(tmp_path: Path) -> None:
    """verifies SettingsStore loads a settings file with mode 0o600 without error."""
    settings_path = tmp_path / "settings.json"
    # create a valid file with correct permissions
    _make_store(settings_path, env={"ADDRESS": "192.168.1.1"})
    # should not raise
    store = SettingsStore(settings_path)
    assert store.get().connection.address == "192.168.1.1"


# ---------------------------------------------------------------------------
# schema migration
# ---------------------------------------------------------------------------


def test_migrate_from_version_zero() -> None:
    """verifies migrate() is called and updates version for pre-v1 dicts."""
    raw = {"version": 0, "connection": {"address": "10.0.0.1"}}
    migrated = migrate(raw, from_version=0)
    assert migrated["version"] == 1
    assert migrated["connection"]["address"] == "10.0.0.1"


def test_migrate_passthrough_for_current_version() -> None:
    """verifies migrate() returns dict unchanged for current schema version."""
    raw: dict[str, Any] = {"version": 1, "web": {"port": 9090}}
    # migrate is a pass-through for version >= 1
    migrated = migrate(raw, from_version=1)
    assert migrated["web"]["port"] == 9090


def test_store_invokes_migrate_for_old_schema(tmp_path: Path) -> None:
    """verifies SettingsStore calls migrate() when loading an older schema version."""
    settings_path = tmp_path / "settings.json"
    # write a v0 file with correct perms
    raw = {"version": 0, "connection": {"address": "10.0.0.1"}}
    fd = os.open(str(settings_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(raw, f)

    # should load without error via migrate()
    store = SettingsStore(settings_path)
    assert store.get().version == 1


# ---------------------------------------------------------------------------
# additional validator coverage
# ---------------------------------------------------------------------------


def test_schedule_validate_empty_timezone() -> None:
    """verifies empty timezone is rejected."""
    s = ScheduleSettings(cron_expression="*/15 * * * *", timezone="")
    errors = s.validate()
    assert any("timezone" in e for e in errors)


def test_sync_validate_invalid_skip_metadata_token() -> None:
    """verifies unknown skip_metadata tokens are rejected."""
    # use object workaround to bypass Literal constraint at runtime
    s = SyncSettings(
        skip_metadata=("t", "z"),  # type: ignore[arg-type]
    )
    errors = s.validate()
    assert any("skip_metadata" in e for e in errors)


def test_logging_validate_negative_backup_count() -> None:
    """verifies negative file_backup_count is rejected."""
    s = LoggingSettings(file_backup_count=-1)
    errors = s.validate()
    assert any("file_backup_count" in e for e in errors)


def test_logging_validate_zero_ring_buffer_capacity() -> None:
    """verifies zero ring_buffer_capacity is rejected."""
    s = LoggingSettings(ring_buffer_capacity=0)
    errors = s.validate()
    assert any("ring_buffer_capacity" in e for e in errors)


def test_web_validate_zero_session_lifetime() -> None:
    """verifies zero session_lifetime_hours is rejected."""
    s = WebSettings(session_lifetime_hours=0)
    errors = s.validate()
    assert any("session_lifetime_hours" in e for e in errors)


def test_listener_exception_does_not_propagate(settings_path: Path) -> None:
    """verifies a listener that raises does not abort the update."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})

    def bad_listener(_old: Settings, _new: Settings) -> None:
        raise RuntimeError("listener failure")

    store.on_change(bad_listener)
    # should not raise; exception is logged and swallowed
    result = store.update(
        lambda s: dataclasses.replace(s, web=dataclasses.replace(s.web, port=5050))
    )
    assert result.web.port == 5050


def test_bootstrap_admin_password_env_var_logs_info(
    settings_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """verifies BLACKVUESYNC_ADMIN_PASSWORD env var produces an info log."""
    import logging

    with caplog.at_level(logging.INFO, logger="blackvuesync.settings"):
        _make_store(settings_path, env={"BLACKVUESYNC_ADMIN_PASSWORD": "secret"})

    messages = [r.message for r in caplog.records]
    assert any("password_hash" in m or "ADMIN_PASSWORD" in m for m in messages)


def test_section_from_dict_ignores_unknown_keys() -> None:
    """verifies _section_from_dict skips keys not in the dataclass."""
    from blackvuesync.settings import _section_from_dict

    raw = {"port": 9090, "unknown_field": "ignored"}
    s = _section_from_dict(WebSettings, raw, set())
    assert s.port == 9090


# ---------------------------------------------------------------------------
# thread safety
# ---------------------------------------------------------------------------


def test_store_concurrent_updates_are_safe(settings_path: Path) -> None:
    """verifies concurrent updates do not corrupt stored settings."""
    store = _make_store(settings_path, env={"ADDRESS": "192.168.0.1"})
    errors: list[Exception] = []

    def increment_port() -> None:
        for _ in range(10):
            try:
                store.update(
                    lambda s: dataclasses.replace(
                        s,
                        web=dataclasses.replace(s.web, port=s.web.port + 1),
                    )
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors.append(exc)
            time.sleep(0.001)

    threads = [threading.Thread(target=increment_port) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent errors: {errors}"
    final_port = store.get().web.port
    # should be > 8080 and within valid range
    assert 8080 < final_port <= 65535


# ---------------------------------------------------------------------------
# C1: Literal field validators (Phase C cleanup)
# ---------------------------------------------------------------------------


def test_sync_validate_invalid_priority() -> None:
    """verifies invalid priority value is rejected."""
    s = SyncSettings(priority="newest")  # type: ignore[arg-type]
    errors = s.validate()
    assert any("priority" in e for e in errors)


def test_sync_validate_invalid_grouping() -> None:
    """verifies invalid grouping value is rejected."""
    s = SyncSettings(grouping="hourly")  # type: ignore[arg-type]
    errors = s.validate()
    assert any("grouping" in e for e in errors)


def test_logging_validate_invalid_format() -> None:
    """verifies invalid logging format value is rejected."""
    s = LoggingSettings(format="yaml")  # type: ignore[arg-type]
    errors = s.validate()
    assert any("format" in e for e in errors)


def test_auth_validate_invalid_mode() -> None:
    """verifies invalid auth mode value is rejected."""
    s = AuthSettings(mode="basic")  # type: ignore[arg-type]
    errors = s.validate()
    assert any("mode" in e for e in errors)


# ---------------------------------------------------------------------------
# C2: Validation errors logged on load (Phase C cleanup)
# ---------------------------------------------------------------------------


def test_load_logs_validation_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """verifies settings with invalid fields emit WARNING log entries on load."""
    import logging

    settings_path = tmp_path / "settings.json"
    raw: dict[str, Any] = {
        "version": 1,
        "sync": {"priority": "invalid_priority"},
    }
    fd = os.open(str(settings_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(raw, f)

    with caplog.at_level(logging.WARNING, logger="blackvuesync.settings"):
        SettingsStore(settings_path)

    messages = [r.message for r in caplog.records]
    assert any("priority" in m for m in messages)


class TestSchedulePaused:
    """tests for the schedule.paused field added in sub-project #2."""

    def test_paused_defaults_to_false(self, tmp_path: Path) -> None:
        """new ScheduleSettings has paused=False by default."""
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store = SettingsStore(tmp_path / "settings.json")
        assert store.get().schedule.paused is False

    def test_paused_persists_round_trip(self, tmp_path: Path) -> None:
        """setting paused=True persists to disk and survives a reload."""
        path = tmp_path / "settings.json"
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store = SettingsStore(path)
            store.update(
                lambda s: dataclasses.replace(
                    s, schedule=dataclasses.replace(s.schedule, paused=True)
                )
            )
        # reload from disk
        with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
            store2 = SettingsStore(path)
        assert store2.get().schedule.paused is True

    def test_paused_validate_accepts_any_bool(self) -> None:
        """validate() returns no errors for either paused value."""
        from blackvuesync.settings import ScheduleSettings

        assert ScheduleSettings(paused=False).validate() == []
        assert ScheduleSettings(paused=True).validate() == []
