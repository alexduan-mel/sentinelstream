import { describe, expect, it } from "vitest";

import { formatDuration, formatRelative, formatTime } from "./time";

// 25 Nov 2026 15:00 UTC is 10:00 ET (EST, UTC-5).
const NOW = new Date("2026-11-25T15:00:00Z");

describe("formatTime", () => {
  it("shows today's times as ET clock time with a relative time", () => {
    expect(formatTime("2026-11-25T14:52:00Z", NOW)).toEqual({
      text: "09:52 ET",
      relative: "8m ago"
    });
    expect(formatTime("2026-11-25T15:03:00Z", NOW)).toEqual({
      text: "10:03 ET",
      relative: "in 3m"
    });
  });

  it("uses the ET date, not the UTC date, to decide what is today", () => {
    // 04:30 UTC on 25 Nov is 23:30 ET on 24 Nov.
    expect(formatTime("2026-11-25T04:30:00Z", NOW)).toEqual({
      text: "Nov 24 23:30 ET",
      relative: null
    });
  });

  it("shows earlier dates with month and day, and other years with the year", () => {
    expect(formatTime("2026-11-23T19:32:00Z", NOW).text).toBe("Nov 23 14:32 ET");
    expect(formatTime("2025-07-04T13:00:00Z", NOW).text).toBe("Jul 4, 2025 09:00 ET");
  });

  it("follows daylight saving time", () => {
    // 1 Jul is EDT, UTC-4.
    expect(formatTime("2026-07-01T22:30:00Z", NOW).text).toBe("Jul 1 18:30 ET");
  });
});

describe("formatRelative", () => {
  it.each([
    [30, "<1m ago"],
    [8 * 60, "8m ago"],
    [3 * 3600 + 59, "3h ago"],
    [2 * 86400, "2d ago"],
    [-90, "in 1m"]
  ])("%i seconds ago reads %s", (secondsAgo, expected) => {
    expect(formatRelative(new Date(NOW.getTime() - secondsAgo * 1000), NOW)).toBe(expected);
  });
});

describe("formatDuration", () => {
  it.each([
    [0.4, "0.4s"],
    [3.44, "3.4s"],
    [125, "2m 5s"],
    [3780, "1h 3m"]
  ])("%f seconds reads %s", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });
});
