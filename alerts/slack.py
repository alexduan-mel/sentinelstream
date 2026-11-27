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

import httpx

from alerts.notifier import Alert, NotifierConfigError, NotifierError
from ingestion.http import Pacer, send_with_retries
from ingestion.market_calendar import NEW_YORK

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


def _eastern(when: datetime) -> str:
    local = when.astimezone(NEW_YORK)
    return f"{local:%b} {local.day}, {local:%H:%M} ET"


def _accepted(when: datetime) -> str:
    """Each reader's own time (Slack renders ``<!date>`` in the zone of their Slack profile, daylight
    saving included), then the market's clock in US Eastern. Clients that cannot render the token show
    the fallback after ``|``."""
    eastern = _eastern(when)
    return f"<!date^{int(when.timestamp())}^{{date_short_pretty}} at {{time}}|{eastern}> ({eastern})"


def format_message(alert: Alert) -> str:
    """The alert as Slack mrkdwn: tickers, form and what matched, then the acceptance time and link.

    Only facts from the filing and the rule that matched; no advice (ground rule 7).
    """
    tickers = ", ".join(f"*{_escape(ticker)}*" for ticker in alert.tickers)
    form = "Form 4" if alert.form_type == "4" else alert.form_type
    lines = [f"{tickers} · {_escape(form)}"]
    lines.extend(_escape(reason) for reason in alert.reasons)
    lines.extend(f"_{_escape(line)}_" for line in alert.history)
    lines.append(f"Accepted {_accepted(alert.accepted_at)} · <{alert.sec_url}|Filing on SEC>")
    return "\n".join(lines)


def payload(alert: Alert) -> dict[str, object]:
    return {"text": format_message(alert), "unfurl_links": False, "unfurl_media": False}


class SlackNotifier:
    channel = CHANNEL

    def __init__(
        self,
        webhook_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        pacer: Pacer = SHARED_PACER,
        base_delay: float = 1.0,
    ) -> None:
        self._url = webhook_url
        self._pacer = pacer
        self._base_delay = base_delay
        self._http = httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport)

    @classmethod
    def from_env(cls) -> SlackNotifier:
        return cls(webhook_url_from_env())

    def close(self) -> None:
        self._http.close()

    def _scrub(self, text: str) -> str:
        return text.replace(self._url, "<SLACK_WEBHOOK_URL>")

    def send(self, alert: Alert) -> None:
        try:
            response = send_with_retries(
                lambda: self._http.post(self._url, json=payload(alert)),
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
