import { apiFetch } from "./client";

/** What a Journal entry records the owner did (ADR 0025). */
export type JournalAction = "opened" | "added" | "trimmed" | "closed" | "pass";
export type CorrectionKind = "thesis" | "source" | "withdrawn";

export type JournalSource = {
  accession_no: string;
  form_type: string;
  accepted_at: string;
  url: string;
};

export type JournalCorrection = {
  kind: CorrectionKind;
  thesis: string | null;
  source: JournalSource | null;
  reason: string;
  known_at: string;
  recorded_by: string;
};

/** How an entry has done since, against QQQ and SPY (ADR 0026). Prices are in today's share basis. */
export type JournalScore = {
  /** waiting: no close to start from yet, or the trade day's prices haven't arrived. */
  status: "scored" | "waiting";
  start_date: string | null;
  start_basis: "trade" | "next_close" | null;
  start_price: number | null;
  /** For an entry with a trade price: whether it fell inside that day's range. */
  price_check: "ok" | "out_of_range" | null;
  end_date: string | null;
  end_price: number | null;
  ends: "closed" | "latest" | null;
  closed_by: number | null;
  sessions: number;
  stock_return: number | null;
  qqq_return: number | null;
  spy_return: number | null;
  vs_qqq: number | null;
  vs_spy: number | null;
  /** ADR 0027. too_early: fewer than 20 daily returns, so no beta or alpha. */
  fit: "ok" | "too_early" | null;
  days: number;
  /** How much the stock moved per 1% move in QQQ. */
  beta: number | null;
  r_squared: number | null;
  /** beta x QQQ's return: the part of the stock's return QQQ's moves explain. */
  market_part: number | null;
  /** The stock's return minus the market part. */
  alpha: number | null;
};

/** Every scored entry together; each counts once (ADR 0027). */
export type JournalCombined = {
  scored: number;
  fitted: number;
  too_early: number;
  /** Mean alpha, negated for Trimmed, Closed and Pass: above zero means the calls were right. */
  alpha_of_calls: number | null;
  calls_right: number;
  /** One regression over every day of the fitted Opened and Added entries. */
  beta_of_holdings: number | null;
  holdings_days: number;
};

export type JournalEntry = {
  id: number;
  symbol: string;
  name: string | null;
  action: JournalAction;
  /** As corrected; the originals are kept alongside. */
  thesis: string;
  source: JournalSource | null;
  original_thesis: string;
  original_source: JournalSource | null;
  /** A real trade: its day (YYYY-MM-DD) and price per share as traded; shares as traded that day. */
  traded_on: string | null;
  price: number | null;
  shares: number | null;
  /** When it was logged, by the server's clock. */
  known_at: string;
  recorded_by: string;
  withdrawn: JournalCorrection | null;
  corrections: JournalCorrection[];
  /** Null for a withdrawn entry. */
  score: JournalScore | null;
};

/** One position in dollars, against the same dollars in QQQ (ADR 0028). */
export type MoneyPosition = {
  symbol: string;
  opened_id: number;
  start_date: string | null;
  end_date: string | null;
  status: "open" | "closed";
  /** Left out of the totals: an entry gave no share count, or prices haven't arrived yet. */
  excluded: "no_shares" | "waiting" | null;
  bought: number | null;
  sold: number | null;
  value: number | null;
  profit: number | null;
  qqq_profit: number | null;
};

export type Money = {
  positions: MoneyPosition[];
  bought: number;
  profit: number;
  qqq_profit: number;
  excluded: number;
};

export type Journal = {
  entries: JournalEntry[];
  open_positions: string[];
  /** Every ticker with an entry, whatever the filter. */
  tickers: string[];
  /** These cover the entries in the response: one ticker's when filtered. */
  combined: JournalCombined;
  money: Money;
};

export type NewEntry = {
  symbol: string;
  action: JournalAction;
  thesis: string;
  accession_no?: string | null;
  traded_on?: string | null;
  price?: number | null;
  shares?: number | null;
};

