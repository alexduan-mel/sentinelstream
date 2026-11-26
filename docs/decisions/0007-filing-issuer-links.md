# 0007: Filings link to every issuer that lists them

Status: accepted, 26 Nov 2026. Refines the "first sighting wins" rule of [ADR 0004](0004-sec-filings-ingestion.md).

## Context

EDGAR files one accession under every company it concerns: co-registrants on an 8-K, and the issuer and reporting owners on a Form 4. The poller reads each watchlist issuer's submissions and upserts filings on `accession_no` with `DO NOTHING`, and `filings.cik` holds one CIK. So a filing listed under two watchlist issuers was stored under whichever was polled first, and the second issuer never saw it. The live ANT and NBIS retry run on 25 Nov 2026 fetched 556 filings and inserted 555; the other was already stored under the other issuer. M1 alerts must reach every company a filing belongs to.

A per-filing backfill flag has a second gap. When a newly watched issuer is polled for the first time, its history is stored with `is_backfill = TRUE`. If one of those filings is also brand new for an issuer already being watched, and the newcomer happens to be polled first in the run, the filing row says backfill and the watched issuer's alert would be lost.

## Decision

**A `filing_issuers` table links each filing to every watchlist CIK whose submissions list it**, keyed on `(accession_no, cik)`. Every poll upserts a link for every filing it reads, so a link missed once is added on the next poll. `filings` keeps one row per accession; `filings.cik` now means "the issuer whose submissions first listed it" and is used only to build the EDGAR URL. The Alerts list shows one ticker per linked issuer and filters by any of them.

**Backfill is decided per link.** A new link is backfill when the run is a backfill, when it is the issuer's first poll, or when the filing was already stored before this run. The last rule makes an old filing that turns out to be shared with a second issuer history for that issuer, not news. A filing first stored earlier in the same run is still news for an issuer that was already watched. Alerts will fire from links with `is_backfill = FALSE`.

**Migration 004 creates the table and gives every stored filing the link it was recorded under**, copying its `is_backfill`, `first_seen_at` and `task_run_id`. The one known shared filing gains its second link on the first poll after deploy, as backfill.

## Consequences

- A link says EDGAR lists the filing under that issuer; it does not say the issuer is the subject. A Form 4 is listed under the issuer and under each reporting owner, so a watchlist company reporting a stake in another company would get a link too. Materiality rules (M1 PR 3) will read the issuer from the Form 4 XML, so only the subject company is alerted.
- Alert deduplication will stay on the accession number (one Slack message per filing, naming every linked ticker), so a filing linked to two issuers is not pushed twice.
- `idx_filing_issuers_cik` serves the poller's "has this issuer been polled" check and the ticker filter.
