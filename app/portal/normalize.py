"""Raw portal strings -> typed domain data.

Every lossy decision is recorded in ``quality_flags`` rather than silently
"fixing" the source. Rules are documented in PROTOCOL.md and covered by
table-driven tests.
"""

from __future__ import annotations

import itertools
import math
import re
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from app.models.consumption import (
    ConsumptionPoint,
    ConsumptionSummary,
    Granularity,
)
from app.models.meter import Meter, MeterDetail, MeterStatus

EMPTY_TOKENS = {"", "-", "--", "n/a", "na", "null", "none", "nan", "unknown?"}
_WS_RE = re.compile(r"[\s\u00a0]+")
_UNIT_RE = re.compile(r"^([+\-]?[0-9][0-9.,\s]*?)\s*([A-Za-z%][A-Za-z0-9%/._-]*)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d %b %y", "%d %b %Y", "%Y/%m/%d", "%d-%m-%Y")
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d %b %y %H:%M",
    "%d %b %Y %H:%M",
)

STATUS_MAP: dict[str, MeterStatus] = {
    "ACTIVE": MeterStatus.ACTIVE,
    "1": MeterStatus.ACTIVE,
    "ON": MeterStatus.ACTIVE,
    "RUNNING": MeterStatus.ACTIVE,
    "INACTIVE": MeterStatus.INACTIVE,
    "0": MeterStatus.INACTIVE,
    "OFF": MeterStatus.INACTIVE,
    "DISCONNECTED": MeterStatus.INACTIVE,
    "FAULT": MeterStatus.FAULT,
    "FAULTY": MeterStatus.FAULT,
    "ERROR": MeterStatus.FAULT,
    "ALARM": MeterStatus.FAULT,
}


def clean_value(raw: Any) -> str | None:
    if raw is None:
        return None
    text = _WS_RE.sub(" ", str(raw)).strip()
    if text.lower() in EMPTY_TOKENS:
        return None
    return text


def normalize_id(raw: Any) -> str | None:
    text = clean_value(raw)
    if text is None:
        return None
    return _WS_RE.sub("", text).upper()


def normalize_status(raw: Any) -> tuple[MeterStatus, str | None, list[str]]:
    raw_text = None if raw is None else str(raw)
    text = clean_value(raw)
    if text is None:
        return MeterStatus.UNKNOWN, raw_text, ["status_missing"]
    key = _WS_RE.sub("", text).upper()
    status = STATUS_MAP.get(key)
    if status is None:
        return MeterStatus.UNKNOWN, raw_text, ["status_vocabulary_unmapped"]
    return status, raw_text, []


def _normalize_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    unit = raw.strip()
    if not unit:
        return None
    canonical = {"kwh": "kWh", "kw": "kW", "mwh": "MWh", "wh": "Wh", "kva": "kVA"}
    return canonical.get(unit.lower(), unit)


def parse_number(raw: Any) -> tuple[float | None, str | None, list[str]]:
    """Parse legacy numeric strings, preserving units and ambiguity flags."""
    text = clean_value(raw)
    if text is None:
        return None, None, []
    text = text.replace("\u00a0", " ").strip()
    match = _UNIT_RE.match(text)
    if match is None:
        return None, None, ["number_parse_error"]
    number_part = match.group(1).replace(" ", "")
    unit = _normalize_unit(match.group(2))
    flags: list[str] = []
    try:
        if "," in number_part and "." in number_part:
            if number_part.rfind(",") > number_part.rfind("."):
                cleaned = number_part.replace(".", "").replace(",", ".")
                flags.append("decimal_comma")
            else:
                cleaned = number_part.replace(",", "")
        elif "," in number_part:
            if re.fullmatch(r"[+\-]?\d{1,2}(,\d{2})*,\d{3}", number_part):
                cleaned = number_part.replace(",", "")
            elif re.fullmatch(r"[+\-]?\d{1,3}(,\d{3})+", number_part):
                cleaned = number_part.replace(",", "")
            elif re.fullmatch(r"[+\-]?\d+,\d{1,2}", number_part):
                cleaned = number_part.replace(",", ".")
                flags.append("decimal_comma")
            else:
                cleaned = number_part.replace(",", "")
                flags.append("number_grouping_ambiguous")
        else:
            cleaned = number_part
        value = float(cleaned)
    except ValueError:
        return None, unit, [*flags, "number_parse_error"]
    if not math.isfinite(value):
        return None, unit, [*flags, "number_not_finite"]
    return value, unit, flags


