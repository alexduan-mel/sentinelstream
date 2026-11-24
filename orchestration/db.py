"""Postgres connections built from the POSTGRES_* settings in the environment."""

from __future__ import annotations

import os

import psycopg2
from psycopg2.extensions import connection as Connection

REQUIRED_SETTINGS = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")


class DatabaseConfigurationError(RuntimeError):
    pass


def connect(*, connect_timeout: int = 5) -> Connection:
    """Open a connection from ``POSTGRES_*`` settings. Missing settings fail with a clear error."""
    missing = [name for name in REQUIRED_SETTINGS if not os.getenv(name)]
    if missing:
        raise DatabaseConfigurationError(f"Missing database settings: {', '.join(missing)}")
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=connect_timeout,
        application_name="sentinelstream",
    )
