from __future__ import annotations

from typing import Any

from fastapi import Request, Response

from app.config import Settings
from app.portal.gateway import PortalGateway
from app.services.cache import CacheResult, TTLCache
from app.services.hierarchy_service import HierarchyService
from app.services.meter_service import MeterService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_meter_service(request: Request) -> MeterService:
    return request.app.state.meter_service


def get_hierarchy_service(request: Request) -> HierarchyService:
    return request.app.state.hierarchy_service


def get_gateway(request: Request) -> PortalGateway:
    return request.app.state.gateway


def get_cache(request: Request) -> TTLCache:
    return request.app.state.cache


def apply_cache_headers(response: Response, result: CacheResult[Any]) -> None:
    response.headers["X-Cache"] = result.state.value
    response.headers["X-Data-Age"] = f"{result.age_s:.0f}"
