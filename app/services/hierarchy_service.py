"""Build a defensible tree from a flat, defective legacy hierarchy export."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.models.common import SourceInfo
from app.models.hierarchy import (
    HierarchyDataQuality,
    HierarchyFlatNode,
    HierarchyNode,
    HierarchyResponse,
)
from app.portal.exceptions import PortalError
from app.portal.gateway import PortalGateway
from app.portal.normalize import PortalNormalizer
from app.services.cache import CacheResult, TTLCache

logger = logging.getLogger(__name__)

NETWORK_ID = "__network__"
UNASSIGNED_ID = "__unassigned__"


@dataclass(slots=True)
class HierarchySnapshot:
    rows: list[dict[str, Any]]
    fetched_at: datetime
    duplicates: int
    multiple_parents: int
    source: str = "hierarchy_page"


@dataclass(slots=True)
class PreparedHierarchy:
    snapshot: HierarchySnapshot
    nodes: dict[str, dict[str, Any]]
    effective_parent: dict[str, str | None]
    orphans: int
    cycles: int
    duplicates: int
    multiple_parents: int
    total_meters: int
    meter_counts: dict[str, int] = field(default_factory=dict)


class HierarchyService:
    def __init__(self, gateway: PortalGateway, cache: TTLCache, settings: Settings) -> None:
        self._gateway = gateway
        self._cache = cache
        self._settings = settings
        self._normalizer = PortalNormalizer(settings.urja_timezone)

    async def get_hierarchy(
        self,
        *,
        root: str | None,
        depth: int | None,
        meter_counts: dict[str, int],
    ) -> tuple[HierarchyResponse, CacheResult[HierarchySnapshot]]:
        result = await self._cache.get_or_load(
            "hierarchy:rows",
            self._load_snapshot,
            fresh_ttl=self._settings.cache_hierarchy_fresh_s,
            stale_ttl=self._settings.cache_hierarchy_stale_s,
        )
        prepared = self._prepare(result.value, meter_counts)
        root_id = (root or "").strip().upper() or NETWORK_ID
        if root_id not in prepared.nodes and root_id != NETWORK_ID:
            raise KeyError(root_id)
        response = self._build_response(prepared, root_id, depth)
        return response, result

    async def path_for(self, zone_id: str | None) -> list[str]:
        if not zone_id:
            return []
        try:
            result = await self._cache.get_or_load(
                "hierarchy:rows",
                self._load_snapshot,
                fresh_ttl=self._settings.cache_hierarchy_fresh_s,
                stale_ttl=self._settings.cache_hierarchy_stale_s,
            )
        except PortalError:
            return []
        prepared = self._prepare(result.value, {})
        if zone_id not in prepared.nodes:
            return []
        names: list[str] = []
        current: str | None = zone_id
        seen: set[str] = set()
        while current and current in prepared.nodes and current not in seen:
            seen.add(current)
            names.append(prepared.nodes[current]["name"])
            current = prepared.effective_parent.get(current)
        return list(reversed(names))

    async def _load_snapshot(self) -> HierarchySnapshot:
        raw_rows = await self._gateway.fetch_hierarchy()
        rows = self._normalizer.hierarchy_rows(raw_rows)

        kept: dict[str, dict[str, Any]] = {}
        duplicates = 0
        multiple_parents = 0
        for row in rows:
            node_id = row["id"]
            existing = kept.get(node_id)
            if existing is None:
                kept[node_id] = row
                continue
            duplicates += 1
            existing["quality_flags"] = _add(existing["quality_flags"], "duplicate_in_source")
            if row["parent_id"] != existing["parent_id"]:
                multiple_parents += 1
                existing["quality_flags"] = _add(
                    existing["quality_flags"], "multiple_parents_in_source"
                )

        return HierarchySnapshot(
            rows=list(kept.values()),
            fetched_at=datetime.now(UTC),
            duplicates=duplicates,
            multiple_parents=multiple_parents,
        )

    def _prepare(
        self, snapshot: HierarchySnapshot, meter_counts: dict[str, int]
    ) -> PreparedHierarchy:
        nodes: dict[str, dict[str, Any]] = {row["id"]: dict(row) for row in snapshot.rows}
        effective: dict[str, str | None] = {}
        orphans = 0
        cycles = 0

        for node_id, node in nodes.items():
            parent = node["parent_id"]
            if parent is None:
                effective[node_id] = None
            elif parent == node_id:
                effective[node_id] = UNASSIGNED_ID
                node["quality_flags"] = _add(node["quality_flags"], "cycle_broken")
                cycles += 1
            elif parent not in nodes:
                effective[node_id] = UNASSIGNED_ID
                node["quality_flags"] = _add(node["quality_flags"], "orphan")
                orphans += 1
            else:
                effective[node_id] = parent

        state: dict[str, int] = {}
        for start in sorted(nodes):
            if state.get(start, 0) != 0:
                continue
            path: list[str] = []
            position: dict[str, int] = {}
            current: str | None = start
            cycle_nodes: list[str] | None = None
            while current is not None and current in nodes:
                if state.get(current, 0) == 2:
                    break
                if current in position:
                    cycle_nodes = path[position[current] :]
                    break
                state[current] = 1
                position[current] = len(path)
                path.append(current)
                current = effective.get(current)

            if cycle_nodes:
                cycles += 1
                for node_id in cycle_nodes:
                    nodes[node_id]["quality_flags"] = _add(
                        nodes[node_id]["quality_flags"], "cycle_broken"
                    )
                break_id = min(cycle_nodes)
                effective[break_id] = UNASSIGNED_ID
            for node_id in path:
                state[node_id] = 2

        counts: dict[str, int] = {}
        unknown_zone_count = 0
        for zone_id, count in meter_counts.items():
            if zone_id not in nodes:
                unknown_zone_count += count
                continue
            walk: str | None = zone_id
            guard: set[str] = set()
            while walk and walk in nodes and walk not in guard:
                guard.add(walk)
                counts[walk] = counts.get(walk, 0) + count
                walk = effective.get(walk)
        if unknown_zone_count:
            counts[UNASSIGNED_ID] = counts.get(UNASSIGNED_ID, 0) + unknown_zone_count

        return PreparedHierarchy(
            snapshot=snapshot,
            nodes=nodes,
            effective_parent=effective,
            orphans=orphans,
            cycles=cycles,
            duplicates=snapshot.duplicates,
            multiple_parents=snapshot.multiple_parents,
            total_meters=sum(meter_counts.values()),
            meter_counts=counts,
        )

    def _build_response(
        self, prepared: PreparedHierarchy, root_id: str, depth: int | None
    ) -> HierarchyResponse:
        nodes = prepared.nodes
        synthetic_root: dict[str, Any] = {
            "id": NETWORK_ID,
            "name": "Urja Network",
            "type": "network",
            "parent_id": None,
            "quality_flags": [],
        }
        unassigned_needed = (
            prepared.orphans > 0
            or prepared.cycles > 0
            or prepared.meter_counts.get(UNASSIGNED_ID, 0) > 0
        )
        synthetic_unassigned = {
            "id": UNASSIGNED_ID,
            "name": "Unassigned",
            "type": "unassigned",
            "parent_id": NETWORK_ID,
            "quality_flags": ["synthetic"],
        }
        all_nodes: dict[str, dict[str, Any]] = dict(nodes)
        all_nodes[NETWORK_ID] = synthetic_root
        if unassigned_needed:
            all_nodes[UNASSIGNED_ID] = synthetic_unassigned

        children: dict[str, list[str]] = {}
        for node_id in nodes:
            parent = prepared.effective_parent.get(node_id)
            if parent == UNASSIGNED_ID and not unassigned_needed:
                parent = NETWORK_ID
            if parent is None:
                parent = NETWORK_ID
            children.setdefault(parent, []).append(node_id)
        for parent in list(children):
            children[parent].sort()
        if unassigned_needed:
            children.setdefault(NETWORK_ID, []).append(UNASSIGNED_ID)
            children[NETWORK_ID].sort()

        counts = dict(prepared.meter_counts)
        counts[NETWORK_ID] = prepared.total_meters
        if unassigned_needed:
            counts[UNASSIGNED_ID] = counts.get(UNASSIGNED_ID, 0) + sum(
                counts.get(child, 0) for child in children.get(UNASSIGNED_ID, [])
            )

        flat: list[HierarchyFlatNode] = []
        root_model = self._build_node(
            root_id,
            all_nodes,
            children,
            counts,
            remaining=depth,
            flat=flat,
            flat_depth=0,
        )
        source = SourceInfo(
            source=prepared.snapshot.source,
            fetched_at=prepared.snapshot.fetched_at,
        )
        return HierarchyResponse(
            root=root_model,
            nodes=flat,
            depth=depth,
            data_quality=HierarchyDataQuality(
                orphans=prepared.orphans,
                cycles=prepared.cycles,
                duplicates=prepared.duplicates,
                multiple_parents=prepared.multiple_parents,
            ),
            source=source,
        )

    def _build_node(
        self,
        node_id: str,
        all_nodes: dict[str, dict[str, Any]],
        children: dict[str, list[str]],
        meter_counts: dict[str, int],
        *,
        remaining: int | None,
        flat: list[HierarchyFlatNode],
        flat_depth: int,
    ) -> HierarchyNode:
        node = all_nodes[node_id]
        child_ids = children.get(node_id, [])
        include_children = remaining is None or remaining > 0
        has_more = bool(child_ids) and not include_children

        flat.append(
            HierarchyFlatNode(
                id=node_id,
                name=node["name"],
                type=node["type"],
                parent_id=node["parent_id"],
                depth=flat_depth,
                meter_count=meter_counts.get(node_id, 0),
                quality_flags=list(node.get("quality_flags", [])),
            )
        )

        child_models: list[HierarchyNode] = []
        if include_children:
            next_remaining = None if remaining is None else remaining - 1
            for child_id in child_ids:
                child_models.append(
                    self._build_node(
                        child_id,
                        all_nodes,
                        children,
                        meter_counts,
                        remaining=next_remaining,
                        flat=flat,
                        flat_depth=flat_depth + 1,
                    )
                )

        return HierarchyNode(
            id=node_id,
            name=node["name"],
            type=node["type"],
            parent_id=node["parent_id"],
            meter_count=meter_counts.get(node_id, 0),
            quality_flags=list(node.get("quality_flags", [])),
            children=child_models,
            has_more_children=has_more,
        )


def _add(values: list[str], value: str) -> list[str]:
    return values if value in values else [*values, value]
