# 0027: Alpha and beta for Journal entries

Status: accepted, 28 Nov 2026.

## Context

A Journal score ([ADR 0026](0026-journal-scores.md)) gives an entry's return and QQQ's over the same days. The difference between them is a blunt measure. A stock that moves twice as much as the market beats QQQ in any rising market, whether or not the thesis was right. The PRD asks for a split into **beta** (how much the stock moves with QQQ) and **alpha** (the rest), measured from the decision against QQQ, as the owner chose on 28 Nov 2026.

## Decision

**Per entry.** The daily returns of the stock and of QQQ are taken from the entry's start to its end.

- The first day runs from a trade price, with QQQ from that day's open, when the entry has one. The last day may end at the closing entry's trade price.
- Compounded, the daily returns give exactly the score's own `stock_return` and `qqq_return`. A test checks this.

A least-squares line through those pairs gives:

- **beta**, the slope: 1.5 means the stock tended to move 1.5% when QQQ moved 1%;
- **r²**, the share of the stock's daily moves the line explains;
- **market part** = beta × QQQ's return over the entry: what QQQ's moves alone would have given a stock this sensitive to them;
- **alpha** = the stock's return − the market part: what QQQ's moves don't explain.

The split is additive, so the page can say "+12%: +9% from the market, +3% alpha".

**Too early.** With fewer than 20 daily returns (about a month), the fit says little. The score gives `fit: too_early` with no beta or alpha, and keeps the plain returns.

**All entries together** (`combined` in `GET /api/journal`; each entry counts once, whatever its size):

- **Alpha of the calls:** the mean of each fitted entry's alpha, signed by what the call was. Opened and Added are calls that the stock would do well, so their alpha counts as is. Trimmed, Closed and Pass are calls that it wouldn't, so their alpha counts negated. Above zero means the calls were right beyond what QQQ's moves explain. `calls_right` counts the fitted entries with a signed alpha above zero.
- **Beta of holdings:** one regression over every daily pair of the fitted Opened and Added entries, so it describes how strongly what was held moved with the market.
- Counts of scored, fitted and too-early entries, so a mean over three entries isn't read as more than it is.

Beta and alpha are worked out on each request with the scores (Python's `statistics.linear_regression`, no new dependency). The daily pairs stay on the server.

## Consequences

- Alpha over a few months of one stock is noisy. r² and the day count are given with each fit, and the page has to show the counts next to any combined figure.
- The regression uses no risk-free rate (the plain market model rather than CAPM excess returns). Over holding periods of months, the difference is a fraction of the rate earned on cash, small next to the noise.
- Each entry's beta comes from its own days only. It isn't a long-run beta for the company: a short holding through a quiet market can give a beta that is far from the company's usual one.
- Signing Pass, Trimmed and Closed against the stock treats "not holding" as the call. A Pass on a company that then did well counts against the record, which is the point of logging passes.
