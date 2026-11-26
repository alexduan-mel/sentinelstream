"""The time zone people read times in, on the web UI and in Slack (``DISPLAY_TIMEZONE``).

Times are stored in UTC; only display converts them. The setting is an IANA zone name, so daylight
saving is followed automatically. Unset, it is US Eastern, the zone EDGAR and the market use. US
Eastern stays available next to it, because when a filing landed relative to the US market session
matters (before the open, after the close).
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

US_EASTERN = "America/New_York"


class DisplayTimezoneError(RuntimeError):
    pass


def display_timezone() -> ZoneInfo:
    name = os.getenv("DISPLAY_TIMEZONE", "").strip() or US_EASTERN
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DisplayTimezoneError(
            f"DISPLAY_TIMEZONE {name!r} is not an IANA time zone name such as America/New_York"
        ) from None


def zone_label(when: datetime, zone: ZoneInfo) -> str:
    """``ET`` for US Eastern, else the zone's abbreviation at that moment (AEDT in summer, AEST in winter)."""
    if zone.key == US_EASTERN:
        return "ET"
    return when.astimezone(zone).tzname() or zone.key


def format_clock(when: datetime, zone: ZoneInfo) -> str:
    """``Nov 27, 08:05 AEDT``."""
    local = when.astimezone(zone)
    return f"{local:%b} {local.day}, {local:%H:%M} {zone_label(when, zone)}"
