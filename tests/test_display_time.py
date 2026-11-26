"""The display time zone: US Eastern by default, any IANA zone by setting, daylight saving followed."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from alerts.display_time import DisplayTimezoneError, display_timezone, format_clock, zone_label

MELBOURNE = ZoneInfo("Australia/Melbourne")


def test_unset_means_us_eastern(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY_TIMEZONE", raising=False)
    assert str(display_timezone()) == "America/New_York"
    monkeypatch.setenv("DISPLAY_TIMEZONE", "  ")
    assert str(display_timezone()) == "America/New_York"


def test_a_set_zone_is_used_and_a_bad_one_fails_clearly(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY_TIMEZONE", " Australia/Melbourne ")
    assert str(display_timezone()) == "Australia/Melbourne"
    for bad in ("Mars/Olympus", "Not/A_Zone", "AEDT"):
        monkeypatch.setenv("DISPLAY_TIMEZONE", bad)
        with pytest.raises(DisplayTimezoneError, match="not an IANA time zone"):
            display_timezone()


def test_clock_follows_daylight_saving() -> None:
    # Melbourne is UTC+11 (AEDT) in November and UTC+10 (AEST) in July.
    assert format_clock(datetime(2026, 11, 26, 21, 5, tzinfo=UTC), MELBOURNE) == "Nov 27, 08:05 AEDT"
    assert format_clock(datetime(2026, 7, 1, 21, 5, tzinfo=UTC), MELBOURNE) == "Jul 2, 07:05 AEST"
    # Daylight saving ends at 03:00 AEDT on 5 Apr 2026, which is 16:00 UTC on 4 Apr.
    assert zone_label(datetime(2026, 4, 4, 15, 59, tzinfo=UTC), MELBOURNE) == "AEDT"
    assert zone_label(datetime(2026, 4, 4, 16, 1, tzinfo=UTC), MELBOURNE) == "AEST"


def test_us_eastern_is_labelled_et_all_year() -> None:
    eastern = ZoneInfo("America/New_York")
    assert format_clock(datetime(2026, 11, 26, 21, 5, tzinfo=UTC), eastern) == "Nov 26, 16:05 ET"
    assert format_clock(datetime(2026, 7, 1, 21, 5, tzinfo=UTC), eastern) == "Jul 1, 17:05 ET"
