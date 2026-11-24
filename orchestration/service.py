"""Plumbing shared by the long-running scheduler and worker processes."""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extensions import connection as Connection

from orchestration import db

LOGGER = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_FILE = "/tmp/sentinelstream-heartbeat"


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )


def env_seconds(name: str, default: float, *, minimum: float) -> float:
    """A positive number of seconds from the environment, with a clear error if it is malformed."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number of seconds, got {raw!r}") from exc
    if value < minimum:
        raise SystemExit(f"{name} must be at least {minimum:g} seconds, got {raw!r}")
    return value


def stop_on_signals() -> threading.Event:
    """An event set by SIGTERM (``docker stop``) or SIGINT, so loops finish their current step and exit."""
    stop = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        LOGGER.info("stop_requested signal=%s", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    return stop


class Heartbeat:
    """Touches a file so the container health check can tell the loop is still turning."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.getenv("HEARTBEAT_FILE", DEFAULT_HEARTBEAT_FILE))

    def beat(self) -> None:
        self.path.touch()

    @contextmanager
    def during(self, interval_seconds: float = 15) -> Iterator[None]:
        """Keep beating from a background thread while a long step (one task run) is in progress."""
        done = threading.Event()

        def keep_beating() -> None:
            while not done.wait(interval_seconds):
                self.beat()

        thread = threading.Thread(target=keep_beating, name="heartbeat", daemon=True)
        self.beat()
        thread.start()
        try:
            yield
        finally:
            done.set()
            thread.join()


def is_fresh(path: str | os.PathLike[str], max_age_seconds: float) -> bool:
    try:
        return time.time() - os.path.getmtime(path) <= max_age_seconds
    except FileNotFoundError:
        return False


def run_with_reconnect(
    name: str, body: Callable[[Connection], None], stop: threading.Event, *, retry_seconds: float
) -> int:
    """Call ``body(conn)`` until ``stop`` is set, opening a new connection whenever the old one breaks.

    Returns the process exit code: 0 after a requested stop, 1 if the database settings are missing.
    """
    while not stop.is_set():
        try:
            conn = db.connect_with_retry(application_name=f"sentinelstream-{name}")
        except db.DatabaseConfigurationError as exc:
            LOGGER.error("%s_failed error=%s", name, exc)
            return 1
        except psycopg2.OperationalError as exc:
            LOGGER.error("%s_database_unavailable error=%s", name, str(exc).strip())
            stop.wait(retry_seconds)
            continue
        try:
            body(conn)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
            LOGGER.error("%s_connection_lost error=%s", name, str(exc).strip())
            stop.wait(retry_seconds)
        finally:
            if not conn.closed:
                conn.close()
    LOGGER.info("%s_stopped", name)
    return 0
