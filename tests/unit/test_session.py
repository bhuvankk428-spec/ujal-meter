from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.portal.client import CircuitBreaker, PortalTransport
from app.portal.exceptions import PortalAuthError
from app.portal.session import PortalSession, is_session_expired
from mock_portal.app import app as mock_portal_app
from mock_portal.app import portal_state


def test_expiry_detection_across_three_signals(
    fixture_text: Callable[[str], str],
) -> None:
    login_html = fixture_text("login.html")
    assert is_session_expired(
        httpx.Response(200, headers={"content-type": "text/html"}, text=login_html)
    )
    assert is_session_expired(httpx.Response(401))
    assert is_session_expired(httpx.Response(302, headers={"location": "/login?next=/meters"}))
    assert (
        is_session_expired(
            httpx.Response(200, headers={"content-type": "text/html"}, text="<h1>Meters</h1>")
        )
        is False
    )
    assert (
        is_session_expired(
            httpx.Response(200, headers={"content-type": "text/csv"}, text="meter_id\nMTR-1")
        )
        is False
    )


async def test_initial_login_is_single_flight(
    portal_session: tuple[PortalSession, PortalTransport],
) -> None:
    session, _ = portal_session
    await asyncio.gather(*[session.request("GET", "/export/meters.csv") for _ in range(10)])
    assert session.login_attempts == 1
    assert session.successful_logins == 1
    assert portal_state.successful_logins == 1


async def test_concurrent_expiry_triggers_exactly_one_reauth(
    portal_session: tuple[PortalSession, PortalTransport],
) -> None:
    session, _ = portal_session
    await session.request("GET", "/export/meters.csv")
    assert session.successful_logins == 1

    portal_state.expire_all()
    responses = await asyncio.gather(
        *[session.request("GET", "/export/meters.csv") for _ in range(10)]
    )
    assert all(response.status_code == 200 for response in responses)
    assert session.login_attempts == 2
    assert session.successful_logins == 2
    assert portal_state.successful_logins == 2
    assert session.generation == 2


async def test_bad_credentials_fail_once_without_loop(settings: Settings) -> None:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_portal_app),
        base_url="http://mock-portal",
        follow_redirects=False,
    )
    bad_settings = settings.model_copy(update={"urja_password": SecretStr("wrong")})
    transport = PortalTransport(
        client,
        connect_timeout_s=1,
        read_timeout_s=2,
        total_timeout_s=5,
        max_concurrency=2,
        max_retries=0,
        retry_base_delay_s=0.001,
        breaker=CircuitBreaker(threshold=5, reset_timeout_s=30),
    )
    session = PortalSession(transport, bad_settings)

    with pytest.raises(PortalAuthError):
        await session.request("GET", "/export/meters.csv")
    assert session.login_attempts == 1
    assert session.successful_logins == 0
    assert portal_state.failed_logins == 1
    await client.aclose()


async def test_expired_session_retries_original_request_once(
    portal_session: tuple[PortalSession, PortalTransport],
) -> None:
    session, _ = portal_session
    await session.request("GET", "/export/meters.csv")
    portal_state.expire_all()
    response = await session.request("GET", "/meters/MTR-0001")
    assert response.status_code == 200
    assert "Meter MTR-0001" in response.text
    assert session.successful_logins == 2
