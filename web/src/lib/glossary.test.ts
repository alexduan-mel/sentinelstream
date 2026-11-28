import { describe, expect, it } from "vitest";

import glossaryMarkdown from "../../../docs/glossary.md?raw";
import { filterGlossary, inlineParts, parseGlossary, slug } from "./glossary";

describe("parseGlossary", () => {
  it("reads sections, terms and paragraphs", () => {
    const glossary = parseGlossary(
      "# Glossary\n\nIntro line one\ncontinues.\n\n## SEC and filings\n\n### CIK\nSEC's ID.\n\nSecond paragraph.\n\n### 8-K item\nNumbered **sections**.\n"
    );
    expect(glossary.intro).toEqual(["Intro line one continues."]);
    expect(glossary.sections).toEqual([
      {
        id: "sec-and-filings",
        title: "SEC and filings",
        terms: [
          { id: "cik", term: "CIK", paragraphs: ["SEC's ID.", "Second paragraph."], seeAlso: [] },
          { id: "8-k-item", term: "8-K item", paragraphs: ["Numbered **sections**."], seeAlso: [] }
        ]
      }
    ]);
  });

  it("reads Short for, Example and See also as fields", () => {
    const glossary = parseGlossary(
      "## S\n\n### CIK\n**Short for:** Central Index Key\n\nSEC's ID.\n\n**Example:** `0001045810`.\n\n" +
        "**See also:** Ticker; pushed, queued and skipped.\n\n### Ticker\nA symbol.\n\n" +
        "### Pushed, Queued and Skipped\nSlack states.\n"
    );
    const [cik] = glossary.sections[0].terms;
    expect(cik).toEqual({
      id: "cik",
      term: "CIK",
      shortFor: "Central Index Key",
      paragraphs: ["SEC's ID."],
      example: "`0001045810`.",
      seeAlso: [
        { id: "ticker", term: "Ticker" },
        { id: "pushed-queued-and-skipped", term: "Pushed, Queued and Skipped" }
      ]
    });
  });

  it("rejects a See also that names no term, or the term itself", () => {
    expect(() => parseGlossary("## S\n\n### CIK\nID.\n\n**See also:** Tickr\n")).toThrow(
      "names no term: Tickr"
    );
    expect(() => parseGlossary("## S\n\n### CIK\nID.\n\n**See also:** CIK\n")).toThrow(
      "names itself"
    );
  });

  it.each([
    ["- a list item", "does not render"],
    ["1. a numbered item", "does not render"],
    ["See [the PRD](docs/PRD.md).", "does not render"],
    ["> a quote", "does not render"]
  ])("rejects Markdown it cannot render: %s", (line, message) => {
    expect(() => parseGlossary(`## S\n\n### T\n${line}\n`)).toThrow(message);
  });

  it("splits bold and code runs", () => {
    expect(inlineParts("A **CIK** like `0001045810`.")).toEqual([
      { kind: "text", text: "A " },
      { kind: "bold", text: "CIK" },
      { kind: "text", text: " like " },
      { kind: "code", text: "0001045810" },
      { kind: "text", text: "." }
    ]);
  });

  it("makes stable anchors", () => {
    expect(slug("Pushed, Queued and Skipped")).toBe("pushed-queued-and-skipped");
    expect(slug("10-K and 10-Q")).toBe("10-k-and-10-q");
  });
});

describe("docs/glossary.md", () => {
  const glossary = parseGlossary(glossaryMarkdown);
  const terms = glossary.sections.flatMap((section) => section.terms);

  it("parses, and every term has an explanation and a unique anchor", () => {
    expect(glossary.sections.map((section) => section.title)).toEqual([
      "SEC and filings",
      "Company financials",
      "Alerts",
      "Prices and markets",
      "Research",
      "The app"
    ]);
    expect(terms.length).toBeGreaterThan(50);
    expect(terms.every((term) => term.paragraphs.length > 0)).toBe(true);
    // Abbreviations say what they stand for; most terms link onwards.
    expect(terms.find((term) => term.id === "cik")?.shortFor).toBe("Central Index Key");
    expect(terms.filter((term) => term.seeAlso.length > 0).length).toBeGreaterThan(50);
    expect(new Set(terms.map((term) => term.id)).size).toBe(terms.length);
  });

  it("explains the terms the app shows", () => {
    for (const id of [
      "cik",
      "filer-type",
      "8-k-item",
      "form-4",
      "materiality",
      "onboarding",
      "base-rate"
    ]) {
      expect(terms.some((term) => term.id === id)).toBe(true);
    }
  });

  it("uses no buy or sell advice wording (ground rule 7)", () => {
    expect(glossaryMarkdown).not.toMatch(/\b(you should|we recommend|recommendation to)\b/i);
  });

  it("filters by every word typed, across names and explanations", () => {
    expect(filterGlossary(glossary, "spread").flatMap((s) => s.terms.map((t) => t.id))).toEqual(
      expect.arrayContaining(["spread", "liquidity"])
    );
    expect(filterGlossary(glossary, "director officer").length).toBeGreaterThan(0);
    expect(filterGlossary(glossary, "zzzz")).toEqual([]);
    // Expansions and examples are searched too.
    expect(
      filterGlossary(glossary, "central index").flatMap((s) => s.terms.map((t) => t.id))
    ).toContain("cik");
    expect(filterGlossary(glossary, "  ")).toBe(glossary.sections);
  });
});
