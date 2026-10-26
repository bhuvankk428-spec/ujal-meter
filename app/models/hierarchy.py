from pydantic import BaseModel, Field

from app.models.common import SourceInfo


class HierarchyDataQuality(BaseModel):
    orphans: int = 0
    cycles: int = 0
    duplicates: int = 0
    multiple_parents: int = 0


class HierarchyFlatNode(BaseModel):
    id: str
    name: str
    type: str
    parent_id: str | None
    depth: int
    meter_count: int = 0
    quality_flags: list[str] = Field(default_factory=list)


class HierarchyNode(BaseModel):
    id: str
    name: str
    type: str
    parent_id: str | None
    meter_count: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    children: list["HierarchyNode"] = Field(default_factory=list)
    has_more_children: bool = False


class HierarchyResponse(BaseModel):
    root: HierarchyNode
    nodes: list[HierarchyFlatNode]
    depth: int | None
    data_quality: HierarchyDataQuality
    source: SourceInfo


HierarchyNode.model_rebuild()
