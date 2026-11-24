"""Postgres connections built from the POSTGRES_* settings in the environment."""

from __future__ import annotations

import logging
import os
import time

import psycopg2
from psycopg2.extensions import connection as Connection

LOGGER = logging.getLogger(__name__)

REQUIRED_SETTINGS = ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
SCHEMA = "sentinel_stream"


class DatabaseConfigurationError(RuntimeError):
    pass


def connect(*, connect_timeout: int = 5, application_name: str = "sentinelstream") -> Connection:
    """Open a connection from ``POSTGRES_*`` settings. Missing settings fail with a clear error.

    The session's ``search_path`` starts with the app schema, so queries use bare table names.
    """
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
        application_name=application_name,
        options=f"-c search_path={SCHEMA},public",
    )


def connect_with_retry(
    *, attempts: int = 15, delay_seconds: float = 2, application_name: str = "sentinelstream"
) -> Connection:
    """Connect, retrying while Postgres is still starting. Configuration errors are not retried."""
    for attempt in range(1, attempts + 1):
        try:
            return connect(application_name=application_name)
        except psycopg2.OperationalError as exc:
            if attempt == attempts:
                raise
            LOGGER.info("database_not_ready attempt=%d/%d error=%s", attempt, attempts, str(exc).strip())
            time.sleep(delay_seconds)
    raise AssertionError("unreachable")
