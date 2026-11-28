"""The Journal ledger and its API, against Postgres (ADR 0025): entries follow a position, the time is
the server's, and nothing is ever edited in place."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg2
import pytest

from ledger import journal
from ledger.journal import Step, sequence_error

NVDA_CIK = "0001045810"
EARNINGS_8K = "0001045810-26-000101"
OTHER_8K = "0001045810-26-000102"
AMZN_8K = "0001018724-26-000050"


@pytest.fixture
def journal_db(connect_app_db):
    """NVDA ready with two 8-K alerts, AMZN ready with one, SPCX still pending."""
    conn = connect_app_db()
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE instruments SET onboarding_status = 'ready', filer_type = 'domestic',
                   cik = CASE symbol WHEN 'NVDA' THEN %s ELSE '0001018724' END
            WHERE symbol IN ('NVDA', 'AMZN')
            """,
            (NVDA_CIK,),
        )
        for accession, cik in ((EARNINGS_8K, NVDA_CIK), (OTHER_8K, NVDA_CIK), (AMZN_8K, "0001018724")):
            cursor.execute(
                """
                INSERT INTO filings (accession_no, cik, form_type, items, filed_date, accepted_at, known_at)
                VALUES (%s, %s, '8-K', '{2.02}', '2026-11-19', '2026-11-19 21:20:00+00', '2026-11-19 21:20:00+00')
                """,
                (accession, cik),
            )
            cursor.execute(
                "INSERT INTO filing_issuers (accession_no, cik, is_backfill) VALUES (%s, %s, FALSE)", (accession, cik)
            )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def nvda_bars(journal_db):
    """NVDA's split-adjusted bar for Fri 20 Nov 2026, and nothing on Wed 18 Nov."""
    with journal_db.cursor() as cursor:
        cursor.execute("""
            INSERT INTO price_bars (instrument_id, trading_date, open, high, low, close, provider)
            SELECT id, '2026-11-20', 182, 190, 180, 186, 'massive' FROM instruments WHERE symbol = 'NVDA'
            """)
    journal_db.commit()
    return journal_db


def _log(client, **body):
    return client.post("/api/journal", json={"symbol": "NVDA", "thesis": "Data-centre demand holds.", **body})


def _steps(*actions: str) -> list[Step]:
    return [Step(action) for action in actions]


def test_sequence_rules() -> None:
    assert sequence_error("NVDA", _steps("pass", "opened", "added", "trimmed", "pass", "closed", "opened")) is None
    assert "already has an open position" in sequence_error("NVDA", _steps("opened", "opened"))
    assert "no open position to mark Added" in sequence_error("NVDA", _steps("added"))
    assert "no open position to mark Closed" in sequence_error("NVDA", _steps("opened", "closed", "closed"))


def test_share_counts_are_followed_while_every_entry_gives_one() -> None:
    def run(*steps: tuple[str, float | None]) -> str | None:
        return sequence_error("NVDA", [Step(action, shares=shares) for action, shares in steps])

    assert run(("opened", 10), ("added", 5), ("trimmed", 14), ("closed", 1)) is None
    assert "position is 15 shares; Trimmed must leave some" in run(("opened", 10), ("added", 5), ("trimmed", 15))
    assert "position is 10 shares; Closed sells all of it" in run(("opened", 10), ("closed", 4))
    # A count missing anywhere since Opened means the position's size is unknown, so nothing is checked.
    assert run(("opened", 10), ("added", None), ("trimmed", 50), ("closed", 3)) is None
    assert run(("opened", None), ("closed", 7)) is None


def test_journal_needs_a_session(api_client) -> None:
    assert api_client.get("/api/journal").status_code == 401
    assert api_client.post("/api/journal", json={}).status_code == 401
    assert api_client.post("/api/journal/1/corrections", json={}).status_code == 401


