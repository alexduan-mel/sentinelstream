import { formatRelative } from "../lib/time";
import { apiFetch } from "./client";
import type { TaskDefinition } from "./tasks";

export type FilingItem = { number: string; title: string | null };
export type Materiality = "high" | "low";

export type SlackDelivery = {
  status: "pending" | "delivered" | "skipped";
  delivered_at: string | null;
  /** Why a pending alert has not gone yet, or why a skipped one never will. */
  detail: string | null;
};

export type Filing = {
  accession_no: string;
  cik: string;
  /** One ticker per watchlist issuer the filing is listed under; empty if none is known. */
  tickers: string[];
  form_type: string;
  items: FilingItem[];
  accepted_at: string;
  filed_date: string;
  sec_url: string;
  /** High if any linked issuer is high; null until every link is classified. */
  materiality: Materiality | null;
  materiality_reason: string | null;
  /** Shares bought in the open market, for a high Form 4. */
  purchase_shares: number | null;
  /** Set once the filing was queued for Slack. */
  slack: SlackDelivery | null;
};

export type FilingPage = { items: Filing[]; total: number };
export type FilingFacets = { tickers: string[]; form_types: string[] };

export type FilingFilters = {
  tickers: string[];
  formTypes: string[];
  /** Calendar days in ``timeZone``, "YYYY-MM-DD". */
  acceptedFrom?: string;
  acceptedTo?: string;
  materiality?: Materiality;
  /** IANA zone whose calendar days the accepted dates are; the API defaults to US Eastern. */
  timeZone?: string;
  limit: number;
  offset: number;
};

export function filingsQuery(filters: FilingFilters): string {
  const params = new URLSearchParams({
    limit: String(filters.limit),
    offset: String(filters.offset)
  });
  filters.tickers.forEach((ticker) => params.append("ticker", ticker));
  filters.formTypes.forEach((form) => params.append("form_type", form));
  if (filters.acceptedFrom) params.set("accepted_from", filters.acceptedFrom);
  if (filters.acceptedTo) params.set("accepted_to", filters.acceptedTo);
  if (filters.materiality) params.set("materiality", filters.materiality);
  if (filters.timeZone) params.set("tz", filters.timeZone);
  return params.toString();
}

export function listFilings(filters: FilingFilters): Promise<FilingPage> {
  return apiFetch<FilingPage>(`/api/filings?${filingsQuery(filters)}`);
}

export function getFilingFacets(): Promise<FilingFacets> {
  return apiFetch<FilingFacets>("/api/filings/facets");
}

/** "Item 2.02 · Results of Operations and Financial Condition", or "Item 2.02" without a title. */
export function itemLabel(item: FilingItem): string {
  return item.title ? `Item ${item.number} · ${item.title}` : `Item ${item.number}`;
}

const shareFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** "Open-market purchase · 12,400 sh" for a high Form 4, else null. */
export function purchaseLabel(filing: Filing): string | null {
  if (filing.purchase_shares === null) return null;
  return `Open-market purchase · ${shareFormat.format(filing.purchase_shares)} sh`;
}

export const SEC_POLL_TASK = "ingest_sec_filings";

/** "Polls every 5 min · last ran 2m ago · next in 3m", from the SEC poller's task definition. */
export function pollLine(task: TaskDefinition | undefined, now: Date = new Date()): string | null {
  if (!task) return null;
  if (task.trigger_mode !== "automatic" || !task.enabled) return "SEC polling is off";
  const parts = [`Polls ${task.schedule_label.toLowerCase()}`];
  const last = task.last_run;
  if (last) {
    if (last.status === "pending" || last.status === "running") parts.push("polling now");
    else {
      const failed = last.status === "failed" || last.status === "partial";
      const ago = formatRelative(new Date(last.created_at), now);
      parts.push(`last ran ${ago}${failed ? ` (${last.status})` : ""}`);
    }
  }
  if (task.next_run_at) {
    const next = new Date(task.next_run_at);
    parts.push(
      next.getTime() <= now.getTime() ? "next due now" : `next ${formatRelative(next, now)}`
    );
  }
  return parts.join(" · ");
}

