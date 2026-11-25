/** Times are stored in UTC and shown in US Eastern (DESIGN.md, Layout). */

const ZONE = "America/New_York";

const clockFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23"
});
const dayFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: ZONE,
  month: "short",
  day: "numeric"
});
const yearFormat = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, year: "numeric" });
const dateKeyFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
});

export type FormattedTime = {
  /** "14:32 ET" today, "Nov 23 14:32 ET" earlier this year, "Nov 23, 2025 14:32 ET" before that. */
  text: string;
  /** "8m ago" or "in 3m", only for times on today's date in ET. */
  relative: string | null;
};

export function formatTime(iso: string, now: Date = new Date()): FormattedTime {
  const date = new Date(iso);
  const clock = `${clockFormat.format(date)} ET`;
  if (dateKeyFormat.format(date) === dateKeyFormat.format(now)) {
    return { text: clock, relative: formatRelative(date, now) };
  }
  const day = dayFormat.format(date);
  if (yearFormat.format(date) === yearFormat.format(now)) {
    return { text: `${day} ${clock}`, relative: null };
  }
  return { text: `${day}, ${yearFormat.format(date)} ${clock}`, relative: null };
}

export function formatRelative(date: Date, now: Date = new Date()): string {
  const seconds = Math.round((now.getTime() - date.getTime()) / 1000);
  const future = seconds < 0;
  const magnitude = Math.abs(seconds);
  let amount: string;
  if (magnitude < 60) amount = "<1m";
  else if (magnitude < 3600) amount = `${Math.floor(magnitude / 60)}m`;
  else if (magnitude < 86400) amount = `${Math.floor(magnitude / 3600)}h`;
  else amount = `${Math.floor(magnitude / 86400)}d`;
  return future ? `in ${amount}` : `${amount} ago`;
}

/** "0.4s", "3.4s", "2m 5s", "1h 3m". */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.round(seconds);
  if (whole < 3600) return `${Math.floor(whole / 60)}m ${whole % 60}s`;
  return `${Math.floor(whole / 3600)}h ${Math.floor((whole % 3600) / 60)}m`;
}
