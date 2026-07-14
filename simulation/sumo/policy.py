"""Stable data contract between SUMO and external signal-control algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple


PROTOCOL_VERSION = "1.0"


@dataclass(frozen=True)
class LaneMetadata:
    lane_id: str
    edge_id: str
    lane_index: int
    role: str
    length: float
    max_speed: float


@dataclass(frozen=True)
class RoadConnectionMetadata:
    connection_id: str
    approach: str
    movement: str
    from_lane: str
    to_lane: str
    direction: str


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


@dataclass(frozen=True)
class LaneObservation:
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float


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


@dataclass(frozen=True)
class SimulationObservation:
    protocol_version: str
    episode_id: str
    step_id: int
    simulation_time: float
    intersections: Mapping[str, IntersectionObservation]
    traffic: TrafficObservation
