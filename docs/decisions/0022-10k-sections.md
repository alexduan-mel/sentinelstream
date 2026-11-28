# 0022: Risk factors and MD&A from 10-Ks, as numbered paragraphs

Status: accepted, 28 Nov 2026.

## Context

M3 shows what changed in a company's 10-K since last year, with every change cited to the filing. The changes worth reading are in two sections: risk factors (Item 1A) and management's discussion and analysis, or MD&A (Item 7). A citation needs a stable address: accession number, item and paragraph.

A 10-K is one HTML document in the filing's EDGAR folder, inline XBRL since 2019. There is no structured feed of its text: SEC's XBRL covers the numbers, not the prose.

Checked on 28 Nov 2026 from the production API container, on all 21 10-Ks stored for the watchlist: AMZN, GOOGL, MSFT and NVDA, fiscal 2020 to 2026, 2–10 MB each. ANT and SPCX have filed no 10-K yet; NBIS files a 20-F.

- All are made with Workiva. Text is in `div`s, page breaks are an `hr` followed by a "Table of Contents" link back, and the page number sits at the foot of each page (Alphabet's as "28.").
- The table of contents lists every item, and links to an empty `div` with an id at the top of the page where the item starts. That id comes before the page's "Table of Contents" line, not on the heading.
- Headings read "Item 1A. Risk Factors", "ITEM 1A.RISK FACTORS" or, at Microsoft, a running header "Item 1A" on every page of the section above the real heading.
- Bullets are two-cell table rows (a "•" cell, then the text) or one `div` with the bullet glued to the text.
- A paragraph that crosses a page break is two `div`s, the second starting in lower case.
- MD&A holds many figure tables, with one-cell caption rows such as "Year Ended" or "($ in millions)".

## Decision

**A parser on Python's own `html.parser`** (`filings/tenk.py`), no new dependency. It reads the document into blocks (one per paragraph-like element, one per table row) and skips hidden content (`ix:header`, `display:none`).

- **A section** runs from an "Item N" heading to the next heading of a different item. Each candidate (the table of contents, running headers, the heading itself) is measured, and the one followed by the most text wins. Under 2,000 characters counts as not found, such as a section incorporated by reference to another document.
- **Paragraphs** are the section's text blocks, numbered from 1. Left out: tables with a row of several cells other than a bullet (figures, counted in `tables_skipped`), page numbers, "Table of Contents" and short lines repeated three or more times in the document. A paragraph cut by a page break is joined back. Headings inside a section (such as "Risks Related to Our Industry") stay as paragraphs, because they are part of what can change.
- **Anchor**: the heading's element id, or for a heading at the top of a page, the id just above that page's header line. It matched the table of contents' own link for both sections in all 21 10-Ks, so a link can open the document at the section.

**Storage.** `filing_sections` has one row per 10-K and item, found or not, with the heading, anchor, source document name and its SHA-256, so the exact file read can be checked against EDGAR. `filing_paragraphs` has the text. Both carry `known_at`, the 10-K's acceptance time.

**Task `extract_10k_sections`**, every hour on the default queue. It reads the two most recent 10-Ks of each ready watchlist issuer, which are the pair a year-over-year diff compares, and at most 8 a run. A 10-K is due until it has sections from the current `EXTRACTOR_VERSION`. Raising the version re-extracts everything, and `force_refresh` does so on demand. A 10-K's rows are replaced in one transaction. A missing section makes the run `partial` once, and a failed fetch is retried next hour.

10-K/A amendments are not read: they usually restate only Part III or exhibits.

## Consequences

- About 100–230 paragraphs per section, a few hundred kilobytes per 10-K stored.
- Paragraph numbers are only stable for one extractor version. The diff stores the version it compared, and raising it means diffing again.
- Figures in MD&A tables are not part of the text diff; the numbers come from XBRL (ADR 0020).
- A 10-K laid out differently (no "Item" headings, sections by cross-reference index) shows as not found rather than wrong text. None of the watchlist's do this today.
- Foreign private issuers' 20-F sections (Item 3.D risk factors, Item 5 operating review) are later work.
