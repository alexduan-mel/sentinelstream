import { apiFetch } from "./client";

export type OnboardingStatus = "pending" | "backfilling" | "ready" | "failed";

export type Instrument = {
  symbol: string;
  name: string | null;
  asset_type: "equity" | "etf";
  role: "watchlist" | "benchmark";
  cik: string | null;
  filer_type: "domestic" | "foreign_private" | null;
  onboarding_status: OnboardingStatus;
  /** Why onboarding failed, or the step a transient error stopped it at. */
  onboarding_error: string | null;
  last_filing: { form_type: string; accepted_at: string } | null;
  prices: { first: string; last: string; bars: number } | null;
};

export type QueuedOnboarding = {
  symbol: string;
  onboarding_status: OnboardingStatus;
  run_id: number;
};

export function listInstruments(): Promise<Instrument[]> {
  return apiFetch<Instrument[]>("/api/instruments");
}

export function addInstrument(symbol: string): Promise<QueuedOnboarding> {
  return apiFetch<QueuedOnboarding>("/api/instruments", {
    method: "POST",
    body: JSON.stringify({ symbol })
  });
}

export async function removeInstrument(symbol: string): Promise<void> {
  await apiFetch<null>(`/api/instruments/${encodeURIComponent(symbol)}`, { method: "DELETE" });
}

export function retryOnboarding(symbol: string): Promise<QueuedOnboarding> {
  return apiFetch<QueuedOnboarding>(`/api/instruments/${encodeURIComponent(symbol)}/onboard`, {
    method: "POST"
  });
}

/** Still being onboarded: the page refreshes until none is. */
export function isOnboarding(instrument: Instrument): boolean {
  return (
    instrument.onboarding_status === "pending" || instrument.onboarding_status === "backfilling"
  );
}

export const FILER_TYPE_LABEL: Record<NonNullable<Instrument["filer_type"]>, string> = {
  domestic: "Domestic",
  foreign_private: "Foreign private"
};

const dayFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric"
});

/** "501 daily bars · Nov 26, 2024 – Nov 25, 2026". Dates are trading days, so no time zone applies. */
export function priceHistoryLabel(prices: Instrument["prices"]): string | null {
  if (!prices) return null;
  const day = (value: string) => dayFormat.format(new Date(`${value}T00:00:00Z`));
  const bars = `${prices.bars.toLocaleString("en-US")} daily ${prices.bars === 1 ? "bar" : "bars"}`;
  return `${bars} · ${day(prices.first)} – ${day(prices.last)}`;
}
