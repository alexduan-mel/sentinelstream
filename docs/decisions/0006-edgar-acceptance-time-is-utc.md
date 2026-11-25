# 0006: EDGAR acceptance time is UTC

Status: accepted, 26 Nov 2026. Supersedes the acceptance-time part of ADR 0004.

## Context

ADR 0004 read the submissions API's `acceptanceDateTime` (for example `2026-11-24T23:46:00.000Z`) as New York wall-clock time and treated the `Z` as wrong. The first screenshot of the Alerts page against live data showed acceptance times that EDGAR cannot produce. EDGAR accepts filings from 06:00 to 22:00 Eastern, but the page showed AMZN Form 4s at 23:13 to 23:46 ET, ANT Form 4s at 22:40 ET and a GOOGL Form 4 at 02:07 ET. The EDGAR index page of the top AMZN Form 4 says "Accepted 2026-11-24 18:46:00". EDGAR's pages use Eastern time, and 18:46 EST is 23:46 UTC. The `Z` is correct.

Every stored `known_at` was therefore four or five hours late. That never caused lookahead, because the error made facts later, not earlier. But it put wrong times on the Alerts page, and it would have hidden real delivery delay from the M1 target of reaching Slack within 10 minutes of acceptance. ADR 0004's tripwire did not catch the error. It only flags a time more than five minutes after the fetch, so it fires only for a filing polled less than about five hours after acceptance, and the live runs so far have been manual and occasional.

## Decision

- **`acceptanceDateTime` is read as written.** A `Z` or a numeric offset is honoured. A value with no zone at all, which the API does not send today, is read as Eastern, the zone of EDGAR's own pages.
- **Stored rows are corrected by a forward migration** (`002_acceptance_times_utc.sql`). It recovers the digits SEC sent (the stored instant as New York wall-clock time) and reads them as UTC. `001_baseline.sql` is not edited.
- **A second tripwire checks EDGAR's filing hours.** A run counts acceptance times outside 06:00 to 22:00 Eastern, with 15 minutes' slack, in `meta.acceptance_outside_edgar_hours` and logs `sec_acceptance_outside_edgar_hours`. Unlike the future-time check, this catches an offset in either direction, and it works on history as well as on fresh filings. The future-time check stays.

## Consequences

- Accepted times on the Alerts page, and every `known_at` used by research, move four or five hours earlier, to the times EDGAR shows.
- Each acceptance time is now checked against what EDGAR shows for a real filing, not against a rule about filing dates. When a source's time zone is in doubt, check it against the source's own page for one record before relying on it.
- The migration cannot round-trip digits inside the hour skipped when daylight time starts (02:00 to 02:59 on the second Sunday in March). In UTC those are Saturday evening Eastern, when EDGAR is closed, so no real filing is affected.
