"""The watchlist API: list, add, remove and retry. Adding checks the symbol with SEC (a fake here),
stores only real tickers, and queues the loading for the worker (ground rule 4)."""

from __future__ import annotations

import httpx
import pytest

from api import instruments
from tests.test_sec_ingestion import TICKER_MAP, FakeSec, _filing, _submissions

MSFT_CIK = "0000789019"


@pytest.fixture(autouse=True)
def fake_sec(monkeypatch) -> FakeSec:
    """SEC as the add endpoint sees it: the shared fake, plus MSFT."""
    fake = FakeSec()
    fake.routes["/files/company_tickers_exchange.json"] = {
        "fields": TICKER_MAP["fields"],
        "data": [*TICKER_MAP["data"], [789019, "MICROSOFT CORP", "MSFT", "Nasdaq"]],
    }
    fake.routes[f"/submissions/CIK{MSFT_CIK}.json"] = _submissions(
        "MICROSOFT CORP", ["MSFT"], [_filing("0000950170-26-000001", "10-Q", "2026-10-29T20:05:00.000Z",
                                             filed="2026-10-29")]
    )  # fmt: skip
    monkeypatch.setattr(instruments, "sec_client", fake.client)
    instruments.TICKER_MAP.clear()
    yield fake
    instruments.TICKER_MAP.clear()


def _queued(connect_app_db) -> list[tuple]:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT task_id, status, tickers, force_refresh, requested_by FROM task_runs ORDER BY id")
        rows = cursor.fetchall()
    conn.close()
    return rows


def _row(connect_app_db, symbol: str) -> tuple | None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("SELECT active, onboarding_status, role FROM instruments WHERE symbol = %s", (symbol,))
        row = cursor.fetchone()
    conn.close()
    return row


def test_instruments_need_a_session(api_client) -> None:
    assert api_client.get("/api/instruments").status_code == 401
    assert api_client.post("/api/instruments", json={"symbol": "MSFT"}).status_code == 401
    assert api_client.delete("/api/instruments/NVDA").status_code == 401


def test_list_shows_the_watchlist_then_benchmarks_with_their_coverage(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET cik = '0001045810', name = 'NVIDIA CORP', filer_type = 'domestic', "
            "onboarding_status = 'ready' WHERE symbol = 'NVDA'"
        )
        cursor.execute(
            "INSERT INTO filings (accession_no, cik, form_type, filed_date, accepted_at, known_at) VALUES "
            "('0001045810-26-000001', '0001045810', '8-K', '2026-11-20', '2026-11-20 21:05+00', '2026-11-20 21:05+00')"
        )
        cursor.execute(
            "INSERT INTO filing_issuers (accession_no, cik, is_backfill) "
            "VALUES ('0001045810-26-000001', '0001045810', TRUE)"
        )
        cursor.execute(
            "INSERT INTO price_bars (instrument_id, trading_date, close, provider) "
            "SELECT id, d, 100, 'massive' FROM instruments, (VALUES (DATE '2024-11-25'), (DATE '2026-11-25')) v (d) "
            "WHERE symbol = 'QQQ'"
        )
    conn.commit()

    items = signed_in_client.get("/api/instruments").json()

    assert [item["symbol"] for item in items] == ["AMZN", "ANT", "GOOGL", "NBIS", "NVDA", "SPCX", "QQQ", "SPY"]
    nvda = next(item for item in items if item["symbol"] == "NVDA")
    assert nvda == {
        "symbol": "NVDA",
        "name": "NVIDIA CORP",
        "asset_type": "equity",
        "role": "watchlist",
        "cik": "0001045810",
        "filer_type": "domestic",
        "onboarding_status": "ready",
        "onboarding_error": None,
        "last_filing": {"form_type": "8-K", "accepted_at": "2026-11-20T21:05:00Z"},
        "prices": None,
    }
    qqq = next(item for item in items if item["symbol"] == "QQQ")
    assert qqq["role"] == "benchmark"
    assert qqq["prices"] == {"first": "2024-11-25", "last": "2026-11-25", "bars": 2}


def test_adding_a_ticker_stores_it_with_its_identity_and_queues_onboarding(signed_in_client, connect_app_db) -> None:
    response = signed_in_client.post("/api/instruments", json={"symbol": " msft "})

    assert response.status_code == 202
    body = response.json()
    assert (body["symbol"], body["onboarding_status"]) == ("MSFT", "pending")
    assert _row(connect_app_db, "MSFT") == (True, "pending", "watchlist")
    msft = next(item for item in signed_in_client.get("/api/instruments").json() if item["symbol"] == "MSFT")
    assert (msft["name"], msft["cik"], msft["filer_type"]) == ("MICROSOFT CORP", MSFT_CIK, "domestic")
    assert _queued(connect_app_db) == [("onboard_tickers", "pending", ["MSFT"], False, "web:admin")]

    again = signed_in_client.post("/api/instruments", json={"symbol": "MSFT"})
    assert again.status_code == 409
    assert again.json()["detail"] == "MSFT is already on the watchlist"


