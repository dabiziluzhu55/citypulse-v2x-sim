"""Stable Python API exposed to traffic-control algorithms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Tuple


@dataclass(frozen=True)
class LaneObservation:
    lane_id: str
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float


@dataclass(frozen=True)
class RoadConnectionMetadata:
    """A legal movement through one controlled intersection."""

    approach: str
    movement: str
    from_edge: str
    from_lane: int
    to_edge: str
    to_lane: int
    direction: str


@dataclass(frozen=True)
class IntersectionObservation:
    intersection_id: str
    current_phase: int
    stage: str
    stage_elapsed: float
    approaches: Mapping[str, Tuple[LaneObservation, ...]]


@dataclass(frozen=True)
class VehicleObservation:
    vehicle_id: str
    road_id: str
    lane_id: str
    lane_index: int
    lane_position: float
    speed: float
    allowed_speed: float
    waiting_time: float
    route: Tuple[str, ...]


@dataclass(frozen=True)
class SimulationObservation:
    simulation_time: float
    intersections: Mapping[str, IntersectionObservation]
    vehicles: Mapping[str, VehicleObservation] = field(default_factory=dict)


@dataclass(frozen=True)
class IntersectionMetadata:
    intersection_id: str
    phase_order: Tuple[int, ...]
    phase_movements: Mapping[int, Tuple[str, Tuple[str, ...]]]
    incoming_lanes: Mapping[str, Tuple[str, ...]]
    tls_ids: Tuple[str, ...]
    junction_ids: Tuple[str, ...] = ()
    connections: Tuple[RoadConnectionMetadata, ...] = ()


@dataclass(frozen=True)
class SimulationMetadata:
    intersections: Mapping[str, IntersectionMetadata]
    decision_interval: float
    minimum_green: float
    network_file: str = ""


@dataclass(frozen=True)
class VehicleAdvice:
    """A bounded vehicle command applied by the runner, never directly by a policy."""

    target_speed: float | None = None
    lane_index: int | None = None
    duration: float = 1.0


@dataclass(frozen=True)
class ControlAction:
    signal_phases: Mapping[str, int | None] = field(default_factory=dict)
    vehicle_advisories: Mapping[str, VehicleAdvice] = field(default_factory=dict)


class SignalPolicy(Protocol):
    """Algorithms return official phases and bounded vehicle advice."""

    def reset(self, metadata: SimulationMetadata) -> None:
        ...

    def act(
        self, observation: SimulationObservation
    ) -> ControlAction | Mapping[str, int | None]:
        ...

    def close(self) -> None:
        ...
