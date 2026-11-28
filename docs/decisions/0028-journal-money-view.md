# 0028: The Journal's money view

Status: accepted, 28 Nov 2026.

## Context

The owner chose two views of the Journal on 28 Nov 2026 ([ADR 0025](0025-journal-ledger.md)):

- the **record of calls**, where every entry counts once whatever its size ([ADR 0027](0027-journal-alpha-beta.md));
- **my money**, built from the optional share counts, which answers "did I make money, and more than QQQ would have made me?".

The money view has to make clear that it is only as complete as the trades logged.

## Decision

**Positions.** The counted entries of a ticker, in trade order, form positions. A position starts with an Opened, takes its Added and Trimmed entries, and ends with a Closed if there is one. Passes belong to no position.

**Dollars.** Each entry is valued at its score's start price ([ADR 0026](0026-journal-scores.md)): the trade price if one was given, or else the first close after it was logged. Share counts are moved to today's share basis through later splits, so the dollar amounts stay as traded.

- **Put in:** the sum of shares × price over Opened and Added.
- **Taken out:** for each Trimmed, its shares as a fraction of those held, × price; for the Closed, everything still held.
- **Value now:** the shares still held × the latest close.
- **Profit:** taken out + value now − put in.

**The same money in QQQ.** Every dollar put in buys QQQ at the same point: QQQ's price at the entry's start, or that day's open for a trade price. Every Trimmed or Closed takes out the same fraction of that QQQ holding at its own point. The difference between the two profits is what the decisions earned beyond QQQ, in dollars.

**Left out, and said so.**

- A position is left out of the totals if any of its entries has no share count (`no_shares`), or if any entry has no price to start from yet (`waiting`).
- The page lists left-out positions with the reason and says how many there are.
- Its footnote says the totals are complete only if every trade, from an alert or not, is logged with its share count.

**Computed with the scores,** on each request (`money` in `GET /api/journal`, covering the entries in the response, so one ticker's when filtered). Nothing is stored.

## Consequences

- Dividends are left out on both sides, the same as in the scores.
- The money view weights big positions heavily, which is the point of it. The record of calls stays the measure of judgment.
- Cash held between trades isn't modelled. Profit is in dollars, not a percentage of a portfolio, because the app doesn't know the size of the portfolio and doesn't want to.
- Real dollar amounts appear on a page behind sign-in. They're derived only from what the owner chose to log.
