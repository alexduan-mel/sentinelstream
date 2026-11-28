// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  filingsQuery,
  itemLabel,
  pollLine,
  purchaseLabel,
  type Filing,
  type FilingDetail
} from "../api/filings";
import type { TaskDefinition } from "../api/tasks";
import { activeNavKey } from "../components/TopBar";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { installDomStubs } from "../test/dom";
import { clearDetailCache } from "./AlertDetail";
import { AlertsPage, clearListCache } from "./AlertsPage";

/** Shows the current address. */
function Where() {
  const location = useLocation();
  return (
    <div data-testid="where">
      {location.pathname}
      {location.search}
    </div>
  );
}

/** The page inside a router, set up as the app does: one page for the list and an open filing. */
function renderPage(entry = "/") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route element={<AlertsPage />}>
          <Route path="/" element={null} />
          <Route path="/alerts/:accessionNo" element={null} />
        </Route>
        <Route path="/elsewhere" element={<div>elsewhere</div>} />
      </Routes>
      <Where />
    </MemoryRouter>
  );
}

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  clearListCache();
  clearDetailCache();
});

const earnings: Filing = {
  accession_no: "0001045810-26-000001",
  cik: "0001045810",
  tickers: ["NVDA"],
  form_type: "8-K",
  items: [
    { number: "2.02", title: "Results of Operations and Financial Condition" },
    { number: "9.01", title: "Financial Statements and Exhibits" }
  ],
  accepted_at: "2026-11-20T21:05:00Z",
  filed_date: "2026-11-20",
  sec_url:
    "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/0001045810-26-000001-index.htm",
  materiality: "high",
  materiality_reason: "Item 2.02 Results of Operations and Financial Condition",
  purchase_shares: null,
  slack: {
    status: "skipped",
    delivered_at: null,
    detail: "classified before the Slack notifier was switched on"
  }
};

const form4: Filing = {
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
  purchase_shares: 12400,
  slack: { status: "delivered", delivered_at: "2026-11-21T04:34:10Z", detail: null }
};

const quarterly: Filing = {
  ...form4,
  accession_no: "0001018724-26-000003",
  form_type: "10-Q",
  materiality: "low",
  materiality_reason: "10-Q: not pushed in v0",
  purchase_shares: null,
  slack: null
};

const poller: TaskDefinition = {
  task_id: "ingest_sec_filings",
  label: "SEC filings",
  schedule_label: "Every 5 min",
  trigger_mode: "automatic",
  enabled: true,
  next_run_at: "2026-11-26T22:03:00Z",
  last_run: { id: 9, status: "succeeded", created_at: "2026-11-26T21:58:00Z" }
};

function detailOf(filing: Filing): FilingDetail {
  return {
    ...filing,
    report_date: null,
    primary_document: null,
    primary_document_url: null,
    first_seen_at: filing.accepted_at,
    detection_delay_seconds: null,
    slack_attempts: null,
    alert_latency_seconds: null,
    issuers: [],
    form4_transactions: [],
    base_rates: []
  };
}

