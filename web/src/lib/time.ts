/**
 * Times are stored in UTC. The page shows them in US Eastern (the market's clock) or in the
 * browser's own zone, whichever the viewer picks in the top bar. Intl follows daylight saving for
 * every IANA zone, so the browser's zone reads AEDT in summer and AEST in winter by itself.
 */

export const US_EASTERN = "America/New_York";

/** The browser's IANA zone, e.g. "Australia/Melbourne". */
export function browserZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || US_EASTERN;
}

type Formatters = {
  clock: Intl.DateTimeFormat;
  day: Intl.DateTimeFormat;
  year: Intl.DateTimeFormat;
  dateKey: Intl.DateTimeFormat;
  name: Intl.DateTimeFormat;
};

const cache = new Map<string, Formatters>();

function formatters(zone: string): Formatters {
  let found = cache.get(zone);
  if (!found) {
    found = {
      clock: new Intl.DateTimeFormat("en-US", {
        timeZone: zone,
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23"
      }),
      day: new Intl.DateTimeFormat("en-US", { timeZone: zone, month: "short", day: "numeric" }),
      year: new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric" }),
      dateKey: new Intl.DateTimeFormat("en-CA", {
        timeZone: zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit"
      }),
      // en-AU names Australian zones (AEDT, AEST) and falls back to GMT offsets elsewhere.
      name: new Intl.DateTimeFormat("en-AU", { timeZone: zone, timeZoneName: "short" })
    };
    cache.set(zone, found);
  }
  return found;
}

/** "ET" for US Eastern, else the zone's abbreviation at that moment: AEDT in summer, AEST in winter. */
export function zoneLabel(date: Date, zone: string): string {
  if (zone === US_EASTERN) return "ET";
  const part = formatters(zone)
    .name.formatToParts(date)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? zone;
}

export type FormattedTime = {
  /** "14:32 ET" today, "Nov 23 14:32 ET" earlier this year, "Nov 23, 2025 14:32 ET" before that. */
  text: string;
  /** "8m ago" or "in 3m", only for times on today's date in the zone. */
  relative: string | null;
};

export function formatTime(
  iso: string,
  now: Date = new Date(),
  zone: string = US_EASTERN
): FormattedTime {
  const date = new Date(iso);
  const f = formatters(zone);
  const clock = `${f.clock.format(date)} ${zoneLabel(date, zone)}`;
  if (f.dateKey.format(date) === f.dateKey.format(now)) {
    return { text: clock, relative: formatRelative(date, now) };
  }
  const day = f.day.format(date);
  if (f.year.format(date) === f.year.format(now)) {
    return { text: `${day} ${clock}`, relative: null };
  }
  return { text: `${day}, ${f.year.format(date)} ${clock}`, relative: null };
}

/** The same moment with its date, in another zone, for hover text: "Nov 26 16:05 ET". */
export function formatInZone(iso: string, zone: string): string {
  const date = new Date(iso);
  const f = formatters(zone);
  return `${f.day.format(date)} ${f.clock.format(date)} ${zoneLabel(date, zone)}`;
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
