"""The worker service: claims pending task runs one at a time and runs their handlers in-process.

The prototype's scheduler ran each job as a blocking subprocess inside its own loop; here the
scheduler only enqueues and this separate service executes (ADR 0003). Each claimed run holds its
task's advisory lock on the worker's control connection until it finishes, which is how the
scheduler's reconciliation tells a live run from one whose worker died.

Run with ``python -m orchestration.worker``.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from collections.abc import Callable, Mapping

from psycopg2.extensions import connection as Connection

from orchestration import db, service
from orchestration.handlers import HANDLERS, Handler
from orchestration.task_runs import TERMINAL_STATUSES, RunResult, TaskRun, claim_next_run, finish_run

LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 5.0


def worker_instance() -> str:
    """Identifies this process in ``task_runs.meta.worker_instance`` (container hostname and pid)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def execute(run: TaskRun, handlers: Mapping[str, Handler], connect: Callable[[], Connection]) -> RunResult:
    """Run the handler for ``run`` on a fresh connection. Never raises: failures become a failed result."""
    handler = handlers.get(run.task_id)
    if handler is None:
        return RunResult(status="failed", error_message=f"no handler registered for task {run.task_id!r}")
    try:
        job_conn = connect()
    except Exception as exc:  # noqa: BLE001 - recorded on the run
        return RunResult(status="failed", error_message=f"could not open a database connection: {exc}")
    try:
        result = handler(run, job_conn)
    except Exception as exc:  # noqa: BLE001 - recorded on the run
        LOGGER.exception("task_handler_failed run_id=%d task_id=%s", run.id, run.task_id)
        return RunResult(status="failed", error_message=f"{type(exc).__name__}: {exc}")
    finally:
        job_conn.close()
    if not isinstance(result, RunResult) or result.status not in TERMINAL_STATUSES:
        return RunResult(status="failed", error_message=f"handler returned an invalid result: {result!r}")
    return result


def process_next(
    conn: Connection,
    *,
    handlers: Mapping[str, Handler],
    connect: Callable[[], Connection],
    instance: str,
    heartbeat: service.Heartbeat | None = None,
) -> bool:
    """Claim and process one pending run. False if nothing was pending."""
    claim = claim_next_run(conn, worker_instance=instance)
    if claim is None:
        return False
    if claim.run is None:
        return True  # skipped at claim time
    if heartbeat is None:
        result = execute(claim.run, handlers, connect)
    else:
        with heartbeat.during():
            result = execute(claim.run, handlers, connect)
    finish_run(conn, claim.run, result)
    return True


def work(
    conn: Connection,
    stop: threading.Event,
    *,
    handlers: Mapping[str, Handler],
    connect: Callable[[], Connection],
    poll_seconds: float,
    heartbeat: service.Heartbeat,
) -> None:
    """Process runs until ``stop`` is set, sleeping ``poll_seconds`` whenever the queue is empty.

    A stop request lets the current run finish; Compose gives the worker a grace period for that.
    """
    instance = worker_instance()
    LOGGER.info("worker_started instance=%s poll_seconds=%g handlers=%s", instance, poll_seconds, ",".join(handlers))
    while not stop.is_set():
        heartbeat.beat()
        if not process_next(conn, handlers=handlers, connect=connect, instance=instance, heartbeat=heartbeat):
            stop.wait(poll_seconds)


def main() -> int:
    service.configure_logging()
    poll_seconds = service.env_seconds("WORKER_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=1)
    stop = service.stop_on_signals()
    heartbeat = service.Heartbeat()

    def connect_job() -> Connection:
        return db.connect(application_name="sentinelstream-job")

    return service.run_with_reconnect(
        "worker",
        lambda conn: work(
            conn, stop, handlers=HANDLERS, connect=connect_job, poll_seconds=poll_seconds, heartbeat=heartbeat
        ),
        stop,
        retry_seconds=poll_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
