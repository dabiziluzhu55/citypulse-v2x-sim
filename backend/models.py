"""OpenAPI request and response models shared by the FastAPI routes."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class RunLifecycleStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    IDLE = "idle"
    ERROR = "error"


class ControlCommand(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    RESET = "reset"
    STEP = "step"


class MapMeta(ApiModel):
    template_id: str
    map_center: tuple[float, float]
    map_bounds: tuple[float, float, float, float]
    default_zoom: int = Field(ge=1, le=22)


class ScenarioTemplate(MapMeta):
    name: str
    intersection_count: int = Field(ge=0)
    description: str


class ScenarioTemplatesResponse(BaseModel):
    templates: list[ScenarioTemplate]


class CreateScenarioRequest(ApiModel):
    name: str = Field(min_length=1, examples=["雄安早高峰场景"])
    template_id: str = Field(min_length=1, examples=["xiongan20"])
    network_source: Literal["osm_import", "prebuilt_sumo", "manual_netedit"]
    traffic_flow: dict[str, Any]
    od_groups: list[dict[str, Any]] = Field(default_factory=list)
    traffic_light: dict[str, Any]
    disturbances: list[dict[str, Any]] = Field(default_factory=list)


class ScenarioFiles(BaseModel):
    net: str
    route: str
    config: str


class CreateScenarioResponse(BaseModel):
    scenario_id: str
    status: str
    files: ScenarioFiles


class StartRunRequest(BaseModel):
    scenario_id: str = Field(min_length=1)
    algorithm: str = Field(default="fixed_time", min_length=1)
    cloud_edge_enabled: bool = True
    realtime: bool = True
    step_length: float = Field(default=1.0, gt=0, le=60)


class StartRunResponse(BaseModel):
    run_id: str
    status: RunLifecycleStatus
    message: str


class ControlRequest(BaseModel):
    command: ControlCommand


class ControlResponse(BaseModel):
    run_id: str
    status: RunLifecycleStatus


class AlgorithmRequest(BaseModel):
    algorithm_id: str = Field(min_length=1, examples=["ippo"])
    parameters: dict[str, Any] = Field(default_factory=dict)


class AlgorithmItem(BaseModel):
    algorithm_id: str
    name: str
    type: Literal["baseline", "rule_based", "reinforcement_learning"]
    description: str


class AlgorithmsResponse(BaseModel):
    algorithms: list[AlgorithmItem]


class AlgorithmSwitchResponse(BaseModel):
    run_id: str
    algorithm: str
    status: Literal["applied"]


class RunStatusResponse(BaseModel):
    run_id: str
    status: RunLifecycleStatus
    sim_time: int
    step: int
    vehicle_count: int
    message: str


class RunOverviewResponse(BaseModel):
    run_id: str
    scenario_id: str
    scenario_name: str
    status: RunLifecycleStatus
    sim_time: int
    vehicle_count: int
    active_vehicle_count: int
    algorithm: str
    cloud_edge_enabled: bool
    avg_speed: float
    avg_waiting_time: float
    avg_queue_length: float
    congested_intersections: int


class TrafficIntersection(ApiModel):
    intersection_id: str
    name: str
    x: float
    y: float
    current_phase: int
    phase_name: str
    phase_duration: float
    queue_length: float
    avg_waiting_time: float
    avg_speed: float
    status: str


class TrafficLane(ApiModel):
    lane_id: str
    edge_id: str
    vehicle_count: int
    queue_length: float
    avg_speed: float
    occupancy: float
    status: str


class TrafficVehicle(ApiModel):
    vehicle_id: str
    x: float
    y: float
    speed: float
    waiting_time: float
    lane_id: str
    type: str
    angle: float | None = None


class TrafficStateResponse(BaseModel):
    run_id: str
    sim_time: int
    intersections: list[TrafficIntersection]
    lanes: list[TrafficLane]
    vehicles: list[TrafficVehicle]


class CollaborationStateResponse(ApiModel):
    run_id: str
    sim_time: int
    cloud: dict[str, Any]
    edges: list[dict[str, Any]]
    vehicles: list[dict[str, Any]]


class EventsResponse(BaseModel):
    events: list[dict[str, Any]]


class PredictionResponse(ApiModel):
    target: str
    horizon: int
    predictions: list[dict[str, Any]]
    model: str
    updated_at: int


class RealtimeMetricsResponse(ApiModel):
    run_id: str
    time: int
    metrics: dict[str, float | int]


class MetricsTimeseriesResponse(ApiModel):
    run_id: str
    series: list[dict[str, float | int]]


class ExperimentComparisonResponse(ApiModel):
    experiment_id: str
    scenario_id: str
    baselines: list[str]
    results: list[dict[str, str | float | int]]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    api_base_path: str
    websocket_path: str
    tiles_available: bool
    tiles_url: str | None


class ErrorResponse(BaseModel):
    detail: str
