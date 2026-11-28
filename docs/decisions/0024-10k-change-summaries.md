# 0024: Cited summaries of 10-K changes, written by Claude

Status: accepted, 28 Nov 2026.

## Context

The ticker page lists every paragraph added, removed and changed in a 10-K's risk factors and MD&A since the year before ([ADR 0023](0023-10k-year-over-year-diff.md)). That is 20 to 180 changes per section, too many to read for a quick picture. M3's stretch goal is a short summary on top, under ground rule 6: any model output is schema-validated, and any claim without a citation to a filing section is rejected. Ground rule 7 also applies: no buy or sell language.

## Decision

**Claude Opus 5.5 (`claude-opus-5-5`), chosen by the owner,** through the official `anthropic` Python SDK. One request per diff, at effort `high`. It reads the section's changes, each numbered `[n]` by its `change_no`, with its heading and last year's and this year's wording. Changes in figures or dates only are left out: they are what MD&A restates every year, and leaving them out keeps the model away from figures. The answer must match a Pydantic schema (`client.beta.messages.parse`): up to six points, each one sentence plus the change numbers it rests on. Server-side refusal fallback (`fallbacks: "default"`) is on, so a policy decline is retried on a fallback model the API picks, and a final refusal is recorded as such.

**Every point is checked before it is stored.** A point is rejected when it:

- cites no change, or a change it was not given;
- states a number that none of its cited paragraphs contains, because numbers come from filings, not models;
- gives advice about the stock ("sell the shares", "undervalued", "price target");
- is empty or over 400 characters.

Rejections are stored with the reason. A summary with every point rejected, or a refusal, shows nothing.

**Storage.** `filing_change_summaries` has one row per diff: status, model, prompt version, how many changes were read, accepted and rejected counts, the rejections, request id and token counts. `filing_change_summary_points` holds the accepted points with their `change_nos`. Each change already cites the accession numbers, item and paragraph numbers in both 10-Ks, so every point resolves to sections of two filings. Both tables cascade from the diff: a diff computed again is summarized again. **`known_at` is when the summary was written**, not when the 10-K was accepted, so research as of an earlier date never sees it (ground rule 2).

**Task `summarize_10k_changes`**, every hour on the research queue: it calls the Claude API, never SEC, and one call can take a minute. It runs at most 4 diffs per run. An API error leaves the diff due for the next run. Raising `PROMPT_VERSION` or running with `force_refresh` writes summaries again. `ANTHROPIC_API_KEY` comes from Parameter Store like every other secret; while it is missing the job fails with a clear error (ground rule 5).

**On the page**, the summary sits above the changes. Each point links to the changes it cites, and each change shows its number. The footnote names the model and date, says the summary was written from the changes shown rather than the whole 10-K, says how many points were dropped, and asks the reader to check a point against its changes.

## Consequences

- Cost is small: one call per section per year. The first eight (four companies, two sections each) are estimated at $1–3, then under $1 a year for the current watchlist.
- The checks catch a missing or wrong citation and an invented figure. They cannot prove that a point fairly reads its cited changes. That is why every point links to them, and why the page says to check.
- A point that cites a heading-only change can be thin. The prompt asks for significant changes first; a reader can always fall back on the full list.
- Changing the model or prompt means raising `PROMPT_VERSION`; the old summaries are replaced on the next runs.
