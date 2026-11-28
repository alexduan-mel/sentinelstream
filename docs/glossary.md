# Glossary

Plain-language meanings of the terms SentinelStream uses, for readers without a finance background. Each term gets a sentence or two, often with an example and related terms.

## SEC and filings

### SEC
**Short for:** Securities and Exchange Commission

The US government agency that regulates US stock markets. Listed companies must publish certain reports with it, and those reports are SentinelStream's main source.

**See also:** EDGAR; Filing

### EDGAR
**Short for:** Electronic Data Gathering, Analysis, and Retrieval

The SEC's public website and database where every filing is published, free to read. SentinelStream checks it every 5 minutes for the companies on your watchlist.

**See also:** Poller; Acceptance time

### Filing
A report a company (or one of its insiders) submits to the SEC, such as an annual report or a notice that a director bought shares. Each filing has a form type, like 8-K or Form 4.

**Example:** NVIDIA's quarterly report is one filing (a 10-Q); a director's purchase of NVIDIA shares is another (a Form 4).

**See also:** Accession number; 8-K; Form 4

### Accession number
The unique ID EDGAR gives every filing. The first 10 digits say who submitted it; SentinelStream uses the whole number to make sure each filing is stored and alerted only once.

**Example:** `0001045810-26-000101`: submitted by CIK 0001045810 (NVIDIA), in 2026, its 101st submission that year.

**See also:** CIK; Filing

### Acceptance time
The exact moment EDGAR accepted a filing and made it public. Everything SentinelStream measures starts here, because it is the first moment anyone could have known.

**Example:** an 8-K accepted at 4:05 pm New York time could not have explained that day's 4:00 pm closing price.

**See also:** Filing date; Detection delay; Point in time

### Filing date
The official date on a filing. It can be a day later than the acceptance time: filings accepted after 5:30 pm New York time are dated the next business day (insiders' Forms 3, 4 and 5 keep the same day's date until 10 pm).

**Example:** an 8-K accepted at 6:00 pm on a Friday carries the following Monday's filing date; a Form 4 accepted at 6:46 pm that Friday is dated Friday.

**See also:** Acceptance time

### CIK
**Short for:** Central Index Key

The SEC's permanent ID number for a company. Filings are organised by CIK, not by ticker, and a CIK never changes even when a ticker is renamed or reused.

**Example:** `0001045810` is NVIDIA's CIK, whatever ticker it trades under.

**See also:** Ticker; Accession number

### Ticker
The short symbol a stock trades under, such as NVDA. Tickers can change, and an old ticker can later be given to a different company, which is why SentinelStream checks each ticker against its CIK.

**Example:** SPCX belonged to an ETF until April 2026 and to SpaceX from its listing in June 2026, so SentinelStream keeps only SpaceX's prices from its listing date.

**See also:** CIK; Listing date

### Filer type
Whether a company reports as a **domestic** company (annual 10-K, quarterly 10-Q, event reports on 8-K) or as a **foreign private issuer**, a non-US company that files a 20-F each year and 6-K reports instead. It decides which alert rules apply.

**Example:** NVIDIA is a domestic filer; Nebius (NBIS), based in the Netherlands, is a foreign private issuer.

**See also:** 10-K and 10-Q; 20-F and 6-K; Materiality

### 10-K and 10-Q
A US company's annual report (10-K) and quarterly report (10-Q), with its financial statements.

**See also:** Filer type; XBRL; Risk factors; MD&A

### Risk factors
Item 1A of a 10-K: the company's own list of what could hurt its business or its stock, each risk with a one-sentence title and a few paragraphs explaining it. Companies add, drop and reword risks from year to year, and those changes are often the news.

**Example:** NVIDIA's 2026 10-K added risks about customers' ability to secure the energy and capital to build data centres.

**See also:** 10-K and 10-Q; MD&A; 10-K changes

### MD&A
**Short for:** Management's Discussion and Analysis of Financial Condition and Results of Operations

