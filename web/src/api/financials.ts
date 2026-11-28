import { apiFetch } from "./client";

/** Key financials from XBRL for one watchlist ticker (ADR 0020). */

export type Basis = "quarterly" | "annual";
export type MetricUnit = "USD" | "USD/shares" | "shares" | "ratio";

export type MetricInfo = {
  key: string;
  label: string;
  unit: MetricUnit;
  definition: string;
};

export type SourceFact = {
  concept: string;
  accession_no: string;
  form: string | null;
  filed_date: string;
  period_start: string | null;
  period_end: string;
  value: number;
  /** The filing's index page on EDGAR. */
  url: string;
};

export type MetricValue = {
  value: number;
  /** reported: one filed value; subtracted: a longer period minus the part before; computed: from several. */
  method: "reported" | "subtracted" | "computed";
  sources: SourceFact[];
};

export type FinancialPeriod = {
  start: string;
  end: string;
  fiscal_year: number | null;
  fiscal_period: string | null;
  values: Record<string, MetricValue | null>;
};

export type KeyFinancials = {
  symbol: string;
  cik: string;
  name: string | null;
  filer_type: "domestic" | "foreign_private" | null;
  basis: Basis;
  as_of: string;
  metrics: MetricInfo[];
  /** Newest first. */
  periods: FinancialPeriod[];
};

export const PERIODS_SHOWN = 8;

export function getKeyFinancials(symbol: string, basis: Basis): Promise<KeyFinancials> {
  const query = new URLSearchParams({ basis, periods: String(PERIODS_SHOWN) });
  return apiFetch<KeyFinancials>(
    `/api/instruments/${encodeURIComponent(symbol)}/financials?${query.toString()}`
  );
}

const dayFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric"
});

/** "Oct 26, 2026". Period and filing dates are calendar days, so no time zone applies. */
export function dayLabel(value: string): string {
  return dayFormat.format(new Date(`${value}T00:00:00Z`));
}

/** "Q3 FY2027" or "FY2027", from the fiscal labels of the filing; the end date if it had none. */
export function periodLabel(period: FinancialPeriod, basis: Basis): string {
  if (period.fiscal_year === null) return `Ended ${dayLabel(period.end)}`;
  if (basis === "annual") return `FY${period.fiscal_year}`;
  const quarter = period.fiscal_period && period.fiscal_period !== "FY" ? period.fiscal_period : "";
  return quarter ? `${quarter} FY${period.fiscal_year}` : `FY${period.fiscal_year}`;
}

const MINUS = "−";

function scaled(abs: number): string {
  if (abs >= 1e12) return `${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(abs / 1e6).toFixed(1)}M`;
  return abs.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

/** The table's short form: "$57.01B", "−$812.3M", "73.5%", "$2.34", "24.30B". */
export function formatValue(value: number, unit: MetricUnit): string {
  const sign = value < 0 ? MINUS : "";
  const abs = Math.abs(value);
  switch (unit) {
    case "ratio":
      return `${sign}${(abs * 100).toFixed(1)}%`;
    case "USD/shares":
      return `${sign}$${abs.toFixed(2)}`;
    case "shares":
      return `${sign}${scaled(abs)}`;
    case "USD":
      return `${sign}$${scaled(abs)}`;
  }
}

/** The figure as filed: "$57,006,000,000", "24,300,000,000 shares", "$2.34 per share". */
export function exactValue(value: number, unit: MetricUnit): string {
  const sign = value < 0 ? MINUS : "";
  const abs = Math.abs(value);
  switch (unit) {
    case "ratio":
      return `${sign}${(abs * 100).toFixed(2)}%`;
    case "USD/shares":
      return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 4 })} per share`;
    case "shares":
      return `${sign}${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })} shares`;
    case "USD":
      return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  }
}

/** How a value was arrived at, in plain words, for the hover text. */
export const METHOD_LABEL: Record<MetricValue["method"], string> = {
  reported: "As filed.",
  subtracted:
    "Not filed for this quarter on its own: the longer period filed minus the part before it " +
    "(for a fourth quarter, the fiscal year minus the first nine months).",
  computed: "Worked out from the filed values below."
};

/** The filing a number links to: its only source, or the most recently filed one. */
export function primarySource(value: MetricValue): SourceFact | null {
  return value.sources.reduce<SourceFact | null>(
    (latest, source) =>
      latest === null || source.filed_date > latest.filed_date ? source : latest,
    null
  );
}

/** The SEC forms each source came from, e.g. "10-Q filed Nov 19, 2026". */
export function sourceLine(source: SourceFact): string {
  const span = source.period_start
    ? `${dayLabel(source.period_start)} – ${dayLabel(source.period_end)}`
    : `at ${dayLabel(source.period_end)}`;
  return `${source.concept} · ${span} · ${source.form ?? "filing"} filed ${dayLabel(source.filed_date)}`;
}

/** Why a cell is empty. Fourth-quarter EPS is a known gap (ADR 0020); otherwise nothing was filed. */
export function missingReason(metric: MetricInfo, period: FinancialPeriod, basis: Basis): string {
  if (basis === "quarterly" && metric.unit === "USD/shares" && period.fiscal_period === "Q4") {
    return (
      "Only the full year was filed. Per-share figures can't be split into a fourth quarter, " +
      "because the share count differs between the parts."
    );
  }
  return "Not in the company's XBRL for this period.";
}
