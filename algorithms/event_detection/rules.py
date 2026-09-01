"""Signal-aware rule baseline for SUMO lane-level event detection."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .congestion import classify_congestion
from .state import (
    IntersectionState,
    edge_id_from_lane,
    rows_to_intersection_states,
    to_bool,
)
from .semantics import (
    EVENT_LANE_BLOCKED,
    EVENT_NORMAL,
    EVENT_ACCIDENT,
    EVENT_SPEED_RESTRICTION,
    EVENT_SPILLBACK,
    cause_for_event_type,
    is_abnormal_event_type,
    traffic_state_for_event_type,
)


DEFAULT_TOPOLOGY = Path("data/maps/sumo/official_tls_topology.json")


@dataclass(frozen=True)
class RuleConfig:
    startup_loss_seconds: float = 10.0
    low_speed_mps: float = 1.0
    min_halting_count: int = 3
    min_occupancy: float = 25.0
    min_vehicle_count: int = 3
    min_waiting_delta: float = 0.0
    consecutive_points: int = 7
    enable_empty_lane_closure: bool = False
    closure_history_min_vehicle_count: int = 2
    closure_min_peer_vehicle_count: int = 4
    closure_max_vehicle_count: int = 0
    closure_max_occupancy: float = 0.005
    closure_max_allowed_speed: float = 1.0
    soft_closure_min_occupancy: float = 0.02
    soft_closure_max_occupancy: float = 0.3
    soft_closure_min_peer_mean_speed: float = 1.0
    soft_closure_min_speed_gap: float = 1.0
    queue_blockage_min_vehicle_count: int = 8
    queue_blockage_min_halting_count: int = 5
    queue_blockage_max_mean_speed: float = 8.0
    queue_blockage_min_waiting_delta: float = 5.0
    queue_blockage_min_waiting_time: float = 120.0
    queue_blockage_downstream_min_vehicle_count: int = 2
    queue_blockage_downstream_max_mean_speed: float = 1.0
    queue_blockage_downstream_min_occupancy: float = 0.01
    queue_blockage_cusum_threshold: float = 1.0
    speed_restriction_max_mean_speed: float = 2.5
    speed_restriction_min_vehicle_count: int = 4
    speed_restriction_max_halting_count: int = 1
    speed_restriction_min_occupancy: float = 0.03
    speed_restriction_max_occupancy: float = 0.15
    speed_restriction_cusum_threshold: float = 1.2
    accident_min_vehicle_count: int = 6
    accident_min_halting_count: int = 4
    accident_max_mean_speed: float = 9.0
    accident_min_waiting_time: float = 100.0
    accident_min_waiting_delta: float = 5.0
    accident_cusum_threshold: float = 0.5
    accident_max_allowed_speed: float = 1.0
    # Accident detection is still an unvalidated draft.  Keep it opt-in so it
    # cannot change the established lane-closure or spillback baselines.
    enable_accident: bool = False
    enable_queue_blockage: bool = False
    enable_speed_restriction: bool = False
    cycle_aware_cusum: bool = False
    confirmed_hold_seconds: float = 45.0
    use_cusum: bool = False
    closure_cusum_drift: float = 0.25
    closure_cusum_decay: float = 0.3
    closure_cusum_threshold: float = 2.5


@dataclass(frozen=True)
class DetectionRow:
    session_id: str
    elapsed_seconds: float
    official_time: str
    intersection_id: str
    lane_id: str
    edge_id: str
    approach_id: str
    current_phase: int | None
    stage: str
    stage_elapsed: float
    lane_has_green: bool
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    waiting_time_delta: float
    occupancy: float
    peer_vehicle_count: int
    peer_mean_speed: float
    closure_cusum_residual: float
    closure_cusum_score: float
    raw_suspicious: bool
    suspicious_streak: int
    event_type: str
    is_abnormal: bool
    traffic_state: str
    cause: str
    cause_confidence: float
    confidence: float
    reason: str


class PhaseGreenResolver:
    """Infer whether a lane should receive green from static topology.

    This is an approximation for offline CSVs that do not yet include
    lane-level signal state. It works best for the current demo_2 single
    intersection data and should be replaced by exported lane_has_green when
    available.
    """

    def __init__(self, topology_path: Path, period: str | None = None) -> None:
        self._phase_edges: dict[str, dict[int, set[str]]] = {}
        self._program_phase_edges: dict[str, dict[str, dict[int, set[str]]]] = {}
        self._phase_lanes: dict[str, dict[int, set[str]]] = {}
        self._program_phase_lanes: dict[str, dict[str, dict[int, set[str]]]] = {}
        self._incoming_edges: dict[str, set[str]] = {}
        self._downstream_lanes: dict[str, dict[str, set[str]]] = {}
        self._period = period
        if not topology_path.exists():
            return
        raw = json.loads(topology_path.read_text(encoding="utf-8-sig"))
        for intersection_id, item in raw.get("intersections", {}).items():
            manifest_connections = item.get("connections", [])
            if manifest_connections:
                self._incoming_edges[intersection_id] = {
                    str(connection["from_edge"]) for connection in manifest_connections
                }
                self._downstream_lanes[intersection_id] = self._parse_downstream_lanes(
                    manifest_connections
                )
                self._phase_lanes[intersection_id] = self._parse_manifest_phase_lanes(
                    manifest_connections,
                    item.get("templates", {}),
                )
                program_lanes = {}
                for program_id, program in item.get("programs", {}).items():
                    program_lanes[str(program_id)] = self._parse_manifest_phase_lanes(
                        manifest_connections,
                        program.get("templates", {}),
                    )
                self._program_phase_lanes[intersection_id] = program_lanes
                continue

            approaches = item.get("approaches", {})
            approach_edges = {
                str(name): {str(edge) for edge in value.get("incoming_edges", [])}
                for name, value in approaches.items()
            }
            self._incoming_edges[intersection_id] = {
                edge for edges in approach_edges.values() for edge in edges
            }
            self._phase_edges[intersection_id] = self._parse_phase_edges(
                item.get("phases", []),
                approach_edges,
            )
            program_edges = {}
            for program_id, program in item.get("programs", {}).items():
                program_edges[str(program_id)] = self._parse_phase_edges(
                    program.get("phases", []),
                    approach_edges,
                )
            self._program_phase_edges[intersection_id] = program_edges

    @staticmethod
    def _parse_downstream_lanes(
        connections: list[Mapping[str, object]],
    ) -> dict[str, set[str]]:
        downstream: dict[str, set[str]] = {}
        for connection in connections:
            from_lane = f"{connection['from_edge']}_{connection['from_lane']}"
            to_lane = f"{connection['to_edge']}_{connection['to_lane']}"
            downstream.setdefault(from_lane, set()).add(to_lane)
        return downstream

    @staticmethod
    def _parse_manifest_phase_lanes(
        connections: list[Mapping[str, object]],
        templates: Mapping[str, object],
    ) -> dict[int, set[str]]:
        phase_lanes: dict[int, set[str]] = {}
        for phase_id, raw_template in templates.items():
            template = raw_template if isinstance(raw_template, Mapping) else {}
            lanes = set()
            for connection in connections:
                tls_id = str(connection["tls_id"])
                link_index = int(connection["link_index"])
                tls_template = template.get(tls_id)
                if not isinstance(tls_template, Mapping):
                    continue
                green = str(tls_template.get("green", ""))
                if link_index >= len(green) or green[link_index] not in {"G", "g"}:
                    continue
                lanes.add(f"{connection['from_edge']}_{connection['from_lane']}")
            phase_lanes[int(phase_id)] = lanes
        return phase_lanes

    @staticmethod
    def _parse_phase_edges(
        phases: list[Mapping[str, object]],
        approach_edges: Mapping[str, set[str]],
    ) -> dict[int, set[str]]:
        phase_edges: dict[int, set[str]] = {}
        for phase in phases:
            phase_id = int(phase["official_phase_no"])
            green_edges = set()
            for approach in phase.get("approaches", []):
                green_edges.update(approach_edges.get(str(approach), set()))
            for group in phase.get("permissive", []):
                for approach in group.get("approaches", []):
                    green_edges.update(approach_edges.get(str(approach), set()))
            for group in phase.get("protected", []):
                for approach in group.get("approaches", []):
                    green_edges.update(approach_edges.get(str(approach), set()))
            phase_edges[phase_id] = green_edges
        return phase_edges

    def _phase_edges_for(self, intersection_id: str) -> dict[int, set[str]]:
        if self._period:
            program_id = f"{intersection_id}_{self._period}"
            phase_edges = self._program_phase_edges.get(intersection_id, {}).get(program_id)
            if phase_edges:
                return phase_edges
        return self._phase_edges.get(intersection_id, {})

    def _phase_lanes_for(self, intersection_id: str) -> dict[int, set[str]]:
        if self._period:
            program_id = f"{intersection_id}_{self._period}"
            phase_lanes = self._program_phase_lanes.get(intersection_id, {}).get(program_id)
            if phase_lanes:
                return phase_lanes
        return self._phase_lanes.get(intersection_id, {})

    def lane_has_green(
        self,
        row: Mapping[str, str],
        current_phase: int | None,
        stage: str,
    ) -> bool:
        explicit = to_bool(row.get("lane_has_green"))
        if explicit is not None:
            return explicit
        if current_phase is None or stage.upper() != "GREEN":
            return False
        intersection_id = row.get("intersection_id", "")
        phase_lanes = self._phase_lanes_for(intersection_id)
        if phase_lanes:
            return row.get("lane_id", "") in phase_lanes.get(current_phase, set())
        lane_edge = edge_id_from_lane(str(row.get("lane_id", "")))
        incoming_edges = self._incoming_edges.get(intersection_id)
        if incoming_edges is not None and lane_edge not in incoming_edges:
            return False
        phase_edges = self._phase_edges_for(intersection_id)
        return lane_edge in phase_edges.get(current_phase, set())

    def downstream_lane_ids(self, intersection_id: str, lane_id: str) -> tuple[str, ...]:
        downstream = self._downstream_lanes.get(intersection_id, {}).get(lane_id, set())
        return tuple(sorted(downstream))


def _score(
    *,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    waiting_delta: float,
    occupancy: float,
    config: RuleConfig,
) -> tuple[float, list[str]]:
    reasons = []
    components = []
    if vehicle_count >= config.min_vehicle_count:
        components.append(min(1.0, vehicle_count / max(config.min_vehicle_count, 1)))
        reasons.append("vehicle_count_high")
    if halting_count >= config.min_halting_count:
        components.append(min(1.0, halting_count / max(config.min_halting_count, 1)))
        reasons.append("halting_count_high")
    if mean_speed <= config.low_speed_mps:
        speed_score = max(0.0, 1.0 - mean_speed / max(config.low_speed_mps, 1e-9))
        components.append(speed_score)
        reasons.append("mean_speed_low")
    if occupancy >= config.min_occupancy:
        components.append(min(1.0, occupancy / max(config.min_occupancy, 1e-9)))
        reasons.append("occupancy_high")
    if waiting_delta > config.min_waiting_delta:
        components.append(1.0)
        reasons.append("waiting_time_increasing")
    score = sum(components) / len(components) if components else 0.0
    return score, reasons


def _closure_residual(
    *,
    lane_has_green: bool,
    stage_elapsed: float,
    had_prior_traffic: bool,
    peer_vehicle_count: int,
    vehicle_count: int,
    occupancy: float,
    config: RuleConfig,
) -> float:
    if (
        not config.enable_empty_lane_closure
        or not lane_has_green
        or stage_elapsed < config.startup_loss_seconds
        or not had_prior_traffic
        or peer_vehicle_count < config.closure_min_peer_vehicle_count
    ):
        return 0.0
    vehicle_score = 1.0 if vehicle_count <= config.closure_max_vehicle_count else 0.0
    occupancy_score = 1.0 if occupancy <= config.closure_max_occupancy else 0.0
    if vehicle_score <= 0.0 or occupancy_score <= 0.0:
        return 0.0
    return (vehicle_score + occupancy_score) / 2.0


def _soft_closure_residual(
    *,
    lane_has_green: bool,
    stage_elapsed: float,
    had_prior_traffic: bool,
    peer_vehicle_count: int,
    peer_mean_speed: float,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy: float,
    config: RuleConfig,
) -> float:
    speed_gap = peer_mean_speed - mean_speed
    if (
        not lane_has_green
        or stage_elapsed < config.startup_loss_seconds
        or not had_prior_traffic
        or peer_vehicle_count < config.closure_min_peer_vehicle_count
        or peer_mean_speed < config.soft_closure_min_peer_mean_speed
        or speed_gap < config.soft_closure_min_speed_gap
        or vehicle_count < config.min_vehicle_count
        or halting_count < config.min_halting_count
        or mean_speed > config.low_speed_mps
        or occupancy < config.soft_closure_min_occupancy
        or occupancy > config.soft_closure_max_occupancy
    ):
        return 0.0
    return 1.0


def _capacity_closure_residual(
    *,
    lane_has_green: bool,
    stage_elapsed: float,
    current_allowed_speed_mps: float | None,
    vehicle_count: int,
    config: RuleConfig,
) -> float:
    """Recognize a closure represented safely as an explicit capacity drop."""
    if (
        not lane_has_green
        or stage_elapsed < config.startup_loss_seconds
        or current_allowed_speed_mps is None
        or current_allowed_speed_mps <= 0.0
        or current_allowed_speed_mps > config.closure_max_allowed_speed
        or vehicle_count < 1
    ):
        return 0.0
    return 1.0


def _queue_blockage_residual(
    *,
    lane_has_green: bool,
    stage_elapsed: float,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    waiting_time: float,
    waiting_delta: float,
    downstream_lane_count: int,
    downstream_blocked_lane_count: int,
    config: RuleConfig,
) -> float:
    if not config.enable_queue_blockage:
        return 0.0
    if (
        not lane_has_green
        or stage_elapsed < config.startup_loss_seconds
        or vehicle_count < config.queue_blockage_min_vehicle_count
        or halting_count < config.queue_blockage_min_halting_count
        or mean_speed > config.queue_blockage_max_mean_speed
        or waiting_time < config.queue_blockage_min_waiting_time
        or waiting_delta <= config.queue_blockage_min_waiting_delta
        or downstream_lane_count <= 0
        or downstream_blocked_lane_count <= 0
    ):
        return 0.0
    vehicle_score = min(1.0, vehicle_count / max(config.queue_blockage_min_vehicle_count, 1))
    halting_score = min(1.0, halting_count / max(config.queue_blockage_min_halting_count, 1))
    speed_score = max(
        0.0,
        1.0 - mean_speed / max(config.queue_blockage_max_mean_speed, 1e-9),
    )
    return max(0.0, min(1.0, (vehicle_score + halting_score + speed_score) / 3.0))


def _speed_restriction_residual(
    *,
    lane_has_green: bool,
    stage_elapsed: float,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy: float,
    config: RuleConfig,
) -> float:
    if not config.enable_speed_restriction:
        return 0.0
    if (
        not lane_has_green
        or stage_elapsed < config.startup_loss_seconds
        or vehicle_count < config.speed_restriction_min_vehicle_count
        or halting_count > config.speed_restriction_max_halting_count
        or mean_speed > config.speed_restriction_max_mean_speed
        or occupancy < config.speed_restriction_min_occupancy
        or occupancy > config.speed_restriction_max_occupancy
    ):
        return 0.0
    flow_score = min(1.0, vehicle_count / max(config.speed_restriction_min_vehicle_count, 1))
    speed_score = max(
        0.0,
        1.0 - mean_speed / max(config.speed_restriction_max_mean_speed, 1e-9),
    )
    return max(0.0, min(1.0, (flow_score + speed_score) / 2.0))


def _accident_residual(*, lane_has_green: bool, stage_elapsed: float, vehicle_count: int,
                       halting_count: int, mean_speed: float, waiting_time: float,
                       waiting_delta: float, current_allowed_speed_mps: float | None,
                       downstream_blocked_lane_count: int,
                       config: RuleConfig) -> float:
    """Identify an incident-induced lane capacity drop from exported lane state.

    The earlier queue-only heuristic falsely classified normal green phases as
    accidents.  An active accident smoke scenario now exports the lane's
    allowed speed, which provides direct, signal-independent capacity evidence.
    """
    if not config.enable_accident:
        return 0.0
    direct_capacity_drop = (
        current_allowed_speed_mps is not None
        and current_allowed_speed_mps > 0.0
        and current_allowed_speed_mps <= config.accident_max_allowed_speed
        and vehicle_count >= config.accident_min_vehicle_count
    )
    upstream_queue_without_downstream_blockage = (
        lane_has_green
        and vehicle_count >= config.accident_min_vehicle_count
        and halting_count >= config.accident_min_halting_count
        and mean_speed <= config.accident_max_mean_speed
        and waiting_time >= config.accident_min_waiting_time
        and waiting_delta >= config.accident_min_waiting_delta
        and downstream_blocked_lane_count == 0
    )
    return 1.0 if direct_capacity_drop or upstream_queue_without_downstream_blockage else 0.0


def detect_states(
    states: list[IntersectionState],
    *,
    config: RuleConfig,
) -> list[DetectionRow]:
    ordered_states = sorted(
        states,
        key=lambda state: (
            state.elapsed_seconds,
            state.intersection_id,
        ),
    )
    previous_waiting: dict[tuple[str, str], float] = {}
    streaks: dict[tuple[str, str], int] = {}
    max_seen_vehicle_count: dict[tuple[str, str], int] = {}
    active_until: dict[tuple[str, str], float] = {}
    active_event_types: dict[tuple[str, str], str] = {}
    closure_cusum_scores: dict[tuple[str, str], float] = {}
    edge_vehicle_counts: dict[tuple[float, str, str], int] = {}
    edge_speed_sums: dict[tuple[float, str, str], float] = {}
    congestion_edge_vehicle_counts: dict[tuple[float, str], int] = {}
    congestion_edge_halting_counts: dict[tuple[float, str], int] = {}
    congestion_edge_speed_sums: dict[tuple[float, str], float] = {}
    congestion_edge_occupancy_sums: dict[tuple[float, str], float] = {}
    congestion_edge_lane_counts: dict[tuple[float, str], int] = {}
    congestion_edge_levels: dict[tuple[float, str], str] = {}
    lane_by_time: dict[tuple[float, str, str], object] = {}
    lane_records = [
        (state, lane)
        for state in ordered_states
        for lane in sorted(state.lanes, key=lambda item: item.lane_id)
    ]
    for state, lane in lane_records:
        lane_by_time[(state.elapsed_seconds, state.intersection_id, lane.lane_id)] = lane
        edge_key = (state.elapsed_seconds, state.intersection_id, lane.edge_id)
        edge_vehicle_counts[edge_key] = (
            edge_vehicle_counts.get(edge_key, 0) + lane.vehicle_count
        )
        edge_speed_sums[edge_key] = edge_speed_sums.get(edge_key, 0.0) + (
            lane.mean_speed * lane.vehicle_count
        )
        congestion_key = (state.elapsed_seconds, lane.edge_id)
        congestion_edge_vehicle_counts[congestion_key] = (
            congestion_edge_vehicle_counts.get(congestion_key, 0) + lane.vehicle_count
        )
        congestion_edge_halting_counts[congestion_key] = (
            congestion_edge_halting_counts.get(congestion_key, 0) + lane.halting_count
        )
        congestion_edge_speed_sums[congestion_key] = (
            congestion_edge_speed_sums.get(congestion_key, 0.0)
            + lane.mean_speed * lane.vehicle_count
        )
        congestion_edge_occupancy_sums[congestion_key] = (
            congestion_edge_occupancy_sums.get(congestion_key, 0.0) + lane.occupancy
        )
        congestion_edge_lane_counts[congestion_key] = (
            congestion_edge_lane_counts.get(congestion_key, 0) + 1
        )
    for congestion_key, vehicle_count in congestion_edge_vehicle_counts.items():
        mean_speed = (
            congestion_edge_speed_sums[congestion_key] / vehicle_count
            if vehicle_count
            else 0.0
        )
        occupancy = (
            congestion_edge_occupancy_sums[congestion_key]
            / congestion_edge_lane_counts[congestion_key]
        )
        congestion_edge_levels[congestion_key] = classify_congestion(
            vehicle_count=vehicle_count,
            halting_count=congestion_edge_halting_counts[congestion_key],
            mean_speed=mean_speed,
            occupancy=occupancy,
        )[0]
    detections = []

    for state, lane in lane_records:
        intersection_id = state.intersection_id
        lane_id = lane.lane_id
        lane_key = (intersection_id, lane_id)
        stage = state.stage
        elapsed_seconds = state.elapsed_seconds
        current_phase = state.current_phase
        stage_elapsed = state.stage_elapsed
        vehicle_count = lane.vehicle_count
        halting_count = lane.halting_count
        mean_speed = lane.mean_speed
        waiting_time = lane.waiting_time
        previous = previous_waiting.get(lane_key, waiting_time)
        waiting_delta = waiting_time - previous
        previous_waiting[lane_key] = waiting_time
        occupancy = lane.occupancy
        lane_has_green = lane.lane_has_green
        congestion_level = (
            congestion_edge_levels.get((elapsed_seconds, lane.edge_id), "free")
            if not lane.edge_id.startswith(":")
            else "free"
        )
        congestion_suspicious = congestion_level != "free"
        peer_vehicle_count = max(
            0,
            edge_vehicle_counts.get((elapsed_seconds, intersection_id, lane.edge_id), 0)
            - vehicle_count,
        )
        peer_speed_sum = max(
            0.0,
            edge_speed_sums.get((elapsed_seconds, intersection_id, lane.edge_id), 0.0)
            - mean_speed * vehicle_count,
        )
        peer_mean_speed = (
            peer_speed_sum / peer_vehicle_count if peer_vehicle_count > 0 else 0.0
        )
        downstream_lanes = [
            lane_by_time[(elapsed_seconds, intersection_id, downstream_lane_id)]
            for downstream_lane_id in lane.downstream_lane_ids
            if (elapsed_seconds, intersection_id, downstream_lane_id) in lane_by_time
        ]
        downstream_blocked_lane_count = sum(
            item.vehicle_count >= config.queue_blockage_downstream_min_vehicle_count
            and item.mean_speed <= config.queue_blockage_downstream_max_mean_speed
            and item.occupancy >= config.queue_blockage_downstream_min_occupancy
            for item in downstream_lanes
        )
        had_prior_traffic = (
            max_seen_vehicle_count.get(lane_key, 0)
            >= config.closure_history_min_vehicle_count
        )

        score, reasons = _score(
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            mean_speed=mean_speed,
            waiting_delta=waiting_delta,
            occupancy=occupancy,
            config=config,
        )
        blockage_suspicious = (
            lane_has_green
            and stage_elapsed >= config.startup_loss_seconds
            and vehicle_count >= config.min_vehicle_count
            and halting_count >= config.min_halting_count
            and mean_speed <= config.low_speed_mps
            and occupancy >= config.min_occupancy
            and waiting_delta >= config.min_waiting_delta
        )
        closure_suspicious = (
            lane_has_green
            and stage_elapsed >= config.startup_loss_seconds
            and had_prior_traffic
            and peer_vehicle_count >= config.closure_min_peer_vehicle_count
            and vehicle_count <= config.closure_max_vehicle_count
            and occupancy <= config.closure_max_occupancy
        )
        closure_residual = _closure_residual(
            lane_has_green=lane_has_green,
            stage_elapsed=stage_elapsed,
            had_prior_traffic=had_prior_traffic,
            peer_vehicle_count=peer_vehicle_count,
            vehicle_count=vehicle_count,
            occupancy=occupancy,
            config=config,
        )
        soft_closure_residual = _soft_closure_residual(
            lane_has_green=lane_has_green,
            stage_elapsed=stage_elapsed,
            had_prior_traffic=had_prior_traffic,
            peer_vehicle_count=peer_vehicle_count,
            peer_mean_speed=peer_mean_speed,
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            mean_speed=mean_speed,
            occupancy=occupancy,
            config=config,
        )
        capacity_closure_residual = _capacity_closure_residual(
            lane_has_green=lane_has_green,
            stage_elapsed=stage_elapsed,
            current_allowed_speed_mps=lane.current_allowed_speed_mps,
            vehicle_count=vehicle_count,
            config=config,
        )
        queue_blockage_residual = _queue_blockage_residual(
            lane_has_green=lane_has_green,
            stage_elapsed=stage_elapsed,
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            mean_speed=mean_speed,
            waiting_time=waiting_time,
            waiting_delta=waiting_delta,
            downstream_lane_count=len(downstream_lanes),
            downstream_blocked_lane_count=downstream_blocked_lane_count,
            config=config,
        )
        speed_restriction_residual = _speed_restriction_residual(
            lane_has_green=lane_has_green,
            stage_elapsed=stage_elapsed,
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            mean_speed=mean_speed,
            occupancy=occupancy,
            config=config,
        )
        accident_residual = _accident_residual(
            lane_has_green=lane_has_green, stage_elapsed=stage_elapsed,
            vehicle_count=vehicle_count, halting_count=halting_count,
            mean_speed=mean_speed, waiting_time=waiting_time,
            waiting_delta=waiting_delta,
            current_allowed_speed_mps=lane.current_allowed_speed_mps,
            downstream_blocked_lane_count=downstream_blocked_lane_count,
            config=config,
        )
        closure_residual = max(
            closure_residual,
            soft_closure_residual,
            capacity_closure_residual,
            queue_blockage_residual,
            speed_restriction_residual,
            accident_residual,
        )
        soft_closure_suspicious = soft_closure_residual > 0.0
        capacity_closure_suspicious = capacity_closure_residual > 0.0
        queue_blockage_suspicious = queue_blockage_residual > 0.0
        speed_restriction_suspicious = speed_restriction_residual > 0.0
        accident_suspicious = accident_residual > 0.0
        previous_closure_score = closure_cusum_scores.get(lane_key, 0.0)
        if closure_residual > 0.0:
            closure_score = max(
                0.0,
                previous_closure_score + closure_residual - config.closure_cusum_drift,
            )
        elif (
            not config.cycle_aware_cusum
            or (lane_has_green and stage_elapsed >= config.startup_loss_seconds)
        ):
            closure_score = max(0.0, previous_closure_score - config.closure_cusum_decay)
        else:
            closure_score = previous_closure_score
        closure_cusum_scores[lane_key] = closure_score
        raw_suspicious = (
            congestion_suspicious
            or blockage_suspicious
            or closure_suspicious
            or soft_closure_suspicious
            or capacity_closure_suspicious
            or queue_blockage_suspicious
            or speed_restriction_suspicious
            or accident_suspicious
        )
        if raw_suspicious:
            streaks[lane_key] = streaks.get(lane_key, 0) + 1
        else:
            streaks[lane_key] = 0
        streak = streaks.get(lane_key, 0)
        candidate_event_type = EVENT_LANE_BLOCKED
        if accident_suspicious:
            candidate_event_type = EVENT_ACCIDENT
        elif speed_restriction_suspicious:
            candidate_event_type = EVENT_SPEED_RESTRICTION
        elif queue_blockage_suspicious:
            candidate_event_type = EVENT_SPILLBACK
        elif (
            blockage_suspicious
            or closure_suspicious
            or soft_closure_suspicious
            or capacity_closure_suspicious
            or congestion_suspicious
        ):
            candidate_event_type = EVENT_LANE_BLOCKED

        event_cusum_threshold = config.closure_cusum_threshold
        if candidate_event_type == EVENT_SPILLBACK:
            event_cusum_threshold = config.queue_blockage_cusum_threshold
        elif candidate_event_type == EVENT_SPEED_RESTRICTION:
            event_cusum_threshold = config.speed_restriction_cusum_threshold
        elif candidate_event_type == EVENT_ACCIDENT:
            event_cusum_threshold = config.accident_cusum_threshold

        congestion_event_ready = (
            config.use_cusum
            and congestion_suspicious
            and candidate_event_type == EVENT_LANE_BLOCKED
            and streak >= config.consecutive_points
        )
        if (
            config.use_cusum
            and closure_residual > 0.0
            and closure_score >= event_cusum_threshold
        ):
            active_until[lane_key] = elapsed_seconds + config.confirmed_hold_seconds
            active_event_types[lane_key] = candidate_event_type
        elif congestion_event_ready or (
            not config.use_cusum
            and streak >= (1 if capacity_closure_suspicious else config.consecutive_points)
        ):
            active_until[lane_key] = elapsed_seconds + config.confirmed_hold_seconds
            active_event_types[lane_key] = candidate_event_type
        is_active = active_until.get(lane_key, -1.0) >= elapsed_seconds
        event_type = (
            active_event_types.get(lane_key, candidate_event_type)
            if is_active
            else EVENT_NORMAL
        )
        if congestion_suspicious and not (
            closure_suspicious
            or soft_closure_suspicious
            or capacity_closure_suspicious
            or queue_blockage_suspicious
            or speed_restriction_suspicious
            or accident_suspicious
        ):
            reason = f"traffic_style_{congestion_level}"
        elif not lane_has_green:
            reason = "no_lane_green"
        elif stage_elapsed < config.startup_loss_seconds:
            reason = "green_startup_loss"
        elif (
            config.use_cusum
            and candidate_event_type == EVENT_SPILLBACK
            and closure_score >= event_cusum_threshold
        ):
            reason = "queue_blockage_cusum_threshold"
        elif (
            config.use_cusum
            and candidate_event_type == EVENT_SPEED_RESTRICTION
            and closure_score >= event_cusum_threshold
        ):
            reason = "speed_restriction_cusum_threshold"
        elif config.use_cusum and candidate_event_type == EVENT_ACCIDENT and closure_score >= event_cusum_threshold:
            reason = "accident_lane_capacity_drop"
        elif config.use_cusum and closure_score >= config.closure_cusum_threshold:
            reason = "closure_cusum_threshold"
        elif speed_restriction_suspicious:
            reason = "speed_restriction_low_speed_with_flow"
        elif queue_blockage_suspicious:
            reason = "queue_blockage_not_releasing"
        elif soft_closure_suspicious:
            reason = "soft_closure_lane_slow_peer_moving"
        elif capacity_closure_suspicious:
            reason = "lane_capacity_restricted"
        elif closure_suspicious:
            reason = "green_lane_empty_after_history"
        elif reasons:
            reason = "+".join(reasons)
        else:
            reason = "normal"
        base_confidence = (
            0.75
            if (
                closure_suspicious
                or soft_closure_suspicious
                or capacity_closure_suspicious
                or queue_blockage_suspicious
                or speed_restriction_suspicious
                or accident_suspicious
                or is_active
            )
            else score
        )
        confidence = min(
            1.0,
            base_confidence
            if is_active
            else base_confidence * min(1.0, streak / max(config.consecutive_points, 1)),
        )
        max_seen_vehicle_count[lane_key] = max(
            max_seen_vehicle_count.get(lane_key, 0),
            vehicle_count,
        )

        detections.append(
            DetectionRow(
                session_id=state.session_id,
                elapsed_seconds=elapsed_seconds,
                official_time=state.official_time,
                intersection_id=intersection_id,
                lane_id=lane_id,
                edge_id=lane.edge_id,
                approach_id=lane.approach_id,
                current_phase=current_phase,
                stage=stage,
                stage_elapsed=stage_elapsed,
                lane_has_green=lane_has_green,
                vehicle_count=vehicle_count,
                halting_count=halting_count,
                mean_speed=mean_speed,
                waiting_time=waiting_time,
                waiting_time_delta=waiting_delta,
                occupancy=occupancy,
                peer_vehicle_count=peer_vehicle_count,
                peer_mean_speed=peer_mean_speed,
                closure_cusum_residual=closure_residual,
                closure_cusum_score=closure_score,
                raw_suspicious=raw_suspicious,
                suspicious_streak=streak,
                event_type=event_type,
                is_abnormal=is_abnormal_event_type(event_type),
                traffic_state=traffic_state_for_event_type(event_type),
                cause=cause_for_event_type(event_type),
                cause_confidence=0.0,
                confidence=confidence,
                reason=reason,
            )
        )
    return detections


def detect_rows(
    rows: list[Mapping[str, str]],
    *,
    resolver: PhaseGreenResolver,
    config: RuleConfig,
) -> list[DetectionRow]:
    states = rows_to_intersection_states(rows, resolver=resolver)
    return detect_states(states, config=config)


def read_lane_csv(path: Path) -> list[Mapping[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_detections(path: Path, rows: list[DetectionRow]) -> None:
    if not rows:
        raise ValueError("no detection rows to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run signal-aware rule detection on SUMO lane snapshot CSV."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cards-output",
        type=Path,
        default=None,
        help="Optional JSON output path for merged event cards.",
    )
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument(
        "--period",
        choices=("morning_peak", "off_peak", "evening_peak"),
        default=None,
        help="Use period-specific topology phases when available.",
    )
    parser.add_argument("--startup-loss-seconds", type=float, default=10.0)
    parser.add_argument("--low-speed-mps", type=float, default=1.0)
    parser.add_argument("--min-halting-count", type=int, default=3)
    parser.add_argument("--min-occupancy", type=float, default=25.0)
    parser.add_argument("--min-vehicle-count", type=int, default=3)
    parser.add_argument("--min-waiting-delta", type=float, default=0.0)
    parser.add_argument("--consecutive-points", type=int, default=7)
    parser.add_argument("--closure-history-min-vehicle-count", type=int, default=2)
    parser.add_argument(
        "--enable-empty-lane-closure",
        action="store_true",
        help=(
            "Enable the older empty-lane closure heuristic. It is disabled by "
            "default because normal turn lanes can be empty while peer lanes move."
        ),
    )
    parser.add_argument("--closure-min-peer-vehicle-count", type=int, default=4)
    parser.add_argument("--closure-max-vehicle-count", type=int, default=0)
    parser.add_argument("--closure-max-occupancy", type=float, default=0.005)
    parser.add_argument("--closure-max-allowed-speed", type=float, default=1.0)
    parser.add_argument("--soft-closure-min-occupancy", type=float, default=0.02)
    parser.add_argument("--soft-closure-max-occupancy", type=float, default=0.3)
    parser.add_argument("--soft-closure-min-peer-mean-speed", type=float, default=1.0)
    parser.add_argument("--soft-closure-min-speed-gap", type=float, default=1.0)
    parser.add_argument("--queue-blockage-min-vehicle-count", type=int, default=8)
    parser.add_argument("--queue-blockage-min-halting-count", type=int, default=5)
    parser.add_argument("--queue-blockage-max-mean-speed", type=float, default=8.0)
    parser.add_argument("--queue-blockage-min-waiting-delta", type=float, default=5.0)
    parser.add_argument("--queue-blockage-min-waiting-time", type=float, default=120.0)
    parser.add_argument("--queue-blockage-downstream-min-vehicle-count", type=int, default=2)
    parser.add_argument("--queue-blockage-downstream-max-mean-speed", type=float, default=1.0)
    parser.add_argument("--queue-blockage-downstream-min-occupancy", type=float, default=0.01)
    parser.add_argument("--queue-blockage-cusum-threshold", type=float, default=1.0)
    parser.add_argument("--speed-restriction-max-mean-speed", type=float, default=2.5)
    parser.add_argument("--speed-restriction-min-vehicle-count", type=int, default=4)
    parser.add_argument("--speed-restriction-max-halting-count", type=int, default=1)
    parser.add_argument("--speed-restriction-min-occupancy", type=float, default=0.03)
    parser.add_argument("--speed-restriction-max-occupancy", type=float, default=0.15)
    parser.add_argument("--speed-restriction-cusum-threshold", type=float, default=1.2)
    parser.add_argument("--accident-max-allowed-speed", type=float, default=1.0)
    parser.add_argument("--enable-queue-blockage", action="store_true")
    parser.add_argument("--enable-speed-restriction", action="store_true")
    parser.add_argument("--enable-accident", action="store_true")
    parser.add_argument("--cycle-aware-cusum", action="store_true")
    parser.add_argument("--confirmed-hold-seconds", type=float, default=45.0)
    parser.add_argument("--use-cusum", action="store_true")
    parser.add_argument("--closure-cusum-drift", type=float, default=0.25)
    parser.add_argument("--closure-cusum-decay", type=float, default=0.3)
    parser.add_argument("--closure-cusum-threshold", type=float, default=2.5)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = RuleConfig(
        startup_loss_seconds=args.startup_loss_seconds,
        low_speed_mps=args.low_speed_mps,
        min_halting_count=args.min_halting_count,
        min_occupancy=args.min_occupancy,
        min_vehicle_count=args.min_vehicle_count,
        min_waiting_delta=args.min_waiting_delta,
        consecutive_points=args.consecutive_points,
        enable_empty_lane_closure=args.enable_empty_lane_closure,
        closure_history_min_vehicle_count=args.closure_history_min_vehicle_count,
        closure_min_peer_vehicle_count=args.closure_min_peer_vehicle_count,
        closure_max_vehicle_count=args.closure_max_vehicle_count,
        closure_max_occupancy=args.closure_max_occupancy,
        closure_max_allowed_speed=args.closure_max_allowed_speed,
        soft_closure_min_occupancy=args.soft_closure_min_occupancy,
        soft_closure_max_occupancy=args.soft_closure_max_occupancy,
        soft_closure_min_peer_mean_speed=args.soft_closure_min_peer_mean_speed,
        soft_closure_min_speed_gap=args.soft_closure_min_speed_gap,
        queue_blockage_min_vehicle_count=args.queue_blockage_min_vehicle_count,
        queue_blockage_min_halting_count=args.queue_blockage_min_halting_count,
        queue_blockage_max_mean_speed=args.queue_blockage_max_mean_speed,
        queue_blockage_min_waiting_delta=args.queue_blockage_min_waiting_delta,
        queue_blockage_min_waiting_time=args.queue_blockage_min_waiting_time,
        queue_blockage_downstream_min_vehicle_count=(
            args.queue_blockage_downstream_min_vehicle_count
        ),
        queue_blockage_downstream_max_mean_speed=args.queue_blockage_downstream_max_mean_speed,
        queue_blockage_downstream_min_occupancy=args.queue_blockage_downstream_min_occupancy,
        queue_blockage_cusum_threshold=args.queue_blockage_cusum_threshold,
        speed_restriction_max_mean_speed=args.speed_restriction_max_mean_speed,
        speed_restriction_min_vehicle_count=args.speed_restriction_min_vehicle_count,
        speed_restriction_max_halting_count=args.speed_restriction_max_halting_count,
        speed_restriction_min_occupancy=args.speed_restriction_min_occupancy,
        speed_restriction_max_occupancy=args.speed_restriction_max_occupancy,
        speed_restriction_cusum_threshold=args.speed_restriction_cusum_threshold,
        accident_max_allowed_speed=args.accident_max_allowed_speed,
        enable_accident=args.enable_accident,
        enable_queue_blockage=args.enable_queue_blockage,
        enable_speed_restriction=args.enable_speed_restriction,
        cycle_aware_cusum=args.cycle_aware_cusum,
        confirmed_hold_seconds=args.confirmed_hold_seconds,
        use_cusum=args.use_cusum,
        closure_cusum_drift=args.closure_cusum_drift,
        closure_cusum_decay=args.closure_cusum_decay,
        closure_cusum_threshold=args.closure_cusum_threshold,
    )
    resolver = PhaseGreenResolver(args.topology, period=args.period)
    rows = read_lane_csv(args.input)
    detections = detect_rows(rows, resolver=resolver, config=config)
    write_detections(args.output, detections)
    if args.cards_output:
        from .cards import build_event_cards, write_cards

        write_cards(args.cards_output, build_event_cards(detections))
    alarm_count = sum(row.event_type != "normal" for row in detections)
    print(f"Wrote {len(detections)} detection rows to {args.output}")
    print(f"Alarms: {alarm_count}")


if __name__ == "__main__":
    main()
