# Glossary

Plain-language meanings of the terms SentinelStream uses, for readers without a finance background. Each term gets a sentence or two, with an example where it helps.

## SEC and filings

### SEC
The US Securities and Exchange Commission, the government agency that regulates US stock markets. Listed companies must publish certain reports with it, and those reports are SentinelStream's main source.

### EDGAR
The SEC's public website and database where every filing is published, free to read. SentinelStream checks it every 5 minutes for the companies on your watchlist.

### Filing
A report a company (or one of its insiders) submits to the SEC, such as an annual report or a notice that a director bought shares. Each filing has a form type, like 8-K or Form 4.

### Accession number
The unique ID EDGAR gives every filing, for example `0001045810-26-000101`. The first 10 digits say who submitted it; SentinelStream uses the whole number to make sure each filing is stored and alerted only once.

### Acceptance time
The exact moment EDGAR accepted a filing and made it public. Everything SentinelStream measures starts here, because it is the first moment anyone could have known.

### Filing date
The official date on a filing. It can be a day later than the acceptance time: filings accepted after 5:30 pm New York time are dated the next business day.

### CIK
Central Index Key: the SEC's permanent ID number for a company, for example `0001045810` for NVIDIA. Filings are organised by CIK, not by ticker, and a CIK never changes even when a ticker is renamed or reused.

### Ticker
The short symbol a stock trades under, such as NVDA. Tickers can change, and an old ticker can later be given to a different company, which is why SentinelStream checks each ticker against its CIK.

### Filer type
Whether a company reports as a **domestic** company (annual 10-K, quarterly 10-Q, event reports on 8-K) or as a **foreign private issuer**, a non-US company that files a 20-F each year and 6-K reports instead. It decides which alert rules apply.

### 10-K and 10-Q
A US company's annual report (10-K) and quarterly report (10-Q), with its financial statements.

### 8-K
A short report a US company must file within days of an important event, such as signing a major contract or a change of CEO. Each 8-K lists numbered items saying what kind of event it covers.

### 8-K item
The numbered section of an 8-K that says what happened. SentinelStream treats four as high: **1.01** (a material agreement was signed), **2.02** (financial results were released), **4.02** (earlier financial statements can no longer be relied on) and **5.02** (a director or senior officer joined, left or changed role).

### 20-F and 6-K
A foreign private issuer's annual report (20-F) and its reports of events between annual reports (6-K). A 6-K has no item numbers, so SentinelStream cannot yet tell an important one from a routine one and does not alert on them.

### Insider
A company's directors, senior officers and anyone owning more than 10% of its shares. They must report their trades in the company's stock.

### Form 4
The form an insider files, within two business days, to report buying or selling the company's shares. SentinelStream reads the details from each Form 4.

### Transaction code
The letter on each line of a Form 4 that says what kind of trade it was. **P** is a purchase on the open market or in a private deal, **S** a sale, **A** a grant or award, **F** shares withheld to pay tax, **M** an option exercise, **G** a gift. SentinelStream treats purchases (P) as high.

### Reporting owner
The insider a Form 4 is about, with their role (for example Director, or Chief Executive Officer).

### XBRL
A machine-readable format companies use to tag the numbers in their reports, such as revenue or shares outstanding. It lets SentinelStream take financial figures straight from the filing instead of from a model.

### Company facts
The SEC's collection of every XBRL number a company has filed, each with the filing it came from and the period it covers.

## Alerts

### Materiality
How important a filing is under SentinelStream's rules: **High** (pushed to Slack) or **Low** (shown on the Alerts page only). High means an 8-K with item 1.01, 2.02, 4.02 or 5.02 from a domestic company, or a Form 4 open-market purchase.

### Alert
A filing on your watchlist, shown on the Alerts page. High alerts are also sent to Slack.

### Pushed, Queued and Skipped
What happened with Slack for a high alert: **Pushed** shows when Slack received it, **Queued** means it is waiting to be sent (for example while Slack is unreachable), and **Skipped** means it will never be sent, such as a filing that was already old when it was found.

### News and History
A filing is **news** when the 5-minute check found it shortly after it was published. It is **history** when it was loaded later, for example when a ticker was first added. History is never sent to Slack.

### Detection delay
How long after EDGAR's acceptance time SentinelStream first saw a filing. Usually a few minutes, because EDGAR is checked every 5 minutes.

### Acceptance to Slack
How long after EDGAR's acceptance time an alert reached Slack. The target is under 10 minutes.

## Prices and markets

### Trading session
One trading day on the US stock market, 9:30 am to 4:00 pm New York time, Monday to Friday except holidays.

### Daily bar
One trading session's prices for a stock: the open, high, low and close, plus the volume (number of shares traded). A daily bar becomes public at the 4:00 pm close.

### Close
The last price of a stock in a trading session. Returns in SentinelStream are measured from close to close.

