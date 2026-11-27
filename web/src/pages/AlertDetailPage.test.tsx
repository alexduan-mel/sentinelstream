// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  form4CodeLabel,
  type BaseRate,
  type BaseRateStats,
  type FilingDetail
} from "../api/filings";
import { installDomStubs } from "../test/dom";
import { AlertDetailPage } from "./AlertDetailPage";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stats(n: number, mean: number, extra: Partial<BaseRateStats> = {}): BaseRateStats {
  return {
    n,
    mean,
    median: mean / 2,
    hit_rate: 0.55,
    ci_low: -0.004,
    ci_high: 0.021,
    months: 22,
    mean_gross_return: mean + 0.01,
    mean_benchmark_return: 0.01,
    mean_cost: 0.002,
    mean_excess_vs_reference: mean + 0.001,
    ended_early: 3,
    ...extra
  };
}

const insiderBaseRate: BaseRate = {
  event_kind: "insider_purchase",
  event_key: "",
  label: "Insider open-market purchase",
  scope: "market",
  scope_label:
    "Based on past filings from all US-listed companies (fewer than 30 in this company's industry, so pooled)",
  as_of: "2026-11-27T11:00:00Z",
  variants: {
    main: {
      "1": stats(2309, 0.0012),
      "5": stats(2243, -0.004),
      "20": stats(1977, 0.0085),
      "60": stats(1276, 0.011, { ci_low: null, ci_high: null, months: 4 })
    },
    none: { "1": stats(9000, 0.02), "5": null, "20": stats(8000, 0.031), "60": null }
  }
};

const purchase: FilingDetail = {
  accession_no: "0001018724-26-000002",
  cik: "0001018724",
  tickers: ["AMZN"],
  form_type: "4",
  items: [],
  accepted_at: "2026-11-21T04:30:00Z",
  filed_date: "2026-11-20",
  sec_url: "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000002/x-index.htm",
  materiality: "high",
  materiality_reason: "Open-market purchase by Jassy Andrew R (Director)",
  purchase_shares: 1000,
  slack: { status: "delivered", delivered_at: "2026-11-21T04:34:10Z", detail: null },
  report_date: "2026-11-19",
  primary_document: "xslF345X05/form4.xml",
  primary_document_url:
    "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000002/xslF345X05/form4.xml",
  first_seen_at: "2026-11-21T04:33:00Z",
  detection_delay_seconds: 180,
  slack_attempts: 1,
  alert_latency_seconds: 250,
  issuers: [
    {
      ticker: "AMZN",
      cik: "0001018724",
      materiality: "high",
      materiality_reason: "Open-market purchase by Jassy Andrew R (Director)",
      is_backfill: false,
      first_seen_at: "2026-11-21T04:33:00Z"
    }
  ],
  form4_transactions: [
    {
      line_no: 1,
      reporting_owners: "Jassy Andrew R",
      owner_roles: "Director",
      security_title: "Common Stock",
      transaction_date: "2026-11-19",
      transaction_code: "P",
      acquired_disposed: "A",
      shares: 1000,
      price_per_share: 181.25,
      shares_owned_after: 12400,
      direct_indirect: "D"
    }
  ],
  base_rates: [insiderBaseRate]
};