def test_log_an_entry_from_an_alert(signed_in_client, journal_db) -> None:
    before = datetime.now(UTC)
    response = _log(signed_in_client, action="opened", accession_no=EARNINGS_8K, thesis="  Margins hold.  ")
    assert response.status_code == 201, response.text
    entry = response.json()

    assert (entry["symbol"], entry["action"], entry["thesis"], entry["recorded_by"]) == (
        "NVDA", "opened", "Margins hold.", "admin"
    )  # fmt: skip
    assert entry["source"] == {
        "accession_no": EARNINGS_8K,
        "form_type": "8-K",
        "accepted_at": "2026-11-19T21:20:00Z",
        "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/0001045810-26-000101-index.htm",
    }
    assert entry["original_source"] == entry["source"]
    assert entry["withdrawn"] is None and entry["corrections"] == []
    logged = datetime.fromisoformat(entry["known_at"])
    assert before - timedelta(seconds=5) <= logged <= datetime.now(UTC) + timedelta(seconds=5)

    journal = signed_in_client.get("/api/journal").json()
    assert [e["id"] for e in journal["entries"]] == [entry["id"]]
    assert journal["open_positions"] == ["NVDA"]


def test_the_time_is_the_servers_never_the_requests(signed_in_client, journal_db) -> None:
    entry = _log(signed_in_client, action="pass", known_at="2020-01-02T15:00:00Z").json()
    assert datetime.fromisoformat(entry["known_at"]).year == datetime.now(UTC).year


def test_actions_follow_a_position(signed_in_client, journal_db) -> None:
    refused = _log(signed_in_client, action="added")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "NVDA has no open position to mark Added; log Opened first"

    for action in ("pass", "opened", "added", "trimmed"):
        assert _log(signed_in_client, action=action).status_code == 201
    assert _log(signed_in_client, action="opened").status_code == 409
    assert _log(signed_in_client, action="closed").status_code == 201
    assert signed_in_client.get("/api/journal").json()["open_positions"] == []

    # Another ticker's position is its own.
    assert _log(signed_in_client, symbol="AMZN", action="opened").status_code == 201
    journal = signed_in_client.get("/api/journal", params={"symbol": "nvda"}).json()
    assert [e["action"] for e in journal["entries"]] == ["closed", "trimmed", "added", "opened", "pass"]
    assert journal["open_positions"] == ["AMZN"]


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"symbol": "QQQ"}, 409, "QQQ is a benchmark; the Journal is for watchlist companies"),
        ({"symbol": "SPCX"}, 409, "SPCX is still onboarding; log decisions once it is Ready"),
        ({"symbol": "MSFT"}, 404, "MSFT is not on the watchlist"),
        ({"accession_no": AMZN_8K}, 422, f"Filing {AMZN_8K} is not an alert for NVDA"),
        ({"accession_no": "0000000000-26-000001"}, 422, "Filing 0000000000-26-000001 is not an alert for NVDA"),
        ({"thesis": "   "}, 422, "The thesis can't be empty"),
        ({"thesis": "x" * 4001}, 422, "The thesis is 4,001 characters; the limit is 4,000"),
    ],
)
def test_refused_entries_store_nothing(signed_in_client, journal_db, body, status, detail) -> None:
    response = _log(signed_in_client, action="pass", **body)
    assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert signed_in_client.get("/api/journal").json()["entries"] == []


def test_unknown_actions_and_bad_accessions_are_rejected(signed_in_client, journal_db) -> None:
    assert _log(signed_in_client, action="bought").status_code == 422
    assert _log(signed_in_client, action="pass", accession_no="nope").status_code == 422


