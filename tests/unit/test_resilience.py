from __future__ import annotations

import httpx
import pytest
import respx

from app.portal.client import CircuitBreaker, PortalTransport
from app.portal.exceptions import CircuitOpenError, PortalTimeoutError, PortalUnavailableError


def make_transport(
    client: httpx.AsyncClient,
    *,
    max_retries: int = 2,
    threshold: int = 5,
    reset_timeout_s: float = 30,
) -> PortalTransport:
    return PortalTransport(
        client,
        connect_timeout_s=1,
        read_timeout_s=1,
        total_timeout_s=2,
        max_concurrency=5,
        max_retries=max_retries,
        retry_base_delay_s=0.001,
        breaker=CircuitBreaker(threshold=threshold, reset_timeout_s=reset_timeout_s),
    )


async def test_retries_retryable_5xx_then_succeeds() -> None:
    async with respx.mock(base_url="https://portal.test") as mock:
        route = mock.get("/flaky")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
        async with httpx.AsyncClient(base_url="https://portal.test") as client:
            transport = make_transport(client)
            response = await transport.request("GET", "/flaky")
        assert response.status_code == 200
        assert response.text == "ok"
        assert route.call_count == 2
        assert transport.breaker.state == "closed"


async def test_4xx_is_not_retried_and_does_not_trip_breaker() -> None:
    async with respx.mock(base_url="https://portal.test") as mock:
        route = mock.get("/bad").mock(return_value=httpx.Response(400))
        async with httpx.AsyncClient(base_url="https://portal.test") as client:
            transport = make_transport(client)
            response = await transport.request("GET", "/bad")
        assert response.status_code == 400
        assert route.call_count == 1
        assert transport.breaker.state == "closed"


async def test_breaker_opens_after_consecutive_failures() -> None:
    async with respx.mock(base_url="https://portal.test") as mock:
        route = mock.get("/down").mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient(base_url="https://portal.test") as client:
            transport = make_transport(client, max_retries=0, threshold=2)
            with pytest.raises(PortalUnavailableError):
                await transport.request("GET", "/down")
            with pytest.raises(PortalUnavailableError):
                await transport.request("GET", "/down")
            assert transport.breaker.state == "open"
            with pytest.raises(CircuitOpenError):
                await transport.request("GET", "/down")
        assert route.call_count == 2


async def test_timeout_raises_domain_error() -> None:
    async with respx.mock(base_url="https://portal.test") as mock:
        route = mock.get("/slow").mock(side_effect=httpx.ConnectTimeout("too slow"))
        async with httpx.AsyncClient(base_url="https://portal.test") as client:
            transport = make_transport(client, max_retries=1)
            with pytest.raises(PortalTimeoutError):
                await transport.request("GET", "/slow")
        assert route.call_count == 2
