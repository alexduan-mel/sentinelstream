import { apiFetch } from "./client";

export type FilingItem = { number: string; title: string | null };

export type Filing = {
  accession_no: string;
  cik: string;
  ticker: string | null;
  form_type: string;
  items: FilingItem[];
  accepted_at: string;
  filed_date: string;
  sec_url: string;
};

export type FilingPage = { items: Filing[]; total: number };
export type FilingFacets = { tickers: string[]; form_types: string[] };

export type FilingFilters = {
  tickers: string[];
  formTypes: string[];
  /** US Eastern calendar days, "YYYY-MM-DD". */
  acceptedFrom?: string;
  acceptedTo?: string;
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
