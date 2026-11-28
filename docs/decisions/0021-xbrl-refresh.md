# 0021: Keep XBRL facts current after onboarding

Status: accepted, 28 Nov 2026.

## Context

Onboarding loads two years of a company's XBRL facts once, from SEC's companyfacts record ([ADR 0010](0010-ticker-onboarding.md)). Key financials read `xbrl_facts` ([ADR 0020](0020-xbrl-key-financials.md)), so without a refresh they stop at the last report filed before onboarding. The SEC poller already records every watchlist filing within 5 minutes of acceptance, with its form type and an `is_xbrl` flag.

companyfacts has no "since" parameter: each request returns everything the company has ever filed, a few megabytes for a large company. SEC usually adds a new filing to it within minutes of acceptance.

## Decision

**A task, `refresh_xbrl_facts`, every hour on the default queue (it calls SEC).** It works from `filings`, not from a clock: a filing is due when it is a periodic report with XBRL (10-Q, 10-K, 20-F, 40-F, their transition forms and amendments), linked to an active watchlist equity whose onboarding is `ready`, accepted in the last 14 days, and no stored fact comes from it yet.

- **One request per issuer with a due filing**, so a quiet hour costs nothing and a reporting season costs one request per company. Share classes share a CIK.
- **Every fact filed since the earliest due filing is stored**, through the same idempotent insert onboarding uses. Facts that restate earlier periods come with the new accession number, so they are new rows and the financials pick the latest-known value.
- **`known_at` is the filing's EDGAR acceptance time**, which `filings` holds for every due filing (ground rule 2).
- **A due filing missing from companyfacts is tried again next hour.** Missing a day after acceptance makes the run `partial`, so it shows on the Tasks page without paging anyone. After 14 days it is no longer tried; `onboard_tickers` with `force_refresh` reloads everything.
- **An index on `xbrl_facts (accession_no)`** answers "does any fact come from this filing?" without reading every fact the company has.

An instrument still backfilling is left to onboarding, which loads its facts itself.

## Consequences

- New quarterly and annual numbers reach the ticker page within about an hour of a filing's acceptance.
- Current reports (8-K, 6-K) are not watched. Their press-release numbers are not XBRL financial statements; the 10-Q or 10-K that follows is.
- A filing whose XBRL SEC never adds to companyfacts costs one request an hour for 14 days, and shows as a `partial` run in that time.
