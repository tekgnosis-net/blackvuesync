"""authentication helpers: password hashing, login_required decorator, rate limiting."""

from __future__ import annotations

import functools
import threading
import time
from collections import deque
from typing import Any, Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import (
    current_app,
    g,
    redirect,
    request,
    session,
    url_for,
)
from werkzeug.exceptions import abort

# argon2id parameters are locked per design spec section 3
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# rate-limit sliding window: 10 failures within 10 minutes locks the IP
_FAILURE_WINDOW_SECONDS = 600
_FAILURE_THRESHOLD = 10
_LOCKOUT_SECONDS = 900

# maps ip -> deque of monotonic failure timestamps
_failure_timestamps: dict[str, deque[float]] = {}
# maps ip -> monotonic timestamp when the lockout expires
_locked_until: dict[str, float] = {}
_rate_limit_lock = threading.Lock()


def hash_password(plaintext: str) -> str:
    """hashes plaintext with argon2id using locked parameters; returns the encoded hash."""
    result: str = _HASHER.hash(plaintext)
    return result


def verify_password(stored_hash: str, plaintext: str) -> bool:
    """verifies plaintext against stored_hash; returns False on mismatch, True on success."""
    try:
        result: bool = _HASHER.verify(stored_hash, plaintext)
        return result
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """returns True if the stored hash was produced with different parameters."""
    result: bool = _HASHER.check_needs_rehash(stored_hash)
    return result


# ---------------------------------------------------------------------------
# rate-limit helpers
# ---------------------------------------------------------------------------


def is_login_locked_out(ip: str) -> bool:
    """returns True if ip is currently locked out."""
    with _rate_limit_lock:
        now = time.monotonic()
        # check explicit lockout-until timestamp first
        until = _locked_until.get(ip, 0.0)
        if now < until:
            return True
        # prune expired lockout entries to bound memory
        if ip in _locked_until and _locked_until[ip] <= now:
            del _locked_until[ip]
        # also check sliding window in case a lockout was never set
        window_start = now - _FAILURE_WINDOW_SECONDS
        dq = _failure_timestamps.get(ip)
        if dq is None:
            return False
        while dq and dq[0] < window_start:
            dq.popleft()
        return len(dq) >= _FAILURE_THRESHOLD


def record_login_failure(ip: str) -> None:
    """records a failed login attempt for ip; sets a lockout if threshold is reached."""
    with _rate_limit_lock:
        now = time.monotonic()
        if ip not in _failure_timestamps:
            _failure_timestamps[ip] = deque()
        _failure_timestamps[ip].append(now)
        # count failures within the current window
        window_start = now - _FAILURE_WINDOW_SECONDS
        recent = sum(1 for t in _failure_timestamps[ip] if t >= window_start)
        if recent >= _FAILURE_THRESHOLD:
            _locked_until[ip] = now + _LOCKOUT_SECONDS


def clear_login_failures(ip: str) -> None:
    """clears all recorded failures and any lockout for ip (called on successful login)."""
    with _rate_limit_lock:
        _failure_timestamps.pop(ip, None)
        _locked_until.pop(ip, None)


# ---------------------------------------------------------------------------
# login_required decorator
# ---------------------------------------------------------------------------

_ViewFunc = Callable[..., Any]


def login_required(view: _ViewFunc) -> _ViewFunc:
    """decorator enforcing authentication per the current auth.mode setting.

    reads auth.mode fresh on every request so a settings change takes
    effect without restarting the server.

    - "none": passes through; sets g.current_user = "anonymous".
    - "proxy": reads the proxy_user_header from trusted proxies; aborts 401
      if the remote IP is not trusted or the header is absent.
    - "login": checks session["user"]; redirects to /login with ?next= if
      absent.
    """

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        settings = current_app.settings_store.get()  # type: ignore[attr-defined]
        auth = settings.auth
        mode = auth.mode

        if mode == "none":
            g.current_user = "anonymous"
            return view(*args, **kwargs)

        if mode == "proxy":
            remote_ip = request.remote_addr or ""
            if remote_ip not in auth.trusted_proxies:
                abort(401)
            header_value = request.headers.get(auth.proxy_user_header, "")
            if not header_value:
                abort(401)
            g.current_user = header_value
            return view(*args, **kwargs)

        # mode == "login"
        user = session.get("user")
        if not user:
            return redirect(url_for("auth_bp.login", next=request.path))
        g.current_user = user
        return view(*args, **kwargs)

    return wrapped
