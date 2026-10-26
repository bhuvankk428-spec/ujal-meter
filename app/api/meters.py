from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import apply_cache_headers, get_meter_service
from app.models.consumption import ConsumptionResponse, Granularity
from app.models.meter import (
    GeoSearchResponse,
    MeterDetail,
    MeterListResponse,
    MeterStatus,
    StatsSummary,
)
from app.services.meter_service import MeterService

router = APIRouter(tags=["meters"])


@router.get(
    "/meters",
    response_model=MeterListResponse,
    summary="List meters",
    description=(
        "Reads the portal's bulk meter export (falling back to paginated list pages), "
        "normalizes it, and filters in-process. Responses may be served from a "
        "stale-while-revalidate cache; inspect X-Cache and X-Data-Age."
    ),
)
async def list_meters(
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: MeterStatus | None = Query(None),
    q: str | None = Query(None, max_length=100, description="Search id, serial, name or zone."),
    zone: str | None = Query(None, max_length=40),
    service: MeterService = Depends(get_meter_service),
) -> MeterListResponse:
    data, cache = await service.list_meters(
        page=page, page_size=page_size, status=status, query=q, zone=zone
    )
    apply_cache_headers(response, cache)
    return data


@router.get(
    "/meters/search",
    response_model=GeoSearchResponse,
    summary="Find meters near a point",
    description=(
        "Bounding-box prefilter followed by haversine distance. Coordinates come from "
        "the normalized bulk export; meters with missing/invalid coordinates are excluded."
    ),
)
async def search_meters(
    response: Response,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(5, ge=0.1, le=500),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: MeterStatus | None = Query(None),
    service: MeterService = Depends(get_meter_service),
) -> GeoSearchResponse:
    data, cache = await service.search_nearby(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        page=page,
        page_size=page_size,
        status=status,
    )
    apply_cache_headers(response, cache)
    return data


@router.get(
    "/meters/{meter_id}",
    response_model=MeterDetail,
    summary="Meter detail",
    description=(
        "Fetches the detail page and joins it with the cached hierarchy path. "
        "Conflicts between list and detail views are surfaced via quality_flags."
    ),
)
async def get_meter(
    response: Response,
    meter_id: str,
    service: MeterService = Depends(get_meter_service),
) -> MeterDetail:
    data, cache = await service.get_meter(meter_id)
    apply_cache_headers(response, cache)
    return data


@router.get(
    "/meters/{meter_id}/consumption",
    response_model=ConsumptionResponse,
    summary="Consumption series",
    description=(
        "Prefers the portal's XHR JSON endpoint over scraping the chart page. "
        "Missing, negative and duplicate points are preserved/flagged, never silently fixed."
    ),
)
async def get_consumption(
    response: Response,
    meter_id: str,
    granularity: Granularity = Query(Granularity.DAILY),
    from_: datetime | None = Query(
        None, alias="from", description="ISO-8601, UTC assumed if naive."
    ),
    to: datetime | None = Query(None, description="ISO-8601, default now."),
    service: MeterService = Depends(get_meter_service),
) -> ConsumptionResponse:
    try:
        data, cache = await service.get_consumption(
            meter_id, from_=from_, to=to, granularity=granularity
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    apply_cache_headers(response, cache)
    return data


@router.get(
    "/stats/summary",
    response_model=StatsSummary,
    summary="Fleet summary",
    description="Small aggregate over the cached meter snapshot; useful for dashboards.",
)
async def stats_summary(
    response: Response,
    service: MeterService = Depends(get_meter_service),
) -> StatsSummary:
    data, cache = await service.stats()
    apply_cache_headers(response, cache)
    return data