def _ambiguous_dmy(parts: list[str]) -> bool:
    return len(parts) == 3 and len(parts[0]) <= 2 and len(parts[1]) <= 2


def parse_portal_date(raw: Any) -> tuple[date | None, list[str]]:
    text = clean_value(raw)
    if text is None:
        return None, []
    text = text.split("T")[0].split(" ")[0]
    flags: list[str] = []
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if "/" in text:
            parts = text.split()[0].split("/")
            if _ambiguous_dmy(parts) and int(parts[0]) <= 12 and int(parts[1]) <= 12:
                flags.append("date_ambiguous_day_first_assumed")
        return parsed, flags
    return None, ["invalid_date"]


def parse_portal_datetime(raw: Any, tz: ZoneInfo) -> tuple[datetime | None, list[str]]:
    """Return an aware UTC datetime. Naive portal values are assumed portal-local."""
    text = clean_value(raw)
    if text is None:
        return None, []
    flags: list[str] = []

    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        parsed = None
        for fmt in _DATETIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            break
        if parsed is None:
            parsed_date = None
            for fmt in _DATE_FORMATS:
                try:
                    parsed_date = datetime.strptime(text, fmt)
                except ValueError:
                    continue
                break
            if parsed_date is None:
                return None, ["invalid_datetime"]
            parsed = parsed_date
        if "/" in text:
            parts = text.split()[0].split("/")
            if _ambiguous_dmy(parts) and int(parts[0]) <= 12 and int(parts[1]) <= 12:
                flags.append("date_ambiguous_day_first_assumed")

    assert parsed is not None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
        flags.append("naive_datetime_assumed_portal_tz")
    return parsed.astimezone(UTC), flags


def _flag_missing(value: Any) -> bool:
    return clean_value(value) is None


