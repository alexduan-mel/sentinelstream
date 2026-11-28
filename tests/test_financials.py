"""Key financials from XBRL: concept mapping, quarters worked out from longer periods, fiscal labels,
and the API that serves them. Figures are NVIDIA's, Alphabet's and Amazon's as filed, rounded."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from filings.financials import key_financials
from research.asof import XbrlFact

NVDA_CIK = "0001045810"
B = 1_000_000_000

# NVIDIA's fiscal year 2026 ran 27 Jan 2025 – 25 Jan 2026.
FY26 = (date(2025, 1, 27), date(2026, 1, 25))
NINE_MONTHS = (date(2025, 1, 27), date(2025, 10, 26))
SIX_MONTHS = (date(2025, 1, 27), date(2025, 7, 27))
Q1 = (date(2025, 1, 27), date(2025, 4, 27))
Q2 = (date(2025, 4, 28), date(2025, 7, 27))
Q3 = (date(2025, 7, 28), date(2025, 10, 26))
Q4 = (date(2025, 10, 27), date(2026, 1, 25))

TEN_Q1 = ("0001045810-25-000116", date(2025, 5, 28), "10-Q", 2026, "Q1")
TEN_Q2 = ("0001045810-25-000209", date(2025, 8, 27), "10-Q", 2026, "Q2")
TEN_Q3 = ("0001045810-25-000230", date(2025, 11, 19), "10-Q", 2026, "Q3")
TEN_K = ("0001045810-26-000021", date(2026, 2, 25), "10-K", 2026, "FY")


def fact(
    concept: str,
    span: tuple[date, date] | date,
    value: float,
    filing: tuple[str, date, str, int, str],
    *,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
) -> XbrlFact:
    accession, filed, form, fiscal_year, fiscal_period = filing
    start, end = (None, span) if isinstance(span, date) else span
    return XbrlFact(
        cik=NVDA_CIK,
        taxonomy=taxonomy,
        concept=concept,
        unit=unit,
        period_start=start,
        period_end=end,
        value=Decimal(str(value)),
        accession_no=accession,
        form=form,
        filed_date=filed,
        known_at=datetime.combine(filed, datetime.min.time(), UTC) + timedelta(hours=21),
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
    )


def nvda_year() -> list[XbrlFact]:
    """Three 10-Qs and the 10-K, the way NVIDIA tags them."""
    return [
        fact("Revenues", Q1, 44.062 * B, TEN_Q1),
        fact("GrossProfit", Q1, 26.668 * B, TEN_Q1),
        fact("EarningsPerShareDiluted", Q1, 0.76, TEN_Q1, unit="USD/shares"),
        fact("NetCashProvidedByUsedInOperatingActivities", Q1, 27.414 * B, TEN_Q1),
        fact("PaymentsToAcquireProductiveAssets", Q1, 1.227 * B, TEN_Q1),
        fact("Revenues", Q2, 46.743 * B, TEN_Q2),
        fact("Revenues", SIX_MONTHS, 90.805 * B, TEN_Q2),
        fact("NetCashProvidedByUsedInOperatingActivities", SIX_MONTHS, 42.779 * B, TEN_Q2),
        fact("PaymentsToAcquireProductiveAssets", SIX_MONTHS, 3.12 * B, TEN_Q2),
        fact("Revenues", Q3, 57.006 * B, TEN_Q3),
        fact("Revenues", NINE_MONTHS, 147.811 * B, TEN_Q3),
        fact("EarningsPerShareDiluted", Q3, 1.30, TEN_Q3, unit="USD/shares"),
        fact("EarningsPerShareDiluted", NINE_MONTHS, 3.14, TEN_Q3, unit="USD/shares"),
        fact("Revenues", FY26, 215.938 * B, TEN_K),
        fact("EarningsPerShareDiluted", FY26, 4.90, TEN_K, unit="USD/shares"),
        fact("LongTermDebtNoncurrent", Q4[1], 7.469 * B, TEN_K),
        fact("LongTermDebtCurrent", Q4[1], 0.999 * B, TEN_K),
        fact("DebtCurrent", Q4[1], 0.999 * B, TEN_K),  # the same current portion again: not added
        fact("CommonStockSharesOutstanding", Q4[1], 24.304 * B, TEN_K, unit="shares"),
        fact("EntityCommonStockSharesOutstanding", date(2025, 11, 14), 24.3 * B, TEN_Q3, unit="shares",
             taxonomy="dei"),
    ]  # fmt: skip


def by_end(periods):
    return {p.end: p for p in periods}


def test_fourth_quarter_is_the_year_minus_the_first_nine_months() -> None:
    q4 = by_end(key_financials(nvda_year(), "quarterly"))[Q4[1]]
    revenue = q4.values["revenue"]
    assert (q4.start, q4.fiscal_year, q4.fiscal_period) == (Q4[0], 2026, "Q4")
    assert revenue.value == Decimal("68.127") * B
    assert revenue.method == "subtracted"
    assert [s.accession_no for s in revenue.sources] == [TEN_K[0], TEN_Q3[0]]


def test_earnings_per_share_is_not_subtracted() -> None:
    periods = by_end(key_financials(nvda_year(), "quarterly"))
    assert periods[Q3[1]].values["diluted_eps"].value == Decimal("1.30")
    assert periods[Q4[1]].values["diluted_eps"] is None  # 4.90 − 3.14 would mix share counts


def test_year_to_date_cash_flows_become_quarters() -> None:
    q2 = by_end(key_financials(nvda_year(), "quarterly"))[Q2[1]]
    fcf = q2.values["free_cash_flow"]
    # (42.779 − 27.414) − (3.12 − 1.227)
    assert fcf.value == Decimal("13.472") * B
    assert fcf.method == "computed"
    assert {s.concept for s in fcf.sources} == {
        "NetCashProvidedByUsedInOperatingActivities",
        "PaymentsToAcquireProductiveAssets",
    }


def test_a_company_switching_concepts_keeps_one_line() -> None:
    q1 = (date(2025, 1, 1), date(2025, 3, 31))
    q1_2026 = (date(2026, 1, 1), date(2026, 3, 31))
    old = ("0001652044-25-000043", date(2025, 4, 25), "10-Q", 2025, "Q1")
    new = ("0001652044-26-000040", date(2026, 4, 24), "10-Q", 2026, "Q1")
    facts = [
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", q1, 90.234 * B, old),
        fact("Revenues", q1_2026, 109.9 * B, new),
    ]
    periods = key_financials(facts, "quarterly")
    assert [(p.end, p.values["revenue"].sources[0].concept) for p in periods] == [
        (q1_2026[1], "Revenues"),
        (q1[1], "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ]


def test_gross_margin_falls_back_to_revenue_minus_cost() -> None:
    q3 = (date(2026, 7, 1), date(2026, 9, 30))
    filing = ("0001018724-26-000040", date(2026, 10, 30), "10-Q", 2026, "Q3")
    facts = [
        fact("RevenueFromContractWithCustomerExcludingAssessedTax", q3, 200 * B, filing),
        fact("CostOfGoodsAndServicesSold", q3, 94 * B, filing),
    ]
    [period] = key_financials(facts, "quarterly")
    margin = period.values["gross_margin"]
    assert margin.value == Decimal("0.53")
    assert margin.method == "computed"
    assert {s.concept for s in margin.sources} == {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "CostOfGoodsAndServicesSold",
    }


def test_nvidia_reports_gross_profit_itself() -> None:
    q1 = by_end(key_financials(nvda_year(), "quarterly"))[Q1[1]]
    assert round(q1.values["gross_margin"].value, 4) == Decimal("0.6052")


def test_total_debt_adds_current_and_noncurrent_long_term_debt() -> None:
    q4 = by_end(key_financials(nvda_year(), "quarterly"))[Q4[1]]
    debt = q4.values["total_debt"]
    assert debt.value == Decimal("8.468") * B
    assert {s.concept for s in debt.sources} == {"LongTermDebtNoncurrent", "LongTermDebtCurrent"}


def test_short_term_borrowings_are_added_and_long_term_total_is_the_fallback() -> None:
    q = (date(2026, 7, 1), date(2026, 9, 30))
    filing = ("0001628280-26-066880", date(2026, 10, 22), "10-Q", 2026, "Q3")
    facts = [
        fact("Revenues", q, 14 * B, filing),
        fact("LongTermDebt", q[1], 37.861 * B, filing),
        fact("ShortTermBorrowings", q[1], 0.38 * B, filing),
    ]
    [period] = key_financials(facts, "quarterly")
    assert period.values["total_debt"].value == Decimal("38.241") * B


def test_shares_fall_back_to_the_cover_page_count_of_the_same_filing() -> None:
    periods = by_end(key_financials(nvda_year(), "quarterly"))
    assert periods[Q4[1]].values["shares_outstanding"].sources[0].concept == "CommonStockSharesOutstanding"
    cover = periods[Q3[1]].values["shares_outstanding"]
    assert cover.value == Decimal("24.3") * B
    assert cover.sources[0].period_end == date(2025, 11, 14)
    assert periods[Q2[1]].values["shares_outstanding"] is None


def test_annual_periods_are_fiscal_years_not_trailing_twelve_months() -> None:
    year = (date(2025, 1, 1), date(2025, 12, 31))
    trailing = (date(2025, 10, 1), date(2026, 9, 30))
    ten_k = ("0001018724-26-000004", date(2026, 2, 6), "10-K", 2025, "FY")
    ten_q = ("0001018724-26-000040", date(2026, 10, 30), "10-Q", 2026, "Q3")
    facts = [fact("NetIncomeLoss", year, 77.67 * B, ten_k), fact("NetIncomeLoss", trailing, 134.78 * B, ten_q)]
    [period] = key_financials(facts, "annual")
    assert (period.end, period.fiscal_year, period.fiscal_period) == (year[1], 2025, "FY")


def test_a_restatement_replaces_the_original_value() -> None:
    restated = ("0001045810-26-000052", date(2026, 5, 20), "10-Q", 2027, "Q1")
    facts = [*nvda_year(), fact("Revenues", Q1, 44.1 * B, restated)]
    q1 = by_end(key_financials(facts, "quarterly"))[Q1[1]]
    assert q1.values["revenue"].value == Decimal("44.1") * B
    # The label still comes from the filing that reported the quarter as its own.
    assert (q1.fiscal_year, q1.fiscal_period) == (2026, "Q1")


def test_a_quarter_known_only_as_a_comparative_borrows_its_label_from_a_year_later() -> None:
    q1_a_year_earlier = (date(2024, 1, 29), date(2024, 4, 28))
    facts = [*nvda_year(), fact("Revenues", q1_a_year_earlier, 26.044 * B, TEN_Q1)]
    old = by_end(key_financials(facts, "quarterly"))[q1_a_year_earlier[1]]
    assert (old.fiscal_year, old.fiscal_period) == (2025, "Q1")


def test_periods_are_newest_first_and_limited() -> None:
    periods = key_financials(nvda_year(), "quarterly", limit=2)
    assert [p.end for p in periods] == [Q4[1], Q3[1]]
    assert [p.end for p in key_financials(nvda_year(), "annual")] == [FY26[1]]


def test_values_in_other_units_are_ignored() -> None:
    q = (date(2025, 1, 1), date(2025, 3, 31))
    filing = ("0001513845-25-000010", date(2025, 4, 30), "20-F", 2025, "FY")
    assert key_financials([fact("Revenues", q, 1, filing, unit="EUR")], "quarterly") == []


# --- API -----------------------------------------------------------------------------------------


def _store(conn, facts: list[XbrlFact]) -> None:
    with conn.cursor() as cursor:
        cursor.execute("UPDATE instruments SET cik = %s WHERE symbol = 'NVDA'", (NVDA_CIK,))
        for f in facts:
            cursor.execute(
                "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, accession_no, "
                "fiscal_year, fiscal_period, form, filed_date, known_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (f.cik, f.taxonomy, f.concept, f.unit, f.period_start, f.period_end, f.value, f.accession_no,
                 f.fiscal_year, f.fiscal_period, f.form, f.filed_date, f.known_at),
            )  # fmt: skip
    conn.commit()


def test_financials_need_a_session(api_client) -> None:
    assert api_client.get("/api/instruments/NVDA/financials").status_code == 401


def test_financials_api_serves_cited_values(signed_in_client, connect_app_db) -> None:
    _store(connect_app_db(), nvda_year())

    body = signed_in_client.get("/api/instruments/nvda/financials").json()
    assert body["symbol"] == "NVDA"
    assert body["basis"] == "quarterly"
    assert [m["key"] for m in body["metrics"]] == [
        "revenue", "gross_margin", "operating_income", "net_income", "diluted_eps",
        "cash", "total_debt", "shares_outstanding", "free_cash_flow",
    ]  # fmt: skip
    newest = body["periods"][0]
    assert (newest["end"], newest["fiscal_year"], newest["fiscal_period"]) == ("2026-01-25", 2026, "Q4")
    revenue = newest["values"]["revenue"]
    assert revenue["value"] == 68_127_000_000
    assert revenue["method"] == "subtracted"
    assert revenue["sources"][0]["url"] == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/0001045810-26-000021-index.htm"
    )
    assert newest["values"]["operating_income"] is None

    annual = signed_in_client.get("/api/instruments/NVDA/financials", params={"basis": "annual"}).json()
    assert [(p["fiscal_year"], p["fiscal_period"]) for p in annual["periods"]] == [(2026, "FY")]


def test_financials_api_refuses_unknown_tickers_and_bad_parameters(signed_in_client) -> None:
    assert signed_in_client.get("/api/instruments/ZZZZ/financials").status_code == 404
    assert signed_in_client.get("/api/instruments/QQQ/financials").status_code == 404  # a benchmark
    assert signed_in_client.get("/api/instruments/NVDA/financials", params={"basis": "monthly"}).status_code == 422
    assert signed_in_client.get("/api/instruments/NVDA/financials", params={"periods": 0}).status_code == 422
