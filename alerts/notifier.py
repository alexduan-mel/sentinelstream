"""The notifier interface: what an alert carries and what a channel must do with it (ADR 0009).

Slack is the only channel in M1 (``alerts.slack``). Email or Telegram can be added later as another
``Notifier`` with its own ``channel`` name; deliveries are recorded and deduplicated per channel, so a
new channel does not resend what Slack already had.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class NotifierConfigError(RuntimeError):
    """The channel is not configured (a missing or malformed setting). Nothing was sent."""


class NotifierError(RuntimeError):
    """The channel did not accept the alert. It stays pending and is retried on a later run."""


@dataclass(frozen=True)
class AlertIssuer:
    """One linked issuer the alert is high for, with the rule's reason."""

    ticker: str
    reason: str


@dataclass(frozen=True)
class Alert:
    accession_no: str
    form_type: str
    accepted_at: datetime
    sec_url: str
    issuers: tuple[AlertIssuer, ...]
    # Plain-text base-rate lines (alerts.base_rate.history_line): what followed similar filings.
    history: tuple[str, ...] = ()

    @property
    def tickers(self) -> list[str]:
        return [issuer.ticker for issuer in self.issuers]

    @property
    def reasons(self) -> list[str]:
        """Distinct reasons in issuer order: co-registrants on one 8-K share the same items."""
        return list(dict.fromkeys(issuer.reason for issuer in self.issuers))


class Notifier(Protocol):
    channel: str

    def send(self, alert: Alert) -> None:
        """Deliver one alert. Raises ``NotifierError`` if the channel did not accept it."""

    def close(self) -> None: ...
