"""Typed fetch operations exposed to the service layer."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.models.consumption import Granularity
from app.portal.client import PortalTransport
from app.portal.exceptions import (
    PortalAuthError,
    PortalError,
    PortalNotFoundError,
    PortalUnavailableError,
)
from app.portal.parsers import (
    is_login_html,
    parse_consumption_json,
    parse_hierarchy,
    parse_meter_detail,
    parse_meter_list,
    parse_meters_csv,
)
from app.portal.session import PortalSession

logger = logging.getLogger(__name__)


class PortalGateway:
    def __init__(
        self, session: PortalSession, transport: PortalTransport, settings: Settings
    ) -> None:
        self._session = session
        self._transport = transport
        self._settings = settings
        self._ready_lock = asyncio.Lock()
        self._ready_cached: tuple[float, bool, str | None] | None = None

    @property
    def session(self) -> PortalSession:
        return self._session

    @property
    def breaker_state(self) -> str:
        return self._transport.breaker.state

    async def fetch_meters_csv(self) -> list[dict[str, str]]:
        response = await self._session.request("GET", "/export/meters.csv")
        self._ensure_success(response, "meters CSV export")
        return parse_meters_csv(response.text)

    async def fetch_meters_page(self, page: int) -> dict[str, Any]:
        response = await self._session.request("GET", "/meters", params={"page": page})
        self._ensure_success(response, f"meter list page {page}")
        return parse_meter_list(response.text)

    async def fetch_all_meter_pages(self, max_pages: int = 100) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        page = 1
        seen_pages: set[int] = set()
        while page and page not in seen_pages and len(seen_pages) < max_pages:
            seen_pages.add(page)
            parsed = await self.fetch_meters_page(page)
            rows.extend(parsed["rows"])
            next_page = parsed.get("next_page")
            if next_page is None:
                total = parsed.get("total")
                if total is not None and len(rows) < total:
                    next_page = page + 1
                else:
                    break
            if next_page == page:
                break
            page = int(next_page)
        return rows

    async def fetch_meter_detail(self, meter_id: str) -> dict[str, str]:
        path = f"/meters/{quote(meter_id, safe='')}"
        response = await self._session.request("GET", path)
        self._ensure_success(response, f"meter detail {meter_id}")
        return parse_meter_detail(response.text)

    async def fetch_consumption(
        self,
        meter_id: str,
        *,
        from_: datetime,
        to: datetime,
        granularity: Granularity,
    ) -> dict[str, Any]:
        path = f"/meters/{quote(meter_id, safe='')}/consumption/data"
        response = await self._session.request(
            "GET",
            path,
            params={
                "from": from_.isoformat(),
                "to": to.isoformat(),
                "granularity": granularity.value,
            },
        )
        self._ensure_success(response, f"consumption for {meter_id}")
        return parse_consumption_json(response.text)

    async def fetch_hierarchy(self) -> list[dict[str, str]]:
        response = await self._session.request("GET", "/hierarchy")
        self._ensure_success(response, "hierarchy")
        return parse_hierarchy(response.text)

    async def probe(self) -> tuple[bool, str | None]:
        """Cheap, cached readiness probe that also proves the session works."""
        ttl = self._settings.readiness_probe_ttl_s
        cached = self._ready_cached
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1], cached[2]

        async with self._ready_lock:
            cached = self._ready_cached
            if cached and time.monotonic() - cached[0] < ttl:
                return cached[1], cached[2]
            ok = False
            detail: str | None = None
            try:
                response = await self._session.request("GET", "/meters", params={"page": 1})
                ok = response.status_code == 200 and not is_login_html(response.text)
                if not ok:
                    detail = f"portal returned HTTP {response.status_code} or a login page"
            except PortalError as exc:
                detail = f"{type(exc).__name__}: {exc}"
            self._ready_cached = (time.monotonic(), ok, detail)
            return ok, detail

    async def close(self) -> None:
        await self._transport.client.aclose()

    @staticmethod
    def _ensure_success(response: httpx.Response, what: str) -> None:
        if response.status_code == 404:
            raise PortalNotFoundError(f"{what} was not found on the portal")
        if response.status_code in (401, 403):
            raise PortalAuthError(f"{what} was rejected by the portal")
        if response.status_code != 200:
            location = response.headers.get("location")
            suffix = f" (redirect to {location})" if location else ""
            raise PortalUnavailableError(f"{what} returned HTTP {response.status_code}{suffix}")
