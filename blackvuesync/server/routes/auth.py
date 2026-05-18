"""authentication routes: /login, /logout, /first-run."""

from __future__ import annotations

import dataclasses
import time
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers import Response

from blackvuesync.server.auth import (
    clear_login_failures,
    hash_password,
    is_login_locked_out,
    login_required,
    record_login_failure,
    verify_password,
)

bp = Blueprint("auth_bp", __name__)

# minimum response time for a failed login in seconds; guards against timing attacks
_MIN_FAILURE_SECONDS = 1.5

# minimum password length for first-run setup
_MIN_PASSWORD_LENGTH = 12


@bp.before_app_request
def redirect_to_first_run() -> Response | None:
    """redirects any request to /first-run when no password has been set.

    skips the redirect for the first-run page itself, static assets, and
    health-check endpoints so the wizard and infrastructure routes remain
    accessible.
    """
    exempt_prefixes = ("/first-run", "/static", "/healthz", "/readyz")
    if any(request.path.startswith(p) for p in exempt_prefixes):
        return None
    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    if not settings.auth.password_hash:
        return redirect(url_for("auth_bp.first_run"))
    return None


@bp.route("/login", methods=["GET"])
def login() -> str | Response:
    """renders the login form; redirects to /first-run if no password is set."""
    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    if not settings.auth.password_hash:
        return redirect(url_for("auth_bp.first_run"))
    return render_template("login.html")


@bp.route("/login", methods=["POST"])
def login_post() -> tuple[str, int] | Response:
    """validates credentials; sets session on success; pads timing on failure."""
    start = time.perf_counter()

    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    auth = settings.auth
    ip = request.remote_addr or "unknown"

    if is_login_locked_out(ip):
        _pad_response_time(start)
        return (
            render_template(
                "login.html", error="too many failed attempts; try again later"
            ),
            429,
        )

    username = request.form.get("username", "")
    password = request.form.get("password", "")

    # always verify (even for wrong usernames) to produce uniform timing
    stored_hash = (
        auth.password_hash
        if auth.password_hash
        else "$argon2id$v=19$m=65536,t=3,p=4$" + "a" * 16 + "$" + "b" * 32
    )
    username_ok = username == auth.username
    password_ok = verify_password(stored_hash, password)

    if not (username_ok and password_ok):
        record_login_failure(ip)
        _pad_response_time(start)
        return render_template("login.html", error="invalid username or password"), 401

    clear_login_failures(ip)
    session.clear()
    session["user"] = auth.username
    session.permanent = True

    next_url = (
        request.args.get("next")
        or request.form.get("next")
        or url_for("ui_bp.dashboard")
    )
    # prevent open-redirect: reject any url with a scheme or netloc (e.g.
    # //evil.com, http://evil.com, javascript:...) and any non-path value.
    _parsed = urlparse(next_url)
    if _parsed.scheme or _parsed.netloc or not next_url.startswith("/"):
        next_url = "/"

    return redirect(next_url)


def _pad_response_time(start: float) -> None:
    """sleeps long enough to make the total response time at least _MIN_FAILURE_SECONDS."""
    elapsed = time.perf_counter() - start
    remaining = _MIN_FAILURE_SECONDS - elapsed
    if remaining > 0:
        time.sleep(remaining)


@bp.route("/logout", methods=["POST"])
@login_required
def logout() -> Response:
    """clears the session and redirects to /login."""
    session.clear()
    return redirect(url_for("auth_bp.login"))


@bp.route("/first-run", methods=["GET"])
def first_run() -> str | Response:
    """renders the first-run wizard; redirects to /login if a password is already set."""
    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    if settings.auth.password_hash:
        return redirect(url_for("auth_bp.login"))
    return render_template("first_run.html")


@bp.route("/first-run", methods=["POST"])
def first_run_post() -> tuple[str, int] | Response:
    """processes the first-run form: validates and stores the initial password."""
    settings = current_app.settings_store.get()  # type: ignore[attr-defined]
    if settings.auth.password_hash:
        return redirect(url_for("auth_bp.login"))

    username = request.form.get("username", "admin").strip() or "admin"
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if len(password) < _MIN_PASSWORD_LENGTH:
        return (
            render_template(
                "first_run.html",
                error=f"password must be at least {_MIN_PASSWORD_LENGTH} characters",
                username=username,
            ),
            400,
        )

    if password != confirm:
        return (
            render_template(
                "first_run.html",
                error="passwords do not match",
                username=username,
            ),
            400,
        )

    pw_hash = hash_password(password)
    current_app.settings_store.update(  # type: ignore[attr-defined]
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(s.auth, username=username, password_hash=pw_hash),
        )
    )
    return redirect(url_for("auth_bp.login"))
