from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.deps import apply_cache_headers, get_hierarchy_service, get_meter_service
from app.models.hierarchy import HierarchyResponse
from app.portal.exceptions import PortalError
from app.services.hierarchy_service import HierarchyService
from app.services.meter_service import MeterService

router = APIRouter(tags=["hierarchy"])


@router.get(
    "/hierarchy",
    response_model=HierarchyResponse,
    summary="Network hierarchy",
    description=(
        "Builds a tree from the portal's flat hierarchy table and reports defects. "
        "Orphans and cycle-break nodes are attached under a synthetic __unassigned__ node. "
        "The response contains both a nested tree and a flat nodes array."
    ),
)
async def get_hierarchy(
    response: Response,
    root: str | None = Query(None, description="Optional node id to use as the subtree root."),
    depth: int | None = Query(None, ge=0, le=10, description="0 returns only the root."),
    hierarchy_service: HierarchyService = Depends(get_hierarchy_service),
    meter_service: MeterService = Depends(get_meter_service),
) -> HierarchyResponse:
    try:
        counts = await meter_service.zone_counts()
    except PortalError:
        counts = {}
    try:
        data, cache = await hierarchy_service.get_hierarchy(
            root=root, depth=depth, meter_counts=counts
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"hierarchy node {exc.args[0]} not found"
        ) from exc
    apply_cache_headers(response, cache)
    return data
