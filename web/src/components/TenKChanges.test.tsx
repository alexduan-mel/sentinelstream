// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  countsLabel,
  paragraphLabel,
  visibleGroups,
  type ParagraphChange,
  type TenKChanges as Changes,
  type TenKItem
} from "../api/tenkChanges";
import { installDomStubs } from "../test/dom";
import { TenKChanges } from "./TenKChanges";

beforeAll(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NEW_URL =
  "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm#i1a";
const OLD_URL =
  "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/nvda-20250126.htm#i1a";

const removed: ParagraphChange = {
  change_no: 1,
  kind: "removed",
  old_paragraph_no: 4,
  new_paragraph_no: null,
  similarity: null,
  numbers_only: false,
  text: "The COVID-19 pandemic could reduce demand for our products.",
  words: null
};
const figures: ParagraphChange = {
  change_no: 2,
  kind: "changed",
  old_paragraph_no: 5,
  new_paragraph_no: 4,
  similarity: 0.778,
  numbers_only: true,
  text: "Revenue was $130.5 billion in fiscal year 2025.",
  words: [
    { op: "equal", text: "Revenue was " },
    { op: "delete", text: "$60.9 " },
    { op: "insert", text: "$130.5 " },
    { op: "equal", text: "billion in fiscal year 2025." }
  ]
};
const reworded: ParagraphChange = {
  change_no: 3,
  kind: "changed",
  old_paragraph_no: 7,
  new_paragraph_no: 7,
  similarity: 0.81,
  numbers_only: false,
  text: "Export rules now cover more of our products.",
  words: [
    { op: "equal", text: "Export rules " },
    { op: "delete", text: "may " },
    { op: "insert", text: "now " },
    { op: "equal", text: "cover more of our products." }
  ]
};
const added: ParagraphChange = {
  change_no: 4,
  kind: "added",
  old_paragraph_no: null,
  new_paragraph_no: 9,
  similarity: null,
  numbers_only: false,
  text: "Customers may not secure the energy to run new data centers.",
  words: null
};

function changes(extra: Partial<Changes> = {}): Changes {
  return {
    symbol: "NVDA",
    cik: "0001045810",
    filer_type: "domestic",
    item: "1A",
    item_label: "Risk factors",
    comparison: {
      new: {
        accession_no: "0001045810-26-000021",
        filed_date: "2026-02-25",
        report_date: "2026-01-25",
        heading: "Item 1A. Risk Factors",
        section_url: NEW_URL,
        index_url: "x"
      },
      old: {
        accession_no: "0001045810-25-000023",
        filed_date: "2025-02-26",
        report_date: "2025-01-26",
        heading: "Item 1A. Risk Factors",
        section_url: OLD_URL,
        index_url: "y"
      },
      counts: { unchanged: 150, moved: 2, changed: 2, numbers_only: 1, added: 1, removed: 1 },
      computed_at: "2026-11-28T05:00:00Z",
      groups: [
        { heading: "Risks Related to Our Industry and Markets", changes: [removed, figures] },
        { heading: "Risks Related to Regulatory Matters", changes: [reworded, added] }
      ],
      summary: null
    },
    ...extra
  };
}

function renderSection(body: Changes, item: TenKItem = "1A", onItemChange = vi.fn()) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    void input; // recorded by the mock, checked by the tests
    return new Response(JSON.stringify(body), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter>
      <TenKChanges symbol="NVDA" item={item} onItemChange={onItemChange} />
    </MemoryRouter>
  );
  return { fetchMock, onItemChange };
}

describe("helpers", () => {
  it("summarise the counts and where each change sits", () => {
    expect(countsLabel(changes().comparison!.counts)).toBe(
      "2 changed (1 only in figures or dates) · 1 added · 1 removed · 152 unchanged"
    );
    expect(paragraphLabel(removed)).toBe("Paragraph 4 last year");
    expect(paragraphLabel(added)).toBe("Paragraph 9 this year");
    expect(paragraphLabel(figures)).toBe("Paragraph 4, was 5");
    expect(paragraphLabel(reworded)).toBe("Paragraph 7");
  });

  it("hide figures-only changes, and groups left empty", () => {
    const groups = [
      { heading: "Summary", changes: [figures] },
      { heading: "Other", changes: [added] }
    ];
    expect(visibleGroups(groups, false)).toEqual([{ heading: "Other", changes: [added] }]);
    expect(visibleGroups(groups, true)).toBe(groups);
  });
});

