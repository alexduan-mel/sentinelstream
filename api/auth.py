"""Session auth: PBKDF2 password hashes and an HMAC-signed session cookie (no session table).

Carried over from the prototype's api.py, without the Holdings unlock flow and the RLS role. The
bootstrap admin row from ``001_baseline.sql`` has no hash; its first successful login with
``AUTH_ADMIN_PASSWORD`` installs one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from psycopg2.extensions import connection as Connection
from pydantic import BaseModel, Field

from api.db import DbConn

AUTH_COOKIE = "ss_session"
AUTH_SESSION_SECONDS = 12 * 60 * 60
AUTH_PASSWORD_ITERATIONS = 600_000
SESSION_SECRET_MIN_LENGTH = 32
ADMIN_PASSWORD_MIN_LENGTH = 12

LOGIN_MAX_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

INCORRECT_CREDENTIALS = "Incorrect username or password"

# Verified against when the username is unknown, so a miss costs as much time as a wrong password.
_DUMMY_HASH: str | None = None


class AuthUser(BaseModel):
    id: int
    username: str
    role: Literal["admin", "user"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, *, iterations: int = AUTH_PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(("pbkdf2_sha256", str(iterations), _b64encode(salt), _b64encode(digest)))


def password_matches(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations_raw, salt_raw, expected_raw = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64decode(salt_raw), int(iterations_raw))
        return hmac.compare_digest(actual, _b64decode(expected_raw))
    except (ValueError, TypeError):
        return False


def _burn_password_check(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    password_matches(password, _DUMMY_HASH)


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------
def _session_secret() -> bytes:
    value = os.getenv("AUTH_SESSION_SECRET", "")
    if len(value) < SESSION_SECRET_MIN_LENGTH:
        raise HTTPException(
            status_code=503,
            detail=f"AUTH_SESSION_SECRET must contain at least {SESSION_SECRET_MIN_LENGTH} characters",
        )
    return value.encode("utf-8")


def encode_session(user: AuthUser, *, now: float | None = None) -> str:
    issued = time.time() if now is None else now
    payload = {"id": user.id, "exp": int(issued) + AUTH_SESSION_SECONDS}
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def decode_session(token: str | None, *, now: float | None = None) -> int | None:
    """The user id in a valid, unexpired token, else None. A missing secret raises 503."""
    if not token:
        return None
    secret = _session_secret()
    try:
        body, signature_raw = token.split(".", 1)
        expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature_raw), expected):
            return None
        payload = json.loads(_b64decode(body))
        if int(payload["exp"]) <= int(time.time() if now is None else now):
            return None
        return int(payload["id"])
    except (ValueError, TypeError, KeyError, UnicodeEncodeError):
        return None


def _cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() == "true"


# ---------------------------------------------------------------------------
# Login rate limiting (in memory, per client address; resets when the API restarts)
# ---------------------------------------------------------------------------
class LoginAttempts:
    """Counts failed logins per client. ``max_attempts`` failures inside the window lock it out."""

    def __init__(
        self,
        *,
        max_attempts: int = LOGIN_MAX_ATTEMPTS,
        window_seconds: int = LOGIN_ATTEMPT_WINDOW_SECONDS,
        lockout_seconds: int = LOGIN_LOCKOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.clock = clock
        self._failures: dict[str, tuple[int, float]] = {}  # key -> (count, expires_at)
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [key for key, (_, expires_at) in self._failures.items() if expires_at <= now]:
            del self._failures[key]

    def retry_after(self, key: str) -> int | None:
        """Seconds until ``key`` may try again, or None if it is not locked out."""
        now = self.clock()
        with self._lock:
            self._purge(now)
            count, expires_at = self._failures.get(key, (0, 0.0))
            if count < self.max_attempts:
                return None
            return max(1, int(expires_at - now + 0.999))

    def record_failure(self, key: str) -> int | None:
        """Count a failure. Returns the lockout in seconds if this failure started one."""
        now = self.clock()
        with self._lock:
            self._purge(now)
            count = self._failures.get(key, (0, 0.0))[0] + 1
            if count >= self.max_attempts:
                self._failures[key] = (count, now + self.lockout_seconds)
                return self.lockout_seconds
            # The window starts at the first failure and is not extended by later ones.
            expires_at = self._failures.get(key, (0, now + self.window_seconds))[1]
            self._failures[key] = (count, expires_at)
            return None

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def _too_many_attempts(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many attempts", "retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
def _load_active_user(conn: Connection, user_id: int) -> AuthUser | None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT id, username, role FROM app_users WHERE id = %s AND active", (user_id,))
        row = cursor.fetchone()
    return None if row is None else AuthUser(id=row[0], username=row[1], role=row[2])


def current_user(request: Request, conn: DbConn) -> AuthUser:
    """The signed-in, still active user, or 401."""
    user_id = decode_session(request.cookies.get(AUTH_COOKIE))
    user = None if user_id is None else _load_active_user(conn, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user


CurrentUser = Annotated[AuthUser, Depends(current_user)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
public_router = APIRouter(prefix="/api/auth", tags=["auth"])
session_router = APIRouter(prefix="/api/auth", tags=["auth"])


def _check_credentials(conn: Connection, username: str, password: str) -> AuthUser | None:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, username, password_hash, role, active FROM app_users WHERE username = %s",
            (username,),
        )
        row = cursor.fetchone()
        if row is None:
            _burn_password_check(password)
            return None
        user_id, stored_username, password_hash, role, active = row

        if password_hash is None:
            # Bootstrap: the admin row has no hash until a login with AUTH_ADMIN_PASSWORD installs one.
            admin_password = os.getenv("AUTH_ADMIN_PASSWORD", "")
            if not (
                role == "admin"
                and len(admin_password) >= ADMIN_PASSWORD_MIN_LENGTH
                and hmac.compare_digest(password.encode("utf-8"), admin_password.encode("utf-8"))
            ):
                _burn_password_check(password)
                return None
            cursor.execute(
                "UPDATE app_users SET password_hash = %s, updated_at = NOW() WHERE id = %s AND password_hash IS NULL",
                (hash_password(password), user_id),
            )
            conn.commit()
        elif not password_matches(password, password_hash):
            return None

    if not active:
        return None
    return AuthUser(id=user_id, username=stored_username, role=role)


@public_router.post("/login", response_model=AuthUser)
def login(payload: LoginRequest, request: Request, response: Response, conn: DbConn) -> AuthUser | JSONResponse:
    attempts: LoginAttempts = request.app.state.login_attempts
    key = _client_key(request)
    _session_secret()  # fail with 503 before checking anything if sessions cannot be issued

    retry_after = attempts.retry_after(key)
    if retry_after is not None:
        return _too_many_attempts(retry_after)

    user = _check_credentials(conn, payload.username.strip().lower(), payload.password)
    if user is None:
        lockout = attempts.record_failure(key)
        if lockout is not None:
            return _too_many_attempts(lockout)
        raise HTTPException(status_code=401, detail=INCORRECT_CREDENTIALS)

    attempts.clear(key)
    response.set_cookie(
        AUTH_COOKIE,
        encode_session(user),
        max_age=AUTH_SESSION_SECONDS,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/api",
    )
    return user


@public_router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE, path="/api", samesite="strict", httponly=True, secure=_cookie_secure())


@session_router.get("/me", response_model=AuthUser)
def me(user: CurrentUser) -> AuthUser:
    return user