export type FilingIssuer = {
  ticker: string | null;
  cik: string;
  materiality: Materiality | null;
  materiality_reason: string | null;
  /** History: loaded when the issuer was first polled or onboarded, so never pushed. */
  is_backfill: boolean;
  first_seen_at: string;
};

export type Form4Transaction = {
  line_no: number;
  reporting_owners: string;
  owner_roles: string;
  security_title: string | null;
  transaction_date: string | null;
  transaction_code: string;
  acquired_disposed: "A" | "D" | null;
  shares: number | null;
  price_per_share: number | null;
  shares_owned_after: number | null;
  direct_indirect: "D" | "I" | null;
};

/** What similar past events were followed by, at one horizon. Returns are fractions (0.012 = 1.2%). */
export type BaseRateStats = {
  n: number;
  /** Average return after trading costs, minus QQQ's over the same days. */
  mean: number;
  median: number;
  /** Share of past events that beat QQQ after costs. */
  hit_rate: number;
  /** 95% range for the average; null with fewer than six months of events. */
  ci_low: number | null;
  ci_high: number | null;
  months: number;
  mean_gross_return: number;
  mean_benchmark_return: number;
  mean_cost: number;
  /** Average after costs, minus SPY's. */
  mean_excess_vs_reference: number | null;
  ended_early: number;
};

export type BaseRateVariant = "main" | "none";
export const BASE_RATE_HORIZONS = ["1", "5", "20", "60"] as const;

export type BaseRate = {
  event_kind: "insider_purchase" | "8k_item";
  event_key: string;
  label: string;
  scope: "industry" | "market";
  scope_label: string;
  as_of: string;
  variants: Record<BaseRateVariant, Record<string, BaseRateStats | null>>;
};

export type FilingDetail = Filing & {
  report_date: string | null;
  primary_document: string | null;
  primary_document_url: string | null;
  first_seen_at: string;
  /** Acceptance to first sighting by the poller; null for history. */
  detection_delay_seconds: number | null;
  slack_attempts: number | null;
  /** Acceptance to Slack delivery. */
  alert_latency_seconds: number | null;
  issuers: FilingIssuer[];
  form4_transactions: Form4Transaction[];
  /** One per high event, for the subject company; empty for a low filing or before one is calculated. */
  base_rates: BaseRate[];
};

export function getFiling(accessionNo: string): Promise<FilingDetail> {
  return apiFetch<FilingDetail>(`/api/filings/${encodeURIComponent(accessionNo)}`);
}

/** Form 4 transaction codes as SEC's Form 4 instructions define them. */
export const FORM4_CODES: Record<string, string> = {
  P: "Open-market or private purchase",
  S: "Open-market or private sale",
  A: "Grant or award",
  D: "Disposition to the issuer",
  F: "Shares withheld for exercise price or tax",
  I: "Discretionary transaction",
  M: "Exercise or conversion of a derivative",
  C: "Conversion of a derivative",
  E: "Expiration of a short derivative position",
  H: "Expiration of a long derivative position",
  O: "Exercise of an out-of-the-money derivative",
  X: "Exercise of an in- or at-the-money derivative",
  G: "Gift",
  L: "Small acquisition",
  W: "Acquired or disposed of by will or inheritance",
  Z: "Deposit into or withdrawal from a voting trust",
  J: "Other acquisition or disposition",
  K: "Equity swap",
  U: "Tendered in a change of control",
  V: "Reported voluntarily earlier than required"
};

/** "P · Open-market or private purchase", or the bare code if SEC's list does not have it. */
export function form4CodeLabel(code: string): string {
  const meaning = FORM4_CODES[code];
  return meaning ? `${code} · ${meaning}` : code;
}

/** "Form 4" for SEC's form type "4"; other forms as written. */
export function formLabel(formType: string): string {
  return formType === "4" ? "Form 4" : formType;
}