### Return
How much a price changed, as a percentage. From $100 to $103 is a +3% return.

### Split and split-adjusted
A split divides each share into several (or, in a reverse split, merges several into one) without changing what the company is worth. A 4-for-1 split turns one $400 share into four $100 shares. Split-adjusted prices rescale the history before the split, so returns are not distorted.

### As traded
Prices exactly as they were on the day, not rescaled for later splits. A $400 share that later split 4-for-1 still shows $400 for the days before the split. SentinelStream keeps market-wide prices this way, so a rule like "$5 or more" sees the price people actually paid, and corrects returns across a split when it calculates them.

### Traded value
How many dollars' worth of a stock changed hands in a day: shares traded times the average price. A $20 stock trading 50,000 shares a day has a traded value of $1 million.

### Listing date
The day a security started trading under its current ticker. Prices from before it belong to whatever used the ticker earlier, so SentinelStream ignores them.

### ETF
Exchange-traded fund: a fund that trades like a single stock but holds a basket of many, such as QQQ or SPY.

### Benchmark
What results are compared against, to tell whether something did better than simply holding the market. SentinelStream's benchmark is QQQ, with SPY shown for reference.

### QQQ
An ETF that tracks the Nasdaq-100, the 100 largest non-financial companies on the Nasdaq exchange. It leans towards large technology companies, like this watchlist.

### SPY
An ETF that tracks the S&P 500, 500 large US companies across all industries. A common stand-in for "the US market".

### Spread
The gap between the highest price a buyer will pay and the lowest price a seller will accept at that moment. For large stocks it is tiny (around 0.01%); for small, rarely traded stocks it can be 1% or more.

### Slippage
The small move in price between deciding to trade and the trade actually happening.

### Trading cost
What buying and later selling a stock costs beyond the quoted price: the spread, slippage and any commission. Research results in SentinelStream have an assumed trading cost taken off so they reflect what was really achievable.

### Liquidity
How easily a stock can be traded without moving its price, roughly how much of it trades each day. Thinly traded stocks have wider spreads and higher trading costs.

### Survivorship bias
Studying only companies that still exist today makes history look better than it was, because the ones that went bust or were delisted drop out. SentinelStream keeps every stock's daily prices as they come in, including ones that later disappear, to avoid it.

## Research

### Point in time
Using only the information that was public at a given moment, as if you were back at that moment. Every fact in SentinelStream records when it became public so research can work this way.

### As of
"As of" a moment means: only data public before that moment. Research reads data only "as of" the time being studied.

### Lookahead
Accidentally using information that was not yet public at the time being studied, for example a filing accepted at 4:05 pm to explain a 4:00 pm price. It makes results look better than anything achievable, so tests check it cannot happen.

### Event study
A standard method that looks at what happened after many past events of the same kind and summarises it. There is no prediction and no machine learning: it is careful averaging over history.

### Return compared with QQQ
A stock's return minus QQQ's return over the same days. If a stock rose 3% while QQQ rose 2%, it did +1% compared with QQQ. This separates what the event did from what the whole market did. Finance calls it the excess or abnormal return.

### Horizon
How long after an event a return is measured. SentinelStream uses 1, 5, 20 and 60 trading days (about a day, a week, a month and a quarter).

### Base rate
What has typically followed a kind of event in the past, for example: "After 1,500 filings like this, the median stock did −0.3% compared with QQQ over 20 days." It is a summary of history, not a forecast or advice.

### Sample size
How many past events a base rate is built from. The smaller it is, the less a base rate can be trusted, so it is always shown.

### Median and average
The median is the middle result when all results are lined up; the average adds them up and divides by how many there are. A few extreme results move the average a lot but the median little, so both are shown.

### Hit rate
The share of past events where the stock did better than the benchmark over the horizon, for example 52%.

### Confidence range
The range the true typical result probably lies in, given how many events there were and how much they varied. A wide range means the evidence is weak.

### Pooling
Combining similar events from other companies when a company has too few of its own, so a base rate rests on a reasonable sample.

## The app

### Watchlist
The companies SentinelStream follows for you. Add or remove them on the Watchlist page.

### Onboarding
Setting up a newly added ticker: **Pending** (waiting to start), **Backfilling** (identified on SEC and watched, while two years of history load), **Ready** (fully set up) or **Failed** (something went wrong; the reason is shown).

### Backfill
Loading past data in one go, such as two years of filings and prices for a newly added ticker.

### Poller
The job that checks EDGAR for new filings from watchlist companies every 5 minutes.

### Task run
One run of a background job, such as one 5-minute check. The Tasks page lists them with how they ended: succeeded, partial (some of it worked), skipped (another run of the same job was still going) or failed.

### ADR
Architecture Decision Record: a short note in `docs/decisions/` explaining one design choice in this project, what was decided and why.
