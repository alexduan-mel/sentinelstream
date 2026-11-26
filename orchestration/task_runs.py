"""The ``task_runs`` lifecycle: enqueue → claim → finish, plus reconciliation of abandoned runs.

Adapted from the prototype's task_runs.py and task_scheduler_worker.py. A run moves through::

    pending ──claim──► running ──finish──► succeeded | partial | failed
       │                  └──worker died──► failed     (reconcile_runs)
       ├──claim──► skipped   (same task already running, or the task was disabled)
       └──never claimed──► failed                       (reconcile_runs)

Only the worker moves a run out of ``pending`` while it is healthy. Every UPDATE is guarded by the
status it expects, so a late writer can never overwrite a state another process already set.

Functions take an open connection and say whether they commit.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from psycopg2.extensions import connection as Connection
from psycopg2.extras import Json

from orchestration import locks

LOGGER = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({"succeeded", "partial", "skipped", "failed"})
ERROR_MESSAGE_LIMIT = 2000


class TaskNotFoundError(LookupError):
    pass


class TaskDisabledError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskRun:
    """A claimed run, as handed to a task handler."""

    id: int
    task_id: str
    trace_id: str
    trigger_source: str
    run_mode: str
    requested_by: str | None
    tickers: list[str]
    as_of_date: date | None
    force_refresh: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class RunResult:
    """What a handler reports back. ``status`` must be terminal."""

    status: str = "succeeded"
    fetched_count: int = 0
    inserted_count: int = 0
    deduped_count: int = 0
    error_message: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """The outcome of claiming one pending run: either ``run`` to execute, or why it was skipped."""

    run_id: int
    task_id: str
    run: TaskRun | None
    skip_reason: str | None = None


def _truncate(message: str | None) -> str | None:
    return None if message is None else message[:ERROR_MESSAGE_LIMIT]


def enqueue_run(
    conn: Connection,
    task_id: str,
    *,
    trigger_source: str,
    run_mode: str,
    requested_by: str | None,
    payload: dict[str, Any] | None = None,
    tickers: list[str] | None = None,
    as_of_date: date | None = None,
    force_refresh: bool = False,
) -> int:
    """Insert a ``pending`` run and return its id. Does not commit.

    The handler's payload is the task's ``default_payload`` overlaid with ``payload``. Unknown and
    disabled tasks are refused.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO task_runs (task_id, worker_name, trace_id, trigger_source, run_mode, status,
                                   requested_by, as_of_date, force_refresh, tickers, meta)
            SELECT task_id, worker_name, %(trace_id)s, %(trigger_source)s, %(run_mode)s, 'pending',
                   %(requested_by)s, %(as_of_date)s, %(force_refresh)s, %(tickers)s,
                   jsonb_build_object('dispatch_payload', default_payload || %(payload)s::jsonb)
            FROM task_definitions
            WHERE task_id = %(task_id)s AND enabled
            RETURNING id
            """,
            {
                "task_id": task_id,
                "trace_id": str(uuid.uuid4()),
                "trigger_source": trigger_source,
                "run_mode": run_mode,
                "requested_by": requested_by,
                "as_of_date": as_of_date,
                "force_refresh": force_refresh,
                "tickers": Json([symbol.strip().upper() for symbol in tickers or [] if symbol.strip()]),
                "payload": Json(payload or {}),
            },
        )
        row = cursor.fetchone()
        if row is not None:
            return int(row[0])
        cursor.execute("SELECT enabled FROM task_definitions WHERE task_id = %s", (task_id,))
        exists = cursor.fetchone()
    if exists is None:
        raise TaskNotFoundError(f"Unknown task {task_id!r}")
    raise TaskDisabledError(f"Task {task_id!r} is disabled")


def claim_next_run(conn: Connection, *, worker_instance: str) -> Claim | None:
    """Take the oldest pending run and commit. None if nothing is pending.

    On success this session holds the task's advisory lock, taken before the run is marked
    ``running``, and the caller must release it with ``finish_run``. If another session holds the
    lock, the run ends ``skipped`` instead. ``SKIP LOCKED`` lets several workers claim in parallel.
    """
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT tr.id, tr.task_id, tr.trace_id::text, tr.trigger_source, tr.run_mode, tr.requested_by,
                       tr.tickers, tr.as_of_date, tr.force_refresh, tr.meta, td.enabled
                FROM task_runs tr
                JOIN task_definitions td ON td.task_id = tr.task_id
                WHERE tr.status = 'pending'
                ORDER BY tr.created_at, tr.id
                LIMIT 1
                FOR UPDATE OF tr SKIP LOCKED
                """)
            row = cursor.fetchone()
            if row is None:
                conn.commit()
                return None
            run_id, task_id, enabled = int(row[0]), str(row[1]), bool(row[10])

            skip_reason = None
            if not enabled:
                skip_reason = "task_disabled"
            elif not locks.try_lock(conn, locks.task_lock_name(task_id)):
                skip_reason = "task_already_running"

            if skip_reason:
                cursor.execute(
                    """
                    UPDATE task_runs
                    SET status = 'skipped', completed_at = NOW(), updated_at = NOW(),
                        meta = meta || jsonb_build_object('skip_reason', %s::text, 'worker_instance', %s::text)
                    WHERE id = %s
                    """,
                    (skip_reason, worker_instance, run_id),
                )
                conn.commit()
                LOGGER.info("task_run_skipped run_id=%d task_id=%s reason=%s", run_id, task_id, skip_reason)
                return Claim(run_id, task_id, None, skip_reason)

            cursor.execute(
                """
                UPDATE task_runs
                SET status = 'running', started_at = NOW(), updated_at = NOW(),
                    meta = meta || jsonb_build_object('worker_instance', %s::text)
                WHERE id = %s
                """,
                (worker_instance, run_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    meta = row[9] if isinstance(row[9], dict) else {}
    payload = meta.get("dispatch_payload")
    run = TaskRun(
        id=run_id,
        task_id=task_id,
        trace_id=str(row[2]),
        trigger_source=str(row[3]),
        run_mode=str(row[4]),
        requested_by=row[5],
        tickers=list(row[6] or []),
        as_of_date=row[7],
        force_refresh=bool(row[8]),
        payload=dict(payload) if isinstance(payload, dict) else {},
    )
    LOGGER.info("task_run_started run_id=%d task_id=%s trace_id=%s", run.id, run.task_id, run.trace_id)
    return Claim(run_id, task_id, run)


def log_run_failed(run_id: int, task_id: str, ended_by: str, error: str | None) -> None:
    """The one ``task_run_failed`` line per failed run, which the CloudWatch alarm counts.

    Written where a run is recorded as failed: by the worker when a handler fails, or by the
    scheduler's reconciliation when no process will finish the run. Kept to a single line, so the
    log driver ships it as one event.
    """
    detail = " ".join((_truncate(error) or "-").split())
    LOGGER.error("task_run_failed run_id=%d task_id=%s ended_by=%s error=%s", run_id, task_id, ended_by, detail)


def finish_run(conn: Connection, run: TaskRun, result: RunResult) -> bool:
    """Record a claimed run's outcome, commit, and release its task lock.

    Returns False if the run was no longer ``running`` (reconciliation already ended it).
    """
    if result.status not in TERMINAL_STATUSES:
        raise ValueError(f"Run must end in a terminal status, not {result.status!r}")
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE task_runs
                SET status = %s, fetched_count = %s, inserted_count = %s, deduped_count = %s,
                    error_message = %s, meta = meta || %s, completed_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status = 'running'
                """,
                (
                    result.status,
                    result.fetched_count,
                    result.inserted_count,
                    result.deduped_count,
                    _truncate(result.error_message),
                    Json(result.meta),
                    run.id,
                ),
            )
            updated = cursor.rowcount == 1
        conn.commit()
    finally:
        conn.rollback()
        locks.unlock(conn, locks.task_lock_name(run.task_id))
        conn.commit()

    log = LOGGER.warning if result.status == "failed" else LOGGER.info
    log(
        "task_run_finished run_id=%d task_id=%s status=%s fetched=%d inserted=%d deduped=%d error=%s",
        run.id,
        run.task_id,
        result.status,
        result.fetched_count,
        result.inserted_count,
        result.deduped_count,
        _truncate(result.error_message) or "-",
    )
    if not updated:
        # Reconciliation already failed the run and logged it.
        LOGGER.warning("task_run_finish_ignored run_id=%d reason=no_longer_running", run.id)
    elif result.status == "failed":
        log_run_failed(run.id, run.task_id, "worker", result.error_message)
    return updated


def reconcile_runs(
    conn: Connection, now: datetime, *, pending_timeout: timedelta, running_grace: timedelta
) -> dict[str, int]:
    """End runs no process will finish, and commit. Run by the scheduler on every tick.

    * ``pending`` older than ``pending_timeout``: no worker claimed it (worker down) → failed.
    * ``running`` whose task lock nobody holds: the worker exited mid-run (crash, deploy restart,
      OOM kill) → failed. Runs started within ``running_grace`` are left alone.

    The liveness check briefly takes the task lock itself. A worker claiming the same task at that
    instant sees it held and skips its run, which is harmless: the next scheduled run picks it up.
    """
    counts = {"pending_timed_out": 0, "running_abandoned": 0}
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE task_runs
                SET status = 'failed', completed_at = %(now)s, updated_at = %(now)s,
                    error_message = COALESCE(error_message, %(message)s)
                WHERE status = 'pending' AND created_at < %(cutoff)s
                RETURNING id, task_id, error_message
                """,
                {
                    "now": now,
                    "cutoff": now - pending_timeout,
                    "message": f"not claimed by a worker within {int(pending_timeout.total_seconds())} s",
                },
            )
            timed_out = cursor.fetchall()
            counts["pending_timed_out"] = len(timed_out)
            conn.commit()
            for run_id, task_id, error in timed_out:
                log_run_failed(run_id, task_id, "scheduler", error)

            cursor.execute(
                "SELECT id, task_id FROM task_runs WHERE status = 'running' AND started_at < %s ORDER BY id",
                (now - running_grace,),
            )
            candidates = cursor.fetchall()
            conn.commit()

        for run_id, task_id in candidates:
            name = locks.task_lock_name(task_id)
            if not locks.try_lock(conn, name):
                conn.commit()
                continue  # a worker still holds it: the run is alive
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE task_runs
                        SET status = 'failed', completed_at = %s, updated_at = %s,
                            error_message = COALESCE(error_message, 'worker stopped before the run finished')
                        WHERE id = %s AND status = 'running'
                        RETURNING error_message
                        """,
                        (now, now, run_id),
                    )
                    abandoned = cursor.fetchone()
                conn.commit()
                if abandoned is not None:
                    counts["running_abandoned"] += 1
                    log_run_failed(run_id, task_id, "scheduler", abandoned[0])
            finally:
                conn.rollback()
                locks.unlock(conn, name)
                conn.commit()
    except Exception:
        conn.rollback()
        raise

    if any(counts.values()):
        LOGGER.warning(
            "task_runs_reconciled pending_timed_out=%d running_abandoned=%d",
            counts["pending_timed_out"],
            counts["running_abandoned"],
        )
    return counts
