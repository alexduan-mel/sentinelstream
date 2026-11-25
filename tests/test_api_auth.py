from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import auth
from api.app import create_app
from tests.conftest import ADMIN_PASSWORD

ADMIN = {"username": "admin", "password": ADMIN_PASSWORD}
PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/logout"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Passwords and session tokens
# ---------------------------------------------------------------------------
def test_password_hash_round_trip() -> None:
    encoded = auth.hash_password("correct horse", iterations=1000)

    assert encoded.startswith("pbkdf2_sha256$1000$")
    assert auth.password_matches("correct horse", encoded)
    assert not auth.password_matches("wrong horse", encoded)


@pytest.mark.parametrize("encoded", [None, "", "md5$1$abc$def", "pbkdf2_sha256$x$abc$def", "garbage"])
def test_malformed_hashes_never_match(encoded) -> None:
    assert not auth.password_matches("anything", encoded)


def test_session_token_round_trip_and_expiry(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_SESSION_SECRET", "k" * 32)
    token = auth.encode_session(auth.AuthUser(id=7, username="admin", role="admin"), now=1_000_000)

    assert auth.decode_session(token, now=1_000_000) == 7
    assert auth.decode_session(token, now=1_000_000 + auth.AUTH_SESSION_SECONDS) is None


def test_tampered_or_foreign_tokens_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_SESSION_SECRET", "k" * 32)
    token = auth.encode_session(auth.AuthUser(id=7, username="admin", role="admin"))
    body, signature = token.split(".")
    forged_body = auth._b64encode(b'{"exp":9999999999,"id":1}')

    assert auth.decode_session(f"{forged_body}.{signature}") is None
    assert auth.decode_session("not-a-token") is None
    assert auth.decode_session("é.é") is None

    monkeypatch.setenv("AUTH_SESSION_SECRET", "other" * 8)
    assert auth.decode_session(token) is None


def test_short_session_secret_is_a_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_SESSION_SECRET", "short")

    with pytest.raises(HTTPException) as excinfo:
        auth.decode_session("a.b")
    assert excinfo.value.status_code == 503


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------
def test_fifth_failure_locks_out_for_the_lockout_period() -> None:
    clock = FakeClock()
    attempts = auth.LoginAttempts(max_attempts=5, window_seconds=300, lockout_seconds=300, clock=clock)

    for _ in range(4):
        assert attempts.record_failure("1.2.3.4") is None
    assert attempts.retry_after("1.2.3.4") is None
    assert attempts.record_failure("1.2.3.4") == 300
    assert attempts.retry_after("1.2.3.4") == 300
    assert attempts.retry_after("5.6.7.8") is None

    clock.now += 299.5
    assert attempts.retry_after("1.2.3.4") == 1
    clock.now += 1
    assert attempts.retry_after("1.2.3.4") is None


def test_failures_outside_the_window_do_not_accumulate() -> None:
    clock = FakeClock()
    attempts = auth.LoginAttempts(max_attempts=3, window_seconds=60, lockout_seconds=300, clock=clock)

    attempts.record_failure("k")
    attempts.record_failure("k")
    clock.now += 61
    assert attempts.record_failure("k") is None
    assert attempts.retry_after("k") is None


def test_success_clears_failures() -> None:
    attempts = auth.LoginAttempts(max_attempts=2, clock=FakeClock())

    attempts.record_failure("k")
    attempts.clear("k")
    assert attempts.record_failure("k") is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def test_first_admin_login_installs_a_hash_and_sets_the_session_cookie(api_client, connect_app_db) -> None:
    response = api_client.post("/api/auth/login", json=ADMIN)

    assert response.status_code == 200
    assert response.json() == {"id": 1, "username": "admin", "role": "admin"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{auth.AUTH_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert "Secure" not in cookie

    with connect_app_db().cursor() as cursor:
        cursor.execute("SELECT password_hash FROM app_users WHERE id = 1")
        stored = cursor.fetchone()[0]
    assert auth.password_matches(ADMIN_PASSWORD, stored)


def test_after_bootstrap_the_stored_hash_wins_over_the_environment(api_client, monkeypatch) -> None:
    assert api_client.post("/api/auth/login", json=ADMIN).status_code == 200
    monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "a-different-password")

    assert api_client.post("/api/auth/login", json=ADMIN).status_code == 200
    assert (
        api_client.post("/api/auth/login", json={"username": "admin", "password": "a-different-password"}).status_code
        == 401
    )


def test_bootstrap_refuses_a_short_admin_password(api_client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "short")

    assert api_client.post("/api/auth/login", json={"username": "admin", "password": "short"}).status_code == 401


def test_cookie_is_secure_when_configured(api_client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

    assert "Secure" in api_client.post("/api/auth/login", json=ADMIN).headers["set-cookie"]


@pytest.mark.parametrize(
    "credentials",
    [{"username": "admin", "password": "wrong-password"}, {"username": "nobody", "password": ADMIN_PASSWORD}],
)
def test_wrong_credentials_get_one_generic_401(api_client, credentials) -> None:
    response = api_client.post("/api/auth/login", json=credentials)

    assert response.status_code == 401
    assert response.json() == {"detail": "Incorrect username or password"}
    assert "set-cookie" not in response.headers


def test_username_is_case_insensitive(api_client) -> None:
    assert (
        api_client.post("/api/auth/login", json={"username": " Admin ", "password": ADMIN_PASSWORD}).status_code == 200
    )


def test_repeated_failures_return_429_with_retry_after(api_client) -> None:
    bad = {"username": "admin", "password": "wrong-password"}
    statuses = [api_client.post("/api/auth/login", json=bad).status_code for _ in range(auth.LOGIN_MAX_ATTEMPTS)]

    assert statuses == [401] * (auth.LOGIN_MAX_ATTEMPTS - 1) + [429]
    locked = api_client.post("/api/auth/login", json=ADMIN)
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) == locked.json()["retry_after_seconds"] > 0
    assert locked.json()["detail"] == "Too many attempts"


def test_login_without_a_session_secret_is_503(api_client, monkeypatch) -> None:
    monkeypatch.delenv("AUTH_SESSION_SECRET")

    response = api_client.post("/api/auth/login", json=ADMIN)

    assert response.status_code == 503
    assert "AUTH_SESSION_SECRET" in response.json()["detail"]


def test_me_requires_a_session(api_client) -> None:
    assert api_client.get("/api/auth/me").status_code == 401

    api_client.post("/api/auth/login", json=ADMIN)
    assert api_client.get("/api/auth/me").json() == {"id": 1, "username": "admin", "role": "admin"}


def test_deactivated_user_loses_their_session(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE app_users SET active = FALSE WHERE id = 1")
    conn.commit()

    assert signed_in_client.get("/api/auth/me").status_code == 401
    assert signed_in_client.post("/api/auth/login", json=ADMIN).status_code == 401


def test_logout_clears_the_cookie(signed_in_client) -> None:
    response = signed_in_client.post("/api/auth/logout")

    assert response.status_code == 204
    assert f'{auth.AUTH_COOKIE}=""' in response.headers["set-cookie"]
    assert signed_in_client.get("/api/auth/me").status_code == 401


def test_every_api_route_except_login_and_logout_requires_a_session(api_client, tmp_path) -> None:
    paths = create_app(version="t", web_dist=tmp_path / "missing").openapi()["paths"]
    api_paths = {path: methods for path, methods in paths.items() if path.startswith("/api/")}
    assert set(api_paths) >= PUBLIC_API_PATHS | {"/api/auth/me"}

    for path, methods in api_paths.items():
        if path in PUBLIC_API_PATHS:
            continue
        concrete = path.replace("{task_id}", "ingest_prices").replace("{run_id}", "1")
        for method in methods:
            response = api_client.request(method.upper(), concrete)
            assert response.status_code == 401, f"{method.upper()} {path} answered {response.status_code}"


def test_healthz_stays_public(api_client) -> None:
    assert api_client.get("/healthz").status_code == 200