Item 7 of a 10-K: management's own explanation of the year's results, what drove them, and the company's cash and borrowing.

**See also:** 10-K and 10-Q; Risk factors; 10-K changes

### 10-K changes
What changed in a 10-K's risk factors or MD&A since the year before, paragraph by paragraph. A paragraph is **added** (this year only), **removed** (last year only) or **changed** (at least 60% of its words are the same, the rest reworded). A change in figures or dates only, such as last year's revenue becoming this year's, is hidden unless asked for. Paragraphs are matched by their wording, not by a model. Above them, a short summary written by a model (Claude) may list the most significant changes; every point links to the changes it rests on, and points that cite nothing, cite the wrong changes or state a figure not in them are dropped before anything is shown.

**Example:** "Revenue was $60.9 billion in fiscal year 2024" becoming "Revenue was $130.5 billion in fiscal year 2025" is a change in figures and dates only.

**See also:** Risk factors; MD&A; Accession number

### 8-K
A short report a US company must file within days of an important event, such as signing a major contract or a change of CEO. Each 8-K lists numbered items saying what kind of event it covers.

**Example:** a company announcing its quarterly results files an 8-K with item 2.02, usually on the same afternoon.

**See also:** 8-K item; Materiality

### 8-K item
The numbered section of an 8-K that says what happened. SentinelStream treats four as high: **1.01** (a material agreement was signed), **2.02** (financial results were released), **4.02** (earlier financial statements can no longer be relied on) and **5.02** (a director or senior officer joined, left or changed role).

**Example:** an 8-K listing items 2.02 and 9.01 announces results (2.02) and attaches the press release (9.01). It is high because of 2.02.

**See also:** 8-K; Materiality; Base rate

### 20-F and 6-K
A foreign private issuer's annual report (20-F) and its reports of events between annual reports (6-K). A 6-K has no item numbers, so SentinelStream cannot yet tell an important one from a routine one and does not alert on them.

**See also:** Filer type

### Insider
A company's directors, senior officers and anyone owning more than 10% of its shares. They must report their trades in the company's stock.

**See also:** Form 4; Reporting owner

### Form 4
The form an insider files, within two business days, to report buying or selling the company's shares. SentinelStream reads the details from each Form 4.

**Example:** a director buys 1,000 shares on the stock market on a Tuesday and files a Form 4 by Thursday, with transaction code P.

**See also:** Transaction code; Insider; 10b5-1 plan

### 10b5-1 plan
A trading plan an insider sets up in advance, which then buys or sells on a schedule. Trades under one say less about what the insider thinks now than a trade they chose to make that day. Form 4s have a box for it.

**Example:** a chief executive who set up a plan in January to sell 10,000 shares every month is not deciding anything new with each month's sale.

**See also:** Form 4

### Transaction code
The letter on each line of a Form 4 that says what kind of trade it was. **P** is a purchase on the open market or in a private deal, **S** a sale, **A** a grant or award, **F** shares withheld to pay tax, **M** an option exercise, **G** a gift. SentinelStream treats purchases (P) as high.

**See also:** Form 4; Materiality

### Reporting owner
The insider a Form 4 is about, with their role (for example Director, or Chief Executive Officer).

**See also:** Insider; Form 4

### XBRL
**Short for:** eXtensible Business Reporting Language

A machine-readable format companies use to tag the numbers in their reports, such as revenue or shares outstanding. It lets SentinelStream take financial figures straight from the filing instead of from a model.

**See also:** Company facts; 10-K and 10-Q

### Company facts
The SEC's collection of every XBRL number a company has filed, each with the filing it came from and the period it covers.

**Example:** every revenue figure NVIDIA has reported, one per quarter and year, each linked to the 10-Q or 10-K it came from.

**See also:** XBRL

## Company financials

### Fiscal year and fiscal quarter
The twelve months a company uses for its accounts, and the four quarters in it. Many companies' years don't match the calendar, so a company names them by the year they end in.

