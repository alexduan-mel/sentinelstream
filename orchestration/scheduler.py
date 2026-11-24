"""The scheduler service: enqueues due tasks as pending runs and reconciles abandoned ones.

Adapted from the prototype's task_scheduler_worker.py. It never executes a task itself: the
worker service claims what this enqueues (ADR 0003). Only one scheduler is active at a time, held
by a session advisory lock; a second instance waits on standby until the lock frees.

Each tick:

1. ``reconcile_runs``: fail runs no process will finish.
2. ``schedule_due_tasks``: for every enabled ``automatic`` task whose ``next_run_at`` has passed,
   insert a pending run and move ``next_run_at`` on, in one transaction. A task just switched to
   automatic (``next_run_at`` is NULL) is given its first ``next_run_at`` and not run straight away.

Run with ``python -m orchestration.scheduler``.
"""

from __future__ import annotations

import logging
import sys
import threading
from datetime import UTC, datetime, timedelta

from psycopg2.extensions import connection as Connection

from orchestration import locks, service
from orchestration.schedule import Schedule, next_run_at
from orchestration.task_runs import enqueue_run, reconcile_runs

LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 30.0
DEFAULT_PENDING_TIMEOUT_SECONDS = 3600.0
DEFAULT_RUNNING_GRACE_SECONDS = 60.0


def _schedule_task(conn: Connection, task_id: str, now: datetime) -> int | None:
    """Enqueue one task if it is still due, in the caller's transaction. Returns the new run id."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT next_run_at, schedule_type, interval_minutes, scheduled_time, schedule_timezone, schedule_days
            FROM task_definitions
            WHERE task_id = %s AND enabled AND trigger_mode = 'automatic'
              AND (next_run_at IS NULL OR next_run_at <= %s)
            FOR UPDATE
            """,
            (task_id, now),
        )
        row = cursor.fetchone()
        if row is None:
            return None  # changed since it was listed
        current_next, *schedule_columns = row
        following = next_run_at(Schedule(*schedule_columns), now)
        if current_next is None:
            cursor.execute(
                "UPDATE task_definitions SET next_run_at = %s, updated_at = NOW() WHERE task_id = %s",
                (following, task_id),
            )
            LOGGER.info("task_schedule_initialised task_id=%s next_run_at=%s", task_id, following.isoformat())
            return None
        run_id = enqueue_run(
            conn, task_id, trigger_source="system_scheduler", run_mode="scheduled", requested_by="scheduler"
        )
        cursor.execute(
            "UPDATE task_definitions SET next_run_at = %s, last_scheduled_at = %s, updated_at = NOW() "
            "WHERE task_id = %s",
            (following, now, task_id),
        )
    LOGGER.info(
        "task_run_enqueued run_id=%d task_id=%s trigger_source=system_scheduler next_run_at=%s",
        run_id,
        task_id,
        following.isoformat(),
    )
    return run_id


def schedule_due_tasks(conn: Connection, now: datetime) -> list[int]:
    """Enqueue every due automatic task, one transaction per task. Returns the ids of the runs created.

    A task whose schedule cannot be computed (for example an invalid timezone) is logged on every
    tick until it is fixed; it does not hold up the other tasks.
    """
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT task_id FROM task_definitions
            WHERE enabled AND trigger_mode = 'automatic' AND (next_run_at IS NULL OR next_run_at <= %s)
            ORDER BY next_run_at NULLS FIRST, task_id
            """,
            (now,),
        )
        due = [str(row[0]) for row in cursor.fetchall()]
    conn.commit()

    created: list[int] = []
    for task_id in due:
        try:
            run_id = _schedule_task(conn, task_id, now)
            conn.commit()
        except ValueError:
            conn.rollback()
            LOGGER.exception("task_schedule_invalid task_id=%s", task_id)
            continue
        except Exception:
            conn.rollback()
            raise
        if run_id is not None:
            created.append(run_id)
    return created


def tick(
    conn: Connection, now: datetime, *, pending_timeout: timedelta, running_grace: timedelta
) -> tuple[dict[str, int], list[int]]:
    reconciled = reconcile_runs(conn, now, pending_timeout=pending_timeout, running_grace=running_grace)
    return reconciled, schedule_due_tasks(conn, now)


def acquire_leadership(
    conn: Connection, stop: threading.Event, *, poll_seconds: float, heartbeat: service.Heartbeat
) -> bool:
    """Wait on standby until this session holds the scheduler lock. False if asked to stop first."""
    waiting_logged = False
    while not stop.is_set():
        acquired = locks.try_lock(conn, locks.SCHEDULER_LOCK)
        conn.commit()
        if acquired:
            return True
        if not waiting_logged:
            LOGGER.warning("scheduler_standby another scheduler holds the lock")
            waiting_logged = True
        heartbeat.beat()
        stop.wait(poll_seconds)
    return False


def schedule(
    conn: Connection,
    stop: threading.Event,
    *,
    poll_seconds: float,
    pending_timeout: timedelta,
    running_grace: timedelta,
    heartbeat: service.Heartbeat,
) -> None:
    if not acquire_leadership(conn, stop, poll_seconds=poll_seconds, heartbeat=heartbeat):
        return
    LOGGER.info(
        "scheduler_started poll_seconds=%g pending_timeout_s=%d running_grace_s=%d",
        poll_seconds,
        pending_timeout.total_seconds(),
        running_grace.total_seconds(),
    )
    while not stop.is_set():
        heartbeat.beat()
        # Connection errors propagate to run_with_reconnect, which reconnects and takes the lock again.
        tick(conn, datetime.now(UTC), pending_timeout=pending_timeout, running_grace=running_grace)
        stop.wait(poll_seconds)


def main() -> int:
    service.configure_logging()
    poll_seconds = service.env_seconds("SCHEDULER_POLL_SECONDS", DEFAULT_POLL_SECONDS, minimum=1)
    pending_timeout = timedelta(
        seconds=service.env_seconds("TASK_RUN_PENDING_TIMEOUT_SECONDS", DEFAULT_PENDING_TIMEOUT_SECONDS, minimum=60)
    )
    running_grace = timedelta(
        seconds=service.env_seconds("TASK_RUN_RUNNING_GRACE_SECONDS", DEFAULT_RUNNING_GRACE_SECONDS, minimum=10)
    )
    stop = service.stop_on_signals()
    heartbeat = service.Heartbeat()
    return service.run_with_reconnect(
        "scheduler",
        lambda conn: schedule(
            conn,
            stop,
            poll_seconds=poll_seconds,
            pending_timeout=pending_timeout,
            running_grace=running_grace,
            heartbeat=heartbeat,
        ),
        stop,
        retry_seconds=poll_seconds,
    )


if __name__ == "__main__":
    sys.exit(main())
