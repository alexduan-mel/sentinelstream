# 0008: Materiality rules v0

Status: accepted, 26 Nov 2026. Amended the same day: the Form 4 rule applies to every filer type (see the last section).

## Context

M1 pushes only material filings to Slack and shows the rest on the Alerts page. CLAUDE.md fixes the v0 rules: 8-K items 1.01, 2.02, 4.02 and 5.02, and Form 4 open-market purchases (transaction code P), are high; everything else is low; rules branch on filer type. Item numbers come free with the submissions API. Transaction codes do not: they are inside each Form 4's ownership XML, one SEC request per filing. A filing can also be linked to several watchlist issuers ([ADR 0007](0007-filing-issuer-links.md)), and on 26 Nov 2026 the one shared filing turned out to be NVIDIA's Schedule 13G about its Nebius stake: listed under both, but about Nebius.

## Decision

**Materiality is stored per filing-issuer link** (`filing_issuers.materiality`, `materiality_reason`, `classified_at`), because the answer depends on the issuer. Its filer type picks the rule set, and a Form 4 is high only for the issuer named in the XML. A watchlist company that is a reporting owner on someone else's Form 4 gets low, with that reason. NULL means not classified yet.

**Rules (`alerts/materiality.py`), pure functions with a reason string for the UI:**

- Domestic: an 8-K naming any of the four items is high, and the reason lists each matched item with its SEC title. A Form 4 is high when the subject issuer has a non-derivative code P row that is not marked disposed. 8-K/A and 4/A are low: amendments usually add exhibits to an event already alerted.
- Foreign private issuers: no high current-report rule in v0. A 6-K has no item numbers, so telling results from routine notices needs its text, which is M3 work.
- Every filer type: the Form 4 rule above. See the amendment below.
- Unknown filer type: low.

**Classification runs inside `ingest_sec_filings`**, right after filings and links are stored, so a high filing is known in the same 5-minute poll. Every unclassified news link (`is_backfill = FALSE`) is handled each run. A new Form 4 whose XML fetch fails stays unclassified, and the run ends `partial`, so the next poll retries it. History is classified too, so every row on the Alerts page has a level, but its Form 4 fetches are capped at 25 per run, newest first. A Form 4 whose XML is fetched but unreadable (bad XML, not an ownership document) is classified low with the parse error as the reason and logged as `form4_unreadable`, rather than retried forever.

**Form 4 XML is parsed with `defusedxml`** (new dependency), since it is input from the internet. The raw XML is the primary document's file name without EDGAR's `xslF345X..` rendering folder; that was checked against a live AMZN Form 4. Non-derivative rows are kept in `form4_transactions` as filed, with `known_at` set to the filing's acceptance time. Alerts will quote them and M2's insider-buying study will need them. Derivative rows are not stored in v0.

## Consequences

- A domestic issuer polled for the first time costs one extra SEC request per historical Form 4, spread over runs. At 25 a run the large-cap watchlist names finish within a few hours, well inside SEC's limits.
- Links that were news before this change (filings first seen in the days since the poller started) are classified as news. The Slack notifier (M1 PR 4) must not push those stale highs when it is first switched on.
- A code P row can be a private purchase as well as an open-market one; Form 4 does not distinguish the two. v0 treats both as high.

## Amendment, 26 Nov 2026: Form 4 for every filer type

The first live classification showed 53 Form 4s, and 15 Form 3s, listed under Nebius (NBIS) since March 2026, with NBIS as the issuer. Nebius still files 20-F and 6-K, so it is still classed `foreign_private`. Its insiders nonetheless report under Section 16, which foreign private issuers' insiders are normally exempt from. The original rule marked those Form 4s low without reading them, so an NBIS insider purchase would have been missed.

The Form 4 rule now applies whatever the filer type. Only current reports (8-K against 6-K) still branch on it. Migration 007 clears the classification of foreign issuers' Form 4s that were marked low without reading, so the poller fetches and classifies them with the history budget. The `filing_high` log line is now written only for news, so classifying history doesn't produce one line per old filing.
