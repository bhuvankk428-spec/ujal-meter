from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.consumption import Granularity
from app.models.meter import MeterStatus
from app.portal.normalize import (
    PortalNormalizer,
    normalize_id,
    normalize_status,
    parse_number,
    parse_portal_date,
    parse_portal_datetime,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.parametrize(
    ("raw", "value", "unit", "flags"),
    [
        ("1,24,500", 124500.0, None, []),
        ("124.50 kWh", 124.5, "kWh", []),
        ("1.234,56", 1234.56, None, ["decimal_comma"]),
        ("12,5", 12.5, None, ["decimal_comma"]),
        ("1,234.56", 1234.56, None, []),
        ("1 234.5", 1234.5, None, []),
        ("-", None, None, []),
        ("N/A", None, None, []),
        ("", None, None, []),
        ("oops", None, None, ["number_parse_error"]),
    ],
)
def test_parse_number(raw: str, value: float | None, unit: str | None, flags: list[str]) -> None:
    parsed, parsed_unit, parsed_flags = parse_number(raw)
    assert parsed == value
    assert parsed_unit == unit
    assert parsed_flags == flags


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Active", MeterStatus.ACTIVE),
        ("ACTIVE", MeterStatus.ACTIVE),
        ("active", MeterStatus.ACTIVE),
        ("1", MeterStatus.ACTIVE),
        ("0", MeterStatus.INACTIVE),
        ("Inactive", MeterStatus.INACTIVE),
        ("FAULT", MeterStatus.FAULT),
        ("Faulty", MeterStatus.FAULT),
        ("N/A", MeterStatus.UNKNOWN),
        ("", MeterStatus.UNKNOWN),
        ("mystery", MeterStatus.UNKNOWN),
    ],
)
def test_status_vocabulary(raw: str, expected: MeterStatus) -> None:
    status, status_raw, flags = normalize_status(raw)
    assert status is expected
    assert status_raw == raw
    assert set(flags).issubset({"status_missing", "status_vocabulary_unmapped"})


def test_normalize_id() -> None:
    assert normalize_id(" mtr-0011 ") == "MTR-0011"
    assert normalize_id("  mtr\t 0012 ") == "MTR0012"
    assert normalize_id("N/A") is None


def test_dates_and_ambiguity() -> None:
    assert parse_portal_date("2026-03-01") == (date(2026, 3, 1), [])
    parsed, flags = parse_portal_date("01/03/2026")
    assert parsed == date(2026, 3, 1)
    assert "date_ambiguous_day_first_assumed" in flags
    assert parse_portal_date("13/03/2026")[0] == date(2026, 3, 13)
    assert parse_portal_date("2026-13-40")[0] is None
    assert "invalid_date" in parse_portal_date("2026-13-40")[1]


def test_datetime_normalization() -> None:
    parsed, flags = parse_portal_datetime("2026-03-01 10:15:00", IST)
    assert parsed == datetime(2026, 3, 1, 4, 45, tzinfo=UTC)
    assert "naive_datetime_assumed_portal_tz" in flags

    parsed, flags = parse_portal_datetime("2026-03-01T10:15:00+05:30", IST)
    assert parsed == datetime(2026, 3, 1, 4, 45, tzinfo=UTC)
    assert flags == []

    parsed, flags = parse_portal_datetime("2026-13-40T10:15:00", IST)
    assert parsed is None
    assert flags == ["invalid_datetime"]


def test_meter_normalization_surfaces_defects() -> None:
    normalizer = PortalNormalizer("Asia/Kolkata")
    meter = normalizer.meter(
        {
            "meter_id": " mtr-0099 ",
            "serial_no": "N/A",
            "display_name": "",
            "status": "1",
            "zone_id": " zn-n1 ",
            "zone_name": "Yelahanka",
            "latitude": "",
            "longitude": "",
            "installed_on": "01/03/2026",
            "last_seen": "2026-03-01 10:15:00",
            "last_reading": "1,24,500",
            "last_reading_unit": "kWh",
            "meter_type": "Smart",
        }
    )
    assert meter.id == "MTR-0099"
    assert meter.display_name == "MTR-0099"
    assert meter.serial_number is None
    assert meter.status is MeterStatus.ACTIVE
    assert meter.zone_id == "ZN-N1"
    assert meter.last_reading == 124500.0
    assert meter.last_reading_unit == "kWh"
    assert meter.last_seen_at == datetime(2026, 3, 1, 4, 45, tzinfo=UTC)
    assert meter.install_date == date(2026, 3, 1)
    assert {
        "name_missing",
        "serial_missing",
        "date_ambiguous_day_first_assumed",
        "naive_datetime_assumed_portal_tz",
    }.issubset(set(meter.quality_flags))


def test_consumption_normalization_flags_without_fixing() -> None:
    normalizer = PortalNormalizer("Asia/Kolkata")
    payload = {
        "unit": "kWh",
        "points": [
            {"timestamp": "2026-02-01T00:00:00+05:30", "value": "10.5"},
            {"timestamp": "2026-02-01T00:00:00+05:30", "value": "11.0"},
            {"timestamp": "2026-02-03T00:00:00+05:30", "value": "-2"},
            {"timestamp": "2026-02-04T00:00:00+05:30", "value": "-"},
            {"timestamp": "not-a-date", "value": "1"},
        ],
    }
    points, summary, granularity, unit, flags = normalizer.consumption(
        payload,
        requested_from=datetime(2026, 2, 1, tzinfo=UTC),
        requested_to=datetime(2026, 2, 5, tzinfo=UTC),
        requested_granularity=Granularity.DAILY,
        source=None,
    )
    assert len(points) == 3
    assert unit == "kWh"
    assert granularity.value == "daily"
    assert summary.total == 8.5
    assert summary.missing_points == 1
    assert {
        "duplicate_timestamps_dropped",
        "negative_values",
        "missing_values",
        "series_gaps",
        "invalid_timestamps_dropped",
    }.issubset(set(flags))
