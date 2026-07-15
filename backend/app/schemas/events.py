"""扰动事件请求Schema"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class LaneClosureRequest(BaseModel):
    event_type: Literal["lane_closure"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: list[str]


class SpeedLimitRequest(BaseModel):
    event_type: Literal["speed_limit"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: list[str]
    max_speed: float = Field(gt=0)


class AccidentRequest(BaseModel):
    event_type: Literal["accident"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float = Field(ge=0.0, le=1.0)


EventRequest = Annotated[
    LaneClosureRequest | SpeedLimitRequest | AccidentRequest,
    Field(discriminator="event_type"),
]


class EventCreatedResponse(BaseModel):
    event_id: str