function renderAt(accession: string, body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter initialEntries={[`/alerts/${accession}`]}>
      <Routes>
        <Route path="/" element={<div>alerts list</div>} />
        <Route path="/alerts/:accessionNo" element={<AlertDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
  return fetchMock;
}

describe("AlertDetailPage", () => {
  it("shows what the filing is, how fast it was seen and pushed, and its Form 4 rows", async () => {
    const fetchMock = renderAt(purchase.accession_no, purchase);

    expect(await screen.findByRole("heading", { name: "AMZN · Form 4" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/api/filings/0001018724-26-000002", expect.anything());
    expect(screen.getByText("Open-market purchase · 1,000 sh")).toBeTruthy();
    expect(
      screen.getByText("Flagged: Open-market purchase by Jassy Andrew R (Director)")
    ).toBeTruthy();
    expect(screen.getByText("3m 0s")).toBeTruthy(); // detection delay
    expect(screen.getByText("4m 10s")).toBeTruthy(); // acceptance to Slack
    expect(screen.getByRole("link", { name: "xslF345X05/form4.xml ↗" }).getAttribute("href")).toBe(
      purchase.primary_document_url
    );

    const row = screen.getByText("Jassy Andrew R").closest("tr") as HTMLElement;
    expect(within(row).getByText("P · Open-market or private purchase")).toBeTruthy();
    expect(within(row).getByText("$181.25")).toBeTruthy();
    expect(within(row).getByText("12,400")).toBeTruthy();
    expect(within(row).getByText("Direct")).toBeTruthy();
  });

  it("marks history, which has no detection delay, and hides an empty Form 4 section", async () => {
    renderAt("0001045810-26-000001", {
      ...purchase,
      accession_no: "0001045810-26-000001",
      tickers: ["NVDA"],
      form_type: "8-K",
      items: [{ number: "2.02", title: "Results of Operations and Financial Condition" }],
      purchase_shares: null,
      slack: null,
      detection_delay_seconds: null,
      alert_latency_seconds: null,
      slack_attempts: null,
      issuers: [{ ...purchase.issuers[0], ticker: "NVDA", is_backfill: true }],
      form4_transactions: []
    });

    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
    expect(
      screen.getByText("Item 2.02 · Results of Operations and Financial Condition")
    ).toBeTruthy();
    expect(screen.getAllByText("History").length).toBe(2); // detection delay and the issuer row
    expect(screen.queryByText("Form 4 transactions")).toBeNull();
  });

  it("shows a missing filing as an error with Retry and links back to Alerts", async () => {
    renderAt("0001045810-26-999999", { detail: "No stored filing 0001045810-26-999999" }, 404);

    expect(await screen.findByText("No stored filing 0001045810-26-999999")).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "← Alerts" }));
    await waitFor(() => expect(screen.getByText("alerts list")).toBeTruthy());
  });

  it("shows what followed similar filings, with gains and losses coloured, for both filter settings", async () => {
    renderAt(purchase.accession_no, purchase);

    expect(await screen.findByText("What followed similar filings")).toBeTruthy();
    expect(screen.getByText("Insider open-market purchase")).toBeTruthy();
    expect(
      screen.getByText(/Based on past filings from all US-listed companies \(fewer than 30/)
    ).toBeTruthy();
    const twenty = screen.getByText("20 sessions").closest("tr") as HTMLElement;
    expect(within(twenty).getByText("1,977")).toBeTruthy();
    expect(within(twenty).getByText("+0.9%").className).toContain("text-positive");
    expect(within(twenty).getByText("55%")).toBeTruthy();
    expect(within(twenty).getByText("-0.4%").className).toContain("text-negative"); // range low
    expect(within(twenty).getByText("+2.1%")).toBeTruthy();
    expect(within(twenty).getByText("-0.2%")).toBeTruthy(); // average cost
    const five = screen.getByText("5 sessions").closest("tr") as HTMLElement;
    expect(within(five).getAllByText("-0.4%")[0].className).toContain("text-negative");
    // Too few months for a range: a dash, explained on hover.
    const sixty = screen.getByText("60 sessions").closest("tr") as HTMLElement;
    expect(within(sixty).queryByText(/to/)).toBeNull();
    expect(screen.getByText(/market's own moves are taken out/)).toBeTruthy();
    expect(screen.getByText(/not a forecast/)).toBeTruthy();

    fireEvent.click(screen.getByText("All stocks"));
    const all = screen.getByText("20 sessions").closest("tr") as HTMLElement;
    expect(within(all).getByText("8,000")).toBeTruthy();
    expect(within(all).getByText("+3.1%")).toBeTruthy();
  });

  it("says when a high filing has no base rate yet, and shows none for a low filing", async () => {
    renderAt(purchase.accession_no, { ...purchase, base_rates: [] });
    expect(await screen.findByText(/No base rate yet/)).toBeTruthy();
    cleanup();

    renderAt(purchase.accession_no, { ...purchase, materiality: "low", base_rates: [] });
    expect(await screen.findByRole("heading", { name: "AMZN · Form 4" })).toBeTruthy();
    expect(screen.queryByText("What followed similar filings")).toBeNull();
  });

  it("labels Form 4 codes from SEC's list", () => {
    expect(form4CodeLabel("S")).toBe("S · Open-market or private sale");
    expect(form4CodeLabel("Q")).toBe("Q");
  });
});
