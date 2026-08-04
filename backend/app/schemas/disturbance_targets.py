"""扰动目标请求 Schema：按路口描述，后端解析为 lane 级事件。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .events import DEFAULT_EVENT_VEHICLE_TYPE_ID


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


class DisturbanceTargetMajorEventOpening(BaseModel):
    event_type: Literal["major_event_opening"]
    intersection_id: str
    start_seconds: float
    end_seconds: float
    vehicle_count: int = Field(gt=0)
    event_id: str | None = None
    venue_lane_id: str | None = None
    source_lane_ids: list[str] | None = None
    vehicle_type_id: str = DEFAULT_EVENT_VEHICLE_TYPE_ID

    @field_validator("vehicle_type_id")
    @classmethod
    def _non_empty_vehicle_type(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("vehicle_type_id cannot be empty.")
        return text

    @model_validator(mode="after")
    def _validate_time_window(self) -> DisturbanceTargetMajorEventOpening:
        if self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be < end_seconds.")
        return self


class DisturbanceTargetMajorEventClosing(BaseModel):
    event_type: Literal["major_event_closing"]
    intersection_id: str
    start_seconds: float
    end_seconds: float
    vehicle_count: int = Field(gt=0)
    event_id: str | None = None
    venue_lane_id: str | None = None
    destination_lane_ids: list[str] | None = None
    vehicle_type_id: str = DEFAULT_EVENT_VEHICLE_TYPE_ID

    @field_validator("vehicle_type_id")
    @classmethod
    def _non_empty_vehicle_type(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("vehicle_type_id cannot be empty.")
        return text

    @model_validator(mode="after")
    def _validate_time_window(self) -> DisturbanceTargetMajorEventClosing:
        if self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be < end_seconds.")
        return self


DisturbanceTarget = Annotated[
    DisturbanceTargetLaneClosure
    | DisturbanceTargetSpeedLimit
    | DisturbanceTargetAccident
    | DisturbanceTargetMajorEventOpening
    | DisturbanceTargetMajorEventClosing,
    Field(discriminator="event_type"),
]
