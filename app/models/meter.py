from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.common import DataQualitySummary, SourceInfo


class MeterStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    FAULT = "FAULT"
    UNKNOWN = "UNKNOWN"


class Meter(BaseModel):
    id: str = Field(description="Canonical, case-insensitive meter id used by this API.")
    portal_id: str = Field(description="Canonical portal id (trimmed/uppercased).")
    serial_number: str | None = Field(default=None, description="Physical meter serial, if known.")
    display_name: str
    status: MeterStatus
    status_raw: str | None = Field(description="Unmodified portal status vocabulary.")
    zone_id: str | None = None
    zone_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    install_date: date | None = None
    last_seen_at: datetime | None = Field(
        default=None, description="Last communication time, normalized to UTC."
    )
    last_reading: float | None = None
    last_reading_unit: str | None = None
    quality_flags: list[str] = Field(
        default_factory=list,
        description="Non-fatal source data defects surfaced instead of hidden.",
    )


class NearbyMeter(Meter):
    distance_km: float


class MeterDetail(Meter):
    address: str | None = None
    meter_type: str | None = None
    phase: str | None = None
    multiplier: float | None = None
    hierarchy_path: list[str] = Field(
        default_factory=list, description="Zone names from network root to the meter's zone."
    )
    source: SourceInfo


class MeterListResponse(BaseModel):
    items: list[Meter]
    page: int
    page_size: int
    total: int
    next_page: int | None
    data_quality: DataQualitySummary
    source: SourceInfo


class GeoSearchResponse(BaseModel):
    items: list[NearbyMeter]
    page: int = 1
    page_size: int
    total: int
    next_page: int | None = None
    center_lat: float
    center_lon: float
    radius_km: float
    data_quality: DataQualitySummary
    source: SourceInfo


class StatusCounts(BaseModel):
    active: int = 0
    inactive: int = 0
    fault: int = 0
    unknown: int = 0


class StatsSummary(BaseModel):
    total_meters: int
    status_counts: StatusCounts
    zones: int
    with_coordinates: int
    flagged_meters: int
    source: SourceInfo
