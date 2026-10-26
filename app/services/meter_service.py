"""Meter orchestration: bulk-first ingestion, filters, detail, consumption, geo."""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.common import DataQualitySummary, SourceInfo
from app.models.consumption import ConsumptionResponse, Granularity
from app.models.meter import (
    GeoSearchResponse,
    Meter,
    MeterDetail,
    MeterListResponse,
    MeterStatus,
    NearbyMeter,
    StatsSummary,
    StatusCounts,
)
from app.portal.exceptions import ParseError, PortalError, PortalNotFoundError
from app.portal.gateway import PortalGateway
from app.portal.normalize import PortalNormalizer, ensure_utc, normalize_id
from app.services.cache import CacheResult, TTLCache
from app.services.hierarchy_service import HierarchyService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MeterSnapshot:
    items: list[Meter]
    fetched_at: datetime
    quality: DataQualitySummary
    source: str

    def zone_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for meter in self.items:
            if meter.zone_id:
                counts[meter.zone_id] += 1
        return dict(counts)


class MeterService:
    def __init__(
        self,
        gateway: PortalGateway,
        cache: TTLCache,
        hierarchy: HierarchyService,
        settings: Settings,
    ) -> None:
        self._gateway = gateway
        self._cache = cache
        self._hierarchy = hierarchy
        self._settings = settings
        self._normalizer = PortalNormalizer(settings.urja_timezone)
        self._tz = ZoneInfo(settings.urja_timezone)

    async def get_snapshot(self) -> CacheResult[MeterSnapshot]:
        return await self._cache.get_or_load(
            "meters:snapshot",
            self._load_snapshot,
            fresh_ttl=self._settings.cache_meters_fresh_s,
            stale_ttl=self._settings.cache_meters_stale_s,
        )

    async def zone_counts(self) -> dict[str, int]:
        snapshot = await self.get_snapshot()
        return snapshot.value.zone_counts()

    async def reload_snapshot(self) -> MeterSnapshot:
        await self._cache.invalidate("meters:")
        result = await self.get_snapshot()
        return result.value

    async def _load_snapshot(self) -> MeterSnapshot:
        source = "bulk_csv"
        try:
            raw_rows = await self._gateway.fetch_meters_csv()
        except (ParseError, PortalNotFoundError) as exc:
            logger.warning("bulk meter export unavailable (%s); falling back to list pages", exc)
            raw_rows = await self._gateway.fetch_all_meter_pages()
            source = "list_pages"

        by_id: dict[str, Meter] = {}
        duplicates = 0
        malformed = 0
        for raw in raw_rows:
            try:
                meter = self._normalizer.meter(raw)
            except ValueError:
                malformed += 1
                continue
            existing = by_id.get(meter.id)
            if existing is None:
                by_id[meter.id] = meter
                continue
            duplicates += 1
            flags = [*existing.quality_flags, "duplicate_in_source"]
            if _completeness(meter) > _completeness(existing):
                flags.extend(meter.quality_flags)
                by_id[meter.id] = meter
            if (
                existing.serial_number
                and meter.serial_number
                and existing.serial_number != meter.serial_number
            ) or (
                existing.status is not meter.status
                and existing.status is not MeterStatus.UNKNOWN
                and meter.status is not MeterStatus.UNKNOWN
            ):
                flags.append("conflicting_source_rows")
            kept = by_id[meter.id]
            kept.quality_flags = _dedupe(flags)

        if not by_id:
            raise ParseError("meter export contained no usable rows after normalization")

        items = sorted(by_id.values(), key=lambda meter: meter.id)
        quality = DataQualitySummary(
            flagged_meters=sum(1 for meter in items if meter.quality_flags),
            duplicate_ids=duplicates,
            malformed_rows=malformed,
            missing_serial=sum(1 for meter in items if meter.serial_number is None),
            missing_coordinates=sum(
                1 for meter in items if meter.latitude is None or meter.longitude is None
            ),
            unknown_status=sum(1 for meter in items if meter.status is MeterStatus.UNKNOWN),
        )
        return MeterSnapshot(
            items=items,
            fetched_at=datetime.now(UTC),
            quality=quality,
            source=source,
        )

    async def list_meters(
        self,
        *,
        page: int,
        page_size: int,
        status: MeterStatus | None,
        query: str | None,
        zone: str | None,
    ) -> tuple[MeterListResponse, CacheResult[MeterSnapshot]]:
        result = await self.get_snapshot()
        snapshot = result.value
        items = snapshot.items
        if status is not None:
            items = [meter for meter in items if meter.status is status]
        if zone:
            normalized_zone = normalize_id(zone)
            items = [meter for meter in items if meter.zone_id == normalized_zone]
        if query:
            needle = query.strip().casefold()
            items = [
                meter
                for meter in items
                if needle
                in " ".join(
                    filter(
                        None,
                        [
                            meter.id,
                            meter.serial_number,
                            meter.display_name,
                            meter.zone_id,
                            meter.zone_name,
                        ],
                    )
                ).casefold()
            ]

        total = len(items)
        page = max(1, page)
        page_size = self._clamp_page_size(page_size)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = items[start:end]
        next_page = page + 1 if end < total else None
        response = MeterListResponse(
            items=page_items,
            page=page,
            page_size=page_size,
            total=total,
            next_page=next_page,
            data_quality=snapshot.quality,
            source=SourceInfo(source=snapshot.source, fetched_at=snapshot.fetched_at),
        )
        return response, result

    async def get_meter(self, meter_id: str) -> tuple[MeterDetail, CacheResult[MeterDetail]]:
        canonical = normalize_id(meter_id)
        if not canonical:
            raise PortalNotFoundError("meter id was empty after normalization")

        async def loader() -> MeterDetail:
            raw = await self._gateway.fetch_meter_detail(canonical)
            source = SourceInfo(source="meter_page", fetched_at=datetime.now(UTC))
            detail = self._normalizer.meter_detail(raw, source)
            detail.id = canonical
            detail.portal_id = canonical

            path = await self._hierarchy.path_for(detail.zone_id)
            detail.hierarchy_path = path
            if detail.zone_id and not path:
                detail.quality_flags = _dedupe([*detail.quality_flags, "zone_not_in_hierarchy"])

            try:
                snapshot = await self.get_snapshot()
            except PortalError:
                snapshot = None
            if snapshot is not None:
                listed = next(
                    (meter for meter in snapshot.value.items if meter.id == canonical), None
                )
                if listed is not None:
                    if (
                        listed.serial_number
                        and detail.serial_number
                        and listed.serial_number != detail.serial_number
                    ):
                        detail.quality_flags = _dedupe(
                            [*detail.quality_flags, "list_detail_field_conflict"]
                        )
                    if (
                        listed.status is not MeterStatus.UNKNOWN
                        and detail.status is not MeterStatus.UNKNOWN
                        and listed.status is not detail.status
                    ):
                        detail.quality_flags = _dedupe(
                            [*detail.quality_flags, "list_detail_status_conflict"]
                        )
            return detail

        result = await self._cache.get_or_load(
            f"meter:{canonical}",
            loader,
            fresh_ttl=self._settings.cache_meter_fresh_s,
            stale_ttl=self._settings.cache_meter_stale_s,
        )
        return result.value, result

    async def get_consumption(
        self,
        meter_id: str,
        *,
        from_: datetime | None,
        to: datetime | None,
        granularity: Granularity,
    ) -> tuple[ConsumptionResponse, CacheResult[ConsumptionResponse]]:
        canonical = normalize_id(meter_id)
        if not canonical:
            raise PortalNotFoundError("meter id was empty after normalization")

        now = datetime.now(UTC)
        if to is None and from_ is None:
            to = now
            from_ = now - timedelta(days=7)
        elif to is None:
            assert from_ is not None
            to = now
        elif from_ is None:
            from_ = to - timedelta(days=7)

        assert from_ is not None and to is not None
        from_utc = ensure_utc(from_, self._tz)
        to_utc = ensure_utc(to, self._tz)
        if from_utc >= to_utc:
            raise ValueError("'from' must be before 'to'")
        if to_utc - from_utc > timedelta(days=366):
            raise ValueError("consumption range is limited to 366 days")

        current_period = to_utc >= now - timedelta(days=2)
        if current_period:
            fresh = self._settings.cache_consumption_fresh_s
            stale = self._settings.cache_consumption_stale_s
        else:
            fresh = self._settings.cache_consumption_closed_fresh_s
            stale = self._settings.cache_consumption_closed_stale_s

        key = (
            f"consumption:{canonical}:{from_utc.isoformat()}:{to_utc.isoformat()}:"
            f"{granularity.value}"
        )

        async def loader() -> ConsumptionResponse:
            raw = await self._gateway.fetch_consumption(
                canonical, from_=from_utc, to=to_utc, granularity=granularity
            )
            points, summary, actual_granularity, unit, flags = self._normalizer.consumption(
                raw,
                requested_from=from_utc,
                requested_to=to_utc,
                requested_granularity=granularity,
                source=SourceInfo(source="consumption_json", fetched_at=datetime.now(UTC)),
            )
            return ConsumptionResponse.model_validate(
                {
                    "meter_id": canonical,
                    "from_time": from_utc,
                    "to": to_utc,
                    "granularity": actual_granularity,
                    "unit": unit,
                    "points": points,
                    "summary": summary,
                    "quality_flags": flags,
                    "source": SourceInfo(source="consumption_json", fetched_at=datetime.now(UTC)),
                }
            )

        result = await self._cache.get_or_load(key, loader, fresh_ttl=fresh, stale_ttl=stale)
        return result.value, result

    async def search_nearby(
        self,
        *,
        lat: float,
        lon: float,
        radius_km: float,
        page: int,
        page_size: int,
        status: MeterStatus | None,
    ) -> tuple[GeoSearchResponse, CacheResult[MeterSnapshot]]:
        result = await self.get_snapshot()
        snapshot = result.value
        lat_delta = radius_km / 111.32
        cos_lat = max(0.01, math.cos(math.radians(lat)))
        lon_delta = radius_km / (111.32 * cos_lat)

        matches: list[NearbyMeter] = []
        for meter in snapshot.items:
            if status is not None and meter.status is not status:
                continue
            if meter.latitude is None or meter.longitude is None:
                continue
            if abs(meter.latitude - lat) > lat_delta:
                continue
            if abs(meter.longitude - lon) > lon_delta:
                continue
            distance = _haversine_km(lat, lon, meter.latitude, meter.longitude)
            if distance <= radius_km:
                matches.append(NearbyMeter(**meter.model_dump(), distance_km=round(distance, 3)))

        matches.sort(key=lambda meter: (meter.distance_km, meter.id))
        total = len(matches)
        page = max(1, page)
        page_size = self._clamp_page_size(page_size)
        start = (page - 1) * page_size
        end = start + page_size
        next_page = page + 1 if end < total else None
        response = GeoSearchResponse(
            items=matches[start:end],
            page=page,
            page_size=page_size,
            total=total,
            next_page=next_page,
            center_lat=lat,
            center_lon=lon,
            radius_km=radius_km,
            data_quality=snapshot.quality,
            source=SourceInfo(source=snapshot.source, fetched_at=snapshot.fetched_at),
        )
        return response, result

    async def stats(self) -> tuple[StatsSummary, CacheResult[MeterSnapshot]]:
        result = await self.get_snapshot()
        snapshot = result.value
        statuses = StatusCounts()
        for meter in snapshot.items:
            if meter.status is MeterStatus.ACTIVE:
                statuses.active += 1
            elif meter.status is MeterStatus.INACTIVE:
                statuses.inactive += 1
            elif meter.status is MeterStatus.FAULT:
                statuses.fault += 1
            else:
                statuses.unknown += 1
        return (
            StatsSummary(
                total_meters=len(snapshot.items),
                status_counts=statuses,
                zones=len(snapshot.zone_counts()),
                with_coordinates=sum(
                    1
                    for meter in snapshot.items
                    if meter.latitude is not None and meter.longitude is not None
                ),
                flagged_meters=snapshot.quality.flagged_meters,
                source=SourceInfo(source=snapshot.source, fetched_at=snapshot.fetched_at),
            ),
            result,
        )

    def _clamp_page_size(self, page_size: int) -> int:
        return max(1, min(page_size, self._settings.max_page_size))


def _completeness(meter: Meter) -> int:
    values = [
        meter.serial_number,
        meter.display_name != meter.id,
        meter.status_raw,
        meter.zone_id,
        meter.latitude,
        meter.longitude,
        meter.install_date,
        meter.last_seen_at,
        meter.last_reading,
    ]
    return sum(1 for value in values if value not in (None, False, ""))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