def test_corrections_keep_the_original(signed_in_client, journal_db) -> None:
    entry = _log(signed_in_client, action="opened", accession_no=EARNINGS_8K, thesis="Margins hold.").json()
    url = f"/api/journal/{entry['id']}/corrections"

    fixed = signed_in_client.post(url, json={"kind": "thesis", "thesis": "Gross margin holds.", "reason": "Typo"})
    assert fixed.status_code == 201, fixed.text
    relinked = signed_in_client.post(url, json={"kind": "source", "accession_no": OTHER_8K, "reason": "Wrong 8-K"})
    body = relinked.json()

    assert (body["thesis"], body["original_thesis"]) == ("Gross margin holds.", "Margins hold.")
    assert body["source"]["accession_no"] == OTHER_8K
    assert body["original_source"]["accession_no"] == EARNINGS_8K
    assert body["known_at"] == entry["known_at"]
    assert [(c["kind"], c["reason"]) for c in body["corrections"]] == [("thesis", "Typo"), ("source", "Wrong 8-K")]

    unlinked = signed_in_client.post(url, json={"kind": "source", "reason": "Not from an alert"}).json()
    assert unlinked["source"] is None and unlinked["original_source"]["accession_no"] == EARNINGS_8K

    for body, status in (
        ({"kind": "source", "accession_no": AMZN_8K, "reason": "x"}, 422),
        ({"kind": "thesis", "reason": "x"}, 422),
        ({"kind": "thesis", "thesis": "y", "reason": " "}, 422),
    ):
        assert signed_in_client.post(url, json=body).status_code == status
    missing = signed_in_client.post("/api/journal/999/corrections", json={"kind": "withdrawn", "reason": "x"})
    assert missing.status_code == 404


def test_withdrawn_entries_stay_listed_and_stop_counting(signed_in_client, journal_db) -> None:
    opened = _log(signed_in_client, action="opened").json()
    closed = _log(signed_in_client, action="closed").json()

    # Withdrawing the Opened would leave the Closed with nothing to close.
    refused = signed_in_client.post(
        f"/api/journal/{opened['id']}/corrections", json={"kind": "withdrawn", "reason": "Logged the wrong ticker"}
    )
    assert refused.status_code == 409
    assert "would break a later entry" in refused.json()["detail"]

    for entry in (closed, opened):
        response = signed_in_client.post(
            f"/api/journal/{entry['id']}/corrections", json={"kind": "withdrawn", "reason": "Logged the wrong ticker"}
        )
        assert response.status_code == 201, response.text
        assert response.json()["withdrawn"]["reason"] == "Logged the wrong ticker"

    journal = signed_in_client.get("/api/journal").json()
    assert len(journal["entries"]) == 2 and all(e["withdrawn"] for e in journal["entries"])
    assert journal["open_positions"] == []
    # With both withdrawn, NVDA has no position, so Opened is allowed again.
    assert _log(signed_in_client, action="opened").status_code == 201

    again = signed_in_client.post(f"/api/journal/{opened['id']}/corrections", json={"kind": "withdrawn", "reason": "x"})
    assert (again.status_code, again.json()["detail"]) == (
        409, f"Journal entry {opened['id']} was withdrawn and can't be corrected"
    )  # fmt: skip


def test_the_tables_refuse_updates_and_deletes(signed_in_client, journal_db) -> None:
    entry = _log(signed_in_client, action="opened").json()
    signed_in_client.post(f"/api/journal/{entry['id']}/corrections", json={"kind": "withdrawn", "reason": "x"})

    for statement in (
        "UPDATE journal_entries SET thesis = 'rewritten'",
        "UPDATE journal_entries SET known_at = known_at - INTERVAL '1 day'",
        "DELETE FROM journal_entries",
        "UPDATE journal_corrections SET reason = 'rewritten'",
        "DELETE FROM journal_corrections",
    ):
        with journal_db.cursor() as cursor, pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
            cursor.execute(statement)
        journal_db.rollback()


