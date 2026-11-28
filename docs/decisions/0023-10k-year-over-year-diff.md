# 0023: Year-over-year 10-K changes, paragraph by paragraph

Status: accepted, 28 Nov 2026.

## Context

[ADR 0022](0022-10k-sections.md) stores each watchlist 10-K's risk factors (Item 1A) and MD&A (Item 7) as numbered paragraphs. M3 shows what changed since the previous year's 10-K, each change cited by accession number, item and paragraph. Most of a section carries over from year to year. What matters is what was added, dropped or reworded.

Companies also move paragraphs, rewrite a sentence inside a long paragraph, and restate every figure and date in MD&A each year.

## Decision

**The comparison** (`filings/tenk_diff.py`, standard library `difflib`) lines up last year's paragraphs with this year's in three passes:

1. **In place**: the longest runs of identical paragraphs in the same order are unchanged.
2. **Moved**: a leftover paragraph with identical text on both sides was moved. It is counted, not reported.
3. **Edited**: the rest are paired by word-level similarity, best pairs first, when at least 60% of their words line up. Those are **changed**. What remains is **added** (this year only) or **removed** (last year only).

Text is compared after folding case, curly quotes, dashes and spacing, so typography alone is no change. A changed paragraph is **numbers only** when it matches once every number and month name is masked. MD&A restates figures every year, and the page can show those apart from real rewording.

**Why 60%.** Tried on the four real pairs (fiscal 2025 → 2026 for AMZN, GOOGL, MSFT and NVDA) on 28 Nov 2026. At 50%, the weakest pairs were often two different paragraphs: Microsoft's "Revenue increased $3.8 billion or 7%." paired with "Cost of revenue decreased $1.8 billion or 7%…". At 60%, the weakest were mostly rewrites of the same paragraph, such as cash from operations rising by a different amount. A few look-alikes remain, such as Amazon's International against North America operating income. So the similarity is stored, and the page shows old and new text side by side. Calling two paragraphs changed when they aren't is worse than showing one removed and one added, so the bar errs high.

| 10-K (fiscal 2026) | Item | Unchanged | Changed (numbers only) | Added | Removed |
| --- | --- | --- | --- | --- | --- |
| AMZN | 1A | 123 | 19 (0) | 2 | 1 |
| AMZN | 7 | 49 | 38 (13) | 9 | 14 |
| GOOGL | 1A | 58 + 4 moved | 78 (1) | 10 | 9 |
| GOOGL | 7 | 112 | 66 (12) | 48 | 62 |
| MSFT | 1A | 43 | 47 (0) | 20 | 8 |
| MSFT | 7 | 84 | 49 (17) | 36 | 27 |
| NVDA | 1A | 155 | 43 (1) | 29 | 14 |
| NVDA | 7 | 35 | 39 (9) | 32 | 40 |

Each section took under half a second.

**Pairs.** Each 10-K's section is compared with the same section of the issuer's previous 10-K, when both were found and extracted by the same version, and the previous one was accepted at most 450 days earlier. So a missing year is never skipped over.

**Storage.**
- `filing_section_diffs` holds one row per 10-K and item: the previous accession, the counts, `extractor_version` (which paragraph numbering was compared) and `comparer_version`.
- `filing_paragraph_changes` holds each change in this year's reading order, with the paragraph numbers on both sides. A removed paragraph sits where it stood last year.
- `known_at` is the later 10-K's acceptance time.
- Both tables hang off `filing_sections`, so re-extracting either 10-K deletes the diff, and the next run computes it again from the new paragraphs.

**Task `diff_10k_sections`**, every hour on the research queue (it reads only this database). It computes pairs without a diff from the current `COMPARER_VERSION` and replaces each pair in one transaction. `force_refresh` recomputes every pair.

## Consequences

- A rewritten risk factor shows as its title and body paragraphs changed, or as several added and removed paragraphs. The ticker page groups changes under the heading or risk title they fall under.
- A paragraph that was split in two, or two merged into one, shows as one changed plus one added or removed.
- Raising `COMPARER_VERSION` or the extractor version recomputes everything; numbers are only comparable within one version.
- No model is involved: every change is a pair of stored paragraphs, each with its citation (ground rule 6). Summaries of the changes are the stretch goal.
