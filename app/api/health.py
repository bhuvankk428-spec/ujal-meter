from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app import __version__
from app.api.deps import get_cache, get_gateway, get_settings_dep
from app.config import Settings
from app.models.common import HealthResponse, ReadyResponse
from app.portal.gateway import PortalGateway
from app.services.cache import TTLCache

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
async def health(
    settings: Settings = Depends(get_settings_dep),
) -> HealthResponse:
    return HealthResponse(
        service=settings.app_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness",
    description=(
        "Probes a real portal data page through the managed session (cached for a few "
        "seconds). Returns 503 when the portal is unreachable or the session cannot be restored."
    ),
)
async def ready(
    response: Response,
    request: Request,
    gateway: PortalGateway = Depends(get_gateway),
    cache: TTLCache = Depends(get_cache),
) -> ReadyResponse:
    portal_ok, detail = await gateway.probe()
    stats = cache.stats()
    payload = ReadyResponse(
        status="ready" if portal_ok else "degraded",
        portal_ok=portal_ok,
        session_authenticated=gateway.session.is_authenticated,
        breaker=gateway.breaker_state,  # type: ignore[arg-type]
        cache_entries=stats["entries"],
        cache_refreshes_inflight=stats["refreshes_inflight"],
        detail=detail,
    )
    if not portal_ok:
        response.status_code = 503
        response.headers["Retry-After"] = "10"
    return payload