def test_a_real_trade_gives_its_day_price_and_shares(signed_in_client, nvda_bars) -> None:
    response = _log(signed_in_client, action="opened", traded_on="2026-11-20", price=185.25, shares=12.5)
    assert response.status_code == 201, response.text
    entry = response.json()
    assert (entry["traded_on"], entry["price"], entry["shares"]) == ("2026-11-20", 185.25, 12.5)
    # The time it was logged is still the server's.
    assert datetime.fromisoformat(entry["known_at"]).date() >= date(2026, 11, 28)

    plain = _log(signed_in_client, action="added").json()
    assert (plain["traded_on"], plain["price"], plain["shares"]) == (None, None, None)


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"price": 175.0}, "$175.00 is outside NVDA's range on 20 Nov 2026 ($180.00 to $190.00); "
                           "enter the price per share the trade was done at"),
        ({"price": 192.5}, "$192.50 is outside NVDA's range on 20 Nov 2026 ($180.00 to $190.00); "
                           "enter the price per share the trade was done at"),
        ({"traded_on": "2026-11-21"}, "Sat 21 Nov 2026 was not a trading day"),
        ({"traded_on": "2026-11-18"}, "No stored price for NVDA on 18 Nov 2026, so the trade can't be checked"),
        ({"traded_on": "2099-01-05"}, "The trade day 05 Jan 2099 is in the future"),
        ({"price": None}, "A trade needs both the day and the price per share"),
        ({"action": "pass"}, "A Pass is not a trade, so it takes no trade day, price or shares"),
    ],
)  # fmt: skip
def test_refused_trades(signed_in_client, nvda_bars, body, detail) -> None:
    response = _log(signed_in_client, **{"action": "opened", "traded_on": "2026-11-20", "price": 185.0, **body})
    assert (response.status_code, response.json()["detail"]) == (422, detail)


def test_a_price_just_outside_the_session_range_is_allowed(signed_in_client, nvda_bars) -> None:
    # Within 1%: a fill just before the open or after the close.
    assert _log(signed_in_client, action="opened", traded_on="2026-11-20", price=191.5).status_code == 201


def test_prices_and_shares_must_be_positive(signed_in_client, nvda_bars) -> None:
    assert _log(signed_in_client, action="opened", traded_on="2026-11-20", price=-1).status_code == 422
    assert _log(signed_in_client, action="opened", shares=0).status_code == 422


def test_a_trade_before_the_latest_entry_is_refused(signed_in_client, nvda_bars) -> None:
    assert _log(signed_in_client, action="opened").status_code == 201
    late = _log(signed_in_client, action="added", traded_on="2026-11-20", price=185.0)
    assert late.status_code == 409
    assert "log entries in the order they happened" in late.json()["detail"]


def test_a_trade_logged_late_goes_in_trade_order(signed_in_client, nvda_bars) -> None:
    assert _log(signed_in_client, action="opened", traded_on="2026-11-20", price=185.0, shares=10).status_code == 201
    assert _log(signed_in_client, action="trimmed", shares=10).status_code == 409
    assert _log(signed_in_client, action="closed", shares=10).status_code == 201
    assert signed_in_client.get("/api/journal").json()["open_positions"] == []


def test_prices_and_shares_are_as_traded_across_a_later_split(signed_in_client, nvda_bars) -> None:
    """A 2-for-1 split on Mon 23 Nov: the stored bars are adjusted (halved), but the trade typed is as
    it was done, and 10 shares bought before the split are 20 after it."""
    with nvda_bars.cursor() as cursor:
        cursor.execute("UPDATE price_bars SET high = 95, low = 90, open = 91, close = 93")
        cursor.execute("""
            INSERT INTO stock_splits (id, symbol, execution_date, split_from, split_to)
            VALUES ('E-nvda-2026', 'NVDA', '2026-11-23', 1, 2)
            """)
    nvda_bars.commit()

    assert _log(signed_in_client, action="opened", traded_on="2026-11-20", price=92.0).status_code == 422
    assert _log(signed_in_client, action="opened", traded_on="2026-11-20", price=185.0, shares=10).status_code == 201
    assert "position is 20 shares" in _log(signed_in_client, action="closed", shares=10).json()["detail"]
    assert _log(signed_in_client, action="closed", shares=20).status_code == 201


def test_a_trade_today_is_accepted_before_its_bar_arrives(signed_in_client, nvda_bars, monkeypatch) -> None:
    """Today's bar is stored after the close, so the price is checked by scoring later (ADR 0025)."""
    monkeypatch.setattr(journal, "_today", lambda conn: date(2026, 11, 24))
    response = _log(signed_in_client, action="opened", traded_on="2026-11-24", price=500.0)
    assert response.status_code == 201, response.text
