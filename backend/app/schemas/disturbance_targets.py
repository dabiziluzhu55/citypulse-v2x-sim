"""扰动目标请求 Schema：按路口描述，后端解析为 lane 级事件。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DisturbanceTargetLaneClosure(BaseModel):
    event_type: Literal["lane_closure"]
    intersection_id: str
    start_seconds: float
    end_seconds: float
    event_id: str | None = None
    lane_ids: list[str] | None = None


class DisturbanceTargetSpeedLimit(BaseModel):
    event_type: Literal["speed_limit"]
    intersection_id: str
    start_seconds: float
    end_seconds: float
    max_speed: float = Field(default=5.0, gt=0)
    event_id: str | None = None
    lane_ids: list[str] | None = None


class DisturbanceTargetAccident(BaseModel):
    event_type: Literal["accident"]
    intersection_id: str
    start_seconds: float
    end_seconds: float
    position_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    event_id: str | None = None
    lane_id: str | None = None


DisturbanceTarget = Annotated[
    DisturbanceTargetLaneClosure
    | DisturbanceTargetSpeedLimit
    | DisturbanceTargetAccident,
    Field(discriminator="event_type"),
]
