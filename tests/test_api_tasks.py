from __future__ import annotations

from datetime import date, time

import pytest

from api.tasks import schedule_label
from orchestration.task_runs import enqueue_run


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("interval", 10, None, "UTC", "all"), "Every 10 min"),
        (("interval", 5, None, "UTC", "all"), "Every 5 min"),
        (("interval", 120, None, "UTC", "all"), "Every 2 h"),
        (("interval", 90, None, "UTC", "all"), "Every 90 min"),
        (("daily_time", None, time(18, 30), "America/New_York", "weekdays"), "Weekdays 18:30 ET"),
        (("daily_time", None, time(6, 0), "UTC", "all"), "Daily 06:00 UTC"),
        (("daily_time", None, time(9, 5), "Europe/London", "all"), "Daily 09:05 Europe/London"),
    ],
)
def test_schedule_label(args, expected) -> None:
    assert schedule_label(*args) == expected


def _enqueue(connect_app_db, task_id: str, **kwargs) -> int:
    conn = connect_app_db()
    run_id = enqueue_run(conn, task_id, trigger_source="cli", run_mode="manual", requested_by="test", **kwargs)
    conn.commit()
    return run_id


def _set_run(connect_app_db, run_id: int, sql_set: str, params: tuple = ()) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(f"UPDATE task_runs SET {sql_set} WHERE id = %s", (*params, run_id))
    conn.commit()


