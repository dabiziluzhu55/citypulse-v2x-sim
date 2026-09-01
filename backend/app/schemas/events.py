"""扰动事件请求Schema"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_EVENT_VEHICLE_TYPE_ID = "citypulse_event_passenger"
DEFAULT_SPEED_LIMIT_MPS = 5.0
_KMH_TO_MPS = 1.0 / 3.6
_SPEED_LIMIT_UNIT_TOLERANCE_MPS = 0.05


def resolve_speed_limit_mps(
    max_speed: float | None,
    speed_kmh: float | None,
    *,
    default_mps: float | None = None,
) -> float:
    """将前端提交的限速统一为 SUMO 使用的 m/s。

    ``max_speed`` 单位为 m/s，``speed_kmh`` 为单位 km/h 的最大限速速度。
    至少提供一个；同时提供时必须表示同一限速。
    """
    if max_speed is not None and speed_kmh is not None:
        converted = float(speed_kmh) * _KMH_TO_MPS
        if abs(converted - float(max_speed)) > _SPEED_LIMIT_UNIT_TOLERANCE_MPS:
            raise ValueError(
                "max_speed (m/s) and speed_kmh (km/h) must describe the same limit."
            )
        return float(max_speed)
    if max_speed is not None:
        return float(max_speed)
    if speed_kmh is not None:
        return float(speed_kmh) * _KMH_TO_MPS
    if default_mps is not None:
        return float(default_mps)
    raise ValueError("Either max_speed (m/s) or speed_kmh (km/h) is required.")


class LaneClosureRequest(BaseModel):
    event_type: Literal["lane_closure"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: list[str]
    ai_control_enabled: bool = False


class SpeedLimitRequest(BaseModel):
    event_type: Literal["speed_limit"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_ids: list[str]
    max_speed: float | None = Field(
        default=None,
        gt=0,
        description="临时限速，单位 m/s。与 speed_kmh 至少提供一个；交给 SUMO 时使用此值。",
    )
    speed_kmh: float | None = Field(
        default=None,
        gt=0,
        description="最大限速速度，单位 km/h。前端用户输入建议用此字段，后端会换算为 max_speed。",
    )
    ai_control_enabled: bool = False

    @model_validator(mode="after")
    def _resolve_speed_limit(self) -> SpeedLimitRequest:
        self.max_speed = resolve_speed_limit_mps(
            self.max_speed,
            self.speed_kmh,
            default_mps=None,
        )
        return self


class AccidentRequest(BaseModel):
    event_type: Literal["accident"]
    event_id: str
    start_seconds: float
    end_seconds: float
    lane_id: str
    position_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    ai_control_enabled: bool = False


class MajorEventOpeningRequest(BaseModel):
    event_type: Literal["major_event_opening"]
    event_id: str
    start_seconds: float
    end_seconds: float
    venue_lane_id: str
    vehicle_count: int = Field(gt=0)
    source_lane_ids: list[str] = Field(default_factory=list)
    vehicle_type_id: str = DEFAULT_EVENT_VEHICLE_TYPE_ID
    ai_control_enabled: bool = False

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
    ai_control_enabled: bool = False

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
