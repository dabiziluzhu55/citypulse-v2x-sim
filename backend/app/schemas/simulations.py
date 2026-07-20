"""仿真请求与响应 Schema：control_mode 从管控模式注册表校验。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..controllers.registry import CONTROL_MODE_REGISTRY
from .events import EventRequest


class StartSimulationRequest(BaseModel):
    intersection_ids: list[str]
    period: str
    origins: dict[str, list[str]] = Field(default_factory=dict)
    window_start_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(gt=0.0)
    flow_multiplier: float = Field(default=1.0, ge=0.1, le=5.0)
    control_mode: str = "fixed"
    seed: int = Field(default=42, ge=0)
    step_length: float = Field(default=0.05, gt=0.0)
    realtime: bool = True
    gui: bool = False
    snapshot_interval_seconds: float = Field(default=0.2, gt=0.0)
    initial_events: list[EventRequest] = Field(default_factory=list)

    @field_validator("intersection_ids")
    @classmethod
    def validate_intersection_ids(cls, value: list[str]) -> list[str]:
        if value != ["demo_2"]:
            raise ValueError("MVP only supports intersection_ids=['demo_2'].")
        return value

    @field_validator("control_mode")
    @classmethod
    def validate_control_mode(cls, value: str) -> str:
        if value not in CONTROL_MODE_REGISTRY:
            raise ValueError(
                f"control_mode must be one of {sorted(CONTROL_MODE_REGISTRY)}."
            )
        return value


class StartSimulationResponse(BaseModel):
    session_id: str
    state: str
    status_url: str
    websocket_url: str
    metrics_url: str | None = None


class StopSimulationResponse(BaseModel):
    session_id: str
    state: str


class SimulationStatusResponse(BaseModel):
    session_id: str
    state: str
    sequence: int
    elapsed_seconds: float
    duration_seconds: float
    progress: float
    official_time: str
    intersections: dict[str, Any]
    vehicles: list[dict[str, Any]]
    events: list[dict[str, Any]]
    metrics: dict[str, Any]
    evaluation: dict[str, Any] | None = None
    error: str | None = None


class MetricsResponse(BaseModel):
    """统一评估指标响应；算法字段仅标识 control_mode，不拆多套接口。"""

    episode_id: str
    algorithm: str
    avg_waiting_time: float = 0.0
    avg_travel_time: float = 0.0
    avg_queue_length: float = 0.0
    throughput: float = 0.0
    fuel_consumption: float = 0.0
    avg_decision_latency_ms: float = 0.0
    departed: int = 0
    arrived: int = 0
    finished: bool = False
