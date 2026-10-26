from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.portal.client import CircuitBreaker, PortalTransport
from app.portal.session import PortalSession
from mock_portal.app import app as mock_portal_app
from mock_portal.app import portal_state

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_mock_portal() -> Iterator[None]:
    portal_state.expire_all()
    portal_state.login_attempts = 0
    portal_state.successful_logins = 0
    portal_state.failed_logins = 0
    yield
    portal_state.expire_all()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        urja_base_url="http://mock-portal",
        urja_username="operator@urja.example",
        urja_password="urja-demo-pass",
        warm_cache_on_startup=False,
        admin_api_key="test-admin-key",
    )


@pytest.fixture
def portal_http_client_factory() -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mock_portal_app),
            base_url="http://mock-portal",
            follow_redirects=False,
        )

    return factory


@pytest.fixture
def api_client(
    settings: Settings,
    portal_http_client_factory: Callable[[], httpx.AsyncClient],
) -> Iterator[TestClient]:
    app = create_app(settings, portal_http_client_factory)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def fixture_text() -> Callable[[str], str]:
    def read(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return read


@pytest.fixture
async def portal_session(
    settings: Settings,
) -> AsyncIterator[tuple[PortalSession, PortalTransport]]:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_portal_app),
        base_url="http://mock-portal",
        follow_redirects=False,
    )
    breaker = CircuitBreaker(threshold=5, reset_timeout_s=30)
    transport = PortalTransport(
        client,
        connect_timeout_s=3,
        read_timeout_s=5,
        total_timeout_s=10,
        max_concurrency=10,
        max_retries=0,
        retry_base_delay_s=0.001,
        breaker=breaker,
    )
    session = PortalSession(transport, settings)
    try:
        yield session, transport
    finally:
        await client.aclose()
