"""Massive (formerly Polygon) daily aggregates client.

Carried over from the prototype's ``ingestion/massive_client.py``. Massive authenticates with an
``apiKey`` query parameter, so raw URLs are never logged and the key is redacted from error text.
Requests are paced by ``MASSIVE_RATE_LIMIT_PER_MINUTE`` (default 5, the free plan's limit).
"""

from __future__ import annotations

import os
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
