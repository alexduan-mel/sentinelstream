from datetime import UTC, datetime, time

import pytest

from orchestration.schedule import Schedule, next_run_at


def test_interval_counts_from_now() -> None:
    schedule = Schedule("interval", interval_minutes=15)

    assert next_run_at(schedule, datetime(2026, 1, 2, 3, 0, tzinfo=UTC)) == datetime(2026, 1, 2, 3, 15, tzinfo=UTC)


def test_weekday_interval_skips_the_weekend() -> None:
    schedule = Schedule("interval", interval_minutes=30, schedule_days="weekdays")

    # Friday 23:50 UTC + 30 min lands on Saturday, so it moves to Monday at the same time of day.
    assert next_run_at(schedule, datetime(2026, 1, 2, 23, 50, tzinfo=UTC)) == datetime(2026, 1, 5, 0, 20, tzinfo=UTC)


def test_daily_time_later_today() -> None:
    schedule = Schedule("daily_time", scheduled_time=time(22, 30))

    assert next_run_at(schedule, datetime(2026, 1, 2, 3, 0, tzinfo=UTC)) == datetime(2026, 1, 2, 22, 30, tzinfo=UTC)


def test_daily_time_exactly_now_moves_to_tomorrow() -> None:
    schedule = Schedule("daily_time", scheduled_time=time(22, 30))

    assert next_run_at(schedule, datetime(2026, 1, 2, 22, 30, tzinfo=UTC)) == datetime(2026, 1, 3, 22, 30, tzinfo=UTC)


def test_weekday_daily_time_skips_the_weekend() -> None:
    schedule = Schedule("daily_time", scheduled_time=time(22, 30), schedule_days="weekdays")

    assert next_run_at(schedule, datetime(2026, 1, 2, 23, 0, tzinfo=UTC)) == datetime(2026, 1, 5, 22, 30, tzinfo=UTC)


def test_daily_time_follows_new_york_across_daylight_saving() -> None:
    # The seeded ingest_prices schedule: 18:30 New York time on weekdays.
    schedule = Schedule(
        "daily_time", scheduled_time=time(18, 30), schedule_timezone="America/New_York", schedule_days="weekdays"
    )

    # Friday 6 March 2026 (EST, UTC-5) → Monday 9 March, after the switch to EDT (UTC-4).
    friday_evening = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)  # 19:00 EST Friday
    assert next_run_at(schedule, friday_evening) == datetime(2026, 3, 9, 22, 30, tzinfo=UTC)
    # Before Friday's run: still Friday, in EST.
    assert next_run_at(schedule, datetime(2026, 3, 6, 20, 0, tzinfo=UTC)) == datetime(2026, 3, 6, 23, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "schedule",
    [
        Schedule("interval", interval_minutes=None),
        Schedule("daily_time", scheduled_time=None),
        Schedule("cron"),
        Schedule("interval", interval_minutes=5, schedule_timezone="Mars/Olympus_Mons"),
    ],
)
def test_rejects_invalid_schedules(schedule: Schedule) -> None:
    with pytest.raises(ValueError):
        next_run_at(schedule, datetime(2026, 1, 2, tzinfo=UTC))


def test_rejects_naive_now() -> None:
    with pytest.raises(ValueError):
        next_run_at(Schedule("interval", interval_minutes=5), datetime(2026, 1, 2))
