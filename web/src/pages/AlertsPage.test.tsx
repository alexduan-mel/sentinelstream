// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { filingsQuery, itemLabel, pollLine, purchaseLabel, type Filing } from "../api/filings";
import type { TaskDefinition } from "../api/tasks";
import { activeNavKey } from "../components/TopBar";
import { installDomStubs } from "../test/dom";
import { AlertsPage } from "./AlertsPage";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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

  it("highlights Alerts only on /", () => {
    expect(activeNavKey("/")).toBe("/");
    expect(activeNavKey("/tasks")).toBe("/tasks");
    expect(activeNavKey("/tasksx")).toBe("");
  });
});

describe("AlertsPage", () => {
  it("lists filings with ticker, form, 8-K items and an SEC link", async () => {
    mockApi();
    render(<AlertsPage />);

    const row = (await screen.findByText("NVDA")).closest("tr") as HTMLElement;
    expect(within(row).getByText("8-K")).toBeTruthy();
    expect(
      within(row).getByText("Item 2.02 · Results of Operations and Financial Condition")
    ).toBeTruthy();
    expect(within(row).getByText("Item 9.01 · Financial Statements and Exhibits")).toBeTruthy();
    const link = within(row).getByRole("link", { name: /0001045810-26-000001/ });
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
    render(<AlertsPage />);

    expect(await screen.findByText("AMZN, NVDA")).toBeTruthy();
    const unknownRow = screen
      .getByRole("link", { name: /0000000001-26-000003/ })
      .closest("tr") as HTMLElement;
    expect(within(unknownRow).getByTitle(`CIK ${form4.cik}`)).toBeTruthy();
  });

  it("shows materiality with the rule's reason and when Slack got it", async () => {
    mockApi({ items: [form4, quarterly, earnings], total: 3 });
    render(<AlertsPage />);

    await screen.findByText("NVDA");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Accepted", "Ticker", "Form", "Item", "Materiality", "Pushed", ""]);

    const purchase = screen.getByRole("link", { name: /0001018724-26-000002/ }).closest("tr")!;
    expect(
      within(purchase as HTMLElement).getByText("Open-market purchase · 12,400 sh")
    ).toBeTruthy();
    fireEvent.mouseEnter(within(purchase as HTMLElement).getByText("High"));
    expect(await screen.findByText(form4.materiality_reason!)).toBeTruthy();
    expect(within(purchase as HTMLElement).getByTitle("2026-11-21T04:34:10Z")).toBeTruthy();

    const low = screen.getByRole("link", { name: /0001018724-26-000003/ }).closest("tr")!;
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
    render(<AlertsPage />);

    fireEvent.mouseEnter(await screen.findByText("Queued"));
    expect(
      await screen.findByText("Last attempt: Slack answered HTTP 503: unavailable")
    ).toBeTruthy();
    const unclassifiedRow = screen
      .getByRole("link", { name: /0001018724-26-000009/ })
      .closest("tr") as HTMLElement;
    fireEvent.mouseEnter(within(unclassifiedRow).getAllByText("—")[0]);
    expect(await screen.findByText("Not classified yet")).toBeTruthy();
  });

  it("filters by materiality and shows the poll line", async () => {
    const fetchMock = mockApi();
    render(<AlertsPage />);

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
    render(<AlertsPage />);

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
    render(<AlertsPage />);

    expect(await screen.findByText("database unavailable")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText("NVDA")).toBeTruthy());
  });
});
