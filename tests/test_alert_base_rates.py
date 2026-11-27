"""Base rates for alerts: the daily calculation, choosing industry or market, and the Alert detail API."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from alerts.base_rate import MIN_INDUSTRY_EVENTS, alert_kinds, base_rates_for, history_line
from orchestration.task_runs import TaskRun, enqueue_run
from research.base_rates import compute_base_rates, industry_scope
from tests.test_base_rates import AFTER_ALL, SESSIONS, E, _bars, _market, _purchase, _rising

NVDA_CIK = "0001045810"
AS_OF = datetime(2026, 11, 27, 11, 0, tzinfo=UTC)


def _issuer(conn, cik: str, sic: str | None, description: str | None = None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO market_issuers (cik, name, latest_8k_date, tickers, exchanges, sic, sic_description) "
            "VALUES (%s, 'Company', '2026-01-01', '{}', '{}', %s, %s) "
            "ON CONFLICT (cik) DO UPDATE SET sic = EXCLUDED.sic, sic_description = EXCLUDED.sic_description",
            (cik, sic, description),
        )
    conn.commit()


def _rate(conn, kind: str, key: str, scope: str, n: int, *, mean: float = 0.01, variant: str = "main") -> None:
    with conn.cursor() as cursor:
        for horizon in (1, 5, 20, 60):
            cursor.execute(
                "INSERT INTO base_rates (event_kind, event_key, scope, variant, horizon, n, mean, median, hit_rate, "
                "ci_low, ci_high, months, mean_gross_return, mean_benchmark_return, mean_cost, "
                "mean_excess_vs_reference, ended_early, as_of) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 0.005, 0.55, -0.01, 0.03, 12, 0.02, 0.01, 0.002, 0.012, 3, %s)",
                (kind, key, scope, variant, horizon, n, mean, AS_OF),
            )
    conn.commit()


def _alerts_run(conn) -> TaskRun:
    run_id = enqueue_run(conn, "compute_base_rates", trigger_source="test", run_mode="manual", requested_by="pytest")
    conn.commit()
    return TaskRun(run_id, "compute_base_rates", "t", "test", "manual", "pytest", [], None, False, {"event": "alerts"})


def _count(conn) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM base_rates")
        (count,) = cursor.fetchone()
    conn.commit()
    return count


# --- kinds and scopes ------------------------------------------------------------------------------


def test_alert_kinds_follow_the_materiality_rules() -> None:
    assert [k[:2] for k in alert_kinds("8-K", ["5.02", "2.02", "9.01"])] == [("8k_item", "2.02"), ("8k_item", "5.02")]
    assert alert_kinds("8-K", ["2.02"])[0][2] == "8-K item 2.02 Results of Operations and Financial Condition"
    assert alert_kinds("4", []) == [("insider_purchase", "", "Insider open-market purchase")]
    assert alert_kinds("10-Q", []) == [] and alert_kinds("8-K", ["8.01"]) == []
    assert (industry_scope("3674"), industry_scope(None), industry_scope("")) == ("sic2:36", None, None)


# --- the daily calculation -------------------------------------------------------------------------


def test_the_daily_run_stores_each_kind_by_market_and_industry_and_replaces_them(connect_app_db) -> None:
    conn = connect_app_db()
    _market(conn)
    _bars(conn, "AAA", _rising(20.0))
    _bars(conn, "BBB", _rising(30.0))
    _purchase(conn, "AAA", "0000000001")
    _purchase(conn, "BBB", "0000000002")
    _issuer(conn, "0000000001", "3674", "Semiconductors & Related Devices")
    _issuer(conn, "0000000002", "7372", "Services-Prepackaged Software")
    with conn.cursor() as cursor:
        cursor.execute("UPDATE market_issuers SET tickers = '{AAA}', exchanges = '{Nasdaq}' WHERE cik = '0000000001'")
        cursor.execute(
            "INSERT INTO market_8k_filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at) "
            "VALUES ('0000000001-26-000900', '0000000001', '8-K', '{2.02,9.01}', %s, %s, %s)",
            (SESSIONS[E], *(datetime.combine(SESSIONS[E], datetime.min.time(), tzinfo=UTC),) * 2),
        )
    conn.commit()

    result = compute_base_rates(_alerts_run(conn), conn, now=AFTER_ALL)

    assert result.status == "succeeded"
    kinds = result.meta["kinds"]
    assert kinds["insider_purchase:"]["events"] == 2 and kinds["insider_purchase:"]["main_n"]["5"] == 2
    assert kinds["8k_item:2.02"]["events"] == 1 and kinds["8k_item:4.02"]["events"] == 0
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT event_kind, event_key, scope, n FROM base_rates "
            "WHERE variant = 'main' AND horizon = 5 ORDER BY 1, 2, 3"
        )
        assert cursor.fetchall() == [
            ("8k_item", "2.02", "market", 1),
            ("8k_item", "2.02", "sic2:36", 1),
            ("insider_purchase", "", "market", 2),
            ("insider_purchase", "", "sic2:36", 1),
            ("insider_purchase", "", "sic2:73", 1),
        ]
    stored = _count(conn)
    compute_base_rates(_alerts_run(conn), conn, now=AFTER_ALL)
    assert _count(conn) == stored  # replaced, not added to


# --- choosing what an alert shows ------------------------------------------------------------------


def test_an_alert_uses_its_industry_when_it_has_enough_events_otherwise_the_market(connect_app_db) -> None:
    conn = connect_app_db()
    _issuer(conn, NVDA_CIK, "3674", "Semiconductors & Related Devices")
    _rate(conn, "8k_item", "2.02", "market", 4000, mean=0.002)
    _rate(conn, "8k_item", "2.02", "sic2:36", MIN_INDUSTRY_EVENTS - 1, mean=0.03)

    [pooled] = base_rates_for(conn, form_type="8-K", items=["2.02", "9.01"], cik=NVDA_CIK)
    assert (pooled.scope, pooled.headline()["n"], pooled.headline()["mean"]) == ("market", 4000, 0.002)
    assert pooled.scope_label == (
        "Based on past filings from all US-listed companies "
        f"(fewer than {MIN_INDUSTRY_EVENTS} in this company's industry, so pooled)"
    )
    assert pooled.variants["none"]["20"] is None  # only main rows stored here
    assert pooled.as_of == AS_OF

    with conn.cursor() as cursor:
        cursor.execute("UPDATE base_rates SET n = %s WHERE scope = 'sic2:36'", (MIN_INDUSTRY_EVENTS,))
    conn.commit()
    [industry] = base_rates_for(conn, form_type="8-K", items=["2.02"], cik=NVDA_CIK)
    assert (industry.scope, industry.headline()["mean"]) == ("industry", 0.03)
    assert industry.scope_label == (
        "Based on past filings from companies in the same broad industry "
        "(SIC group 36, which includes Semiconductors & Related Devices)"
    )

    assert base_rates_for(conn, form_type="8-K", items=["5.02"], cik=NVDA_CIK) == []  # none stored
    assert base_rates_for(conn, form_type="4", items=[], cik="0000999999") == []
    _rate(conn, "insider_purchase", "", "market", 900)
    [unknown_industry] = base_rates_for(conn, form_type="4", items=[], cik="0000999999")
    assert unknown_industry.scope_label == "Based on past filings from all US-listed companies"


# --- the API ---------------------------------------------------------------------------------------


def _filing(conn, accession: str, form: str, items: list[str], level: str) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at) "
            "VALUES (%s, %s, %s, %s, '2026-11-20', '2026-11-20 21:05:00+00', '2026-11-20 21:05:00+00')",
            (accession, NVDA_CIK, form, items),
        )
        cursor.execute(
            "INSERT INTO filing_issuers (accession_no, cik, is_backfill, materiality, materiality_reason, "
            "classified_at) VALUES (%s, %s, FALSE, %s, 'reason', NOW())",
            (accession, NVDA_CIK, level),
        )
    conn.commit()


@pytest.fixture
def seeded(connect_app_db):
    conn = connect_app_db()
    _issuer(conn, NVDA_CIK, "3674", "Semiconductors & Related Devices")
    _rate(conn, "8k_item", "2.02", "market", 4000)
    _filing(conn, "0001045810-26-000001", "8-K", ["2.02", "9.01"], "high")
    _filing(conn, "0001045810-26-000002", "8-K", ["8.01"], "low")
    return conn


def test_the_alert_detail_carries_its_base_rate(seeded, signed_in_client) -> None:
    body = signed_in_client.get("/api/filings/0001045810-26-000001").json()
    [rate] = body["base_rates"]
    assert (rate["event_kind"], rate["event_key"], rate["scope"]) == ("8k_item", "2.02", "market")
    assert rate["label"].startswith("8-K item 2.02")
    assert rate["variants"]["main"]["20"]["n"] == 4000
    assert rate["variants"]["main"]["20"]["hit_rate"] == 0.55
    assert rate["variants"]["none"]["20"] is None

    assert signed_in_client.get("/api/filings/0001045810-26-000002").json()["base_rates"] == []


# --- the Slack line --------------------------------------------------------------------------------


def test_history_line_describes_what_followed_without_advice(connect_app_db) -> None:
    conn = connect_app_db()
    _issuer(conn, NVDA_CIK, "3674", "Semiconductors & Related Devices")
    _rate(conn, "8k_item", "2.02", "sic2:36", 120, mean=-0.018)
    _rate(conn, "insider_purchase", "", "market", 1977, mean=0.0085)
    with conn.cursor() as cursor:
        cursor.execute("UPDATE base_rates SET ci_low = NULL, ci_high = NULL WHERE event_kind = 'insider_purchase'")
    conn.commit()

    [results] = base_rates_for(conn, form_type="8-K", items=["2.02"], cik=NVDA_CIK)
    assert history_line(results) == (
        "History: after 120 past 8-Ks with item 2.02 in the same broad industry, the next 20 sessions averaged "
        "-1.8% vs QQQ after costs; 55% beat QQQ (95% range -1.0% to +3.0%)."
    )
    [purchase] = base_rates_for(conn, form_type="4", items=[], cik=NVDA_CIK)
    assert history_line(purchase) == (
        "History: after 1,977 past insider purchases across all US-listed companies, the next 20 sessions "
        "averaged +0.9% vs QQQ after costs; 55% beat QQQ."
    )
    no_twenty = dataclasses.replace(purchase, variants={"main": {"20": None}, "none": {}})
    assert history_line(no_twenty) is None
