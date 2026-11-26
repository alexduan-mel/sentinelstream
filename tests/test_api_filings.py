from __future__ import annotations

import pytest

from api.filings import filing_items
from filings.edgar import sec_index_url
from filings.form_8k import ITEM_TITLES, item_title

NVDA_CIK = "0001045810"
AMZN_CIK = "0001018724"
UNKNOWN_CIK = "0000000001"
EARNINGS_REASON = "Item 2.02 Results of Operations and Financial Condition"
PURCHASE_REASON = "2 open-market purchases by Jassy Andrew R (Director)"


def test_8k_items_have_sec_titles_and_other_forms_do_not() -> None:
    assert item_title("5.02").startswith("Departure of Directors or Certain Officers")
    assert item_title(" 2.02 ") == "Results of Operations and Financial Condition"
    assert item_title("9.99") is None
    assert all(len(number) == 4 and number[1] == "." for number in ITEM_TITLES)

    assert [item.model_dump() for item in filing_items("8-K/A", ["4.02", "9.99"])] == [
        {"number": "4.02", "title": ITEM_TITLES["4.02"]},
        {"number": "9.99", "title": None},
    ]
    assert [item.title for item in filing_items("1-U", ["1.01"])] == [None]


def test_sec_index_url_drops_cik_zeros_and_accession_dashes() -> None:
    assert sec_index_url(NVDA_CIK, "0001045810-26-000123") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/0001045810-26-000123-index.htm"
    )


def _seed(connect_app_db) -> None:
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute("UPDATE instruments SET cik = %s WHERE symbol = 'NVDA'", (NVDA_CIK,))
        cursor.execute("UPDATE instruments SET cik = %s WHERE symbol = 'AMZN'", (AMZN_CIK,))
        # A second, inactive symbol on NVDA's CIK must not displace the watchlist ticker.
        cursor.execute(
            "INSERT INTO instruments (symbol, asset_type, role, cik, active) "
            "VALUES ('AAAA', 'equity', 'watchlist', %s, FALSE)",
            (NVDA_CIK,),
        )
        rows = [
            # accession, cik, form, items, accepted (UTC)
            ("0001045810-26-000001", NVDA_CIK, "8-K", ["2.02", "9.01"], "2026-11-20 21:05:00+00"),
            ("0001018724-26-000002", AMZN_CIK, "4", [], "2026-11-21 04:30:00+00"),  # Nov 20 23:30 ET
            ("0001018724-26-000003", AMZN_CIK, "10-Q", [], "2026-11-21 15:00:00+00"),
            ("0000000001-26-000004", UNKNOWN_CIK, "8-K", ["8.01"], "2026-11-22 13:00:00+00"),
        ]
        for accession, cik, form, items, accepted in rows:
            cursor.execute(
                """
                INSERT INTO filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at)
                VALUES (%s, %s, %s, %s, %s::timestamptz::date, %s, %s)
                """,
                (accession, cik, form, items, accepted, accepted, accepted),
            )
            cursor.execute(
                "INSERT INTO filing_issuers (accession_no, cik, is_backfill) VALUES (%s, %s, FALSE)", (accession, cik)
            )
        # NVDA's 8-K is also listed under AMZN, so it belongs to both.
        cursor.execute(
            "INSERT INTO filing_issuers (accession_no, cik, is_backfill) VALUES ('0001045810-26-000001', %s, FALSE)",
            (AMZN_CIK,),
        )
        # Materiality per link. The unknown issuer's 8-K stays unclassified.
        for accession, cik, level, reason in (
            ("0001045810-26-000001", NVDA_CIK, "high", EARNINGS_REASON),
            ("0001045810-26-000001", AMZN_CIK, "low", "8-K without a high item"),
            ("0001018724-26-000002", AMZN_CIK, "high", PURCHASE_REASON),
            ("0001018724-26-000003", AMZN_CIK, "low", "10-Q: not pushed in v0"),
        ):
            cursor.execute(
                "UPDATE filing_issuers SET materiality = %s, materiality_reason = %s, classified_at = NOW() "
                "WHERE accession_no = %s AND cik = %s",
                (level, reason, accession, cik),
            )
        # Two purchases count; the sale does not.
        for line, code, shares, disposed in ((1, "P", 1000, "A"), (2, "P", 400.5, "A"), (3, "S", 9999, "D")):
            cursor.execute(
                "INSERT INTO form4_transactions (accession_no, line_no, issuer_cik, reporting_owners, owner_roles, "
                "transaction_code, acquired_disposed, shares, known_at) "
                "VALUES ('0001018724-26-000002', %s, %s, 'Jassy Andrew R', 'Director', %s, %s, %s, "
                "'2026-11-21 04:30:00+00')",
                (line, AMZN_CIK, code, disposed, shares),
            )
        cursor.execute(
            "INSERT INTO alert_deliveries (accession_no, channel, status, attempts, delivered_at) "
            "VALUES ('0001018724-26-000002', 'slack', 'delivered', 1, '2026-11-21 04:34:10+00')"
        )
        cursor.execute(
            "INSERT INTO alert_deliveries (accession_no, channel, status, skip_reason) "
            "VALUES ('0001045810-26-000001', 'slack', 'skipped', %s)",
            ("classified before the Slack notifier was switched on",),
        )
    conn.commit()


