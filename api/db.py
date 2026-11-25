"""Per-request database connections for the API."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from psycopg2.extensions import connection as Connection

from orchestration import db

Connect = Callable[[], Connection]


def default_connect() -> Connection:
    return db.connect(application_name="sentinelstream-api")


def get_conn(request: Request) -> Iterator[Connection]:
    """One connection per request, rolled back unless the endpoint committed. Missing settings → 503."""
    connect: Connect = request.app.state.connect
    try:
        conn = connect()
    except db.DatabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


DbConn = Annotated[Connection, Depends(get_conn)]
