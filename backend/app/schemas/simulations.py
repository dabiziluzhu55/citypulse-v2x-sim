"""仿真请求与响应 Schema：control_mode 从管控模式注册表校验。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..controllers.registry import CONTROL_MODE_REGISTRY
from ..core.playback import ALLOWED_PLAYBACK_SPEEDS, validate_playback_speed
from ..scenario.presets import SCENARIO_PRESET_REGISTRY
from .disturbance_targets import DisturbanceTarget


class StartSimulationRequest(BaseModel):
    scenario_preset_id: str
    period: str
    origins: dict[str, list[str]] = Field(default_factory=dict)
    window_start_seconds: float = Field(default=0.0, ge=0.0)
    duration_seconds: float = Field(gt=0.0)
    control_mode: str = "fixed"
    seed: int = Field(default=42, ge=0)
    step_length: float = Field(default=0.05, gt=0.0)
    realtime: bool = True
    gui: bool = False
    snapshot_interval_seconds: float = Field(default=0.2, gt=0.0)
    disturbance_targets: list[DisturbanceTarget] = Field(default_factory=list)
    playback_speed: float | None = Field(
        default=None,
        description="播放倍速；None 时 realtime=true 由内核默认 1×，realtime=false 不限速。",
    )

    @field_validator("scenario_preset_id")
    @classmethod
    def validate_scenario_preset_id(cls, value: str) -> str:
        if value not in SCENARIO_PRESET_REGISTRY:
            raise ValueError(
                f"scenario_preset_id must be one of {sorted(SCENARIO_PRESET_REGISTRY)}."
            )
        return value

    @field_validator("playback_speed")
    @classmethod
    def validate_playback_speed_field(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return validate_playback_speed(value)

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
    scenario_preset_id: str | None = None


class StopSimulationResponse(BaseModel):
    session_id: str
    state: str


class SimulationPlaybackResponse(BaseModel):
    """暂停/恢复等播放控制操作的统一响应。"""

    session_id: str
    state: str
    playback_speed: float | None = None


class SetPlaybackSpeedRequest(BaseModel):
    playback_speed: float

    @field_validator("playback_speed")
    @classmethod
    def validate_playback_speed_field(cls, value: float) -> float:
        return validate_playback_speed(value)


class SimulationStatusResponse(BaseModel):
    session_id: str
    state: str
    sequence: int
    elapsed_seconds: float
    duration_seconds: float
    progress: float
    official_time: str
    playback_speed: float | None = None
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
