from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceInfo(BaseModel):
    """Provenance for data that crossed the portal boundary."""

    system: str = Field(default="urja-meter-ops", examples=["urja-meter-ops"])
    source: str = Field(description="Portal data path used, e.g. bulk_csv or meter_page")
    fetched_at: datetime = Field(description="When the adapter fetched this data (UTC).")


class DataQualitySummary(BaseModel):
    flagged_meters: int = 0
    duplicate_ids: int = 0
    malformed_rows: int = 0
    missing_serial: int = 0
    missing_coordinates: int = 0
    unknown_status: int = 0


class ProblemDetail(BaseModel):
    """RFC 9457 application/problem+json body."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "type": "about:blank",
                "title": "Not found",
                "status": 404,
                "detail": "meter detail MTR-9999 was not found on the portal",
                "instance": "/api/v1/meters/MTR-9999",
                "request_id": "c0f1d8e6fca54e0e8f3f2c1e9d7a6b5c",
            }
        },
    )

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    request_id: str | None = None
    errors: list[dict] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    portal_ok: bool
    session_authenticated: bool
    breaker: Literal["closed", "open", "half_open"]
    cache_entries: int
    cache_refreshes_inflight: int
    detail: str | None = None


class CacheRefreshResponse(BaseModel):
    invalidated_entries: int
    meters_reloaded: int
    hierarchy_reloaded: int
    refreshed_at: datetime
