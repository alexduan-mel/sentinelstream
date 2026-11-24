"""Postgres session advisory locks, carried over from the prototype's task_locks.py.

Session locks (not transaction locks) are used on purpose: they outlive the transactions a run
commits, and Postgres drops them when the holder's connection closes, so a crashed process never
leaves a lock behind. They are re-entrant within one session, so each holder must unlock once per
successful ``try_lock``.
"""

from __future__ import annotations

from psycopg2.extensions import connection as Connection

SCHEDULER_LOCK = "sentinel_stream_task_scheduler"


def task_lock_name(task_id: str) -> str:
    """Held by whichever worker is running ``task_id``, for the whole run."""
    return f"sentinel_stream_task:{task_id}"


def try_lock(conn: Connection, name: str) -> bool:
    """Take the lock without waiting. Runs inside the caller's transaction and does not commit."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (name,))
        return bool(cursor.fetchone()[0])


def unlock(conn: Connection, name: str) -> bool:
    """Release one hold of the lock. False if this session did not hold it."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))
        return bool(cursor.fetchone()[0])