def _accessions(page: dict) -> list[str]:
    return [item["accession_no"] for item in page["items"]]


def test_filings_need_a_session(api_client) -> None:
    assert api_client.get("/api/filings").status_code == 401
    assert api_client.get("/api/filings/facets").status_code == 401


def test_filings_are_listed_newest_first_with_ticker_items_and_link(signed_in_client, connect_app_db) -> None:
    _seed(connect_app_db)

    page = signed_in_client.get("/api/filings").json()

    assert page["total"] == 4
    assert _accessions(page) == [
        "0000000001-26-000004",
        "0001018724-26-000003",
        "0001018724-26-000002",
        "0001045810-26-000001",
    ]
    unknown_issuer, _, form4, earnings = page["items"]
    assert unknown_issuer["tickers"] == []
    assert (unknown_issuer["materiality"], unknown_issuer["materiality_reason"], unknown_issuer["slack"]) == (
        None,
        None,
        None,
    )
    assert form4["tickers"] == ["AMZN"] and form4["items"] == []
    assert (form4["materiality"], form4["materiality_reason"], form4["purchase_shares"]) == (
        "high",
        PURCHASE_REASON,
        1400.5,
    )
    assert form4["slack"] == {"status": "delivered", "delivered_at": "2026-11-21T04:34:10Z", "detail": None}
    assert earnings == {
        "accession_no": "0001045810-26-000001",
        "cik": NVDA_CIK,
        "tickers": ["AMZN", "NVDA"],
        "form_type": "8-K",
        "items": [
            {"number": "2.02", "title": "Results of Operations and Financial Condition"},
            {"number": "9.01", "title": "Financial Statements and Exhibits"},
        ],
        "accepted_at": "2026-11-20T21:05:00Z",
        "filed_date": "2026-11-20",
        "sec_url": sec_index_url(NVDA_CIK, "0001045810-26-000001"),
        # High for NVDA, low for AMZN: high wins, with NVDA's reason.
        "materiality": "high",
        "materiality_reason": EARNINGS_REASON,
        "purchase_shares": None,
        "slack": {
            "status": "skipped",
            "delivered_at": None,
            "detail": "classified before the Slack notifier was switched on",
        },
    }


def test_filings_filter_by_ticker_form_and_eastern_date(signed_in_client, connect_app_db) -> None:
    _seed(connect_app_db)

    def get(**params) -> dict:
        response = signed_in_client.get("/api/filings", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    assert _accessions(get(ticker=["amzn"])) == [
        "0001018724-26-000003",
        "0001018724-26-000002",
        "0001045810-26-000001",  # linked to AMZN as well as NVDA
    ]
    assert _accessions(get(ticker=["NVDA"])) == ["0001045810-26-000001"]
    assert get(ticker=["NVDA", "AMZN"])["total"] == 3
    assert get(ticker=["ZZZZ"]) == {"items": [], "total": 0}
    assert _accessions(get(form_type=["8-K", "4"])) == [
        "0000000001-26-000004",
        "0001018724-26-000002",
        "0001045810-26-000001",
    ]
    # 04:30 UTC on Nov 21 is 23:30 ET on Nov 20, so it belongs to Nov 20.
    assert _accessions(get(accepted_from="2026-11-20", accepted_to="2026-11-20")) == [
        "0001018724-26-000002",
        "0001045810-26-000001",
    ]
    assert _accessions(get(accepted_from="2026-11-21")) == ["0000000001-26-000004", "0001018724-26-000003"]

    assert _accessions(get(materiality="high")) == ["0001018724-26-000002", "0001045810-26-000001"]
    # Low means every link classified and none high, so the unclassified 8-K is in neither.
    assert _accessions(get(materiality="low")) == ["0001018724-26-000003"]
    assert get(materiality="high", ticker=["NVDA"])["total"] == 1

    paged = get(limit=1, offset=1)
    assert _accessions(paged) == ["0001018724-26-000003"] and paged["total"] == 4
    assert get(limit=10, offset=10) == {"items": [], "total": 4}


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"accepted_from": "nope"},
        {"accepted_from": "2026-11-22", "accepted_to": "2026-11-21"},
        {"materiality": "medium"},
    ],
)
def test_filings_reject_bad_parameters(signed_in_client, params) -> None:
    assert signed_in_client.get("/api/filings", params=params).status_code == 422


def test_facets_list_resolved_watchlist_tickers_and_stored_forms(signed_in_client, connect_app_db) -> None:
    assert signed_in_client.get("/api/filings/facets").json() == {"tickers": [], "form_types": []}

    _seed(connect_app_db)

    assert signed_in_client.get("/api/filings/facets").json() == {
        "tickers": ["AMZN", "NVDA"],
        "form_types": ["10-Q", "4", "8-K"],
    }
