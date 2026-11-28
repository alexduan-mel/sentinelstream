// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { activeNavKey } from "../components/TopBar";
import { installDomStubs } from "../test/dom";
import { GlossaryPage } from "./GlossaryPage";

const scrolledTo: string[] = [];
beforeAll(() => {
  installDomStubs();
  // jsdom has no layout, so no scrolling; record where the page asked to scroll instead.
  Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
    scrolledTo.push(this.id);
  });
});
afterEach(cleanup);

function Hash() {
  return <div data-testid="hash">{useLocation().hash}</div>;
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/glossary"]}>
      <GlossaryPage />
      <Hash />
    </MemoryRouter>
  );
}

describe("GlossaryPage", () => {
  it("shows every section with its terms, bold and code rendered", () => {
    renderPage();

    expect(screen.getByRole("heading", { name: /^Glossary · \d+$/ })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "SEC and filings" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Research" })).toBeTruthy();
    const cik = document.getElementById("cik") as HTMLElement;
    expect(cik.textContent).toContain("Central Index Key");
    expect(within(cik).getByText(/^Example:/)).toBeTruthy();
    expect(within(cik).getByRole("link", { name: "Ticker" }).getAttribute("href")).toBe(
      "/glossary#ticker"
    );
    expect(screen.getAllByText("0001045810")[0].tagName).toBe("CODE");
    expect(screen.getAllByText("1.01")[0].tagName).toBe("STRONG");
  });

  it("narrows to matching terms and says when nothing matches", () => {
    renderPage();
    const search = screen.getByLabelText("Search the glossary");

    fireEvent.change(search, { target: { value: "spread" } });
    expect(document.getElementById("spread")).toBeTruthy();
    expect(document.getElementById("cik")).toBeNull();

    expect(screen.getByText(/^\d+ of \d+ terms$/)).toBeTruthy();
    const topics = screen.getByRole("navigation", { name: "Glossary topics" });
    expect(within(topics).queryByText("The app")).toBeNull(); // no matches there

    // A See also link to a term the search hides clears the search and goes there.
    const tradingCost = document.getElementById("trading-cost") as HTMLElement;
    fireEvent.click(within(tradingCost).getByRole("link", { name: "Slippage" }));
    expect(document.getElementById("slippage")).toBeTruthy();
    expect((search as HTMLInputElement).value).toBe("");
    expect(screen.getByTestId("hash").textContent).toBe("#slippage");

    fireEvent.change(search, { target: { value: "qwertyuiop" } });
    expect(screen.getByText("No terms match “qwertyuiop”.")).toBeTruthy();
  });

  it("lists the topics with their counts, and jumps to one", () => {
    renderPage();
    const topics = screen.getByRole("navigation", { name: "Glossary topics" });
    const links = within(topics).getAllByRole("link");

    expect(links.map((link) => link.textContent)).toEqual([
      expect.stringMatching(/^SEC and filings\d+$/),
      expect.stringMatching(/^Company financials\d+$/),
      expect.stringMatching(/^Alerts\d+$/),
      expect.stringMatching(/^Prices and markets\d+$/),
      expect.stringMatching(/^Research\d+$/),
      expect.stringMatching(/^Journal\d+$/),
      expect.stringMatching(/^The app\d+$/)
    ]);
    // The topic being read is marked (jsdom has no layout, so which one is not checked here).
    expect(links.filter((link) => link.getAttribute("aria-current") === "location")).toHaveLength(
      1
    );
    fireEvent.click(within(topics).getByRole("link", { name: /^Research/ }));
    expect(screen.getByTestId("hash").textContent).toBe("#research");
    expect(scrolledTo[scrolledTo.length - 1]).toBe("research");
  });

  it("is a nav tab of its own", () => {
    expect(activeNavKey("/glossary")).toBe("/glossary");
  });
});
