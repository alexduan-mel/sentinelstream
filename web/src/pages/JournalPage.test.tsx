// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import dayjs from "dayjs";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { FilingDetail } from "../api/filings";
import type { Instrument } from "../api/instruments";
import { tradeLabel, type Journal, type JournalEntry } from "../api/journal";
import { installDomStubs } from "../test/dom";
import { JournalPage } from "./JournalPage";
import { LogDecisionPage, tradeDayDisabled } from "./LogDecisionPage";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const EARNINGS = "0001045810-26-000101";

const opened: JournalEntry = {
  id: 1,
  symbol: "NVDA",
  name: "NVIDIA CORP",
  action: "opened",
  thesis: "Gross margin holds.",
  source: {
    accession_no: EARNINGS,
    form_type: "8-K",
    accepted_at: "2026-11-19T21:20:00Z",
    url: "https://www.sec.gov/x-index.htm"
  },
  original_thesis: "Margins hold.",
  original_source: {
    accession_no: EARNINGS,
    form_type: "8-K",
    accepted_at: "2026-11-19T21:20:00Z",
    url: "https://www.sec.gov/x-index.htm"
  },
  traded_on: "2026-11-20",
  price: 185.25,
  shares: 12.5,
  known_at: "2026-11-28T15:00:00Z",
  recorded_by: "admin",
  withdrawn: null,
  corrections: [
    {
      kind: "thesis",
      thesis: "Gross margin holds.",
      source: null,
      reason: "Typo",
      known_at: "2026-11-28T15:05:00Z",
      recorded_by: "admin"
    }
  ],
  score: null
};

const passed: JournalEntry = {
  ...opened,
  id: 2,
  symbol: "AMZN",
  name: "AMAZON COM INC",
  action: "pass",
  thesis: "Capex too high for now.",
  original_thesis: "Capex too high for now.",
  source: null,
  original_source: null,
  traded_on: null,
  price: null,
  shares: null,
  withdrawn: {
    kind: "withdrawn",
    thesis: null,
    source: null,
    reason: "Logged the wrong ticker",
    known_at: "2026-11-28T16:00:00Z",
    recorded_by: "admin"
  },
  corrections: []
};

const combined = {
  scored: 0,
  fitted: 0,
  too_early: 0,
  alpha_of_calls: null,
  calls_right: 0,
  beta_of_holdings: null,
  holdings_days: 0
};

const emptyMoney = { positions: [], bought: 0, profit: 0, qqq_profit: 0, excluded: 0 };

const journal: Journal = {
  entries: [passed, opened],
  open_positions: ["NVDA"],
  tickers: ["AMZN", "NVDA"],
  combined,
  money: emptyMoney
};

function instrument(symbol: string, extra: Partial<Instrument> = {}): Instrument {
  return {
    symbol,
    name: `${symbol} INC`,
    asset_type: "equity",
    role: "watchlist",
    cik: "0001045810",
    filer_type: "domestic",
    onboarding_status: "ready",
    onboarding_error: null,
    last_filing: null,
    prices: null,
    ...extra
  };
}

type Route = { method?: string; path: string; status?: number; body: unknown };

