from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings
from app.services.cache import TTLCache
from app.services.hierarchy_service import (
    NETWORK_ID,
    UNASSIGNED_ID,
    HierarchyService,
)
from mock_portal.data import hierarchy_rows


class FakeGateway:
    async def fetch_hierarchy(self) -> list[dict[str, str]]:
        return hierarchy_rows()


@pytest.fixture
def service(settings: Settings) -> HierarchyService:
    gateway: Any = FakeGateway()
    return HierarchyService(gateway, TTLCache(jitter_ratio=0), settings)


async def test_defects_are_reported_not_hidden(service: HierarchyService) -> None:
    response, _ = await service.get_hierarchy(
        root=None,
        depth=None,
        meter_counts={
            "ZN-N1": 2,
            "ZN-ORPHAN": 1,
            "ZN-CYC-A": 1,
            "ZN-404": 3,
        },
    )
    assert response.root.id == NETWORK_ID
    assert response.root.meter_count == 7
    quality = response.data_quality
    assert quality.orphans == 1
    assert quality.cycles == 1
    assert quality.duplicates == 1
    assert quality.multiple_parents == 1

    flat_ids = {node.id for node in response.nodes}
    assert {"ZN-CYC-A", "ZN-CYC-B", "ZN-ORPHAN", UNASSIGNED_ID}.issubset(flat_ids)
    assert response.nodes[0].id == NETWORK_ID

    orphan = next(node for node in response.nodes if node.id == "ZN-ORPHAN")
    assert "orphan" in orphan.quality_flags
    cycle_node = next(node for node in response.nodes if node.id == "ZN-CYC-A")
    assert "cycle_broken" in cycle_node.quality_flags


async def test_depth_limit(service: HierarchyService) -> None:
    response, _ = await service.get_hierarchy(root=None, depth=0, meter_counts={})
    assert response.root.children == []
    assert response.root.has_more_children is True
    assert len(response.nodes) == 1


async def test_root_subtree_and_path(service: HierarchyService) -> None:
    response, _ = await service.get_hierarchy(root="zn-n1", depth=2, meter_counts={"ZN-N1": 4})
    assert response.root.id == "ZN-N1"
    assert response.root.meter_count == 4
    assert await service.path_for("ZN-S1") == [
        "Bengaluru Network",
        "South",
        "Jayanagar",
    ]


async def test_unknown_root_raises(service: HierarchyService) -> None:
    with pytest.raises(KeyError):
        await service.get_hierarchy(root="ZN-MISSING", depth=None, meter_counts={})