function mockApi(
  page: { items: Filing[]; total: number } = { items: [form4, earnings], total: 2 }
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url === "/api/filings/facets")
      return json({ tickers: ["AMZN", "NVDA"], form_types: ["4", "8-K"] });
    if (url.startsWith("/api/filings?")) return json(page);
    if (url === "/api/tasks") return json([poller]);
    const detail = url.match(/^\/api\/filings\/(.+)$/);
    if (detail) {
      const filing = page.items.find((f) => f.accession_no === decodeURIComponent(detail[1]));
      if (filing) return json(detailOf(filing));
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("filings helpers", () => {
  it("labels items with and without an SEC title", () => {
    expect(itemLabel(earnings.items[0])).toBe(
      "Item 2.02 · Results of Operations and Financial Condition"
    );
    expect(itemLabel({ number: "1.01", title: null })).toBe("Item 1.01");
  });

  it("repeats ticker and form parameters and skips empty dates", () => {
    const query = new URLSearchParams(
      filingsQuery({
        tickers: ["NVDA", "AMZN"],
        formTypes: ["8-K"],
        acceptedFrom: "2026-11-20",
        acceptedTo: "",
        limit: 50,
        offset: 50
      })
    );
    expect(query.getAll("ticker")).toEqual(["NVDA", "AMZN"]);
    expect(query.getAll("form_type")).toEqual(["8-K"]);
    expect(query.get("accepted_from")).toBe("2026-11-20");
    expect(query.has("accepted_to")).toBe(false);
    expect(query.get("offset")).toBe("50");
    expect(query.has("materiality")).toBe(false);
    const high = new URLSearchParams(
      filingsQuery({ tickers: [], formTypes: [], materiality: "high", limit: 50, offset: 0 })
    );
    expect(high.get("materiality")).toBe("high");
  });

  it("labels an open-market purchase with its shares", () => {
    expect(purchaseLabel(form4)).toBe("Open-market purchase · 12,400 sh");
    expect(purchaseLabel({ ...form4, purchase_shares: 1400.5 })).toBe(
      "Open-market purchase · 1,400.5 sh"
    );
    expect(purchaseLabel(earnings)).toBeNull();
  });

  it("says how often the poller runs, when it last ran and when it runs next", () => {
    const now = new Date("2026-11-26T22:00:00Z");
    expect(pollLine(poller, now)).toBe("Polls every 5 min · last ran 2m ago · next in 3m");
    expect(pollLine({ ...poller, last_run: { ...poller.last_run!, status: "partial" } }, now)).toBe(
      "Polls every 5 min · last ran 2m ago (partial) · next in 3m"
    );
    expect(pollLine({ ...poller, last_run: { ...poller.last_run!, status: "running" } }, now)).toBe(
      "Polls every 5 min · polling now · next in 3m"
    );
    expect(pollLine({ ...poller, next_run_at: "2026-11-26T21:59:00Z" }, now)).toBe(
      "Polls every 5 min · last ran 2m ago · next due now"
    );
    expect(pollLine({ ...poller, last_run: null, next_run_at: null }, now)).toBe(
      "Polls every 5 min"
    );
    expect(pollLine({ ...poller, enabled: false }, now)).toBe("SEC polling is off");
    expect(pollLine(undefined, now)).toBeNull();
  });

  it("highlights Alerts on / and on alert detail pages", () => {
    expect(activeNavKey("/")).toBe("/");
    expect(activeNavKey("/alerts/0001045810-26-000001")).toBe("/");
    expect(activeNavKey("/tasks")).toBe("/tasks");
    expect(activeNavKey("/tasksx")).toBe("");
  });
});

describe("AlertsPage", () => {
  it("lists filings with ticker, form, 8-K items and an SEC link", async () => {
    mockApi();
    renderPage();

    const row = (await screen.findByText("NVDA")).closest("tr") as HTMLElement;
    expect(within(row).getByText("8-K")).toBeTruthy();
    expect(
      within(row).getByText("Item 2.02 · Results of Operations and Financial Condition")
    ).toBeTruthy();
    expect(within(row).getByText("Item 9.01 · Financial Statements and Exhibits")).toBeTruthy();
    const link = within(row).getByRole("link", { name: /0001045810-26-000001 on SEC EDGAR/ });
    expect(link.getAttribute("href")).toBe(earnings.sec_url);
    expect(link.getAttribute("target")).toBe("_blank");

    const form4Row = screen.getByText("AMZN").closest("tr") as HTMLElement;
    expect(within(form4Row).queryByText(/^Item/)).toBeNull();
    expect(screen.getByRole("heading", { name: "Alerts · 2" })).toBeTruthy();
  });

  it("shows every issuer a shared filing is listed under, and a dash when none is known", async () => {
    const shared: Filing = { ...earnings, tickers: ["AMZN", "NVDA"] };
    const unknown: Filing = { ...form4, accession_no: "0000000001-26-000003", tickers: [] };
    mockApi({ items: [shared, unknown], total: 2 });
    renderPage();

    expect(await screen.findByText("AMZN, NVDA")).toBeTruthy();
    const unknownRow = screen
      .getByRole("link", { name: /0000000001-26-000003 on SEC EDGAR/ })
      .closest("tr") as HTMLElement;
    expect(within(unknownRow).getByTitle(`CIK ${form4.cik}`)).toBeTruthy();
  });

  it("shows materiality with the rule's reason and when Slack got it", async () => {
    mockApi({ items: [form4, quarterly, earnings], total: 3 });
    renderPage();

    await screen.findByText("NVDA");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Accepted", "Ticker", "Form", "Item", "Materiality", "Pushed", ""]);

    const purchase = screen
      .getByRole("link", { name: /0001018724-26-000002 on SEC EDGAR/ })
      .closest("tr")!;
    expect(
      within(purchase as HTMLElement).getByText("Open-market purchase · 12,400 sh")
    ).toBeTruthy();
    fireEvent.mouseEnter(within(purchase as HTMLElement).getByText("High"));
    expect(await screen.findByText(form4.materiality_reason!)).toBeTruthy();
    expect(within(purchase as HTMLElement).getByText("Nov 20 23:34 ET")).toBeTruthy();

    const low = screen
      .getByRole("link", { name: /0001018724-26-000003 on SEC EDGAR/ })
      .closest("tr")!;
    expect(within(low as HTMLElement).getByText("Low")).toBeTruthy();

    const skipped = screen.getByText("NVDA").closest("tr") as HTMLElement;
    fireEvent.mouseEnter(within(skipped).getByText("Skipped"));
    expect(
      await screen.findByText("Not pushed: classified before the Slack notifier was switched on")
    ).toBeTruthy();
  });

  it("shows queued alerts and filings not classified yet", async () => {
    const queued: Filing = {
      ...earnings,
      slack: {
        status: "pending",
        delivered_at: null,
        detail: "Slack answered HTTP 503: unavailable"
      }
    };
    const unclassified: Filing = {
      ...quarterly,
      accession_no: "0001018724-26-000009",
      materiality: null,
      materiality_reason: null
    };
    mockApi({ items: [queued, unclassified], total: 2 });
    renderPage();

    fireEvent.mouseEnter(await screen.findByText("Queued"));
    expect(
      await screen.findByText("Last attempt: Slack answered HTTP 503: unavailable")
    ).toBeTruthy();
    const unclassifiedRow = screen
      .getByRole("link", { name: /0001018724-26-000009 on SEC EDGAR/ })
      .closest("tr") as HTMLElement;
    fireEvent.mouseEnter(within(unclassifiedRow).getAllByText("—")[0]);
    expect(await screen.findByText("Not classified yet")).toBeTruthy();
  });

  it("filters by materiality and shows the poll line", async () => {
    const fetchMock = mockApi();
    renderPage();

    expect(await screen.findByText(/^Polls every 5 min · /)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "High" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => String(input).includes("materiality=high"))
      ).toBe(true)
    );
  });

  it("shows a one-line empty state", async () => {
    mockApi({ items: [], total: 0 });
    renderPage();

    expect(await screen.findByText("No filings yet")).toBeTruthy();
  });

  it("shows an error with Retry, and retries", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/filings/facets") {
          return new Response(JSON.stringify({ tickers: [], form_types: [] }), { status: 200 });
        }
        calls += 1;
        if (calls === 1) {
          return new Response(JSON.stringify({ detail: "database unavailable" }), { status: 503 });
        }
        return new Response(JSON.stringify({ items: [earnings], total: 1 }), { status: 200 });
      })
    );
    renderPage();

    expect(await screen.findByText("database unavailable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("NVDA")).toBeTruthy());
  });
  it("opens a filing beside a narrow list, keeping the filters in the address", async () => {
    mockApi();
    renderPage("/?materiality=high&ticker=NVDA");

    fireEvent.click(await screen.findByRole("link", { name: "Details of 0001045810-26-000001" }));
    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
    expect(screen.getByTestId("where").textContent).toBe(
      "/alerts/0001045810-26-000001?materiality=high&ticker=NVDA"
    );
    const list = screen.getByRole("navigation", { name: "Alerts list" });
    const current = within(list).getByRole("button", { current: true });
    expect(current.textContent).toContain("NVDA · 8-K"); // formLabel keeps 8-K as is
    expect(screen.getByText("Filtered: NVDA · High")).toBeTruthy();
  });

  it("closes with ×, Esc or Back, showing the list as it was without loading it again", async () => {
    const fetchMock = mockApi();
    renderPage();
    const listCalls = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/filings?")).length;

    fireEvent.click(await screen.findByText("NVDA"));
    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
    expect(screen.queryByRole("navigation", { name: "Alerts list" })).toBeNull();

    fireEvent.click(screen.getByText("NVDA"));
    await screen.findByRole("heading", { name: "NVDA · 8-K" });
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
    expect(listCalls()).toBe(1);
  });

  it("moves between filings with the arrow keys and Newer / Older, without adding history", async () => {
    mockApi({ items: [form4, earnings, quarterly], total: 3 });
    renderPage();

    fireEvent.click((await screen.findAllByText("AMZN", { selector: "td" }))[0]);
    expect(await screen.findByRole("heading", { name: "AMZN · Form 4" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Newer filing" }) as HTMLButtonElement).disabled
    ).toBe(true);

    fireEvent.keyDown(window, { key: "ArrowDown" });
    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Older filing" }));
    expect(await screen.findByRole("heading", { name: "AMZN · 10-Q" })).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Older filing" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.keyDown(window, { key: "ArrowUp" });
    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();

    // Stepping replaced the address each time, so one Esc (Back) returns to the list.
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
  });

  it("leaves the arrow keys alone while a filter is being typed in", async () => {
    mockApi({ items: [form4, earnings], total: 2 });
    renderPage();
    fireEvent.click(await screen.findByText("AMZN", { selector: "td" }));
    await screen.findByRole("heading", { name: "AMZN · Form 4" });

    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(screen.getByRole("heading", { name: "AMZN · Form 4" })).toBeTruthy();
    input.remove();
  });

  it("opens a link to a filing straight in the pane, and closing it shows the list", async () => {
    mockApi();
    renderPage("/alerts/0001045810-26-000001");

    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/"));
    expect(await screen.findByText("AMZN", { selector: "td" })).toBeTruthy();
  });

  it("keeps a loaded list across visits and reloads it only on Refresh", async () => {
    const fetchMock = mockApi();
    const { unmount } = renderPage();
    expect(await screen.findByText("Updated just now")).toBeTruthy();
    unmount();

    renderPage();
    expect(screen.getByText("NVDA", { selector: "td" })).toBeTruthy(); // at once, from memory
    const listCalls = () =>
      fetchMock.mock.calls.filter(([input]) => String(input).startsWith("/api/filings?")).length;
    expect(listCalls()).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(listCalls()).toBe(2));
  });

  it("says when newer filings match, and shows them only when asked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let total = 2;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url === "/api/filings/facets") return json({ tickers: [], form_types: [] });
      if (url === "/api/tasks") return json([poller]);
      return json({ items: [form4, earnings], total });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await screen.findByText("NVDA", { selector: "td" });

    total = 5;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(await screen.findByText("3 new filings since this list was loaded")).toBeTruthy();
    expect(screen.getByText("NVDA", { selector: "td" })).toBeTruthy(); // the table did not change

    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() =>
      expect(screen.queryByText("3 new filings since this list was loaded")).toBeNull()
    );
  });

  it("reads its filters from the address and writes changes back to it, starting again at page 1", async () => {
    const fetchMock = mockApi();
    renderPage("/?materiality=high&ticker=NVDA&page=2");

    await waitFor(() => {
      const query = fetchMock.mock.calls
        .map(([input]) => String(input))
        .find((url) => url.startsWith("/api/filings?"));
      expect(query).toContain("materiality=high");
      expect(query).toContain("ticker=NVDA");
      expect(query).toContain("offset=50"); // page 2
    });
    expect((screen.getByRole("radio", { name: "High" }) as HTMLInputElement).checked).toBe(true);

    fireEvent.click(screen.getByRole("radio", { name: "Low" }));
    await waitFor(() =>
      expect(screen.getByTestId("where").textContent).toBe("/?ticker=NVDA&materiality=low")
    );
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/?ticker=NVDA"));
  });

  it("opens a filing from its row, but not from its SEC link", async () => {
    mockApi();
    renderPage();

    const link = await screen.findByRole("link", { name: /0001045810-26-000001 on SEC EDGAR/ });
    fireEvent.click(link);
    expect(screen.getByTestId("where").textContent).toBe("/");

    fireEvent.click(screen.getByText("NVDA"));
    expect(await screen.findByRole("heading", { name: "NVDA · 8-K" })).toBeTruthy();
  });
});