def test_list_tasks_shows_seeded_definitions(signed_in_client) -> None:
    response = signed_in_client.get("/api/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert [task["task_id"] for task in tasks] == sorted(task["task_id"] for task in tasks)
    # Tasks added by later migrations are due at once after them, then on their schedules.
    due_now = {
        task["task_id"]: (task["schedule_label"], task["trigger_mode"])
        for task in tasks
        if task["task_id"] in ("ingest_insider_datasets", "ingest_market_bars", "ingest_splits", "onboard_tickers")
    }
    assert due_now == {
        "ingest_insider_datasets": ("Every 1 h", "automatic"),
        "ingest_market_bars": ("Every 10 min", "automatic"),
        "ingest_splits": ("Daily 17:00 ET", "automatic"),
        "onboard_tickers": ("Daily 19:00 ET", "automatic"),
    }
    assert all(task["next_run_at"] is not None for task in tasks if task["task_id"] in due_now)
    tasks = [task for task in tasks if task["task_id"] not in due_now]
    assert tasks == [
        {
            "task_id": "ingest_prices",
            "label": "Ingest daily price bars",
            "schedule_label": "Weekdays 18:30 ET",
            "trigger_mode": "automatic",
            "enabled": True,
            "next_run_at": None,
            "last_run": None,
        },
        {
            "task_id": "ingest_sec_filings",
            "label": "Ingest SEC filings",
            "schedule_label": "Every 5 min",
            "trigger_mode": "automatic",
            "enabled": True,
            "next_run_at": None,
            "last_run": None,
        },
    ]


def test_next_run_is_shown_only_for_enabled_automatic_tasks(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE task_definitions SET next_run_at = '2026-11-25T15:00:00Z',
                   trigger_mode = CASE WHEN task_id = 'ingest_sec_filings' THEN 'automatic' ELSE 'manual' END
            """)
    conn.commit()

    by_id = {task["task_id"]: task for task in signed_in_client.get("/api/tasks").json()}

    assert by_id["ingest_sec_filings"]["next_run_at"] == "2026-11-25T15:00:00Z"
    assert by_id["ingest_prices"]["next_run_at"] is None


def test_run_now_queues_a_pending_manual_run(signed_in_client, connect_app_db) -> None:
    response = signed_in_client.post("/api/tasks/ingest_prices/run")

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.json() == {"run_id": run_id, "status": "pending"}

    with connect_app_db().cursor() as cursor:
        cursor.execute("SELECT status, trigger_source, run_mode, requested_by FROM task_runs WHERE id = %s", (run_id,))
        assert cursor.fetchone() == ("pending", "api", "manual", "web:admin")

    by_id = {task["task_id"]: task for task in signed_in_client.get("/api/tasks").json()}
    last_run = by_id["ingest_prices"]["last_run"]
    assert last_run["id"] == run_id and last_run["status"] == "pending"


def test_run_now_refuses_unknown_and_disabled_tasks(signed_in_client, connect_app_db) -> None:
    assert signed_in_client.post("/api/tasks/no_such_task/run").status_code == 404

    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE task_definitions SET enabled = FALSE WHERE task_id = 'ingest_prices'")
    conn.commit()
    assert signed_in_client.post("/api/tasks/ingest_prices/run").status_code == 409


def test_runs_are_listed_newest_first_with_filters_and_paging(signed_in_client, connect_app_db) -> None:
    first = _enqueue(connect_app_db, "ingest_prices")
    second = _enqueue(connect_app_db, "ingest_sec_filings")
    third = _enqueue(connect_app_db, "ingest_prices")
    _set_run(
        connect_app_db,
        first,
        "status = 'failed', started_at = created_at, completed_at = created_at + interval '3.4 seconds', "
        "error_message = 'boom', fetched_count = 3, inserted_count = 2, deduped_count = 1",
    )

    page = signed_in_client.get("/api/task-runs").json()
    assert [item["id"] for item in page["items"]] == [third, second, first]
    assert page["total"] == 3
    oldest = page["items"][2]
    assert oldest["duration_seconds"] == pytest.approx(3.4)
    assert (oldest["fetched_count"], oldest["inserted_count"], oldest["deduped_count"]) == (3, 2, 1)
    assert oldest["error_message"] == "boom"
    assert page["items"][0]["started_at"] is None and page["items"][0]["duration_seconds"] is None

    by_task = signed_in_client.get("/api/task-runs", params={"task_id": "ingest_prices"}).json()
    assert [item["id"] for item in by_task["items"]] == [third, first]

    by_status = signed_in_client.get("/api/task-runs", params={"status": "failed"}).json()
    assert [item["id"] for item in by_status["items"]] == [first] and by_status["total"] == 1

    paged = signed_in_client.get("/api/task-runs", params={"limit": 1, "offset": 1}).json()
    assert [item["id"] for item in paged["items"]] == [second] and paged["total"] == 3

    beyond = signed_in_client.get("/api/task-runs", params={"limit": 10, "offset": 10}).json()
    assert beyond == {"items": [], "total": 3}


@pytest.mark.parametrize("params", [{"status": "done"}, {"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_invalid_run_filters_are_422(signed_in_client, params) -> None:
    assert signed_in_client.get("/api/task-runs", params=params).status_code == 422


def test_run_detail_includes_trace_and_meta(signed_in_client, connect_app_db) -> None:
    run_id = _enqueue(
        connect_app_db,
        "ingest_prices",
        tickers=["nvda"],
        as_of_date=date(2026, 11, 20),
        force_refresh=True,
        payload={"lookback_days": 3},
    )

    detail = signed_in_client.get(f"/api/task-runs/{run_id}").json()

    assert detail["id"] == run_id and detail["status"] == "pending"
    assert len(detail["trace_id"]) == 36
    assert detail["tickers"] == ["NVDA"]
    assert detail["as_of_date"] == "2026-11-20"
    assert detail["force_refresh"] is True
    assert detail["meta"]["dispatch_payload"]["lookback_days"] == 3
    assert signed_in_client.get("/api/task-runs/999999").status_code == 404


def test_run_again_queues_a_copy_with_the_same_parameters(signed_in_client) -> None:
    original = signed_in_client.post("/api/tasks/ingest_prices/run").json()["run_id"]
    original_detail = signed_in_client.get(f"/api/task-runs/{original}").json()

    response = signed_in_client.post(f"/api/task-runs/{original}/rerun")

    assert response.status_code == 202
    copy = signed_in_client.get(f"/api/task-runs/{response.json()['run_id']}").json()
    assert copy["id"] != original and copy["status"] == "pending"
    assert copy["trace_id"] != original_detail["trace_id"]
    for field in ("task_id", "tickers", "as_of_date", "force_refresh"):
        assert copy[field] == original_detail[field]
    assert copy["meta"]["dispatch_payload"] == original_detail["meta"]["dispatch_payload"]
    assert copy["requested_by"] == "web:admin"


def test_run_again_copies_cli_parameters(signed_in_client, connect_app_db) -> None:
    run_id = _enqueue(
        connect_app_db, "ingest_sec_filings", tickers=["ANT", "NBIS"], force_refresh=True, payload={"lookback_days": 3}
    )

    copy_id = signed_in_client.post(f"/api/task-runs/{run_id}/rerun").json()["run_id"]
    copy = signed_in_client.get(f"/api/task-runs/{copy_id}").json()

    assert copy["tickers"] == ["ANT", "NBIS"] and copy["force_refresh"] is True
    assert copy["meta"]["dispatch_payload"]["lookback_days"] == 3
    assert signed_in_client.post("/api/task-runs/999999/rerun").status_code == 404
