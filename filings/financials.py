"""Key financials from XBRL: nine line items per quarter or year, each traced to the filings it came from.

Companies tag the same line item with different XBRL concepts (NVIDIA reports ``Revenues``, Amazon
``RevenueFromContractWithCustomerExcludingAssessedTax``, and Alphabet moved from the second to the
first in 2025). Each metric lists the concepts that can carry it, in order of preference, and every
period takes the first one the company reported for it (ADR 0020).

Quarterly flows are not always filed as quarters. A 10-K reports the year, not the fourth quarter,
and 10-Q cash-flow statements run from the start of the year. A missing quarter is the longer
period minus the part before it, from the same concept: Q4 = full year − first nine months. Values
that cannot be split that way (earnings per share) are left empty rather than guessed.

Every value carries ``sources``: the filed facts it was read or worked out from, each with its
accession number (ground rule 6). Only reported facts are used; nothing is estimated.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from research.asof import XbrlFact, latest_per_period

Basis = Literal["quarterly", "annual"]
Method = Literal["reported", "subtracted", "computed"]

QUARTER_DAYS = range(80, 101)  # 13-week quarters are 91 days; some run 84 or 98
ANNUAL_DAYS = range(350, 381)  # 52- or 53-week years, and calendar years
NINE_MONTH_DAYS = range(260, 290)

USD = "USD"
SHARES = "shares"
USD_PER_SHARE = "USD/shares"

REVENUE = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
COST_OF_REVENUE = ("CostOfRevenue", "CostOfGoodsAndServicesSold")
GROSS_PROFIT = ("GrossProfit",)
OPERATING_INCOME = ("OperatingIncomeLoss",)
NET_INCOME = ("NetIncomeLoss", "ProfitLoss")
DILUTED_EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")
CASH = ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")
LONG_TERM_DEBT_CURRENT = ("LongTermDebtCurrent",)
LONG_TERM_DEBT_NONCURRENT = ("LongTermDebtNoncurrent",)
LONG_TERM_DEBT_TOTAL = ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities")
SHORT_TERM_DEBT = ("ShortTermBorrowings", "CommercialPaper")
SHARES_OUTSTANDING = ("CommonStockSharesOutstanding",)
COVER_PAGE_SHARES = ("EntityCommonStockSharesOutstanding",)
OPERATING_CASH_FLOW = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
CAPITAL_EXPENDITURE = ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")

# Concepts that mark a period as one the company reported results for.
_ANCHORS = REVENUE + NET_INCOME + OPERATING_INCOME

_UNITS: dict[str, str] = {
    **{c: USD for c in REVENUE + COST_OF_REVENUE + GROSS_PROFIT + OPERATING_INCOME + NET_INCOME + CASH},
    **{c: USD for c in LONG_TERM_DEBT_CURRENT + LONG_TERM_DEBT_NONCURRENT + LONG_TERM_DEBT_TOTAL + SHORT_TERM_DEBT},
    **{c: USD for c in OPERATING_CASH_FLOW + CAPITAL_EXPENDITURE},
    **{c: USD_PER_SHARE for c in DILUTED_EPS},
    **{c: SHARES for c in SHARES_OUTSTANDING + COVER_PAGE_SHARES},
}
CONCEPTS: tuple[str, ...] = tuple(_UNITS)


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: Literal["USD", "USD/shares", "shares", "ratio"]
    definition: str


METRICS: tuple[Metric, ...] = (
    Metric("revenue", "Revenue", "USD", "Sales for the period, as the company reports them."),
    Metric(
        "gross_margin",
        "Gross margin",
        "ratio",
        "Gross profit ÷ revenue. Gross profit as reported, or revenue minus cost of revenue when it is not.",
    ),
    Metric("operating_income", "Operating income", "USD", "Profit from the business before interest and tax."),
    Metric("net_income", "Net income", "USD", "Profit after every cost, interest and tax, for shareholders."),
    Metric(
        "diluted_eps",
        "Diluted EPS",
        "USD/shares",
        "Net income per share, counting shares that options and convertibles could add. "
        "Not split into a fourth quarter when only the year is filed.",
    ),
    Metric("cash", "Cash", "USD", "Cash and cash equivalents at the end of the period."),
    Metric(
        "total_debt",
        "Total debt",
        "USD",
        "Long-term debt including the part due within a year, plus short-term borrowings and commercial "
        "paper. Leases are not included.",
    ),
    Metric(
        "shares_outstanding",
        "Shares outstanding",
        "shares",
        "Common shares at the end of the period; when only the cover-page count is filed, the count on "
        "the date it states.",
    ),
    Metric(
        "free_cash_flow",
        "Free cash flow",
        "USD",
        "Cash from operations minus spending on property and equipment.",
    ),
)
METRIC_KEYS = tuple(m.key for m in METRICS)


@dataclass(frozen=True)
class Source:
    """One filed XBRL value a number was read or worked out from."""

    concept: str
    accession_no: str
    form: str | None
    filed_date: date
    period_start: date | None
    period_end: date
    value: Decimal


@dataclass(frozen=True)
class Value:
    value: Decimal
    method: Method
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    fiscal_year: int | None
    fiscal_period: str | None  # "Q1".."Q4" or "FY"
    values: dict[str, Value | None] = field(default_factory=dict)


def _source(fact: XbrlFact) -> Source:
    return Source(
        concept=fact.concept,
        accession_no=fact.accession_no,
        form=fact.form,
        filed_date=fact.filed_date,
        period_start=fact.period_start,
        period_end=fact.period_end,
        value=fact.value,
    )


def _reported(fact: XbrlFact) -> Value:
    return Value(fact.value, "reported", (_source(fact),))


def _combined(value: Decimal, method: Method, *parts: Value) -> Value:
    sources: list[Source] = []
    for part in parts:
        sources.extend(s for s in part.sources if s not in sources)
    return Value(value, method, tuple(sources))


class _Facts:
    """The latest known value of each concept per period, split into flows (a span) and balances (a day)."""

    def __init__(self, facts: Iterable[XbrlFact]) -> None:
        usable = [f for f in facts if _UNITS.get(f.concept) == f.unit]
        self.all_versions = usable
        self.flows: dict[str, dict[tuple[date, date], XbrlFact]] = defaultdict(dict)
        self.balances: dict[str, dict[date, XbrlFact]] = defaultdict(dict)
        for fact in latest_per_period(usable):
            if fact.period_start is None:
                self.balances[fact.concept][fact.period_end] = fact
            else:
                self.flows[fact.concept][(fact.period_start, fact.period_end)] = fact

    def flow(self, concepts: Sequence[str], start: date, end: date, *, can_subtract: bool = True) -> Value | None:
        for concept in concepts:
            spans = self.flows.get(concept, {})
            fact = spans.get((start, end))
            if fact is not None:
                return _reported(fact)
            if not can_subtract:
                continue
            # The same concept over a longer span ending on the same day, minus the span before ``start``.
            for (whole_start, whole_end), whole in sorted(spans.items()):
                if whole_end != end or whole_start >= start:
                    continue
                part = spans.get((whole_start, start - timedelta(days=1)))
                if part is not None:
                    return Value(whole.value - part.value, "subtracted", (_source(whole), _source(part)))
        return None

    def balance(self, concepts: Sequence[str], day: date) -> Value | None:
        for concept in concepts:
            fact = self.balances.get(concept, {}).get(day)
            if fact is not None:
                return _reported(fact)
        return None


def _periods(facts: _Facts, basis: Basis) -> list[tuple[date, date]]:
    # A fiscal year is a year an annual report covered. 10-Qs also file trailing twelve months
    # (Amazon does), which are not fiscal years.
    fiscal_years = {
        (f.period_start, f.period_end)
        for f in facts.all_versions
        if f.period_start is not None and f.fiscal_period == "FY"
    }
    spans: set[tuple[date, date]] = set()
    for concept in _ANCHORS:
        for start, end in facts.flows.get(concept, {}):
            days = (end - start).days + 1
            is_year = days in ANNUAL_DAYS and (start, end) in fiscal_years
            if basis == "annual" and is_year:
                spans.add((start, end))
            elif basis == "quarterly" and days in QUARTER_DAYS:
                spans.add((start, end))
            elif basis == "quarterly" and is_year:
                # A fourth quarter exists when the same year's first nine months were filed too.
                for nine_start, nine_end in facts.flows.get(concept, {}):
                    if nine_start == start and (nine_end - nine_start).days + 1 in NINE_MONTH_DAYS:
                        spans.add((nine_end + timedelta(days=1), end))
    # One period per end date; if two starts were tagged, keep the one nearest a normal length.
    normal = 91 if basis == "quarterly" else 365
    by_end: dict[date, tuple[date, date]] = {}
    for span in spans:
        held = by_end.get(span[1])
        if held is None or abs((span[1] - span[0]).days - normal) < abs((held[1] - held[0]).days - normal):
            by_end[span[1]] = span
    return sorted(by_end.values(), key=lambda s: s[1])


def _report_ends(facts: _Facts) -> dict[str, date]:
    """Each filing's own period: the latest end of any span it reported (comparatives end earlier)."""
    ends: dict[str, date] = {}
    for fact in facts.all_versions:
        if fact.period_start is not None and fact.period_end > ends.get(fact.accession_no, date.min):
            ends[fact.accession_no] = fact.period_end
    return ends


