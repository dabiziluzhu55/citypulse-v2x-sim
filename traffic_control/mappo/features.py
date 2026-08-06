"""Deployable IPPO-v8 feature builder for MAPPO inference (self-contained copy).

``StateBuilder`` is copied from ``algorithms/ippo/controller.py`` and
``IPPOV8FeatureBuilder`` from ``algorithms/mappo/features.py`` so the
deployment layer does not depend on the training workspace.  Keep in sync
whenever the training-side feature contract changes.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from traffic_control.ippo.contract import intersection_fingerprint_from_index
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS, identity_slots_for


DEFAULT_ACTION_INTERVAL = 15.0
DEFAULT_GREEN_DURATION = 30.0
MAX_WAITING = 200.0
MAX_STAGE_ELAPSED = 120.0
MAX_PHASES = 8
MAX_LANES = 20
MAX_OCCUPANCY = 100.0
VEHICLE_LENGTH_WITH_GAP = 7.5
SATURATION_FLOW_PER_LANE = 0.5
MIN_MAX_GREEN_SECONDS = 60.0
MAX_SERVICE_AGE = 120.0
PHASE_FEATURES = 11

CENTRALIZED_STATE_SCHEMA = "centralized_local_obs_pool_v1"
IPPO_V8_LOCAL_OBSERVATION_SCHEMA = "ippo_v8_local_obs_112_plus_identity_v1"
IPPO_V8_IDENTITY_OFFSET = 9


def _intersection_sort_key(intersection_id: str) -> Tuple[str, int, str]:
    prefix, separator, suffix = str(intersection_id).rpartition("_")
    if separator and suffix.isdigit():
        return prefix, int(suffix), ""
    return str(intersection_id), -1, str(intersection_id)


class _Idx:
    __slots__ = (
        "phase_order",
        "phase_index",
        "n_phases",
        "lane_order",
        "lane_capacities",
        "lane_speed_limits",
        "lane_edges",
        "outgoing_order",
        "outgoing_capacities",
        "phase_connections",
        "phase_movements",
        "phase_durations",
        "flow_reference_rate",
    )

    def __init__(self) -> None:
        self.phase_order: List[int] = []
        self.phase_index: Dict[int, int] = {}
        self.n_phases = 0
        self.lane_order: List[str] = []
        self.lane_capacities: Dict[str, float] = {}
        self.lane_speed_limits: Dict[str, float] = {}
        self.lane_edges: Dict[str, str] = {}
        self.outgoing_order: List[str] = []
        self.outgoing_capacities: Dict[str, float] = {}
        self.phase_connections: List[Tuple[Tuple[str, str], ...]] = []
        self.phase_movements: List[Tuple[Tuple[str, str], ...]] = []
        self.phase_durations: List[float] = []
        self.flow_reference_rate = SATURATION_FLOW_PER_LANE


def _build_index(i_meta: Mapping[str, Any]) -> _Idx:
    index = _Idx()
    index.phase_order = [int(phase) for phase in i_meta.get("phase_order", [])]
    index.phase_index = {phase: offset for offset, phase in enumerate(index.phase_order)}
    index.n_phases = len(index.phase_order)
    index.lane_order = list(i_meta.get("incoming_lanes", []))[:MAX_LANES]
    index.outgoing_order = list(i_meta.get("outgoing_lanes", []))
    lane_metadata = i_meta.get("lanes", {})
    for lane_id, metadata in lane_metadata.items():
        edge_id = metadata.get("edge_id")
        if edge_id is not None:
            index.lane_edges[str(lane_id)] = str(edge_id)
    for lane_id in index.lane_order:
        metadata = lane_metadata.get(lane_id, {})
        length = float(metadata.get("length_m", metadata.get("length", 150.0)))
        speed_limit = float(
            metadata.get("speed_limit_mps", metadata.get("max_speed", 20.0))
        )
        index.lane_capacities[lane_id] = max(length / VEHICLE_LENGTH_WITH_GAP, 1.0)
        index.lane_speed_limits[lane_id] = max(speed_limit, 1.0)
    for lane_id in index.outgoing_order:
        metadata = lane_metadata.get(lane_id, {})
        length = float(metadata.get("length_m", metadata.get("length", 150.0)))
        index.outgoing_capacities[lane_id] = max(
            length / VEHICLE_LENGTH_WITH_GAP, 1.0
        )

    connection_lanes = {
        str(item.get("connection_id")): str(item.get("from_lane"))
        for item in i_meta.get("connections", [])
        if item.get("connection_id") is not None and item.get("from_lane") is not None
    }
    connections_by_id = {
        str(item.get("connection_id")): item
        for item in i_meta.get("connections", [])
        if item.get("connection_id") is not None
    }
    phases = i_meta.get("phases", {})
    served_lane_counts = []
    for phase_id in index.phase_order:
        phase = phases.get(str(phase_id), phases.get(phase_id, {}))
        priorities = phase.get("connection_priorities", {})
        served_pairs = {
            (str(connection["from_lane"]), str(connection["to_lane"]))
            for connection_id in priorities
            for connection in (connections_by_id.get(str(connection_id)),)
            if connection
            and connection.get("from_lane") is not None
            and connection.get("to_lane") is not None
        }
        index.phase_connections.append(tuple(sorted(served_pairs)))
        index.phase_movements.append(
            tuple(
                sorted(
                    {
                        (index.lane_edges[incoming], index.lane_edges[outgoing])
                        for incoming, outgoing in served_pairs
                        if incoming in index.lane_edges
                        and outgoing in index.lane_edges
                    }
                )
            )
        )
        index.phase_durations.append(
            max(float(phase.get("green_seconds", DEFAULT_GREEN_DURATION)), 1.0)
        )
        served_lane_counts.append(
            len(
                {
                    connection_lanes[str(connection_id)]
                    for connection_id in priorities
                    if str(connection_id) in connection_lanes
                }
            )
        )
    max_served_lanes = max(served_lane_counts, default=0)
    if max_served_lanes <= 0:
        max_served_lanes = max(1, min(len(index.lane_order), 4))
    index.flow_reference_rate = SATURATION_FLOW_PER_LANE * max_served_lanes
    return index


class StateBuilder:
    """Build fixed-size, normalized local observations with agent identity."""

    def __init__(self, metadata: Mapping[str, Any]) -> None:
        raw = metadata.get("intersections", {})
        self.intersection_ids = tuple(sorted(raw, key=_intersection_sort_key))
        slot_indices = identity_slots_for(self.intersection_ids)
        self._agent_indices = {
            intersection_id: index
            for index, intersection_id in enumerate(self.intersection_ids)
        }
        self._slot_indices = dict(zip(self.intersection_ids, slot_indices))
        self._indices = {
            intersection_id: _build_index(raw[intersection_id])
            for intersection_id in self.intersection_ids
        }

    @property
    def max_state_dim(self) -> int:
        if not self.intersection_ids:
            return 0
        return MAX_PHASES + 1 + len(IDENTITY_SLOT_IDS) + 5 * MAX_LANES + 3

    def get_fingerprint(self, intersection_id: str) -> dict[str, Any]:
        index = self._indices.get(intersection_id)
        if index is None:
            raise ValueError(f"Unknown controlled intersection: {intersection_id}")
        return intersection_fingerprint_from_index(index)

    @property
    def max_phases(self) -> int:
        return max((index.n_phases for index in self._indices.values()), default=0)

    def get_phase_order(self, intersection_id: str) -> List[int]:
        index = self._indices.get(intersection_id)
        return list(index.phase_order) if index else []

    def get_incoming_lanes(self, intersection_id: str) -> Tuple[str, ...]:
        index = self._indices.get(intersection_id)
        return tuple(index.lane_order) if index else ()

    def get_outgoing_lanes(self, intersection_id: str) -> Tuple[str, ...]:
        index = self._indices.get(intersection_id)
        return tuple(index.outgoing_order) if index else ()

    def get_incoming_capacity(self, intersection_id: str) -> float:
        index = self._indices.get(intersection_id)
        return sum(index.lane_capacities.values()) if index else 1.0

    def get_lane_capacity(self, intersection_id: str, lane_id: str) -> float:
        index = self._indices.get(intersection_id)
        if index is None:
            return 1.0
        return float(
            index.lane_capacities.get(
                lane_id, index.outgoing_capacities.get(lane_id, 1.0)
            )
        )

    def get_flow_reference_rate(self, intersection_id: str) -> float:
        index = self._indices.get(intersection_id)
        return index.flow_reference_rate if index else SATURATION_FLOW_PER_LANE

    def build_phase_features(
        self,
        intersection_id: str,
        intersection: Mapping[str, Any],
        *,
        simulation_time: float,
        last_service_times: Mapping[int, float],
        vehicles: Optional[Mapping[str, Mapping[str, Any]]] = None,
        demand_horizon_seconds: float = DEFAULT_ACTION_INTERVAL,
    ) -> np.ndarray:
        """Describe what every candidate phase serves, independent of action index."""

        index = self._indices.get(intersection_id)
        if index is None:
            return np.zeros((0, PHASE_FEATURES), dtype=np.float32)
        features = np.zeros((index.n_phases, PHASE_FEATURES), dtype=np.float32)
        lanes = intersection.get("lanes", {})
        current_phase = int(
            intersection.get(
                "current_phase", index.phase_order[0] if index.phase_order else 0
            )
        )
        local_movements = {
            movement
            for phase_movements in index.phase_movements
            for movement in phase_movements
        }
        effective_etas: Dict[Tuple[str, str], List[float]] = {}
        for vehicle in (vehicles or {}).values():
            motion = vehicle.get("motion", {})
            speed = float(motion.get("speed_mps", 0.0))
            if not math.isfinite(speed) or speed <= 0.1:
                continue
            next_signal = vehicle.get("next_signal")
            if not isinstance(next_signal, Mapping):
                continue
            if str(next_signal.get("intersection_id", "")) != intersection_id:
                continue
            distance = float(next_signal.get("distance_m", math.inf))
            if not math.isfinite(distance) or distance <= 0.0:
                continue
            location = vehicle.get("location", {})
            route_edges = [str(edge) for edge in location.get("route_edges", ())]
            route_index = max(int(location.get("route_index", 0)), 0)
            next_movement = next(
                (
                    (route_edges[offset], route_edges[offset + 1])
                    for offset in range(route_index, len(route_edges) - 1)
                    if (route_edges[offset], route_edges[offset + 1])
                    in local_movements
                ),
                None,
            )
            if next_movement is None:
                continue
            effective_etas.setdefault(next_movement, []).append(distance / speed)

        def mean(values: Sequence[float]) -> float:
            return float(np.mean(values)) if values else 0.0

        for phase_offset, pairs in enumerate(index.phase_connections):
            incoming_lanes = sorted({incoming for incoming, _ in pairs})
            outgoing_lanes = sorted({outgoing for _, outgoing in pairs})
            incoming_density = [
                float(lanes.get(lane_id, {}).get("vehicle_count", 0.0))
                / index.lane_capacities.get(lane_id, 1.0)
                for lane_id in incoming_lanes
            ]
            incoming_halting = [
                float(lanes.get(lane_id, {}).get("halting_count", 0.0))
                / index.lane_capacities.get(lane_id, 1.0)
                for lane_id in incoming_lanes
            ]
            incoming_waiting = [
                float(lanes.get(lane_id, {}).get("waiting_time", 0.0))
                / max(MAX_WAITING * index.lane_capacities.get(lane_id, 1.0), 1.0)
                for lane_id in incoming_lanes
            ]
            outgoing_density = [
                float(lanes.get(lane_id, {}).get("vehicle_count", 0.0))
                / index.outgoing_capacities.get(lane_id, 1.0)
                for lane_id in outgoing_lanes
            ]
            outgoing_occupancy = [
                float(lanes.get(lane_id, {}).get("occupancy", 0.0)) / MAX_OCCUPANCY
                for lane_id in outgoing_lanes
            ]
            phase_id = index.phase_order[phase_offset]
            mean_incoming = mean(incoming_density)
            mean_outgoing = mean(outgoing_density)
            service_age = (
                0.0
                if phase_id == current_phase
                else max(
                    simulation_time
                    - float(last_service_times.get(phase_id, 0.0)),
                    0.0,
                )
            )
            demand_horizon = max(float(demand_horizon_seconds), 0.0)
            near_count = sum(
                1
                for movement in index.phase_movements[phase_offset]
                for eta in effective_etas.get(movement, ())
                if eta <= demand_horizon
            )
            far_count = sum(
                1
                for movement in index.phase_movements[phase_offset]
                for eta in effective_etas.get(movement, ())
                if demand_horizon < eta <= 2.0 * demand_horizon
            )
            demand_reference = (
                SATURATION_FLOW_PER_LANE
                * demand_horizon
                * max(len(incoming_lanes), 1)
            )

            def normalized_demand(count: int) -> float:
                return float(
                    np.clip(
                        math.log1p(count)
                        / math.log1p(max(demand_reference, 1.0)),
                        0.0,
                        1.0,
                    )
                )

            features[phase_offset] = np.asarray(
                [
                    np.clip(mean_incoming, 0.0, 1.0),
                    np.clip(mean(incoming_halting), 0.0, 1.0),
                    np.clip(mean(incoming_waiting), 0.0, 1.0),
                    np.clip(mean_outgoing, 0.0, 1.0),
                    np.clip(mean(outgoing_occupancy), 0.0, 1.0),
                    np.clip(mean_incoming - mean_outgoing, -1.0, 1.0),
                    float(phase_id == current_phase),
                    np.clip(service_age / MAX_SERVICE_AGE, 0.0, 1.0),
                    np.clip(
                        index.phase_durations[phase_offset] / MAX_STAGE_ELAPSED,
                        0.0,
                        1.0,
                    ),
                    normalized_demand(near_count),
                    normalized_demand(far_count),
                ],
                dtype=np.float32,
            )
        return features

    def build_action_mask(
        self,
        intersection_id: str,
        intersection: Mapping[str, Any],
        *,
        max_green_factor: float,
    ) -> Tuple[np.ndarray, bool]:
        """Mask padded actions and force an alternative after excessive green."""

        index = self._indices.get(intersection_id)
        if index is None or index.n_phases <= 0:
            raise ValueError(f"IPPO intersection {intersection_id!r} has no phases")
        mask = np.ones(index.n_phases, dtype=np.bool_)
        if max_green_factor <= 0.0 or index.n_phases <= 1:
            return mask, False
        current_phase = int(intersection.get("current_phase", index.phase_order[0]))
        current_offset = index.phase_index.get(current_phase)
        if current_offset is None:
            return mask, False
        max_green = max(
            MIN_MAX_GREEN_SECONDS,
            index.phase_durations[current_offset] * max_green_factor,
        )
        if float(intersection.get("stage_elapsed", 0.0)) + 1e-9 >= max_green:
            mask[current_offset] = False
            return mask, True
        return mask, False

    def build(self, intersection_id: str, observation: Mapping[str, Any]) -> np.ndarray:
        index = self._indices.get(intersection_id)
        intersection = observation.get("intersections", {}).get(intersection_id, {})
        if index is None or not intersection:
            return np.zeros(self.max_state_dim, dtype=np.float32)

        current_phase = int(
            intersection.get(
                "current_phase", index.phase_order[0] if index.phase_order else 0
            )
        )
        phase_one_hot = np.zeros(MAX_PHASES, dtype=np.float32)
        phase_offset = index.phase_index.get(current_phase)
        if phase_offset is not None and 0 <= phase_offset < MAX_PHASES:
            phase_one_hot[phase_offset] = 1.0

        elapsed = float(intersection.get("stage_elapsed", 0.0))
        elapsed_feature = np.array(
            [np.clip(elapsed / MAX_STAGE_ELAPSED, 0.0, 1.0)], dtype=np.float32
        )

        identity = np.zeros(len(IDENTITY_SLOT_IDS), dtype=np.float32)
        identity[self._slot_indices[intersection_id]] = 1.0

        lane_features: List[np.ndarray] = []
        lanes = intersection.get("lanes", {})
        for lane_offset in range(MAX_LANES):
            if lane_offset >= len(index.lane_order):
                lane_features.append(np.zeros(5, dtype=np.float32))
                continue
            lane_id = index.lane_order[lane_offset]
            lane = lanes.get(lane_id, {})
            capacity = index.lane_capacities[lane_id]
            speed_limit = index.lane_speed_limits[lane_id]
            lane_features.append(
                np.array(
                    [
                        np.clip(float(lane.get("vehicle_count", 0)) / capacity, 0.0, 1.0),
                        np.clip(float(lane.get("halting_count", 0)) / capacity, 0.0, 1.0),
                        np.clip(
                            float(lane.get("waiting_time", 0.0))
                            / (MAX_WAITING * capacity),
                            0.0,
                            1.0,
                        ),
                        np.clip(float(lane.get("mean_speed", 0.0)) / speed_limit, 0.0, 1.0),
                        np.clip(
                            float(lane.get("occupancy", 0.0)) / MAX_OCCUPANCY,
                            0.0,
                            1.0,
                        ),
                    ],
                    dtype=np.float32,
                )
            )
        outgoing_occupancies = [
            np.clip(
                float(lanes.get(lane_id, {}).get("occupancy", 0.0))
                / MAX_OCCUPANCY,
                0.0,
                1.0,
            )
            for lane_id in index.outgoing_order
        ]
        outgoing_queue_ratios = [
            np.clip(
                float(lanes.get(lane_id, {}).get("halting_count", 0.0))
                / index.outgoing_capacities[lane_id],
                0.0,
                1.0,
            )
            for lane_id in index.outgoing_order
        ]
        outgoing_summary = np.asarray(
            [
                float(np.mean(outgoing_occupancies)) if outgoing_occupancies else 0.0,
                max(outgoing_occupancies, default=0.0),
                max(outgoing_queue_ratios, default=0.0),
            ],
            dtype=np.float32,
        )
        return np.concatenate(
            [phase_one_hot, elapsed_feature, identity, *lane_features, outgoing_summary]
        )

    def get_all_states(self, observation: Mapping[str, Any]) -> Dict[str, np.ndarray]:
        return {
            intersection_id: self.build(intersection_id, observation)
            for intersection_id in self.intersection_ids
        }



class IPPOV8FeatureBuilder(StateBuilder):
    """Pinned adapter for the verified IPPO-v8 local feature contract."""

    pass
