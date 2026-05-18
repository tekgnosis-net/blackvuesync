"""placeholder ui routes: /, /settings, /logs, /stats, /viewer."""

from __future__ import annotations

from flask import Blueprint, render_template

from blackvuesync import __version__
from blackvuesync.server.auth import login_required

bp = Blueprint("ui_bp", __name__)


@bp.route("/", methods=["GET"])
@login_required
def dashboard() -> str:
    """renders the dashboard placeholder page."""
    return render_template(
        "_placeholders/dashboard.html",
        version=__version__,
        page="dashboard",
    )


@bp.route("/settings", methods=["GET"])
@login_required
def settings() -> str:
    """renders the settings placeholder page."""
    return render_template(
        "_placeholders/settings.html",
        version=__version__,
        page="settings",
    )


@bp.route("/logs", methods=["GET"])
@login_required
def logs() -> str:
    """renders the log viewer placeholder page."""
    return render_template(
        "_placeholders/logs.html",
        version=__version__,
        page="logs",
    )


@bp.route("/stats", methods=["GET"])
@login_required
def stats() -> str:
    """renders the statistics placeholder page."""
    return render_template(
        "_placeholders/stats.html",
        version=__version__,
        page="stats",
    )


@bp.route("/viewer", methods=["GET"])
@login_required
def viewer() -> str:
    """renders the dashcam viewer placeholder page."""
    return render_template(
        "_placeholders/viewer.html",
        version=__version__,
        page="viewer",
    )
