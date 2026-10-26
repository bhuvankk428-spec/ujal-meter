"""Deterministic defective dataset used by the mock portal."""

from __future__ import annotations

import calendar
import csv
import io
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

PORTAL_TZ = ZoneInfo("Asia/Kolkata")

ZONES: list[dict[str, str]] = [
    {
        "node_id": "ZN-ROOT",
        "parent_id": "",
        "name": "Bengaluru Network",
        "type": "network",
        "level": "0",
    },
    {"node_id": "ZN-NORTH", "parent_id": "ZN-ROOT", "name": "North", "type": "zone", "level": "L1"},
    {
        "node_id": "ZN-N1",
        "parent_id": "ZN-NORTH",
        "name": "Yelahanka",
        "type": "feeder",
        "level": "level-2",
    },
    {"node_id": "ZN-N2", "parent_id": "ZN-NORTH", "name": "Hebbal", "type": "feeder", "level": "3"},
    {"node_id": "ZN-SOUTH", "parent_id": "ZN-ROOT", "name": "South", "type": "zone", "level": "1"},
    {
        "node_id": "ZN-S1",
        "parent_id": "ZN-SOUTH",
        "name": "Jayanagar",
        "type": "feeder",
        "level": "L2",
    },
    {"node_id": "ZN-EAST", "parent_id": "ZN-ROOT", "name": "East", "type": "zone", "level": "1"},
    {
        "node_id": "ZN-E1",
        "parent_id": "ZN-EAST",
        "name": "Whitefield",
        "type": "feeder",
        "level": "L2",
    },
    {"node_id": "ZN-N2", "parent_id": "ZN-EAST", "name": "Hebbal", "type": "feeder", "level": "3"},
    {"node_id": "ZN-ISLAND", "parent_id": "", "name": "Island Zone", "type": "zone", "level": "1"},
    {
        "node_id": "ZN-ORPHAN",
        "parent_id": "ZN-GHOST",
        "name": "Orphan Feeder",
        "type": "feeder",
        "level": "2",
    },
    {
        "node_id": "ZN-CYC-A",
        "parent_id": "ZN-CYC-B",
        "name": "Cycle A",
        "type": "feeder",
        "level": "2",
    },
    {
        "node_id": "ZN-CYC-B",
        "parent_id": "ZN-CYC-A",
        "name": "Cycle B",
        "type": "feeder",
        "level": "2",
    },
]

ZONE_NAMES = {zone["node_id"]: zone["name"] for zone in ZONES}

_NAMES = [
    "Gandhi Nagar Feeder",
    "Yelahanka Main",
    "Hebbal Industrial",
    "Jayanagar 4th Block",
    "Whitefield Tech Park",
    "MG Road Commercial",
    "Koramangala 5th Block",
    "Indiranagar 100ft",
    "Malleshwaram West",
    "Rajajinagar Block 2",
    "Banashankari Stage 3",
    "JP Nagar Phase 6",
    "Bellandur Lake View",
    "Sarjapur Road",
    "Electronic City Phase 1",
    "HSR Layout Sector 2",
    "Island Substation",
    "Orphan Pump House",
    "Cycle Industrial",
    "Unmapped Layout",
    "Yeshwanthpur Yard",
    "Peenya Industrial",
    "Dasarahalli Feeder",
    "Kengeri Township",
]

_STATUSES = [
    "Active",
    "Active",
    "active",
    "FAULT",
    "Inactive",
    "1",
    "0",
    "N/A",
    "",
    "ACTIVE",
    "active",
    "Active",
    "Active",
    "Active",
    "Active",
    "ACTIVE",
    "Active",
    "Fault",
    "Inactive",
    "ACTIVE",
    "Active",
    "ACTIVE",
    "Active",
    "Active",
]

_ZONES = [
    "ZN-N1",
    "ZN-N1",
    "ZN-N1",
    "ZN-N1",
    "ZN-N1",
    "ZN-N2",
    "ZN-N2",
    "ZN-N2",
    "ZN-N2",
    "ZN-N2",
    "ZN-S1",
    "ZN-S1",
    "ZN-S1",
    "ZN-E1",
    "ZN-E1",
    "ZN-E1",
    "ZN-ISLAND",
    "ZN-ORPHAN",
    "ZN-CYC-A",
    "ZN-404",
    "ZN-ROOT",
    "ZN-ROOT",
    "ZN-ROOT",
    "ZN-ROOT",
]

