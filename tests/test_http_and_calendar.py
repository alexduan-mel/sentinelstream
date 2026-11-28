"""Shared HTTP pacing/retries and the US market calendar."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from ingestion import market_calendar as cal
from ingestion.http import Pacer, retry_after_seconds, send_with_retries


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://example.test/x"))


# --- pacing and retries ----------------------------------------------------------------------------


def test_pacer_spaces_calls_by_the_interval() -> None:
    clock = FakeClock()
    pacer = Pacer(0.2, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        pacer.wait()
    assert clock.slept == [0.2, 0.2]


def test_retry_after_accepts_seconds_and_http_dates() -> None:
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert retry_after_seconds(_response(429, {"Retry-After": "7"})) == 7
    assert retry_after_seconds(_response(429, {"Retry-After": "Wed, 25 Nov 2026 12:00:30 GMT"}), now=now) == 30
    assert retry_after_seconds(_response(429, {"Retry-After": "soon"})) is None
    assert retry_after_seconds(_response(429)) is None


def test_retries_honour_retry_after_then_return_the_success() -> None:
    clock = FakeClock()
    pacer = Pacer(0, clock=clock, sleep=clock.sleep)
    responses = iter([_response(429, {"Retry-After": "9"}), _response(503), _response(200)])

    result = send_with_retries(lambda: next(responses), pacer=pacer, base_delay=1)

    assert result.status_code == 200
    # Retry-After 9 s beats the 1 s backoff; the second retry backs off 2 s.
    assert clock.slept == [9, 2]


def test_a_retry_after_beyond_the_cap_is_honoured_by_giving_up() -> None:
    calls = []

    def send() -> httpx.Response:
        calls.append(1)
        return _response(429, {"Retry-After": "3600"})

    result = send_with_retries(send, pacer=Pacer(0), max_retry_after=120)
    assert result.status_code == 429
    assert len(calls) == 1


def test_non_retryable_statuses_return_at_once_and_transport_errors_raise_after_the_last_attempt() -> None:
    assert send_with_retries(lambda: _response(404), pacer=Pacer(0)).status_code == 404

    attempts = []

    def broken() -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        send_with_retries(broken, pacer=Pacer(0), attempts=3, base_delay=0)
    assert len(attempts) == 3


# --- market calendar -------------------------------------------------------------------------------


def test_2026_holidays_match_the_published_nyse_schedule() -> None:
    assert sorted(cal.holidays(2026)) == [
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),  # 4 July is a Saturday
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    ]


def test_a_saturday_new_years_day_closes_nothing() -> None:
    # 1 January 2022 was a Saturday: NYSE traded on Friday 31 December 2021.
    assert cal.is_trading_day(date(2021, 12, 31))
    assert date(2021, 12, 31) not in cal.holidays(2022)
    # A Sunday New Year's Day moves to Monday (2023).
    assert not cal.is_trading_day(date(2023, 1, 2))


def test_early_closes() -> None:
    assert cal.session_close(date(2026, 11, 27)) == cal.EARLY_CLOSE  # day after Thanksgiving
    assert cal.session_close(date(2026, 12, 24)) == cal.EARLY_CLOSE  # Christmas is a Friday
    assert cal.session_close(date(2026, 7, 2)) == cal.REGULAR_CLOSE  # 4 July is a Saturday
    assert cal.session_close(date(2026, 11, 25)) == cal.REGULAR_CLOSE


@pytest.mark.parametrize(
    ("now_utc", "expected"),
    [
        (datetime(2026, 11, 25, 20, 59, tzinfo=UTC), date(2026, 11, 24)),  # 15:59 New York, still open
        (datetime(2026, 11, 25, 21, 0, tzinfo=UTC), date(2026, 11, 25)),  # 16:00 close
        (datetime(2026, 11, 26, 18, 0, tzinfo=UTC), date(2026, 11, 25)),  # Thanksgiving
        (datetime(2026, 11, 27, 18, 0, tzinfo=UTC), date(2026, 11, 27)),  # 13:00 after an early close
        (datetime(2026, 11, 29, 12, 0, tzinfo=UTC), date(2026, 11, 27)),  # Sunday
        (datetime(2026, 11, 30, 3, 0, tzinfo=UTC), date(2026, 11, 27)),  # Sunday evening in New York
    ],
)
def test_latest_completed_session(now_utc: datetime, expected: date) -> None:
    assert cal.latest_completed_session(now_utc) == expected


@pytest.mark.parametrize(
    ("now_utc", "expected"),
    [
        (datetime(2026, 11, 25, 22, 0, tzinfo=UTC), date(2026, 11, 24)),  # 17:00 New York: closed, day not over
        (datetime(2026, 11, 26, 4, 59, tzinfo=UTC), date(2026, 11, 24)),  # 23:59 New York
        (datetime(2026, 11, 26, 5, 0, tzinfo=UTC), date(2026, 11, 25)),  # midnight New York
        (datetime(2026, 11, 28, 0, 30, tzinfo=UTC), date(2026, 11, 25)),  # Friday evening after the half day
        (datetime(2026, 11, 28, 10, 0, tzinfo=UTC), date(2026, 11, 27)),  # Saturday morning
        (datetime(2026, 11, 30, 10, 0, tzinfo=UTC), date(2026, 11, 27)),  # Monday morning
        (datetime(2026, 7, 1, 4, 30, tzinfo=UTC), date(2026, 6, 30)),  # 00:30 New York in summer time: 30 June ended
    ],
)
def test_latest_published_session(now_utc: datetime, expected: date) -> None:
    assert cal.latest_published_session(now_utc) == expected


def test_latest_completed_session_needs_an_aware_time() -> None:
    with pytest.raises(ValueError):
        cal.latest_completed_session(datetime(2026, 11, 25, 12, 0))
    with pytest.raises(ValueError):
        cal.latest_published_session(datetime(2026, 11, 25, 12, 0))
