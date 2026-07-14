"""Stable Python API exposed to signal-control algorithms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Tuple


@dataclass(frozen=True)
class LaneObservation:
    lane_id: str
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float


@dataclass(frozen=True)
class IntersectionObservation:
    intersection_id: str
    current_phase: int
    stage: str
    stage_elapsed: float
    approaches: Mapping[str, Tuple[LaneObservation, ...]]


@dataclass(frozen=True)
class SimulationObservation:
    simulation_time: float
    intersections: Mapping[str, IntersectionObservation]


@dataclass(frozen=True)
class IntersectionMetadata:
    intersection_id: str
    phase_order: Tuple[int, ...]
    phase_movements: Mapping[int, Tuple[str, Tuple[str, ...]]]
    incoming_lanes: Mapping[str, Tuple[str, ...]]
    tls_ids: Tuple[str, ...]


@dataclass(frozen=True)
class SimulationMetadata:
    intersections: Mapping[str, IntersectionMetadata]
    decision_interval: float
    minimum_green: float


class SignalPolicy(Protocol):
    """Algorithms return official phase numbers, never raw SUMO states."""

    def reset(self, metadata: SimulationMetadata) -> None:
        ...

    def act(self, observation: SimulationObservation) -> Mapping[str, int | None]:
        ...

    def close(self) -> None:
        ...