export type NewCorrection = {
  kind: CorrectionKind;
  reason: string;
  thesis?: string | null;
  accession_no?: string | null;
};

export const THESIS_MAX = 4000;
export const REASON_MAX = 1000;

export function getJournal(symbol?: string): Promise<Journal> {
  const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  return apiFetch<Journal>(`/api/journal${query}`);
}

export function logEntry(entry: NewEntry): Promise<JournalEntry> {
  return apiFetch<JournalEntry>("/api/journal", { method: "POST", body: JSON.stringify(entry) });
}

export function correctEntry(id: number, correction: NewCorrection): Promise<JournalEntry> {
  return apiFetch<JournalEntry>(`/api/journal/${id}/corrections`, {
    method: "POST",
    body: JSON.stringify(correction)
  });
}

export const ACTIONS: JournalAction[] = ["opened", "added", "trimmed", "closed", "pass"];

export const ACTION_LABEL: Record<JournalAction, string> = {
  opened: "Opened",
  added: "Added",
  trimmed: "Trimmed",
  closed: "Closed",
  pass: "Pass"
};

/** Plain-language meaning, for hovers and the form. */
export const ACTION_HELP: Record<JournalAction, string> = {
  opened: "Started a position: bought shares when none were held",
  added: "Bought more of a position already held",
  trimmed: "Sold part of a position, keeping some",
  closed: "Sold the whole position",
  pass: "Looked at it and chose not to act. Scored too, so not acting is on the record"
};

/**
 * Whether an action can be logged now. Opened starts a position, Added, Trimmed and Closed need one,
 * and Pass is always allowed (the same rule the API enforces).
 */
export function actionAllowed(action: JournalAction, open: boolean): boolean {
  if (action === "pass") return true;
  return action === "opened" ? !open : open;
}

export function isTrade(action: JournalAction): boolean {
  return action !== "pass";
}

const tradeDay = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric"
});
const priceFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 6
});
const sharesFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });

/** "Nov 20, 2026 · $185.25 · 12.5 sh", "12 sh" or null when the entry gave no trade details. */
export function tradeLabel(
  entry: Pick<JournalEntry, "traded_on" | "price" | "shares">
): string | null {
  const parts: string[] = [];
  if (entry.traded_on) parts.push(tradeDay.format(new Date(`${entry.traded_on}T00:00:00Z`)));
  if (entry.price !== null) parts.push(priceFormat.format(entry.price));
  if (entry.shares !== null) parts.push(`${sharesFormat.format(entry.shares)} sh`);
  return parts.length ? parts.join(" · ") : null;
}

/** Opened and Added are calls that the stock does well; Trimmed, Closed and Pass that it doesn't. */
export const CALL_SIGN: Record<JournalAction, 1 | -1> = {
  opened: 1,
  added: 1,
  trimmed: -1,
  closed: -1,
  pass: -1
};

/** Whether a call was right beyond what QQQ's moves explain; null until it has an alpha. */
export function callRight(entry: Pick<JournalEntry, "action" | "score">): boolean | null {
  const alpha = entry.score?.alpha;
  if (alpha === null || alpha === undefined) return null;
  return CALL_SIGN[entry.action] * alpha > 0;
}

const percentFormat = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
  signDisplay: "exceptZero"
});
const dollarsFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0
});
const signedDollarsFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
  signDisplay: "exceptZero"
});

/** "+10.0%", "−2.3%", "0.0%". */
export function percent(value: number): string {
  return percentFormat.format(value).replace("-", "−");
}

/** "$1,565". */
export function dollars(value: number): string {
  return dollarsFormat.format(value);
}

/** "+$73", "−$120". */
export function signedDollars(value: number): string {
  return signedDollarsFormat.format(value).replace("-", "−");
}

/** "Nov 20, 2026" for a trading day (YYYY-MM-DD; no time zone applies). */
export function dayLabel(value: string): string {
  return tradeDay.format(new Date(`${value}T00:00:00Z`));
}
