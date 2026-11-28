// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  exactValue,
  formatValue,
  periodLabel,
  primarySource,
  type FinancialPeriod,
  type KeyFinancials,
  type MetricInfo,
  type MetricValue
} from "../api/financials";
import type { TenKChanges } from "../api/tenkChanges";
import { installDomStubs } from "../test/dom";
import { TickerPage } from "./TickerPage";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const TEN_Q = "0001045810-26-000100";
const TEN_K = "0001045810-26-000010";
const index = (accession: string) =>
  `https://www.sec.gov/Archives/edgar/data/1045810/${accession.split("-").join("")}/${accession}-index.htm`;

const metrics: MetricInfo[] = [
  { key: "revenue", label: "Revenue", unit: "USD", definition: "Sales for the period." },
  {
    key: "gross_margin",
    label: "Gross margin",
    unit: "ratio",
    definition: "Gross profit ÷ revenue."
  },
  { key: "diluted_eps", label: "Diluted EPS", unit: "USD/shares", definition: "Per share." }
];

function reported(
  value: number,
  accession: string,
  filed: string,
  concept = "Revenues"
): MetricValue {
  return {
    value,
    method: "reported",
    sources: [
      {
        concept,
        accession_no: accession,
        form: accession === TEN_K ? "10-K" : "10-Q",
        filed_date: filed,
        period_start: "2026-07-27",
        period_end: "2026-10-26",
        value,
        url: index(accession)
      }
    ]
  };
}

const q3: FinancialPeriod = {
  start: "2026-07-27",
  end: "2026-10-26",
  fiscal_year: 2027,
  fiscal_period: "Q3",
  values: {
    revenue: reported(57006000000, TEN_Q, "2026-11-19"),
    gross_margin: {
      value: 0.735,
      method: "computed",
      sources: [
        reported(41900000000, TEN_Q, "2026-11-19", "GrossProfit").sources[0],
        reported(57006000000, TEN_Q, "2026-11-19").sources[0]
      ]
    },
    diluted_eps: reported(1.3, TEN_Q, "2026-11-19", "EarningsPerShareDiluted")
  }
};

const q4: FinancialPeriod = {
  start: "2026-01-27",
  end: "2026-04-26",
  fiscal_year: 2026,
  fiscal_period: "Q4",
  values: {
    revenue: {
      value: 39331000000,
      method: "subtracted",
      sources: [
        { ...reported(130497000000, TEN_K, "2026-02-26").sources[0], period_start: "2025-01-27" },
        { ...reported(91166000000, "0001045810-25-000230", "2025-11-20").sources[0] }
      ]
    },
    gross_margin: null,
    diluted_eps: null
  }
};

function financials(extra: Partial<KeyFinancials> = {}): KeyFinancials {
  return {
    symbol: "NVDA",
    cik: "0001045810",
    name: "NVIDIA CORP",
    filer_type: "domestic",
    basis: "quarterly",
    as_of: "2026-11-28T10:00:00Z",
    metrics,
    periods: [q3, q4],
    ...extra
  };
}

/** No 10-K comparison yet: the 10-K changes section says so in one line. */
function noComparison(url: string): TenKChanges {
  return {
    symbol: "NVDA",
    cik: "0001045810",
    filer_type: "domestic",
    item: url.includes("item=7") ? "7" : "1A",
    item_label: url.includes("item=7") ? "Management's discussion and analysis" : "Risk factors",
    comparison: null
  };
}

