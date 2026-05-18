"""flask application factory for the blackvuesync web server."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from flask import Flask, Response, request
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from blackvuesync.settings import SettingsStore


def create_app(
    settings_store: SettingsStore,
    testing: bool = False,
) -> Flask:
    """constructs and configures the Flask app with auth, routes, and middleware.

    attaches settings_store to the app instance so routes can access it
    via current_app.settings_store without importing a global singleton.
    """
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.settings_store = settings_store  # type: ignore[attr-defined]

    settings = settings_store.get()
    secret = settings.auth.session_secret or "dev-insecure-placeholder"

    app.config.update(
        TESTING=testing,
        SECRET_KEY=secret.encode() if isinstance(secret, str) else secret,
        SESSION_COOKIE_NAME="bvs_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=settings.web.session_lifetime_hours),
        WTF_CSRF_HEADERS=["X-CSRFToken"],
        WTF_CSRF_ENABLED=not testing,
    )

    CSRFProtect(app)

    # proxy fix: honors X-Forwarded-For / X-Forwarded-Proto from one trusted proxy
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    # blueprints are imported inside create_app to avoid circular imports at
    # module level; flask routes depend on the app context. pylint C0415 is
    # suppressed below because the deferred import is intentional.

    # pylint: disable=import-outside-toplevel
    from blackvuesync.server.routes.auth import bp as auth_bp
    from blackvuesync.server.routes.health import bp as health_bp
    from blackvuesync.server.routes.ui import bp as ui_bp

    # pylint: enable=import-outside-toplevel

    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(health_bp)

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        """sets security-related HTTP response headers on every response."""
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://*.tile.openstreetmap.org; "
            "media-src 'self' blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains"
            )
        return response

    return app


__all__: list[str] = ["create_app"]


def _store_attr_info() -> dict[str, Any]:
    """documents dynamic attributes attached to Flask app instances at runtime."""
    return {
        "settings_store": "SettingsStore instance attached by create_app()",
    }
