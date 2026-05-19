"""api settings routes: GET /api/settings, PATCH /api/settings/<section>."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from flask import Blueprint, Response, current_app

from blackvuesync.server.auth import login_required
from blackvuesync.settings import _SECTION_FIELDS, Settings, SettingsStore

api_settings_bp = Blueprint("api_settings_bp", __name__, url_prefix="/api/settings")

_MIME_JSON = "application/json"

# fields whose values must never be sent to the client; the literal "***"
# sentinel is returned instead. the same sentinel is also the signal on
# PATCH that means "leave this field unchanged".
_REDACTED_FIELDS: dict[str, set[str]] = {
    "auth": {"password_hash", "session_secret"},
}

_REDACTED_SENTINEL = "***"


def _section_to_dict(name: str, section: Any) -> dict[str, Any]:
    """converts a section dataclass to a dict, redacting secret fields and
    adding the _tier annotation."""
    d: dict[str, Any] = dataclasses.asdict(section)
    redact = _REDACTED_FIELDS.get(name, set())
    for field_name in redact:
        if field_name in d and d[field_name]:
            d[field_name] = _REDACTED_SENTINEL
    d["_tier"] = section.__class__.TIER
    return d


def _settings_to_dict(s: Settings) -> dict[str, Any]:
    """converts the full Settings to a redacted dict with per-section tier."""
    out: dict[str, Any] = {"version": s.version}
    for name in _SECTION_FIELDS:
        out[name] = _section_to_dict(name, getattr(s, name))
    return out


@api_settings_bp.route("", methods=["GET"])
@login_required
def get_settings() -> Response:
    """returns the full settings as JSON; secrets redacted to '***'."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    return Response(
        json.dumps(_settings_to_dict(store.get())),
        status=200,
        mimetype=_MIME_JSON,
    )


__all__ = ["api_settings_bp"]