@pytest.mark.parametrize("symbol", ["", "1ABC", "TOO-LONG-SYMB", "AB CD", "NVDA;"])
def test_bad_symbols_are_rejected(signed_in_client, symbol: str) -> None:
    assert signed_in_client.post("/api/instruments", json={"symbol": symbol}).status_code == 422


@pytest.mark.parametrize(
    ("symbol", "detail"),
    [
        ("XYZQ", "XYZQ: not found on any US exchange"),
        ("ANT", "ANT: CIK 0009999999 for ANT files as a fund (485BPOS), not a company"),  # the fake's fund
    ],
)
def test_a_symbol_that_is_not_a_listed_company_is_refused_and_not_stored(
    signed_in_client, connect_app_db, symbol: str, detail: str
) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM instruments WHERE symbol = 'ANT'")  # so the fund check is reached
    conn.commit()

    response = signed_in_client.post("/api/instruments", json={"symbol": symbol})

    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert _row(connect_app_db, symbol) is None
    assert _queued(connect_app_db) == []


def test_nothing_is_added_while_sec_cannot_be_reached(signed_in_client, connect_app_db, fake_sec) -> None:
    fake_sec.routes["/files/company_tickers_exchange.json"] = httpx.Response(503)

    response = signed_in_client.post("/api/instruments", json={"symbol": "MSFT"})

    assert response.status_code == 503
    assert response.json()["detail"] == "Could not reach SEC to check MSFT. Try again in a minute."
    assert _row(connect_app_db, "MSFT") is None


def test_the_ticker_map_is_fetched_once_and_reused(signed_in_client, fake_sec) -> None:
    assert signed_in_client.post("/api/instruments", json={"symbol": "MSFT"}).status_code == 202
    assert signed_in_client.post("/api/instruments", json={"symbol": "XYZQ"}).status_code == 422
    assert fake_sec.paths().count("/files/company_tickers_exchange.json") == 1


def test_duplicates_and_benchmarks_are_refused_before_asking_sec(signed_in_client, fake_sec) -> None:
    assert signed_in_client.post("/api/instruments", json={"symbol": "NVDA"}).status_code == 409
    assert signed_in_client.post("/api/instruments", json={"symbol": "SPY"}).status_code == 409
    assert fake_sec.requests == []


def test_benchmarks_cannot_be_added_or_removed(signed_in_client) -> None:
    assert signed_in_client.post("/api/instruments", json={"symbol": "QQQ"}).status_code == 409
    removed = signed_in_client.delete("/api/instruments/SPY")
    assert removed.status_code == 409
    assert "benchmark" in removed.json()["detail"]


def test_removing_keeps_history_and_adding_back_refreshes_identity(signed_in_client, connect_app_db) -> None:
    assert signed_in_client.delete("/api/instruments/nvda").status_code == 204
    assert _row(connect_app_db, "NVDA")[0] is False
    assert signed_in_client.delete("/api/instruments/NVDA").status_code == 404
    assert "NVDA" not in [item["symbol"] for item in signed_in_client.get("/api/instruments").json()]

    back = signed_in_client.post("/api/instruments", json={"symbol": "NVDA"})
    assert back.status_code == 202
    assert _row(connect_app_db, "NVDA") == (True, "pending", "watchlist")
    assert _queued(connect_app_db)[-1][2:4] == (["NVDA"], False)  # identity checked on the way back in


def test_retrying_a_failed_ticker_queues_a_fresh_lookup(signed_in_client, connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(
            "UPDATE instruments SET onboarding_status = 'failed', onboarding_error = 'not found' WHERE symbol = 'SPCX'"
        )
    conn.commit()

    response = signed_in_client.post("/api/instruments/SPCX/onboard")

    assert response.status_code == 202
    assert response.json()["onboarding_status"] == "pending"
    assert _row(connect_app_db, "SPCX")[1] == "pending"
    assert _queued(connect_app_db)[-1][2:4] == (["SPCX"], True)
    assert signed_in_client.post("/api/instruments/QQQ/onboard").status_code == 404
    assert signed_in_client.post("/api/instruments/ZZZZ/onboard").status_code == 404
