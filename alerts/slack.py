"""Slack incoming-webhook notifier (ADR 0009).

The webhook URL is a secret: anyone holding it can post to the channel. It comes from
``SLACK_WEBHOOK_URL`` (on EC2 a Parameter Store SecureString), is never logged, and is scrubbed from
any error text that could reach ``task_runs``.

Slack allows about one message a second per webhook and answers a breach with 429 and
``Retry-After``; both are honoured through the shared ``ingestion.http`` helpers. A 200 with body
``ok`` is the only success. Other answers (``invalid_payload``, ``no_service`` for a removed webhook,
``channel_is_archived``) become a ``NotifierError`` naming Slack's reason.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from alerts.display_time import US_EASTERN, DisplayTimezoneError, display_timezone, format_clock
from alerts.notifier import Alert, NotifierConfigError, NotifierError
from ingestion.http import Pacer, send_with_retries

CHANNEL = "slack"
WEBHOOK_PREFIX = "https://hooks.slack.com/"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
SHARED_PACER = Pacer(1.0)


def webhook_url_from_env() -> str:
    value = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not value:
        raise NotifierConfigError(
            "SLACK_WEBHOOK_URL is not set; create a Slack incoming webhook and store it in Parameter Store "
            "as /sentinelstream/SLACK_WEBHOOK_URL (SecureString)"
        )
    if not value.startswith(WEBHOOK_PREFIX):
        raise NotifierConfigError(f"SLACK_WEBHOOK_URL must be a Slack incoming webhook URL ({WEBHOOK_PREFIX}...)")
    return value


def _escape(text: str) -> str:
    """Slack mrkdwn treats &, < and > as control characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _accepted(when: datetime, zone: ZoneInfo) -> str:
    """In the display time zone, with US Eastern after it when that differs (the market's clock)."""
    text = format_clock(when, zone)
    if zone.key != US_EASTERN:
        text += f" ({format_clock(when, ZoneInfo(US_EASTERN))})"
    return text


def format_message(alert: Alert, zone: ZoneInfo | None = None) -> str:
    """The alert as Slack mrkdwn: tickers, form and what matched, then the acceptance time and link.

    Only facts from the filing and the rule that matched; no advice (ground rule 7). Times are in
    ``zone`` (the notifier passes the display time zone), US Eastern if not given.
    """
    tickers = ", ".join(f"*{_escape(ticker)}*" for ticker in alert.tickers)
    form = "Form 4" if alert.form_type == "4" else alert.form_type
    lines = [f"{tickers} · {_escape(form)}"]
    lines.extend(_escape(reason) for reason in alert.reasons)
    accepted = _accepted(alert.accepted_at, zone or ZoneInfo(US_EASTERN))
    lines.append(f"Accepted {accepted} · <{alert.sec_url}|Filing on SEC>")
    return "\n".join(lines)


def payload(alert: Alert, zone: ZoneInfo | None = None) -> dict[str, object]:
    return {"text": format_message(alert, zone), "unfurl_links": False, "unfurl_media": False}


class SlackNotifier:
    channel = CHANNEL

    def __init__(
        self,
        webhook_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        pacer: Pacer = SHARED_PACER,
        base_delay: float = 1.0,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._url = webhook_url
        self._zone = zone or ZoneInfo(US_EASTERN)
        self._pacer = pacer
        self._base_delay = base_delay
        self._http = httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport)

    @classmethod
    def from_env(cls) -> SlackNotifier:
        url = webhook_url_from_env()
        try:
            zone = display_timezone()
        except DisplayTimezoneError as exc:
            raise NotifierConfigError(str(exc)) from None
        return cls(url, zone=zone)

    def close(self) -> None:
        self._http.close()

    def _scrub(self, text: str) -> str:
        return text.replace(self._url, "<SLACK_WEBHOOK_URL>")

    def send(self, alert: Alert) -> None:
        try:
            response = send_with_retries(
                lambda: self._http.post(self._url, json=payload(alert, self._zone)),
                pacer=self._pacer,
                base_delay=self._base_delay,
                describe="slack_webhook",
            )
        except httpx.TransportError as exc:
            raise NotifierError(self._scrub(f"Slack request failed: {type(exc).__name__}: {exc}")) from None
        body = response.text.strip()
        if response.status_code == 200 and body == "ok":
            return
        reason = self._scrub(body[:200]) or "no body"
        raise NotifierError(f"Slack answered HTTP {response.status_code}: {reason}")
