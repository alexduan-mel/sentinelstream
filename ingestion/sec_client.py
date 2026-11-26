"""SEC EDGAR client: declared User-Agent, client-side rate limit, retries with backoff.

Carried over from the prototype's ``company_context/sec_client.py``. Changes:

* Settings come from ``SEC_USER_AGENT`` only, and a missing or e-mail-less value fails the job.
* The response cache is gone. It kept non-Archive responses for an hour, which would hide new
  filings from the poller. Each run fetches the ticker map at most once, so nothing needs caching.
* Retries use the shared ``ingestion.http`` helpers. SEC answers a rate-limit breach with 403, so 403
  is retried too.

SEC's fair-access policy allows 10 requests a second; this client stays at 5. The pacer is shared by
every client in the process, so the limit holds however many clients a worker creates.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from ingestion.http import RETRY_STATUSES, Pacer, send_with_retries

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
REQUESTS_PER_SECOND = 5
MAX_RESPONSE_BYTES = 20_000_000
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

SHARED_PACER = Pacer(1.0 / REQUESTS_PER_SECOND)

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_CIK = re.compile(r"^[0-9]{10}$")
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_ARCHIVE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


class SecConfigurationError(RuntimeError):
    pass


class SecError(RuntimeError):
    pass


def user_agent_from_env() -> str:
    """``SEC_USER_AGENT``, which SEC requires to name the application and a contact e-mail."""
    value = os.getenv("SEC_USER_AGENT", "").strip()
    if not value:
        raise SecConfigurationError(
            "SEC_USER_AGENT is not set; SEC requires an application name and contact e-mail, "
            "e.g. 'SentinelStream you@example.com'"
        )
    if not _EMAIL.search(value):
        raise SecConfigurationError("SEC_USER_AGENT must include a contact e-mail address")
    return value


class SecClient:
    def __init__(
        self,
        user_agent: str,
        *,
        transport: httpx.BaseTransport | None = None,
        pacer: Pacer = SHARED_PACER,
        base_delay: float = 1.0,
    ) -> None:
        self._pacer = pacer
        self._base_delay = base_delay
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> SecClient:
        return cls(user_agent_from_env())

    def __enter__(self) -> SecClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get_json(self, url: str) -> Any:
        response = self._get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise SecError(f"SEC response for {url} is not JSON") from exc

    def _get(self, url: str) -> httpx.Response:
        response = send_with_retries(
            lambda: self._http.get(url),
            pacer=self._pacer,
            base_delay=self._base_delay,
            retry_statuses=RETRY_STATUSES | {403},
            describe=f"sec {httpx.URL(url).path}",
        )
        if response.status_code == 404:
            raise SecError(f"SEC returned 404 for {url}")
        if not 200 <= response.status_code < 300:
            hint = " (check SEC_USER_AGENT and SEC fair-access limits)" if response.status_code in (403, 429) else ""
            raise SecError(f"SEC request failed: HTTP {response.status_code} for {url}{hint}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise SecError(f"SEC response for {url} exceeds {MAX_RESPONSE_BYTES} bytes")
        return response

    def archive_file(self, cik: str, accession_no: str, name: str) -> bytes:
        """One file from a filing's EDGAR Archives folder, for example a Form 4's ownership XML."""
        if not _CIK.match(cik):
            raise ValueError(f"CIK must be 10 digits, got {cik!r}")
        if not _ACCESSION.match(accession_no):
            raise ValueError(f"not an accession number: {accession_no!r}")
        if not _ARCHIVE_NAME.match(name) or ".." in name:
            raise ValueError(f"not a plain file name: {name!r}")
        url = ARCHIVE_URL.format(cik=int(cik), folder=accession_no.replace("-", ""), name=name)
        return self._get(url).content

    def ticker_map(self) -> list[dict[str, Any]]:
        """SEC's ticker → CIK map, one dict per row (``cik``, ``name``, ``ticker``, ``exchange``)."""
        payload = self.get_json(TICKER_MAP_URL)
        try:
            fields = payload["fields"]
            return [dict(zip(fields, row, strict=True)) for row in payload["data"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise SecError("SEC ticker map has an unexpected shape") from exc

    def submissions(self, cik: str) -> dict[str, Any]:
        """The issuer's submissions record: identity plus its most recent filings (up to ~1,000)."""
        if not _CIK.match(cik):
            raise ValueError(f"CIK must be 10 digits, got {cik!r}")
        payload = self.get_json(SUBMISSIONS_URL.format(cik=cik))
        if not isinstance(payload, dict):
            raise SecError(f"SEC submissions for CIK {cik} has an unexpected shape")
        return payload
