from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models.common import ProblemDetail
from app.portal.exceptions import (
    CircuitOpenError,
    ParseError,
    PortalAuthError,
    PortalNotFoundError,
    PortalTimeoutError,
    PortalUnavailableError,
)

logger = logging.getLogger(__name__)


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    headers: dict[str, str] | None = None,
    errors: list[dict] | None = None,
) -> JSONResponse:
    body = ProblemDetail(
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        request_id=getattr(request.state, "request_id", None),
        errors=errors,
    )
    return JSONResponse(
        status_code=status,
        content=jsonable_encoder(body),
        media_type="application/problem+json",
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PortalNotFoundError)
    async def portal_not_found(request: Request, exc: PortalNotFoundError) -> JSONResponse:
        return problem_response(request, status=404, title="Not found", detail=str(exc))

    @app.exception_handler(ParseError)
    async def parse_error(request: Request, exc: ParseError) -> JSONResponse:
        logger.warning("portal parse failure: %s", exc)
        return problem_response(
            request,
            status=502,
            title="Portal response could not be understood",
            detail=str(exc),
        )

    @app.exception_handler(PortalTimeoutError)
    async def portal_timeout(request: Request, exc: PortalTimeoutError) -> JSONResponse:
        return problem_response(
            request,
            status=504,
            title="Portal timeout",
            detail=str(exc),
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(CircuitOpenError)
    async def circuit_open(request: Request, exc: CircuitOpenError) -> JSONResponse:
        retry_after = "15"
        transport = getattr(request.app.state, "transport", None)
        if transport is not None:
            retry_after = str(transport.breaker.retry_after_s or 15)
        return problem_response(
            request,
            status=503,
            title="Portal temporarily unavailable",
            detail=str(exc),
            headers={"Retry-After": retry_after},
        )

    @app.exception_handler(PortalAuthError)
    async def portal_auth(request: Request, exc: PortalAuthError) -> JSONResponse:
        return problem_response(
            request,
            status=503,
            title="Portal authentication unavailable",
            detail=str(exc),
            headers={"Retry-After": "30"},
        )

    @app.exception_handler(PortalUnavailableError)
    async def portal_unavailable(request: Request, exc: PortalUnavailableError) -> JSONResponse:
        return problem_response(
            request,
            status=503,
            title="Portal unavailable",
            detail=str(exc),
            headers={"Retry-After": "10"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            request,
            status=422,
            title="Invalid request parameters",
            detail="One or more query or path parameters are invalid.",
            errors=jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        title = HTTPStatus(exc.status_code).phrase
        detail = str(exc.detail)
        headers = dict(exc.headers or {})
        return problem_response(
            request, status=exc.status_code, title=title, detail=detail, headers=headers
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error", exc_info=exc)
        return problem_response(
            request,
            status=500,
            title="Internal server error",
            detail="An unexpected error occurred. Quote the request id when reporting it.",
        )
