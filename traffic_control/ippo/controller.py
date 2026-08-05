"""部署版IPPO控制器（仅支持推理）
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import torch

from traffic_control.ippo.model import IPPONetwork, PHASE_FEATURES
from traffic_control.ippo.contract import (
    intersection_fingerprint_from_index,
    load_contract,
    validate_contract,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS, identity_slots_for
from traffic_control.protocol import finish_response, initialize_response, step_response

logger = logging.getLogger(__name__)

MODEL_VERSION = "v8"
DEFAULT_ACTION_INTERVAL = 15.0
DEFAULT_MODEL_FILENAME = "ippo_v8_20tls_ep160.pt"
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / DEFAULT_MODEL_FILENAME

MAX_WAITING = 200.0
MAX_STAGE_ELAPSED = 120.0
MAX_PHASES = 8
MAX_LANES = 20
MAX_OCCUPANCY = 100.0
VEHICLE_LENGTH_WITH_GAP = 7.5

SATURATION_FLOW_PER_LANE = 0.5
DEFAULT_GREEN_DURATION = 30.0
DEFAULT_MAX_GREEN_FACTOR = 2.0
MIN_MAX_GREEN_SECONDS = 60.0
MAX_SERVICE_AGE = 120.0

PHASE_FEATURE_SCHEMA = "connection_pressure_service_age_eta_demand_v2"

_model: Optional[IPPONetwork] = None
_state_builder: Optional["StateBuilder"] = None
_phase_orders: Dict[str, List[int]] = {}
_intersection_ids: Tuple[str, ...] = ()
_model_intersection_ids: Tuple[str, ...] = ()
_inference_mode = "model"
_model_path: Optional[str] = None
_loaded_model_path: Optional[str] = None
_last_decision_times: Dict[str, float] = {}
_last_phase_service_times: Dict[str, Dict[int, float]] = {}
_pending_signal_commands: Dict[str, dict] = {}
_signal_execution_stats: Dict[str, dict] = {}
_decision_interval = 5.0
_minimum_green = 5.0
_action_interval = DEFAULT_ACTION_INTERVAL
_max_green_factor = DEFAULT_MAX_GREEN_FACTOR
_effective_demand_enabled = True
_obs_dim = 0
_act_dim = 0


def _effective_demand_from_environment() -> bool:
    value = os.environ.get("IPPO_EFFECTIVE_DEMAND", "on").strip().lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    raise ValueError("IPPO_EFFECTIVE_DEMAND must be 'on' or 'off'.")


def default_model_path() -> Path:
    alias = os.environ.get("IPPO_MODEL_ALIAS", "").strip()
    if alias:
        from .aliases import resolve_model_path

        return resolve_model_path(alias)
    override = os.environ.get("IPPO_MODEL_PATH", "").strip()
    if override:
        return Path(override)
    return DEFAULT_MODEL_PATH

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
        """Topology fingerprint of one controlled intersection (contract v2)."""
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


def _masked_categorical(
    logits: torch.Tensor, valid_actions: int | Sequence[int] | torch.Tensor
) -> torch.distributions.Categorical:
    if logits.ndim != 2 or logits.shape[1] <= 0:
        raise ValueError("logits must have shape [batch, actions].")
    raw = torch.as_tensor(valid_actions, device=logits.device)
    if raw.dtype == torch.bool:
        if raw.ndim == 1:
            raw = raw.unsqueeze(0).expand(logits.shape[0], -1)
        if tuple(raw.shape) != tuple(logits.shape):
            raise ValueError("boolean action masks must match logits shape.")
        if torch.any(~raw.any(dim=1)):
            raise ValueError("every action mask row must enable at least one action.")
        return torch.distributions.Categorical(logits=logits.masked_fill(~raw, -1e8))

    counts = raw.to(dtype=torch.long)
    if counts.ndim == 0:
        counts = counts.repeat(logits.shape[0])
    if counts.ndim != 1 or counts.shape[0] != logits.shape[0]:
        raise ValueError("valid_action_counts must contain one value per batch row.")
    if torch.any(counts < 1) or torch.any(counts > logits.shape[1]):
        raise ValueError("valid_action_counts must be between 1 and the action dimension.")
    action_indices = torch.arange(logits.shape[1], device=logits.device).unsqueeze(0)
    masked_logits = logits.masked_fill(action_indices >= counts.unsqueeze(1), -1e8)
    return torch.distributions.Categorical(logits=masked_logits)

def load_checkpoint_metadata(path: str | os.PathLike[str]) -> dict:
    checkpoint = torch.load(Path(path), map_location="cpu")
    if not isinstance(checkpoint, dict) or "model_state_dict" not in checkpoint:
        raise ValueError(
            f"Checkpoint is a legacy raw state_dict; an IPPO {MODEL_VERSION} checkpoint is required."
        )
    required = {
        "model_version",
        "intersection_ids",
        "obs_dim",
        "act_dim",
        "action_interval",
    }
    missing = sorted(required - checkpoint.keys())
    if missing:
        raise ValueError(f"Checkpoint metadata is incomplete: {missing}")
    return checkpoint




def _eligible_for_decision(
    intersection: Mapping[str, Any], simulation_time: float, last_decision: float
) -> bool:
    return (
        str(intersection.get("stage", "")).upper() == "GREEN"
        and intersection.get("pending_phase") is None
        and float(intersection.get("stage_elapsed", 0.0)) + 1e-9 >= _minimum_green
        and simulation_time + 1e-9 >= last_decision + _action_interval
    )


def _observe_signal_execution(
    intersections: Mapping[str, Any], simulation_time: float
) -> None:
    """Infer whether a previously requested phase became effective."""

    for intersection_id in _intersection_ids:
        intersection = intersections.get(intersection_id, {})
        if str(intersection.get("stage", "")).upper() != "GREEN":
            continue
        current_phase = int(intersection.get("current_phase", -1))
        if current_phase in _last_phase_service_times[intersection_id]:
            _last_phase_service_times[intersection_id][current_phase] = simulation_time
        stats = _signal_execution_stats[intersection_id]
        stats["max_observed_green_s"] = max(
            float(stats["max_observed_green_s"]),
            float(intersection.get("stage_elapsed", 0.0)),
        )

    for intersection_id, pending in list(_pending_signal_commands.items()):
        intersection = intersections.get(intersection_id, {})
        if (
            str(intersection.get("stage", "")).upper() == "GREEN"
            and int(intersection.get("current_phase", -1))
            == int(pending["target_phase"])
        ):
            delay = max(simulation_time - float(pending["requested_at"]), 0.0)
            stats = _signal_execution_stats[intersection_id]
            stats["observed_changes"] += 1.0
            stats["change_delay_total_s"] += delay
            stats["change_delay_max_s"] = max(
                float(stats["change_delay_max_s"]), delay
            )
            _pending_signal_commands.pop(intersection_id, None)

def _record_signal_command(
    intersection_id: str,
    intersection: Mapping[str, Any],
    target_phase: int,
    simulation_time: float,
    *,
    max_green_forced: bool,
) -> None:
    stats = _signal_execution_stats[intersection_id]
    stats["commands"] += 1.0
    if max_green_forced:
        stats["max_green_forced_commands"] += 1.0
    phase_key = str(int(target_phase))
    phase_counts = stats["phase_commands"]
    phase_counts[phase_key] = int(phase_counts.get(phase_key, 0)) + 1
    current_phase = int(intersection.get("current_phase", target_phase))
    if int(target_phase) == current_phase:
        return
    stats["change_requests"] += 1.0
    if intersection_id in _pending_signal_commands:
        stats["unresolved_changes"] += 1.0
    _pending_signal_commands[intersection_id] = {
        "target_phase": int(target_phase),
        "requested_at": float(simulation_time),
    }


def _choose_action(
    state: np.ndarray, phase_features: np.ndarray, action_mask: np.ndarray
) -> int:
    valid_actions = np.flatnonzero(action_mask)
    if valid_actions.size == 0:
        raise RuntimeError("IPPO action mask contains no valid phase.")
    if _inference_mode == "fixed":
        return int(valid_actions[0])
    if _model is None:
        raise RuntimeError("IPPO model is not initialized.")
    with torch.no_grad():
        tensor = torch.from_numpy(state).unsqueeze(0).float()
        phase_tensor = torch.from_numpy(phase_features).unsqueeze(0).float()
        logits, _value = _model(tensor, phase_tensor)
        distribution = _masked_categorical(
            logits, torch.from_numpy(action_mask).unsqueeze(0)
        )
        # Deployed IPPO always selects the mode of the masked categorical.
        action = torch.argmax(distribution.probs, dim=-1)
        return int(action.item())


def initialize(payload: dict) -> dict:
    global _model, _state_builder, _phase_orders
    global _intersection_ids, _model_intersection_ids, _inference_mode, _model_path
    global _loaded_model_path
    global _last_decision_times, _last_phase_service_times
    global _signal_execution_stats, _pending_signal_commands
    global _decision_interval, _minimum_green, _action_interval, _max_green_factor
    global _effective_demand_enabled
    global _obs_dim, _act_dim

    mode = os.environ.get("IPPO_MODE", "model").strip().lower()
    if mode not in {"model", "fixed"}:
        raise ValueError(
            f"Unsupported IPPO_MODE: {mode!r}. Deployable IPPO supports 'model' or 'fixed'."
        )
    _inference_mode = mode
    _model_path = str(default_model_path())
    _effective_demand_enabled = _effective_demand_from_environment()

    _state_builder = StateBuilder(payload)
    _intersection_ids = _state_builder.intersection_ids
    _phase_orders = {
        intersection_id: _state_builder.get_phase_order(intersection_id)
        for intersection_id in _intersection_ids
    }
    _obs_dim = _state_builder.max_state_dim
    _act_dim = _state_builder.max_phases
    if not _intersection_ids or _obs_dim <= 0 or _act_dim <= 0:
        raise ValueError("IPPO requires at least one intersection with at least one phase.")
    if _act_dim > MAX_PHASES:
        raise ValueError(f"IPPO supports at most {MAX_PHASES} phases per intersection.")
    if any(not phases for phases in _phase_orders.values()):
        raise ValueError("Every controlled intersection must have at least one phase.")

    _decision_interval = float(payload.get("decision_interval", 5.0))
    _minimum_green = float(payload.get("minimum_green", 5.0))
    requested_interval = float(
        os.environ.get("IPPO_ACTION_INTERVAL", str(DEFAULT_ACTION_INTERVAL))
    )
    if _decision_interval <= 0 or _minimum_green < 0 or requested_interval <= 0:
        raise ValueError("Decision interval and action interval must be positive.")
    _action_interval = max(requested_interval, _decision_interval, _minimum_green)
    _max_green_factor = float(
        os.environ.get("IPPO_MAX_GREEN_FACTOR", str(DEFAULT_MAX_GREEN_FACTOR))
    )
    if not math.isfinite(_max_green_factor) or _max_green_factor < 0.0:
        raise ValueError("IPPO_MAX_GREEN_FACTOR must be finite and non-negative.")

    if mode == "model":
        model_path = Path(_model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"IPPO checkpoint does not exist: {model_path}")
        checkpoint = load_checkpoint_metadata(model_path)
        _act_dim = int(checkpoint["act_dim"])
        if _act_dim > MAX_PHASES:
            raise ValueError(f"IPPO supports at most {MAX_PHASES} phases per intersection.")
        _effective_demand_enabled = bool(
            checkpoint.get("effective_demand_enabled", True)
        )
        _contract_version, contract_view = load_contract(model_path, checkpoint)
        live_fingerprints = {
            intersection_id: _state_builder.get_fingerprint(intersection_id)
            for intersection_id in _intersection_ids
        }
        validate_contract(
            contract_view,
            intersection_ids=_intersection_ids,
            fingerprints=live_fingerprints,
            obs_dim=_obs_dim,
            act_dim=_act_dim,
            action_interval=_action_interval,
            max_green_factor=_max_green_factor,
            phase_feature_schema=PHASE_FEATURE_SCHEMA,
            effective_demand_enabled=_effective_demand_enabled,
            model_version=MODEL_VERSION,
        )
        _model = IPPONetwork(_obs_dim, _act_dim)
        _model.load_state_dict(checkpoint["model_state_dict"])
        _model.eval()
        _model_intersection_ids = _intersection_ids
        _loaded_model_path = str(model_path.resolve())
        logger.info("IPPO %s 推理: %s", MODEL_VERSION, _loaded_model_path)
    else:
        _model = None
        _loaded_model_path = None
        _model_intersection_ids = _intersection_ids
        logger.info("IPPO %s fixed 占位模式（无模型）", MODEL_VERSION)

    if _state_builder.max_phases > _act_dim:
        raise ValueError(
            "Active subset requires more phase slots than the checkpoint action "
            f"dimension ({_state_builder.max_phases} > {_act_dim}); the checkpoint "
            "cannot represent this subset."
        )

    _last_decision_times = {
        intersection_id: -math.inf for intersection_id in _intersection_ids
    }
    _last_phase_service_times = {
        intersection_id: {phase: 0.0 for phase in _phase_orders[intersection_id]}
        for intersection_id in _intersection_ids
    }
    _signal_execution_stats = {
        intersection_id: {
            "commands": 0.0,
            "change_requests": 0.0,
            "observed_changes": 0.0,
            "change_delay_total_s": 0.0,
            "change_delay_max_s": 0.0,
            "unresolved_changes": 0.0,
            "max_green_forced_commands": 0.0,
            "max_observed_green_s": 0.0,
            "valid_phase_count": len(_phase_orders[intersection_id]),
            "phase_commands": {},
        }
        for intersection_id in _intersection_ids
    }
    _pending_signal_commands = {}
    return initialize_response(episode_id=str(payload["episode_id"]))


def step(payload: dict) -> dict:
    if _state_builder is None:
        raise RuntimeError("IPPO is not initialized.")
    simulation_time = float(
        payload.get(
            "simulation_time", float(payload.get("step_id", 0)) * _decision_interval
        )
    )
    observations = payload.get("intersections", {})
    _observe_signal_execution(observations, simulation_time)
    states = _state_builder.get_all_states(payload)
    signal_actions: Dict[str, dict] = {}

    for intersection_id in _intersection_ids:
        intersection = observations.get(intersection_id)
        if not intersection:
            continue
        state = states[intersection_id]
        if not _eligible_for_decision(
            intersection, simulation_time, _last_decision_times[intersection_id]
        ):
            continue

        phase_order = _phase_orders[intersection_id]
        local_features = _state_builder.build_phase_features(
            intersection_id,
            intersection,
            simulation_time=simulation_time,
            last_service_times=_last_phase_service_times[intersection_id],
            vehicles=(payload.get("vehicles", {}) if _effective_demand_enabled else {}),
            demand_horizon_seconds=_action_interval,
        )
        local_mask, max_green_forced = _state_builder.build_action_mask(
            intersection_id,
            intersection,
            max_green_factor=_max_green_factor,
        )
        phase_features = np.zeros((_act_dim, PHASE_FEATURES), dtype=np.float32)
        phase_features[: len(phase_order)] = local_features
        action_mask = np.zeros(_act_dim, dtype=np.bool_)
        action_mask[: len(phase_order)] = local_mask
        action_index = _choose_action(state, phase_features, action_mask)
        target_phase = phase_order[action_index]
        signal_actions[intersection_id] = {"target_phase": target_phase}
        _record_signal_command(
            intersection_id,
            intersection,
            target_phase,
            simulation_time,
            max_green_forced=max_green_forced,
        )
        _last_decision_times[intersection_id] = simulation_time

    return step_response(
        episode_id=str(payload["episode_id"]),
        step_id=payload["step_id"],
        signals=signal_actions,
    )


def finish(payload: dict) -> dict:
    global _model, _state_builder, _phase_orders, _intersection_ids
    global _model_intersection_ids, _loaded_model_path, _model_path
    global _last_decision_times, _last_phase_service_times
    global _signal_execution_stats, _pending_signal_commands
    already = _state_builder is None
    _state_builder = None
    _phase_orders = {}
    _intersection_ids = ()
    _last_decision_times = {}
    _last_phase_service_times = {}
    _signal_execution_stats = {}
    _pending_signal_commands = {}
    # Keep loaded model weights for warm reuse within the same worker process.
    _ = payload
    return finish_response(already_finished=already)
