"""api auth routes: GET /api/auth/me, POST /api/auth/password, DELETE /api/auth/sessions."""

from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, g

from blackvuesync.server.auth import login_required
from blackvuesync.settings import SettingsStore

api_auth_bp = Blueprint("api_auth_bp", __name__, url_prefix="/api/auth")

_MIME_JSON = "application/json"


@api_auth_bp.route("/me", methods=["GET"])
@login_required
def me() -> Response:
    """returns the current authenticated user and auth mode."""
    store: SettingsStore = current_app.settings_store  # type: ignore[attr-defined]
    mode = store.get().auth.mode
    body = json.dumps({"username": g.current_user, "mode": mode})
    return Response(body, status=200, mimetype=_MIME_JSON)


__all__ = ["api_auth_bp"]
