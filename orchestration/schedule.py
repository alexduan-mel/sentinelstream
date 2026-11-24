"""When a scheduled task runs next. Carried over from the prototype's task_scheduler_worker.py.

A missed run (the scheduler was down) runs once on the next tick and the following run is computed
from that moment, so downtime never causes a burst of catch-up runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SATURDAY = 5


@dataclass(frozen=True)
class Schedule:
    """The scheduling columns of one ``task_definitions`` row."""

    schedule_type: str  # 'interval' or 'daily_time'
    interval_minutes: int | None = None
    scheduled_time: time | None = None
    schedule_timezone: str = "UTC"
    schedule_days: str = "all"  # 'all' or 'weekdays' (Mon-Fri in schedule_timezone)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid schedule timezone {name!r}") from exc


def next_run_at(schedule: Schedule, now: datetime) -> datetime:
    """The first run time strictly after ``now`` (an aware datetime), in UTC."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = _zone(schedule.schedule_timezone)
    weekdays_only = schedule.schedule_days == "weekdays"

    if schedule.schedule_type == "interval":
        if not schedule.interval_minutes or schedule.interval_minutes <= 0:
            raise ValueError("interval schedule needs a positive interval_minutes")
        local = (now + timedelta(minutes=schedule.interval_minutes)).astimezone(zone)
        while weekdays_only and local.weekday() >= SATURDAY:
            local += timedelta(days=1)
        return local.astimezone(UTC)

    if schedule.schedule_type == "daily_time":
        if schedule.scheduled_time is None:
            raise ValueError("daily_time schedule needs a scheduled_time")
        local_now = now.astimezone(zone)
        local = datetime.combine(local_now.date(), schedule.scheduled_time, tzinfo=zone)
        if local <= local_now:
            local = datetime.combine(local_now.date() + timedelta(days=1), schedule.scheduled_time, tzinfo=zone)
        while weekdays_only and local.weekday() >= SATURDAY:
            local = datetime.combine(local.date() + timedelta(days=1), schedule.scheduled_time, tzinfo=zone)
        return local.astimezone(UTC)

    raise ValueError(f"Unsupported schedule_type {schedule.schedule_type!r}")