**Example:** NVIDIA's fiscal year 2027 runs from late January 2026 to late January 2027, so the quarter ending in October 2026 is its Q3 FY2027.

**See also:** 10-K and 10-Q; Key financials

### Key financials
The nine numbers on a ticker page: revenue, gross margin, operating income, net income, diluted EPS, cash, total debt, shares outstanding and free cash flow. Each comes from the company's XBRL and links to the filing it came from.

**See also:** XBRL; Fiscal year and fiscal quarter; Restatement

### Revenue
The money a company took in from selling its products and services during the period, before any costs.

**See also:** Gross margin; Key financials

### Gross margin
The share of revenue left after the direct cost of making what was sold. A higher margin means each sale keeps more for everything else.

**Example:** revenue of $100M and a cost of revenue of $30M give a gross margin of 70%.

**See also:** Revenue; Operating income

### Operating income
What the business earned from its operations: revenue minus the cost of revenue and running costs such as research, sales and administration. Interest and tax come later.

**See also:** Gross margin; Net income

### Net income
The profit left for shareholders after every cost, interest and tax. Negative means a loss.

**See also:** Operating income; Diluted EPS

### Diluted EPS
**Short for:** diluted earnings per share

Net income divided by the number of shares, counting the extra shares that stock options and convertible bonds could turn into. A fourth quarter is left empty when only the full year was filed, because per-share figures can't be split by subtraction.

**See also:** Net income; Shares outstanding

### Cash
Cash and short-term holdings that are as good as cash (cash equivalents), at the end of the period.

**See also:** Total debt; Free cash flow

### Total debt
Borrowed money still owed at the end of the period: long-term debt, including the part due within a year, plus short-term borrowing. Leases are not counted.

**See also:** Cash

### Shares outstanding
The number of the company's shares held by investors at the end of the period. When it rises, each share owns a smaller part of the company.

**See also:** Diluted EPS; Split and split-adjusted

### Free cash flow
Cash the business generated from its operations, minus what it spent on buildings and equipment. It is what is left to repay debt, buy back shares or save.

**Example:** $20B from operations and $6B spent on data centres give $14B of free cash flow.

**See also:** Cash; Net income

### Restatement
A later filing correcting a figure the company reported before. The ticker page always shows the most recently filed figure, and links to the filing it came from.

**See also:** Key financials; Point in time

## Alerts

### Materiality
How important a filing is under SentinelStream's rules: **High** (pushed to Slack) or **Low** (shown on the Alerts page only). High means an 8-K with item 1.01, 2.02, 4.02 or 5.02 from a domestic company, or a Form 4 open-market purchase.

**Example:** an 8-K announcing results (item 2.02) is High; one announcing an annual meeting date (item 8.01) is Low.

**See also:** 8-K item; Transaction code; Alert

### Alert
A filing on your watchlist, shown on the Alerts page. High alerts are also sent to Slack.

**See also:** Materiality; Pushed, Queued and Skipped

### Pushed, Queued and Skipped
What happened with Slack for a high alert: **Pushed** shows when Slack received it, **Queued** means it is waiting to be sent (for example while Slack is unreachable), and **Skipped** means it will never be sent, such as a filing that was already old when it was found.

**See also:** Alert; Acceptance to Slack

### News and History
A filing is **news** when the 5-minute check found it shortly after it was published. It is **history** when it was loaded later, for example when a ticker was first added. History is never sent to Slack.

**Example:** adding MSFT to the watchlist loads its last two years of filings as history; its next 8-K is news.

**See also:** Backfill; Poller

### Detection delay
How long after EDGAR's acceptance time SentinelStream first saw a filing. Usually a few minutes, because EDGAR is checked every 5 minutes.

**Example:** accepted at 4:05:12 pm, seen by the 4:08 pm check: a detection delay of 2 minutes 48 seconds.

**See also:** Acceptance time; Acceptance to Slack

### Acceptance to Slack
How long after EDGAR's acceptance time an alert reached Slack. The target is under 10 minutes.