function mockApi(routes: Route[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const route = routes.find((r) => (r.method ?? "GET") === method && url === r.path);
    if (!route)
      return new Response(JSON.stringify({ detail: `no route ${method} ${url}` }), { status: 500 });
    return new Response(JSON.stringify(route.body), { status: route.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function posted(fetchMock: ReturnType<typeof mockApi>, path: string): unknown {
  const call = fetchMock.mock.calls.find(
    ([input, init]) => String(input) === path && (init as RequestInit)?.method === "POST"
  );
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : undefined;
}

function Address() {
  const { pathname, search } = useLocation();
  return <div data-testid="address">{`${pathname}${search}`}</div>;
}

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/journal" element={<JournalPage />} />
        <Route path="/journal/new" element={<LogDecisionPage />} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
      <Address />
    </MemoryRouter>
  );
}

describe("tradeLabel and trade days", () => {
  it("formats a trade and leaves out what was not given", () => {
    expect(tradeLabel(opened)).toBe("Nov 20, 2026 · $185.25 · 12.5 sh");
    expect(tradeLabel({ traded_on: null, price: null, shares: 10 })).toBe("10 sh");
    expect(tradeLabel(passed)).toBeNull();
  });

  it("refuses future days and weekends", () => {
    expect(tradeDayDisabled(dayjs("2026-11-20"), "2026-11-28")).toBe(false); // Friday
    expect(tradeDayDisabled(dayjs("2026-11-21"), "2026-11-28")).toBe(true); // Saturday
    expect(tradeDayDisabled(dayjs("2026-11-30"), "2026-11-28")).toBe(true); // future
  });
});

describe("JournalPage", () => {
  it("lists entries with action, thesis, trade and alert, and open positions", async () => {
    mockApi([{ path: "/api/journal", body: journal }]);
    renderAt("/journal");

    const row = (await screen.findByText("Gross margin holds.")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Opened")).toBeTruthy();
    expect(within(row).getByText("Corrected")).toBeTruthy();
    expect(within(row).getByText("Nov 20, 2026 · $185.25 · 12.5 sh")).toBeTruthy();
    expect(within(row).getByRole("link", { name: "8-K · Nov 19, 2026" }).getAttribute("href")).toBe(
      `/alerts/${EARNINGS}`
    );
    expect(within(row).getByRole("link", { name: "NVDA" }).getAttribute("href")).toBe(
      "/watchlist/NVDA"
    );

    const withdrawn = screen.getByText("Capex too high for now.").closest("tr") as HTMLElement;
    expect(within(withdrawn).getByText("Withdrawn")).toBeTruthy();
    expect(within(withdrawn).getByText("Not a trade")).toBeTruthy();

    expect(screen.getByText("Open positions: NVDA")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Journal · 2" })).toBeTruthy();
  });

  it("filters by ticker from the address, on the server", async () => {
    mockApi([{ path: "/api/journal?symbol=AMZN", body: { ...journal, entries: [passed] } }]);
    renderAt("/journal?ticker=AMZN");

    expect(await screen.findByText("Capex too high for now.")).toBeTruthy();
    expect(screen.queryByText("Gross margin holds.")).toBeNull();
    expect(screen.getByRole("link", { name: "Log a decision" }).getAttribute("href")).toBe(
      "/journal/new?ticker=AMZN"
    );
  });

  it("shows the original under Details and adds a correction with a reason", async () => {
    const fetchMock = mockApi([
      { path: "/api/journal", body: journal },
      { method: "POST", path: "/api/journal/1/corrections", status: 201, body: opened }
    ]);
    renderAt("/journal");

    const row = (await screen.findByText("Gross margin holds.")).closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByText("Details"));
    expect(await screen.findByText("As first logged")).toBeTruthy();
    expect(screen.getByText("Margins hold.")).toBeTruthy();
    expect(screen.getByText("Thesis corrected")).toBeTruthy();

    fireEvent.click(screen.getByText("Withdraw"));
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(await screen.findByText("Give a reason; it is kept with the correction")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "Wrong ticker" } });
    fireEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    await waitFor(() =>
      expect(posted(fetchMock, "/api/journal/1/corrections")).toEqual({
        kind: "withdrawn",
        reason: "Wrong ticker",
        thesis: null,
        accession_no: null
      })
    );
  });

  it("has an empty state and an error state", async () => {
    mockApi([
      {
        path: "/api/journal",
        body: { entries: [], open_positions: [], tickers: [], combined, money: emptyMoney }
      }
    ]);
    renderAt("/journal");
    expect(await screen.findByText("No decisions logged yet")).toBeTruthy();
    expect(screen.getByText("No open positions")).toBeTruthy();
    cleanup();

    mockApi([{ path: "/api/journal", status: 500, body: { detail: "Database unavailable" } }]);
    renderAt("/journal");
    expect(await screen.findByText("Database unavailable")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});

const filing = {
  accession_no: EARNINGS,
  cik: "0001045810",
  tickers: ["NVDA"],
  form_type: "8-K",
  items: [{ number: "2.02", title: "Results of Operations and Financial Condition" }],
  accepted_at: "2026-11-19T21:20:00Z"
} as unknown as FilingDetail;

describe("LogDecisionPage", () => {
  const instruments = [
    instrument("NVDA"),
    instrument("AMZN"),
    instrument("SPCX", { onboarding_status: "pending" }),
    instrument("QQQ", { asset_type: "etf", role: "benchmark" })
  ];

  it("logs a decision from an alert, with only the actions the position allows", async () => {
    const fetchMock = mockApi([
      { path: "/api/instruments", body: instruments },
      { path: "/api/journal", body: journal },
      { path: `/api/filings/${EARNINGS}`, body: filing },
      { method: "POST", path: "/api/journal", status: 201, body: opened }
    ]);
    renderAt(`/journal/new?ticker=NVDA&alert=${EARNINGS}`);

    expect(await screen.findByText("NVDA has an open position in the Journal")).toBeTruthy();
    expect(
      screen.getByRole("link", {
        name: "8-K · Item 2.02 · Results of Operations and Financial Condition"
      })
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "← Alert" }).getAttribute("href")).toBe(
      `/alerts/${EARNINGS}`
    );
    expect((screen.getByRole("radio", { name: "Opened" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("radio", { name: "Trimmed" }) as HTMLInputElement).disabled).toBe(
      false
    );

    fireEvent.click(screen.getByRole("button", { name: "Log decision" }));
    expect(await screen.findByText("Pick what you did")).toBeTruthy();

    fireEvent.click(screen.getByRole("radio", { name: "Added" }));
    expect(screen.getByText("Bought more of a position already held")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Thesis"), { target: { value: "Supply is easing." } });
    fireEvent.click(screen.getByRole("button", { name: "Log decision" }));

    await waitFor(() =>
      expect(screen.getByTestId("address").textContent).toBe("/journal?ticker=NVDA")
    );
    expect(posted(fetchMock, "/api/journal")).toEqual({
      symbol: "NVDA",
      action: "added",
      thesis: "Supply is easing.",
      accession_no: EARNINGS,
      traded_on: null,
      price: null,
      shares: null
    });
  });

  it("leaves out the alert when unlinked, hides trade details for a Pass, and shows refusals", async () => {
    const fetchMock = mockApi([
      { path: "/api/instruments", body: instruments },
      { path: "/api/journal", body: journal },
      { path: `/api/filings/${EARNINGS}`, body: filing },
      {
        method: "POST",
        path: "/api/journal",
        status: 409,
        body: { detail: "NVDA's latest entry is for 30 Nov 2026" }
      }
    ]);
    renderAt(`/journal/new?ticker=NVDA&alert=${EARNINGS}`);

    fireEvent.click(await screen.findByText("Don't link"));
    expect(screen.getByText("Not linked to an alert")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Trimmed" }));
    expect(screen.getByText("Trade details (optional)")).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Pass" }));
    expect(screen.queryByText("Trade details (optional)")).toBeNull();

    fireEvent.change(screen.getByLabelText("Thesis"), {
      target: { value: "Waiting for the 10-K." }
    });
    fireEvent.click(screen.getByRole("button", { name: "Log decision" }));

    expect(await screen.findByText("NVDA's latest entry is for 30 Nov 2026")).toBeTruthy();
    expect(posted(fetchMock, "/api/journal")).toMatchObject({ action: "pass", accession_no: null });
  });

  it("offers only ready watchlist companies and says when an alert is not for the ticker", async () => {
    mockApi([
      { path: "/api/instruments", body: instruments },
      { path: "/api/journal", body: journal },
      { path: `/api/filings/${EARNINGS}`, body: filing }
    ]);
    renderAt(`/journal/new?ticker=AMZN&alert=${EARNINGS}`);

    expect(
      await screen.findByText("This alert is not for AMZN, so the entry won't be linked to it")
    ).toBeTruthy();
    expect(screen.getByText("AMZN has no open position in the Journal")).toBeTruthy();
    expect((screen.getByRole("radio", { name: "Added" }) as HTMLInputElement).disabled).toBe(true);

    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Ticker" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["NVDA", "AMZN"]);
  });
});
