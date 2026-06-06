"""settings schema, validation, atomic persistence, and env-var bootstrap."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, ClassVar, Literal

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

PropagationTier = Literal["immediate", "next_tick", "restart"]

# duration regex pattern reused from sync.py grammar: <number>[shdw]
_DURATION_RE_PATTERN = r"^\d+[shdw]?$"

# valid cron token characters
_CRON_FIELD_RE_PATTERN = r"^[0-9*/,\-]+$"

# valid skip-metadata type codes
_VALID_SKIP_METADATA = frozenset(("t", "3", "g"))

# valid Literal field values for member-check validation
_VALID_PRIORITIES = frozenset(("date", "rdate", "type"))
_VALID_GROUPINGS = frozenset(("none", "daily", "weekly", "monthly", "yearly"))
_VALID_LOG_FORMATS = frozenset(("text", "json"))
_VALID_AUTH_MODES = frozenset(("login", "none", "proxy"))


def _valid_duration(value: str) -> bool:
    """returns True if value matches the duration grammar used by blackvuesync."""
    return bool(re.match(_DURATION_RE_PATTERN, value))


def _valid_cron(value: str) -> bool:
    """returns True if value looks like a valid 5-field cron expression."""
    parts = value.split()
    if len(parts) != 5:
        return False
    return all(re.match(_CRON_FIELD_RE_PATTERN, p) for p in parts)


# ---------------------------------------------------------------------------
# section dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionSettings:
    """connection settings for the dashcam."""

    TIER: ClassVar[PropagationTier] = "restart"

    address: str = ""
    timeout_seconds: float = 10.0

    def validate(self) -> list[str]:
        """validates connection settings; returns a list of error strings."""
        errors: list[str] = []
        if not self.address:
            errors.append("connection.address must not be empty")
        if self.timeout_seconds <= 0:
            errors.append("connection.timeout_seconds must be greater than zero")
        return errors


@dataclass(frozen=True)
class ScheduleSettings:
    """sync schedule settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    cron_expression: str = "*/15 * * * *"
    timezone: str = "UTC"
    paused: bool = False

    def validate(self) -> list[str]:
        """validates schedule settings; returns a list of error strings."""
        errors: list[str] = []
        if not _valid_cron(self.cron_expression):
            errors.append(
                f"schedule.cron_expression is not a valid 5-field cron expression: "
                f"{self.cron_expression!r}"
            )
        if not self.timezone:
            errors.append("schedule.timezone must not be empty")
        return errors


@dataclass(frozen=True)
class SyncSettings:
    """recording sync settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    priority: Literal["date", "rdate", "type"] = "date"
    grouping: Literal["none", "daily", "weekly", "monthly", "yearly"] = "none"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    retry_failed_after: str = "1d"
    skip_metadata: tuple[Literal["t", "3", "g"], ...] = ()
    affinity_key: str | None = None

    def validate(self) -> list[str]:
        """validates sync settings; returns a list of error strings."""
        errors: list[str] = []
        if self.priority not in _VALID_PRIORITIES:
            errors.append(
                f"sync.priority must be one of {sorted(_VALID_PRIORITIES)!r}, "
                f"got {self.priority!r}"
            )
        if self.grouping not in _VALID_GROUPINGS:
            errors.append(
                f"sync.grouping must be one of {sorted(_VALID_GROUPINGS)!r}, "
                f"got {self.grouping!r}"
            )
        if not _valid_duration(self.retry_failed_after):
            errors.append(
                f"sync.retry_failed_after is not a valid duration: "
                f"{self.retry_failed_after!r}"
            )
        invalid_meta = {str(m) for m in self.skip_metadata} - _VALID_SKIP_METADATA
        if invalid_meta:
            errors.append(
                f"sync.skip_metadata contains invalid tokens: {sorted(invalid_meta)!r}"
            )
        return errors


@dataclass(frozen=True)
class RetentionSettings:
    """recording retention settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    keep: str = "2w"
    max_used_disk_percent: int = 90

    def validate(self) -> list[str]:
        """validates retention settings; returns a list of error strings."""
        errors: list[str] = []
        if not _valid_duration(self.keep):
            errors.append(f"retention.keep is not a valid duration: {self.keep!r}")
        if not 1 <= self.max_used_disk_percent <= 100:
            errors.append("retention.max_used_disk_percent must be between 1 and 100")
        return errors


