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
