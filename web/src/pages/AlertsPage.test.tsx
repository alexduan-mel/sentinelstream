// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { filingsQuery, itemLabel, type Filing } from "../api/filings";
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
  ticker: "NVDA",
  form_type: "8-K",
  items: [
    { number: "2.02", title: "Results of Operations and Financial Condition" },
    { number: "9.01", title: "Financial Statements and Exhibits" }
  ],
  accepted_at: "2026-11-20T21:05:00Z",
  filed_date: "2026-11-20",
  sec_url:
    "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/0001045810-26-000001-index.htm"
};

const form4: Filing = {
  accession_no: "0001018724-26-000002",
  cik: "0001018724",
  ticker: "AMZN",
  form_type: "4",
  items: [],
  accepted_at: "2026-11-21T04:30:00Z",
  filed_date: "2026-11-20",
  sec_url: "https://www.sec.gov/Archives/edgar/data/1018724/000101872426000002/x-index.htm"
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

  it("never shows materiality or push columns before M1", async () => {
    mockApi();
    render(<AlertsPage />);

    await screen.findByText("NVDA");
    const headers = screen.getAllByRole("columnheader").map((cell) => cell.textContent);
    expect(headers).toEqual(["Accepted", "Ticker", "Form", "Item", ""]);
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
