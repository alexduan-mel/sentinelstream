# 0025: The Journal, an append-only ledger of decisions

Status: accepted, 28 Nov 2026.

## Context

M4 keeps a record of the owner's own decisions and measures each one against QQQ (PRD: "Log a decision and thesis from an alert; returns vs QQQ; alpha and beta split"). A record like this is only useful if it is honest. It must not be possible to rewrite a thesis after the fact, backdate an entry to a price already known, or quietly drop the decisions that went badly. Ground rule 7 also applies: no broker, no orders, no recommendation language. Entries are the owner's own record.

The owner chose these on 28 Nov 2026:

- **Actions:** Opened, Added, Trimmed, Closed, and Pass ("looked, chose not to act"), so not acting is scored too.
- **Price:** the price a real trade was done at, entered with its day and checked against that day's prices. Without one, and always for a Pass, the entry starts from the first daily close after it was logged.
- **Size:** an optional share count. Every entry still counts once in the record of calls. Share counts feed a separate "my money" view.
- **Alpha and beta:** measured from the entry until it ends, against QQQ. This is scored in later M4 PRs.

## Decision

**Two tables, both append-only** (migration 025). `journal_entries` holds one row per decision: ticker, action, thesis, optional alert (`accession_no`), optional trade (`traded_on`, `price`, `shares`), who logged it, and `known_at`. A trigger refuses every UPDATE and DELETE on both tables, so the guarantee doesn't rest on the API alone.

**`known_at` is the database clock at insert, never a request field.** An entry without a trade price can only be scored from a close that came after it, so it can never borrow a price already known when it was logged (ground rule 2).

**A trade price is checked, not trusted.** The day must be a trading day, not in the future, with a stored bar. The price must be inside that day's low–high range, with 1% leeway for a fill just before the open or after the close. Stored bars are split-adjusted while the typed price is as traded. The range is therefore moved back to that day's share basis using the stored splits after it (ADR 0013), and share counts are moved forward to today's basis the same way. Today's bar is stored only after the close, so a trade typed for today is accepted as entered. Scoring checks it once the bar arrives, and an out-of-range price is flagged rather than used.

**Entries follow a position, in trade order.** Opened starts a position. Added and Trimmed need one, and Closed ends it. Pass is allowed at any time and takes no trade fields. Entries are ordered by trade day (or the day they were logged), then by log time. An entry can't be logged with a trade day earlier than the ticker's latest entry. While every entry since Opened gives a share count, Trimmed must leave some shares and Closed must be the whole position. Once one entry has no count, the size is unknown and is not checked.

**Corrections are new rows** in `journal_corrections`, each with a reason:

- `thesis`: new wording.
- `source`: a different alert, or none.
- `withdrawn`: the entry stops counting but stays listed with its reason.

The API returns the original and the corrected wording side by side. The action, ticker, trade and time can't be corrected. To fix one, the entry is withdrawn and logged again with its trade day, so the score is unchanged and the history shows both. An entry can't be withdrawn if that would leave a later entry with no position (an Added after the Opened is gone), and it can be withdrawn only once.

**Rules for logging:** only active, ready watchlist equities, not benchmarks. A linked alert must be a filing listed under that company (`filing_issuers`). Each log locks the ticker's `instruments` row, so two entries on one ticker can't cross.

**API:** `GET /api/journal` (`?symbol=` optional) returns every entry, newest first, withdrawn ones included, plus the tickers with an open position. `POST /api/journal` logs an entry and returns `201`. `POST /api/journal/{id}/corrections` adds a correction. Logging writes one row in the request, so it isn't background work under ground rule 4.

## Consequences

- Two measures come out of one ledger. The **record of calls** counts each entry once: when I act on a filing, does the stock then beat QQQ? The **my money** view weights entries by shares and shows whether the owner made money. It is complete only when every trade, alert-driven or not, is logged, and the page has to say so.
- Real share counts and prices are stored on an internet-facing server behind sign-in, which is the reason the prototype's Holdings feature was left behind. They are optional, and the record of calls works without them.
- Self-reported prices are a trust point. The low–high check catches typos and impossible prices, not a cherry-picked fill inside the range. Over a long holding period the difference is small.
- A trade logged weeks late is allowed, but its `known_at` shows the gap. A late Pass gets no such allowance: it is always scored from after it was logged.
- No edits means a typo in a thesis stays in the history next to its correction. That is the intended cost.
