"""Simulation DTOs shared by Backend and SUMO worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .ai_control import AIControlConfig, AIControlStatus
from .events import DisturbanceEvent, EventSnapshot
from .playback import PLAYBACK_SPEEDS


@dataclass(frozen=True)
class SimulationConfig:
    intersection_ids: tuple[str, ...]
    period: str = "morning_peak"
    scenario_preset_id: str = ""
    scenario_scope: str = "global"
    origins: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    window_start_seconds: float = 0.0
    duration_seconds: float | None = None
    flow_multiplier: float = 1.0
    control_mode: str = "fixed"
    algorithm_transport: str = "local"
    algorithm_module: str = ""
    decision_interval: float = 5.0
    minimum_green: float = 5.0
    seed: int = 42
    step_length: float = 0.2
    gui: bool = False
    realtime: bool = False
    playback_speed: float | None = None
    start_paused: bool = False
    snapshot_interval_seconds: float = 1
    ai_observer_module: str = ""
    ai_frame_interval_seconds: float = 1
    ai_observer_shutdown_timeout: float = 5.0
    initial_events: tuple[DisturbanceEvent, ...] = ()
    baseline_controller: str = ""
    ai_control: AIControlConfig = field(default_factory=AIControlConfig)
    algorithm_decision_observer: Callable[[Mapping[str, Any]], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class OriginCapability:
    origin_id: str
    label: str
    lane_ids: tuple[str, ...]


@dataclass(frozen=True)
class LaneCapability:
    lane_id: str
    edge_id: str
    lane_index: int
    role: str
    approach: str | None
    approach_label: str | None
    length: float
    max_speed: float


@dataclass(frozen=True)
class IntersectionCapability:
    intersection_id: str
    longitude: float | None
    latitude: float | None
    periods: tuple[str, ...]
    origins: tuple[OriginCapability, ...]
    lanes: tuple[LaneCapability, ...]


@dataclass(frozen=True)
class ScenarioScopeCapability:
    scope_id: str
    label: str
    periods: tuple[str, ...]
    intersection_ids: tuple[str, ...]


@dataclass(frozen=True)
class SimulationCatalog:
    intersections: Mapping[str, IntersectionCapability]
    scenario_scopes: Mapping[str, ScenarioScopeCapability] = field(default_factory=dict)
    event_types: tuple[str, ...] = (
        "lane_closure",
        "speed_limit",
        "accident",
        "major_event_opening",
        "major_event_closing",
    )
    flow_multiplier_min: float = 0.1
    flow_multiplier_max: float = 5.0
    playback_speeds: tuple[float, ...] = PLAYBACK_SPEEDS


@dataclass(frozen=True)
class LaneRuntimeSnapshot:
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float
    edge_id: str = ""
    lane_index: int = 0
    role: str = ""
    approach_id: str | None = None
    downstream_lane_ids: tuple[str, ...] = ()
    lane_has_green: bool | None = None
    signal_state: str | None = None
    current_allowed_speed_mps: float | None = None
    queue_length_m: float | None = None
    queue_length_is_estimate: bool = True
    lane_length_m: float | None = None


@dataclass(frozen=True)
class IntersectionRuntimeSnapshot:
    current_phase: int
    pending_phase: int | None
    stage: str
    stage_elapsed: float
    lanes: Mapping[str, LaneRuntimeSnapshot]


@dataclass(frozen=True)
class VehicleRuntimeSnapshot:
    vehicle_id: str
    x: float
    y: float
    speed: float
    angle: float
    road_id: str
    lane_id: str
    controllable: bool = False
    type_id: str = ""
    acceleration: float = 0.0
    lane_index: int = -1
    lane_position: float = 0.0
    allowed_speed: float = 0.0
    route_id: str = ""
    route_index: int = -1
    waiting_time: float = 0.0
    time_loss: float = 0.0
    distance: float = 0.0
    fuel_rate_mg_s: float = 0.0
    fuel_total_mg: float = 0.0
    fuel_total_ml: float = 0.0
    hard_braking_events: int = 0
    next_intersection_id: str | None = None
    target_speed: float | None = None
    target_lane_index: int | None = None


@dataclass(frozen=True)
class SessionMetrics:
    active_vehicles: int = 0
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    remaining_vehicles: int = 0
    halting_vehicles: int = 0
    total_waiting_time: float = 0.0
    mean_speed: float = 0.0
    fuel_consumed_mg: float = 0.0
    fuel_consumed_ml: float = 0.0
    hard_braking_events: int = 0


@dataclass(frozen=True)
class SimulationSnapshot:
    session_id: str
    state: str
    sequence: int
    elapsed_seconds: float
    duration_seconds: float
    progress: float
    official_time: str
    playback_speed: float | None = None
    intersections: Mapping[str, IntersectionRuntimeSnapshot] = field(default_factory=dict)
    vehicles: tuple[VehicleRuntimeSnapshot, ...] = ()
    events: tuple[EventSnapshot, ...] = ()
    v2x_events: tuple[Mapping[str, object], ...] = ()
    metrics: SessionMetrics = field(default_factory=SessionMetrics)
    error: str | None = None
    ai_takeover: AIControlStatus = field(default_factory=AIControlStatus)
    evaluation_scope: Mapping[str, object] | None = None
