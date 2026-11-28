// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { priceHistoryLabel, type Instrument } from "../api/instruments";
import { activeNavKey } from "../components/TopBar";
import { installDomStubs } from "../test/dom";
import { COLUMN_HELP, STATUS_HELP, STUCK_HELP, WatchlistPage } from "./WatchlistPage";

function renderPage() {
  return render(
    <MemoryRouter>
      <WatchlistPage />
    </MemoryRouter>
  );
}

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const base: Omit<Instrument, "symbol"> = {
  name: null,
  asset_type: "equity",
  role: "watchlist",
  cik: null,
  filer_type: null,
  onboarding_status: "pending",
  onboarding_error: null,
  last_filing: null,
  prices: null
};

const nvda: Instrument = {
  ...base,
  symbol: "NVDA",
  name: "NVIDIA CORP",
  cik: "0001045810",
  filer_type: "domestic",
  onboarding_status: "ready",
  last_filing: { form_type: "8-K", accepted_at: "2026-11-20T21:05:00Z" },
  prices: { first: "2024-11-26", last: "2026-11-25", bars: 501 }
};
const nbis: Instrument = {
  ...nvda,
  symbol: "NBIS",
  name: "Nebius Group N.V.",
  cik: "0001513845",
  filer_type: "foreign_private",
  onboarding_status: "backfilling",
  onboarding_error: "xbrl: SEC request failed: HTTP 503"
};
const xyzq: Instrument = {
  ...base,
  symbol: "XYZQ",
  onboarding_status: "failed",
  onboarding_error: "XYZQ is not in SEC's ticker map"
};
const qqq: Instrument = {
  ...base,
  symbol: "QQQ",
  name: "Invesco QQQ Trust, Series 1",
  asset_type: "etf",
  role: "benchmark",
  onboarding_status: "ready",
  prices: { first: "2024-11-26", last: "2026-11-25", bars: 501 }
};

type Reply = { status: number; body?: unknown };

function mockApi(lists: Instrument[][], replies: Record<string, Reply> = {}) {
  let call = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/instruments" && method === "GET") {
      const list = lists[Math.min(call, lists.length - 1)];
      call += 1;
      return new Response(JSON.stringify(list), { status: 200 });
    }
    const reply = replies[`${method} ${url}`];
    if (reply) {
      return new Response(reply.body === undefined ? "" : JSON.stringify(reply.body), {
        status: reply.status
      });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function calls(fetchMock: ReturnType<typeof mockApi>, method: string): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => ((init as RequestInit | undefined)?.method ?? "GET") === method)
    .map(([input]) => String(input));
}

describe("watchlist helpers", () => {
  it("labels price coverage with bar count and trading-day range", () => {
    expect(priceHistoryLabel(qqq.prices)).toBe("501 daily bars · Nov 26, 2024 – Nov 25, 2026");
    expect(priceHistoryLabel({ first: "2026-11-25", last: "2026-11-25", bars: 1 })).toBe(
      "1 daily bar · Nov 25, 2026 – Nov 25, 2026"
    );
    expect(priceHistoryLabel(null)).toBeNull();
  });

  it("puts Watchlist between Alerts and Tasks", () => {
    expect(activeNavKey("/watchlist")).toBe("/watchlist");
  });
});

