"""Massive (formerly Polygon) daily aggregates client.

Carried over from the prototype's ``ingestion/massive_client.py``. Massive authenticates with an
``apiKey`` query parameter, so raw URLs are never logged and the key is redacted from error text.
Requests are paced by ``MASSIVE_RATE_LIMIT_PER_MINUTE`` (default 5, the free plan's limit).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from ingestion.http import Pacer, send_with_retries

BASE_URL = "https://api.massive.com"
DEFAULT_RATE_LIMIT_PER_MINUTE = 5
TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MassiveConfigurationError(RuntimeError):
    pass


class MassiveError(RuntimeError):
    pass


def api_key_from_env() -> str:
    value = os.getenv("MASSIVE_API_KEY", "").strip()
    if not value:
        raise MassiveConfigurationError("MASSIVE_API_KEY is not set; price ingestion needs a Massive API key")
    return value


def rate_limit_from_env() -> int:
    raw = os.getenv("MASSIVE_RATE_LIMIT_PER_MINUTE", "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        raise MassiveConfigurationError(f"MASSIVE_RATE_LIMIT_PER_MINUTE must be a positive integer, got {raw!r}")
    return value


@dataclass(frozen=True)
class TickerDetails:
    name: str | None
    cik: str | None
    list_date: date | None


class MassiveClient:
    def __init__(
        self,
        api_key: str,
        *,
        requests_per_minute: int = DEFAULT_RATE_LIMIT_PER_MINUTE,
        transport: httpx.BaseTransport | None = None,
        pacer: Pacer | None = None,
        base_delay: float = 2.0,
    ) -> None:
        self._api_key = api_key
        self._pacer = pacer or Pacer(60.0 / requests_per_minute)
        self._base_delay = base_delay
        self._http = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, transport=transport)

    @classmethod
    def from_env(cls) -> MassiveClient:
        return cls(api_key_from_env(), requests_per_minute=rate_limit_from_env())

    def __enter__(self) -> MassiveClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "[redacted]")

    def daily_bars(self, symbol: str, date_from: date, date_to: date) -> dict[str, Any]:
        """Split-adjusted daily bars for ``date_from``..``date_to`` inclusive, oldest first.

        Returns the provider payload; its ``results`` list holds one aggregate per session.
        """
        path = f"/v2/aggs/ticker/{symbol}/range/1/day/{date_from.isoformat()}/{date_to.isoformat()}"
        params = {"adjusted": "true", "sort": "asc", "limit": "50000", "apiKey": self._api_key}
        try:
            response = send_with_retries(
                lambda: self._http.get(path, params=params),
                pacer=self._pacer,
                base_delay=self._base_delay,
                describe=f"massive aggs {symbol}",
            )
        except httpx.TransportError as exc:
            raise MassiveError(f"Massive request for {symbol} failed: {self._redact(str(exc))}") from None

        if response.status_code >= 400:
            body = self._redact(response.text.strip())[:300]
            hint = " Check MASSIVE_API_KEY and plan permissions." if response.status_code in (401, 403) else ""
            raise MassiveError(f"Massive request for {symbol} failed: HTTP {response.status_code} {body}{hint}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MassiveError(f"Massive response for {symbol} is not JSON") from exc
        if not isinstance(payload, dict):
            raise MassiveError(f"Massive response for {symbol} has an unexpected shape")
        status = str(payload.get("status") or "").upper()
        if status not in ("OK", "DELAYED"):
            raise MassiveError(f"Massive returned status {status or 'missing'} for {symbol}")
        results = payload.get("results") or []
        if not isinstance(results, list):
            raise MassiveError(f"Massive results for {symbol} are not a list")
        payload["results"] = results
        return payload

    def ticker_details(self, symbol: str) -> TickerDetails | None:
        """What Massive knows about the security now trading as ``symbol``; None if it knows none.

        ``list_date`` is when this security started trading under the symbol. A symbol can be reused,
        so bars dated before it belong to an earlier security.
        """
        try:
            response = send_with_retries(
                lambda: self._http.get(f"/v3/reference/tickers/{symbol}", params={"apiKey": self._api_key}),
                pacer=self._pacer,
                base_delay=self._base_delay,
                describe=f"massive ticker {symbol}",
            )
        except httpx.TransportError as exc:
            raise MassiveError(f"Massive request for {symbol} details failed: {self._redact(str(exc))}") from None
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            body = self._redact(response.text.strip())[:300]
            raise MassiveError(f"Massive request for {symbol} details failed: HTTP {response.status_code} {body}")
        try:
            results = response.json().get("results") or {}
        except (ValueError, AttributeError) as exc:
            raise MassiveError(f"Massive details for {symbol} are not a JSON object") from exc
        if not isinstance(results, dict) or not results:
            return None
        raw_list_date = str(results.get("list_date") or "").strip()
        try:
            list_date = date.fromisoformat(raw_list_date) if raw_list_date else None
        except ValueError as exc:
            raise MassiveError(f"Massive list date for {symbol} is not a date: {raw_list_date!r}") from exc
        cik = str(results.get("cik") or "").strip() or None
        return TickerDetails(
            name=str(results.get("name") or "").strip() or None,
            cik=f"{int(cik):010d}" if cik and cik.isdigit() else None,
            list_date=list_date,
        )