_INSTALLED = [
    "2025-01-10",
    "2025-02-11",
    "2025-03-12",
    "2025-04-13",
    "2025-05-14",
    "2025-06-15",
    "2025-07-16",
    "2025-08-17",
    "2025-09-18",
    "2025-10-19",
    "2025-11-20",
    "01/03/2026",
    "1 Mar 26",
    "2026-01-05",
    "2026-01-06",
    "2026-01-07",
    "2026-01-08",
    "2026-13-40",
    "2026-01-10",
    "2026-01-11",
    "2026-01-12",
    "2026-02-30",
    "2026-01-14",
    "2026-01-15",
]

_LAST_SEEN = [
    "2026-03-01T10:15:00+05:30",
    "2026-03-01 10:15:00",
    "01/03/2026 10:15",
    "2026-02-28T23:59:00+05:30",
    "2026-02-28T18:00:00+05:30",
    "2026-03-01T09:00:00+05:30",
    "2026-02-20T07:45:00+05:30",
    "N/A",
    "2026-03-01T08:00:00+05:30",
    "2026-03-01T11:00:00+05:30",
    "2026-03-01T12:00:00+05:30",
    "2026-03-01T13:00:00+05:30",
    "2026-03-01T14:00:00+05:30",
    "2026-03-01T15:00:00+05:30",
    "2026-03-01T16:00:00+05:30",
    "2026-03-01T17:00:00+05:30",
    "2026-03-01T18:00:00+05:30",
    "2026-13-40T00:00:00",
    "2026-03-01T19:00:00+05:30",
    "2026-03-01T20:00:00+05:30",
    "2026-03-01T21:00:00+05:30",
    "2026-03-01T22:00:00+05:30",
    "2026-03-01T23:00:00+05:30",
    "2026-03-02T00:00:00+05:30",
]

_LAST_READING = [
    "120.5",
    "98.2",
    "67.8",
    "0",
    "45.0",
    "310.2",
    "0",
    "N/A",
    "-",
    "76.4",
    "88.0",
    "99.5",
    "120.0",
    "1,24,500",
    "124.50 kWh",
    "1.234,56",
    "12,5",
    "N/A",
    "33.3",
    "44.4",
    "55.5",
    "66.6",
    "77.7",
    "-",
]

_READING_UNITS = [
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "",
    "kWh",
    "kW",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
    "kWh",
]


