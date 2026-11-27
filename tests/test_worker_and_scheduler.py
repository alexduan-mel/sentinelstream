"""The worker executes what the scheduler enqueues; both against Postgres."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orchestration import enqueue, healthcheck, locks, scheduler, service
from orchestration.task_runs import RunResult, enqueue_run
from orchestration.worker import process_next, work

TASK = "ingest_prices"
OTHER_TASK = "ingest_sec_filings"
GRACE = timedelta(seconds=60)
PENDING_TIMEOUT = timedelta(hours=1)


def _enqueue(conn, task_id: str = TASK) -> int:
    run_id = enqueue_run(conn, task_id, trigger_source="test", run_mode="manual", requested_by="pytest")
    conn.commit()
    return run_id


def _status(conn, run_id: int) -> tuple[str, str | None]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT status, error_message FROM task_runs WHERE id = %s", (run_id,))
        row = cursor.fetchone()
    conn.rollback()
    return row


def _make_automatic(conn, task_id: str, *, next_run_at: datetime | None, timezone_name: str = "UTC") -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE task_definitions SET trigger_mode = 'automatic', next_run_at = %s, schedule_timezone = %s "
            "WHERE task_id = %s",
            (next_run_at, timezone_name, task_id),
        )
    conn.commit()


def _definition(conn, task_id: str) -> tuple[datetime | None, datetime | None]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT next_run_at, last_scheduled_at FROM task_definitions WHERE task_id = %s", (task_id,))
        row = cursor.fetchone()
    conn.rollback()
    return row


def _pending(conn) -> list[tuple[str, str, str]]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT task_id, trigger_source, run_mode FROM task_runs WHERE status = 'pending' ORDER BY id")
        rows = cursor.fetchall()
    conn.rollback()
    return rows


# --- worker ----------------------------------------------------------------------------------------


def test_worker_runs_the_handler_on_its_own_connection(connect_app_db) -> None:
    control = connect_app_db()
    run_id = _enqueue(control)
    seen = {}

    def handler(run, job_conn):
        seen["run_id"] = run.id
        seen["separate_connection"] = job_conn is not control
        with job_conn.cursor() as cursor:
            cursor.execute("SELECT status FROM task_runs WHERE id = %s", (run.id,))
            seen["status_during_run"] = cursor.fetchone()[0]
        return RunResult(status="succeeded", fetched_count=4, inserted_count=4)

    assert process_next(control, handlers={TASK: handler}, connect=connect_app_db, instance="w")

    assert seen == {"run_id": run_id, "separate_connection": True, "status_during_run": "running"}
    assert _status(control, run_id) == ("succeeded", None)
    assert process_next(control, handlers={TASK: handler}, connect=connect_app_db, instance="w") is False


def test_handler_exception_fails_the_run_and_frees_the_task(connect_app_db, caplog) -> None:
    control = connect_app_db()
    run_id = _enqueue(control)

    def handler(run, job_conn):
        raise RuntimeError("SEC returned 503 after 5 retries")

    process_next(control, handlers={TASK: handler}, connect=connect_app_db, instance="w")

    assert _status(control, run_id) == ("failed", "RuntimeError: SEC returned 503 after 5 retries")
    # The line the CloudWatch alarm counts, once.
    assert [r.getMessage() for r in caplog.records if "task_run_failed" in r.getMessage()] == [
        f"task_run_failed run_id={run_id} task_id={TASK} ended_by=worker "
        "error=RuntimeError: SEC returned 503 after 5 retries"
    ]
    # The next run of the same task is not blocked.
    next_id = _enqueue(control)
    process_next(control, handlers={TASK: lambda run, conn: RunResult()}, connect=connect_app_db, instance="w")
    assert _status(control, next_id)[0] == "succeeded"


def test_task_without_a_handler_fails_clearly(connect_app_db) -> None:
    control = connect_app_db()
    run_id = _enqueue(control)

    process_next(control, handlers={}, connect=connect_app_db, instance="w")

    assert _status(control, run_id) == ("failed", "no handler registered for task 'ingest_prices'")


@pytest.mark.parametrize("bad_result", [None, RunResult(status="pending")])
def test_invalid_handler_result_fails_the_run(connect_app_db, bad_result) -> None:
    control = connect_app_db()
    run_id = _enqueue(control)

    process_next(control, handlers={TASK: lambda run, conn: bad_result}, connect=connect_app_db, instance="w")

    status, error = _status(control, run_id)
    assert status == "failed" and error.startswith("handler returned an invalid result")


def test_uncommitted_handler_writes_are_rolled_back(connect_app_db) -> None:
    control = connect_app_db()
    _enqueue(control)

    def handler(run, job_conn):
        with job_conn.cursor() as cursor:
            cursor.execute("UPDATE instruments SET name = 'should not stick' WHERE symbol = 'NVDA'")
        return RunResult()

    process_next(control, handlers={TASK: handler}, connect=connect_app_db, instance="w")

    with control.cursor() as cursor:
        cursor.execute("SELECT name FROM instruments WHERE symbol = 'NVDA'")
        assert cursor.fetchone()[0] is None


def test_work_loop_drains_the_queue_and_stops_on_request(connect_app_db, tmp_path: Path) -> None:
    control = connect_app_db()
    run_ids = [_enqueue(control, TASK), _enqueue(control, OTHER_TASK)]
    stop = threading.Event()
    done = []

    def handler(run, job_conn):
        done.append(run.id)
        if len(done) == len(run_ids):
            stop.set()
        return RunResult()

    heartbeat = service.Heartbeat(tmp_path / "heartbeat")
    thread = threading.Thread(
        target=work,
        args=(control, stop),
        kwargs={
            "handlers": {TASK: handler, OTHER_TASK: handler},
            "connect": connect_app_db,
            "poll_seconds": 0.1,
            "heartbeat": heartbeat,
        },
    )
    thread.start()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert done == run_ids
    assert healthcheck.main(["--max-age", "60", "--file", str(heartbeat.path)]) == 0


# --- scheduler -------------------------------------------------------------------------------------


def test_newly_automatic_task_gets_a_next_run_but_does_not_run_yet(connect_app_db) -> None:
    conn = connect_app_db()
    _make_automatic(conn, OTHER_TASK, next_run_at=None)
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)

    assert scheduler.schedule_due_tasks(conn, now) == []

    assert _definition(conn, OTHER_TASK) == (now + timedelta(minutes=5), None)
    assert _pending(conn) == []


def test_due_task_is_enqueued_once_and_rescheduled(connect_app_db) -> None:
    conn = connect_app_db()
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    _make_automatic(conn, OTHER_TASK, next_run_at=now - timedelta(hours=3))  # missed while down

    created = scheduler.schedule_due_tasks(conn, now)

    assert len(created) == 1
    assert _pending(conn) == [(OTHER_TASK, "system_scheduler", "scheduled")]
    assert _definition(conn, OTHER_TASK) == (now + timedelta(minutes=5), now)
    # Not due again until next_run_at: no catch-up burst.
    assert scheduler.schedule_due_tasks(conn, now + timedelta(minutes=4)) == []


def test_manual_and_disabled_tasks_are_never_scheduled(connect_app_db) -> None:
    conn = connect_app_db()
    past = datetime(2026, 1, 1, tzinfo=UTC)
    with conn.cursor() as cursor:
        cursor.execute("UPDATE task_definitions SET next_run_at = %s", (past,))
        cursor.execute("UPDATE task_definitions SET trigger_mode = 'manual' WHERE task_id <> %s", (TASK,))
        cursor.execute(
            "UPDATE task_definitions SET trigger_mode = 'automatic', enabled = FALSE WHERE task_id = %s", (TASK,)
        )
    conn.commit()

    assert scheduler.schedule_due_tasks(conn, datetime(2026, 11, 25, tzinfo=UTC)) == []


def test_one_bad_task_definition_does_not_block_the_others(connect_app_db) -> None:
    conn = connect_app_db()
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    _make_automatic(conn, TASK, next_run_at=now - timedelta(minutes=1), timezone_name="Mars/Olympus_Mons")
    _make_automatic(conn, OTHER_TASK, next_run_at=now - timedelta(minutes=1))

    created = scheduler.schedule_due_tasks(conn, now)

    assert len(created) == 1
    assert _pending(conn) == [(OTHER_TASK, "system_scheduler", "scheduled")]


def test_scheduled_run_flows_through_to_the_worker(connect_app_db) -> None:
    scheduler_conn, worker_conn = connect_app_db(), connect_app_db()
    now = datetime.now(UTC)
    _make_automatic(scheduler_conn, OTHER_TASK, next_run_at=now - timedelta(seconds=1))

    _, created = scheduler.tick(scheduler_conn, now, pending_timeout=PENDING_TIMEOUT, running_grace=GRACE)
    process_next(
        worker_conn, handlers={OTHER_TASK: lambda run, conn: RunResult()}, connect=connect_app_db, instance="w"
    )

    assert _status(worker_conn, created[0]) == ("succeeded", None)


def test_only_one_scheduler_leads(connect_app_db, tmp_path: Path) -> None:
    leader, standby = connect_app_db(), connect_app_db()
    heartbeat = service.Heartbeat(tmp_path / "heartbeat")
    assert scheduler.acquire_leadership(leader, threading.Event(), poll_seconds=0.05, heartbeat=heartbeat)

    stop = threading.Event()
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "led", scheduler.acquire_leadership(standby, stop, poll_seconds=0.05, heartbeat=heartbeat)
        )
    )
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()  # waiting on standby, still beating
    assert service.is_fresh(heartbeat.path, 5)

    leader.close()  # leader exits: its lock goes with the connection
    thread.join(timeout=5)
    assert result == {"led": True}
    locks.unlock(standby, locks.SCHEDULER_LOCK)


# --- CLI and plumbing ------------------------------------------------------------------------------


def test_enqueue_cli_queues_a_manual_run(connect_app_db, monkeypatch, capsys) -> None:
    monkeypatch.setattr(enqueue.db, "connect", lambda **_: connect_app_db())

    assert enqueue.main([TASK, "--ticker", "nvda", "--payload", '{"limit": 3}']) == 0

    run_id = int(capsys.readouterr().out)
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT status, trigger_source, run_mode, tickers, meta FROM task_runs WHERE id = %s", (run_id,))
        assert cursor.fetchone() == ("pending", "cli", "manual", ["NVDA"], {"dispatch_payload": {"limit": 3}})


def test_enqueue_cli_rejects_an_unknown_task(connect_app_db, monkeypatch, capsys) -> None:
    monkeypatch.setattr(enqueue.db, "connect", lambda **_: connect_app_db())

    assert enqueue.main(["no_such_task"]) == 1
    assert "Unknown task 'no_such_task'" in capsys.readouterr().err


def test_services_exit_clearly_without_database_settings(monkeypatch, caplog) -> None:
    for name in ("POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    code = service.run_with_reconnect("worker", lambda conn: None, threading.Event(), retry_seconds=0)

    assert code == 1
    assert "Missing database settings" in caplog.text


def test_stale_or_missing_heartbeat_is_unhealthy(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    assert healthcheck.main(["--max-age", "60", "--file", str(path)]) == 1
    path.touch()
    old = time.time() - 120
    os.utime(path, (old, old))
    assert healthcheck.main(["--max-age", "60", "--file", str(path)]) == 1


def test_env_seconds_rejects_bad_values(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_POLL_SECONDS", "soon")
    with pytest.raises(SystemExit):
        service.env_seconds("WORKER_POLL_SECONDS", 5, minimum=1)
    monkeypatch.setenv("WORKER_POLL_SECONDS", "0")
    with pytest.raises(SystemExit):
        service.env_seconds("WORKER_POLL_SECONDS", 5, minimum=1)
    monkeypatch.setenv("WORKER_POLL_SECONDS", "2.5")
    assert service.env_seconds("WORKER_POLL_SECONDS", 5, minimum=1) == 2.5


def test_worker_refuses_an_unknown_queue() -> None:
    from orchestration import worker

    with pytest.raises(SystemExit):
        worker.main(["--queue", "everything"])
