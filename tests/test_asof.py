"""The as-of access layer blocks lookahead (ground rule 2, ADR 0011).

Each test stores facts on both sides of a moment and checks that a reader dated at that moment sees
only what was public strictly before it.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.asof import FACT_TABLES, AsOf, AsOfError

NY = ZoneInfo("America/New_York")
NVDA_CIK = "0001045810"
NBIS_CIK = "0001513845"
RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"


def _bar(conn, symbol: str, trading_date: date, close: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO price_bars (instrument_id, trading_date, close, provider) "
            "SELECT id, %s, %s, 'massive' FROM instruments WHERE symbol = %s",
            (trading_date, Decimal(close), symbol),
        )
    conn.commit()


def _filing(conn, accession: str, cik: str, accepted_at: datetime, *, form: str = "8-K", items=()) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (accession, cik, form, list(items), accepted_at.astimezone(NY).date(), accepted_at, accepted_at),
        )
        cursor.execute(
            "INSERT INTO filing_issuers (accession_no, cik, is_backfill) VALUES (%s, %s, TRUE)", (accession, cik)
        )
    conn.commit()


def _link(conn, accession: str, cik: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO filing_issuers (accession_no, cik, is_backfill) VALUES (%s, %s, TRUE)", (accession, cik)
        )
    conn.commit()


def _form4_row(conn, accession: str, line_no: int, code: str, known_at: datetime) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO form4_transactions (accession_no, line_no, issuer_cik, reporting_owners, owner_roles, "
            "transaction_date, transaction_code, acquired_disposed, shares, price_per_share, known_at) "
            "VALUES (%s, %s, %s, 'Doe Jane', 'director', %s, %s, 'A', 1000, 150, %s)",
            (accession, line_no, NVDA_CIK, known_at.date(), code, known_at),
        )
    conn.commit()


def _xbrl(conn, accession: str, value: int, known_at: datetime, *, form: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO xbrl_facts (cik, taxonomy, concept, unit, period_start, period_end, value, accession_no, "
            "form, filed_date, known_at) VALUES (%s, 'us-gaap', 'Revenues', 'USD', '2026-02-01', '2026-04-30', "
            "%s, %s, %s, %s, %s)",
            (NVDA_CIK, value, accession, form, known_at.astimezone(NY).date(), known_at),
        )
    conn.commit()


# --- as_of itself ---------------------------------------------------------------------------------


def test_naive_as_of_is_rejected(connect_app_db):
    with pytest.raises(AsOfError, match="timezone-aware"):
        AsOf(connect_app_db(), datetime(2026, 11, 25, 16, 0))


def test_non_datetime_as_of_is_rejected(connect_app_db):
    with pytest.raises(AsOfError, match="datetime"):
        AsOf(connect_app_db(), date(2026, 11, 25))


# --- daily bars -----------------------------------------------------------------------------------


def test_a_bar_is_hidden_until_its_close_has_passed(connect_app_db):
    conn = connect_app_db()
    _bar(conn, "NVDA", date(2026, 11, 24), "180")
    _bar(conn, "NVDA", date(2026, 11, 25), "190")
    close_25th = datetime(2026, 11, 25, 16, 0, tzinfo=NY)

    def dates_seen(as_of: datetime) -> list[date]:
        return [bar.trading_date for bar in AsOf(conn, as_of).bars("NVDA")]

    assert dates_seen(close_25th - timedelta(minutes=1)) == [date(2026, 11, 24)]  # during the session
    assert dates_seen(close_25th) == [date(2026, 11, 24)]  # at the close: strictly before only
    assert dates_seen(close_25th + timedelta(microseconds=1)) == [date(2026, 11, 24), date(2026, 11, 25)]


def test_bar_close_is_new_york_time_in_any_as_of_zone(connect_app_db):
    conn = connect_app_db()
    _bar(conn, "NVDA", date(2026, 11, 25), "190")
    # 16:00 New York on 25 Nov (EST, UTC-5) is 21:00 UTC and 06:00 on 26 Nov in Tokyo.
    tokyo = ZoneInfo("Asia/Tokyo")
    assert AsOf(conn, datetime(2026, 11, 26, 5, 59, tzinfo=tokyo)).bars("NVDA") == []
    assert len(AsOf(conn, datetime(2026, 11, 26, 6, 1, tzinfo=tokyo)).bars("NVDA")) == 1
    assert AsOf(conn, datetime(2026, 11, 25, 20, 59, tzinfo=UTC)).bars("NVDA") == []


def test_bars_filter_by_symbol_and_dates(connect_app_db):
    conn = connect_app_db()
    for day, close in ((20, "170"), (21, "175"), (24, "180")):
        _bar(conn, "NVDA", date(2026, 11, day), close)
    _bar(conn, "QQQ", date(2026, 11, 21), "500")
    reader = AsOf(conn, datetime(2026, 11, 26, tzinfo=UTC))

    bars = reader.bars("nvda", start=date(2026, 11, 21), end=date(2026, 11, 24))

    assert [(b.symbol, b.trading_date, b.close) for b in bars] == [
        ("NVDA", date(2026, 11, 21), Decimal("175")),
        ("NVDA", date(2026, 11, 24), Decimal("180")),
    ]
    assert [b.symbol for b in reader.bars("QQQ")] == ["QQQ"]


# --- filings --------------------------------------------------------------------------------------


def test_a_filing_is_hidden_until_after_acceptance(connect_app_db):
    conn = connect_app_db()
    accepted = datetime(2026, 11, 19, 21, 20, 5, tzinfo=UTC)
    _filing(conn, "0001045810-26-000100", NVDA_CIK, accepted, items=("2.02", "9.01"))

    assert AsOf(conn, accepted - timedelta(seconds=1)).filings() == []
    assert AsOf(conn, accepted).filings() == []
    [filing] = AsOf(conn, accepted + timedelta(microseconds=1)).filings()
    assert filing.accession_no == "0001045810-26-000100"
    assert filing.items == ("2.02", "9.01")
    assert filing.known_at == accepted


def test_filings_by_issuer_include_shared_filings_and_stay_point_in_time(connect_app_db):
    conn = connect_app_db()
    early = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    late = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)
    # NVIDIA's schedule 13G about its NBIS stake is stored under NVDA and linked to NBIS too (ADR 0007).
    _filing(conn, "0001045810-26-000062", NVDA_CIK, early, form="SCHEDULE 13G")
    _link(conn, "0001045810-26-000062", NBIS_CIK)
    _filing(conn, "0001513845-26-000010", NBIS_CIK, late, form="6-K")
    _filing(conn, "0001045810-26-000101", NVDA_CIK, late, form="8-K")

    between = AsOf(conn, early + timedelta(days=1))
    assert [(f.accession_no, f.cik) for f in between.filings(cik=NBIS_CIK)] == [("0001045810-26-000062", NBIS_CIK)]

    after = AsOf(conn, late + timedelta(days=1))
    assert [f.accession_no for f in after.filings(cik=NBIS_CIK)] == ["0001045810-26-000062", "0001513845-26-000010"]
    assert [f.accession_no for f in after.filings(cik=NBIS_CIK, form_types=["6-K"])] == ["0001513845-26-000010"]
    assert [f.accession_no for f in after.filings(since=late)] == ["0001045810-26-000101", "0001513845-26-000010"]


# --- Form 4 rows ----------------------------------------------------------------------------------


def test_form4_rows_are_hidden_until_their_filing_is_accepted(connect_app_db):
    conn = connect_app_db()
    first = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)
    second = datetime(2026, 11, 24, 22, 0, tzinfo=UTC)
    _filing(conn, "0001045810-26-000201", NVDA_CIK, first, form="4")
    _filing(conn, "0001045810-26-000202", NVDA_CIK, second, form="4")
    _form4_row(conn, "0001045810-26-000201", 1, "P", first)
    _form4_row(conn, "0001045810-26-000202", 1, "P", second)
    _form4_row(conn, "0001045810-26-000202", 2, "S", second)

    seen = AsOf(conn, second).form4_transactions(codes=["P"])
    assert [(r.accession_no, r.line_no) for r in seen] == [("0001045810-26-000201", 1)]

    later = AsOf(conn, second + timedelta(seconds=1))
    assert [(r.accession_no, r.transaction_code) for r in later.form4_transactions(issuer_cik=NVDA_CIK)] == [
        ("0001045810-26-000201", "P"),
        ("0001045810-26-000202", "P"),
        ("0001045810-26-000202", "S"),
    ]
    assert len(later.form4_transactions(since=second)) == 2


# --- XBRL values ----------------------------------------------------------------------------------


def test_a_restated_value_replaces_the_original_only_once_public(connect_app_db):
    conn = connect_app_db()
    ten_q = datetime(2026, 5, 28, 20, 30, tzinfo=UTC)
    ten_k = datetime(2027, 2, 25, 21, 0, tzinfo=UTC)
    _xbrl(conn, "0001045810-26-000050", 44_062_000_000, ten_q, form="10-Q")
    _xbrl(conn, "0001045810-27-000010", 43_900_000_000, ten_k, form="10-K")  # restated in the annual report

    assert AsOf(conn, ten_q).latest_xbrl_values(NVDA_CIK, "Revenues") == []

    [before_restatement] = AsOf(conn, ten_k).latest_xbrl_values(NVDA_CIK, "Revenues", unit="USD")
    assert before_restatement.value == Decimal(44_062_000_000)
    assert before_restatement.form == "10-Q"

    reader = AsOf(conn, ten_k + timedelta(seconds=1))
    [after_restatement] = reader.latest_xbrl_values(NVDA_CIK, "Revenues")
    assert after_restatement.value == Decimal(43_900_000_000)
    assert [f.form for f in reader.xbrl_facts(NVDA_CIK, "Revenues")] == ["10-Q", "10-K"]


def test_several_concepts_are_read_at_once_with_their_filing_labels(connect_app_db):
    conn = connect_app_db()
    filed = datetime(2026, 5, 28, 20, 30, tzinfo=UTC)
    _xbrl(conn, "0001045810-26-000050", 44_062_000_000, filed, form="10-Q")
    with conn.cursor() as cursor:
        cursor.execute("UPDATE xbrl_facts SET fiscal_year = 2027, fiscal_period = 'Q1'")
    conn.commit()

    [fact] = AsOf(conn, filed + timedelta(seconds=1)).xbrl_facts(NVDA_CIK, "Revenues", "NetIncomeLoss")
    assert (fact.concept, fact.fiscal_year, fact.fiscal_period) == ("Revenues", 2027, "Q1")
    with pytest.raises(ValueError, match="at least one concept"):
        AsOf(conn, filed).xbrl_facts(NVDA_CIK)


# --- every reader, every moment -------------------------------------------------------------------


def test_no_reader_ever_returns_a_row_known_at_or_after_as_of(connect_app_db):
    conn = connect_app_db()
    start = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
    for day in range(10):
        moment = start + timedelta(days=day, minutes=37 * day)
        accession = f"0001045810-26-{300 + day:06d}"
        _filing(conn, accession, NVDA_CIK, moment, form="4")
        _form4_row(conn, accession, 1, "P", moment)
        _xbrl(conn, accession, 1_000 + day, moment, form="10-Q")
        _bar(conn, "NVDA", (start + timedelta(days=day)).date(), str(100 + day))

    for hours in range(0, 24 * 11, 5):
        as_of = start + timedelta(hours=hours)
        reader = AsOf(conn, as_of)
        known = (
            [b.known_at for b in reader.bars("NVDA")]
            + [f.known_at for f in reader.filings()]
            + [f.known_at for f in reader.filings(cik=NVDA_CIK)]
            + [r.known_at for r in reader.form4_transactions()]
            + [x.known_at for x in reader.xbrl_facts(NVDA_CIK, "Revenues")]
            + [x.known_at for x in reader.latest_xbrl_values(NVDA_CIK, "Revenues")]
            + [x.known_at for x in reader.xbrl_facts(NVDA_CIK, "Revenues", "NetIncomeLoss", "GrossProfit")]
        )
        assert all(k < as_of for k in known), as_of


# --- research code goes through this layer --------------------------------------------------------

_DIRECT_READ = re.compile(r"\b(?:FROM|JOIN)\s+(?:sentinel_stream\.)?(" + "|".join(FACT_TABLES) + r")\b", re.I)


def _string_constants(path: Path) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_research_modules_do_not_read_fact_tables_directly():
    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in sorted(RESEARCH_DIR.rglob("*.py"))
        if path.name != "asof.py"
        for text in _string_constants(path)
        for match in _DIRECT_READ.finditer(text)
    ]
    assert offenders == []


def test_the_direct_read_check_catches_a_query():
    assert _DIRECT_READ.search("select close from price_bars where trading_date = %s")
    assert _DIRECT_READ.search("SELECT * FROM sentinel_stream.filings f JOIN filing_issuers fi ON true")
    assert not _DIRECT_READ.search("Read price bars through AsOf.bars")
