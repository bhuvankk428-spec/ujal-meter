from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import SourceInfo


class Granularity(StrEnum):
    HOURLY = "hourly"
    DAILY = "daily"
    MONTHLY = "monthly"


class ConsumptionPoint(BaseModel):
    timestamp: datetime = Field(description="Interval start, normalized to UTC.")
    value: float | None = Field(description="Consumption in the response unit; null if missing.")
    quality_flags: list[str] = Field(default_factory=list)


class ConsumptionSummary(BaseModel):
    total: float | None
    minimum: float | None
    maximum: float | None
    average: float | None
    non_null_points: int
    missing_points: int
    unit: str


class ConsumptionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    meter_id: str
    from_time: datetime = Field(alias="from", description="Requested range start (UTC).")
    to: datetime = Field(description="Requested range end (UTC).")
    granularity: Granularity
    unit: str = Field(description="Energy unit, e.g. kWh.")
    points: list[ConsumptionPoint]
    summary: ConsumptionSummary
    quality_flags: list[str] = Field(
        default_factory=list, description="Series-level defects (gaps, negatives, duplicates)."
    )
    source: SourceInfo