@dataclass(frozen=True)
class LoggingSettings:
    """logging output settings."""

    TIER: ClassVar[PropagationTier] = "immediate"

    verbose: int = 0
    quiet: bool = False
    format: Literal["text", "json"] = "text"
    file_max_bytes: int = 10 * 1024 * 1024
    file_backup_count: int = 5
    ring_buffer_capacity: int = 1000

    def validate(self) -> list[str]:
        """validates logging settings; returns a list of error strings."""
        errors: list[str] = []
        if self.format not in _VALID_LOG_FORMATS:
            errors.append(
                f"logging.format must be one of {sorted(_VALID_LOG_FORMATS)!r}, "
                f"got {self.format!r}"
            )
        if self.verbose < 0:
            errors.append("logging.verbose must be >= 0")
        if self.file_max_bytes <= 0:
            errors.append("logging.file_max_bytes must be greater than zero")
        if self.file_backup_count < 0:
            errors.append("logging.file_backup_count must be >= 0")
        if self.ring_buffer_capacity <= 0:
            errors.append("logging.ring_buffer_capacity must be greater than zero")
        return errors


@dataclass(frozen=True)
class MetricsSettings:
    """prometheus metrics export settings."""

    TIER: ClassVar[PropagationTier] = "immediate"

    file: str | None = None
    pushgateway_url: str | None = None
    job: str = "blackvuesync"
    instance: str | None = None
    state_file: str = "/config/metrics-state.json"

    def validate(self) -> list[str]:
        """validates metrics settings; returns a list of error strings."""
        return []


@dataclass(frozen=True)
class StatsSettings:
    """statistics time-series store settings."""

    TIER: ClassVar[PropagationTier] = "next_tick"

    retention_days: int = 365  # prune run records older than this; 0 keeps all

    def validate(self) -> list[str]:
        """validates stats settings; returns a list of error strings."""
        if self.retention_days < 0:
            return ["stats.retention_days must be zero or greater"]
        return []


@dataclass(frozen=True)
class WebSettings:
    """web server settings."""

    TIER: ClassVar[PropagationTier] = "restart"

    port: int = 8080
    session_lifetime_hours: int = 24

    def validate(self) -> list[str]:
        """validates web settings; returns a list of error strings."""
        errors: list[str] = []
        if not 1 <= self.port <= 65535:
            errors.append("web.port must be between 1 and 65535")
        if self.session_lifetime_hours <= 0:
            errors.append("web.session_lifetime_hours must be greater than zero")
        return errors


@dataclass(frozen=True)
class AuthSettings:
    """authentication settings."""

    TIER: ClassVar[PropagationTier] = "immediate"

    mode: Literal["login", "none", "proxy"] = "login"
    username: str = "admin"
    password_hash: str = ""
    session_secret: str = ""
    trusted_proxies: tuple[str, ...] = ()
    proxy_user_header: str = "X-Remote-User"

    def validate(self) -> list[str]:
        """validates auth settings; returns a list of error strings."""
        errors: list[str] = []
        if self.mode not in _VALID_AUTH_MODES:
            errors.append(
                f"auth.mode must be one of {sorted(_VALID_AUTH_MODES)!r}, "
                f"got {self.mode!r}"
            )
        if self.mode == "proxy":
            if not self.trusted_proxies:
                errors.append(
                    "auth.trusted_proxies must not be empty when mode is 'proxy'"
                )
            if not self.proxy_user_header:
                errors.append(
                    "auth.proxy_user_header must not be empty when mode is 'proxy'"
                )
        return errors