**See also:** Detection delay; Pushed, Queued and Skipped

## Prices and markets

### Trading session
One trading day on the US stock market, 9:30 am to 4:00 pm New York time, Monday to Friday except holidays.

**Example:** the day after Thanksgiving is a short session that closes at 1:00 pm.

**See also:** Daily bar; Horizon

### Daily bar
One trading session's prices for a stock: the open, high, low and close, plus the volume (number of shares traded). A daily bar becomes public at the 4:00 pm close.

**See also:** Close; Trading session

### Close
The last price of a stock in a trading session. Returns in SentinelStream are measured from close to close.

**See also:** Return; Daily bar

### Return
How much a price changed, as a percentage. From $100 to $103 is a +3% return.

**See also:** Return compared with QQQ; Close

### Split and split-adjusted
A split divides each share into several (or, in a reverse split, merges several into one) without changing what the company is worth. A 4-for-1 split turns one $400 share into four $100 shares. Split-adjusted prices rescale the history before the split, so returns are not distorted.

**Example:** NVIDIA split 10-for-1 on 10 June 2024, so a split-adjusted chart shows its earlier prices divided by 10.

**See also:** As traded

### As traded
Prices exactly as they were on the day, not rescaled for later splits. A $400 share that later split 4-for-1 still shows $400 for the days before the split. SentinelStream keeps market-wide prices this way, so a rule like "$5 or more" sees the price people actually paid, and corrects returns across a split when it calculates them.

**See also:** Split and split-adjusted; Lookahead

### Traded value
How many dollars' worth of a stock changed hands in a day: shares traded times the average price. A $20 stock trading 50,000 shares a day has a traded value of $1 million.

**See also:** Liquidity

### Listing date
The day a security started trading under its current ticker. Prices from before it belong to whatever used the ticker earlier, so SentinelStream ignores them.

**See also:** Ticker

### ETF
**Short for:** Exchange-traded fund

A fund that trades like a single stock but holds a basket of many, such as QQQ or SPY.

**See also:** QQQ; SPY

### Benchmark
What results are compared against, to tell whether something did better than simply holding the market. SentinelStream's benchmark is QQQ, with SPY shown for reference.

**See also:** QQQ; SPY; Return compared with QQQ

### QQQ
An ETF that tracks the Nasdaq-100, the 100 largest non-financial companies on the Nasdaq exchange. It leans towards large technology companies, like this watchlist.

**See also:** Benchmark; SPY

### SPY
An ETF that tracks the S&P 500, 500 large US companies across all industries. A common stand-in for "the US market".

**See also:** Benchmark; QQQ

### Spread
The gap between the highest price a buyer will pay and the lowest price a seller will accept at that moment. For large stocks it is tiny (around 0.01%); for small, rarely traded stocks it can be 1% or more.

**Example:** buyers bid $9.95 and sellers ask $10.05: a spread of 10 cents, or 1%. Buying and selling straight away would lose about that much.

**See also:** Trading cost; Liquidity

### Slippage
The small move in price between deciding to trade and the trade actually happening.

**See also:** Trading cost

### Trading cost
What buying and later selling a stock costs beyond the quoted price: the spread, slippage and any commission. Research results in SentinelStream have an assumed trading cost taken off so they reflect what was really achievable.

**Example:** SentinelStream takes off 0.10% for each purchase and each sale, or half the stock's estimated spread when that is larger.

**See also:** Spread; Slippage; Liquidity

### Liquidity
How easily a stock can be traded without moving its price, roughly how much of it trades each day. Thinly traded stocks have wider spreads and higher trading costs.

**See also:** Traded value; Spread

### Survivorship bias
Studying only companies that still exist today makes history look better than it was, because the ones that went bust or were delisted drop out. SentinelStream keeps every stock's daily prices as they come in, including ones that later disappear, to avoid it.

**See also:** Base rate

## Research

