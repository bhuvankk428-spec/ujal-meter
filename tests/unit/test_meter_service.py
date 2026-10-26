from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.portal.exceptions import ParseError
from app.services.cache import TTLCache
from app.services.meter_service import MeterService


class FakeGateway:
    def __init__(
        self,
        *,
        csv_rows: list[dict[str, str]] | None = None,
        csv_error: Exception | None = None,
        list_rows: list[dict[str, str]] | None = None,
    ) -> None:
        self.csv_rows = csv_rows or []
        self.csv_error = csv_error
        self.list_rows = list_rows or []

    async def fetch_meters_csv(self) -> list[dict[str, str]]:
        if self.csv_error is not None:
            raise self.csv_error
        return self.csv_rows

    async def fetch_all_meter_pages(self) -> list[dict[str, str]]:
        return self.list_rows


class FakeHierarchy:
    async def path_for(self, zone_id: str | None) -> list[str]:
        return []


def make_service(gateway: Any, settings: Settings) -> MeterService:
    return MeterService(gateway, TTLCache(jitter_ratio=0), FakeHierarchy(), settings)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_csv_failure_falls_back_to_list_pages(settings: Settings) -> None:
    gateway = FakeGateway(
        csv_error=ParseError("export unavailable"),
        list_rows=[
            {
                "meter_id": " MTR-0001 ",
                "serial_no": "",
                "display_name": "",
                "status": "1",
                "zone_id": "zn-n1",
            }
        ],
    )
    snapshot = await make_service(gateway, settings).get_snapshot()
    assert snapshot.value.source == "list_pages"
    assert len(snapshot.value.items) == 1
    meter = snapshot.value.items[0]
    assert meter.id == "MTR-0001"
    assert meter.display_name == "MTR-0001"
    assert meter.status.value == "ACTIVE"
    assert {"name_missing", "serial_missing"} <= set(meter.quality_flags)


@pytest.mark.asyncio
async def test_duplicate_keeps_primary_fault_and_reports_conflict(settings: Settings) -> None:
    primary = {
        "meter_id": "MTR-0004",
        "serial_no": "SN-0004",
        "display_name": "Jayanagar 4th Block",
        "status": "FAULT",
        "zone_id": "ZN-N1",
        "latitude": "12.99",
        "longitude": "77.55",
        "installed_on": "2025-04-13",
        "last_seen": "2026-02-28T23:59:00+05:30",
        "last_reading": "0",
        "last_reading_unit": "kWh",
    }
    duplicate = {
        **primary,
        "serial_no": "SN-0004-B",
        "status": "ACTIVE",
    }
    gateway = FakeGateway(csv_rows=[primary, duplicate])
    snapshot = await make_service(gateway, settings).get_snapshot()
    assert snapshot.value.quality.duplicate_ids == 1
    assert len(snapshot.value.items) == 1
    meter = snapshot.value.items[0]
    assert meter.serial_number == "SN-0004"
    assert meter.status.value == "FAULT"
    assert {"duplicate_in_source", "conflicting_source_rows"} <= set(meter.quality_flags)