function mockApi(
  answer: (url: string) => KeyFinancials | Response,
  tenk: (url: string) => TenKChanges | Response = noComparison
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const result = url.includes("/tenk-changes") ? tenk(url) : answer(url);
    return result instanceof Response
      ? result
      : new Response(JSON.stringify(result), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The key-financials requests made, in order. */
function financialCalls(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls
    .map(([input]) => String(input))
    .filter((url) => url.includes("/financials"));
}

function Search() {
  return <div data-testid="search">{useLocation().search}</div>;
}

function renderPage(path = "/watchlist/NVDA") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/watchlist/:symbol"
          element={
            <>
              <TickerPage />
              <Search />
            </>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

describe("formatting", () => {
  it("shortens large figures and keeps the exact one for hover", () => {
    expect(formatValue(57006000000, "USD")).toBe("$57.01B");
    expect(formatValue(-812300000, "USD")).toBe("−$812.3M");
    expect(formatValue(0.7352, "ratio")).toBe("73.5%");
    expect(formatValue(1.3, "USD/shares")).toBe("$1.30");
    expect(formatValue(24300000000, "shares")).toBe("24.30B");
    expect(exactValue(57006000000, "USD")).toBe("$57,006,000,000");
    expect(exactValue(24300000000, "shares")).toBe("24,300,000,000 shares");
  });

  it("labels periods with the filing's fiscal labels", () => {
    expect(periodLabel(q3, "quarterly")).toBe("Q3 FY2027");
    expect(periodLabel({ ...q3, fiscal_period: "FY" }, "annual")).toBe("FY2027");
    expect(periodLabel({ ...q3, fiscal_year: null }, "quarterly")).toBe("Ended Oct 26, 2026");
  });

  it("links a worked-out value to its most recently filed source", () => {
    expect(primarySource(q4.values.revenue!)?.accession_no).toBe(TEN_K);
  });
});

describe("TickerPage", () => {
  it("shows each metric by quarter, every number linked to its filing", async () => {
    const fetchMock = mockApi(() => financials());
    renderPage();

    const revenue = await screen.findByRole("link", { name: "$57.01B" });
    expect(revenue.getAttribute("href")).toBe(index(TEN_Q));
    expect(screen.getByRole("link", { name: "$39.33B" }).getAttribute("href")).toBe(index(TEN_K));
    expect(screen.getByRole("link", { name: "73.5%" })).toBeTruthy();
    expect(screen.getAllByText("Q3 FY2027").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Ended Oct 26, 2026").length).toBeGreaterThan(0);
    expect(screen.getByText("NVIDIA CORP · Domestic filer · CIK 0001045810")).toBeTruthy();
    expect(financialCalls(fetchMock)[0]).toBe(
      "/api/instruments/NVDA/financials?basis=quarterly&periods=8"
    );

    // Hovering shows the exact figure and where it came from.
    fireEvent.mouseEnter(revenue);
    const tooltip = await screen.findByRole("tooltip");
    expect(within(tooltip).getByText("$57,006,000,000")).toBeTruthy();
    expect(within(tooltip).getByText("As filed.")).toBeTruthy();
    expect(
      within(tooltip).getByText("Revenues · Jul 27, 2026 – Oct 26, 2026 · 10-Q filed Nov 19, 2026")
    ).toBeTruthy();
  });

  it("explains empty cells, including fourth-quarter EPS", async () => {
    mockApi(() => financials());
    renderPage();
    await screen.findByRole("link", { name: "$57.01B" });

    const row = screen.getByText("Diluted EPS").closest("tr")!;
    const empty = within(row).getAllByText("—");
    expect(empty).toHaveLength(1);
    fireEvent.mouseEnter(empty[0]);
    expect((await screen.findByRole("tooltip")).textContent).toContain(
      "Per-share figures can't be split into a fourth quarter"
    );
  });

  it("switches to fiscal years and keeps the choice in the address", async () => {
    const fetchMock = mockApi((url) =>
      financials(
        url.includes("basis=annual")
          ? { basis: "annual", periods: [{ ...q3, fiscal_period: "FY", start: "2025-10-27" }] }
          : {}
      )
    );
    renderPage();
    await screen.findAllByText("Q3 FY2027");

    fireEvent.click(screen.getByText("Fiscal years"));
    expect((await screen.findAllByText("FY2027")).length).toBeGreaterThan(0);
    expect(screen.getByTestId("search").textContent).toBe("?basis=annual");
    expect(financialCalls(fetchMock).slice(-1)[0]).toContain("basis=annual");
  });

  it("shows only fiscal years for a foreign private issuer", async () => {
    const fetchMock = mockApi((url) =>
      financials({
        symbol: "NBIS",
        name: "Nebius Group N.V.",
        filer_type: "foreign_private",
        basis: url.includes("basis=annual") ? "annual" : "quarterly",
        periods: url.includes("basis=annual") ? [{ ...q3, fiscal_period: "FY" }] : []
      })
    );
    renderPage("/watchlist/NBIS");

    expect((await screen.findAllByText("FY2027")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Quarters")).toBeNull();
    expect(screen.getByText(/Files a 20-F once a year/)).toBeTruthy();
    expect(financialCalls(fetchMock)).toHaveLength(2);
  });

  it("says so when nothing is filed, and shows API errors with Retry", async () => {
    mockApi(() => financials({ periods: [] }));
    renderPage();
    expect(await screen.findByText("No quarters filed in XBRL yet.")).toBeTruthy();
    cleanup();

    mockApi(
      () =>
        new Response(JSON.stringify({ detail: "'XYZ' is not on the watchlist" }), { status: 404 })
    );
    renderPage("/watchlist/XYZ");
    expect(await screen.findByText("'XYZ' is not on the watchlist")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy());
  });
});
