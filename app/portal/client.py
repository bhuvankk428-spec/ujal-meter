"""Low-level portal transport: timeouts, retries, breaker, concurrency cap.

Only GET (reads) and the login POST exist. There is deliberately no generic
write method in the adapter.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from app.portal.exceptions import (
    CircuitOpenError,
    PortalTimeoutError,
    PortalUnavailableError,
)

BreakerState = Literal["closed", "open", "half_open"]
RETRYABLE_STATUS = {502, 503, 504}

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Tiny consecutive-failure breaker with one half-open probe."""

    def __init__(self, threshold: int, reset_timeout_s: float) -> None:
        self._threshold = max(1, threshold)
        self._reset_timeout_s = reset_timeout_s
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self._reset_timeout_s:
            return "half_open"
        return "open"

    @property
    def retry_after_s(self) -> int:
        if self._opened_at is None:
            return 0
        remaining = self._reset_timeout_s - (time.monotonic() - self._opened_at)
        return max(0, int(remaining) + 1)

    def before_request(self) -> None:
        state = self.state
        if state == "open":
            raise CircuitOpenError(f"portal circuit is open; retry in about {self.retry_after_s}s")
        if state == "half_open":
            if self._probe_in_flight:
                raise CircuitOpenError("portal circuit half-open probe already in flight")
            self._probe_in_flight = True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        if self._opened_at is not None:
            self._opened_at = time.monotonic()
            self._probe_in_flight = False
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()
            self._probe_in_flight = False
            logger.warning("portal circuit opened after %s consecutive failures", self._failures)


class PortalTransport:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        connect_timeout_s: float,
        read_timeout_s: float,
        total_timeout_s: float,
        max_concurrency: int,
        max_retries: int,
        retry_base_delay_s: float,
        breaker: CircuitBreaker,
    ) -> None:
        self._client = client
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=read_timeout_s,
            pool=connect_timeout_s,
        )
        self._total_timeout_s = total_timeout_s
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._max_retries = max(0, max_retries)
        self._retry_base_delay_s = max(0.0, retry_base_delay_s)
        self._breaker = breaker

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def note_failure(self) -> None:
        self._breaker.record_failure()

    def note_success(self) -> None:
        self._breaker.record_success()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        self._breaker.before_request()
        retryable = method.upper() == "GET"
        attempts = self._max_retries + 1 if retryable else 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self._semaphore:
                    async with asyncio.timeout(self._total_timeout_s):
                        response = await self._client.request(
                            method,
                            path,
                            params=params,
                            data=data,
                            headers=headers,
                            timeout=self._timeout,
                            follow_redirects=False,
                        )
            except (TimeoutError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await self._backoff(attempt)
                    continue
                self._breaker.record_failure()
                raise PortalTimeoutError(
                    f"portal {method} {path} timed out after {attempt + 1} attempt(s)"
                ) from exc
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await self._backoff(attempt)
                    continue
                self._breaker.record_failure()
                raise PortalUnavailableError(
                    f"portal {method} {path} was unreachable: {type(exc).__name__}"
                ) from exc

            if response.status_code in RETRYABLE_STATUS and attempt + 1 < attempts:
                logger.warning(
                    "portal %s %s returned %s; retrying (%s/%s)",
                    method,
                    path,
                    response.status_code,
                    attempt + 1,
                    self._max_retries,
                )
                await self._backoff(attempt)
                continue

            if response.status_code in RETRYABLE_STATUS or response.status_code >= 500:
                self._breaker.record_failure()
                raise PortalUnavailableError(
                    f"portal {method} {path} returned HTTP {response.status_code}"
                )
            if response.status_code == 429:
                self._breaker.record_failure()
                raise PortalUnavailableError(
                    f"portal {method} {path} rate-limited the request (HTTP 429)"
                )

            self._breaker.record_success()
            return response

        self._breaker.record_failure()
        raise PortalUnavailableError(f"portal {method} {path} failed after retries: {last_error}")

    async def _backoff(self, attempt: int) -> None:
        base = self._retry_base_delay_s * (2**attempt)
        delay = base + random.uniform(0, max(base * 0.5, 0.01))
        await asyncio.sleep(delay)
