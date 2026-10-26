from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException

from app.api.deps import (
    get_cache,
    get_hierarchy_service,
    get_meter_service,
    get_settings_dep,
)
from app.config import Settings
from app.models.common import CacheRefreshResponse
from app.services.cache import TTLCache
from app.services.hierarchy_service import HierarchyService
from app.services.meter_service import MeterService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/cache/refresh",
    response_model=CacheRefreshResponse,
    summary="Invalidate and warm caches",
    description="Protected by the X-API-Key header. Safe to call: it only re-reads the portal.",
)
async def refresh_cache(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings_dep),
    cache: TTLCache = Depends(get_cache),
    meter_service: MeterService = Depends(get_meter_service),
    hierarchy_service: HierarchyService = Depends(get_hierarchy_service),
) -> CacheRefreshResponse:
    expected = settings.admin_api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="A valid X-API-Key header is required")

    invalidated = await cache.invalidate()
    meter_snapshot = await meter_service.reload_snapshot()
    counts = meter_snapshot.zone_counts()
    hierarchy, _ = await hierarchy_service.get_hierarchy(root=None, depth=None, meter_counts=counts)
    return CacheRefreshResponse(
        invalidated_entries=invalidated,
        meters_reloaded=len(meter_snapshot.items),
        hierarchy_reloaded=len(hierarchy.nodes),
        refreshed_at=datetime.now(UTC),
    )
