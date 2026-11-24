"""Shared fixtures. Database tests need a disposable Postgres 16 named by TEST_DATABASE_URL.

Locally, for example:
    docker run --rm -d --name ss-test-pg -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=test postgres:16
    export TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/postgres

Without it the database tests are skipped locally and fail in CI.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator

import psycopg2
import pytest
from psycopg2 import sql
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import make_dsn, parse_dsn


@pytest.fixture
def connect_test_db() -> Iterator[Callable[[], Connection]]:
    """Create an empty database for one test; yield a function that opens connections to it."""
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        message = "TEST_DATABASE_URL is not set (see tests/conftest.py)"
        if os.getenv("CI"):
            pytest.fail(message)
        pytest.skip(message)

    name = f"ss_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))

    dsn = make_dsn(**{**parse_dsn(admin_url), "dbname": name})
    opened: list[Connection] = []

    def connect() -> Connection:
        conn = psycopg2.connect(dsn)
        opened.append(conn)
        return conn

    try:
        yield connect
    finally:
        for conn in opened:
            conn.close()
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.close()


@pytest.fixture
def connect_app_db(connect_test_db) -> Callable[[], Connection]:
    """Like ``connect_test_db``, with the real migrations applied and the app schema on the search path."""
    from orchestration.migrations import run_migrations

    run_migrations(connect_test_db())

    def connect() -> Connection:
        conn = connect_test_db()
        with conn.cursor() as cursor:
            cursor.execute("SET search_path TO sentinel_stream, public")
        conn.commit()
        return conn

    return connect
