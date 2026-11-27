# 0018: Base rates for alerts

Status: accepted, 27 Nov 2026.

## Context

M2 ends with a base rate on each high alert, shown on the Alert detail page and in Slack, and pooled across similar stocks when the sample is small. The calculator ([ADR 0015](0015-base-rate-calculator.md)) can now study both kinds of event that make an alert high ([ADR 0008](0008-materiality-rules-v0.md)): insider open-market purchases ([ADR 0014](0014-insider-transactions-data-sets.md)) and 8-K items 1.01, 2.02, 4.02 and 5.02 ([ADR 0017](0017-market-wide-8k-history.md)). A full run takes minutes, and an alert must reach Slack within 10 minutes of acceptance. So nothing can be calculated in the alert path.

## Decision

**Calculate daily, store, and look up.**

- `compute_base_rates` becomes automatic: daily at 06:00 New York time, before the open, on the research worker ([ADR 0016](0016-research-worker.md)), with the payload `{"event": "alerts"}`.
- The run covers all five kinds in **one pass over the stocks**: each stock's bars are read once, for every kind of event it has.
- It **replaces** the `base_rates` table in one transaction. A row is one kind and key, scope, filter setting and horizon, with the same statistics as the calculator's report.
- One-off reports (`{"event": "insider_purchases"}`, …) still go to the run's meta only.

**Scopes.**

- `market`: every company.
- `sic2:NN`: one industry, the two-digit SIC major group from the company's SEC record (`market_issuers.sic`, [ADR 0017](0017-market-wide-8k-history.md)). A major group is broad: group 36 includes semiconductors and household appliances alike. That breadth is what gives a useful sample.

**Which one an alert shows** (`alerts.base_rate.base_rates_for`):

- **Kind.** A high Form 4 is an `insider_purchase`. A high 8-K gets one base rate per high item it carries.
- **Scope.** The company's industry, when that has at least 30 past events with a 20-session result under the main filters. Otherwise the whole market, pooled, and the label says so. 30 is a common rule of thumb for an average to settle; the range shown with it says how settled it is.
- **What the Alert detail API returns.** `GET /api/filings/{accession}` returns `base_rates` for the filing's first high issuer, with both filter settings at all four horizons. It is empty for a low filing, or before a run has stored anything.

**Industry is reference data, not a time-stamped fact.** SEC keeps no history of a company's industry code. So `AsOf.issuer_industries()` is the one as-of read without a `known_at` filter. It is documented as such, and used only to group past events, never as an input to a return.

**Wording.** A base rate is history, not a forecast. Text built from it describes what followed past events and never says what to do (ground rule 7).

## Consequences

- An alert shows the base rate from the latest daily run: never newer data than the alert itself, and at most a day old.
- Until the market-wide backfills finish (bars, 8-Ks), base rates rest on partial history. Each row's `n` and range say how much.
- About 5 kinds × (1 + up to 80 industries) × 2 settings × 4 horizons: a few thousand rows.
- The Alert detail page shows them for high filings, and the Slack message adds one italic line per high event from the 20-session main-filter result ("History: after 1,977 past insider purchases across all US-listed companies, the next 20 sessions averaged +0.9% vs QQQ after costs; 55% beat QQQ (95% range -0.4% to +2.1%)."). The line is looked up from stored rows during delivery; if the lookup fails, the alert goes out without it.
