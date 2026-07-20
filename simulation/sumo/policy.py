"""Stable data contract between SUMO and external signal-control algorithms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


PROTOCOL_VERSION = "2.0"


@dataclass(frozen=True)
class LaneMetadata:
    lane_id: str
    edge_id: str
    lane_index: int
    role: str
    length: float
    max_speed: float
    intersection_id: str = ""
    approach_id: str | None = None
    movements: Tuple[str, ...] = ()
    length_m: float = 0.0
    speed_limit_mps: float = 0.0
    downstream_lane_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RoadConnectionMetadata:
    connection_id: str
    approach: str
    movement: str
    from_lane: str
    to_lane: str
    direction: str
    tls_id: str = ""
    link_index: int = -1


@dataclass(frozen=True)
class PhaseMetadata:
    phase_id: int
    name: str
    movement: str
    approaches: Tuple[str, ...]
    green_seconds: float
    yellow_seconds: float
    clearance_seconds: float
    connection_priorities: Mapping[str, str]


@dataclass(frozen=True)
class IntersectionMetadata:
    intersection_id: str
    phase_order: Tuple[int, ...]
    phases: Mapping[int, PhaseMetadata]
    lanes: Mapping[str, LaneMetadata]
    incoming_lanes: Tuple[str, ...]
    outgoing_lanes: Tuple[str, ...]
    connections: Tuple[RoadConnectionMetadata, ...]
    direct_neighbors: Tuple[str, ...]


@dataclass(frozen=True)
class SimulationMetadata:
    protocol_version: str
    episode_id: str
    period: str
    seed: int
    decision_interval: float
    minimum_green: float
    intersections: Mapping[str, IntersectionMetadata]
    vehicle_types: Mapping[str, "VehicleTypeMetadata"] = field(default_factory=dict)
    vehicle_control: "VehicleControlMetadata | None" = None


@dataclass(frozen=True)
class VehicleTypeMetadata:
    type_id: str
    profile_id: str
    vehicle_class: str
    powertrain: str
    emission_class: str
    accel_mps2: float
    decel_mps2: float
    length_m: float
    width_m: float
    min_gap_m: float
    max_speed_mps: float
    fuel_density_mg_per_ml: float
    hard_braking_threshold_mps2: float


@dataclass(frozen=True)
class VehicleControlMetadata:
    supported_actions: Tuple[str, ...]
    action_lease_seconds: float
    speed_unit: str = "m/s"
    lane_change_scope: str = "current_edge"


@dataclass(frozen=True)
class LaneConnectionSignalObservation:
    connection_id: str
    movement: str
    downstream_lane_id: str
    signal_state: str


@dataclass(frozen=True)
class LaneObservation:
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float
    lane_has_green: bool | None = None
    signal_state: str | None = None
    queue_length_m: float = 0.0
    queue_length_is_estimate: bool = True
    current_allowed_speed_mps: float = 0.0
    controlled_vehicle_count: int = 0
    min_target_speed_mps: float | None = None
    mean_target_speed_mps: float | None = None
    connection_signal_states: Tuple[LaneConnectionSignalObservation, ...] = ()


@dataclass(frozen=True)
class IntersectionObservation:
    current_phase: int
    pending_phase: int | None
    stage: str
    stage_elapsed: float
    lanes: Mapping[str, LaneObservation]


@dataclass(frozen=True)
class TrafficObservation:
    active_vehicles: int
    departed_vehicles: int
    arrived_vehicles: int
    min_expected_vehicles: int
    fuel_consumed_mg: float = 0.0
    fuel_consumed_ml: float = 0.0
    hard_braking_events: int = 0


@dataclass(frozen=True)
class VehiclePositionObservation:
    x_m: float
    y_m: float


@dataclass(frozen=True)
class VehicleMotionObservation:
    speed_mps: float
    acceleration_mps2: float
    angle_deg: float
    allowed_speed_mps: float


@dataclass(frozen=True)
class VehicleLocationObservation:
    road_id: str
    lane_id: str
    lane_index: int
    lane_position_m: float
    route_id: str
    route_index: int
    route_edges: Tuple[str, ...]


@dataclass(frozen=True)
class VehicleTrafficObservation:
    waiting_time_s: float
    accumulated_waiting_time_s: float
    time_loss_s: float
    distance_m: float


@dataclass(frozen=True)
class NextSignalObservation:
    intersection_id: str
    tls_id: str
    distance_m: float
    state: str


@dataclass(frozen=True)
class VehicleEnergyObservation:
    fuel_rate_mg_s: float
    fuel_since_last_decision_mg: float
    fuel_total_mg: float
    fuel_total_ml: float


@dataclass(frozen=True)
class VehicleDrivingEventsObservation:
    hard_braking_since_last_decision: int
    hard_braking_total: int


@dataclass(frozen=True)
class VehicleObservation:
    type_id: str
    position: VehiclePositionObservation
    motion: VehicleMotionObservation
    location: VehicleLocationObservation
    traffic: VehicleTrafficObservation
    next_signal: NextSignalObservation | None
    energy: VehicleEnergyObservation
    driving_events: VehicleDrivingEventsObservation


@dataclass(frozen=True)
class PreviousVehicleActionResult:
    requested: Mapping[str, object]
    actual_speed_mps: float | None
    actual_lane_index: int | None
    speed_status: str | None
    lane_change_status: str | None


@dataclass(frozen=True)
class PreviousActionResults:
    step_id: int | None
    vehicles: Mapping[str, PreviousVehicleActionResult] = field(default_factory=dict)


@dataclass(frozen=True)
class SimulationObservation:
    protocol_version: str
    episode_id: str
    step_id: int
    simulation_time: float
    intersections: Mapping[str, IntersectionObservation]
    traffic: TrafficObservation
    vehicles: Mapping[str, VehicleObservation] = field(default_factory=dict)
    previous_action_results: PreviousActionResults = field(
        default_factory=lambda: PreviousActionResults(step_id=None)
    )


@dataclass(frozen=True)
class AIFrameObservation:
    protocol_version: str
    episode_id: str
    frame_id: int
    simulation_time: float
    intersections: Mapping[str, IntersectionObservation]
    traffic: TrafficObservation
    vehicles: Mapping[str, VehicleObservation] = field(default_factory=dict)
    previous_action_results: PreviousActionResults = field(
        default_factory=lambda: PreviousActionResults(step_id=None)
    )


@dataclass(frozen=True)
class AlgorithmDecision:
    signal_actions: Mapping[str, object]
    vehicle_actions: Mapping[str, object]


@dataclass(frozen=True)
class VehicleAction:
    target_speed_mps: float | None = None
    target_lane_index: int | None = None
