"""The Slack notifier: settings, message text and how it reads Slack's answers. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from alerts.notifier import Alert, AlertIssuer, NotifierConfigError, NotifierError
from alerts.slack import SlackNotifier, format_message, webhook_url_from_env
from ingestion.http import Pacer
from tests.test_http_and_calendar import FakeClock

WEBHOOK = "https://hooks.slack.com/services/T000/B000/not-a-real-secret"
SEC_URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/0001045810-26-000123-index.htm"


def _alert(*issuers: AlertIssuer, form_type: str = "8-K") -> Alert:
    return Alert(
        accession_no="0001045810-26-000123",
        form_type=form_type,
        accepted_at=datetime(2026, 11, 26, 21, 5, 30, tzinfo=UTC),  # 16:05 ET
        sec_url=SEC_URL,
        issuers=issuers or (AlertIssuer("NVDA", "Item 2.02 Results of Operations and Financial Condition"),),
    )


def _notifier(handler) -> tuple[SlackNotifier, FakeClock]:
    clock = FakeClock()
    notifier = SlackNotifier(
        WEBHOOK, transport=httpx.MockTransport(handler), pacer=Pacer(1.0, clock=clock, sleep=clock.sleep)
    )
    return notifier, clock


# --- settings --------------------------------------------------------------------------------------


def test_webhook_url_must_be_set_and_be_a_slack_webhook(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    with pytest.raises(NotifierConfigError, match="SLACK_WEBHOOK_URL is not set"):
        webhook_url_from_env()

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://example.com/secret-path")
    with pytest.raises(NotifierConfigError) as error:
        webhook_url_from_env()
    assert "secret-path" not in str(error.value)

    monkeypatch.setenv("SLACK_WEBHOOK_URL", f"  {WEBHOOK}\n")
    assert webhook_url_from_env() == WEBHOOK


# --- message ---------------------------------------------------------------------------------------


def test_message_names_tickers_form_reason_time_and_links_the_filing() -> None:
    assert format_message(_alert()) == (
        "*NVDA* · 8-K\n"
        "Item 2.02 Results of Operations and Financial Condition\n"
        f"Accepted Nov 26, 16:05 ET · <{SEC_URL}|Filing on SEC>"
    )


def test_a_filing_for_two_issuers_is_one_message_with_shared_reasons_once() -> None:
    reason = "Item 1.01 Entry into a Material Definitive Agreement"
    text = format_message(_alert(AlertIssuer("ANT", reason), AlertIssuer("NBIS", reason)))
    assert text.splitlines()[:2] == ["*ANT*, *NBIS* · 8-K", reason]


def test_form_4_reason_is_escaped_for_slack_mrkdwn() -> None:
    text = format_message(
        _alert(AlertIssuer("AMZN", "Open-market purchase by Smith & Jones <Trust> (Director)"), form_type="4")
    )
    assert text.splitlines()[:2] == [
        "*AMZN* · Form 4",
        "Open-market purchase by Smith &amp; Jones &lt;Trust&gt; (Director)",
    ]


def test_message_has_no_trading_language() -> None:
    words = format_message(_alert()).lower().split()
    assert not {"buy", "sell", "hold", "recommend", "recommendation"} & set(words)


# --- sending ---------------------------------------------------------------------------------------


def test_send_posts_json_without_link_unfurls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    notifier, _clock = _notifier(handler)
    notifier.send(_alert())

    assert [str(r.url) for r in requests] == [WEBHOOK]
    body = json.loads(requests[0].content)
    assert body == {"text": format_message(_alert()), "unfurl_links": False, "unfurl_media": False}


def test_rate_limit_is_retried_after_slacks_retry_after() -> None:
    answers = [httpx.Response(429, headers={"Retry-After": "3"}, text="rate_limited"), httpx.Response(200, text="ok")]
    notifier, clock = _notifier(lambda request: answers.pop(0))
    notifier.send(_alert())
    assert answers == []
    assert sum(clock.slept) >= 3


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, "no_service", "HTTP 404: no_service"),
        (410, "channel_is_archived", "HTTP 410: channel_is_archived"),
        (400, "invalid_payload", "HTTP 400: invalid_payload"),
        (200, "", "HTTP 200: no body"),
    ],
)
def test_anything_but_200_ok_is_an_error_naming_slacks_reason(status: int, body: str, expected: str) -> None:
    notifier, _clock = _notifier(lambda request: httpx.Response(status, text=body))
    with pytest.raises(NotifierError, match=expected):
        notifier.send(_alert())


def test_errors_never_contain_the_webhook_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"could not reach {WEBHOOK}", request=request)

    notifier, _clock = _notifier(handler)
    with pytest.raises(NotifierError) as error:
        notifier.send(_alert())
    assert WEBHOOK not in str(error.value)
    assert "not-a-real-secret" not in str(error.value)
    assert "<SLACK_WEBHOOK_URL>" in str(error.value)

    echo, _clock = _notifier(lambda request: httpx.Response(400, text=f"bad url {WEBHOOK}"))
    with pytest.raises(NotifierError) as error:
        echo.send(_alert())
    assert WEBHOOK not in str(error.value)