describe("WatchlistPage", () => {
  it("lists tracked tickers with status, filer type and last filing, then benchmarks", async () => {
    mockApi([[nbis, nvda, xyzq, qqq]]);
    renderPage();

    const row = (await screen.findByText("NVIDIA CORP")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Domestic")).toBeTruthy();
    expect(within(row).getByText("0001045810")).toBeTruthy();
    expect(within(row).getByText("Ready")).toBeTruthy();
    expect(within(row).getByText(/8-K ·/)).toBeTruthy();
    expect(within(row).queryByRole("button", { name: "Retry" })).toBeNull();
    // A ticker SEC has tied to a company opens its ticker page; one it rejected is plain text.
    expect(within(row).getByRole("link", { name: "NVDA" }).getAttribute("href")).toBe(
      "/watchlist/NVDA"
    );

    const failed = screen.getByText("XYZQ").closest("tr") as HTMLElement;
    expect(within(failed).getByText("Failed")).toBeTruthy();
    expect(within(failed).getByText("XYZQ is not in SEC's ticker map")).toBeTruthy();
    expect(within(failed).getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(within(failed).queryByRole("link", { name: "XYZQ" })).toBeNull();

    const stuck = screen.getByText("NBIS").closest("tr") as HTMLElement;
    expect(within(stuck).getByText("Backfilling")).toBeTruthy();
    expect(within(stuck).getByText("Foreign private")).toBeTruthy();
    expect(within(stuck).getByRole("button", { name: "Retry" })).toBeTruthy();

    const benchmark = screen.getByText("QQQ").closest("tr") as HTMLElement;
    expect(within(benchmark).getByText("Invesco QQQ Trust, Series 1")).toBeTruthy();
    expect(
      within(benchmark).getByText("501 daily bars · Nov 26, 2024 – Nov 25, 2026")
    ).toBeTruthy();
    expect(within(benchmark).queryByRole("button", { name: "Remove" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Watchlist · 3" })).toBeTruthy();
  });

  it("explains the columns and what each status means and leads to", async () => {
    mockApi([[nbis, nvda, xyzq, qqq]]);
    renderPage();
    await screen.findByText("NVIDIA CORP");

    fireEvent.mouseEnter(screen.getByLabelText("About CIK"));
    expect(await screen.findByText(COLUMN_HELP.cik)).toBeTruthy();
    fireEvent.mouseEnter(screen.getByLabelText("About Onboarding"));
    expect(await screen.findByText(COLUMN_HELP.onboarding)).toBeTruthy();
    expect(COLUMN_HELP.onboarding).toContain("Pending → Backfilling → Ready");

    const ready = screen.getByText("NVIDIA CORP").closest("tr") as HTMLElement;
    fireEvent.mouseEnter(within(ready).getByText("Ready"));
    expect(await screen.findByText(STATUS_HELP.ready)).toBeTruthy();

    // Backfilling with an error is stuck until a retry, so it says so rather than "Next: Ready".
    const stuck = screen.getByText("NBIS").closest("tr") as HTMLElement;
    fireEvent.mouseEnter(within(stuck).getByText("Backfilling"));
    expect(await screen.findByText(STUCK_HELP)).toBeTruthy();
  });

  it("adds a ticker, uppercased, and shows it pending", async () => {
    const added: Instrument = { ...base, symbol: "MSFT" };
    const fetchMock = mockApi([[nvda], [nvda, added]], {
      "POST /api/instruments": {
        status: 202,
        body: { symbol: "MSFT", onboarding_status: "pending", run_id: 7 }
      }
    });
    renderPage();
    await screen.findByText("NVIDIA CORP");

    fireEvent.change(screen.getByLabelText("Ticker to add"), { target: { value: " msft " } });
    fireEvent.click(screen.getByRole("button", { name: "Add ticker" }));

    const row = (await screen.findByText("MSFT")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Pending")).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ symbol: "MSFT" });
    expect((screen.getByLabelText("Ticker to add") as HTMLInputElement).value).toBe("");
  });

  it.each([
    ["nvda", 409, "NVDA is already on the watchlist"],
    ["xyzq", 422, "XYZQ: not found on any US exchange"]
  ])("shows why %s cannot be added next to the input", async (typed, status, detail) => {
    const fetchMock = mockApi([[nvda]], {
      "POST /api/instruments": { status, body: { detail } }
    });
    renderPage();
    await screen.findByText("NVIDIA CORP");

    fireEvent.change(screen.getByLabelText("Ticker to add"), { target: { value: typed } });
    fireEvent.click(screen.getByRole("button", { name: "Add ticker" }));

    expect(await screen.findByText(detail)).toBeTruthy();
    expect(calls(fetchMock, "GET")).toHaveLength(1); // the list is not reloaded: nothing was added
  });

  it("retries a failed ticker and removes one after confirming", async () => {
    const fetchMock = mockApi([[nvda, xyzq]], {
      "POST /api/instruments/XYZQ/onboard": {
        status: 202,
        body: { symbol: "XYZQ", onboarding_status: "pending", run_id: 8 }
      },
      "DELETE /api/instruments/NVDA": { status: 204 }
    });
    renderPage();
    await screen.findByText("NVIDIA CORP");

    const failed = screen.getByText("XYZQ").closest("tr") as HTMLElement;
    fireEvent.click(within(failed).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(calls(fetchMock, "POST")).toEqual(["/api/instruments/XYZQ/onboard"])
    );

    const row = screen.getByText("NVIDIA CORP").closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(calls(fetchMock, "DELETE")).toEqual([]); // nothing until confirmed
    const confirm = (await screen.findByText("Remove NVDA?")).closest(
      ".ant-popover"
    ) as HTMLElement;
    expect(within(confirm).getByText(/filings, prices and alerts are kept/)).toBeTruthy();
    fireEvent.click(within(confirm).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls(fetchMock, "DELETE")).toEqual(["/api/instruments/NVDA"]));
  });

  it("refreshes while a ticker is onboarding and stops once all are settled", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const pending: Instrument = { ...base, symbol: "MSFT" };
    const fetchMock = mockApi([[pending], [{ ...pending, onboarding_status: "ready" }]]);
    renderPage();
    await screen.findByText("Pending");

    await vi.advanceTimersByTimeAsync(5_000);
    expect(await screen.findByText("Ready")).toBeTruthy();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(calls(fetchMock, "GET")).toHaveLength(2);
  });

  it("shows an empty state and the load error with Retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "database unavailable" }), { status: 503 })
      )
    );
    renderPage();
    expect(await screen.findByText("database unavailable")).toBeTruthy();

    cleanup();
    mockApi([[qqq]]);
    renderPage();
    expect(await screen.findByText("No tickers yet. Add one above.")).toBeTruthy();
  });
});