class PortalNormalizer:
    def __init__(self, timezone: str = "Asia/Kolkata") -> None:
        self.tz = ZoneInfo(timezone)
        self.timezone = timezone

    def meter(self, raw: dict[str, Any]) -> Meter:
        meter_id = normalize_id(raw.get("meter_id"))
        flags: list[str] = []
        if meter_id is None:
            raise ValueError("meter row has no usable meter_id")

        status, status_raw, status_flags = normalize_status(raw.get("status"))
        flags.extend(status_flags)

        name = clean_value(raw.get("display_name"))
        if name is None:
            name = meter_id
            flags.append("name_missing")

        serial = clean_value(raw.get("serial_no"))
        if serial is None:
            flags.append("serial_missing")

        latitude, _, lat_flags = parse_number(raw.get("latitude"))
        longitude, _, lon_flags = parse_number(raw.get("longitude"))
        flags.extend(lat_flags)
        flags.extend(lon_flags)
        if latitude is not None and not -90 <= latitude <= 90:
            latitude = None
            flags.append("invalid_latitude")
        if longitude is not None and not -180 <= longitude <= 180:
            longitude = None
            flags.append("invalid_longitude")
        if (latitude is None) != (longitude is None):
            latitude = None
            longitude = None
            flags.append("incomplete_coordinates")

        install_date, date_flags = parse_portal_date(raw.get("installed_on"))
        flags.extend(date_flags)
        last_seen, seen_flags = parse_portal_datetime(raw.get("last_seen"), self.tz)
        flags.extend(seen_flags)

        reading, reading_unit, reading_flags = parse_number(raw.get("last_reading"))
        flags.extend(reading_flags)
        if reading_unit is None:
            reading_unit = clean_value(raw.get("last_reading_unit"))
            reading_unit = _normalize_unit(reading_unit)

        zone_id = normalize_id(raw.get("zone_id"))
        zone_name = clean_value(raw.get("zone_name"))

        return Meter(
            id=meter_id,
            portal_id=meter_id,
            serial_number=serial,
            display_name=name,
            status=status,
            status_raw=status_raw,
            zone_id=zone_id,
            zone_name=zone_name,
            latitude=latitude,
            longitude=longitude,
            install_date=install_date,
            last_seen_at=last_seen,
            last_reading=reading,
            last_reading_unit=reading_unit,
            quality_flags=_dedupe(flags),
        )

    def meter_detail(self, raw: dict[str, Any], source: Any) -> MeterDetail:
        base = self.meter(raw)
        flags = list(base.quality_flags)

        address = clean_value(raw.get("address"))
        meter_type = clean_value(raw.get("meter_type"))
        phase = clean_value(raw.get("phase"))
        multiplier, _, mult_flags = parse_number(raw.get("multiplier"))
        flags.extend(mult_flags)
        if multiplier is not None and multiplier <= 0:
            multiplier = None
            flags.append("invalid_multiplier")

        data = base.model_dump()
        data.update(
            {
                "address": address,
                "meter_type": meter_type,
                "phase": phase,
                "multiplier": multiplier,
                "hierarchy_path": [],
                "quality_flags": _dedupe(flags),
                "source": source,
            }
        )
        return MeterDetail(**data)

    def consumption(
        self,
        raw: dict[str, Any],
        *,
        requested_from: datetime,
        requested_to: datetime,
        requested_granularity: Granularity,
        source: Any,
    ) -> tuple[list[ConsumptionPoint], ConsumptionSummary, Granularity, str, list[str]]:
        unit = _normalize_unit(clean_value(raw.get("unit"))) or "kWh"
        granularity = requested_granularity
        flags: list[str] = []

        raw_points = raw.get("points") or []
        points: list[ConsumptionPoint] = []
        seen: set[datetime] = set()
        duplicate_count = 0
        missing_count = 0
        negative_count = 0
        naive_count = 0
        invalid_count = 0

        for raw_point in raw_points:
            timestamp, ts_flags = parse_portal_datetime(raw_point.get("timestamp"), self.tz)
            if timestamp is None:
                invalid_count += 1
                continue
            if "naive_datetime_assumed_portal_tz" in ts_flags:
                naive_count += 1
            if timestamp in seen:
                duplicate_count += 1
                continue
            seen.add(timestamp)

            value, _, value_flags = parse_number(raw_point.get("value"))
            point_flags = [*ts_flags, *value_flags]
            if value is None:
                missing_count += 1
                point_flags.append("missing_value")
            elif value < 0:
                negative_count += 1
                point_flags.append("negative_value")

            points.append(
                ConsumptionPoint(
                    timestamp=timestamp,
                    value=value,
                    quality_flags=_dedupe(point_flags),
                )
            )

        points.sort(key=lambda point: point.timestamp)

        gap_count = _count_gaps(points, granularity)
        values = [point.value for point in points if point.value is not None]
        summary = ConsumptionSummary(
            total=round(sum(values), 3) if values else None,
            minimum=min(values) if values else None,
            maximum=max(values) if values else None,
            average=round(sum(values) / len(values), 3) if values else None,
            non_null_points=len(values),
            missing_points=missing_count,
            unit=unit,
        )

        if missing_count:
            flags.append("missing_values")
        if negative_count:
            flags.append("negative_values")
        if duplicate_count:
            flags.append("duplicate_timestamps_dropped")
        if gap_count:
            flags.append("series_gaps")
        if naive_count:
            flags.append("naive_timestamps_assumed_portal_tz")
        if invalid_count:
            flags.append("invalid_timestamps_dropped")
        if not points:
            flags.append("empty_series")

        return points, summary, granularity, unit, _dedupe(flags)

    def hierarchy_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for raw in rows:
            node_id = normalize_id(raw.get("node_id"))
            if node_id is None:
                raise ValueError("hierarchy row has no usable node id")
            parent_id = normalize_id(raw.get("parent_id"))
            name = clean_value(raw.get("name")) or node_id
            node_type = (clean_value(raw.get("type")) or "unknown").lower()
            level_raw = clean_value(raw.get("level"))
            level: int | None = None
            flags: list[str] = []
            if level_raw is not None:
                match = re.search(r"\d+", level_raw)
                if match:
                    level = int(match.group())
                else:
                    flags.append("level_not_numeric")
            normalized.append(
                {
                    "id": node_id,
                    "parent_id": parent_id,
                    "name": name,
                    "type": node_type,
                    "level": level,
                    "level_raw": level_raw,
                    "quality_flags": flags,
                }
            )
        return normalized


def _count_gaps(points: list[ConsumptionPoint], granularity: Granularity) -> int:
    if len(points) < 2 or granularity is Granularity.MONTHLY:
        return 0
    expected = 3600.0 if granularity is Granularity.HOURLY else 86400.0
    gaps = 0
    for previous, current in itertools.pairwise(points):
        delta = (current.timestamp - previous.timestamp).total_seconds()
        if delta > expected * 1.5:
            gaps += 1
    return gaps


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def ensure_utc(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(UTC)


def day_bounds(value: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, tzinfo=tz)
    end = datetime.combine(value, time.max, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)
