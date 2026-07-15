"""仿真目录响应Schema"""

from __future__ import annotations

from pydantic import BaseModel


class OriginSchema(BaseModel):
    origin_id: str
    label: str
    lane_ids: list[str]


class LaneSchema(BaseModel):
    lane_id: str
    edge_id: str
    lane_index: int
    role: str
    approach: str | None
    approach_label: str | None
    length: float
    max_speed: float


class IntersectionSchema(BaseModel):
    intersection_id: str
    longitude: float | None
    latitude: float | None
    periods: list[str]
    origins: list[OriginSchema]
    lanes: list[LaneSchema]


class FlowMultiplierRangeSchema(BaseModel):
    min: float
    max: float


class CatalogResponse(BaseModel):
    intersections: list[IntersectionSchema]
    event_types: list[str]
    control_modes: list[str]
    flow_multiplier: FlowMultiplierRangeSchema
