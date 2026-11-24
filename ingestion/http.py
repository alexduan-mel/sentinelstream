"""Client-side pacing and retries shared by every provider client (ground rule 5).

Carried over from the prototype, which had one copy in the SEC client and another in the Massive
client. Both now use ``Pacer`` for spacing and ``send_with_retries`` for exponential backoff that
honours ``Retry-After``.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

LOGGER = logging.getLogger(__name__)

# Provider clients put credentials in query strings (Massive's apiKey); httpx logs full URLs at INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class Pacer:
    """Spaces calls at least ``min_interval`` seconds apart, across threads of one process.

    Every attempt takes a slot, retries included, so a retry storm can never exceed the budget.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must not be negative")
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        """Block until the next slot is free, then take it."""
        with self._lock:
            while (delay := self._next_slot - self._clock()) > 0:
                self._sleep(delay)
            self._next_slot = self._clock() + self.min_interval

    def hold_off(self, seconds: float) -> None:
        """Push the next slot at least ``seconds`` into the future (backoff, ``Retry-After``)."""
        with self._lock:
            self._next_slot = max(self._next_slot, self._clock() + seconds)


def retry_after_seconds(response: httpx.Response, *, now: datetime | None = None) -> float | None:
    """The response's ``Retry-After`` in seconds (delta or HTTP date), or None if absent or malformed."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    return max(0.0, seconds) if math.isfinite(seconds) else None


def send_with_retries(
    send: Callable[[], httpx.Response],
    *,
    pacer: Pacer,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    max_retry_after: float = 120.0,
    retry_statuses: Collection[int] = RETRY_STATUSES,
    describe: str = "request",
) -> httpx.Response:
    """Call ``send`` until it returns a non-retryable response or ``attempts`` run out.

    Before every attempt the pacer is waited on. After a transport error or a status in
    ``retry_statuses`` the next attempt is held off by ``base_delay * 2**n`` (capped at
    ``max_delay``), or by ``Retry-After`` when the server asks for longer. A ``Retry-After`` above
    ``max_retry_after`` is honoured by giving up instead of tying up the worker.

    Returns the last response, whatever its status, so the caller can build its own error. Raises the
    last ``httpx.TransportError`` if no attempt got a response.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(attempts):
        pacer.wait()
        try:
            response = send()
        except httpx.TransportError as exc:
            if attempt == attempts - 1:
                raise
            delay = min(max_delay, base_delay * 2**attempt)
            LOGGER.warning("http_retry describe=%s attempt=%d error=%s delay=%.1f", describe, attempt + 1, exc, delay)
            pacer.hold_off(delay)
            continue

        if response.status_code not in retry_statuses or attempt == attempts - 1:
            return response
        delay = min(max_delay, base_delay * 2**attempt)
        asked = retry_after_seconds(response)
        if asked is not None:
            if asked > max_retry_after:
                LOGGER.warning(
                    "http_retry_after_too_long describe=%s status=%d retry_after=%.0f",
                    describe,
                    response.status_code,
                    asked,
                )
                return response
            delay = max(delay, asked)
        LOGGER.warning(
            "http_retry describe=%s attempt=%d status=%d delay=%.1f", describe, attempt + 1, response.status_code, delay
        )
        pacer.hold_off(delay)
    raise AssertionError("unreachable")