def _meter_record(index: int) -> dict[str, str]:
    number = index + 1
    meter_id = f"MTR-{number:04d}"
    if number == 11:
        meter_id = " MTR-0011 "
    serial = f"SN-{number:04d}"
    if number == 10:
        serial = "N/A"
    elif number == 11:
        serial = " SN-0011 "
    elif number == 22:
        serial = "sn-0022"
    zone_id = _ZONES[index]
    lat = 12.92 + (number % 10) * 0.018
    lon = 77.54 + (number // 10) * 0.035
    if number in (8, 20, 21):
        lat_text = ""
        lon_text = ""
    else:
        lat_text = f"{lat:.5f}"
        lon_text = f"{lon:.5f}"
    name = _NAMES[index]
    if number == 11:
        name = ""
    elif number == 21:
        name = "N/A"
    return {
        "meter_id": meter_id,
        "serial_no": serial,
        "display_name": name,
        "status": _STATUSES[index],
        "zone_id": zone_id,
        "zone_name": ZONE_NAMES.get(zone_id, "Unmapped"),
        "latitude": lat_text,
        "longitude": lon_text,
        "installed_on": _INSTALLED[index],
        "last_seen": _LAST_SEEN[index],
        "last_reading": _LAST_READING[index],
        "last_reading_unit": _READING_UNITS[index],
        "meter_type": "Smart" if number % 3 else "Legacy",
        "address": f"{number} {name or 'Unnamed'} Road, Bengaluru",
        "phase": "3-phase" if number % 2 else "1-phase",
        "multiplier": "12,5" if number == 17 else "1.0",
    }


METERS: list[dict[str, str]] = [_meter_record(i) for i in range(24)]

METERS_BY_ID: dict[str, dict[str, str]] = {}
for _record in METERS:
    _canonical = _record["meter_id"].strip().upper()
    METERS_BY_ID[_canonical] = _record

_MTR_0011 = METERS_BY_ID["MTR-0011"]
_MTR_0011 = dict(_MTR_0011)
_MTR_0011["serial_no"] = "SN-0011-REV"
_MTR_0011["display_name"] = ""
METERS_BY_ID["MTR-0011"] = _MTR_0011


def csv_rows() -> list[dict[str, str]]:
    rows = [dict(record) for record in METERS]
    duplicate = dict(METERS[3])
    duplicate["serial_no"] = "SN-0004-B"
    duplicate["status"] = "ACTIVE"
    rows.insert(4, duplicate)
    return rows


CSV_COLUMNS = [
    "meter_id",
    "serial_no",
    "display_name",
    "status",
    "zone_id",
    "zone_name",
    "latitude",
    "longitude",
    "installed_on",
    "last_seen",
    "last_reading",
    "last_reading_unit",
    "meter_type",
]


def meters_csv() -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in csv_rows():
        writer.writerow(row)
    return "\ufeff" + buffer.getvalue()


def hierarchy_rows() -> list[dict[str, str]]:
    return [dict(row) for row in ZONES]


def consumption_payload(
    meter_id: str,
    *,
    from_: datetime,
    to: datetime,
    granularity: str,
) -> dict[str, object]:
    canonical = meter_id.strip().upper()
    local_from = from_.astimezone(PORTAL_TZ)
    local_to = to.astimezone(PORTAL_TZ)
    if local_from >= local_to:
        local_to = local_from + timedelta(days=1)

    stamps = _timestamps(local_from, local_to, granularity)
    number = int(canonical.split("-")[-1]) if canonical[-4:].isdigit() else 7
    base = 12 + (number * 7) % 23
    points: list[dict[str, object]] = []
    for index, stamp in enumerate(stamps):
        value = base + 6 * math.sin((index + number) / 3.1)
        value = round(max(0.5, value), 2)
        raw_value: object = f"{value:.2f}"
        timestamp = stamp.isoformat()

        if number == 14 and index == 2:
            raw_value = None
        elif number == 14 and index == 4:
            raw_value = "-"
        elif number == 15 and index == 3:
            raw_value = "-5.50"
        elif number == 17 and index % 3 == 2:
            continue
        elif number == 19 and index == 1:
            timestamp = stamp.replace(tzinfo=None).isoformat()
        elif number == 22 and index == 1:
            raw_value = f"{value:.2f} kWh"

        points.append({"timestamp": timestamp, "value": raw_value})
        if number == 16 and index == 2:
            points.append({"timestamp": timestamp, "value": f"{value + 1:.2f}"})

    if number == 3:
        points.reverse()

    total_value = 0.0
    for point in points:
        parsed = _as_number(point["value"])
        if parsed is not None:
            total_value += parsed
    total = round(total_value, 3)
    return {
        "meter_id": canonical,
        "unit": "kWh",
        "granularity": granularity,
        "from": local_from.isoformat(),
        "to": local_to.isoformat(),
        "points": points,
        "portal_total": total,
    }


def _timestamps(start: datetime, end: datetime, granularity: str) -> list[datetime]:
    stamps: list[datetime] = []
    if granularity == "hourly":
        current = start.replace(minute=0, second=0, microsecond=0)
        while current <= end and len(stamps) < 500:
            stamps.append(current)
            current += timedelta(hours=1)
    elif granularity == "monthly":
        current = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while current <= end and len(stamps) < 120:
            stamps.append(current)
            year = current.year + (1 if current.month == 12 else 0)
            month = 1 if current.month == 12 else current.month + 1
            current = current.replace(year=year, month=month)
    else:
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current <= end and len(stamps) < 500:
            stamps.append(current)
            current += timedelta(days=1)
    if not stamps:
        stamps.append(start)
    return stamps


def _as_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_portal_datetime(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.split("T")[0])
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PORTAL_TZ)
    return parsed.astimezone(UTC)


def month_name(value: datetime) -> str:
    return calendar.month_abbr[value.month]
