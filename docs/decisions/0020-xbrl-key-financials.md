# 0020: Key financials from XBRL

Status: accepted, 28 Nov 2026.

## Context

M3 (Explain) shows a company's key financials beside its filings. Ground rule 6 says the numbers come from SEC XBRL, not from a model. Onboarding already stores every XBRL value a watchlist company filed in the last two years in `xbrl_facts` ([ADR 0010](0010-ticker-onboarding.md)), exactly as filed. Three problems stand between those rows and a table of quarters:

- **Companies tag the same line item differently.** NVIDIA and Alphabet report revenue as `Revenues`, Amazon, Microsoft and SpaceX as `RevenueFromContractWithCustomerExcludingAssessedTax`. Alphabet switched from the second to the first during 2025. Gross profit is tagged by NVIDIA and Microsoft but not by Amazon or Alphabet, which tag only cost of revenue (and under different names).
- **Not every quarter is filed as a quarter.** A 10-K reports the fiscal year, not its fourth quarter. 10-Q cash-flow statements run from the start of the fiscal year (6 and 9 months), not the quarter.
- **Filings repeat earlier periods.** Each 10-Q repeats last year's quarter, and later filings sometimes restate it. In companyfacts, a fact's fiscal year and period (`fy`, `fp`) belong to the filing that reported it, not to the period the value covers.

Live data checked on 28 Nov 2026 for NVDA, GOOGL, AMZN, MSFT, SPCX and NBIS.

## Decision

**Nine metrics**, picked by the owner: revenue, gross margin, operating income, net income, diluted EPS, cash, total debt, shares outstanding and free cash flow. Quarterly by default (the latest 8), annual on request. The mapping lives in `filings/financials.py`. It is a pure function over the facts that `AsOf.xbrl_facts` returns for one company.

**Concept lists in order of preference.** Each metric lists the us-gaap concepts that can carry it. For each period, the first concept the company reported wins. A company that changes tags keeps one continuous line.

| Metric | Concepts, first match wins |
| --- | --- |
| Revenue | `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `…IncludingAssessedTax`, `SalesRevenueNet` |
| Gross margin | `GrossProfit` ÷ revenue; else (revenue − `CostOfRevenue` or `CostOfGoodsAndServicesSold`) ÷ revenue |
| Operating income | `OperatingIncomeLoss` |
| Net income | `NetIncomeLoss` (the parent's shareholders), else `ProfitLoss` |
| Diluted EPS | `EarningsPerShareDiluted`, `EarningsPerShareBasicAndDiluted` |
| Cash | `CashAndCashEquivalentsAtCarryingValue`, else the total including restricted cash |
| Total debt | (`LongTermDebtNoncurrent` + `LongTermDebtCurrent`), else `LongTermDebt`, else `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`; plus `ShortTermBorrowings` and `CommercialPaper` when filed |
| Shares outstanding | `CommonStockSharesOutstanding` at period end, else the cover-page `dei:EntityCommonStockSharesOutstanding` of the filing for that period |
| Free cash flow | operating cash flow − (`PaymentsToAcquirePropertyPlantAndEquipment` or `PaymentsToAcquireProductiveAssets`) |

The balance-sheet debt lines come first because `LongTermDebt` is sometimes the face value from the debt note (Amazon: $55.1B against $54.7B carried). `DebtCurrent` is not added, because NVIDIA files it as the same figure as `LongTermDebtCurrent`. Leases are left out of debt.

**Periods.** A quarter is a span of 80–100 days. A fiscal year is a span of 350–380 days that an annual report (`fp = FY`) covered. Without that check, Amazon's trailing-twelve-month figures in its 10-Qs would look like years.

**Missing quarters by subtraction, never by estimate.** When a quarter is not filed directly, it is the same concept over a longer span ending on the same day, minus the span before the quarter starts: Q4 = year − 9 months, and Q2 cash flow = 6 months − Q1. Per-share figures are never subtracted, because share counts differ between the parts, so diluted EPS for a fourth quarter stays empty. A metric that cannot be read or worked out is `null`, never zero.

**Latest known value.** For each concept and period, the most recently filed value is used, so a restatement replaces the original. The API reads as of the request time through the as-of layer, so research can later ask what the table looked like on any past date.

**Fiscal labels from the filing's own period.** A filing's own period is the latest end date among the spans it reported, since comparatives end earlier. A period takes `fy`/`fp` from the filing whose own period it is (an `FY` filing labels the fourth quarter `Q4`). A period known only as a comparative, because its own filing predates the stored two years, borrows the label of the period a year later, with the fiscal year one less.

**Citations.** Each value carries its `sources`: concept, accession number, form, filing date, period and value of every fact it was read or worked out from, plus the EDGAR link. `method` says whether it was `reported`, `subtracted` or `computed`.

**API.** `GET /api/instruments/{symbol}/financials?basis=quarterly|annual&periods=8` for watchlist tickers. It returns the metric definitions and the periods, newest first.

## Consequences

- With two years of filings stored, a company has about 8 quarters and 3–4 fiscal years.
- Foreign private issuers file annual reports only (NBIS: 20-F), so they have no quarterly figures. Their 6-K results are not XBRL-tagged. The ticker page will say so.
- Companies without the listed concepts show empty cells rather than a guess. For example, SpaceX tags no share count without a class breakdown, and companyfacts drops class-level values. Adding a concept to a list is the fix, with a test.
- Values in a currency other than USD are ignored for now.
- `xbrl_facts` is loaded only at onboarding. Keeping it current after each new 10-Q, 10-K or 20-F is a separate step in M3.
