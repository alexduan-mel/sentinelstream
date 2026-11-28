# Insider buying, Dec 2024 to Nov 2026: did the published effect show up?

Written 28 Nov 2026. Data as of 28 Nov 2026 00:15 UTC. This closes M2's last box. The result is reported whichever way it came out.

## The short answer

**Partly, and not in a form anyone could have used.**

- **Against QQQ, insider buys lost.** Over the 20 trading sessions after a buy, the stocks did 0.13% worse than QQQ *before* trading costs, and 1.30% worse *after* them. Over 60 sessions they did 2.1% worse before costs.
- **Against similar stocks, insider buys did a little better.** The same companies on other dates did 1.18% worse than QQQ over 20 sessions. So the buys were **1.05 points ahead** of them (95% range 0.14 to 2.02). Against other insider-buying companies on the same dates, they were 0.59 points ahead, a range that includes zero.
- **Trading costs were about as large as that edge.** The estimated cost of a round trip in these stocks averaged 1.17%.
- **Bigger purchases did better than small ones.** Buys of $500k or more were 1.19% ahead of QQQ over 20 sessions before costs, and buys under $50k were 1.45% behind. The ranges overlap, so this is a lean, not a finding.

**For SentinelStream:** an insider purchase is mild evidence about a stock, not a reason to expect it to beat QQQ. The base rate on an insider-buy alert, which compares with QQQ, will usually be negative. That mostly shows small companies lagging QQQ in these two years, plus costs, not the insiders being wrong.

## What the published research says

Studies of US data from earlier decades found that stocks rose more than the market after insiders bought on the open market. The size was very roughly half a percent to one percent a month. The effect was stronger in small companies, and much weaker for insiders who trade on a regular schedule.

- Lakonishok and Lee (2001), *Are insider trades informative?*: purchases predict returns, mostly in small companies.
- Jeng, Metrick and Zeckhauser (2003), *Estimating the returns to insider trading*: purchases earn abnormal returns, sales do not.
- Cohen, Malloy and Pomorski (2012), *Decoding inside information*: the returns come from "opportunistic" insiders, not "routine" ones who trade in the same month every year.

Those studies cover decades and thousands of companies. This one covers 23 months, so it can show whether the effect is visible in recent data. It cannot settle whether the effect is real.

## What was measured

**Events.** Open-market purchases (Form 4, transaction code P) from SEC's Insider Transactions Data Sets, filed October 2024 to September 2026 ([ADR 0014](../decisions/0014-insider-transactions-data-sets.md)). One company on one filing date counts as one event, however many insiders bought ([ADR 0015](../decisions/0015-base-rate-calculator.md)): 18,988 events.

**Timing.** The datasets give only the filing date, so an event counts as public at midnight New York after that date. Each event is bought at the close of the next session. This misses any jump on the filing day itself, so it is a conservative choice.

**Returns.** Each event is held 1, 5, 20 and 60 sessions, and the stock's return is compared with QQQ over exactly the same days.

- *Excess return* is the stock's return minus QQQ's.
- *Before costs* is the raw price change. *After costs* subtracts each stock's estimated cost of buying and selling: at least 0.10% a trade, or half its bid–ask spread estimated from recent highs and lows if that is larger.
- Prices are not adjusted for dividends.

**Filters.** These are the calculator's two settings. Neither was tuned on this result.

- *Main*: the stock closed at $5 or more before entry, and traded at least $1M a day. That leaves 7,733 events.
- *None*: every event that matched a traded stock, 16,212 events.

**Matching.** 925 events had no readable ticker, and 1,519 had a ticker with no prices: mostly over-the-counter stocks, which the market-wide bars don't cover. 332 had no bar on the entry day. Those 2,776 events are left out.

**Confidence range.** This is the 95% range for the average, from resampling whole calendar months. Stocks in the same month move together, so resampling single events would make the range look narrower than the evidence supports.

### The controls

Comparing insider buys with QQQ alone mixes two things: what insiders know, and what kind of companies they work for. Most insider buying happens in small companies, and in these two years small companies as a group lagged QQQ, which is large technology stocks.

To separate the two, the same calculator ran on two control groups. Each has three draws per event, with a fixed random seed.

- **Same stocks, other dates:** every insider-buy stock, on random sessions at least 30 days from any of its own insider buys. This holds the company fixed.
- **Same dates, other stocks:** every insider-buy date, with random other stocks from the same pool (stocks whose insiders bought at some point) that had no buy within 30 days of it. This holds the market's moves on those days fixed.

The difference between insider buys and a control uses the same month resampling. Both groups are resampled by the same months, so moves they share cancel out.

## Results

### Main filter: excess return over QQQ, before costs

| Sessions held | Insider buys (n = 7,733) | Same stocks, other dates | Same dates, other stocks |
| ---: | ---: | ---: | ---: |
| 1 | −0.03% | −0.04% | −0.02% |
| 5 | −0.01% | −0.30% | −0.12% |
| 20 | −0.13% | −1.18% | −0.73% |
| 60 | −2.14% | −4.17% | −2.52% |