@dataclass(frozen=True)
class SystemSettings:
    """system-level settings."""

    TIER: ClassVar[PropagationTier] = "restart"

    destination: str = "/recordings"
    dry_run: bool = False

    def validate(self) -> list[str]:
        """validates system settings; returns a list of error strings."""
        errors: list[str] = []
        if not self.destination:
            errors.append("system.destination must not be empty")
        return errors


# ---------------------------------------------------------------------------
# top-level settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:  # pylint: disable=too-many-instance-attributes
    """top-level settings container."""

    version: int = SCHEMA_VERSION
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    sync: SyncSettings = field(default_factory=SyncSettings)
    retention: RetentionSettings = field(default_factory=RetentionSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    metrics: MetricsSettings = field(default_factory=MetricsSettings)
    stats: StatsSettings = field(default_factory=StatsSettings)
    web: WebSettings = field(default_factory=WebSettings)
    auth: AuthSettings = field(default_factory=AuthSettings)
    system: SystemSettings = field(default_factory=SystemSettings)

    def validate(self) -> list[str]:
        """validates all sections; returns aggregated list of error strings."""
        errors: list[str] = []
        errors.extend(self.connection.validate())
        errors.extend(self.schedule.validate())
        errors.extend(self.sync.validate())
        errors.extend(self.retention.validate())
        errors.extend(self.logging.validate())
        errors.extend(self.metrics.validate())
        errors.extend(self.stats.validate())
        errors.extend(self.web.validate())
        errors.extend(self.auth.validate())
        errors.extend(self.system.validate())
        return errors


# ---------------------------------------------------------------------------
# serialization helpers
# ---------------------------------------------------------------------------


_SECTION_FIELDS: dict[str, type] = {
    "connection": ConnectionSettings,
    "schedule": ScheduleSettings,
    "sync": SyncSettings,
    "retention": RetentionSettings,
    "logging": LoggingSettings,
    "metrics": MetricsSettings,
    "stats": StatsSettings,
    "web": WebSettings,
    "auth": AuthSettings,
    "system": SystemSettings,
}

# fields whose values are tuple[str, ...] and must be round-tripped as lists
_TUPLE_FIELDS: dict[str, set[str]] = {
    "sync": {"include", "exclude", "skip_metadata"},
    "auth": {"trusted_proxies"},
}

# fields that must never be sent to clients; the redaction sentinel "***" is
# returned instead and stripped again on inbound patches.
_REDACTED_FIELDS: dict[str, set[str]] = {
    "auth": {"password_hash", "session_secret"},
}


def _section_to_dict(section: object) -> dict[str, Any]:
    """converts a frozen section dataclass to a JSON-serializable dict."""
    result: dict[str, Any] = {}
    for f in fields(section):  # type: ignore[arg-type]
        value = getattr(section, f.name)
        if isinstance(value, tuple):
            result[f.name] = list(value)
        else:
            result[f.name] = value
    return result


def _settings_to_dict(settings: Settings) -> dict[str, Any]:
    """converts a Settings object to a JSON-serializable dict."""
    result: dict[str, Any] = {"version": settings.version}
    for section_name in _SECTION_FIELDS:
        result[section_name] = _section_to_dict(getattr(settings, section_name))
    return result


def _section_from_dict(cls: type, raw: dict[str, Any], tuple_fields: set[str]) -> Any:
    """constructs a frozen section dataclass from a dict, restoring tuples."""
    kwargs: dict[str, Any] = {}
    valid_field_names = {f.name for f in fields(cls)}
    for key, value in raw.items():
        if key not in valid_field_names:
            continue
        if key in tuple_fields and isinstance(value, list):
            kwargs[key] = tuple(value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def _settings_from_dict(raw: dict[str, Any]) -> Settings:
    """constructs a Settings object from a raw dict."""
    kwargs: dict[str, Any] = {"version": raw.get("version", SCHEMA_VERSION)}
    for section_name, section_cls in _SECTION_FIELDS.items():
        section_raw = raw.get(section_name, {})
        tuple_fields = _TUPLE_FIELDS.get(section_name, set())
        kwargs[section_name] = _section_from_dict(
            section_cls, section_raw, tuple_fields
        )
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# schema migration
# ---------------------------------------------------------------------------


def migrate(raw: dict[str, Any], from_version: int) -> dict[str, Any]:
    """migrates a raw settings dict from an older schema version to current.

    currently a pass-through since only schema version 1 exists. future
    versions add migration steps here.
    """
    if from_version < 1:
        # placeholder: no-op migration from pre-1 to 1
        raw = dict(raw)
        raw["version"] = 1
    return raw


# ---------------------------------------------------------------------------
# SettingsStore
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    """raised when Settings.validate() returns errors."""

    def __init__(self, errors: list[str]) -> None:
        """initializes with a list of validation error messages."""
        self.errors = errors
        super().__init__("; ".join(errors))


class SettingsStore:
    """thread-safe settings store with atomic persistence and change notifications."""

    def __init__(self, path: Path) -> None:
        """initializes the store and loads or bootstraps settings from path."""
        self._path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Settings, Settings], None]] = []
        self._settings = self._load_or_bootstrap()

    def get(self) -> Settings:
        """returns the current settings snapshot."""
        with self._lock:
            return self._settings

    def update(self, mutation: Callable[[Settings], Settings]) -> Settings:
        """applies mutation, validates, persists atomically, and notifies listeners."""
        with self._lock:
            old = self._settings
            new = mutation(old)
            errors = new.validate()
            if errors:
                raise ValidationError(errors)
            self._save(new)
            self._settings = new
        # snapshots the listeners list so a callback that registers a new
        # listener (via on_change()) cannot mutate the list mid-iteration.
        # (suppresses S7504.)
        for listener in list(self._listeners):  # NOSONAR
            try:
                listener(old, new)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("settings change listener raised an exception")
        return new

    def on_change(self, listener: Callable[[Settings, Settings], None]) -> None:
        """registers a listener called after each successful update."""
        with self._lock:
            self._listeners.append(listener)

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def _save(self, settings: Settings) -> None:
        """writes settings atomically to the store's path with 0600 perms."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        raw = _settings_to_dict(settings)
        try:
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            # best-effort cleanup of partial temp file; failures are non-fatal
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self._path)
        # fsync the directory to make the rename durable
        dir_fd = os.open(str(self._path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _load(self) -> Settings:
        """reads and parses settings from the store's path."""
        # checks file permissions before reading sensitive settings
        file_stat = os.stat(self._path)
        if file_stat.st_mode & 0o077:
            raise PermissionError(
                f"settings file {self._path} has insecure permissions "
                f"(mode {oct(stat.S_IMODE(file_stat.st_mode))}); "
                f"expected 0o600 or stricter"
            )
        with open(self._path, encoding="utf-8") as f:
            raw: dict[str, Any] = json.load(f)
        version = raw.get("version", 1)
        if version < SCHEMA_VERSION:
            raw = migrate(raw, version)
        settings = _settings_from_dict(raw)
        for error in settings.validate():
            logger.warning("settings validation error: %s", error)
        return settings

    def _bootstrap_from_env(self) -> Settings:  # pylint: disable=too-many-locals
        """builds initial settings from environment variables on first run."""
        env = os.environ

        # treats empty string as absent; mirrors blackvuesync.sh's
        # [ -n "${X:-}" ] check so Dockerfile ENV defaults like X="" do not
        # produce parse errors (e.g., int("") -> ValueError).
        def _env(key: str, default: str) -> str:
            val = env.get(key, "")
            return val if val else default

        # warns about retired env vars
        if env.get("CRON"):
            logger.warning(
                "env var CRON is ignored; the new service is always-on "
                "and uses settings.schedule"
            )
        if env.get("RUN_ONCE"):
            logger.warning(
                "env var RUN_ONCE is ignored; the new service is always-on "
                "and uses settings.schedule"
            )

        connection = ConnectionSettings(
            address=_env("ADDRESS", ""),
            timeout_seconds=float(_env("TIMEOUT", "10.0")),
        )

        schedule = ScheduleSettings(
            cron_expression=_env("BLACKVUESYNC_SCHEDULE", "*/15 * * * *"),
            timezone=_env("BLACKVUESYNC_TIMEZONE", "UTC"),
        )

        raw_include = _env("INCLUDE", "")
        raw_exclude = _env("EXCLUDE", "")
        raw_skip_meta = _env("SKIP_METADATA", "")
        sync = SyncSettings(
            priority=_env("PRIORITY", "date"),  # type: ignore[arg-type]
            grouping=_env("GROUPING", "none"),  # type: ignore[arg-type]
            include=tuple(raw_include.split(",")) if raw_include else (),
            exclude=tuple(raw_exclude.split(",")) if raw_exclude else (),
            retry_failed_after=_env("RETRY_FAILED_AFTER", "1d"),
            skip_metadata=tuple(raw_skip_meta) if raw_skip_meta else (),  # type: ignore[arg-type]
            affinity_key=_env("AFFINITY_KEY", "") or None,
        )

        retention = RetentionSettings(
            keep=_env("KEEP", "2w"),
            max_used_disk_percent=int(_env("MAX_USED_DISK", "90")),
        )

        verbose_raw = _env("VERBOSE", "0")
        log_settings = LoggingSettings(
            verbose=int(verbose_raw) if verbose_raw.isdigit() else 0,
            quiet=_env("QUIET", "").lower() in ("1", "true", "yes"),
            format=_env("LOG_FORMAT", "text"),  # type: ignore[arg-type]
        )

        metrics = MetricsSettings(
            file=_env("METRICS_FILE", "") or None,
            pushgateway_url=_env("METRICS_PUSHGATEWAY_URL", "") or None,
            job=_env("METRICS_JOB", "blackvuesync"),
            instance=_env("METRICS_INSTANCE", "") or None,
            state_file=_env("METRICS_STATE_FILE", "/config/metrics-state.json"),
        )

        stats = StatsSettings(
            retention_days=int(_env("STATS_RETENTION_DAYS", "365")),
        )

        web = WebSettings(
            port=int(_env("BLACKVUESYNC_PORT", "8080")),
        )

        admin_password = _env("BLACKVUESYNC_ADMIN_PASSWORD", "")
        if admin_password:
            # password hashing deferred to phase c; first-run wizard will hash it
            logger.info(
                "BLACKVUESYNC_ADMIN_PASSWORD set; password_hash will be populated "
                "by the first-run wizard in phase c"
            )
        auth = AuthSettings(
            username=_env("BLACKVUESYNC_ADMIN_USERNAME", "admin"),
            password_hash="",
            session_secret=secrets.token_hex(32),
        )

        settings = Settings(
            connection=connection,
            schedule=schedule,
            sync=sync,
            retention=retention,
            logging=log_settings,
            metrics=metrics,
            stats=stats,
            web=web,
            auth=auth,
        )

        bootstrapped_fields = [
            k
            for k, v in {
                "ADDRESS": env.get("ADDRESS"),
                "TIMEOUT": env.get("TIMEOUT"),
                "BLACKVUESYNC_SCHEDULE": env.get("BLACKVUESYNC_SCHEDULE"),
                "BLACKVUESYNC_PORT": env.get("BLACKVUESYNC_PORT"),
                "BLACKVUESYNC_ADMIN_USERNAME": env.get("BLACKVUESYNC_ADMIN_USERNAME"),
            }.items()
            if v
        ]
        if bootstrapped_fields:
            logger.info(
                "bootstrapped settings from env vars: %s",
                ", ".join(bootstrapped_fields),
            )

        return settings

    def _load_or_bootstrap(self) -> Settings:
        """loads settings from disk if the file exists; otherwise bootstraps from env."""
        if self._path.exists():
            return self._load()
        settings = self._bootstrap_from_env()
        self._save(settings)
        return settings
