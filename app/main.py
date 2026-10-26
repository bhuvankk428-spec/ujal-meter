from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import admin, health, hierarchy, meters
from app.api.errors import register_exception_handlers
from app.config import Settings, get_settings
from app.models.common import ProblemDetail
from app.observability import configure_logging, request_middleware
from app.portal.client import CircuitBreaker, PortalTransport
from app.portal.gateway import PortalGateway
from app.portal.session import PortalSession
from app.services.cache import TTLCache
from app.services.hierarchy_service import HierarchyService
from app.services.meter_service import MeterService

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

DESCRIPTION = """
A read-only, resilient REST API in front of the legacy **Urja Meter Ops** portal.

The portal is owned by the adapter: clients never see HTML, cookies, or portal
credentials. Fetched data is normalized into typed models, cached with
stale-while-revalidate, and data-quality defects are surfaced as `quality_flags`
instead of being silently corrected.

**Try it:** `GET /api/v1/meters`, `GET /api/v1/hierarchy`, `GET /ready`.
"""


def create_app(
    settings: Settings | None = None,
    portal_http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if portal_http_client_factory is not None:
            http_client = portal_http_client_factory()
        else:
            http_client = httpx.AsyncClient(
                base_url=settings.base_url,
                headers={"User-Agent": settings.urja_user_agent},
                follow_redirects=False,
            )

        breaker = CircuitBreaker(
            threshold=settings.breaker_failure_threshold,
            reset_timeout_s=settings.breaker_reset_timeout_s,
        )
        transport = PortalTransport(
            http_client,
            connect_timeout_s=settings.portal_connect_timeout_s,
            read_timeout_s=settings.portal_read_timeout_s,
            total_timeout_s=settings.portal_total_timeout_s,
            max_concurrency=settings.portal_max_concurrency,
            max_retries=settings.portal_max_retries,
            retry_base_delay_s=settings.portal_retry_base_delay_s,
            breaker=breaker,
        )
        session = PortalSession(transport, settings)
        gateway = PortalGateway(session, transport, settings)
        cache = TTLCache(jitter_ratio=settings.cache_jitter_ratio)
        hierarchy_service = HierarchyService(gateway, cache, settings)
        meter_service = MeterService(gateway, cache, hierarchy_service, settings)

        app.state.settings = settings
        app.state.transport = transport
        app.state.session = session
        app.state.gateway = gateway
        app.state.cache = cache
        app.state.hierarchy_service = hierarchy_service
        app.state.meter_service = meter_service

        warm_task: asyncio.Task[None] | None = None
        if settings.warm_cache_on_startup:
            warm_task = asyncio.create_task(_warm(meter_service, hierarchy_service))

        try:
            yield
        finally:
            if warm_task is not None:
                warm_task.cancel()
                await asyncio.gather(warm_task, return_exceptions=True)
            await cache.aclose()
            await gateway.close()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "meters", "description": "Meter list, detail, consumption and geo search."},
            {"name": "hierarchy", "description": "Network tree with data-quality reporting."},
            {"name": "health", "description": "Liveness and portal readiness."},
            {"name": "admin", "description": "Cache maintenance; protected by X-API-Key."},
        ],
        contact={"name": "Flock Energy take-home"},
        license_info={"name": "MIT"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Cache", "X-Data-Age", "X-Request-ID", "X-Response-Time-Ms"],
    )
    app.middleware("http")(request_middleware)
    register_exception_handlers(app)

    app.include_router(meters.router, prefix="/api/v1")
    app.include_router(hierarchy.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(health.router)

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> Response:
        index_file = WEB_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(
            {
                "service": settings.app_name,
                "version": __version__,
                "docs": "/docs",
                "openapi": "/openapi.json",
                "api": "/api/v1/meters",
            }
        )

    @app.get("/reference", include_in_schema=False)
    async def scalar_reference() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html>
<html>
  <head><title>Urja Meter Ops API Reference</title><meta charset="utf-8" /></head>
  <body>
    <script id="api-reference" data-url="/openapi.json"></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>"""
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    _install_error_responses(app)
    return app


COMMON_PROBLEM_RESPONSES: dict[str, str] = {
    "500": "Unexpected server error",
    "502": "Portal response could not be parsed",
    "503": "Portal unavailable, circuit open, or authentication failed",
    "504": "Portal request timed out",
}

PATH_PROBLEM_RESPONSES: dict[str, dict[str, str]] = {
    "/api/v1/meters/{meter_id}": {"404": "Unknown meter"},
    "/api/v1/meters/{meter_id}/consumption": {"404": "Unknown meter"},
    "/api/v1/hierarchy": {"404": "Unknown hierarchy root"},
    "/api/v1/admin/cache/refresh": {"401": "Missing or invalid X-API-Key"},
    "/ready": {"503": "Portal is not ready"},
}


def _install_error_responses(app: FastAPI) -> None:
    """Document the application/problem+json shape on every operation."""
    base_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = base_openapi()
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        schemas["ProblemDetail"] = ProblemDetail.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        reference = {"$ref": "#/components/schemas/ProblemDetail"}
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes["ApiKeyAuth"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }
        for path, operations in schema.get("paths", {}).items():
            extra = PATH_PROBLEM_RESPONSES.get(path, {})
            for method, operation in operations.items():
                if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if path == "/api/v1/admin/cache/refresh":
                    operation["security"] = [{"ApiKeyAuth": []}]
                responses = operation.setdefault("responses", {})
                for status, description in {**COMMON_PROBLEM_RESPONSES, **extra}.items():
                    responses.setdefault(
                        status,
                        {
                            "description": description,
                            "content": {"application/problem+json": {"schema": reference}},
                        },
                    )
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


async def _warm(meter_service: MeterService, hierarchy_service: HierarchyService) -> None:
    try:
        snapshot = await meter_service.get_snapshot()
        await hierarchy_service.get_hierarchy(
            root=None, depth=None, meter_counts=snapshot.value.zone_counts()
        )
        logger.info("cache warm-up completed with %s meters", len(snapshot.value.items))
    except Exception as exc:
        logger.warning("cache warm-up skipped: %s", exc)


app = create_app()