The figures are averages. Medians are lower at every horizon (for insider buys, −0.93% at 20 sessions), because a few large winners pull the average up. At 60 sessions n is 7,382: events after late August 2026 haven't reached their 60th session yet.

### How far insider buys were ahead of the controls

Main filter, before costs. The difference is in percentage points.

| Sessions held | vs same stocks, other dates | vs same dates, other stocks |
| ---: | ---: | ---: |
| 1 | 0.00 (−0.11 to 0.10) | −0.01 (−0.11 to 0.08) |
| 5 | 0.29 (−0.00 to 0.56) | 0.12 (−0.16 to 0.38) |
| 20 | **1.05 (0.14 to 2.02)** | 0.59 (−0.12 to 1.45) |
| 60 | 2.02 (−0.56 to 4.91) | 0.37 (−1.47 to 2.38) |

The edge builds over weeks rather than appearing the next day, as in the published studies. It is clearer against the same companies on other dates than against other companies on the same dates.

### After costs

| Sessions held | Insider buys vs QQQ, after costs | Average round-trip cost |
| ---: | ---: | ---: |
| 1 | −1.20% (−1.37 to −1.04) | 1.17% |
| 5 | −1.17% (−1.78 to −0.60) | 1.17% |
| 20 | −1.30% (−3.07 to 0.48) | 1.17% |
| 60 | −3.32% (−7.52 to 1.21) | 1.18% |

The share of insider buys that beat QQQ after costs was 42% at 20 sessions. Against SPY instead of QQQ, the 20-session figure after costs is −0.93%.

### By size of purchase

Main filter, 20 sessions, excess over QQQ before costs.

| Dollars bought that day | n | Average | 95% range |
| --- | ---: | ---: | ---: |
| under $50k | 2,574 | −1.45% | −3.51 to 0.47 |
| $50k to $500k | 3,155 | +0.11% | −1.57 to 1.76 |
| $500k or more | 2,004 | +1.19% | −0.77 to 3.60 |

The order matches the idea that people put more money behind stronger convictions. All three ranges overlap, though.

### Leaving out pre-planned trades

Some insiders buy under a 10b5-1 plan: a schedule set months ahead, so the trade says little about the present. Leaving those filings out drops 700 events. The 20-session result after costs moves only from −1.30% to −1.12%, because few purchases are pre-planned. Plans mostly cover sales.

### No filter

With every matched stock kept, including those under $5 and the barely traded, insider buys were 0.30 points ahead of both controls on the first day (range 0.07 to 0.68). By 20 sessions the gap had shrunk to about 0.2 to 0.3 points, with ranges spanning zero. Estimated costs in this group average 1.43% a round trip, so the first-day jump could not have been captured.

## What to be careful about

- **Two years is short.** There are 23 months of entries, in one market regime: a strong run for large technology stocks. The published effect is an average over decades, and it varies a lot between years.
- **Many comparisons.** This page reports 16 differences against controls. Four have ranges that exclude zero. Chance alone would give about one, and the four are not independent. The 20-session result against the same stocks is the most solid, and it is still one result.
- **The cost estimate is rough.** Spreads are estimated from daily highs and lows (Abdi and Ranaldo, 2017), not from quotes. The estimate is noisy for small stocks and may overstate their costs. Even at the 0.20% minimum round trip, though, insider buys trailed QQQ.
- **Size is only partly controlled.** There is no market-cap data. The controls draw from the same pool of companies, which helps, but the pool still leans small.
- **Opportunistic against routine insiders was not tested.** That split needs three years of each insider's history, and this data has two.
- **Late filings.** An insider has two business days to file. A filing that arrives late gives its information to the market late, and the data can't tell those apart.

## How to reproduce it

On the EC2 host, with the research worker running:

```sh
# Calculator reports for insider purchases, in the task run's meta (Tasks page):
sudo docker exec sentinelstream-worker-1 python -m orchestration.enqueue compute_base_rates \
  --payload '{"event":"insider_purchases","exclude_10b5_1":false}'
# The comparison with the control groups (prints JSON):
sudo docker exec sentinelstream-research-worker-1 python -m research.insider_study
```

The code is in `research/insider_study.py`. The output this page was written from is in [`insider-buying-2026-11-28.json`](insider-buying-2026-11-28.json). The controls use a fixed seed, so the same data gives the same numbers. New insider data arrives quarterly, and the next dataset (October to December 2026) will add another quarter of events.

## What could follow

These are not M2 work. Each would need the owner's decision.

- Show a same-stock control next to the QQQ base rate on insider-buy alerts, so the alert compares like with like.
- Rerun after each quarterly dataset, and once there are three years, split opportunistic from routine insiders.
- Add market caps from XBRL shares outstanding (M3) to test the small-company part of the published finding directly.