describe("TenKChanges", () => {
  it("lists changes under their headings, each linked to its section in the filing", async () => {
    const { fetchMock } = renderSection(changes());

    expect(await screen.findByText("Risks Related to Regulatory Matters")).toBeTruthy();
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/instruments/NVDA/tenk-changes?item=1A");
    expect(
      screen.getByText(
        /Risk factors in the 10-K for the year ended Jan 25, 2026, filed Feb 25, 2026/
      )
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Item 1A this year ↗" }).getAttribute("href")).toBe(
      NEW_URL
    );
    expect(screen.getByRole("link", { name: "last year ↗" }).getAttribute("href")).toBe(OLD_URL);

    // A removed paragraph links to last year's 10-K; the others to this year's.
    const removedRow = screen.getByText(removed.text).closest("div.flex") as HTMLElement;
    expect(within(removedRow).getByText("Removed")).toBeTruthy();
    expect(within(removedRow).getByRole("link").getAttribute("href")).toBe(OLD_URL);
    const addedRow = screen.getByText(added.text).closest("div.flex") as HTMLElement;
    expect(
      within(addedRow).getByRole("link", { name: "This year's 10-K ↗" }).getAttribute("href")
    ).toBe(NEW_URL);

    // A reworded paragraph shows what was struck out and what was added.
    expect(screen.getByText("may").tagName).toBe("DEL");
    expect(screen.getByText("now").tagName).toBe("INS");
    expect(screen.getByText(/Paragraph 7 · 81% of words the same/)).toBeTruthy();
  });

  it("hides changes in figures or dates until asked", async () => {
    renderSection(changes());
    await screen.findByText("Risks Related to Regulatory Matters");

    expect(screen.queryByText("$130.5")).toBeNull();
    fireEvent.click(screen.getByText("Show changes in figures or dates only (1)"));
    expect(screen.getByText("$130.5").tagName).toBe("INS");
    expect(screen.getByText(/figures or dates only · 78% of words the same/)).toBeTruthy();
  });

  it("switches between risk factors and MD&A", async () => {
    const { onItemChange } = renderSection(changes());
    await screen.findByText("Risks Related to Regulatory Matters");

    fireEvent.click(screen.getByText("MD&A"));
    expect(onItemChange).toHaveBeenCalledWith("7");
  });

  it("says why there is nothing to compare", async () => {
    renderSection(changes({ comparison: null }));
    expect(await screen.findByText(/it needs two years of 10-Ks/)).toBeTruthy();
    cleanup();

    renderSection(changes({ symbol: "NBIS", filer_type: "foreign_private", comparison: null }));
    expect(await screen.findByText(/NBIS files a 20-F each year, not a 10-K/)).toBeTruthy();
    cleanup();

    const unchanged = changes();
    unchanged.comparison!.groups = [{ heading: "Summary", changes: [figures] }];
    renderSection(unchanged);
    expect(await screen.findByText(/Only figures or dates changed/)).toBeTruthy();
  });

  it("shows the checked summary, each point linking to the changes it cites", async () => {
    const body = changes();
    body.comparison!.summary = {
      status: "ok",
      model: "claude-opus-5-5",
      written_at: "2026-11-28T06:00:00Z",
      points: [
        { text: "A risk about the energy to run data centers was added.", change_nos: [4] },
        { text: "The COVID-19 risk was dropped and export wording tightened.", change_nos: [1, 3] }
      ],
      rejected_count: 1
    };
    renderSection(body);

    expect(await screen.findByText("Summary")).toBeTruthy();
    const point = screen.getByText(/The COVID-19 risk was dropped/).closest("li") as HTMLElement;
    expect(within(point).getByRole("link", { name: "[1]" }).getAttribute("href")).toBe("#change-1");
    expect(within(point).getByRole("link", { name: "[3]" }).getAttribute("href")).toBe("#change-3");
    expect(document.getElementById("change-3")?.textContent).toContain("Export rules");
    expect(
      screen.getByText(/Written by claude-opus-5-5 on Nov 28, 2026 from the changes below/)
        .textContent
    ).toContain("1 point without a valid citation was left out.");
  });

  it("says when there is no summary to show", async () => {
    const body = changes();
    body.comparison!.summary = {
      status: "rejected",
      model: "claude-opus-5-5",
      written_at: "2026-11-28T06:00:00Z",
      points: [],
      rejected_count: 3
    };
    renderSection(body);
    expect(
      await screen.findByText(/every point the model wrote failed the citation checks/)
    ).toBeTruthy();
    expect(screen.queryByText("Summary")).toBeNull();
  });
});