def _fiscal_labels(facts: _Facts, spans: list[tuple[date, date]], basis: Basis) -> dict[date, tuple[int, str]]:
    """Fiscal year and period of each span, from the filing that reported it as its own period.

    A span whose own filing is older than the stored history takes its label from the span a year
    later or earlier (same period, fiscal year one apart).
    """
    report_ends = _report_ends(facts)
    by_filing: dict[date, XbrlFact] = {}
    for fact in sorted(facts.all_versions, key=lambda f: (f.filed_date, f.accession_no)):
        own_end = report_ends.get(fact.accession_no)
        if own_end is None or fact.fiscal_year is None or fact.fiscal_period is None:
            continue
        by_filing.setdefault(own_end, fact)

    labels: dict[date, tuple[int, str]] = {}
    for _, end in spans:
        fact = by_filing.get(end)
        if fact is None or fact.fiscal_year is None or fact.fiscal_period is None:
            continue
        period = fact.fiscal_period
        if basis == "quarterly" and period == "FY":
            period = "Q4"
        elif basis == "annual" and period != "FY":
            continue
        if basis == "quarterly" and period not in ("Q1", "Q2", "Q3", "Q4"):
            continue
        labels[end] = (fact.fiscal_year, period)

    # Repeat so a label can travel more than one year back (2025 → 2024 → 2023).
    changed = True
    while changed:
        changed = False
        for _, end in spans:
            if end in labels:
                continue
            for other_end, (year, period) in list(labels.items()):
                gap = (other_end - end).days
                if abs(gap) in ANNUAL_DAYS:
                    labels[end] = (year - 1 if gap > 0 else year + 1, period)
                    changed = True
                    break
    return labels


