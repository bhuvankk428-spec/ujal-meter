"""Session manager: lazy login, expiry detection, single-flight re-auth.

The legacy portal has no token endpoint. It sets a cookie after a form login and
silently re-renders the login page (HTTP 200) when a session is gone. That last
behaviour is why expiry detection lives in exactly one function.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import Settings
from app.portal.client import PortalTransport
from app.portal.exceptions import (
    CircuitOpenError,
    ParseError,
    PortalAuthError,
    PortalError,
)
from app.portal.parsers import is_login_html, parse_login_page

logger = logging.getLogger(__name__)

SESSION_ERROR_STATUS = {401, 403}
REDIRECT_STATUS = {301, 302, 303, 307, 308}


def is_session_expired(response: httpx.Response) -> bool:
    """One canonical rule for every portal page type.

    Handles the three signals used by the reference portal: 401/403 on XHR
    endpoints, a redirect to /login on downloads, and a 200 response that is
    actually the login form (the classic legacy trap).
    """
    if response.status_code in SESSION_ERROR_STATUS:
        return True
    if response.status_code in REDIRECT_STATUS:
        location = (response.headers.get("location") or "").lower()
        return "login" in location
    if response.status_code == 200:
        content_type = (response.headers.get("content-type") or "").lower()
        if "html" in content_type or "text" in content_type:
            return is_login_html(response.text)
    return False


class PortalSession:
    def __init__(self, transport: PortalTransport, settings: Settings) -> None:
        self._transport = transport
        self._settings = settings
        self._authenticated = False
        self._authenticated_at: float | None = None
        self._generation = 0
        self._login_lock = asyncio.Lock()
        self.login_attempts = 0
        self.successful_logins = 0

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def session_age_s(self) -> float | None:
        if self._authenticated_at is None:
            return None
        return round(time.monotonic() - self._authenticated_at, 1)

    async def ensure_login(self) -> None:
        if self._authenticated:
            return
        async with self._login_lock:
            if self._authenticated:
                return
            await self._login()

    async def request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        await self.ensure_login()
        generation = self._generation
        response = await self._transport.request(method, path, **kwargs)  # type: ignore[arg-type]

        if not is_session_expired(response):
            return response

        logger.info("portal session expired; re-authenticating (generation %s)", generation)
        await self._reauthenticate(generation)
        response = await self._transport.request(method, path, **kwargs)  # type: ignore[arg-type]
        if is_session_expired(response):
            raise PortalAuthError("portal rejected the session immediately after re-authentication")
        return response

    async def _reauthenticate(self, seen_generation: int) -> None:
        async with self._login_lock:
            if self._generation != seen_generation and self._authenticated:
                return
            self._authenticated = False
            await self._login()

    async def _login(self) -> None:
        self.login_attempts += 1
        try:
            login_page = await self._transport.request("GET", "/login")
            if login_page.status_code != 200:
                raise PortalAuthError(f"login page returned HTTP {login_page.status_code}")
            form = parse_login_page(login_page.text)
            response = await self._transport.request(
                "POST",
                form["action"],
                data={
                    form["email_field"]: self._settings.urja_username,
                    form["password_field"]: self._settings.urja_password.get_secret_value(),
                    "csrf_token": form["csrf_token"],
                },
            )
        except (ParseError, PortalError) as exc:
            self._authenticated = False
            if isinstance(exc, CircuitOpenError):
                raise
            self._transport.note_failure()
            raise PortalAuthError(f"portal login failed: {exc}") from exc

        if self._login_succeeded(response):
            self._authenticated = True
            self._authenticated_at = time.monotonic()
            self._generation += 1
            self.successful_logins += 1
            self._transport.note_success()
            logger.info("portal login succeeded (generation %s)", self._generation)
            return

        self._authenticated = False
        self._transport.note_failure()
        raise PortalAuthError(
            "portal login was rejected; check URJA_USERNAME/URJA_PASSWORD or lockout policy"
        )

    def _login_succeeded(self, response: httpx.Response) -> bool:
        if response.status_code in REDIRECT_STATUS:
            location = (response.headers.get("location") or "").lower()
            return "login" not in location
        if response.status_code == 200:
            return not is_login_html(response.text)
        return False
