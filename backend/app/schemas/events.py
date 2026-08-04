"""扰动事件请求 Schema。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_EVENT_VEHICLE_TYPE_ID = "citypulse_event_passenger"


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


class MajorEventOpeningRequest(BaseModel):
    event_type: Literal["major_event_opening"]
    event_id: str
    start_seconds: float
    end_seconds: float
    venue_lane_id: str
    vehicle_count: int = Field(gt=0)
    source_lane_ids: list[str] = Field(default_factory=list)
    vehicle_type_id: str = DEFAULT_EVENT_VEHICLE_TYPE_ID

    @field_validator("vehicle_type_id")
    @classmethod
    def _non_empty_vehicle_type(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("vehicle_type_id cannot be empty.")
        return text

    @field_validator("venue_lane_id")
    @classmethod
    def _non_empty_venue(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("venue_lane_id cannot be empty.")
        return text

    @model_validator(mode="after")
    def _validate_time_window(self) -> MajorEventOpeningRequest:
        if self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be < end_seconds.")
        return self


class MajorEventClosingRequest(BaseModel):
    event_type: Literal["major_event_closing"]
    event_id: str
    start_seconds: float
    end_seconds: float
    venue_lane_id: str
    vehicle_count: int = Field(gt=0)
    destination_lane_ids: list[str] = Field(default_factory=list)
    vehicle_type_id: str = DEFAULT_EVENT_VEHICLE_TYPE_ID

    @field_validator("vehicle_type_id")
    @classmethod
    def _non_empty_vehicle_type(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("vehicle_type_id cannot be empty.")
        return text

    @field_validator("venue_lane_id")
    @classmethod
    def _non_empty_venue(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("venue_lane_id cannot be empty.")
        return text

    @model_validator(mode="after")
    def _validate_time_window(self) -> MajorEventClosingRequest:
        if self.start_seconds >= self.end_seconds:
            raise ValueError("start_seconds must be < end_seconds.")
        return self


EventRequest = Annotated[
    LaneClosureRequest
    | SpeedLimitRequest
    | AccidentRequest
    | MajorEventOpeningRequest
    | MajorEventClosingRequest,
    Field(discriminator="event_type"),
]


class EventCreatedResponse(BaseModel):
    event_id: str
