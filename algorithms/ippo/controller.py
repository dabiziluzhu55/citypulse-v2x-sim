"""Phase-aware IPPO with execution-aligned rollouts.

The SUMO side calls :func:`step` every ``decision_interval`` seconds.  A policy
decision is emitted only when the signal controller can accept it: the signal
must be green, have no pending transition, satisfy minimum green, and satisfy
the slower IPPO action interval.  Rewards observed between two decisions are
therefore credited to the last action that SUMO actually accepted.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from traffic_control.ippo.contract import (
    CHECKPOINT_CONTRACT_VERSION,
    COLLAB_MESSAGE_SCHEMA,
    NORMALIZATION,
    OBSERVATION_SCHEMA,
    fingerprint_sha256,
    intersection_fingerprint_from_index,
    load_contract,
    validate_contract,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS, identity_slots_for
logger = logging.getLogger(__name__)

MODEL_VERSION = "v8"
DEFAULT_ACTION_INTERVAL = 15.0
DEFAULT_INTERSECTION_IDS = tuple(f"demo_{index}" for index in range(1, 21))

MAX_WAITING = 200.0
MAX_STAGE_ELAPSED = 120.0
MAX_PHASES = 8
MAX_LANES = 20
MAX_OCCUPANCY = 100.0
VEHICLE_LENGTH_WITH_GAP = 7.5

ACTOR_LR = 3e-4
CRITIC_LR = 1e-4
CLIP_EPS = 0.2
GAMMA = 0.99
GAE_LAMBDA = 0.95
ENTROPY_COEF = 0.01
PPO_EPOCHS = 4
BATCH_SIZE = 128
ACCUMULATE_EPISODES = 4
CHECKPOINT_INTERVAL = 20
MAX_GRAD_NORM = 0.5
HUBER_DELTA = 10.0
REWARD_MIN = -3.0
REWARD_MAX = 1.0
SATURATION_FLOW_PER_LANE = 0.5
DEFAULT_GREEN_DURATION = 30.0
DEFAULT_MAX_GREEN_FACTOR = 2.0
MIN_MAX_GREEN_SECONDS = 60.0
MAX_SERVICE_AGE = 120.0

PHASE_FEATURES = 11
PHASE_PRESSURE_INDEX = 5
PHASE_CURRENT_INDEX = 6
PHASE_SERVICE_AGE_INDEX = 7
PHASE_NEAR_DEMAND_INDEX = 9
PHASE_FAR_DEMAND_INDEX = 10
PHASE_FEATURE_SCHEMA = "connection_pressure_service_age_eta_demand_v2"
REWARD_COMPONENT_NAMES = ("D", "F_safe", "B", "H")

_model: Optional["IPPONetwork"] = None
_optimizer_actor: Optional[torch.optim.Adam] = None
_optimizer_critic: Optional[torch.optim.Adam] = None
_state_builder: Optional["StateBuilder"] = None
_episode = 0

_buffer_episodes: List[Dict[str, List[dict]]] = []
_episode_trajectories: Dict[str, List[dict]] = {}
_pending_transitions: Dict[str, dict] = {}
_latest_states: Dict[str, np.ndarray] = {}

_phase_orders: Dict[str, List[int]] = {}
_intersection_ids: Tuple[str, ...] = ()
_model_intersection_ids: Tuple[str, ...] = ()
_inference_mode = "random"
_model_path: Optional[str] = None
_loaded_model_path: Optional[str] = None
_vehicle_reward_state: Dict[str, Tuple[float, Optional[str]]] = {}
_last_reward_time: Optional[float] = None
_last_decision_times: Dict[str, float] = {}
_last_phase_service_times: Dict[str, Dict[int, float]] = {}
_signal_execution_stats: Dict[str, dict] = {}
_pending_signal_commands: Dict[str, dict] = {}
_decision_interval = 5.0
_minimum_green = 5.0
_action_interval = DEFAULT_ACTION_INTERVAL
_max_green_factor = DEFAULT_MAX_GREEN_FACTOR
_effective_demand_enabled = True
_obs_dim = 0
_act_dim = 0

_collector_policy_state: Optional[Dict[str, torch.Tensor]] = None
_collector_policy_seed: Optional[int] = None
_collector_rollout_seed: Optional[int] = None
_collector_metadata: Optional[dict] = None
_collected_rollout: Optional[dict] = None
_evaluation_episode_id = ""


def _orthogonal_init(layer: nn.Module, gain: float) -> None:
    if isinstance(layer, nn.Linear):
        nn.init.orthogonal_(layer.weight, gain)
        nn.init.constant_(layer.bias, 0.0)


def _effective_demand_from_environment() -> bool:
    value = os.environ.get("IPPO_EFFECTIVE_DEMAND", "on").strip().lower()
    if value in {"1", "true", "on", "yes"}:
        return True
    if value in {"0", "false", "off", "no"}:
        return False
    raise ValueError("IPPO_EFFECTIVE_DEMAND must be 'on' or 'off'.")


class IPPONetwork(nn.Module):
    """Parameter-shared, candidate-scoring policy with a separate critic."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: int = 128):
        super().__init__()
        self.obs_dim = int(obs_dim)
        self.act_dim = int(act_dim)
        self.actor_body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.critic_body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.phase_actor = nn.Sequential(
            nn.Linear(hidden + PHASE_FEATURES, hidden),
            nn.Tanh(),
            nn.Linear(hidden, 1),
        )
        self.critic = nn.Linear(hidden, 1)

        for layer in self.actor_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        for layer in self.critic_body:
            _orthogonal_init(layer, math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[0], math.sqrt(2.0))
        _orthogonal_init(self.phase_actor[2], 0.01)
        _orthogonal_init(self.critic, 1.0)

    def actor_forward(
        self, obs: torch.Tensor, phase_features: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        if obs.ndim != 2:
            raise ValueError("actor observations must have shape [batch, features].")
        batch = obs.shape[0]
        if phase_features is None:
            phase_features = torch.zeros(
                batch,
                self.act_dim,
                PHASE_FEATURES,
                dtype=obs.dtype,
                device=obs.device,
            )
        expected = (batch, self.act_dim, PHASE_FEATURES)
        if tuple(phase_features.shape) != expected:
            raise ValueError(
                f"phase features must have shape {expected}, got {tuple(phase_features.shape)}"
            )
        context = self.actor_body(obs).unsqueeze(1).expand(-1, self.act_dim, -1)
        return self.phase_actor(torch.cat((context, phase_features), dim=-1)).squeeze(-1)

    def critic_forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.critic(self.critic_body(obs))

    def forward(
        self, obs: torch.Tensor, phase_features: Optional[torch.Tensor] = None
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        return self.actor_forward(obs, phase_features), self.critic_forward(obs)

    def actor_parameters(self) -> Iterable[nn.Parameter]:
        return (*self.actor_body.parameters(), *self.phase_actor.parameters())

    def critic_parameters(self) -> Iterable[nn.Parameter]:
        return (*self.critic_body.parameters(), *self.critic.parameters())


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


def _new_optimizers(model: IPPONetwork) -> Tuple[torch.optim.Adam, torch.optim.Adam]:
    return (
        torch.optim.Adam(model.actor_parameters(), lr=ACTOR_LR, eps=1e-5),
        torch.optim.Adam(model.critic_parameters(), lr=CRITIC_LR, eps=1e-5),
    )


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


def _validate_checkpoint(
    checkpoint: Mapping[str, Any],
    intersection_ids: Sequence[str],
    obs_dim: int,
    act_dim: int,
    action_interval: float,
) -> None:
    if checkpoint.get("model_version") != MODEL_VERSION:
        raise ValueError(
            f"Checkpoint version {checkpoint.get('model_version')!r} is not {MODEL_VERSION!r}."
        )
    saved_ids = tuple(str(value) for value in checkpoint.get("intersection_ids", ()))
    current_ids = tuple(str(iid) for iid in intersection_ids)
    if not set(current_ids) <= set(saved_ids):
        raise ValueError(
            f"Checkpoint intersection_ids are not a superset of current: "
            f"saved={saved_ids}, current={current_ids}"
        )
    if int(checkpoint.get("obs_dim", -1)) != obs_dim:
        raise ValueError("Checkpoint observation dimension does not match current metadata.")
    if int(checkpoint.get("act_dim", -1)) != act_dim:
        raise ValueError("Checkpoint action dimension does not match current metadata.")
    if abs(float(checkpoint["action_interval"]) - action_interval) > 1e-9:
        raise ValueError(
            "Checkpoint action interval does not match the current IPPO configuration."
        )
    if checkpoint.get("phase_feature_schema") != PHASE_FEATURE_SCHEMA:
        raise ValueError("Checkpoint phase feature schema does not match current IPPO.")
    if abs(float(checkpoint.get("max_green_factor", -1.0)) - _max_green_factor) > 1e-9:
        raise ValueError("Checkpoint maximum-green configuration does not match current IPPO.")


def _reset_training_model(obs_dim: int, act_dim: int) -> None:
    global _model, _optimizer_actor, _optimizer_critic, _model_intersection_ids
    global _loaded_model_path, _buffer_episodes, _episode
    _model = IPPONetwork(obs_dim, act_dim)
    _optimizer_actor, _optimizer_critic = _new_optimizers(_model)
    _model_intersection_ids = _intersection_ids
    _loaded_model_path = None
    _buffer_episodes.clear()
    _episode = 0


def _copy_policy_state(
    state: Optional[Mapping[str, torch.Tensor]],
) -> Optional[Dict[str, torch.Tensor]]:
    if state is None:
        return None
    return {
        str(name): tensor.detach().cpu().clone()
        for name, tensor in state.items()
    }


def prepare_collector(
    *,
    policy_state: Optional[Mapping[str, torch.Tensor]],
    policy_seed: int,
    rollout_seed: int,
) -> None:
    """Install one immutable policy generation for a worker-process rollout."""

    global _collector_policy_state, _collector_policy_seed, _collector_rollout_seed
    global _collector_metadata, _collected_rollout
    _collector_policy_state = _copy_policy_state(policy_state)
    _collector_policy_seed = int(policy_seed)
    _collector_rollout_seed = int(rollout_seed)
    _collector_metadata = None
    _collected_rollout = None


def export_policy_state() -> Dict[str, torch.Tensor]:
    if _model is None:
        raise RuntimeError("IPPO model is not initialized.")
    copied = _copy_policy_state(_model.state_dict())
    assert copied is not None
    return copied


def install_parallel_initial_policy(state: Mapping[str, torch.Tensor]) -> None:
    """Adopt the collectors' initial policy before any optimizer update."""

    if _model is None:
        raise RuntimeError("IPPO model is not initialized.")
    if _episode != 0 or _buffer_episodes:
        raise RuntimeError("Initial policy can only be installed before the first rollout.")
    _model.load_state_dict(state)


def training_episode_count() -> int:
    return _episode


def initialize(payload: dict) -> dict:
    global _model, _optimizer_actor, _optimizer_critic, _state_builder, _phase_orders
    global _intersection_ids, _model_intersection_ids, _inference_mode, _model_path
    global _loaded_model_path, _vehicle_reward_state, _last_reward_time
    global _last_decision_times, _last_phase_service_times
    global _signal_execution_stats, _pending_signal_commands
    global _episode_trajectories, _pending_transitions, _latest_states
    global _decision_interval, _minimum_green, _action_interval, _max_green_factor
    global _effective_demand_enabled
    global _obs_dim, _act_dim
    global _episode, _collector_metadata, _collected_rollout
    global _evaluation_episode_id

    mode = os.environ.get("IPPO_MODE", "random").strip().lower()
    if mode not in {"random", "fixed", "model", "train", "collect"}:
        raise ValueError(f"Unsupported IPPO_MODE: {mode!r}")
    _inference_mode = mode
    _model_path = os.environ.get("IPPO_MODEL_PATH") or None
    _effective_demand_enabled = _effective_demand_from_environment()

    _state_builder = StateBuilder(payload)
    _collector_metadata = copy.deepcopy(payload)
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

    signature_matches = (
        _model is not None
        and _model.obs_dim == _obs_dim
        and _model.act_dim == _act_dim
        and _model_intersection_ids == _intersection_ids
    )

    if mode == "model":
        if not _model_path:
            raise ValueError("IPPO_MODEL_PATH is required when IPPO_MODE=model.")
        if not Path(_model_path).is_file():
            raise FileNotFoundError(f"IPPO checkpoint does not exist: {_model_path}")
        checkpoint = load_checkpoint_metadata(_model_path)
        _effective_demand_enabled = bool(
            checkpoint.get("effective_demand_enabled", True)
        )
        _contract_version, contract_view = load_contract(_model_path, checkpoint)
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
        _loaded_model_path = str(Path(_model_path).resolve())
        _optimizer_actor = None
        _optimizer_critic = None
        logger.info("IPPO %s 推理: %s", MODEL_VERSION, _model_path)
    elif mode == "train":
        resume_path = str(Path(_model_path).resolve()) if _model_path else None
        if resume_path and _loaded_model_path != resume_path:
            checkpoint = load_checkpoint_metadata(resume_path)
            _effective_demand_enabled = bool(
                checkpoint.get("effective_demand_enabled", True)
            )
            _contract_version, contract_view = load_contract(resume_path, checkpoint)
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
            _model_intersection_ids = _intersection_ids
            _loaded_model_path = resume_path
            _buffer_episodes.clear()
            _optimizer_actor, _optimizer_critic = _new_optimizers(_model)
            reset_optimizer = (
                os.environ.get("IPPO_RESET_OPTIMIZER", "0").strip().lower()
                in {"1", "true", "on", "yes"}
            )
            if not reset_optimizer:
                actor_state = checkpoint.get("optimizer_actor_state_dict")
                critic_state = checkpoint.get("optimizer_critic_state_dict")
                if actor_state is not None and critic_state is not None:
                    _optimizer_actor.load_state_dict(actor_state)
                    _optimizer_critic.load_state_dict(critic_state)
            else:
                _optimizer_actor, _optimizer_critic = _new_optimizers(_model)
            _episode = int(checkpoint.get("episode", 0))
            logger.info("IPPO %s 从 checkpoint 恢复权重: %s", MODEL_VERSION, resume_path)
        elif not signature_matches:
            if _model is not None:
                logger.warning("IPPO metadata changed; resetting incompatible model and rollout buffer.")
            _reset_training_model(_obs_dim, _act_dim)
        elif _optimizer_actor is None or _optimizer_critic is None:
            _optimizer_actor, _optimizer_critic = _new_optimizers(_model)
        _model.train()
        logger.info(
            "IPPO %s 训练: obs=%d act=%d 路口=%d action_interval=%.1fs "
            "max_green=%.1fx effective_demand=%s",
            MODEL_VERSION,
            _obs_dim,
            _act_dim,
            len(_intersection_ids),
            _action_interval,
            _max_green_factor,
            "on" if _effective_demand_enabled else "off",
        )
    elif mode == "collect":
        if _collector_policy_seed is None or _collector_rollout_seed is None:
            raise RuntimeError("prepare_collector() must be called before collect mode.")
        torch.manual_seed(_collector_policy_seed)
        _model = IPPONetwork(_obs_dim, _act_dim)
        if _collector_policy_state is not None:
            _model.load_state_dict(_collector_policy_state)
        _model.eval()
        _model_intersection_ids = _intersection_ids
        _optimizer_actor = None
        _optimizer_critic = None
        _loaded_model_path = None
        _collector_metadata = copy.deepcopy(payload)
        _collected_rollout = None
        torch.manual_seed(_collector_rollout_seed)
        np.random.seed(_collector_rollout_seed % (2**32 - 1))
        logger.info(
            "IPPO %s 并行采样: obs=%d act=%d 路口=%d rollout_seed=%d",
            MODEL_VERSION,
            _obs_dim,
            _act_dim,
            len(_intersection_ids),
            _collector_rollout_seed,
        )

    _vehicle_reward_state = {}
    _last_reward_time = None
    _last_decision_times = {intersection_id: -math.inf for intersection_id in _intersection_ids}
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
    _episode_trajectories = {intersection_id: [] for intersection_id in _intersection_ids}
    _pending_transitions = {}
    _latest_states = {}
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_runtime.start("IPPO", payload)
    _evaluation_episode_id = str(payload["episode_id"])
    return {"protocol_version": "2.0", "episode_id": payload["episode_id"], "ready": True}


def _normalize_reward(raw_reward: float) -> float:
    """Keep the physically normalized reward on one stationary scale."""

    return float(np.clip(raw_reward, REWARD_MIN, REWARD_MAX))


def _spillback_penalty(occupancy_percent: float) -> float:
    occupancy = float(np.clip(occupancy_percent / MAX_OCCUPANCY, 0.0, 1.0))
    return float(np.clip((occupancy - 0.70) / 0.20, 0.0, 1.0) ** 2)


def _next_intersection(vehicle: Mapping[str, Any]) -> Optional[str]:
    next_signal = vehicle.get("next_signal")
    if not isinstance(next_signal, Mapping):
        return None
    intersection_id = next_signal.get("intersection_id")
    return str(intersection_id) if intersection_id is not None else None


def _vehicle_interval_statistics(
    payload: Mapping[str, Any],
) -> Tuple[Dict[str, float], Dict[str, int]]:
    """Infer local delay increments and stop-line crossings from live telemetry."""

    global _vehicle_reward_state
    delays = {intersection_id: 0.0 for intersection_id in _intersection_ids}
    crossings = {intersection_id: 0 for intersection_id in _intersection_ids}
    current: Dict[str, Tuple[float, Optional[str]]] = {}
    controlled = set(_intersection_ids)
    vehicles = payload.get("vehicles", {})
    if not isinstance(vehicles, Mapping):
        vehicles = {}

    for vehicle_id, raw_vehicle in vehicles.items():
        if not isinstance(raw_vehicle, Mapping):
            continue
        traffic = raw_vehicle.get("traffic", {})
        time_loss = (
            float(traffic.get("time_loss_s", 0.0))
            if isinstance(traffic, Mapping)
            else 0.0
        )
        next_intersection = _next_intersection(raw_vehicle)
        previous = _vehicle_reward_state.get(str(vehicle_id))
        if previous is not None:
            previous_time_loss, previous_intersection = previous
            target = (
                previous_intersection
                if previous_intersection in controlled
                else next_intersection
            )
            if target in controlled:
                delays[target] += max(time_loss - previous_time_loss, 0.0)
            if (
                previous_intersection in controlled
                and next_intersection != previous_intersection
            ):
                crossings[previous_intersection] += 1
        current[str(vehicle_id)] = (time_loss, next_intersection)

    for vehicle_id, (_, previous_intersection) in _vehicle_reward_state.items():
        if vehicle_id not in current and previous_intersection in controlled:
            crossings[previous_intersection] += 1
    _vehicle_reward_state = current
    return delays, crossings


def _incoming_waiting(intersection_id: str, intersection: Mapping[str, Any]) -> float:
    assert _state_builder is not None
    lanes = intersection.get("lanes", {})
    return sum(
        float(lanes.get(lane_id, {}).get("waiting_time", 0.0))
        for lane_id in _state_builder.get_incoming_lanes(intersection_id)
    )


def _accumulate_v5a_reward(
    intersection_id: str,
    intersection: Mapping[str, Any],
    *,
    elapsed_seconds: float,
    delay_increment: float,
    crossings: int,
) -> None:
    pending = _pending_transitions.get(intersection_id)
    if pending is None or elapsed_seconds <= 0.0 or _state_builder is None:
        return

    lanes = intersection.get("lanes", {})
    incoming_lanes = _state_builder.get_incoming_lanes(intersection_id)
    incoming_halt = 0.0
    queue_ratio = 0.0
    for lane_id in incoming_lanes:
        halting = float(lanes.get(lane_id, {}).get("halting_count", 0.0))
        incoming_halt += halting
        queue_ratio = max(
            queue_ratio,
            halting / _state_builder.get_lane_capacity(intersection_id, lane_id),
        )

    outgoing_lanes = _state_builder.get_outgoing_lanes(intersection_id)
    spillbacks = []
    for lane_id in outgoing_lanes:
        occupancy = float(lanes.get(lane_id, {}).get("occupancy", 0.0))
        spillbacks.append(_spillback_penalty(occupancy))
        pending["outgoing_occupancy_seconds"][lane_id] = (
            pending["outgoing_occupancy_seconds"].get(lane_id, 0.0)
            + float(np.clip(occupancy / MAX_OCCUPANCY, 0.0, 1.0))
            * elapsed_seconds
        )
    current_spillback = max(spillbacks, default=0.0)
    pending["delay_increment"] += max(float(delay_increment), 0.0)
    pending["stopped_vehicle_seconds"] += incoming_halt * elapsed_seconds
    pending["queue_ratio_seconds"] += queue_ratio * elapsed_seconds
    pending["crossings"] += max(int(crossings), 0)
    pending["safe_crossings"] += max(int(crossings), 0) * (
        1.0 - current_spillback
    )
    pending["blocked_crossings"] += max(int(crossings), 0) * current_spillback
    pending["observed_seconds"] += elapsed_seconds
    pending["observations"] += 1
    pending["waiting_end"] = _incoming_waiting(intersection_id, intersection)


def _finalize_v5a_reward(intersection_id: str, transition: dict) -> None:
    assert _state_builder is not None
    duration = max(float(transition.get("observed_seconds", 0.0)), 1e-6)
    capacity = max(_state_builder.get_incoming_capacity(intersection_id), 1.0)
    delay = float(np.clip(transition["delay_increment"] / (duration * capacity), 0.0, 1.5))
    stopped = float(
        np.clip(transition["stopped_vehicle_seconds"] / (duration * capacity), 0.0, 1.5)
    )
    queue = float(np.clip(transition["queue_ratio_seconds"] / duration, 0.0, 1.5))
    congestion = 0.45 * delay + 0.40 * stopped + 0.15 * queue

    average_outgoing = [
        value / duration
        for value in transition["outgoing_occupancy_seconds"].values()
    ]
    maximum_spillback = max(
        (_spillback_penalty(value * MAX_OCCUPANCY) for value in average_outgoing),
        default=0.0,
    )
    crossings = float(transition["crossings"])
    flow_weighted_spillback = (
        float(transition["blocked_crossings"]) / crossings if crossings > 0.0 else 0.0
    )
    spillback = 0.70 * maximum_spillback + 0.30 * flow_weighted_spillback

    flow_reference = max(
        _state_builder.get_flow_reference_rate(intersection_id) * duration, 1.0
    )
    safe_flow = float(np.clip(transition["safe_crossings"] / flow_reference, 0.0, 1.0))
    waiting_reference = max(MAX_WAITING * capacity, 1.0)
    waiting_gain = float(
        np.clip(
            (float(transition["waiting_start"]) - float(transition["waiting_end"]))
            / waiting_reference,
            -1.0,
            1.0,
        )
    )
    raw_reward = -0.60 * congestion + 0.20 * safe_flow - 0.15 * spillback + 0.05 * waiting_gain
    transition["reward_components"] = {
        "D": congestion,
        "L": delay,
        "S": stopped,
        "Qmax": queue,
        "F_safe": safe_flow,
        "B": spillback,
        "H": waiting_gain,
    }
    transition["raw_reward"] = float(raw_reward)
    transition["raw_reward_parts"] = [float(raw_reward)]
    transition["reward"] = _normalize_reward(raw_reward)


def _predict_value(state: np.ndarray) -> float:
    if _model is None:
        return 0.0
    with torch.no_grad():
        tensor = torch.from_numpy(state).unsqueeze(0).float()
        return float(_model.critic_forward(tensor).squeeze().item())


def _complete_pending(
    intersection_id: str, next_state: np.ndarray, *, bootstrap: bool
) -> bool:
    transition = _pending_transitions.pop(intersection_id, None)
    if transition is None:
        return False
    _finalize_v5a_reward(intersection_id, transition)
    transition["done"] = not bootstrap
    transition["next_value"] = _predict_value(next_state) if bootstrap else 0.0
    _episode_trajectories[intersection_id].append(transition)
    return True


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


def signal_execution_diagnostics() -> dict:
    """Return aggregate command/execution and phase-dominance diagnostics."""

    commands = sum(item["commands"] for item in _signal_execution_stats.values())
    requested = sum(
        item["change_requests"] for item in _signal_execution_stats.values()
    )
    observed = sum(
        item["observed_changes"] for item in _signal_execution_stats.values()
    )
    unresolved = sum(
        item["unresolved_changes"] for item in _signal_execution_stats.values()
    ) + len(_pending_signal_commands)
    delay_total = sum(
        item["change_delay_total_s"] for item in _signal_execution_stats.values()
    )
    multi_phase = [
        item
        for item in _signal_execution_stats.values()
        if int(item["valid_phase_count"]) > 1
    ]
    dominance = [
        max(item["phase_commands"].values()) / item["commands"]
        for item in multi_phase
        if item["commands"] and item["phase_commands"]
    ]
    return {
        "source": "inferred_from_phase_observations",
        "commands": int(commands),
        "change_requests": int(requested),
        "observed_changes": int(observed),
        "unresolved_changes": int(unresolved),
        "change_execution_rate": float(observed / requested) if requested else 1.0,
        "mean_change_delay_s": float(delay_total / observed) if observed else 0.0,
        "max_change_delay_s": max(
            (
                float(item["change_delay_max_s"])
                for item in _signal_execution_stats.values()
            ),
            default=0.0,
        ),
        "max_green_forced_commands": int(
            sum(
                item["max_green_forced_commands"]
                for item in _signal_execution_stats.values()
            )
        ),
        "multi_phase_max_observed_green_s": max(
            (float(item["max_observed_green_s"]) for item in multi_phase),
            default=0.0,
        ),
        "multi_phase_mean_phase_dominance": (
            float(np.mean(dominance)) if dominance else 0.0
        ),
        "multi_phase_max_phase_dominance": max(dominance, default=0.0),
        "per_intersection": copy.deepcopy(_signal_execution_stats),
    }


def _choose_action(
    state: np.ndarray, phase_features: np.ndarray, action_mask: np.ndarray
) -> Tuple[int, float, float]:
    valid_actions = np.flatnonzero(action_mask)
    if valid_actions.size == 0:
        raise RuntimeError("IPPO action mask contains no valid phase.")
    if _inference_mode == "random":
        return int(np.random.choice(valid_actions)), 0.0, 0.0
    if _inference_mode == "fixed":
        return int(valid_actions[0]), 0.0, 0.0
    if _model is None:
        raise RuntimeError("IPPO model is not initialized.")
    with torch.no_grad():
        tensor = torch.from_numpy(state).unsqueeze(0).float()
        phase_tensor = torch.from_numpy(phase_features).unsqueeze(0).float()
        logits, value = _model(tensor, phase_tensor)
        distribution = _masked_categorical(
            logits, torch.from_numpy(action_mask).unsqueeze(0)
        )
        if _inference_mode == "model":
            action = torch.argmax(distribution.probs, dim=-1)
        else:
            action = distribution.sample()
        return (
            int(action.item()),
            float(distribution.log_prob(action).item()),
            float(value.squeeze().item()),
        )


def step(payload: dict) -> dict:
    global _last_reward_time
    if _state_builder is None:
        raise RuntimeError("IPPO is not initialized.")
    decision_started = time.perf_counter()
    simulation_time = float(
        payload.get(
            "simulation_time", float(payload.get("step_id", 0)) * _decision_interval
        )
    )
    observations = payload.get("intersections", {})
    _observe_signal_execution(observations, simulation_time)
    states = _state_builder.get_all_states(payload)
    signal_actions: Dict[str, dict] = {}
    delay_increments, crossing_counts = _vehicle_interval_statistics(payload)
    elapsed_seconds = (
        0.0
        if _last_reward_time is None
        else max(simulation_time - _last_reward_time, 0.0)
    )
    _last_reward_time = simulation_time

    for intersection_id in _intersection_ids:
        intersection = observations.get(intersection_id)
        if not intersection:
            continue
        state = states[intersection_id]
        _latest_states[intersection_id] = state

        total_waiting = _incoming_waiting(intersection_id, intersection)
        if _inference_mode in {"train", "collect"}:
            _accumulate_v5a_reward(
                intersection_id,
                intersection,
                elapsed_seconds=elapsed_seconds,
                delay_increment=delay_increments.get(intersection_id, 0.0),
                crossings=crossing_counts.get(intersection_id, 0),
            )

        if not _eligible_for_decision(
            intersection, simulation_time, _last_decision_times[intersection_id]
        ):
            continue

        if _inference_mode in {"train", "collect"}:
            _complete_pending(intersection_id, state, bootstrap=True)

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
        action_index, log_probability, value = _choose_action(
            state, phase_features, action_mask
        )
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

        if _inference_mode in {"train", "collect"}:
            _pending_transitions[intersection_id] = {
                "obs": state.copy(),
                "phase_features": phase_features.copy(),
                "action_mask": action_mask.copy(),
                "action": action_index,
                "reward": 0.0,
                "raw_reward": 0.0,
                "raw_reward_parts": [],
                "log_prob": log_probability,
                "value": value,
                "valid_action_count": len(phase_order),
                "observations": 0,
                "observed_seconds": 0.0,
                "delay_increment": 0.0,
                "stopped_vehicle_seconds": 0.0,
                "queue_ratio_seconds": 0.0,
                "outgoing_occupancy_seconds": {},
                "crossings": 0,
                "safe_crossings": 0.0,
                "blocked_crossings": 0.0,
                "waiting_start": total_waiting,
                "waiting_end": total_waiting,
            }

    response = {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signal_actions, "vehicles": {}},
    }
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_runtime.record_latency(
        (time.perf_counter() - decision_started) * 1000.0,
        episode_id=str(payload["episode_id"]),
    )
    evaluation_runtime.observe_decision(payload)
    return response


def _clear_episode_rollout() -> None:
    global _last_reward_time
    _episode_trajectories.clear()
    _pending_transitions.clear()
    _latest_states.clear()
    _vehicle_reward_state.clear()
    _last_reward_time = None


def finish(payload: dict) -> None:
    global _episode, _collected_rollout
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_payload = dict(payload)
    evaluation_payload.setdefault("episode_id", _evaluation_episode_id)
    evaluation_runtime.finish(evaluation_payload)
    if _inference_mode not in {"train", "collect"} or _model is None:
        return

    reason = str(payload.get("reason", "error")).lower()
    if reason != "completed":
        dropped = len(_pending_transitions) + sum(
            len(trajectory) for trajectory in _episode_trajectories.values()
        )
        logger.warning("IPPO rollout discarded: reason=%s samples=%d", reason, dropped)
        _collected_rollout = None
        _clear_episode_rollout()
        return

    pending_dropped = 0
    for intersection_id in tuple(_pending_transitions):
        latest_state = _latest_states.get(intersection_id)
        pending = _pending_transitions[intersection_id]
        if latest_state is None or int(pending.get("observations", 0)) == 0:
            _pending_transitions.pop(intersection_id, None)
            pending_dropped += 1
            continue
        _complete_pending(intersection_id, latest_state, bootstrap=True)

    accepted = {
        intersection_id: list(trajectory)
        for intersection_id, trajectory in _episode_trajectories.items()
        if trajectory
    }
    sample_count = sum(len(trajectory) for trajectory in accepted.values())
    if sample_count == 0:
        logger.warning("IPPO rollout ignored: no executed policy decisions")
        _collected_rollout = None
        _clear_episode_rollout()
        return

    if _inference_mode == "collect":
        if _collector_metadata is None:
            raise RuntimeError("Collector metadata is unavailable.")
        _collected_rollout = {
            "trajectories": copy.deepcopy(accepted),
            "sample_count": sample_count,
            "pending_dropped": pending_dropped,
            "metadata": copy.deepcopy(_collector_metadata),
            "policy_state": export_policy_state(),
            "signal_execution": signal_execution_diagnostics(),
        }
        logger.info(
            "并行 rollout 完成: samples=%d pending_dropped=%d reward=%.3f "
            "switch=%d/%d forced=%d dominance=%.3f",
            sample_count,
            pending_dropped,
            float(
                np.mean(
                    [
                        item["raw_reward"]
                        for trajectory in accepted.values()
                        for item in trajectory
                    ]
                )
            ),
            _collected_rollout["signal_execution"]["observed_changes"],
            _collected_rollout["signal_execution"]["change_requests"],
            _collected_rollout["signal_execution"]["max_green_forced_commands"],
            _collected_rollout["signal_execution"][
                "multi_phase_mean_phase_dominance"
            ],
        )
        _clear_episode_rollout()
        return

    _buffer_episodes.append(accepted)
    _episode += 1
    raw_rewards = [
        transition["raw_reward"]
        for trajectory in accepted.values()
        for transition in trajectory
    ]
    component_means = {
        name: float(
            np.mean(
                [
                    item["reward_components"][name]
                    for trajectory in accepted.values()
                    for item in trajectory
                ]
            )
        )
        for name in REWARD_COMPONENT_NAMES
    }
    logger.info(
        "EP%d 完成: departed=%d arrived=%d samples=%d pending_dropped=%d "
        "reward=%.3f D=%.3f F=%.3f B=%.3f H=%.3f",
        _episode,
        int(payload.get("departed_vehicles", 0)),
        int(payload.get("arrived_vehicles", 0)),
        sample_count,
        pending_dropped,
        float(np.mean(raw_rewards)) if raw_rewards else 0.0,
        component_means["D"],
        component_means["F_safe"],
        component_means["B"],
        component_means["H"],
    )

    if len(_buffer_episodes) >= ACCUMULATE_EPISODES:
        _ppo_update()

    if _episode % CHECKPOINT_INTERVAL == 0:
        checkpoint_dir = Path(
            os.environ.get(
                "IPPO_CHECKPOINT_DIR", str(Path(__file__).resolve().parent / "checkpoints")
            )
        )
        save_checkpoint(checkpoint_dir / f"ippo_{MODEL_VERSION}_ep{_episode}.pt")
    _clear_episode_rollout()


def evaluation_result() -> Optional[Dict[str, Any]]:
    """Return the latest six-metric result after ``finish``."""

    from algorithms.evaluation import runtime as evaluation_runtime

    result = evaluation_runtime.last_result()
    return None if result is None else result.to_dict()


def take_collected_rollout() -> dict:
    """Return exactly one completed rollout from a collector worker."""

    global _collected_rollout
    if _collected_rollout is None:
        raise RuntimeError("No completed collector rollout is available.")
    result = _collected_rollout
    _collected_rollout = None
    return result


def ingest_parallel_rollouts(
    rollouts: Sequence[Mapping[str, Any]], *, update: bool = True
) -> dict:
    """Update one synchronous batch collected by one policy generation."""

    global _episode
    if _inference_mode != "train" or _model is None:
        raise RuntimeError("Parallel rollouts require an initialized training learner.")
    if not rollouts:
        raise ValueError("At least one parallel rollout is required.")
    if _buffer_episodes:
        raise RuntimeError("Stale rollout buffer must be empty before a parallel batch.")

    total_samples = 0
    for rollout in rollouts:
        source = rollout.get("trajectories")
        if not isinstance(source, Mapping) or not source:
            raise ValueError("Parallel rollout has no trajectories.")
        unknown = set(source) - set(_intersection_ids)
        if unknown:
            raise ValueError(f"Parallel rollout contains unknown intersections: {sorted(unknown)}")
        accepted: Dict[str, List[dict]] = {}
        for intersection_id, trajectory in source.items():
            if not trajectory:
                continue
            normalized: List[dict] = []
            for item in trajectory:
                transition = copy.deepcopy(item)
                transition["reward"] = _normalize_reward(
                    float(transition.get("raw_reward", 0.0))
                )
                normalized.append(transition)
            accepted[str(intersection_id)] = normalized
            total_samples += len(normalized)
        if not accepted:
            raise ValueError("Parallel rollout has no valid policy samples.")
        _buffer_episodes.append(accepted)
        _episode += 1

    if update:
        _ppo_update(episode_count=len(rollouts))
    return {"episodes": len(rollouts), "samples": total_samples}


def _compute_gae(trajectory: Sequence[Mapping[str, Any]]) -> Tuple[np.ndarray, np.ndarray]:
    advantages = np.zeros(len(trajectory), dtype=np.float32)
    returns = np.zeros(len(trajectory), dtype=np.float32)
    gae = 0.0
    for index in reversed(range(len(trajectory))):
        transition = trajectory[index]
        nonterminal = 0.0 if bool(transition.get("done", False)) else 1.0
        value = float(transition["value"])
        next_value = float(transition.get("next_value", 0.0))
        delta = float(transition["reward"]) + GAMMA * next_value * nonterminal - value
        gae = delta + GAMMA * GAE_LAMBDA * nonterminal * gae
        advantages[index] = gae
        returns[index] = gae + value
    return advantages, returns


def _ppo_update(episode_count: Optional[int] = None) -> None:
    if _model is None or _optimizer_actor is None or _optimizer_critic is None:
        raise RuntimeError("IPPO optimizers are not initialized.")
    if episode_count is None:
        episode_count = min(ACCUMULATE_EPISODES, len(_buffer_episodes))
    if episode_count <= 0 or episode_count > len(_buffer_episodes):
        raise ValueError("episode_count must select at least one buffered rollout.")
    episodes = _buffer_episodes[:episode_count]
    rows: List[Tuple[dict, float, float]] = []
    for episode in episodes:
        for trajectory in episode.values():
            advantages, returns = _compute_gae(trajectory)
            rows.extend(
                (transition, float(advantages[index]), float(returns[index]))
                for index, transition in enumerate(trajectory)
            )
    if not rows:
        del _buffer_episodes[: len(episodes)]
        return

    observations = np.stack([row[0]["obs"] for row in rows]).astype(np.float32)
    phase_features = np.stack(
        [row[0]["phase_features"] for row in rows]
    ).astype(np.float32)
    candidate_counts = np.asarray(
        [row[0]["valid_action_count"] for row in rows], dtype=np.int64
    )
    candidate_mask = (
        np.arange(phase_features.shape[1])[None, :] < candidate_counts[:, None]
    )
    near_demands = phase_features[:, :, PHASE_NEAR_DEMAND_INDEX][candidate_mask]
    far_demands = phase_features[:, :, PHASE_FAR_DEMAND_INDEX][candidate_mask]
    action_masks = np.stack([row[0]["action_mask"] for row in rows]).astype(np.bool_)
    actions = np.asarray([row[0]["action"] for row in rows], dtype=np.int64)
    raw_rewards = np.asarray([row[0]["raw_reward"] for row in rows], dtype=np.float32)
    component_means = {
        name: float(
            np.mean(
                [
                    float(row[0].get("reward_components", {}).get(name, 0.0))
                    for row in rows
                ]
            )
        )
        for name in REWARD_COMPONENT_NAMES
    }
    old_log_probabilities = np.asarray(
        [row[0]["log_prob"] for row in rows], dtype=np.float32
    )
    old_values = np.asarray([row[0]["value"] for row in rows], dtype=np.float32)
    advantages = np.asarray([row[1] for row in rows], dtype=np.float32)
    returns = np.asarray([row[2] for row in rows], dtype=np.float32)
    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

    obs_tensor = torch.from_numpy(observations)
    phase_feature_tensor = torch.from_numpy(phase_features)
    action_mask_tensor = torch.from_numpy(action_masks)
    action_tensor = torch.from_numpy(actions)
    advantage_tensor = torch.from_numpy(advantages)
    return_tensor = torch.from_numpy(returns)
    old_log_prob_tensor = torch.from_numpy(old_log_probabilities)

    actor_parameters = tuple(_model.actor_parameters())
    critic_parameters = tuple(_model.critic_parameters())
    totals = {
        "actor": 0.0,
        "critic": 0.0,
        "entropy": 0.0,
        "kl": 0.0,
        "clip": 0.0,
        "actor_grad": 0.0,
        "critic_grad": 0.0,
    }
    update_count = 0
    total_samples = len(rows)
    for _ in range(PPO_EPOCHS):
        permutation = torch.randperm(total_samples)
        for start in range(0, total_samples, BATCH_SIZE):
            indices = permutation[start : start + BATCH_SIZE]
            if indices.numel() == 0:
                continue
            update_count += 1

            logits = _model.actor_forward(
                obs_tensor[indices], phase_feature_tensor[indices]
            )
            distribution = _masked_categorical(logits, action_mask_tensor[indices])
            new_log_probability = distribution.log_prob(action_tensor[indices])
            entropy = distribution.entropy().mean()
            log_ratio = new_log_probability - old_log_prob_tensor[indices]
            ratio = log_ratio.exp()
            unclipped = ratio * advantage_tensor[indices]
            clipped = torch.clamp(ratio, 1.0 - CLIP_EPS, 1.0 + CLIP_EPS) * advantage_tensor[
                indices
            ]
            actor_loss = -torch.min(unclipped, clipped).mean()
            actor_objective = actor_loss - ENTROPY_COEF * entropy

            _optimizer_actor.zero_grad()
            actor_objective.backward()
            actor_grad = nn.utils.clip_grad_norm_(actor_parameters, MAX_GRAD_NORM)
            _optimizer_actor.step()

            predicted_values = _model.critic_forward(obs_tensor[indices]).squeeze(-1)
            critic_loss = F.huber_loss(
                predicted_values, return_tensor[indices], delta=HUBER_DELTA
            )
            _optimizer_critic.zero_grad()
            critic_loss.backward()
            critic_grad = nn.utils.clip_grad_norm_(critic_parameters, MAX_GRAD_NORM)
            _optimizer_critic.step()

            with torch.no_grad():
                approximate_kl = ((ratio - 1.0) - log_ratio).mean()
                clip_fraction = ((ratio - 1.0).abs() > CLIP_EPS).float().mean()
            totals["actor"] += float(actor_loss.detach())
            totals["critic"] += float(critic_loss.detach())
            totals["entropy"] += float(entropy.detach())
            totals["kl"] += float(approximate_kl)
            totals["clip"] += float(clip_fraction)
            totals["actor_grad"] += float(actor_grad)
            totals["critic_grad"] += float(critic_grad)

    del _buffer_episodes[: len(episodes)]
    residual_variance = float(np.var(returns - old_values))
    return_variance = float(np.var(returns))
    explained_variance = (
        float("nan") if return_variance == 0.0 else 1.0 - residual_variance / return_variance
    )
    divisor = max(update_count, 1)
    logger.info(
        "PPO 更新: samples=%d rew_raw=%.3f±%.3f D=%.3f F=%.3f B=%.3f H=%.3f "
        "ret=%.2f±%.2f "
        "val=%.2f±%.2f ev=%.3f actor=%.4f critic=%.4f ent=%.3f "
        "kl=%.4f clip=%.3f grad_actor=%.3f grad_critic=%.3f "
        "demand_near=%.4f/%.3f/%.3f demand_far=%.4f/%.3f/%.3f",
        total_samples,
        float(raw_rewards.mean()),
        float(raw_rewards.std()),
        component_means["D"],
        component_means["F_safe"],
        component_means["B"],
        component_means["H"],
        float(returns.mean()),
        float(returns.std()),
        float(old_values.mean()),
        float(old_values.std()),
        explained_variance,
        totals["actor"] / divisor,
        totals["critic"] / divisor,
        totals["entropy"] / divisor,
        totals["kl"] / divisor,
        totals["clip"] / divisor,
        totals["actor_grad"] / divisor,
        totals["critic_grad"] / divisor,
        float(near_demands.mean()),
        float(np.count_nonzero(near_demands) / len(near_demands)),
        float(near_demands.max()),
        float(far_demands.mean()),
        float(np.count_nonzero(far_demands) / len(far_demands)),
        float(far_demands.max()),
    )


def save_checkpoint(path: str | os.PathLike[str]) -> Path:
    if _model is None:
        raise RuntimeError("Cannot save an uninitialized IPPO model.")
    if _collector_metadata is None or not _collector_metadata.get("intersections"):
        raise RuntimeError(
            "Cannot save a checkpoint without collector metadata; "
            "initialize() must receive the protocol metadata first."
        )
    builder = StateBuilder(_collector_metadata)
    fingerprints = {
        iid: builder.get_fingerprint(iid) for iid in builder.intersection_ids
    }
    destination = Path(path)
    if destination.suffix != ".pt":
        destination = destination.with_suffix(".pt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "model_version": MODEL_VERSION,
        "intersection_ids": list(_model_intersection_ids),
        "obs_dim": _model.obs_dim,
        "act_dim": _model.act_dim,
        "episode": _episode,
        "action_interval": _action_interval,
        "max_green_factor": _max_green_factor,
        "phase_feature_schema": PHASE_FEATURE_SCHEMA,
        "effective_demand_enabled": _effective_demand_enabled,
        "checkpoint_contract_version": CHECKPOINT_CONTRACT_VERSION,
        "identity_slots": list(IDENTITY_SLOT_IDS),
        "observation_schema": dict(OBSERVATION_SCHEMA),
        "phase_features": int(PHASE_FEATURES),
        "normalization": dict(NORMALIZATION),
        "collab_message_schema": COLLAB_MESSAGE_SCHEMA,
        "per_intersection_fingerprints": {
            iid: {"sha256": fingerprint_sha256(fp), "fingerprint": fp}
            for iid, fp in fingerprints.items()
        },
        "model_state_dict": _model.state_dict(),
        "optimizer_actor_state_dict": (
            _optimizer_actor.state_dict() if _optimizer_actor is not None else None
        ),
        "optimizer_critic_state_dict": (
            _optimizer_critic.state_dict() if _optimizer_critic is not None else None
        ),
        "reward_definition": {
            "name": "v5a_local_pressure",
            "formula": "-0.60D + 0.20F_safe - 0.15B + 0.05H",
            "normalization": "physical_fixed",
            "occupancy_input_unit": "percent",
        },
    }
    seed_start = os.environ.get("IPPO_TRAIN_SEED_START")
    seed_end = os.environ.get("IPPO_TRAIN_SEED_END")
    if seed_start is not None and seed_end is not None:
        checkpoint["training_seed_range"] = {
            "start": int(seed_start),
            "end": int(seed_end),
        }
    training_periods = os.environ.get("IPPO_TRAIN_PERIODS")
    if training_periods:
        checkpoint["training_periods"] = [
            value for value in training_periods.split(",") if value
        ]
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        torch.save(checkpoint, temporary)
        temporary.replace(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    logger.info("Checkpoint: %s", destination)
    return destination