def _gross_margin(facts: _Facts, start: date, end: date, revenue: Value | None) -> Value | None:
    if revenue is None or revenue.value == 0:
        return None
    gross = facts.flow(GROSS_PROFIT, start, end)
    if gross is None:
        cost = facts.flow(COST_OF_REVENUE, start, end)
        if cost is None:
            return None
        gross = _combined(revenue.value - cost.value, "computed", revenue, cost)
    return _combined(gross.value / revenue.value, "computed", gross, revenue)


def _total_debt(facts: _Facts, day: date) -> Value | None:
    noncurrent = facts.balance(LONG_TERM_DEBT_NONCURRENT, day)
    if noncurrent is not None:
        current = facts.balance(LONG_TERM_DEBT_CURRENT, day)
        parts = [noncurrent] if current is None else [noncurrent, current]
    else:
        total = facts.balance(LONG_TERM_DEBT_TOTAL, day)
        if total is None:
            return None
        parts = [total]
    for concept in SHORT_TERM_DEBT:
        short = facts.balance((concept,), day)
        if short is not None:
            parts.append(short)
    if len(parts) == 1:
        return parts[0]
    return _combined(sum((p.value for p in parts), Decimal(0)), "computed", *parts)


def _shares(facts: _Facts, day: date, report_ends: dict[str, date]) -> Value | None:
    balance = facts.balance(SHARES_OUTSTANDING, day)
    if balance is not None:
        return balance
    # The cover page states the count on a later date; use the one in the filing for this period.
    cover = [
        f
        for concept in COVER_PAGE_SHARES
        for f in facts.balances.get(concept, {}).values()
        if report_ends.get(f.accession_no) == day
    ]
    if not cover:
        return None
    return _reported(max(cover, key=lambda f: (f.filed_date, f.accession_no)))


def _free_cash_flow(facts: _Facts, start: date, end: date) -> Value | None:
    operating = facts.flow(OPERATING_CASH_FLOW, start, end)
    capex = facts.flow(CAPITAL_EXPENDITURE, start, end)
    if operating is None or capex is None:
        return None
    return _combined(operating.value - capex.value, "computed", operating, capex)


def key_financials(facts: Iterable[XbrlFact], basis: Basis, *, limit: int = 8) -> list[Period]:
    """The latest ``limit`` periods, newest first, with every metric in ``METRICS`` (None when not filed).

    ``facts`` should hold every stored version of the ``CONCEPTS`` for one company (``AsOf.xbrl_facts``):
    the latest version of each value is used, and older ones only to name fiscal periods.
    """
    table = _Facts(facts)
    spans = _periods(table, basis)[-limit:] if limit > 0 else []
    labels = _fiscal_labels(table, spans, basis)
    report_ends = _report_ends(table)
    periods: list[Period] = []
    for start, end in reversed(spans):
        revenue = table.flow(REVENUE, start, end)
        year, fiscal_period = labels.get(end, (None, None))
        periods.append(
            Period(
                start=start,
                end=end,
                fiscal_year=year,
                fiscal_period=fiscal_period,
                values={
                    "revenue": revenue,
                    "gross_margin": _gross_margin(table, start, end, revenue),
                    "operating_income": table.flow(OPERATING_INCOME, start, end),
                    "net_income": table.flow(NET_INCOME, start, end),
                    "diluted_eps": table.flow(DILUTED_EPS, start, end, can_subtract=False),
                    "cash": table.balance(CASH, end),
                    "total_debt": _total_debt(table, end),
                    "shares_outstanding": _shares(table, end, report_ends),
                    "free_cash_flow": _free_cash_flow(table, start, end),
                },
            )
        )
    return periods