### Point in time
Using only the information that was public at a given moment, as if you were back at that moment. Every fact in SentinelStream records when it became public so research can work this way.

**See also:** As of; Lookahead; Acceptance time

### As of
"As of" a moment means: only data public before that moment. Research reads data only "as of" the time being studied.

**Example:** as of 3:00 pm on a trading day, that day's closing price does not exist yet.

**See also:** Point in time; Lookahead

### Lookahead
Accidentally using information that was not yet public at the time being studied, for example a filing accepted at 4:05 pm to explain a 4:00 pm price. It makes results look better than anything achievable, so tests check it cannot happen.

**See also:** Point in time; As of

### Event study
A standard method that looks at what happened after many past events of the same kind and summarises it. There is no prediction and no machine learning: it is careful averaging over history.

**See also:** Base rate; Return compared with QQQ

### Return compared with QQQ
A stock's return minus QQQ's return over the same days. If a stock rose 3% while QQQ rose 2%, it did +1% compared with QQQ. This separates what the event did from what the whole market did. Finance calls it the excess or abnormal return.

**See also:** Benchmark; Base rate; Horizon

### Horizon
How long after an event a return is measured. SentinelStream uses 1, 5, 20 and 60 trading days (about a day, a week, a month and a quarter).

**See also:** Trading session; Base rate

### Base rate
What has typically followed a kind of event in the past, for example: "After 1,500 filings like this, the median stock did −0.3% compared with QQQ over 20 days." It is a summary of history, not a forecast or advice.

**See also:** Event study; Sample size; Confidence range; Pooling

### Sample size
How many past events a base rate is built from. The smaller it is, the less a base rate can be trusted, so it is always shown.

**See also:** Base rate; Confidence range

### Median and average
The median is the middle result when all results are lined up; the average adds them up and divides by how many there are. A few extreme results move the average a lot but the median little, so both are shown.

**Example:** results of −2%, −1%, 0%, +1% and +30% have a median of 0% but an average of +5.6%, pulled up by the one +30%.

**See also:** Base rate

### Hit rate
The share of past events where the stock did better than the benchmark over the horizon, for example 52%.

**See also:** Base rate; Benchmark

### Confidence range
The range the true typical result probably lies in, given how many events there were and how much they varied. A wide range means the evidence is weak.

**Example:** an average of +0.9% with a 95% range of −0.4% to +2.1% spans zero, so history cannot tell this kind of event apart from simply holding QQQ.

**See also:** Sample size; Base rate

### Pooling
Combining similar events from other companies when a company has too few of its own, so a base rate rests on a reasonable sample. SentinelStream uses the company's broad industry when it has at least 30 past events of the kind, and otherwise all US-listed companies, and says which.

**See also:** Industry (SIC group); Base rate; Sample size

### Industry (SIC group)
SEC files every company under a four-digit Standard Industrial Classification code, such as 3674 for semiconductors. The first two digits form a broad group, here 36 for electronic equipment, which is what base rates use to compare a company with its peers.

**See also:** Pooling

## The app

### Watchlist
The companies SentinelStream follows for you. Add or remove them on the Watchlist page.

**See also:** Onboarding; Alert

### Onboarding
Setting up a newly added ticker: **Pending** (waiting to start), **Backfilling** (identified on SEC and watched, while two years of history load), **Ready** (fully set up) or **Failed** (something went wrong; the reason is shown).

**See also:** Watchlist; Backfill

### Backfill
Loading past data in one go, such as two years of filings and prices for a newly added ticker.

**See also:** Onboarding; News and History

### Poller
The job that checks EDGAR for new filings from watchlist companies every 5 minutes.

**See also:** EDGAR; Detection delay; Task run

### Task run
One run of a background job, such as one 5-minute check. The Tasks page lists them with how they ended: succeeded, partial (some of it worked), skipped (another run of the same job was still going) or failed.

**See also:** Poller

### ADR
**Short for:** Architecture Decision Record

A short note in `docs/decisions/` explaining one design choice in this project, what was decided and why.
