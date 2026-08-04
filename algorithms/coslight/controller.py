"""CoSLight signal controller for Protocol 2.0.

The controller keeps complete multi-intersection timesteps intact during PPO,
uses a separate learned Top-K collaborator policy for V17 while preserving
legacy checkpoint inference, masks nonexistent phases, and assigns each
observed reward to the action that produced it.
"""

from __future__ import annotations

import copy
import glob
import logging
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

try:  # Package import used by ``algorithm_module=algorithms.coslight``.
    from .lane_state import build_static_indices, reset_lane_state
    from .cloud import RegionalCloudCoordinator
except ImportError:  # Direct ``algorithm_module=controller`` compatibility.
    from lane_state import build_static_indices, reset_lane_state
    from cloud import RegionalCloudCoordinator


logger = logging.getLogger(__name__)

# Observation layout: phase one-hot + stage elapsed + fixed lane slots.
MAX_PHASES = 8
# The current 20-intersection production topology peaks at 32 observed lanes
# (demo_8: 19 incoming + 13 outgoing).  Refuse larger topologies explicitly
# instead of silently hiding reward-relevant lanes from the policy.
MAX_LANES = 32
LANE_FEATURES = 6
OBS_DIM = MAX_PHASES + 1 + MAX_LANES * LANE_FEATURES
PHASE_TRAFFIC_FEATURES = 6
PHASE_FEATURES = 8
# Parameters added by the V17 action-dependent collaborator bias.  Older
# checkpoints (including the V16 warm-start source) predate them; model loads
# tolerate exactly this missing set so warm-start and deployed V16 inference
# remain reproducible.
NEW_COLLAB_BIAS_PARAMS = frozenset(
    {
        "collab_bias.0.weight",
        "collab_bias.0.bias",
        "collab_bias.2.weight",
        "collab_bias.2.bias",
    }
)
PHASE_FEATURE_SCHEMA = "movement_pressure_v3"

TRANS_HIDDEN = 64
TRANS_HEADS = 4
TRANS_LAYERS = 2
DEFAULT_TOP_K = 5
# Protocol callbacks stay at 5 seconds so vehicle guidance remains responsive,
# but signal PPO transitions use a fixed horizon.  This interval is longer than
# the production yellow + all-red transition (3 + 2 seconds) plus minimum green.
JOINT_DECISION_INTERVAL = 15.0

# PPO.
LR = 3e-4
ACTOR_LR = LR
CRITIC_LR = LR
# The direct physical pressure scorer receives much weaker gradients than the
# residual policy head. V13 verified that a dedicated multiplier makes this
# path move without destabilizing the rest of the actor.
# V13/V15 used 10x only to prove that updates reached the physical action
# scorer.  Held-out 300/600s results showed no traffic benefit and a persistent
# throughput loss, so the performance experiment returns to the base actor LR.
PHASE_SCORER_LR_MULTIPLIER = 1.0
CLIP_EPS = 0.2
GAMMA = 0.99
GAE_LAMBDA = 0.95
ENTROPY_COEF = 0.01
VALUE_COEF = 0.5
PPO_EPOCHS = 4
BATCH_SIZE = 32  # Joint timesteps, not flattened agents.
ACCUMULATE_EPISODES = 4
MAX_GRAD_NORM = 0.5
HUBER_DELTA = 5.0
REWARD_CLIP = 10.0
CHECKPOINT_FORMAT_VERSION = 17
POLICY_OBJECTIVE = "independent_phase_and_collaborator_ratios_v1"
POLICY_ARCHITECTURE = "movement_pressure_local_learned_topk_residual_v5"
COLLABORATION_SCHEMA = "all_candidate_plackett_luce_topk_v2"
ACTOR_ENCODER_SCOPE = "local_then_selected_topk_v2"
COLLABORATOR_POLICY_COEF = 1.0
COLLABORATOR_ENTROPY_COEF = 0.01
COLLABORATOR_DIAGONAL_COEF = 0.01
COLLABORATOR_SYMMETRY_COEF = 0.01

CHECKPOINT_INTERVAL = 4
MAX_CHECKPOINTS = 5

# Reward.
ETA = 0.3
# Spillback is a Stage-2 cloud-coordination concern. Keep the term computed for
# diagnostics, but do not let it change the Stage-1 local signal reward.
LAMBDA_SPILL = 0.0
LAMBDA_SWITCH = 0.05
REWARD_SCHEMA = "stage1_local_pressure_neighbor_switch_v1"
TERMINAL_TRANSITION_SCHEMA = "fixed_horizon_guard_bootstrap_v1"
SPILL_THRESHOLD = 0.70
PRESSURE_PRIOR_SCALE = 1.0
# Stage-2 uses a bounded, non-negative direction bonus around the neutral
# cloud value 1.0.  Multiplying a signed movement pressure made a preferred
# action *less* attractive whenever its pressure was negative and often
# preserved the original action ordering.  The additive form is neutral at
# 1.0 and contributes at most ``max_weight - 1`` to an enabled action.
CLOUD_DIRECTION_PRIOR_SCHEMA = (
    "bounded_additive_direction_bonus_positive_pressure_v2"
)
# Runtime-only experiment knob. ``inf`` preserves V15 behavior; finite values
# keep PPO inside a capacity-normalized pressure band around MaxPressure.
DEFAULT_PRESSURE_SHIELD_MARGIN = math.inf
# Runtime-only experiment knob.  The learned residual is trusted only when the
# strongest legal served-movement pressure is above this floor.  ``-inf`` keeps
# V15 behavior; finite values fall back to the same-state MaxPressure action in
# weak/negative-signal states.
DEFAULT_RESIDUAL_MIN_BEST_PRESSURE = -math.inf
# Deterministic-evaluation-only switch hysteresis.  ``-inf`` records the
# selected-vs-current policy confidence without changing V16 actions.  A
# finite non-negative margin keeps the current legal phase when the selected
# phase's logit advantage is no larger than the margin.
DEFAULT_SWITCH_LOGIT_MARGIN = -math.inf
# Break exact pressure ties in favour of the current phase without creating a
# material hysteresis band around near-ties.
HOLD_PRIOR_BIAS = 1e-6
VEHICLE_LENGTH = 5.0
DEFAULT_GREEN_DURATION = 30.0
DEFAULT_MAX_GREEN_FACTOR = 2.0
MIN_MAX_GREEN_SECONDS = 60.0
REWARD_MODES = ("pressure", "legacy_delta")
BRAKING_ATTRIBUTION_WINDOWS_S = (5.0, 10.0, 15.0, 30.0)
BRAKING_DISTANCE_THRESHOLDS_M = (50.0, 100.0, 200.0)


@dataclass
class _IntersectionIndex:
    phase_order: List[int]
    phase_index: Dict[int, int]
    incoming_lanes: List[str]
    outgoing_lanes: List[str]
    lane_order: List[str]
    lane_capacity: Dict[str, float]
    lane_length: Dict[str, float]
    lane_speed: Dict[str, float]
    lane_edges: Dict[str, str]
    phase_connections: List[Tuple[Tuple[str, str], ...]]
    # Protected (``G``) movements only.  Stage-1 observations deliberately use
    # ``phase_connections`` above, which also contains permissive (``g``)
    # movements.  The Stage-2 cloud direction prior must not treat an
    # incidental permissive turn as the main direction served by a phase.
    phase_primary_connections: List[Tuple[Tuple[str, str], ...]]
    phase_durations: List[float]


class StateBuilder:
    """Build stable, normalized per-intersection observations."""

    def __init__(self, metadata: Dict[str, Any], tls_order: Sequence[str]) -> None:
        self.phase_feature_schema = PHASE_FEATURE_SCHEMA
        self.indices: Dict[str, _IntersectionIndex] = {}
        for tid in tls_order:
            meta = metadata.get("intersections", {}).get(tid, {})
            phase_order = [int(value) for value in meta.get("phase_order", [])]
            if not phase_order:
                raise ValueError(f"CoSLight intersection {tid!r} has no controllable phases")
            if len(phase_order) > MAX_PHASES:
                raise ValueError(
                    f"CoSLight intersection {tid!r} has {len(phase_order)} phases; "
                    f"maximum is {MAX_PHASES}"
                )

            incoming = [str(value) for value in meta.get("incoming_lanes", [])]
            outgoing = [str(value) for value in meta.get("outgoing_lanes", [])]
            lane_order = incoming + [lane for lane in outgoing if lane not in incoming]
            if len(lane_order) > MAX_LANES:
                raise ValueError(
                    f"CoSLight intersection {tid!r} has {len(lane_order)} lanes; "
                    f"maximum is {MAX_LANES}"
                )
            lanes_meta = meta.get("lanes", {})
            capacity: Dict[str, float] = {}
            length: Dict[str, float] = {}
            speed: Dict[str, float] = {}
            lane_edges: Dict[str, str] = {}
            for lane_id in lane_order:
                lane_meta = lanes_meta.get(lane_id, {})
                lane_length = float(
                    lane_meta.get("length_m", lane_meta.get("length", 50.0))
                )
                lane_speed = float(
                    lane_meta.get("speed_limit_mps", lane_meta.get("max_speed", 13.9))
                )
                length[lane_id] = max(lane_length, 1.0)
                speed[lane_id] = max(lane_speed, 0.1)
                capacity[lane_id] = max(lane_length / VEHICLE_LENGTH, 1.0)
                lane_edges[lane_id] = str(
                    lane_meta.get("edge_id") or lane_id.rsplit("_", 1)[0]
                )

            connections_by_id = {
                str(connection.get("connection_id")): connection
                for connection in meta.get("connections", [])
                if connection.get("connection_id")
            }
            phases_meta = meta.get("phases", {})
            phase_connections: List[Tuple[Tuple[str, str], ...]] = []
            phase_primary_connections: List[Tuple[Tuple[str, str], ...]] = []
            phase_durations: List[float] = []
            for phase_id in phase_order:
                phase = phases_meta.get(
                    str(phase_id), phases_meta.get(phase_id, {})
                )
                phase_durations.append(
                    max(
                        float(
                            phase.get(
                                "green_seconds", DEFAULT_GREEN_DURATION
                            )
                        ),
                        1.0,
                    )
                )
                priorities = phase.get("connection_priorities", {})
                served_pairs = set()
                primary_pairs = set()
                for raw_connection_id, raw_priority in priorities.items():
                    connection = connections_by_id.get(str(raw_connection_id))
                    if (
                        connection
                        and connection.get("from_lane")
                        and connection.get("to_lane")
                    ):
                        pair = (
                            str(connection["from_lane"]),
                            str(connection["to_lane"]),
                        )
                        served_pairs.add(pair)
                        if str(raw_priority).strip().lower() == "protected":
                            primary_pairs.add(pair)
                phase_connections.append(tuple(sorted(served_pairs)))
                phase_primary_connections.append(tuple(sorted(primary_pairs)))

            self.indices[tid] = _IntersectionIndex(
                phase_order=phase_order,
                phase_index={phase: index for index, phase in enumerate(phase_order)},
                incoming_lanes=incoming,
                outgoing_lanes=outgoing,
                lane_order=lane_order,
                lane_capacity=capacity,
                lane_length=length,
                lane_speed=speed,
                lane_edges=lane_edges,
                phase_connections=phase_connections,
                phase_primary_connections=phase_primary_connections,
                phase_durations=phase_durations,
            )

    def build_all(self, intersections: Dict[str, Any], tls_order: Sequence[str]) -> np.ndarray:
        observations = np.zeros((len(tls_order), OBS_DIM), dtype=np.float32)
        for agent_index, tid in enumerate(tls_order):
            index = self.indices[tid]
            intersection = intersections.get(tid, {})
            if not intersection:
                continue

            current_phase = int(
                intersection.get("current_phase", index.phase_order[0])
            )
            observations[agent_index, index.phase_index.get(current_phase, 0)] = 1.0
            observations[agent_index, MAX_PHASES] = np.clip(
                float(intersection.get("stage_elapsed", 0.0)) / 120.0,
                0.0,
                1.0,
            )

            lanes = intersection.get("lanes", {})
            for lane_slot, lane_id in enumerate(index.lane_order[:MAX_LANES]):
                lane = lanes.get(lane_id, {})
                capacity = index.lane_capacity.get(lane_id, 1.0)
                lane_length = index.lane_length.get(lane_id, 50.0)
                speed_limit = index.lane_speed.get(lane_id, 13.9)
                base = MAX_PHASES + 1 + lane_slot * LANE_FEATURES
                features = (
                    float(lane.get("vehicle_count", 0.0)) / capacity,
                    float(lane.get("halting_count", 0.0)) / capacity,
                    _occupancy_ratio(lane.get("occupancy", 0.0)),
                    float(lane.get("queue_length_m", 0.0)) / lane_length,
                    float(lane.get("mean_speed", 0.0)) / speed_limit,
                    float(lane.get("waiting_time", 0.0)) / max(capacity * 120.0, 1.0),
                )
                observations[agent_index, base : base + LANE_FEATURES] = np.clip(
                    np.asarray(features, dtype=np.float32), 0.0, 1.0
                )
        return observations

    def build_phase_features(
        self,
        intersections: Mapping[str, Any],
        tls_order: Sequence[str],
        action_dim: int,
    ) -> np.ndarray:
        """Build action-aligned demand features from phase connection metadata."""

        features = np.zeros(
            (len(tls_order), action_dim, PHASE_FEATURES), dtype=np.float32
        )
        for agent_index, tid in enumerate(tls_order):
            index = self.indices[tid]
            intersection = intersections.get(tid, {})
            lanes = intersection.get("lanes", {})
            current_phase = int(
                intersection.get("current_phase", index.phase_order[0])
            )
            for phase_index, pairs in enumerate(
                index.phase_connections[:action_dim]
            ):
                features[agent_index, phase_index, 6] = float(
                    index.phase_order[phase_index] == current_phase
                )
                features[agent_index, phase_index, 7] = np.clip(
                    index.phase_durations[phase_index] / 120.0,
                    0.0,
                    1.0,
                )
                incoming_lanes = sorted({pair[0] for pair in pairs})
                outgoing_lanes = sorted({pair[1] for pair in pairs})
                if not incoming_lanes:
                    continue

                incoming_density = [
                    float(lanes.get(lane_id, {}).get("vehicle_count", 0.0))
                    / index.lane_capacity.get(lane_id, 1.0)
                    for lane_id in incoming_lanes
                ]
                incoming_halting = [
                    float(lanes.get(lane_id, {}).get("halting_count", 0.0))
                    / index.lane_capacity.get(lane_id, 1.0)
                    for lane_id in incoming_lanes
                ]
                incoming_queue = [
                    float(lanes.get(lane_id, {}).get("queue_length_m", 0.0))
                    / index.lane_length.get(lane_id, 50.0)
                    for lane_id in incoming_lanes
                ]
                incoming_waiting = [
                    float(lanes.get(lane_id, {}).get("waiting_time", 0.0))
                    / max(
                        index.lane_capacity.get(lane_id, 1.0) * 120.0,
                        1.0,
                    )
                    for lane_id in incoming_lanes
                ]
                outgoing_density = [
                    float(lanes.get(lane_id, {}).get("vehicle_count", 0.0))
                    / index.lane_capacity.get(lane_id, 1.0)
                    for lane_id in outgoing_lanes
                ]

                mean_incoming = float(np.mean(incoming_density))
                mean_outgoing = (
                    float(np.mean(outgoing_density))
                    if outgoing_density
                    else 0.0
                )
                # Preserve the exact served-movement pressure used by the
                # diagnostic MaxPressure controller.  Averaging unique input
                # and output lanes changes phase ordering whenever candidate
                # phases serve different numbers of movements.
                movement_pressure = sum(
                    float(lanes.get(incoming_lane, {}).get("vehicle_count", 0.0))
                    / index.lane_capacity.get(incoming_lane, 1.0)
                    - float(lanes.get(outgoing_lane, {}).get("vehicle_count", 0.0))
                    / index.lane_capacity.get(outgoing_lane, 1.0)
                    for incoming_lane, outgoing_lane in pairs
                )
                pressure_feature = (
                    movement_pressure
                    if self.phase_feature_schema == "movement_pressure_v3"
                    else mean_incoming - mean_outgoing
                )
                features[
                    agent_index, phase_index, :PHASE_TRAFFIC_FEATURES
                ] = np.asarray(
                    (
                        mean_incoming,
                        float(np.mean(incoming_halting)),
                        float(np.mean(incoming_queue)),
                        float(np.mean(incoming_waiting)),
                        mean_outgoing,
                        pressure_feature,
                    ),
                    dtype=np.float32,
                )

        features[..., :5] = np.clip(features[..., :5], 0.0, 1.0)
        return features


class PositionalEncoding(nn.Module):
    def __init__(self, hidden: int, max_agents: int = 256) -> None:
        super().__init__()
        encoding = torch.zeros(max_agents, hidden)
        position = torch.arange(max_agents, dtype=torch.float32).unsqueeze(1)
        divisor = torch.exp(
            torch.arange(0, hidden, 2, dtype=torch.float32)
            * (-math.log(10000.0) / hidden)
        )
        encoding[:, 0::2] = torch.sin(position * divisor)
        encoding[:, 1::2] = torch.cos(position * divisor)
        self.register_buffer("encoding", encoding)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        return value + self.encoding[: value.shape[1]].unsqueeze(0)


@dataclass
class PolicyOutput:
    actions: torch.Tensor
    action_log_probs: torch.Tensor
    collaborators: torch.Tensor
    collaborator_log_probs: torch.Tensor
    values: torch.Tensor
    action_entropy: torch.Tensor
    collaborator_entropy: torch.Tensor
    collaboration_logits: torch.Tensor
    action_logits: torch.Tensor


class PhaseFeatureScorer(nn.Module):
    """Zero-init action scorer that does not advance PyTorch's RNG stream."""

    def __init__(self) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.zeros(1, PHASE_TRAFFIC_FEATURES))

    def forward(self, phase_features: torch.Tensor) -> torch.Tensor:
        return F.linear(phase_features, self.weight)


class RunningValueNorm:
    """Scalar running normalization for centralized-critic return targets."""

    def __init__(self, epsilon: float = 1e-5) -> None:
        self.epsilon = float(epsilon)
        self.mean = 0.0
        self.m2 = 0.0
        self.count = 0

    @property
    def variance(self) -> float:
        if self.count < 1:
            return 1.0
        return max(self.m2 / self.count, self.epsilon)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    def update(self, values: np.ndarray | torch.Tensor) -> None:
        array = np.asarray(
            values.detach().cpu().numpy()
            if isinstance(values, torch.Tensor)
            else values,
            dtype=np.float64,
        ).reshape(-1)
        if array.size == 0:
            return
        if not np.isfinite(array).all():
            raise ValueError("value-normalization update contains non-finite returns")
        batch_count = int(array.size)
        batch_mean = float(array.mean())
        batch_m2 = float(np.square(array - batch_mean).sum())
        if self.count == 0:
            self.mean = batch_mean
            self.m2 = batch_m2
            self.count = batch_count
            return
        delta = batch_mean - self.mean
        combined_count = self.count + batch_count
        self.mean += delta * batch_count / combined_count
        self.m2 += (
            batch_m2
            + delta * delta * self.count * batch_count / combined_count
        )
        self.count = combined_count

    def normalize(self, values: torch.Tensor) -> torch.Tensor:
        return (values - values.new_tensor(self.mean)) / values.new_tensor(self.std)

    def denormalize(self, values: torch.Tensor) -> torch.Tensor:
        return values * values.new_tensor(self.std) + values.new_tensor(self.mean)

    def state_dict(self) -> Dict[str, float | int]:
        return {"mean": self.mean, "m2": self.m2, "count": self.count}

    def load_state_dict(self, state: Mapping[str, Any]) -> None:
        mean = float(state.get("mean", 0.0))
        m2 = float(state.get("m2", 0.0))
        count = int(state.get("count", 0))
        if not math.isfinite(mean) or not math.isfinite(m2) or m2 < 0.0 or count < 0:
            raise ValueError("invalid value-normalization state")
        self.mean = mean
        self.m2 = m2
        self.count = count


class CoSLightNetwork(nn.Module):
    """Shared signal policy with explicit learned Top-K communication."""

    def __init__(
        self,
        num_agents: int,
        obs_dim: int,
        act_dim: int,
        top_k: int,
        hidden: int = TRANS_HIDDEN,
        neighbor_mask: Optional[torch.Tensor] = None,
    ) -> None:
        super().__init__()
        if num_agents < 1:
            raise ValueError("CoSLight needs at least one intersection")
        self.num_agents = int(num_agents)
        self.obs_dim = int(obs_dim)
        self.act_dim = int(act_dim)
        self.top_k = min(max(int(top_k), 1), self.num_agents)
        self.hidden = int(hidden)
        if neighbor_mask is None:
            neighbor_mask = torch.ones(
                self.num_agents, self.num_agents, dtype=torch.bool
            )
        neighbor_mask = torch.as_tensor(neighbor_mask, dtype=torch.bool)
        if tuple(neighbor_mask.shape) != (self.num_agents, self.num_agents):
            raise ValueError(
                "neighbor mask must be [agents, agents], got "
                f"{tuple(neighbor_mask.shape)}"
            )
        if bool(torch.any(~torch.diagonal(neighbor_mask))):
            raise ValueError("neighbor mask must include each intersection itself")
        self.register_buffer(
            "neighbor_mask", neighbor_mask.clone(), persistent=False
        )
        self.use_legacy_collaboration = False
        # V17 learns a separate all-candidate Top-K collaborator policy.  Older
        # checkpoints toggle this off while loading so their original direct-
        # neighbor inference remains reproducible.
        self.use_learned_topk_collaboration = True
        self.use_pressure_prior = True
        # New policies encode every intersection locally, then aggregate only
        # the selected Top-K messages. Old checkpoints toggle this off during
        # loading so their inference semantics remain reproducible.
        self.use_local_actor_encoder = True
        self.pressure_prior_scale = PRESSURE_PRIOR_SCALE
        self.hold_prior_bias = HOLD_PRIOR_BIAS

        self.obs_embed = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.LayerNorm(hidden),
            nn.ReLU(),
        )
        self.position = PositionalEncoding(hidden)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden,
            nhead=TRANS_HEADS if hidden % TRANS_HEADS == 0 else 1,
            dim_feedforward=hidden * 2,
            dropout=0.0,
            batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(
            encoder_layer,
            num_layers=TRANS_LAYERS,
            norm=nn.LayerNorm(hidden),
        )
        self.score_projection = nn.Linear(hidden, hidden, bias=False)
        self.self_score_bias = nn.Parameter(torch.tensor(0.1))
        self.target_projection = nn.Linear(hidden, hidden, bias=False)
        self.source_projection = nn.Linear(hidden, hidden, bias=False)
        self.context_projection = nn.Linear(hidden, hidden, bias=False)
        self.actor_head = nn.Sequential(
            nn.Linear(hidden * 2, hidden),
            nn.ReLU(),
            nn.Linear(hidden, act_dim),
        )
        nn.init.zeros_(self.actor_head[-1].weight)
        nn.init.zeros_(self.actor_head[-1].bias)
        # Do not use ``nn.Linear`` followed by zero initialization here:
        # its constructor consumes RNG and shifts otherwise-identical v7
        # stochastic action streams in controlled A/B experiments.
        self.phase_scorer = PhaseFeatureScorer()
        self.phase_actor_head = nn.Sequential(
            nn.Linear(hidden * 2 + PHASE_FEATURES, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
        )
        nn.init.zeros_(self.phase_actor_head[-1].weight)
        nn.init.zeros_(self.phase_actor_head[-1].bias)
        # V17 action-dependent collaborator bias: the selected Top-K messages
        # produce a per-phase-action logit bias.  An action-constant additive
        # context cancels in the argmax; this action-dependent bias lets the
        # messages change relative phase preference directly.  Zero-initialized
        # so warm starts keep the validated V16 behavior until the bias learns.
        self.collab_bias = nn.Sequential(
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Linear(hidden, act_dim),
        )
        nn.init.zeros_(self.collab_bias[-1].weight)
        nn.init.zeros_(self.collab_bias[-1].bias)
        # v7/v8 inference uses the fixed-index actor head. New training uses
        # action-conditioned phase features plus a direct residual scorer.
        for parameter in self.actor_head.parameters():
            parameter.requires_grad_(False)
        self.critic_obs_embed = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.LayerNorm(hidden),
            nn.ReLU(),
        )
        self.critic_position = PositionalEncoding(hidden)
        critic_encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden,
            nhead=TRANS_HEADS if hidden % TRANS_HEADS == 0 else 1,
            dim_feedforward=hidden * 2,
            dropout=0.0,
            batch_first=True,
            norm_first=True,
        )
        self.critic_transformer = nn.TransformerEncoder(
            critic_encoder_layer,
            num_layers=TRANS_LAYERS,
            norm=nn.LayerNorm(hidden),
        )
        self.critic_head = nn.Sequential(
            nn.Linear(hidden * 2, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
        )

    def encode(self, observations: torch.Tensor) -> torch.Tensor:
        if observations.ndim != 3:
            raise ValueError(
                f"expected [batch, agents, obs] input, got {tuple(observations.shape)}"
            )
        if observations.shape[1] != self.num_agents:
            raise ValueError(
                f"expected {self.num_agents} agents, got {observations.shape[1]}"
            )
        embedded = self.obs_embed(observations)
        if self.use_local_actor_encoder:
            return embedded
        return self.transformer(self.position(embedded))

    def actor_optimizer_groups(self) -> List[Dict[str, Any]]:
        """Use V13's validated higher LR only for the direct pressure scorer."""

        phase_scorer_ids = {id(parameter) for parameter in self.phase_scorer.parameters()}
        base_parameters = [
            parameter
            for parameter in self.actor_parameters()
            if id(parameter) not in phase_scorer_ids
        ]
        return [
            {"params": base_parameters, "lr": ACTOR_LR},
            {
                "params": list(self.phase_scorer.parameters()),
                "lr": ACTOR_LR * PHASE_SCORER_LR_MULTIPLIER,
            },
        ]

    def collaboration_logits(self, encoded: torch.Tensor) -> torch.Tensor:
        """Legacy symmetric scores retained for v7-v9 inference."""

        projected = F.normalize(self.score_projection(encoded), dim=-1, eps=1e-6)
        scores = torch.matmul(projected, projected.transpose(-2, -1))
        identity = torch.eye(
            self.num_agents, device=encoded.device, dtype=encoded.dtype
        ).unsqueeze(0)
        # Dot-product scores are symmetric by construction. Self collaboration is
        # legal and receives a small learned bias, matching CoSLight's diagonal prior.
        return scores + self.self_score_bias * identity

    def learned_collaboration_logits(self, encoded: torch.Tensor) -> torch.Tensor:
        """Return trainable all-candidate logits for the V17 Top-K policy."""

        target = self.target_projection(encoded)
        source = self.source_projection(encoded)
        scores = torch.matmul(target, source.transpose(-2, -1)) / math.sqrt(
            float(self.hidden)
        )
        identity = torch.eye(
            self.num_agents, device=encoded.device, dtype=encoded.dtype
        ).unsqueeze(0)
        # Unlike cosine similarity, these logits are not bounded to [-1, 1].
        # The diagonal and symmetry terms are regularizers, not hard masks.
        return scores + self.self_score_bias * identity

    def topology_attention(
        self, encoded: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return differentiable CoLight-style neighbor context and diagnostics."""

        target = self.target_projection(encoded)
        source = self.source_projection(encoded)
        logits = torch.matmul(target, source.transpose(-2, -1)) / math.sqrt(
            float(self.hidden)
        )
        mask = self.neighbor_mask.unsqueeze(0).expand(encoded.shape[0], -1, -1)
        masked_logits = logits.masked_fill(~mask, -1e8)
        weights = torch.softmax(masked_logits, dim=-1)
        context = torch.matmul(weights, self.context_projection(encoded))
        entropy = -(weights * torch.log(weights.clamp_min(1e-8))).sum(dim=-1)
        collaborators = torch.topk(
            masked_logits, k=self.top_k, dim=-1
        ).indices
        selected_are_neighbors = torch.gather(
            mask, dim=-1, index=collaborators
        )
        self_indices = torch.arange(
            self.num_agents, device=encoded.device
        ).reshape(1, self.num_agents, 1)
        collaborators = torch.where(
            selected_are_neighbors, collaborators, self_indices
        )
        return context, collaborators, entropy, masked_logits

    def select_collaborators(
        self,
        logits: torch.Tensor,
        deterministic: bool = False,
        selections: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Sample/evaluate unique collaborators with a Plackett-Luce policy."""
        batch, agents, candidates = logits.shape
        if agents != self.num_agents or candidates != self.num_agents:
            raise ValueError("collaboration logits must be [batch, agents, agents]")
        if selections is not None:
            selections = selections.long()
            if tuple(selections.shape) != (batch, agents, self.top_k):
                raise ValueError(
                    "stored collaborators must be [batch, agents, top_k]"
                )

        available = torch.ones_like(logits, dtype=torch.bool)
        chosen: List[torch.Tensor] = []
        total_log_prob = torch.zeros(batch, agents, device=logits.device)
        total_entropy = torch.zeros(batch, agents, device=logits.device)
        for selection_index in range(self.top_k):
            distribution = torch.distributions.Categorical(
                logits=logits.masked_fill(~available, -1e8)
            )
            if selections is not None:
                selected = selections[..., selection_index]
                if not bool(
                    torch.gather(available, -1, selected.unsqueeze(-1)).all()
                ):
                    raise ValueError("stored collaborator sequence contains duplicates")
            elif deterministic:
                selected = torch.argmax(distribution.probs, dim=-1)
            else:
                selected = distribution.sample()
            chosen.append(selected)
            total_log_prob = total_log_prob + distribution.log_prob(selected)
            total_entropy = total_entropy + distribution.entropy()
            available = available.scatter(-1, selected.unsqueeze(-1), False)

        return (
            torch.stack(chosen, dim=-1),
            total_log_prob,
            total_entropy / float(self.top_k),
        )

    def selected_collaborator_context(
        self,
        encoded: torch.Tensor,
        collaborators: torch.Tensor,
    ) -> torch.Tensor:
        """Aggregate exactly the selected Top-K local messages."""

        batch, agents, hidden = encoded.shape
        expected = (batch, agents, self.top_k)
        if tuple(collaborators.shape) != expected:
            raise ValueError(
                f"collaborators must be {expected}, got {tuple(collaborators.shape)}"
            )
        messages = self.context_projection(encoded)
        gathered = torch.gather(
            messages.unsqueeze(1).expand(-1, agents, -1, -1),
            2,
            collaborators.long().unsqueeze(-1).expand(-1, -1, -1, hidden),
        )
        return gathered.mean(dim=2)

    def _actor_logits(
        self,
        encoded: torch.Tensor,
        collaborators: torch.Tensor,
        phase_features: Optional[torch.Tensor] = None,
        collaborator_context: Optional[torch.Tensor] = None,
        pressure_prior_weights: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        batch, agents, hidden = encoded.shape
        if collaborator_context is None:
            collaborator_context = self.selected_collaborator_context(
                encoded, collaborators
            )
        elif tuple(collaborator_context.shape) != (batch, agents, hidden):
            raise ValueError(
                "collaborator context must be [batch, agents, hidden], got "
                f"{tuple(collaborator_context.shape)}"
            )
        actor_context = torch.cat((encoded, collaborator_context), dim=-1)
        logits = self.actor_head(actor_context)
        if phase_features is None:
            return logits
        expected_shape = (batch, agents, self.act_dim, PHASE_FEATURES)
        if tuple(phase_features.shape) != expected_shape:
            raise ValueError(
                f"phase features must be {expected_shape}, "
                f"got {tuple(phase_features.shape)}"
            )
        expanded_context = actor_context.unsqueeze(2).expand(
            -1, -1, self.act_dim, -1
        )
        conditioned_logits = self.phase_actor_head(
            torch.cat((expanded_context, phase_features), dim=-1)
        ).squeeze(-1)
        legacy_phase_logits = self.phase_scorer(
            phase_features[..., :PHASE_TRAFFIC_FEATURES]
        ).squeeze(-1)
        if pressure_prior_weights is None:
            prior_weights = torch.ones_like(phase_features[..., 5])
        else:
            if tuple(pressure_prior_weights.shape) != expected_shape[:3]:
                raise ValueError(
                    "pressure prior weights must be "
                    f"{expected_shape[:3]}, got {tuple(pressure_prior_weights.shape)}"
                )
            if not torch.isfinite(pressure_prior_weights).all():
                raise ValueError("pressure prior weights must be finite")
            if bool((pressure_prior_weights < 1.0).any()):
                raise ValueError("pressure prior weights cannot be below 1")
            prior_weights = pressure_prior_weights.to(
                device=phase_features.device, dtype=phase_features.dtype
            )
        pressure_prior = (
            self.pressure_prior_scale
            * (phase_features[..., 5] + (prior_weights - 1.0))
            + self.hold_prior_bias * phase_features[..., 6]
            if self.use_pressure_prior
            else torch.zeros_like(conditioned_logits)
        )
        # Action-dependent collaborator bias: the selected Top-K messages add
        # a per-phase-action logit bias, so they can change action-relative
        # logits instead of only shifting every action by the same amount.
        collaborator_bias = self.collab_bias(collaborator_context)
        return (
            logits
            + pressure_prior
            + legacy_phase_logits
            + conditioned_logits
            + collaborator_bias
        )

    def values(self, observations: torch.Tensor) -> torch.Tensor:
        critic_encoded = self.critic_transformer(
            self.critic_position(self.critic_obs_embed(observations))
        )
        global_context = critic_encoded.mean(dim=1, keepdim=True).expand(
            -1, self.num_agents, -1
        )
        return self.critic_head(
            torch.cat((critic_encoded, global_context), dim=-1)
        ).squeeze(-1)

    def actor_parameters(self) -> Tuple[nn.Parameter, ...]:
        parameters = [self.self_score_bias]
        for module in (
            self.obs_embed,
            self.transformer,
            self.score_projection,
            self.target_projection,
            self.source_projection,
            self.context_projection,
            self.actor_head,
            self.phase_scorer,
            self.phase_actor_head,
            self.collab_bias,
        ):
            parameters.extend(module.parameters())
        return tuple(parameters)

    def critic_parameters(self) -> Tuple[nn.Parameter, ...]:
        parameters: List[nn.Parameter] = []
        for module in (
            self.critic_obs_embed,
            self.critic_transformer,
            self.critic_head,
        ):
            parameters.extend(module.parameters())
        return tuple(parameters)

    def act(
        self,
        observations: torch.Tensor,
        valid_action_counts: torch.Tensor | Sequence[int] | int,
        deterministic: bool = False,
        action_masks: Optional[torch.Tensor] = None,
        phase_features: Optional[torch.Tensor] = None,
        pressure_prior_weights: Optional[torch.Tensor] = None,
    ) -> PolicyOutput:
        encoded = self.encode(observations)
        if self.use_legacy_collaboration:
            collaboration_logits = self.collaboration_logits(encoded)
            collaborators, collaborator_log_probs, collaborator_entropy = (
                self.select_collaborators(
                    collaboration_logits, deterministic=deterministic
                )
            )
            collaborator_context = None
        elif self.use_learned_topk_collaboration:
            collaboration_logits = self.learned_collaboration_logits(encoded)
            collaborators, collaborator_log_probs, collaborator_entropy = (
                self.select_collaborators(
                    collaboration_logits, deterministic=deterministic
                )
            )
            collaborator_context = None
        else:
            (
                collaborator_context,
                collaborators,
                collaborator_entropy,
                collaboration_logits,
            ) = self.topology_attention(encoded)
            collaborator_log_probs = torch.zeros(
                encoded.shape[:2], dtype=encoded.dtype, device=encoded.device
            )
        action_logits = self._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=collaborator_context,
            pressure_prior_weights=pressure_prior_weights,
        )
        action_distribution = _masked_categorical(
            action_logits, valid_action_counts, action_masks
        )
        actions = (
            torch.argmax(action_distribution.probs, dim=-1)
            if deterministic
            else action_distribution.sample()
        )
        return PolicyOutput(
            actions=actions,
            action_log_probs=action_distribution.log_prob(actions),
            collaborators=collaborators,
            collaborator_log_probs=collaborator_log_probs,
            values=self.values(observations),
            action_entropy=action_distribution.entropy(),
            collaborator_entropy=collaborator_entropy,
            collaboration_logits=collaboration_logits,
            action_logits=action_logits,
        )

    def evaluate_actions(
        self,
        observations: torch.Tensor,
        actions: torch.Tensor,
        valid_action_counts: torch.Tensor,
        collaborators: torch.Tensor,
        action_masks: Optional[torch.Tensor] = None,
        phase_features: Optional[torch.Tensor] = None,
        pressure_prior_weights: Optional[torch.Tensor] = None,
    ) -> PolicyOutput:
        encoded = self.encode(observations)
        if self.use_legacy_collaboration:
            collaboration_logits = self.collaboration_logits(encoded)
            collaborators, collaborator_log_probs, collaborator_entropy = (
                self.select_collaborators(
                    collaboration_logits, selections=collaborators
                )
            )
            collaborator_context = None
        elif self.use_learned_topk_collaboration:
            collaboration_logits = self.learned_collaboration_logits(encoded)
            collaborators, collaborator_log_probs, collaborator_entropy = (
                self.select_collaborators(
                    collaboration_logits, selections=collaborators
                )
            )
            collaborator_context = None
        else:
            (
                collaborator_context,
                collaborators,
                collaborator_entropy,
                collaboration_logits,
            ) = self.topology_attention(encoded)
            collaborator_log_probs = torch.zeros(
                encoded.shape[:2], dtype=encoded.dtype, device=encoded.device
            )
        action_logits = self._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=collaborator_context,
            pressure_prior_weights=pressure_prior_weights,
        )
        action_distribution = _masked_categorical(
            action_logits,
            valid_action_counts,
            action_masks,
        )
        return PolicyOutput(
            actions=actions,
            action_log_probs=action_distribution.log_prob(actions),
            collaborators=collaborators,
            collaborator_log_probs=collaborator_log_probs,
            values=self.values(observations),
            action_entropy=action_distribution.entropy(),
            collaborator_entropy=collaborator_entropy,
            collaboration_logits=collaboration_logits,
            action_logits=action_logits,
        )


def _masked_categorical(
    logits: torch.Tensor,
    valid_action_counts: torch.Tensor | Sequence[int] | int,
    action_masks: Optional[torch.Tensor] = None,
) -> torch.distributions.Categorical:
    """Categorical distribution with zero mass on nonexistent phase slots."""
    if logits.ndim < 2:
        raise ValueError("phase logits need a batch dimension")
    action_dim = logits.shape[-1]
    batch_shape = logits.shape[:-1]
    counts = torch.as_tensor(
        valid_action_counts, device=logits.device, dtype=torch.long
    )
    if counts.numel() == 1:
        counts = counts.reshape((1,) * len(batch_shape)).expand(batch_shape)
    elif tuple(counts.shape) == tuple(batch_shape[-1:]) and len(batch_shape) == 2:
        counts = counts.unsqueeze(0).expand(batch_shape)
    elif tuple(counts.shape) != tuple(batch_shape):
        raise ValueError(
            f"valid action counts shape {tuple(counts.shape)} does not match {tuple(batch_shape)}"
        )
    if bool(torch.any(counts < 1)) or bool(torch.any(counts > action_dim)):
        raise ValueError("valid phase count must be in [1, action_dim]")
    valid = (
        torch.arange(action_dim, device=logits.device)
        .reshape((1,) * len(batch_shape) + (action_dim,))
        < counts.unsqueeze(-1)
    )
    if action_masks is not None:
        mask = torch.as_tensor(action_masks, device=logits.device, dtype=torch.bool)
        if (
            len(batch_shape) == 2
            and tuple(mask.shape) == (batch_shape[-1], action_dim)
        ):
            mask = mask.unsqueeze(0).expand(*batch_shape, action_dim)
        elif tuple(mask.shape) != (*batch_shape, action_dim):
            raise ValueError(
                f"action mask shape {tuple(mask.shape)} does not match "
                f"{(*batch_shape, action_dim)}"
            )
        valid = valid & mask
    if bool(torch.any(~valid.any(dim=-1))):
        raise ValueError("every agent must have at least one valid phase action")
    return torch.distributions.Categorical(
        logits=logits.masked_fill(~valid, -1e8)
    )


def _phase_clipped_surrogate(
    new_action_log_probs: torch.Tensor,
    old_action_log_probs: torch.Tensor,
    advantages: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """PPO loss for the only environment action: the selected signal phase."""

    log_ratio = new_action_log_probs - old_action_log_probs
    ratio = log_ratio.exp()
    surrogate = ratio * advantages
    clipped_surrogate = torch.clamp(
        ratio, 1.0 - CLIP_EPS, 1.0 + CLIP_EPS
    ) * advantages
    return (
        -torch.minimum(surrogate, clipped_surrogate).mean(),
        log_ratio,
        ratio,
    )


def _collaborator_clipped_surrogate(
    new_log_probs: torch.Tensor,
    old_log_probs: torch.Tensor,
    advantages: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Independent PPO loss for the sampled Top-K collaborator sequence."""

    log_ratio = new_log_probs - old_log_probs
    ratio = log_ratio.exp()
    surrogate = ratio * advantages
    clipped_surrogate = torch.clamp(
        ratio, 1.0 - CLIP_EPS, 1.0 + CLIP_EPS
    ) * advantages
    return (
        -torch.minimum(surrogate, clipped_surrogate).mean(),
        log_ratio,
        ratio,
    )


def _gradient_l2_norm(
    gradients: Sequence[Optional[torch.Tensor]],
) -> float:
    squared_norm = sum(
        float(gradient.detach().square().sum())
        for gradient in gradients
        if gradient is not None
    )
    return math.sqrt(squared_norm)


def _gradient_cosine(
    left: Sequence[Optional[torch.Tensor]],
    right: Sequence[Optional[torch.Tensor]],
) -> float:
    dot = 0.0
    for left_gradient, right_gradient in zip(left, right):
        if left_gradient is not None and right_gradient is not None:
            dot += float(
                (left_gradient.detach() * right_gradient.detach()).sum()
            )
    denominator = _gradient_l2_norm(left) * _gradient_l2_norm(right)
    return dot / denominator if denominator > 1e-12 else 0.0


# Runtime state. Protocol 2.0 invokes the local module from one thread.
_model: Optional[CoSLightNetwork] = None
_actor_optimizer: Optional[torch.optim.Adam] = None
_critic_optimizer: Optional[torch.optim.Adam] = None
_value_normalizer = RunningValueNorm()
_state_builder: Optional[StateBuilder] = None
_tls_order: List[str] = []
_phase_orders: Dict[str, List[int]] = {}
_neighbors: Dict[str, List[str]] = {}
_incoming: Dict[str, List[str]] = {}
_outgoing: Dict[str, List[str]] = {}
_movements: Dict[str, List[Tuple[str, str]]] = {}
_lane_capacity: Dict[str, float] = {}
_phase_durations: Dict[str, Dict[int, float]] = {}
_edge_lanes: Dict[str, list] = {}
_minimum_green = 5.0
_max_green_factor = DEFAULT_MAX_GREEN_FACTOR
_pressure_shield_margin = DEFAULT_PRESSURE_SHIELD_MARGIN
_residual_min_best_pressure = DEFAULT_RESIDUAL_MIN_BEST_PRESSURE
_switch_logit_margin = DEFAULT_SWITCH_LOGIT_MARGIN
_cloud_mode = "off"
_cloud_coordinator: Optional[RegionalCloudCoordinator] = None

_mode = "random"
_model_path: Optional[str] = None
_loaded_resume_path: Optional[str] = None
_top_k = DEFAULT_TOP_K
_reward_mode = "pressure"
_episode = 0
_episode_trajectory: Dict[str, Any] = {}
_pending_transition: Optional[Dict[str, Any]] = None
_last_joint_decision_time: Optional[float] = None
_latest_observation_time: Optional[float] = None
_terminal_bootstrap_values: Optional[np.ndarray] = None
_horizon_guard_active = False
_horizon_guarded_agents = 0
_buffer_episodes: List[Dict[str, Any]] = []
_prev_pressure: Dict[str, float] = {}
_prev_actions: Dict[str, int] = {}
_pending_signal_commands: Dict[str, Dict[str, float | int]] = {}
_signal_execution_stats: Dict[str, Dict[str, Any]] = {}
_observed_phase_by_tls: Dict[str, int] = {}
_last_observed_phase_changes: Dict[str, Dict[str, float | int]] = {}
_vehicle_braking_stats: Dict[str, Any] = {}
_pressure_shield_stats: Dict[str, Any] = {}
_switch_hysteresis_stats: Dict[str, Any] = {}
_reward_mean = 0.0
_reward_m2 = 0.0
_reward_count = 0
_sample_logged = 0
_policy_generation = 0

_collector_policy_state: Optional[Dict[str, torch.Tensor]] = None
_collector_policy_seed: Optional[int] = None
_collector_rollout_seed: Optional[int] = None
_collector_policy_generation: Optional[int] = None
_collector_value_stats: Optional[Dict[str, float | int]] = None
_collector_metadata: Optional[dict] = None
_collected_rollout: Optional[dict] = None


def _build_neighbor_mask(
    tls_order: Sequence[str],
    neighbors: Mapping[str, Sequence[str]],
    top_k: int,
) -> torch.Tensor:
    """Build a fixed self-plus-direct-neighbor CoLight attention scope."""

    order = list(tls_order)
    index = {tid: position for position, tid in enumerate(order)}
    scope_size = min(max(int(top_k), 1), len(order))
    mask = torch.zeros((len(order), len(order)), dtype=torch.bool)
    for target_index, tid in enumerate(order):
        candidates = [tid]
        candidates.extend(
            neighbor
            for neighbor in neighbors.get(tid, ())
            if neighbor in index and neighbor != tid
        )
        for source_tid in list(dict.fromkeys(candidates))[:scope_size]:
            mask[target_index, index[source_tid]] = True
    return mask


def _new_trajectory() -> Dict[str, Any]:
    return {
        "obs": [],
        "actions": [],
        "action_log_probs": [],
        "collaborators": [],
        "collaborator_log_probs": [],
        "values": [],
        "normalized_values": [],
        "rewards": [],
        "raw_rewards": [],
        "transition_durations_s": [],
        "valid_action_counts": [],
        "action_masks": [],
        "phase_features": [],
        "last_values": None,
    }


def _reset_runtime_state() -> None:
    """Reset module globals; intended for tests and fresh worker processes."""
    global _model, _actor_optimizer, _critic_optimizer, _value_normalizer
    global _state_builder, _tls_order, _phase_orders
    global _neighbors, _incoming, _outgoing, _movements
    global _lane_capacity, _phase_durations
    global _edge_lanes, _minimum_green, _max_green_factor
    global _pressure_shield_margin, _residual_min_best_pressure
    global _switch_logit_margin
    global _cloud_mode, _cloud_coordinator
    global _mode, _model_path, _loaded_resume_path
    global _top_k, _reward_mode, _episode
    global _episode_trajectory, _pending_transition, _last_joint_decision_time
    global _latest_observation_time
    global _terminal_bootstrap_values, _horizon_guard_active
    global _horizon_guarded_agents
    global _prev_pressure, _prev_actions
    global _pending_signal_commands, _signal_execution_stats
    global _observed_phase_by_tls, _last_observed_phase_changes
    global _vehicle_braking_stats
    global _pressure_shield_stats, _switch_hysteresis_stats
    global _reward_mean, _reward_m2, _reward_count, _sample_logged
    global _policy_generation, _collector_policy_state, _collector_policy_seed
    global _collector_rollout_seed, _collector_policy_generation
    global _collector_value_stats, _collector_metadata, _collected_rollout

    _model = None
    _actor_optimizer = None
    _critic_optimizer = None
    _value_normalizer = RunningValueNorm()
    _state_builder = None
    _tls_order = []
    _phase_orders = {}
    _neighbors = {}
    _incoming = {}
    _outgoing = {}
    _movements = {}
    _lane_capacity = {}
    _phase_durations = {}
    _edge_lanes = {}
    _minimum_green = 5.0
    _max_green_factor = DEFAULT_MAX_GREEN_FACTOR
    _pressure_shield_margin = DEFAULT_PRESSURE_SHIELD_MARGIN
    _residual_min_best_pressure = DEFAULT_RESIDUAL_MIN_BEST_PRESSURE
    _switch_logit_margin = DEFAULT_SWITCH_LOGIT_MARGIN
    _cloud_mode = "off"
    _cloud_coordinator = None
    _mode = "random"
    _model_path = None
    _loaded_resume_path = None
    _top_k = DEFAULT_TOP_K
    _reward_mode = "pressure"
    _episode = 0
    _episode_trajectory = _new_trajectory()
    _pending_transition = None
    _last_joint_decision_time = None
    _latest_observation_time = None
    _terminal_bootstrap_values = None
    _horizon_guard_active = False
    _horizon_guarded_agents = 0
    _buffer_episodes.clear()
    _prev_pressure = {}
    _prev_actions = {}
    _pending_signal_commands = {}
    _signal_execution_stats = {}
    _observed_phase_by_tls = {}
    _last_observed_phase_changes = {}
    _vehicle_braking_stats = {}
    _pressure_shield_stats = {}
    _switch_hysteresis_stats = {}
    _reward_mean = 0.0
    _reward_m2 = 0.0
    _reward_count = 0
    _sample_logged = 0
    _policy_generation = 0
    _collector_policy_state = None
    _collector_policy_seed = None
    _collector_rollout_seed = None
    _collector_policy_generation = None
    _collector_value_stats = None
    _collector_metadata = None
    _collected_rollout = None
    reset_lane_state()


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
    policy_generation: int,
    value_stats: Optional[Mapping[str, Any]] = None,
) -> None:
    """Install one immutable policy generation for a worker-process rollout."""

    global _collector_policy_state, _collector_policy_seed, _collector_rollout_seed
    global _collector_policy_generation, _collector_value_stats
    global _collector_metadata, _collected_rollout
    _collector_policy_state = _copy_policy_state(policy_state)
    _collector_policy_seed = int(policy_seed)
    _collector_rollout_seed = int(rollout_seed)
    _collector_policy_generation = int(policy_generation)
    _collector_value_stats = (
        dict(value_stats)
        if value_stats is not None
        else RunningValueNorm().state_dict()
    )
    _collector_metadata = None
    _collected_rollout = None


def export_policy_state() -> Dict[str, torch.Tensor]:
    if _model is None:
        raise RuntimeError("CoSLight model is not initialized")
    copied = _copy_policy_state(_model.state_dict())
    assert copied is not None
    return copied


def export_value_stats() -> Dict[str, float | int]:
    return dict(_value_normalizer.state_dict())


def _load_model_weights(state: Mapping[str, torch.Tensor]) -> None:
    """Load model weights, tolerating only the V17 collab-gate parameters.

    Older checkpoints (V16 warm-start source, deployed V16 inference files)
    predate the action-dependent collaborator bias and do not contain its
    parameters; any other missing or unexpected key is a real mismatch.
    """
    if _model is None:
        raise RuntimeError("CoSLight model is not initialized")
    incompatible = _model.load_state_dict(state, strict=False)
    unexpected = set(incompatible.unexpected_keys)
    missing = set(incompatible.missing_keys) - NEW_COLLAB_BIAS_PARAMS
    if unexpected or missing:
        raise ValueError(
            "CoSLight checkpoint model parameters are incompatible: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )


def install_parallel_initial_policy(
    state: Mapping[str, torch.Tensor],
    value_stats: Optional[Mapping[str, Any]] = None,
) -> None:
    """Adopt the collectors' initial policy before any optimizer update."""

    if _model is None:
        raise RuntimeError("CoSLight model is not initialized")
    if _episode != 0 or _buffer_episodes:
        raise RuntimeError("Initial policy can only be installed before the first rollout")
    _load_model_weights(state)
    if value_stats is not None:
        _value_normalizer.load_state_dict(value_stats)


def training_episode_count() -> int:
    return _episode


def policy_generation() -> int:
    return _policy_generation


def initialize(payload: dict) -> dict:
    global _model, _actor_optimizer, _critic_optimizer, _value_normalizer
    global _state_builder, _tls_order, _phase_orders
    global _neighbors, _incoming, _outgoing, _movements
    global _lane_capacity, _phase_durations
    global _edge_lanes, _minimum_green, _max_green_factor
    global _pressure_shield_margin, _residual_min_best_pressure
    global _switch_logit_margin
    global _cloud_mode, _cloud_coordinator
    global _mode, _model_path, _loaded_resume_path
    global _top_k, _reward_mode
    global _episode_trajectory, _pending_transition, _last_joint_decision_time
    global _latest_observation_time
    global _terminal_bootstrap_values, _horizon_guard_active
    global _horizon_guarded_agents
    global _prev_pressure, _prev_actions
    global _pending_signal_commands, _signal_execution_stats
    global _observed_phase_by_tls, _last_observed_phase_changes
    global _vehicle_braking_stats
    global _pressure_shield_stats, _switch_hysteresis_stats
    global _collector_value_stats, _collector_metadata, _collected_rollout

    # Keep the historical Protocol 2.0 behavior: importing the algorithm without
    # an explicit mode trains the policy.  Baseline/evaluation launches must opt
    # into an explicit evaluation mode deliberately.
    _mode = os.environ.get("COSLIGHT_MODE", "train").strip().lower()
    if _mode not in {
        "random",
        "max_pressure",
        "fixed",
        "untrained",
        "model",
        "stochastic_model",
        "train",
        "collect",
    }:
        raise ValueError(f"unsupported COSLIGHT_MODE={_mode!r}")
    _model_path = os.environ.get("COSLIGHT_MODEL_PATH")
    _top_k = int(os.environ.get("COSLIGHT_TOP_K", str(DEFAULT_TOP_K)))
    if _top_k < 1:
        raise ValueError("COSLIGHT_TOP_K must be positive")
    _reward_mode = os.environ.get("COSLIGHT_REWARD_MODE", "pressure").strip().lower()
    if _reward_mode not in REWARD_MODES:
        raise ValueError(
            f"COSLIGHT_REWARD_MODE must be one of {REWARD_MODES}, "
            f"got {_reward_mode!r}"
        )
    _max_green_factor = float(
        os.environ.get(
            "COSLIGHT_MAX_GREEN_FACTOR", str(DEFAULT_MAX_GREEN_FACTOR)
        )
    )
    if not math.isfinite(_max_green_factor) or _max_green_factor < 0.0:
        raise ValueError("COSLIGHT_MAX_GREEN_FACTOR must be finite and non-negative")
    _pressure_shield_margin = float(
        os.environ.get(
            "COSLIGHT_PRESSURE_SHIELD_MARGIN",
            str(DEFAULT_PRESSURE_SHIELD_MARGIN),
        )
    )
    if math.isnan(_pressure_shield_margin) or _pressure_shield_margin < 0.0:
        raise ValueError(
            "COSLIGHT_PRESSURE_SHIELD_MARGIN must be non-negative or inf"
        )
    _residual_min_best_pressure = float(
        os.environ.get(
            "COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE",
            str(DEFAULT_RESIDUAL_MIN_BEST_PRESSURE),
        )
    )
    if math.isnan(_residual_min_best_pressure):
        raise ValueError(
            "COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE must not be NaN"
        )
    _switch_logit_margin = float(
        os.environ.get(
            "COSLIGHT_SWITCH_LOGIT_MARGIN",
            str(DEFAULT_SWITCH_LOGIT_MARGIN),
        )
    )
    if (
        math.isnan(_switch_logit_margin)
        or _switch_logit_margin == math.inf
        or (
            math.isfinite(_switch_logit_margin)
            and _switch_logit_margin < 0.0
        )
    ):
        raise ValueError(
            "COSLIGHT_SWITCH_LOGIT_MARGIN must be -inf or finite and non-negative"
        )
    _cloud_mode = os.environ.get("COSLIGHT_CLOUD_MODE", "off").strip().lower()
    if _cloud_mode not in {
        "off",
        "regional_rule",
        "platoon_shadow",
        "platoon_control",
        "platoon_hold_shadow",
        "platoon_hold_control",
        "platoon_hold_safe_shadow",
        "platoon_hold_safe_control",
    }:
        raise ValueError(
            "COSLIGHT_CLOUD_MODE must be 'off', 'regional_rule', or "
            "a platoon shadow/control mode, "
            f"got {_cloud_mode!r}"
        )
    if _cloud_mode != "off" and _mode not in {"model", "stochastic_model"}:
        raise ValueError(
            "Stage-2 cloud modulation is inference-only and requires "
            "COSLIGHT_MODE=model or stochastic_model"
        )

    intersections_meta = payload.get("intersections", {})
    _tls_order = sorted(str(tid) for tid in intersections_meta)
    if not _tls_order:
        raise ValueError("CoSLight needs at least one intersection")
    _state_builder = StateBuilder(payload, _tls_order)
    _phase_orders = {
        tid: list(_state_builder.indices[tid].phase_order) for tid in _tls_order
    }
    _neighbors = {
        tid: [
            str(neighbor)
            for neighbor in intersections_meta[tid].get("direct_neighbors", [])
            if str(neighbor) in intersections_meta
        ]
        for tid in _tls_order
    }
    _incoming = {
        tid: list(_state_builder.indices[tid].incoming_lanes) for tid in _tls_order
    }
    _outgoing = {
        tid: list(_state_builder.indices[tid].outgoing_lanes) for tid in _tls_order
    }
    _movements = {
        tid: [
            (str(connection["from_lane"]), str(connection["to_lane"]))
            for connection in intersections_meta[tid].get("connections", [])
            if connection.get("from_lane") and connection.get("to_lane")
        ]
        for tid in _tls_order
    }
    _lane_capacity = {
        lane_id: capacity
        for index in _state_builder.indices.values()
        for lane_id, capacity in index.lane_capacity.items()
    }
    _phase_durations = {}
    for tid in _tls_order:
        phases = intersections_meta[tid].get("phases", {})
        _phase_durations[tid] = {
            int(phase_id): float(phase.get("green_seconds", DEFAULT_GREEN_DURATION))
            for phase_id, phase in phases.items()
        }
    _edge_lanes = payload.get("edge_lanes", {}) or {}
    _minimum_green = max(float(payload.get("minimum_green", 5.0)), 0.0)
    build_static_indices(payload, _edge_lanes)

    num_agents = len(_tls_order)
    act_dim = max(len(order) for order in _phase_orders.values())
    if _cloud_mode in {
        "regional_rule",
        "platoon_shadow",
        "platoon_control",
        "platoon_hold_shadow",
        "platoon_hold_control",
        "platoon_hold_safe_shadow",
        "platoon_hold_safe_control",
    }:
        topology_path = os.environ.get("COSLIGHT_CLOUD_TOPOLOGY")
        if not topology_path:
            raise ValueError(
                "COSLIGHT_CLOUD_TOPOLOGY is required when cloud mode is enabled"
            )
        _cloud_coordinator = RegionalCloudCoordinator.from_file(
            topology_path,
            tls_order=_tls_order,
            incoming_lanes=_incoming,
            lane_capacity=_lane_capacity,
            lane_length={
                lane_id: length
                for index in _state_builder.indices.values()
                for lane_id, length in index.lane_length.items()
            },
            phase_connections={
                tid: _state_builder.indices[tid].phase_primary_connections
                for tid in _tls_order
            },
            source_phase_connections={
                tid: (
                    _state_builder.indices[tid].phase_connections
                    if _cloud_mode == "platoon_shadow"
                    else _state_builder.indices[tid].phase_primary_connections
                )
                for tid in _tls_order
            },
            lane_edges={
                tid: _state_builder.indices[tid].lane_edges for tid in _tls_order
            },
            lane_speed={
                lane_id: speed
                for index in _state_builder.indices.values()
                for lane_id, speed in index.lane_speed.items()
            },
            phase_orders=_phase_orders,
            action_dim=act_dim,
            coordination_mode=_cloud_mode,
            update_interval_s=float(
                os.environ.get("COSLIGHT_CLOUD_UPDATE_INTERVAL", "60")
            ),
            max_weight=float(
                os.environ.get("COSLIGHT_CLOUD_MAX_WEIGHT", "1.2")
            ),
            target_queue_ratio=float(
                os.environ.get("COSLIGHT_CLOUD_TARGET_QUEUE", "0.01")
            ),
            spill_threshold=float(
                os.environ.get("COSLIGHT_CLOUD_SPILL_THRESHOLD", "0.7")
            ),
            min_platoon_vehicles=int(
                os.environ.get("COSLIGHT_CLOUD_MIN_PLATOON_VEHICLES", "1")
            ),
            platoon_lead_s=float(
                os.environ.get("COSLIGHT_CLOUD_PLATOON_LEAD", "15")
            ),
            platoon_lag_s=float(
                os.environ.get("COSLIGHT_CLOUD_PLATOON_LAG", "15")
            ),
            hold_cooldown_s=float(
                os.environ.get("COSLIGHT_CLOUD_HOLD_COOLDOWN", "0")
            ),
        )
    else:
        _cloud_coordinator = None
    neighbor_mask = _build_neighbor_mask(_tls_order, _neighbors, _top_k)
    if (
        _mode in {"train", "collect", "untrained"}
        and num_agents > 1
        and _top_k >= num_agents
    ):
        raise ValueError(
            "learned Top-K must leave at least one intersection unselected; "
            f"got top_k={_top_k} for {num_agents} intersections"
        )
    dimensions_match = (
        isinstance(_model, CoSLightNetwork)
        and _model.num_agents == num_agents
        and _model.obs_dim == OBS_DIM
        and _model.act_dim == act_dim
        and _model.top_k == min(_top_k, num_agents)
        and torch.equal(_model.neighbor_mask.cpu(), neighbor_mask)
    )

    if _mode in {"model", "stochastic_model"}:
        if not _model_path:
            raise ValueError(
                f"COSLIGHT_MODE={_mode} requires COSLIGHT_MODEL_PATH"
            )
        if not os.path.isfile(_model_path):
            raise FileNotFoundError(f"CoSLight checkpoint not found: {_model_path}")
        _model = CoSLightNetwork(
            num_agents, OBS_DIM, act_dim, _top_k, neighbor_mask=neighbor_mask
        )
        _actor_optimizer = None
        _critic_optimizer = None
        _value_normalizer = RunningValueNorm()
        _load_checkpoint(_model_path, load_optimizer=False)
        _buffer_episodes.clear()
        _model.eval()
    elif _mode == "untrained":
        torch.manual_seed(int(os.environ.get("COSLIGHT_POLICY_SEED", "42")))
        _model = CoSLightNetwork(
            num_agents, OBS_DIM, act_dim, _top_k, neighbor_mask=neighbor_mask
        )
        _actor_optimizer = None
        _critic_optimizer = None
        _value_normalizer = RunningValueNorm()
        _buffer_episodes.clear()
        _loaded_resume_path = None
        _model.eval()
    elif _mode == "train":
        if not dimensions_match:
            _model = CoSLightNetwork(
                num_agents, OBS_DIM, act_dim, _top_k, neighbor_mask=neighbor_mask
            )
            _actor_optimizer = None
            _critic_optimizer = None
            _value_normalizer = RunningValueNorm()
            _buffer_episodes.clear()
            _loaded_resume_path = None
        if _actor_optimizer is None or _critic_optimizer is None:
            _actor_optimizer = torch.optim.Adam(_model.actor_optimizer_groups())
            _critic_optimizer = torch.optim.Adam(
                _model.critic_parameters(), lr=CRITIC_LR
            )
        _model.use_learned_topk_collaboration = True
        resume_path = os.environ.get("COSLIGHT_RESUME_PATH")
        if resume_path and resume_path != _loaded_resume_path:
            if not os.path.isfile(resume_path):
                raise FileNotFoundError(f"CoSLight resume checkpoint not found: {resume_path}")
            _load_checkpoint(resume_path, load_optimizer=True)
            _loaded_resume_path = resume_path
        _model.train()
    elif _mode == "collect":
        if (
            _collector_policy_seed is None
            or _collector_rollout_seed is None
            or _collector_policy_generation is None
        ):
            raise RuntimeError("prepare_collector() must be called before collect mode")
        torch.manual_seed(_collector_policy_seed)
        _model = CoSLightNetwork(
            num_agents, OBS_DIM, act_dim, _top_k, neighbor_mask=neighbor_mask
        )
        if _collector_policy_state is not None:
            _load_model_weights(_collector_policy_state)
        _model.eval()
        _actor_optimizer = None
        _critic_optimizer = None
        _value_normalizer = RunningValueNorm()
        _value_normalizer.load_state_dict(_collector_value_stats or {})
        _loaded_resume_path = None
        _collector_metadata = copy.deepcopy(payload)
        _collected_rollout = None
        torch.manual_seed(_collector_rollout_seed)
        np.random.seed(_collector_rollout_seed % (2**32 - 1))
    else:
        # Fixed/random baselines do not need a neural network.
        _model = None
        _actor_optimizer = None
        _critic_optimizer = None
        _value_normalizer = RunningValueNorm()

    _episode_trajectory = _new_trajectory()
    _pending_transition = None
    _last_joint_decision_time = None
    _latest_observation_time = None
    _terminal_bootstrap_values = None
    _horizon_guard_active = False
    _horizon_guarded_agents = 0
    _prev_pressure = {}
    _prev_actions = {}
    _pending_signal_commands = {}
    _observed_phase_by_tls = {}
    _last_observed_phase_changes = {}
    _signal_execution_stats = {
        tid: {
            "valid_phase_count": len(_phase_orders[tid]),
            "commands": 0.0,
            "change_requests": 0.0,
            "observed_changes": 0.0,
            "unresolved_changes": 0.0,
            "change_delay_total_s": 0.0,
            "change_delay_max_s": 0.0,
            "max_observed_green_s": 0.0,
            "max_green_forced_commands": 0.0,
            "phase_commands": {},
        }
        for tid in _tls_order
    }
    _vehicle_braking_stats = {
        "vehicle_observations": 0,
        "hard_braking_events": 0,
        "latest_traffic_hard_braking_total": 0,
        "attributed_vehicle_observations": 0,
        "attributed_events": 0,
        "unattributed_events": 0,
        "post_switch": {
            str(int(window)): {"vehicle_observations": 0, "events": 0}
            for window in BRAKING_ATTRIBUTION_WINDOWS_S
        },
        "distance": {
            str(int(distance)): {"vehicle_observations": 0, "events": 0}
            for distance in BRAKING_DISTANCE_THRESHOLDS_M
        },
        "recent_10s_near_100m": {
            "vehicle_observations": 0,
            "events": 0,
        },
        "outside_30s": {"vehicle_observations": 0, "events": 0},
        "events": [],
        "per_intersection": {
            tid: {
                "vehicle_observations": 0,
                "events": 0,
                "post_switch": {
                    str(int(window)): {
                        "vehicle_observations": 0,
                        "events": 0,
                    }
                    for window in BRAKING_ATTRIBUTION_WINDOWS_S
                },
            }
            for tid in _tls_order
        },
    }
    _pressure_shield_stats = {
        "decisions": 0,
        "candidate_actions": 0,
        "allowed_actions": 0,
        "filtered_actions": 0,
        "selected_regrets": [],
        "positive_regret_count": 0,
        "reference_disagreements": 0,
        "reference_disagreement_events": [],
        "residual_guarded_agents": 0,
        "per_intersection": {
            tid: {
                "decisions": 0,
                "positive_regret_count": 0,
                "reference_disagreements": 0,
                "zero_regret_reference_disagreements": 0,
                "residual_guarded_count": 0,
                "selected_regret_total": 0.0,
                "selected_regret_max": 0.0,
            }
            for tid in _tls_order
        },
    }
    _switch_hysteresis_stats = {
        "agent_decisions": 0,
        "switch_candidates": 0,
        "held_switches": 0,
        "logit_gaps": [],
        "held_events": [],
        "per_intersection": {
            tid: {
                "agent_decisions": 0,
                "switch_candidates": 0,
                "held_switches": 0,
                "logit_gap_total": 0.0,
                "logit_gap_max": 0.0,
            }
            for tid in _tls_order
        },
    }
    logger.info(
        "CoSLight initialize: mode=%s intersections=%d obs=%d actions=%d top_k=%d "
        "policy_generation=%d reward=%s max_green_factor=%.2f pressure_shield=%s "
        "residual_min_pressure=%s switch_logit_margin=%s cloud=%s",
        _mode,
        num_agents,
        OBS_DIM,
        act_dim,
        min(_top_k, num_agents),
        _policy_generation if _mode != "collect" else _collector_policy_generation,
        _reward_mode,
        _max_green_factor,
        (
            f"{_pressure_shield_margin:.6g}"
            if math.isfinite(_pressure_shield_margin)
            else "off"
        ),
        (
            f"{_residual_min_best_pressure:.6g}"
            if math.isfinite(_residual_min_best_pressure)
            else "off"
        ),
        (
            f"{_switch_logit_margin:.6g}"
            if math.isfinite(_switch_logit_margin)
            else "off"
        ),
        _cloud_mode,
    )
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "ready": True,
    }


def build_state(intersections: Dict[str, Any]) -> np.ndarray:
    if _state_builder is None:
        raise RuntimeError("CoSLight must be initialized before build_state")
    return _state_builder.build_all(intersections, _tls_order)


def _occupancy_ratio(raw_value: Any) -> float:
    # Protocol 2.0 always forwards TraCI's percentage in [0, 100].  Do not
    # infer units from magnitude: 0.8 is a valid 0.8 percent observation.
    value = max(float(raw_value or 0.0), 0.0)
    return float(np.clip(value / 100.0, 0.0, 1.0))


def _lane_density(lanes: Mapping[str, Any], lane_id: str) -> float:
    return (
        float(lanes.get(lane_id, {}).get("vehicle_count", 0.0))
        / max(_lane_capacity.get(lane_id, 1.0), 1.0)
    )


def _signed_lane_pressure(intersections: Dict[str, Any], tls_id: str) -> float:
    lanes = intersections.get(tls_id, {}).get("lanes", {})
    incoming = sum(_lane_density(lanes, lane_id) for lane_id in _incoming.get(tls_id, []))
    outgoing = sum(_lane_density(lanes, lane_id) for lane_id in _outgoing.get(tls_id, []))
    return incoming - outgoing


def compute_pressure(intersections: Dict[str, Any], tls_id: str) -> float:
    """Capacity-normalized absolute pressure, following PressLight semantics."""

    lanes = intersections.get(tls_id, {}).get("lanes", {})
    movements = _movements.get(tls_id, [])
    if not movements:
        return abs(_signed_lane_pressure(intersections, tls_id))
    movement_pressure = sum(
        _lane_density(lanes, incoming_lane)
        - _lane_density(lanes, outgoing_lane)
        for incoming_lane, outgoing_lane in movements
    )
    return abs(movement_pressure)


def _max_pressure_action_indices(
    intersections: Mapping[str, Any],
    action_masks: np.ndarray,
) -> np.ndarray:
    """Choose the valid phase with the largest served-movement pressure."""

    if _state_builder is None:
        raise RuntimeError("CoSLight state builder is not initialized")
    actions = np.zeros(len(_tls_order), dtype=np.int64)
    for agent_index, tid in enumerate(_tls_order):
        index = _state_builder.indices[tid]
        valid_actions = np.flatnonzero(action_masks[agent_index])
        if valid_actions.size == 0:
            raise RuntimeError(f"CoSLight intersection {tid!r} has no valid action")

        intersection = intersections.get(tid, {})
        lanes = intersection.get("lanes", {})
        current_phase = int(
            intersection.get("current_phase", index.phase_order[0])
        )
        current_action = index.phase_index.get(current_phase)
        mapped_scores = []
        for action_index in valid_actions:
            pairs = index.phase_connections[int(action_index)]
            if not pairs:
                continue
            score = sum(
                _lane_density(lanes, incoming_lane)
                - _lane_density(lanes, outgoing_lane)
                for incoming_lane, outgoing_lane in pairs
            )
            mapped_scores.append((int(action_index), float(score)))

        if not mapped_scores:
            actions[agent_index] = (
                int(current_action)
                if current_action is not None
                and bool(action_masks[agent_index, current_action])
                else int(valid_actions[0])
            )
            continue

        best_score = max(score for _, score in mapped_scores)
        best_actions = [
            action_index
            for action_index, score in mapped_scores
            if math.isclose(score, best_score, rel_tol=1e-9, abs_tol=1e-12)
        ]
        # Avoid needless switches when the current phase is tied for best.
        actions[agent_index] = (
            int(current_action)
            if current_action in best_actions
            else int(best_actions[0])
        )
    return actions


def _prime_reward_baseline(payload: Dict[str, Any]) -> None:
    global _prev_pressure
    intersections = payload.get("intersections", {})
    _prev_pressure = {
        tid: _signed_lane_pressure(intersections, tid) for tid in _tls_order
    }


def compute_reward(
    payload: Dict[str, Any], executed_actions: Optional[Dict[str, int]] = None
) -> np.ndarray:
    """Return one capacity-normalized reward per intersection."""
    global _prev_pressure, _prev_actions
    intersections = payload.get("intersections", {})
    if _reward_mode == "legacy_delta":
        signed_pressure = {
            tid: _signed_lane_pressure(intersections, tid) for tid in _tls_order
        }
        local_signal = {
            tid: _prev_pressure.get(tid, signed_pressure[tid]) - signed_pressure[tid]
            for tid in _tls_order
        }
    else:
        local_signal = {
            tid: -compute_pressure(intersections, tid) for tid in _tls_order
        }
    rewards = np.zeros(len(_tls_order), dtype=np.float32)
    for agent_index, tid in enumerate(_tls_order):
        neighbors = [
            neighbor for neighbor in _neighbors.get(tid, []) if neighbor in local_signal
        ]
        regional = (
            float(np.mean([local_signal[neighbor] for neighbor in neighbors]))
            if neighbors
            else 0.0
        )
        lanes = intersections.get(tid, {}).get("lanes", {})
        spillback = sum(
            max(
                0.0,
                _occupancy_ratio(lanes.get(lane_id, {}).get("occupancy", 0.0))
                - SPILL_THRESHOLD,
            )
            for lane_id in _outgoing.get(tid, [])
        )
        action = (executed_actions or {}).get(tid)
        switched = float(
            action is not None
            and tid in _prev_actions
            and _prev_actions[tid] != action
        )
        rewards[agent_index] = (
            local_signal[tid]
            + ETA * regional
            - LAMBDA_SPILL * spillback
            - LAMBDA_SWITCH * switched
        )

    if _reward_mode == "legacy_delta":
        _prev_pressure = signed_pressure
    if executed_actions:
        _prev_actions.update(executed_actions)
    return rewards


def _normalize_rewards(raw_rewards: np.ndarray) -> np.ndarray:
    global _reward_mean, _reward_m2, _reward_count
    for raw in np.asarray(raw_rewards, dtype=np.float32).reshape(-1):
        _reward_count += 1
        delta = float(raw) - _reward_mean
        _reward_mean += delta / _reward_count
        _reward_m2 += delta * (float(raw) - _reward_mean)
    variance = _reward_m2 / max(_reward_count - 1, 1)
    # Rewards are capacity-normalized already. Never amplify early samples.
    scale = max(math.sqrt(max(variance, 0.0)), 1.0)
    return np.clip(raw_rewards / scale, -REWARD_CLIP, REWARD_CLIP).astype(np.float32)


def _complete_pending_transition(payload: Dict[str, Any]) -> None:
    if _pending_transition is None:
        _prime_reward_baseline(payload)
        return
    raw_rewards = compute_reward(payload, _pending_transition["phases"])
    trajectory = _episode_trajectory
    trajectory["obs"].append(_pending_transition["obs"])
    trajectory["actions"].append(_pending_transition["actions"])
    trajectory["action_log_probs"].append(_pending_transition["action_log_probs"])
    trajectory["collaborators"].append(_pending_transition["collaborators"])
    trajectory["collaborator_log_probs"].append(
        _pending_transition["collaborator_log_probs"]
    )
    trajectory["values"].append(_pending_transition["values"])
    trajectory["normalized_values"].append(
        _pending_transition["normalized_values"]
    )
    trajectory["valid_action_counts"].append(
        _pending_transition["valid_action_counts"]
    )
    trajectory["action_masks"].append(_pending_transition["action_masks"])
    trajectory["phase_features"].append(
        _pending_transition["phase_features"]
    )
    end_time = float(
        payload.get("simulation_time", _pending_transition["decision_time"])
    )
    trajectory["transition_durations_s"].append(
        max(0.0, end_time - float(_pending_transition["decision_time"]))
    )
    trajectory["raw_rewards"].append(raw_rewards)
    trajectory["rewards"].append(
        _normalize_rewards(raw_rewards)
        if _mode == "train"
        else np.zeros_like(raw_rewards, dtype=np.float32)
    )


def _can_issue_joint_signal_decision(
    intersections: Dict[str, Any], simulation_time: float
) -> bool:
    """Only decide at a fixed horizon when every controller is ready."""
    missing = set(_tls_order) - set(intersections)
    if missing:
        raise ValueError(f"CoSLight observation is missing intersections: {sorted(missing)}")
    if (
        _last_joint_decision_time is not None
        and simulation_time + 1e-9
        < _last_joint_decision_time + JOINT_DECISION_INTERVAL
    ):
        return False
    for tid in _tls_order:
        intersection = intersections.get(tid, {})
        if (
            intersection.get("stage") != "GREEN"
            or intersection.get("pending_phase") is not None
            or float(intersection.get("stage_elapsed", 0.0)) + 1e-9 < _minimum_green
        ):
            return False
    return True


def _build_action_masks(
    intersections: Mapping[str, Any],
) -> Tuple[np.ndarray, set[str]]:
    """Mask padded phases and force an alternative after excessive green."""

    action_dim = max(len(order) for order in _phase_orders.values())
    masks = np.zeros((len(_tls_order), action_dim), dtype=np.bool_)
    forced: set[str] = set()
    for agent_index, tid in enumerate(_tls_order):
        phase_order = _phase_orders[tid]
        masks[agent_index, : len(phase_order)] = True
        if _max_green_factor <= 0.0 or len(phase_order) <= 1:
            continue
        intersection = intersections.get(tid, {})
        current_phase = int(intersection.get("current_phase", phase_order[0]))
        current_index = (
            phase_order.index(current_phase)
            if current_phase in phase_order
            else None
        )
        if current_index is None:
            continue
        nominal_green = _phase_durations.get(tid, {}).get(
            current_phase, DEFAULT_GREEN_DURATION
        )
        max_green = max(
            MIN_MAX_GREEN_SECONDS,
            float(nominal_green) * _max_green_factor,
        )
        if float(intersection.get("stage_elapsed", 0.0)) + 1e-9 >= max_green:
            masks[agent_index, current_index] = False
            forced.add(tid)
    return masks, forced


def _pressure_shield_action_masks(
    action_masks: np.ndarray,
    phase_features: np.ndarray,
    margin: float,
) -> np.ndarray:
    """Keep only actions close to the best legal movement pressure.

    The shield constrains the learned residual, not the SUMO safety state
    machine.  ``action_masks`` has already removed padded or max-green-forced
    phases, so the shield may only narrow that legal set and can never restore
    a forbidden action.
    """

    masks = np.asarray(action_masks, dtype=np.bool_)
    features = np.asarray(phase_features, dtype=np.float32)
    if features.ndim != 3 or features.shape[:2] != masks.shape:
        raise ValueError(
            "phase features and action masks must share [agents, actions]"
        )
    if math.isnan(margin) or margin < 0.0:
        raise ValueError("pressure shield margin must be non-negative or inf")
    shielded = masks.copy()
    if math.isinf(margin):
        return shielded

    pressure_scores = features[..., 5]
    for agent_index in range(masks.shape[0]):
        legal = np.flatnonzero(masks[agent_index])
        if legal.size == 0:
            raise RuntimeError("pressure shield received an empty legal action set")
        best_score = float(np.max(pressure_scores[agent_index, legal]))
        allowed = legal[
            pressure_scores[agent_index, legal] >= best_score - margin - 1e-9
        ]
        if allowed.size == 0:
            # The maximum itself must satisfy the bound; keep an explicit guard
            # against non-finite feature regressions.
            raise RuntimeError("pressure shield removed every legal action")
        shielded[agent_index] = False
        shielded[agent_index, allowed] = True
    return shielded


def _residual_activation_action_masks(
    action_masks: np.ndarray,
    phase_features: np.ndarray,
    reference_actions: np.ndarray,
    min_best_pressure: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Fall back to MaxPressure when the physical action signal is weak.

    This is a state-dependent residual guard, not a new traffic objective.  It
    only narrows the already legal action set and therefore cannot bypass the
    SUMO safety state machine or a pressure-shield mask.
    """

    masks = np.asarray(action_masks, dtype=np.bool_)
    features = np.asarray(phase_features, dtype=np.float32)
    references = np.asarray(reference_actions, dtype=np.int64)
    if features.ndim != 3 or features.shape[:2] != masks.shape:
        raise ValueError(
            "phase features and action masks must share [agents, actions]"
        )
    if references.shape != (masks.shape[0],):
        raise ValueError("reference actions must contain one action per agent")
    if math.isnan(min_best_pressure):
        raise ValueError("residual minimum best pressure must not be NaN")

    guarded_masks = masks.copy()
    guarded = np.zeros(masks.shape[0], dtype=np.bool_)
    if min_best_pressure == -math.inf:
        return guarded_masks, guarded

    pressure_scores = features[..., 5]
    for agent_index in range(masks.shape[0]):
        legal = np.flatnonzero(masks[agent_index])
        if legal.size == 0:
            raise RuntimeError("residual guard received an empty legal action set")
        reference_action = int(references[agent_index])
        if reference_action not in legal:
            raise RuntimeError("MaxPressure reference action is not legal")
        best_score = float(np.max(pressure_scores[agent_index, legal]))
        if best_score <= min_best_pressure + 1e-12:
            guarded_masks[agent_index] = False
            guarded_masks[agent_index, reference_action] = True
            guarded[agent_index] = True
    return guarded_masks, guarded


def _switch_hysteresis_actions(
    selected_actions: np.ndarray,
    current_actions: np.ndarray,
    selected_log_probs: np.ndarray,
    current_log_probs: np.ndarray,
    action_masks: np.ndarray,
    margin: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Hold a legal current phase when a deterministic switch is low-confidence.

    The log-probability difference equals the selected-vs-current logit
    difference because both actions share the same masked categorical
    normalizer.  ``-inf`` is diagnostic-only and preserves the selected action.
    """

    selected = np.asarray(selected_actions, dtype=np.int64)
    current = np.asarray(current_actions, dtype=np.int64)
    selected_log = np.asarray(selected_log_probs, dtype=np.float64)
    current_log = np.asarray(current_log_probs, dtype=np.float64)
    masks = np.asarray(action_masks, dtype=np.bool_)
    expected = (masks.shape[0],)
    if not all(
        array.shape == expected
        for array in (selected, current, selected_log, current_log)
    ):
        raise ValueError("switch hysteresis inputs must contain one value per agent")
    if (
        math.isnan(margin)
        or margin == math.inf
        or (math.isfinite(margin) and margin < 0.0)
    ):
        raise ValueError("switch logit margin must be -inf or finite and non-negative")

    candidates = np.zeros(expected, dtype=np.bool_)
    for agent_index, current_action in enumerate(current):
        if (
            0 <= current_action < masks.shape[1]
            and bool(masks[agent_index, current_action])
            and int(selected[agent_index]) != int(current_action)
        ):
            candidates[agent_index] = True
    gaps = selected_log - current_log
    if not np.isfinite(gaps[candidates]).all():
        raise ValueError("switch candidate logit gaps must be finite")
    held = (
        candidates & (gaps <= margin + 1e-12)
        if math.isfinite(margin)
        else np.zeros(expected, dtype=np.bool_)
    )
    final_actions = selected.copy()
    final_actions[held] = current[held]
    return final_actions, candidates, held, gaps


def _apply_deterministic_switch_hysteresis(
    observations: np.ndarray,
    valid_counts: np.ndarray,
    action_masks: np.ndarray,
    phase_features: np.ndarray,
    intersections: Mapping[str, Any],
    policy_output: PolicyOutput,
    simulation_time: float,
    pressure_prior_weights: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Apply and record the inference-only switch-confidence experiment."""

    if _model is None:
        raise RuntimeError("CoSLight model is not initialized")
    selected = (
        policy_output.actions.squeeze(0).detach().cpu().numpy().astype(np.int64)
    )
    current = selected.copy()
    for agent_index, tid in enumerate(_tls_order):
        current_phase = int(
            intersections.get(tid, {}).get(
                "current_phase", _phase_orders[tid][int(selected[agent_index])]
            )
        )
        if current_phase in _phase_orders[tid]:
            current[agent_index] = _phase_orders[tid].index(current_phase)
        else:
            current[agent_index] = -1

    evaluation_actions = current.copy()
    evaluation_actions[evaluation_actions < 0] = selected[evaluation_actions < 0]
    evaluation_kwargs = {}
    if pressure_prior_weights is not None:
        evaluation_kwargs["pressure_prior_weights"] = torch.from_numpy(
            pressure_prior_weights
        ).unsqueeze(0)
    with torch.no_grad():
        current_output = _model.evaluate_actions(
            torch.from_numpy(observations).unsqueeze(0).float(),
            torch.from_numpy(evaluation_actions).unsqueeze(0),
            torch.from_numpy(valid_counts),
            policy_output.collaborators,
            action_masks=torch.from_numpy(action_masks),
            phase_features=torch.from_numpy(phase_features).unsqueeze(0),
            **evaluation_kwargs,
        )
    final_actions, candidates, held, gaps = _switch_hysteresis_actions(
        selected,
        current,
        policy_output.action_log_probs.squeeze(0).detach().cpu().numpy(),
        current_output.action_log_probs.squeeze(0).detach().cpu().numpy(),
        action_masks,
        _switch_logit_margin,
    )

    _switch_hysteresis_stats["agent_decisions"] += len(_tls_order)
    _switch_hysteresis_stats["switch_candidates"] += int(candidates.sum())
    _switch_hysteresis_stats["held_switches"] += int(held.sum())
    recorded_gaps = _switch_hysteresis_stats["logit_gaps"]
    for agent_index, tid in enumerate(_tls_order):
        per_intersection = _switch_hysteresis_stats["per_intersection"][tid]
        per_intersection["agent_decisions"] += 1
        if not bool(candidates[agent_index]):
            continue
        gap = float(gaps[agent_index])
        recorded_gaps.append(gap)
        per_intersection["switch_candidates"] += 1
        per_intersection["logit_gap_total"] += gap
        per_intersection["logit_gap_max"] = max(
            float(per_intersection["logit_gap_max"]), gap
        )
        if not bool(held[agent_index]):
            continue
        _switch_hysteresis_stats["per_intersection"][tid]["held_switches"] += 1
        events = _switch_hysteresis_stats["held_events"]
        if len(events) < 200:
            events.append(
                {
                    "time_s": float(simulation_time),
                    "tls_id": tid,
                    "selected_phase": int(
                        _phase_orders[tid][int(selected[agent_index])]
                    ),
                    "held_phase": int(
                        _phase_orders[tid][int(current[agent_index])]
                    ),
                    "logit_gap": gap,
                }
            )
    return final_actions


def _record_pressure_shield_decision(
    base_masks: np.ndarray,
    shielded_masks: np.ndarray,
    phase_features: np.ndarray,
    selected_actions: np.ndarray,
    reference_actions: Optional[np.ndarray] = None,
    simulation_time: Optional[float] = None,
    residual_guarded: Optional[np.ndarray] = None,
) -> None:
    pressure_scores = np.asarray(phase_features, dtype=np.float32)[..., 5]
    selected = np.asarray(selected_actions, dtype=np.int64)
    _pressure_shield_stats["decisions"] += 1
    _pressure_shield_stats["candidate_actions"] += int(base_masks.sum())
    _pressure_shield_stats["allowed_actions"] += int(shielded_masks.sum())
    _pressure_shield_stats["filtered_actions"] += int(
        base_masks.sum() - shielded_masks.sum()
    )
    regrets = _pressure_shield_stats["selected_regrets"]
    for agent_index, selected_action in enumerate(selected):
        legal = np.flatnonzero(base_masks[agent_index])
        if legal.size == 0 or not bool(shielded_masks[agent_index, selected_action]):
            raise RuntimeError("policy selected an action outside the shield")
        best_score = float(np.max(pressure_scores[agent_index, legal]))
        regret = max(
            0.0,
            best_score - float(pressure_scores[agent_index, selected_action]),
        )
        regrets.append(regret)
        tid = _tls_order[agent_index]
        per_intersection = _pressure_shield_stats["per_intersection"][tid]
        per_intersection["decisions"] += 1
        if residual_guarded is not None and bool(residual_guarded[agent_index]):
            _pressure_shield_stats["residual_guarded_agents"] += 1
            per_intersection["residual_guarded_count"] += 1
        per_intersection["selected_regret_total"] += regret
        per_intersection["selected_regret_max"] = max(
            per_intersection["selected_regret_max"], regret
        )
        if regret > 1e-9:
            _pressure_shield_stats["positive_regret_count"] += 1
            per_intersection["positive_regret_count"] += 1

        if reference_actions is None:
            continue
        reference_action = int(reference_actions[agent_index])
        if int(selected_action) == reference_action:
            continue
        _pressure_shield_stats["reference_disagreements"] += 1
        per_intersection["reference_disagreements"] += 1
        if regret <= 1e-9:
            per_intersection["zero_regret_reference_disagreements"] += 1
        events = _pressure_shield_stats["reference_disagreement_events"]
        if len(events) < 100:
            events.append(
                {
                    "time_s": (
                        float(simulation_time)
                        if simulation_time is not None
                        else None
                    ),
                    "tls_id": tid,
                    "selected_phase": int(
                        _phase_orders[tid][int(selected_action)]
                    ),
                    "reference_phase": int(
                        _phase_orders[tid][reference_action]
                    ),
                    "best_pressure": float(best_score),
                    "selected_pressure": float(
                        pressure_scores[agent_index, selected_action]
                    ),
                    "reference_pressure": float(
                        pressure_scores[agent_index, reference_action]
                    ),
                    "pressure_regret": float(regret),
                }
            )


def _observe_signal_execution(
    intersections: Mapping[str, Any], simulation_time: float
) -> None:
    """Infer command execution from later Protocol 2.0 phase observations."""

    for raw_tid, intersection in intersections.items():
        tid = str(raw_tid)
        if tid in _signal_execution_stats and intersection.get("stage") == "GREEN":
            current_phase = int(intersection.get("current_phase", -1))
            previous_phase = _observed_phase_by_tls.get(tid)
            if previous_phase is None:
                _observed_phase_by_tls[tid] = current_phase
            elif current_phase != previous_phase:
                stage_elapsed = max(
                    float(intersection.get("stage_elapsed", 0.0)), 0.0
                )
                _last_observed_phase_changes[tid] = {
                    # Protocol callbacks are discrete.  ``stage_elapsed`` gives
                    # a closer estimate than the callback timestamp when the
                    # new green began between two observations.
                    "time_s": max(float(simulation_time) - stage_elapsed, 0.0),
                    "observed_at_s": float(simulation_time),
                    "from_phase": int(previous_phase),
                    "to_phase": current_phase,
                }
                _observed_phase_by_tls[tid] = current_phase
            stats = _signal_execution_stats[tid]
            stats["max_observed_green_s"] = max(
                float(stats["max_observed_green_s"]),
                float(intersection.get("stage_elapsed", 0.0)),
            )
    for tid, pending in list(_pending_signal_commands.items()):
        intersection = intersections.get(tid, {})
        current_phase = int(intersection.get("current_phase", -1))
        if (
            intersection.get("stage") == "GREEN"
            and current_phase == int(pending["target_phase"])
        ):
            delay = max(simulation_time - float(pending["requested_at"]), 0.0)
            stats = _signal_execution_stats[tid]
            stats["observed_changes"] += 1.0
            stats["change_delay_total_s"] += delay
            stats["change_delay_max_s"] = max(
                stats["change_delay_max_s"], delay
            )
            del _pending_signal_commands[tid]


def _observe_vehicle_braking(payload: Mapping[str, Any], simulation_time: float) -> None:
    """Attribute Protocol 2.0 braking events to observed signal changes.

    This is diagnostic-only: it reads vehicle observations after SUMO has
    advanced and never changes signal/vehicle actions, rewards, or rollouts.
    Buckets are cumulative so one event may appear in several time/distance
    thresholds.  Rates therefore compare events with vehicle-observation
    exposure inside the same bucket.
    """

    traffic = payload.get("traffic", {}) or {}
    _vehicle_braking_stats["latest_traffic_hard_braking_total"] = max(
        int(traffic.get("hard_braking_events", 0) or 0), 0
    )
    for vehicle_id, raw_vehicle in (payload.get("vehicles", {}) or {}).items():
        vehicle = raw_vehicle or {}
        _vehicle_braking_stats["vehicle_observations"] += 1
        driving_events = vehicle.get("driving_events", {}) or {}
        event_count = max(
            int(driving_events.get("hard_braking_since_last_decision", 0) or 0),
            0,
        )
        _vehicle_braking_stats["hard_braking_events"] += event_count

        next_signal = vehicle.get("next_signal") or {}
        tid = str(
            next_signal.get("intersection_id")
            or next_signal.get("tls_id")
            or ""
        )
        per_intersection = _vehicle_braking_stats["per_intersection"].get(tid)
        if per_intersection is None:
            _vehicle_braking_stats["unattributed_events"] += event_count
            continue

        _vehicle_braking_stats["attributed_vehicle_observations"] += 1
        _vehicle_braking_stats["attributed_events"] += event_count
        per_intersection["vehicle_observations"] += 1
        per_intersection["events"] += event_count

        phase_change = _last_observed_phase_changes.get(tid)
        age_s: Optional[float] = None
        if phase_change is not None:
            candidate_age = float(simulation_time) - float(phase_change["time_s"])
            if candidate_age >= 0.0:
                age_s = candidate_age

        distance_m: Optional[float] = None
        try:
            candidate_distance = float(next_signal.get("distance_m"))
        except (TypeError, ValueError):
            candidate_distance = math.nan
        if math.isfinite(candidate_distance) and candidate_distance >= 0.0:
            distance_m = candidate_distance

        for window in BRAKING_ATTRIBUTION_WINDOWS_S:
            if age_s is None or age_s > window:
                continue
            key = str(int(window))
            _vehicle_braking_stats["post_switch"][key][
                "vehicle_observations"
            ] += 1
            _vehicle_braking_stats["post_switch"][key]["events"] += event_count
            per_intersection["post_switch"][key]["vehicle_observations"] += 1
            per_intersection["post_switch"][key]["events"] += event_count

        for distance in BRAKING_DISTANCE_THRESHOLDS_M:
            if distance_m is None or distance_m > distance:
                continue
            key = str(int(distance))
            _vehicle_braking_stats["distance"][key][
                "vehicle_observations"
            ] += 1
            _vehicle_braking_stats["distance"][key]["events"] += event_count

        if (
            age_s is not None
            and age_s <= 10.0
            and distance_m is not None
            and distance_m <= 100.0
        ):
            joint_bucket = _vehicle_braking_stats["recent_10s_near_100m"]
            joint_bucket["vehicle_observations"] += 1
            joint_bucket["events"] += event_count
        if age_s is None or age_s > 30.0:
            outside_bucket = _vehicle_braking_stats["outside_30s"]
            outside_bucket["vehicle_observations"] += 1
            outside_bucket["events"] += event_count

        if event_count and len(_vehicle_braking_stats["events"]) < 200:
            motion = vehicle.get("motion", {}) or {}
            location = vehicle.get("location", {}) or {}
            _vehicle_braking_stats["events"].append(
                {
                    "time_s": float(simulation_time),
                    "vehicle_id": str(vehicle_id),
                    "tls_id": tid,
                    "events": event_count,
                    "distance_m": distance_m,
                    "time_since_phase_change_s": age_s,
                    "acceleration_mps2": motion.get("acceleration_mps2"),
                    "lane_id": location.get("lane_id"),
                    "last_change_from_phase": (
                        int(phase_change["from_phase"])
                        if phase_change is not None
                        else None
                    ),
                    "last_change_to_phase": (
                        int(phase_change["to_phase"])
                        if phase_change is not None
                        else None
                    ),
                }
            )


def _record_signal_commands(
    intersections: Mapping[str, Any],
    phases: Mapping[str, int],
    simulation_time: float,
    max_green_forced: set[str],
) -> None:
    for tid, target_phase in phases.items():
        stats = _signal_execution_stats[tid]
        stats["commands"] += 1.0
        if tid in max_green_forced:
            stats["max_green_forced_commands"] += 1.0
        phase_counts = stats["phase_commands"]
        phase_key = str(int(target_phase))
        phase_counts[phase_key] = int(phase_counts.get(phase_key, 0)) + 1
        intersection = intersections.get(tid, {})
        current_phase = int(intersection.get("current_phase", target_phase))
        if int(target_phase) == current_phase:
            continue
        stats["change_requests"] += 1.0
        if tid in _pending_signal_commands:
            stats["unresolved_changes"] += 1.0
        _pending_signal_commands[tid] = {
            "target_phase": int(target_phase),
            "requested_at": float(simulation_time),
        }


def signal_execution_diagnostics() -> dict:
    """Return algorithm-inferred signal execution metrics, never SUMO receipts."""

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
    max_green_forced = sum(
        item["max_green_forced_commands"]
        for item in _signal_execution_stats.values()
    )
    delay_total = sum(
        item["change_delay_total_s"] for item in _signal_execution_stats.values()
    )
    delay_max = max(
        (
            item["change_delay_max_s"]
            for item in _signal_execution_stats.values()
        ),
        default=0.0,
    )
    phase_dominance = [
        max(item["phase_commands"].values()) / item["commands"]
        for item in _signal_execution_stats.values()
        if item["commands"] and item["phase_commands"]
    ]
    multi_phase_stats = [
        item
        for item in _signal_execution_stats.values()
        if int(item.get("valid_phase_count", 1)) > 1
    ]
    multi_phase_dominance = [
        max(item["phase_commands"].values()) / item["commands"]
        for item in multi_phase_stats
        if item["commands"] and item["phase_commands"]
    ]
    shield_candidates = int(_pressure_shield_stats.get("candidate_actions", 0))
    shield_allowed = int(_pressure_shield_stats.get("allowed_actions", 0))
    shield_filtered = int(_pressure_shield_stats.get("filtered_actions", 0))
    selected_regrets = np.asarray(
        _pressure_shield_stats.get("selected_regrets", []), dtype=np.float64
    )
    shield_decisions = int(_pressure_shield_stats.get("decisions", 0))
    shield_agent_decisions = shield_decisions * len(_tls_order)
    shield_per_intersection = {}
    for tid, item in _pressure_shield_stats.get(
        "per_intersection", {}
    ).items():
        decisions = int(item.get("decisions", 0))
        shield_per_intersection[tid] = {
            "decisions": decisions,
            "positive_regret_count": int(
                item.get("positive_regret_count", 0)
            ),
            "reference_disagreements": int(
                item.get("reference_disagreements", 0)
            ),
            "zero_regret_reference_disagreements": int(
                item.get("zero_regret_reference_disagreements", 0)
            ),
            "residual_guarded_count": int(
                item.get("residual_guarded_count", 0)
            ),
            "selected_regret_mean": (
                float(item.get("selected_regret_total", 0.0)) / decisions
                if decisions
                else 0.0
            ),
            "selected_regret_max": float(
                item.get("selected_regret_max", 0.0)
            ),
        }
    switch_candidates = int(
        _switch_hysteresis_stats.get("switch_candidates", 0)
    )
    held_switches = int(_switch_hysteresis_stats.get("held_switches", 0))
    switch_logit_gaps = np.asarray(
        _switch_hysteresis_stats.get("logit_gaps", []), dtype=np.float64
    )
    switch_per_intersection = {}
    for tid, item in _switch_hysteresis_stats.get(
        "per_intersection", {}
    ).items():
        candidates = int(item.get("switch_candidates", 0))
        held = int(item.get("held_switches", 0))
        switch_per_intersection[tid] = {
            "agent_decisions": int(item.get("agent_decisions", 0)),
            "switch_candidates": candidates,
            "held_switches": held,
            "hold_rate": float(held / candidates) if candidates else 0.0,
            "logit_gap_mean": (
                float(item.get("logit_gap_total", 0.0)) / candidates
                if candidates
                else 0.0
            ),
            "logit_gap_max": float(item.get("logit_gap_max", 0.0)),
        }

    def braking_bucket(item: Mapping[str, Any]) -> Dict[str, float | int]:
        observations = int(item.get("vehicle_observations", 0))
        events = int(item.get("events", 0))
        return {
            "vehicle_observations": observations,
            "events": events,
            "events_per_1000_vehicle_observations": (
                1000.0 * events / observations if observations else 0.0
            ),
        }

    braking_observed = int(_vehicle_braking_stats.get("hard_braking_events", 0))
    braking_total = int(
        _vehicle_braking_stats.get("latest_traffic_hard_braking_total", 0)
    )
    braking_per_intersection = {}
    for tid, item in _vehicle_braking_stats.get("per_intersection", {}).items():
        braking_per_intersection[tid] = {
            **braking_bucket(item),
            "post_switch": {
                key: braking_bucket(bucket)
                for key, bucket in item.get("post_switch", {}).items()
            },
        }
    return {
        "source": "inferred_from_phase_observations",
        "commands": int(commands),
        "change_requests": int(requested),
        "observed_changes": int(observed),
        "unresolved_changes": int(unresolved),
        "max_green_forced_commands": int(max_green_forced),
        "change_execution_rate": float(observed / requested) if requested else 1.0,
        "mean_change_delay_s": float(delay_total / observed) if observed else 0.0,
        "max_change_delay_s": float(delay_max),
        "max_observed_green_s": max(
            (
                float(item["max_observed_green_s"])
                for item in _signal_execution_stats.values()
            ),
            default=0.0,
        ),
        "mean_phase_dominance": (
            float(np.mean(phase_dominance)) if phase_dominance else 0.0
        ),
        "max_phase_dominance": max(phase_dominance, default=0.0),
        # A one-phase signal can legally remain green for the whole episode.
        # Keep the all-signal fields above for raw observability, but use these
        # multi-phase fields to diagnose phase starvation.
        "multi_phase_max_observed_green_s": max(
            (
                float(item["max_observed_green_s"])
                for item in multi_phase_stats
            ),
            default=0.0,
        ),
        "multi_phase_mean_phase_dominance": (
            float(np.mean(multi_phase_dominance))
            if multi_phase_dominance
            else 0.0
        ),
        "multi_phase_max_phase_dominance": max(
            multi_phase_dominance, default=0.0
        ),
        "pressure_shield_margin": (
            float(_pressure_shield_margin)
            if math.isfinite(_pressure_shield_margin)
            else None
        ),
        "residual_min_best_pressure": (
            float(_residual_min_best_pressure)
            if math.isfinite(_residual_min_best_pressure)
            else None
        ),
        "switch_logit_margin": (
            float(_switch_logit_margin)
            if math.isfinite(_switch_logit_margin)
            else None
        ),
        "cloud": (
            _cloud_coordinator.diagnostics()
            if _cloud_coordinator is not None
            else {"mode": "off"}
        ),
        "switch_hysteresis_agent_decisions": int(
            _switch_hysteresis_stats.get("agent_decisions", 0)
        ),
        "switch_confidence_candidates": switch_candidates,
        "switch_hysteresis_held": held_switches,
        "switch_hysteresis_hold_rate": (
            float(held_switches / switch_candidates)
            if switch_candidates
            else 0.0
        ),
        "switch_logit_gap_mean": (
            float(switch_logit_gaps.mean()) if switch_logit_gaps.size else 0.0
        ),
        "switch_logit_gap_p50": (
            float(np.percentile(switch_logit_gaps, 50))
            if switch_logit_gaps.size
            else 0.0
        ),
        "switch_logit_gap_p95": (
            float(np.percentile(switch_logit_gaps, 95))
            if switch_logit_gaps.size
            else 0.0
        ),
        "switch_logit_gap_max": (
            float(switch_logit_gaps.max()) if switch_logit_gaps.size else 0.0
        ),
        "switch_hysteresis_events": copy.deepcopy(
            _switch_hysteresis_stats.get("held_events", [])
        ),
        "switch_hysteresis_per_intersection": switch_per_intersection,
        "hard_braking_attribution_source": (
            "protocol2_vehicle_events_next_signal_and_observed_green_phase"
        ),
        "vehicle_observations": int(
            _vehicle_braking_stats.get("vehicle_observations", 0)
        ),
        "vehicle_hard_braking_events_observed": braking_observed,
        "traffic_hard_braking_total": braking_total,
        "vehicle_hard_braking_event_coverage": (
            float(braking_observed / braking_total) if braking_total else 1.0
        ),
        "hard_braking_attributed_vehicle_observations": int(
            _vehicle_braking_stats.get("attributed_vehicle_observations", 0)
        ),
        "hard_braking_attributed_events": int(
            _vehicle_braking_stats.get("attributed_events", 0)
        ),
        "hard_braking_unattributed_events": int(
            _vehicle_braking_stats.get("unattributed_events", 0)
        ),
        "hard_braking_post_switch": {
            key: braking_bucket(item)
            for key, item in _vehicle_braking_stats.get("post_switch", {}).items()
        },
        "hard_braking_by_next_signal_distance": {
            key: braking_bucket(item)
            for key, item in _vehicle_braking_stats.get("distance", {}).items()
        },
        "hard_braking_recent_10s_near_100m": braking_bucket(
            _vehicle_braking_stats.get("recent_10s_near_100m", {})
        ),
        "hard_braking_outside_30s": braking_bucket(
            _vehicle_braking_stats.get("outside_30s", {})
        ),
        "hard_braking_per_intersection": braking_per_intersection,
        "hard_braking_attribution_events": copy.deepcopy(
            _vehicle_braking_stats.get("events", [])
        ),
        "pressure_shield_decisions": shield_decisions,
        "pressure_shield_candidate_actions": shield_candidates,
        "pressure_shield_allowed_actions": shield_allowed,
        "pressure_shield_filtered_actions": shield_filtered,
        "pressure_shield_filter_rate": (
            float(shield_filtered / shield_candidates)
            if shield_candidates
            else 0.0
        ),
        "pressure_shield_mean_allowed_actions": (
            float(shield_allowed / max(len(_tls_order), 1))
            / max(int(_pressure_shield_stats.get("decisions", 0)), 1)
        ),
        "selected_pressure_regret_mean": (
            float(selected_regrets.mean()) if selected_regrets.size else 0.0
        ),
        "selected_pressure_regret_p95": (
            float(np.percentile(selected_regrets, 95))
            if selected_regrets.size
            else 0.0
        ),
        "selected_pressure_regret_max": (
            float(selected_regrets.max()) if selected_regrets.size else 0.0
        ),
        "selected_pressure_regret_positive_count": int(
            _pressure_shield_stats.get("positive_regret_count", 0)
        ),
        "maxpressure_reference_disagreement_count": int(
            _pressure_shield_stats.get("reference_disagreements", 0)
        ),
        "maxpressure_reference_disagreement_rate": (
            float(_pressure_shield_stats.get("reference_disagreements", 0))
            / shield_agent_decisions
            if shield_agent_decisions
            else 0.0
        ),
        "maxpressure_reference_disagreement_events": copy.deepcopy(
            _pressure_shield_stats.get("reference_disagreement_events", [])
        ),
        "residual_guarded_agent_decisions": int(
            _pressure_shield_stats.get("residual_guarded_agents", 0)
        ),
        "residual_guard_rate": (
            float(_pressure_shield_stats.get("residual_guarded_agents", 0))
            / shield_agent_decisions
            if shield_agent_decisions
            else 0.0
        ),
        "pressure_shield_per_intersection": shield_per_intersection,
        "per_intersection": copy.deepcopy(_signal_execution_stats),
    }


def _training_episode_duration_s() -> Optional[float]:
    raw_duration = os.environ.get("COSLIGHT_EPISODE_DURATION")
    if raw_duration is None:
        return None
    duration = float(raw_duration)
    if not math.isfinite(duration) or duration <= 0.0:
        raise ValueError("COSLIGHT_EPISODE_DURATION must be finite and positive")
    return duration


def _configured_ppo_epochs() -> int:
    """Return the explicit fine-tuning epoch count, preserving V16 by default."""

    raw_value = os.environ.get("COSLIGHT_PPO_EPOCHS")
    if raw_value is None:
        return PPO_EPOCHS
    try:
        epoch_count = int(raw_value)
    except ValueError as exc:
        raise ValueError("COSLIGHT_PPO_EPOCHS must be a positive integer") from exc
    if epoch_count < 1:
        raise ValueError("COSLIGHT_PPO_EPOCHS must be a positive integer")
    return epoch_count


def step(payload: dict) -> dict:
    global _pending_transition, _last_joint_decision_time
    global _latest_observation_time
    global _terminal_bootstrap_values, _horizon_guard_active
    global _horizon_guarded_agents

    intersections = payload.get("intersections", {})
    simulation_time = float(payload.get("simulation_time", 0.0))
    _latest_observation_time = simulation_time
    _observe_signal_execution(intersections, simulation_time)
    _observe_vehicle_braking(payload, simulation_time)
    if _horizon_guard_active:
        vehicle_actions = (
            _build_vehicle_actions(payload)
            if os.environ.get("COSLIGHT_VEHICLE_GUIDANCE", "off").lower() == "rule"
            else {}
        )
        return {
            "protocol_version": "2.0",
            "episode_id": payload["episode_id"],
            "step_id": payload["step_id"],
            "actions": {"signals": {}, "vehicles": vehicle_actions},
        }
    if not _can_issue_joint_signal_decision(intersections, simulation_time):
        vehicle_actions = (
            _build_vehicle_actions(payload)
            if os.environ.get("COSLIGHT_VEHICLE_GUIDANCE", "off").lower() == "rule"
            else {}
        )
        return {
            "protocol_version": "2.0",
            "episode_id": payload["episode_id"],
            "step_id": payload["step_id"],
            "actions": {"signals": {}, "vehicles": vehicle_actions},
        }

    observations = build_state(intersections)
    if _mode in {"train", "collect"}:
        _complete_pending_transition(payload)
        episode_duration = _training_episode_duration_s()
        if (
            episode_duration is not None
            and simulation_time + JOINT_DECISION_INTERVAL
            > episode_duration + 1e-6
        ):
            if _model is None:
                raise RuntimeError("CoSLight model is not initialized")
            with torch.no_grad():
                normalized_bootstrap = _model.values(
                    torch.from_numpy(observations).unsqueeze(0).float()
                )
            _terminal_bootstrap_values = (
                _value_normalizer.denormalize(normalized_bootstrap)
                .squeeze(0)
                .cpu()
                .numpy()
                .astype(np.float32)
            )
            _pending_transition = None
            _horizon_guard_active = True
            _horizon_guarded_agents = len(_tls_order)
            _last_joint_decision_time = simulation_time
            vehicle_actions = (
                _build_vehicle_actions(payload)
                if os.environ.get("COSLIGHT_VEHICLE_GUIDANCE", "off").lower()
                == "rule"
                else {}
            )
            return {
                "protocol_version": "2.0",
                "episode_id": payload["episode_id"],
                "step_id": payload["step_id"],
                "actions": {"signals": {}, "vehicles": vehicle_actions},
            }

    valid_counts = np.asarray(
        [len(_phase_orders[tid]) for tid in _tls_order], dtype=np.int64
    )
    base_action_masks, max_green_forced = _build_action_masks(intersections)
    action_masks = base_action_masks
    if _mode == "random":
        action_indices = np.asarray(
            [
                int(np.random.choice(np.flatnonzero(mask)))
                for mask in action_masks
            ],
            dtype=np.int64,
        )
        policy_output = None
    elif _mode == "max_pressure":
        action_indices = _max_pressure_action_indices(
            intersections, action_masks
        )
        policy_output = None
    elif _mode == "fixed":
        action_indices = np.asarray(
            [int(np.flatnonzero(mask)[0]) for mask in action_masks],
            dtype=np.int64,
        )
        policy_output = None
    else:
        if _model is None:
            raise RuntimeError("CoSLight model is not initialized")
        if _state_builder is None:
            raise RuntimeError("CoSLight state builder is not initialized")
        phase_features = _state_builder.build_phase_features(
            intersections, _tls_order, _model.act_dim
        )
        pressure_prior_weights = None
        shadow_prior_weights = None
        if _cloud_coordinator is not None:
            if _cloud_mode == "regional_rule":
                pressure_prior_weights = _cloud_coordinator.phase_weights(
                    intersections, phase_features, simulation_time
                )
            elif _cloud_mode == "platoon_control":
                pressure_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections, phase_features, simulation_time
                )
            elif _cloud_mode == "platoon_shadow":
                shadow_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections, phase_features, simulation_time
                )
            elif _cloud_mode == "platoon_hold_shadow":
                shadow_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections,
                    phase_features,
                    simulation_time,
                    hold_current_only=True,
                )
            elif _cloud_mode == "platoon_hold_control":
                pressure_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections,
                    phase_features,
                    simulation_time,
                    hold_current_only=True,
                )
            elif _cloud_mode == "platoon_hold_safe_shadow":
                shadow_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections,
                    phase_features,
                    simulation_time,
                    hold_current_only=True,
                    require_positive_pressure=True,
                )
            elif _cloud_mode == "platoon_hold_safe_control":
                pressure_prior_weights = _cloud_coordinator.platoon_shadow_weights(
                    intersections,
                    phase_features,
                    simulation_time,
                    hold_current_only=True,
                    require_positive_pressure=True,
                )
        reference_actions = _max_pressure_action_indices(
            intersections, base_action_masks
        )
        action_masks = _pressure_shield_action_masks(
            base_action_masks,
            phase_features,
            _pressure_shield_margin,
        )
        action_masks, residual_guarded = _residual_activation_action_masks(
            action_masks,
            phase_features,
            reference_actions,
            _residual_min_best_pressure,
        )
        policy_kwargs = {}
        if pressure_prior_weights is not None:
            policy_kwargs["pressure_prior_weights"] = torch.from_numpy(
                pressure_prior_weights
            ).unsqueeze(0)
        with torch.no_grad():
            policy_output = _model.act(
                torch.from_numpy(observations).unsqueeze(0).float(),
                torch.from_numpy(valid_counts),
                deterministic=_mode in {"model", "untrained"},
                action_masks=torch.from_numpy(action_masks),
                phase_features=torch.from_numpy(phase_features).unsqueeze(0),
                **policy_kwargs,
            )
            if pressure_prior_weights is not None and _mode == "model":
                baseline_output = _model.act(
                    torch.from_numpy(observations).unsqueeze(0).float(),
                    torch.from_numpy(valid_counts),
                    deterministic=True,
                    action_masks=torch.from_numpy(action_masks),
                    phase_features=torch.from_numpy(phase_features).unsqueeze(0),
                )
                _cloud_coordinator.record_counterfactual_actions(
                    baseline_output.actions.squeeze(0).cpu().numpy(),
                    policy_output.actions.squeeze(0).cpu().numpy(),
                    simulation_time,
                    baseline_logits=baseline_output.action_logits.squeeze(0)
                    .cpu()
                    .numpy(),
                    cloud_logits=policy_output.action_logits.squeeze(0)
                    .cpu()
                    .numpy(),
                    action_masks=action_masks,
                    pressure_prior_weights=pressure_prior_weights,
                    phase_features=phase_features,
                )
            elif shadow_prior_weights is not None and _mode == "model":
                shadow_output = _model.act(
                    torch.from_numpy(observations).unsqueeze(0).float(),
                    torch.from_numpy(valid_counts),
                    deterministic=True,
                    action_masks=torch.from_numpy(action_masks),
                    phase_features=torch.from_numpy(phase_features).unsqueeze(0),
                    pressure_prior_weights=torch.from_numpy(
                        shadow_prior_weights
                    ).unsqueeze(0),
                )
                _cloud_coordinator.record_counterfactual_actions(
                    policy_output.actions.squeeze(0).cpu().numpy(),
                    shadow_output.actions.squeeze(0).cpu().numpy(),
                    simulation_time,
                    baseline_logits=policy_output.action_logits.squeeze(0)
                    .cpu()
                    .numpy(),
                    cloud_logits=shadow_output.action_logits.squeeze(0)
                    .cpu()
                    .numpy(),
                    action_masks=action_masks,
                    pressure_prior_weights=shadow_prior_weights,
                    phase_features=phase_features,
                )
        action_indices = policy_output.actions.squeeze(0).cpu().numpy().astype(np.int64)
        if _mode in {"model", "untrained"}:
            action_indices = _apply_deterministic_switch_hysteresis(
                observations,
                valid_counts,
                action_masks,
                phase_features,
                intersections,
                policy_output,
                simulation_time,
                pressure_prior_weights,
            )
        _record_pressure_shield_decision(
            base_action_masks,
            action_masks,
            phase_features,
            action_indices,
            reference_actions=reference_actions,
            simulation_time=simulation_time,
            residual_guarded=residual_guarded,
        )

    phases = {
        tid: _phase_orders[tid][int(action_indices[agent_index])]
        for agent_index, tid in enumerate(_tls_order)
    }
    signals = {
        tid: {"target_phase": phase} for tid, phase in phases.items()
    }
    _record_signal_commands(
        intersections, phases, simulation_time, max_green_forced
    )
    _last_joint_decision_time = simulation_time

    if _mode in {"train", "collect"} and policy_output is not None:
        _pending_transition = {
            "obs": observations.copy(),
            "actions": action_indices.copy(),
            "action_log_probs": policy_output.action_log_probs.squeeze(0).cpu().numpy(),
            "collaborators": policy_output.collaborators.squeeze(0).cpu().numpy(),
            "collaborator_log_probs": (
                policy_output.collaborator_log_probs.squeeze(0).cpu().numpy()
            ),
            "values": (
                _value_normalizer.denormalize(policy_output.values)
                .squeeze(0)
                .cpu()
                .numpy()
            ),
            "normalized_values": (
                policy_output.values.squeeze(0).cpu().numpy()
            ),
            "valid_action_counts": valid_counts.copy(),
            "action_masks": action_masks.copy(),
            "phase_features": phase_features.copy(),
            "phases": phases,
            "decision_time": simulation_time,
        }

    vehicle_actions = (
        _build_vehicle_actions(payload)
        if os.environ.get("COSLIGHT_VEHICLE_GUIDANCE", "off").lower() == "rule"
        else {}
    )
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signals, "vehicles": vehicle_actions},
    }


def finish(payload: dict) -> None:
    global _episode, _episode_trajectory, _pending_transition
    global _last_joint_decision_time
    global _latest_observation_time
    global _terminal_bootstrap_values, _horizon_guard_active
    global _horizon_guarded_agents
    global _prev_pressure, _prev_actions
    global _pending_signal_commands, _signal_execution_stats
    global _collected_rollout

    reset_lane_state()
    if _mode not in {"train", "collect"} or _model is None:
        return

    # A stopped session may have been interrupted mid-transition; only a normal
    # completed horizon is safe for training.
    keep_rollout = payload.get("reason") == "completed"
    sample_count = len(_episode_trajectory["obs"])
    pending_decision_time = (
        float(_pending_transition.get("decision_time", 0.0))
        if _pending_transition is not None
        else 0.0
    )
    finish_time = float(
        payload.get(
            "simulation_time",
            _latest_observation_time
            if _latest_observation_time is not None
            else pending_decision_time,
        )
    )
    terminal_pending_age_s = (
        max(0.0, finish_time - pending_decision_time)
        if _pending_transition is not None
        else 0.0
    )
    # A decision made exactly at the horizon has no environment time in which
    # to execute. It is a bootstrap state/action, not a dropped PPO transition.
    terminal_unexecuted_action_agents = (
        len(_tls_order)
        if _pending_transition is not None and terminal_pending_age_s <= 1e-6
        else 0
    )
    pending_dropped = (
        len(_tls_order)
        if _pending_transition is not None and terminal_pending_age_s > 1e-6
        else 0
    )
    pending_values = (
        np.asarray(_pending_transition["values"], dtype=np.float32)
        if _pending_transition is not None
        else (
            np.asarray(_terminal_bootstrap_values, dtype=np.float32)
            if _terminal_bootstrap_values is not None
            else np.zeros(len(_tls_order), dtype=np.float32)
        )
    )
    _episode_trajectory["last_values"] = pending_values
    episode_data = {
        key: (
            np.asarray(value)
            if isinstance(value, list)
            else np.asarray(value, dtype=np.float32)
        )
        for key, value in _episode_trajectory.items()
    }
    execution_diagnostics = signal_execution_diagnostics()
    if sample_count and keep_rollout and _mode == "collect":
        if _collector_metadata is None or _collector_policy_generation is None:
            raise RuntimeError("Collector metadata or policy generation is unavailable")
        _collected_rollout = {
            "trajectory": copy.deepcopy(episode_data),
            "sample_count": sample_count,
            "pending_dropped": pending_dropped,
            "terminal_unexecuted_action_agents": terminal_unexecuted_action_agents,
            "terminal_pending_age_s": terminal_pending_age_s,
            "terminal_horizon_guarded_agents": _horizon_guarded_agents,
            "metadata": copy.deepcopy(_collector_metadata),
            "policy_state": export_policy_state(),
            "value_normalization": export_value_stats(),
            "policy_generation": _collector_policy_generation,
            "reward_mode": _reward_mode,
            "max_green_factor": _max_green_factor,
            "phase_feature_schema": PHASE_FEATURE_SCHEMA,
            "policy_architecture": POLICY_ARCHITECTURE,
            "collaboration_schema": COLLABORATION_SCHEMA,
            "signal_execution": execution_diagnostics,
        }
    elif sample_count and keep_rollout:
        _buffer_episodes.append(episode_data)
    elif sample_count:
        logger.warning(
            "Discarding incomplete CoSLight rollout: reason=%r joint_steps=%d",
            payload.get("reason"),
            sample_count,
        )

    raw = np.asarray(_episode_trajectory["raw_rewards"], dtype=np.float32)
    if _mode == "collect":
        logger.info(
            "Parallel rollout complete: generation=%d joint_steps=%d "
            "agent_samples=%d pending_dropped=%d terminal_unexecuted=%d "
            "horizon_guarded=%d "
            "pending_age=%.1fs transition_dt=%.1f±%.1fs raw_reward=%.4f±%.4f "
            "switch_applied=%d/%d delay=%.1fs unresolved=%d forced_max_green=%d "
            "multi_phase_max_green=%.1fs multi_phase_dominance=%.3f/%.3f",
            int(_collector_policy_generation or 0),
            sample_count,
            sample_count * len(_tls_order),
            pending_dropped,
            terminal_unexecuted_action_agents,
            _horizon_guarded_agents,
            terminal_pending_age_s,
            float(np.mean(_episode_trajectory["transition_durations_s"]))
            if _episode_trajectory["transition_durations_s"]
            else 0.0,
            float(np.std(_episode_trajectory["transition_durations_s"]))
            if _episode_trajectory["transition_durations_s"]
            else 0.0,
            float(raw.mean()) if raw.size else 0.0,
            float(raw.std()) if raw.size else 0.0,
            execution_diagnostics["observed_changes"],
            execution_diagnostics["change_requests"],
            execution_diagnostics["mean_change_delay_s"],
            execution_diagnostics["unresolved_changes"],
            execution_diagnostics["max_green_forced_commands"],
            execution_diagnostics["multi_phase_max_observed_green_s"],
            execution_diagnostics["multi_phase_mean_phase_dominance"],
            execution_diagnostics["multi_phase_max_phase_dominance"],
        )
        _episode_trajectory = _new_trajectory()
        _pending_transition = None
        _last_joint_decision_time = None
        _latest_observation_time = None
        _terminal_bootstrap_values = None
        _horizon_guard_active = False
        _horizon_guarded_agents = 0
        _prev_pressure = {}
        _prev_actions = {}
        _pending_signal_commands = {}
        _signal_execution_stats = {}
        return

    _episode += 1
    logger.info(
        "EP%d complete: departed=%d arrived=%d joint_steps=%d agent_samples=%d "
        "pending_dropped=%d terminal_unexecuted=%d horizon_guarded=%d "
        "pending_age=%.1fs "
        "transition_dt=%.1f±%.1fs raw_reward=%.4f±%.4f "
        "switch_applied=%d/%d delay=%.1fs unresolved=%d forced_max_green=%d "
        "multi_phase_max_green=%.1fs multi_phase_dominance=%.3f/%.3f",
        _episode,
        int(payload.get("departed_vehicles", 0)),
        int(payload.get("arrived_vehicles", 0)),
        sample_count,
        sample_count * len(_tls_order),
        pending_dropped,
        terminal_unexecuted_action_agents,
        _horizon_guarded_agents,
        terminal_pending_age_s,
        float(np.mean(_episode_trajectory["transition_durations_s"]))
        if _episode_trajectory["transition_durations_s"]
        else 0.0,
        float(np.std(_episode_trajectory["transition_durations_s"]))
        if _episode_trajectory["transition_durations_s"]
        else 0.0,
        float(raw.mean()) if raw.size else 0.0,
        float(raw.std()) if raw.size else 0.0,
        execution_diagnostics["observed_changes"],
        execution_diagnostics["change_requests"],
        execution_diagnostics["mean_change_delay_s"],
        execution_diagnostics["unresolved_changes"],
        execution_diagnostics["max_green_forced_commands"],
        execution_diagnostics["multi_phase_max_observed_green_s"],
        execution_diagnostics["multi_phase_mean_phase_dominance"],
        execution_diagnostics["multi_phase_max_phase_dominance"],
    )

    if len(_buffer_episodes) >= ACCUMULATE_EPISODES:
        _ppo_update()
    if _episode % CHECKPOINT_INTERVAL == 0:
        _save_checkpoint()

    _episode_trajectory = _new_trajectory()
    _pending_transition = None
    _last_joint_decision_time = None
    _latest_observation_time = None
    _terminal_bootstrap_values = None
    _horizon_guard_active = False
    _horizon_guarded_agents = 0
    _prev_pressure = {}
    _prev_actions = {}
    _pending_signal_commands = {}
    _signal_execution_stats = {}


def take_collected_rollout() -> dict:
    """Return exactly one completed rollout from a collector worker."""

    global _collected_rollout
    if _collected_rollout is None:
        raise RuntimeError("No completed collector rollout is available")
    result = _collected_rollout
    _collected_rollout = None
    return result


def ingest_parallel_rollouts(
    rollouts: Sequence[Mapping[str, Any]], *, update: bool = True
) -> dict:
    """Normalize and consume one synchronous on-policy batch centrally."""

    global _episode
    if (
        _mode != "train"
        or _model is None
        or _actor_optimizer is None
        or _critic_optimizer is None
    ):
        raise RuntimeError("Parallel rollouts require an initialized training learner")
    if not rollouts:
        raise ValueError("At least one parallel rollout is required")
    if _buffer_episodes:
        raise RuntimeError("Stale rollout buffer must be empty before a parallel batch")

    total_samples = 0
    for rollout in rollouts:
        if rollout.get("policy_architecture") != POLICY_ARCHITECTURE:
            raise ValueError(
                "Parallel rollout policy architecture mismatch: "
                f"expected {POLICY_ARCHITECTURE!r}, "
                f"got {rollout.get('policy_architecture')!r}"
            )
        if rollout.get("collaboration_schema") != COLLABORATION_SCHEMA:
            raise ValueError(
                "Parallel rollout collaboration schema mismatch: "
                f"expected {COLLABORATION_SCHEMA!r}, "
                f"got {rollout.get('collaboration_schema')!r}"
            )
        rollout_phase_feature_schema = str(
            rollout.get("phase_feature_schema", "")
        )
        if rollout_phase_feature_schema != PHASE_FEATURE_SCHEMA:
            raise ValueError(
                "Parallel rollout phase-feature schema mismatch: "
                f"expected {PHASE_FEATURE_SCHEMA!r}, "
                f"got {rollout_phase_feature_schema!r}"
            )
        rollout_reward_mode = str(rollout.get("reward_mode", "legacy_delta"))
        if rollout_reward_mode != _reward_mode:
            raise ValueError(
                "Parallel rollout reward mode mismatch: "
                f"expected {_reward_mode!r}, got {rollout_reward_mode!r}"
            )
        rollout_max_green_factor = float(
            rollout.get("max_green_factor", 0.0)
        )
        if not math.isclose(
            rollout_max_green_factor,
            _max_green_factor,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "Parallel rollout max-green factor mismatch: "
                f"expected {_max_green_factor}, "
                f"got {rollout_max_green_factor}"
            )
        source = rollout.get("trajectory")
        if not isinstance(source, Mapping):
            raise ValueError("Parallel rollout has no joint trajectory")
        episode = copy.deepcopy(dict(source))
        raw_rewards = np.asarray(episode.get("raw_rewards"), dtype=np.float32)
        observations = np.asarray(episode.get("obs"), dtype=np.float32)
        transition_durations = np.asarray(
            episode.get("transition_durations_s"), dtype=np.float32
        )
        action_masks = np.asarray(episode.get("action_masks"), dtype=np.bool_)
        phase_features = np.asarray(
            episode.get("phase_features"), dtype=np.float32
        )
        if raw_rewards.ndim != 2 or raw_rewards.shape[1] != len(_tls_order):
            raise ValueError("Parallel raw rewards must be [time, intersections]")
        if observations.ndim != 3 or observations.shape[:2] != raw_rewards.shape:
            raise ValueError("Parallel observations must align with joint rewards")
        expected_phase_shape = (*action_masks.shape, PHASE_FEATURES)
        if phase_features.shape != expected_phase_shape:
            raise ValueError(
                "Parallel phase features must align with action masks: "
                f"expected {expected_phase_shape}, got {phase_features.shape}"
            )
        sample_count = int(raw_rewards.shape[0])
        if sample_count < 1:
            raise ValueError("Parallel rollout has no policy samples")
        if (
            transition_durations.shape != (sample_count,)
            or not np.isfinite(transition_durations).all()
            or not np.allclose(
                transition_durations,
                JOINT_DECISION_INTERVAL,
                rtol=0.0,
                atol=1e-3,
            )
        ):
            raise ValueError(
                "Parallel transition durations must all match the fixed "
                f"{JOINT_DECISION_INTERVAL:.1f}s PPO horizon"
            )

        normalized = [
            _normalize_rewards(raw_rewards[timestep])
            for timestep in range(sample_count)
        ]
        episode["raw_rewards"] = raw_rewards
        episode["transition_durations_s"] = transition_durations
        episode["rewards"] = np.stack(normalized).astype(np.float32)
        _buffer_episodes.append(episode)
        _episode += 1
        total_samples += sample_count

    if update:
        _ppo_update(episode_count=len(rollouts))
    return {"episodes": len(rollouts), "samples": total_samples}


def _compute_gae(
    rewards: np.ndarray,
    values: np.ndarray,
    last_values: np.ndarray,
    gamma: float = GAMMA,
    gae_lambda: float = GAE_LAMBDA,
) -> Tuple[np.ndarray, np.ndarray]:
    rewards = np.asarray(rewards, dtype=np.float32)
    values = np.asarray(values, dtype=np.float32)
    last_values = np.asarray(last_values, dtype=np.float32)
    if rewards.shape != values.shape or rewards.ndim != 2:
        raise ValueError("joint rewards and values must both be [time, agents]")
    if last_values.shape != rewards.shape[1:]:
        raise ValueError("last_values must have one value per agent")
    advantages = np.zeros_like(rewards)
    gae = np.zeros(rewards.shape[1], dtype=np.float32)
    next_values = last_values.copy()
    for timestep in reversed(range(len(rewards))):
        delta = rewards[timestep] + gamma * next_values - values[timestep]
        gae = delta + gamma * gae_lambda * gae
        advantages[timestep] = gae
        next_values = values[timestep]
    return advantages, advantages + values


def _build_training_batch(episodes: Sequence[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    fields: Dict[str, List[np.ndarray]] = {
        "obs": [],
        "actions": [],
        "action_log_probs": [],
        "collaborators": [],
        "collaborator_log_probs": [],
        "values": [],
        "normalized_values": [],
        "rewards": [],
        "raw_rewards": [],
        "valid_action_counts": [],
        "action_masks": [],
        "phase_features": [],
        "advantages": [],
        "returns": [],
    }
    for episode in episodes:
        if len(episode.get("obs", [])) == 0:
            continue
        rewards = np.asarray(episode["rewards"], dtype=np.float32)
        values = np.asarray(episode["values"], dtype=np.float32)
        normalized_values = np.asarray(
            episode.get("normalized_values", values), dtype=np.float32
        )
        if normalized_values.shape != values.shape:
            raise ValueError("normalized values must align with raw values")
        episode["normalized_values"] = normalized_values
        action_masks = np.asarray(episode["action_masks"], dtype=np.bool_)
        phase_features = np.asarray(
            episode.get(
                "phase_features",
                np.zeros((*action_masks.shape, PHASE_FEATURES), dtype=np.float32),
            ),
            dtype=np.float32,
        )
        expected_phase_shape = (*action_masks.shape, PHASE_FEATURES)
        if phase_features.shape != expected_phase_shape:
            raise ValueError(
                "phase features must align with action masks: "
                f"expected {expected_phase_shape}, got {phase_features.shape}"
            )
        episode["phase_features"] = phase_features
        advantages, returns = _compute_gae(
            rewards, values, np.asarray(episode["last_values"], dtype=np.float32)
        )
        for name in (
            "obs",
            "actions",
            "action_log_probs",
            "collaborators",
            "collaborator_log_probs",
            "values",
            "normalized_values",
            "rewards",
            "raw_rewards",
            "valid_action_counts",
            "action_masks",
            "phase_features",
        ):
            fields[name].append(np.asarray(episode[name]))
        fields["advantages"].append(advantages)
        fields["returns"].append(returns)
    return {
        name: np.concatenate(parts, axis=0)
        for name, parts in fields.items()
        if parts
    }


def _parameter_group_norm(
    values: Mapping[str, torch.Tensor], prefixes: Sequence[str]
) -> float:
    squared = sum(
        float(torch.sum(tensor.detach().float().square()))
        for name, tensor in values.items()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
    )
    return math.sqrt(max(squared, 0.0))


def _probe_policy(
    observations: torch.Tensor,
    valid_counts: torch.Tensor,
    action_masks: torch.Tensor,
    phase_features: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, float]:
    if _model is None:
        raise RuntimeError("CoSLight probe requires an initialized model")
    with torch.no_grad():
        encoded = _model.encode(observations)
        if _model.use_legacy_collaboration:
            collaboration_logits = _model.collaboration_logits(encoded)
            collaborators, _, _ = _model.select_collaborators(
                collaboration_logits, deterministic=True
            )
            collaborator_context = None
        elif _model.use_learned_topk_collaboration:
            collaboration_logits = _model.learned_collaboration_logits(encoded)
            collaborators, _, _ = _model.select_collaborators(
                collaboration_logits, deterministic=True
            )
            collaborator_context = _model.selected_collaborator_context(
                encoded, collaborators
            )
        else:
            (
                collaborator_context,
                collaborators,
                _,
                collaboration_logits,
            ) = _model.topology_attention(encoded)
        action_distribution = _masked_categorical(
            _model._actor_logits(
                encoded,
                collaborators,
                phase_features,
                collaborator_context=collaborator_context,
            ),
            valid_counts,
            action_masks,
        )
        bias_abs = 0.0
        if collaborator_context is not None:
            bias_abs = float(
                _model.collab_bias(collaborator_context).abs().mean()
            )
        return (
            action_distribution.probs.detach().cpu(),
            torch.softmax(collaboration_logits, dim=-1).detach().cpu(),
            torch.argmax(action_distribution.probs, dim=-1).detach().cpu(),
            bias_abs,
        )


def _probe_topology_policy(
    observations: torch.Tensor,
    valid_counts: torch.Tensor,
    action_masks: torch.Tensor,
    phase_features: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Evaluate the V16 topology path on the same fixed observations."""

    if _model is None:
        raise RuntimeError("CoSLight topology probe requires an initialized model")
    with torch.no_grad():
        encoded = _model.encode(observations)
        context, collaborators, _, _ = _model.topology_attention(encoded)
        distribution = _masked_categorical(
            _model._actor_logits(
                encoded,
                collaborators,
                phase_features,
                collaborator_context=context,
            ),
            valid_counts,
            action_masks,
        )
        return (
            distribution.probs.detach().cpu(),
            torch.argmax(distribution.probs, dim=-1).detach().cpu(),
        )


def _ppo_update(episode_count: Optional[int] = None) -> None:
    global _policy_generation
    if (
        _model is None
        or _actor_optimizer is None
        or _critic_optimizer is None
    ):
        raise RuntimeError("CoSLight PPO called before model initialization")
    episode_count = min(
        int(episode_count or ACCUMULATE_EPISODES), len(_buffer_episodes)
    )
    if episode_count < 1:
        return
    episodes = _buffer_episodes[:episode_count]
    batch = _build_training_batch(episodes)
    if not batch:
        _buffer_episodes[:episode_count] = []
        return

    raw_advantages = batch["advantages"].astype(np.float32)
    advantage_mean = float(raw_advantages.mean())
    advantage_std = float(raw_advantages.std())
    advantage_abs_mean = float(np.abs(raw_advantages).mean())
    advantages = raw_advantages.copy()
    advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
    obs = torch.from_numpy(batch["obs"].astype(np.float32))
    actions = torch.from_numpy(batch["actions"].astype(np.int64))
    old_action_log_probs = torch.from_numpy(
        batch["action_log_probs"].astype(np.float32)
    )
    collaborators = torch.from_numpy(batch["collaborators"].astype(np.int64))
    old_collaborator_log_probs = torch.from_numpy(
        batch["collaborator_log_probs"].astype(np.float32)
    )
    valid_counts = torch.from_numpy(
        batch["valid_action_counts"].astype(np.int64)
    )
    action_masks = torch.from_numpy(batch["action_masks"].astype(np.bool_))
    phase_features = torch.from_numpy(
        batch["phase_features"].astype(np.float32)
    )
    advantage_tensor = torch.from_numpy(advantages)
    returns = torch.from_numpy(batch["returns"].astype(np.float32))
    _value_normalizer.update(returns)
    normalized_returns = _value_normalizer.normalize(returns)
    normalized_old_values = torch.from_numpy(
        batch["normalized_values"].astype(np.float32)
    )

    total_steps = obs.shape[0]
    probe_count = min(total_steps, 32)
    (
        old_probe_actions,
        old_probe_collaborators,
        old_probe_argmax,
        old_probe_bias,
    ) = _probe_policy(
        obs[:probe_count],
        valid_counts[:probe_count],
        action_masks[:probe_count],
        phase_features[:probe_count],
    )
    topology_probe_actions, topology_probe_argmax = _probe_topology_policy(
        obs[:probe_count],
        valid_counts[:probe_count],
        action_masks[:probe_count],
        phase_features[:probe_count],
    )
    probe_epsilon = 1e-8
    topology_probe_action_kl = float(
        (
            topology_probe_actions
            * (
                torch.log(topology_probe_actions + probe_epsilon)
                - torch.log(old_probe_actions + probe_epsilon)
            )
        )
        .sum(dim=-1)
        .mean()
    )
    topology_probe_argmax_change = float(
        (topology_probe_argmax != old_probe_argmax).float().mean()
    )
    parameters_before = {
        name: parameter.detach().cpu().clone()
        for name, parameter in _model.named_parameters()
    }
    metrics = {
        "policy": 0.0,
        "collaborator_policy": 0.0,
        "critic": 0.0,
        "action_entropy": 0.0,
        "collab_entropy": 0.0,
        "approx_kl": 0.0,
        "clip_fraction": 0.0,
        "collaborator_approx_kl": 0.0,
        "collaborator_clip_fraction": 0.0,
        "diagonal": 0.0,
        "symmetry": 0.0,
        "policy_grad_norm": 0.0,
        "collaborator_grad_norm": 0.0,
        "entropy_grad_norm": 0.0,
        "policy_entropy_grad_cosine": 0.0,
        "actor_grad_norm": 0.0,
        "critic_grad_norm": 0.0,
    }
    ratio_samples: List[torch.Tensor] = []
    collaborator_ratio_samples: List[torch.Tensor] = []
    trainable_actor_parameters = tuple(
        parameter
        for parameter in _model.actor_parameters()
        if parameter.requires_grad
    )
    update_count = 0
    _model.train()
    ppo_epochs = _configured_ppo_epochs()
    for _ in range(ppo_epochs):
        permutation = torch.randperm(total_steps)
        for start in range(0, total_steps, BATCH_SIZE):
            indices = permutation[start : start + BATCH_SIZE]
            output = _model.evaluate_actions(
                obs[indices],
                actions[indices],
                valid_counts[indices],
                collaborators[indices],
                action_masks[indices],
                phase_features[indices],
            )
            policy_loss, log_ratio, ratio = (
                _phase_clipped_surrogate(
                    output.action_log_probs,
                    old_action_log_probs[indices],
                    advantage_tensor[indices],
                )
            )
            (
                collaborator_policy_loss,
                collaborator_log_ratio,
                collaborator_ratio,
            ) = _collaborator_clipped_surrogate(
                output.collaborator_log_probs,
                old_collaborator_log_probs[indices],
                advantage_tensor[indices],
            )

            value_prediction_clipped = normalized_old_values[indices] + torch.clamp(
                output.values - normalized_old_values[indices],
                -CLIP_EPS,
                CLIP_EPS,
            )
            critic_loss_unclipped = F.huber_loss(
                output.values,
                normalized_returns[indices],
                delta=HUBER_DELTA,
                reduction="none",
            )
            critic_loss_clipped = F.huber_loss(
                value_prediction_clipped,
                normalized_returns[indices],
                delta=HUBER_DELTA,
                reduction="none",
            )
            critic_loss = torch.maximum(
                critic_loss_unclipped, critic_loss_clipped
            ).mean()
            collaborator_probabilities = torch.softmax(
                output.collaboration_logits, dim=-1
            )
            diagonal_probability = torch.diagonal(
                collaborator_probabilities, dim1=-2, dim2=-1
            ).mean()
            symmetry_loss = (
                collaborator_probabilities
                - collaborator_probabilities.transpose(-2, -1)
            ).square().mean()
            actor_objective = (
                policy_loss
                + COLLABORATOR_POLICY_COEF * collaborator_policy_loss
                - ENTROPY_COEF * output.action_entropy.mean()
                - COLLABORATOR_ENTROPY_COEF
                * output.collaborator_entropy.mean()
                - COLLABORATOR_DIAGONAL_COEF * diagonal_probability
                + COLLABORATOR_SYMMETRY_COEF * symmetry_loss
            )

            policy_gradients = torch.autograd.grad(
                policy_loss,
                trainable_actor_parameters,
                retain_graph=True,
                allow_unused=True,
            )
            collaborator_gradients = torch.autograd.grad(
                collaborator_policy_loss,
                trainable_actor_parameters,
                retain_graph=True,
                allow_unused=True,
            )
            entropy_objective = -ENTROPY_COEF * output.action_entropy.mean()
            entropy_gradients = torch.autograd.grad(
                entropy_objective,
                trainable_actor_parameters,
                retain_graph=True,
                allow_unused=True,
            )

            _actor_optimizer.zero_grad()
            actor_objective.backward()
            actor_grad_norm = nn.utils.clip_grad_norm_(
                _model.actor_parameters(), MAX_GRAD_NORM
            )
            _actor_optimizer.step()

            _critic_optimizer.zero_grad()
            (VALUE_COEF * critic_loss).backward()
            critic_grad_norm = nn.utils.clip_grad_norm_(
                _model.critic_parameters(), MAX_GRAD_NORM
            )
            _critic_optimizer.step()

            with torch.no_grad():
                metrics["policy"] += float(policy_loss)
                metrics["collaborator_policy"] += float(
                    collaborator_policy_loss
                )
                metrics["critic"] += float(critic_loss)
                metrics["action_entropy"] += float(output.action_entropy.mean())
                metrics["collab_entropy"] += float(
                    output.collaborator_entropy.mean()
                )
                metrics["approx_kl"] += float(
                    ((ratio - 1.0) - log_ratio).mean()
                )
                metrics["clip_fraction"] += float(
                    ((ratio - 1.0).abs() > CLIP_EPS).float().mean()
                )
                metrics["collaborator_approx_kl"] += float(
                    ((collaborator_ratio - 1.0) - collaborator_log_ratio).mean()
                )
                metrics["collaborator_clip_fraction"] += float(
                    (
                        (collaborator_ratio - 1.0).abs() > CLIP_EPS
                    ).float().mean()
                )
                metrics["diagonal"] += float(diagonal_probability)
                metrics["symmetry"] += float(symmetry_loss)
                metrics["policy_grad_norm"] += _gradient_l2_norm(
                    policy_gradients
                )
                metrics["collaborator_grad_norm"] += _gradient_l2_norm(
                    collaborator_gradients
                )
                metrics["entropy_grad_norm"] += _gradient_l2_norm(
                    entropy_gradients
                )
                metrics["policy_entropy_grad_cosine"] += _gradient_cosine(
                    policy_gradients, entropy_gradients
                )
                metrics["actor_grad_norm"] += float(actor_grad_norm)
                metrics["critic_grad_norm"] += float(critic_grad_norm)
                ratio_samples.append(ratio.detach().reshape(-1).cpu())
                collaborator_ratio_samples.append(
                    collaborator_ratio.detach().reshape(-1).cpu()
                )
                update_count += 1

    _buffer_episodes[:episode_count] = []
    raw_rewards = batch["raw_rewards"]
    old_values = batch["values"]
    target_returns = batch["returns"]
    explained_variance = 1.0 - float(np.var(target_returns - old_values)) / (
        float(np.var(target_returns)) + 1e-8
    )
    parameters_after = {
        name: parameter.detach().cpu()
        for name, parameter in _model.named_parameters()
    }
    parameter_deltas = {
        name: parameters_after[name] - parameters_before[name]
        for name in parameters_before
    }
    total_parameter_delta = _parameter_group_norm(
        parameter_deltas, tuple(parameters_before)
    )
    total_parameter_norm = _parameter_group_norm(
        parameters_before, tuple(parameters_before)
    )
    actor_encoder_delta = _parameter_group_norm(
        parameter_deltas, ("obs_embed", "transformer")
    )
    actor_delta = _parameter_group_norm(
        parameter_deltas,
        (
            "score_projection",
            "self_score_bias",
            "target_projection",
            "source_projection",
            "context_projection",
            "phase_scorer",
            "phase_actor_head",
        ),
    )
    phase_scorer_delta = _parameter_group_norm(
        parameter_deltas, ("phase_scorer",)
    )
    phase_scorer_norm = _parameter_group_norm(
        parameters_after, ("phase_scorer",)
    )
    phase_head_delta = _parameter_group_norm(
        parameter_deltas, ("phase_actor_head.2",)
    )
    phase_head_norm = _parameter_group_norm(
        parameters_after, ("phase_actor_head.2",)
    )
    critic_delta = _parameter_group_norm(
        parameter_deltas,
        ("critic_obs_embed", "critic_transformer", "critic_head"),
    )
    with torch.no_grad():
        post_values = (
            _value_normalizer.denormalize(_model.values(obs))
            .detach()
            .cpu()
            .numpy()
        )
    post_explained_variance = 1.0 - float(
        np.var(target_returns - post_values)
    ) / (float(np.var(target_returns)) + 1e-8)
    (
        new_probe_actions,
        new_probe_collaborators,
        new_probe_argmax,
        new_probe_bias,
    ) = _probe_policy(
        obs[:probe_count],
        valid_counts[:probe_count],
        action_masks[:probe_count],
        phase_features[:probe_count],
    )
    valid_phase_slots = (
        np.arange(batch["action_masks"].shape[-1])[None, None, :]
        < batch["valid_action_counts"][..., None]
    )
    nonzero_phase_slots = np.any(
        np.abs(
            batch["phase_features"][..., :PHASE_TRAFFIC_FEATURES]
        ) > 1e-8,
        axis=-1,
    )
    phase_feature_coverage = float(
        np.logical_and(valid_phase_slots, nonzero_phase_slots).sum()
        / max(valid_phase_slots.sum(), 1)
    )
    effective_action_masks = np.logical_and(
        valid_phase_slots, batch["action_masks"]
    )
    legal_action_counts = effective_action_masks.sum(axis=-1)
    legal_action_histogram = {
        int(count): int((legal_action_counts == count).sum())
        for count in np.unique(legal_action_counts)
    }
    pressure_feature_values = batch["phase_features"][..., 5][
        effective_action_masks
    ]
    pressure_feature_mean = float(pressure_feature_values.mean())
    pressure_feature_std = float(pressure_feature_values.std())
    pressure_feature_p01, pressure_feature_p99 = np.quantile(
        pressure_feature_values, [0.01, 0.99]
    )
    action_advantages = {}
    for action_index in range(batch["action_masks"].shape[-1]):
        selected = batch["actions"] == action_index
        if not np.any(selected):
            continue
        action_advantages[action_index] = {
            "count": int(selected.sum()),
            "raw_mean": float(raw_advantages[selected].mean()),
            "normalized_mean": float(advantages[selected].mean()),
        }
    all_ratios = (
        torch.cat(ratio_samples).numpy()
        if ratio_samples
        else np.ones(1, dtype=np.float32)
    )
    all_collaborator_ratios = (
        torch.cat(collaborator_ratio_samples).numpy()
        if collaborator_ratio_samples
        else np.ones(1, dtype=np.float32)
    )
    ratio_mean = float(all_ratios.mean())
    ratio_std = float(all_ratios.std())
    ratio_p01 = float(np.percentile(all_ratios, 1))
    ratio_p99 = float(np.percentile(all_ratios, 99))
    collaborator_ratio_mean = float(all_collaborator_ratios.mean())
    collaborator_ratio_std = float(all_collaborator_ratios.std())
    collaborator_array = batch["collaborators"].astype(np.int64)
    target_indices = np.arange(_model.num_agents)[None, :, None]
    selected_are_local = _model.neighbor_mask.cpu().numpy()[
        target_indices, collaborator_array
    ]
    nonlocal_selection_rate = float((~selected_are_local).mean())
    self_selection_rate = float(
        np.any(collaborator_array == target_indices, axis=-1).mean()
    )
    unique_selection_rate = float(
        np.mean(
            [
                len(set(row.tolist())) == _model.top_k
                for timestep in collaborator_array
                for row in timestep
            ]
        )
    )
    reciprocal_count = 0
    directed_count = 0
    for timestep in collaborator_array:
        selected_sets = [set(row.tolist()) for row in timestep]
        for target, sources in enumerate(selected_sets):
            for source in sources:
                if source == target:
                    continue
                directed_count += 1
                reciprocal_count += int(target in selected_sets[source])
    reciprocal_selection_rate = (
        float(reciprocal_count / directed_count)
        if directed_count
        else 1.0
    )
    epsilon = 1e-8
    probe_action_kl = float(
        (
            old_probe_actions
            * (
                torch.log(old_probe_actions + epsilon)
                - torch.log(new_probe_actions + epsilon)
            )
        )
        .sum(dim=-1)
        .mean()
    )
    probe_collaborator_kl = float(
        (
            old_probe_collaborators
            * (
                torch.log(old_probe_collaborators + epsilon)
                - torch.log(new_probe_collaborators + epsilon)
            )
        )
        .sum(dim=-1)
        .mean()
    )
    probe_argmax_change = float(
        (old_probe_argmax != new_probe_argmax).float().mean()
    )
    old_probe_topk = torch.topk(
        old_probe_collaborators, k=_model.top_k, dim=-1
    ).indices
    new_probe_topk = torch.topk(
        new_probe_collaborators, k=_model.top_k, dim=-1
    ).indices
    probe_topk_overlap = (
        old_probe_topk.unsqueeze(-1) == new_probe_topk.unsqueeze(-2)
    ).any(dim=-1).float().mean()
    probe_topk_churn = 1.0 - float(probe_topk_overlap)
    _policy_generation += 1
    logger.info(
        "PPO update: generation=%d joint_steps=%d agent_samples=%d reward=%.4f±%.4f "
        "ev=%.3f policy=%.4f cos=%.4f critic=%.4f ent=%.3f/%.3f "
        "kl=%.7f/%.7f clip=%.3f/%.3f self=%.3f sym=%.6f",
        _policy_generation,
        total_steps,
        int(np.prod(batch["actions"].shape)),
        float(raw_rewards.mean()),
        float(raw_rewards.std()),
        explained_variance,
        metrics["policy"] / max(update_count, 1),
        metrics["collaborator_policy"] / max(update_count, 1),
        metrics["critic"] / max(update_count, 1),
        metrics["action_entropy"] / max(update_count, 1),
        metrics["collab_entropy"] / max(update_count, 1),
        metrics["approx_kl"] / max(update_count, 1),
        metrics["collaborator_approx_kl"] / max(update_count, 1),
        metrics["clip_fraction"] / max(update_count, 1),
        metrics["collaborator_clip_fraction"] / max(update_count, 1),
        metrics["diagonal"] / max(update_count, 1),
        metrics["symmetry"] / max(update_count, 1),
    )
    logger.info(
        "PPO diagnostics: adv=%.4f±%.4f |adv|=%.4f return=%.4f±%.4f "
        "value=%.4f±%.4f post_value=%.4f±%.4f post_ev=%.3f "
        "grad_actor=%.4f grad_critic=%.4f dparam=%.6f rel=%.6g "
        "dactor_encoder=%.6f dactor=%.6f dcritic=%.6f "
        "phase_cov=%.3f dphase_score=%.6f phase_score=%.6f "
        "dphase_out=%.6f phase_out=%.6f "
        "grad_pg=%.6f grad_cos=%.6f grad_ent=%.6f pg_ent_cos=%.3f "
        "ratio=%.6f±%.6f p01/p99=%.6f/%.6f cos_ratio=%.6f±%.6f "
        "value_norm=%.4f±%.4f/%d "
        "probe_kl=%.6f/%.6f probe_argmax_change=%.3f "
        "topology_probe_kl/change=%.6f/%.3f "
        "collab_bias=%.4f/%.4f "
        "probe_topk_churn=%.3f selection_nonlocal/self/unique/reciprocal="
        "%.3f/%.3f/%.3f/%.3f "
        "legal_actions=%s phase_pressure=%.4f±%.4f p01/p99=%.4f/%.4f "
        "adv_by_action=%s",
        advantage_mean,
        advantage_std,
        advantage_abs_mean,
        float(target_returns.mean()),
        float(target_returns.std()),
        float(old_values.mean()),
        float(old_values.std()),
        float(post_values.mean()),
        float(post_values.std()),
        post_explained_variance,
        metrics["actor_grad_norm"] / max(update_count, 1),
        metrics["critic_grad_norm"] / max(update_count, 1),
        total_parameter_delta,
        total_parameter_delta / max(total_parameter_norm, 1e-12),
        actor_encoder_delta,
        actor_delta,
        critic_delta,
        phase_feature_coverage,
        phase_scorer_delta,
        phase_scorer_norm,
        phase_head_delta,
        phase_head_norm,
        metrics["policy_grad_norm"] / max(update_count, 1),
        metrics["collaborator_grad_norm"] / max(update_count, 1),
        metrics["entropy_grad_norm"] / max(update_count, 1),
        metrics["policy_entropy_grad_cosine"] / max(update_count, 1),
        ratio_mean,
        ratio_std,
        ratio_p01,
        ratio_p99,
        collaborator_ratio_mean,
        collaborator_ratio_std,
        _value_normalizer.mean,
        _value_normalizer.std,
        _value_normalizer.count,
        probe_action_kl,
        probe_collaborator_kl,
        probe_argmax_change,
        topology_probe_action_kl,
        topology_probe_argmax_change,
        old_probe_bias,
        new_probe_bias,
        probe_topk_churn,
        nonlocal_selection_rate,
        self_selection_rate,
        unique_selection_rate,
        reciprocal_selection_rate,
        legal_action_histogram,
        pressure_feature_mean,
        pressure_feature_std,
        pressure_feature_p01,
        pressure_feature_p99,
        action_advantages,
    )


def _checkpoint_payload() -> Dict[str, Any]:
    if _model is None:
        raise RuntimeError("no CoSLight model to save")
    numpy_rng = np.random.get_state()
    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "model_state_dict": _model.state_dict(),
        "actor_optimizer_state_dict": (
            _actor_optimizer.state_dict() if _actor_optimizer else None
        ),
        "critic_optimizer_state_dict": (
            _critic_optimizer.state_dict() if _critic_optimizer else None
        ),
        "episode": _episode,
        "policy_generation": _policy_generation,
        "reward_stats": {
            "mean": _reward_mean,
            "m2": _reward_m2,
            "count": _reward_count,
        },
        "value_normalization": _value_normalizer.state_dict(),
        "rng_state": {
            "python": random.getstate(),
            "numpy": {
                "bit_generator": numpy_rng[0],
                "state": torch.from_numpy(numpy_rng[1].copy()),
                "position": int(numpy_rng[2]),
                "has_gauss": int(numpy_rng[3]),
                "cached_gaussian": float(numpy_rng[4]),
            },
            "torch": torch.get_rng_state(),
        },
        "model_config": {
            "num_agents": _model.num_agents,
            "obs_dim": _model.obs_dim,
            "act_dim": _model.act_dim,
            "top_k": _model.top_k,
            "hidden": _model.hidden,
        },
        "training_config": {
            "reward_mode": _reward_mode,
            "max_green_factor": _max_green_factor,
            "value_normalization": True,
            "critic_architecture": "independent_transformer",
            "policy_objective": POLICY_OBJECTIVE,
            "policy_architecture": POLICY_ARCHITECTURE,
            "phase_feature_schema": PHASE_FEATURE_SCHEMA,
            "collaboration_schema": COLLABORATION_SCHEMA,
            "actor_encoder_scope": ACTOR_ENCODER_SCOPE,
            "reward_schema": REWARD_SCHEMA,
            "terminal_transition_schema": TERMINAL_TRANSITION_SCHEMA,
            "spillback_coef": LAMBDA_SPILL,
            "actor_lr": ACTOR_LR,
            "critic_lr": CRITIC_LR,
            "phase_scorer_lr_multiplier": PHASE_SCORER_LR_MULTIPLIER,
            "collaborator_policy_coef": COLLABORATOR_POLICY_COEF,
            "collaborator_entropy_coef": COLLABORATOR_ENTROPY_COEF,
            "collaborator_diagonal_coef": COLLABORATOR_DIAGONAL_COEF,
            "collaborator_symmetry_coef": COLLABORATOR_SYMMETRY_COEF,
            "ppo_epochs": _configured_ppo_epochs(),
            "pressure_prior_scale": PRESSURE_PRIOR_SCALE,
            "hold_prior_bias": HOLD_PRIOR_BIAS,
        },
        "tls_order": list(_tls_order),
        "phase_orders": dict(_phase_orders),
        "neighbor_mask": _model.neighbor_mask.detach().cpu(),
    }
    seed_start = os.environ.get("COSLIGHT_TRAIN_SEED_START")
    seed_end = os.environ.get("COSLIGHT_TRAIN_SEED_END")
    if seed_start is not None and seed_end is not None:
        payload["training_seed_range"] = {
            "start": int(seed_start),
            "end": int(seed_end),
        }
    return payload


def _save_checkpoint(path: Optional[str | Path] = None) -> Path:
    if path is None:
        directory = Path(
            os.environ.get("COSLIGHT_CHECKPOINT_DIR")
            or Path(__file__).with_name("checkpoints")
        )
        path = directory / f"coslight_v2_ep{_episode}.pt"
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = checkpoint_path.with_name(f".{checkpoint_path.name}.tmp")
    torch.save(_checkpoint_payload(), temporary_path)
    os.replace(temporary_path, checkpoint_path)
    logger.info("CoSLight checkpoint: %s", checkpoint_path)

    if path is not None and checkpoint_path.name.startswith("coslight_v2_ep"):
        files = sorted(
            glob.glob(str(checkpoint_path.parent / "coslight_v2_ep*.pt")),
            key=os.path.getmtime,
        )
        for old_path in files[:-MAX_CHECKPOINTS]:
            os.remove(old_path)
    return checkpoint_path


def _load_checkpoint(path: str | Path, load_optimizer: bool) -> None:
    global _episode, _reward_mean, _reward_m2, _reward_count, _policy_generation
    global _value_normalizer
    if _model is None:
        raise RuntimeError("initialize model dimensions before loading checkpoint")
    # ``weights_only`` rejects executable pickle payloads while still supporting
    # tensor state dictionaries and optimizer state saved by this module.
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        format_version = int(checkpoint.get("format_version", 0))
        if format_version > CHECKPOINT_FORMAT_VERSION:
            raise ValueError(
                "CoSLight checkpoint format is newer than this runtime: "
                f"checkpoint={format_version}, runtime={CHECKPOINT_FORMAT_VERSION}"
            )
        model_config = checkpoint.get("model_config")
        if not isinstance(model_config, dict):
            raise ValueError("CoSLight checkpoint is missing model_config")
        expected_config = {
            "num_agents": _model.num_agents,
            "obs_dim": _model.obs_dim,
            "act_dim": _model.act_dim,
            "top_k": _model.top_k,
            "hidden": _model.hidden,
        }
        actual_config = {
            key: int(model_config.get(key, -1)) for key in expected_config
        }
        if actual_config != expected_config:
            raise ValueError(
                "CoSLight checkpoint model_config mismatch: "
                f"expected {expected_config}, got {actual_config}"
            )
        checkpoint_tls_order = checkpoint.get("tls_order")
        if checkpoint_tls_order is not None and list(checkpoint_tls_order) != _tls_order:
            raise ValueError("CoSLight checkpoint tls_order does not match this scenario")
        checkpoint_phase_orders = checkpoint.get("phase_orders")
        if checkpoint_phase_orders is not None:
            normalized_phase_orders = {
                str(tid): [int(phase) for phase in phases]
                for tid, phases in checkpoint_phase_orders.items()
            }
            if normalized_phase_orders != _phase_orders:
                mismatches = {
                    tid: {
                        "checkpoint": normalized_phase_orders.get(tid),
                        "runtime": _phase_orders.get(tid),
                    }
                    for tid in sorted(
                        set(normalized_phase_orders).union(_phase_orders)
                    )
                    if normalized_phase_orders.get(tid) != _phase_orders.get(tid)
                }
                raise ValueError(
                    "CoSLight checkpoint phase_orders do not match this scenario: "
                    f"{mismatches}"
                )
        training_config = checkpoint.get("training_config", {})
        checkpoint_phase_schema = training_config.get("phase_feature_schema")
        supported_phase_schemas = {
            "connection_pressure_v1",
            "connection_pressure_v2",
            PHASE_FEATURE_SCHEMA,
        }
        if format_version >= 8:
            if checkpoint_phase_schema not in supported_phase_schemas:
                raise ValueError(
                    "CoSLight checkpoint phase-feature schema is unsupported: "
                    f"{checkpoint_phase_schema!r}"
                )
            if _state_builder is None:
                raise RuntimeError("CoSLight state builder is not initialized")
            _state_builder.phase_feature_schema = str(checkpoint_phase_schema)
        if format_version >= CHECKPOINT_FORMAT_VERSION:
            current_semantics = {
                "policy_objective": POLICY_OBJECTIVE,
                "policy_architecture": POLICY_ARCHITECTURE,
                "phase_feature_schema": PHASE_FEATURE_SCHEMA,
                "collaboration_schema": COLLABORATION_SCHEMA,
                "actor_encoder_scope": ACTOR_ENCODER_SCOPE,
                "reward_schema": REWARD_SCHEMA,
                "terminal_transition_schema": TERMINAL_TRANSITION_SCHEMA,
            }
            for key, expected in current_semantics.items():
                actual = training_config.get(key)
                if actual != expected:
                    raise ValueError(
                        f"CoSLight checkpoint {key} mismatch: "
                        f"expected={expected!r}, got={actual!r}"
                    )
            prior_values = {
                "pressure_prior_scale": PRESSURE_PRIOR_SCALE,
                "hold_prior_bias": HOLD_PRIOR_BIAS,
                "spillback_coef": LAMBDA_SPILL,
                "actor_lr": ACTOR_LR,
                "critic_lr": CRITIC_LR,
                "phase_scorer_lr_multiplier": PHASE_SCORER_LR_MULTIPLIER,
                "collaborator_policy_coef": COLLABORATOR_POLICY_COEF,
                "collaborator_entropy_coef": COLLABORATOR_ENTROPY_COEF,
                "collaborator_diagonal_coef": COLLABORATOR_DIAGONAL_COEF,
                "collaborator_symmetry_coef": COLLABORATOR_SYMMETRY_COEF,
            }
            for key, expected in prior_values.items():
                actual = float(training_config.get(key, math.nan))
                if not math.isclose(
                    actual, expected, rel_tol=0.0, abs_tol=1e-12
                ):
                    raise ValueError(
                        f"CoSLight checkpoint {key} mismatch: "
                        f"expected={expected}, got={actual}"
                    )
        if load_optimizer:
            if (
                not bool(training_config.get("value_normalization", False))
                or training_config.get("critic_architecture")
                != "independent_transformer"
                or training_config.get("policy_objective") != POLICY_OBJECTIVE
                or training_config.get("policy_architecture")
                != POLICY_ARCHITECTURE
                or training_config.get("phase_feature_schema")
                != PHASE_FEATURE_SCHEMA
                or training_config.get("collaboration_schema")
                != COLLABORATION_SCHEMA
                or training_config.get("actor_encoder_scope")
                != ACTOR_ENCODER_SCOPE
                or training_config.get("reward_schema") != REWARD_SCHEMA
                or training_config.get("terminal_transition_schema")
                != TERMINAL_TRANSITION_SCHEMA
            ):
                raise ValueError(
                    "CoSLight checkpoint value normalization, critic architecture, "
                    "policy objective, policy architecture, phase-feature schema, "
                    "collaboration schema, actor scope, reward schema, or "
                    "terminal-transition schema is "
                    "incompatible with continued training"
                )
            resume_values = {
                "spillback_coef": LAMBDA_SPILL,
                "actor_lr": ACTOR_LR,
                "critic_lr": CRITIC_LR,
                "phase_scorer_lr_multiplier": PHASE_SCORER_LR_MULTIPLIER,
            }
            for key, expected in resume_values.items():
                actual = float(training_config.get(key, math.nan))
                if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12):
                    raise ValueError(
                        f"CoSLight checkpoint {key} mismatch: "
                        f"checkpoint={actual}, expected={expected}"
                    )
            checkpoint_reward_mode = str(
                training_config.get("reward_mode", "legacy_delta")
            )
            if checkpoint_reward_mode != _reward_mode:
                raise ValueError(
                    "CoSLight checkpoint reward mode mismatch: "
                    f"checkpoint={checkpoint_reward_mode!r}, "
                    f"requested={_reward_mode!r}"
                )
            checkpoint_max_green_factor = float(
                training_config.get("max_green_factor", 0.0)
            )
            if not math.isclose(
                checkpoint_max_green_factor,
                _max_green_factor,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "CoSLight checkpoint max-green factor mismatch: "
                    f"checkpoint={checkpoint_max_green_factor}, "
                    f"requested={_max_green_factor}"
                )
        checkpoint_neighbor_mask = checkpoint.get("neighbor_mask")
        if format_version >= 10:
            if not isinstance(checkpoint_neighbor_mask, torch.Tensor):
                raise ValueError("CoSLight checkpoint is missing neighbor mask")
            if not torch.equal(
                checkpoint_neighbor_mask.to(dtype=torch.bool),
                _model.neighbor_mask.cpu(),
            ):
                raise ValueError(
                    "CoSLight checkpoint neighbor mask does not match this scenario"
                )
        _model.use_legacy_collaboration = (
            format_version < 10
        )
        _model.use_learned_topk_collaboration = format_version >= 17
        _model.use_pressure_prior = format_version >= 11
        _model.use_local_actor_encoder = format_version >= 14
        if _model.use_pressure_prior:
            _model.pressure_prior_scale = float(
                training_config.get("pressure_prior_scale", PRESSURE_PRIOR_SCALE)
            )
            _model.hold_prior_bias = float(
                training_config.get("hold_prior_bias", HOLD_PRIOR_BIAS)
            )
        if format_version >= 10:
            _load_model_weights(checkpoint["model_state_dict"])
        else:
            incompatible = _model.load_state_dict(
                checkpoint["model_state_dict"], strict=False
            )
            if format_version >= 6:
                unexpected_missing = (
                    set(incompatible.missing_keys)
                    - {
                        "phase_scorer.weight",
                        "phase_actor_head.0.weight",
                        "phase_actor_head.0.bias",
                        "phase_actor_head.2.weight",
                        "phase_actor_head.2.bias",
                        "target_projection.weight",
                        "source_projection.weight",
                        "context_projection.weight",
                    }
                    - NEW_COLLAB_BIAS_PARAMS
                )
                if unexpected_missing or incompatible.unexpected_keys:
                    raise ValueError(
                        "CoSLight checkpoint model parameters are incompatible: "
                        f"missing={sorted(unexpected_missing)}, "
                        f"unexpected={sorted(incompatible.unexpected_keys)}"
                    )
        value_stats = checkpoint.get("value_normalization")
        if isinstance(value_stats, Mapping):
            _value_normalizer.load_state_dict(value_stats)
        elif load_optimizer:
            raise ValueError(
                "CoSLight checkpoint is missing value-normalization statistics"
            )
        if load_optimizer:
            actor_optimizer_state = checkpoint.get("actor_optimizer_state_dict")
            critic_optimizer_state = checkpoint.get("critic_optimizer_state_dict")
            if (
                _actor_optimizer is None
                or _critic_optimizer is None
                or not isinstance(actor_optimizer_state, Mapping)
                or not isinstance(critic_optimizer_state, Mapping)
            ):
                raise ValueError(
                    "CoSLight checkpoint is missing separate optimizer states"
                )
            _actor_optimizer.load_state_dict(actor_optimizer_state)
            _critic_optimizer.load_state_dict(critic_optimizer_state)
        _episode = int(checkpoint.get("episode", _episode))
        _policy_generation = int(
            checkpoint.get(
                "policy_generation",
                _episode // ACCUMULATE_EPISODES,
            )
        )
        stats = checkpoint.get("reward_stats", {})
        _reward_mean = float(stats.get("mean", _reward_mean))
        _reward_m2 = float(stats.get("m2", _reward_m2))
        _reward_count = int(stats.get("count", _reward_count))
        if load_optimizer:
            rng_state = checkpoint.get("rng_state")
            if isinstance(rng_state, Mapping):
                python_state = rng_state.get("python")
                if python_state is not None:
                    random.setstate(python_state)
                numpy_state = rng_state.get("numpy")
                if isinstance(numpy_state, Mapping) and isinstance(
                    numpy_state.get("state"), torch.Tensor
                ):
                    np.random.set_state(
                        (
                            str(numpy_state.get("bit_generator", "MT19937")),
                            numpy_state["state"].cpu().numpy().astype(np.uint32),
                            int(numpy_state.get("position", 0)),
                            int(numpy_state.get("has_gauss", 0)),
                            float(numpy_state.get("cached_gaussian", 0.0)),
                        )
                    )
                torch_state = rng_state.get("torch")
                if isinstance(torch_state, torch.Tensor):
                    torch.set_rng_state(torch_state.cpu())
    else:
        # Compatibility with original raw tensor-only state_dict checkpoints.
        _model.use_local_actor_encoder = False
        _model.load_state_dict(checkpoint, strict=False)
    logger.info("Loaded CoSLight checkpoint: %s", path)


def checkpoint_load(path: str) -> None:
    global _mode
    _load_checkpoint(path, load_optimizer=False)
    _mode = "model"
    _model.eval()


def load_checkpoint_metadata(path: str | Path) -> dict:
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(checkpoint, dict) or "model_state_dict" not in checkpoint:
        raise ValueError("CoSLight checkpoint must contain model_state_dict")
    return checkpoint


def save_checkpoint(path: str | Path) -> Path:
    return _save_checkpoint(path)


def finalize_training(path: str | Path) -> Path:
    """Train on any tail rollouts and atomically save the final checkpoint."""
    if (
        _model is None
        or _actor_optimizer is None
        or _critic_optimizer is None
        or _mode != "train"
    ):
        raise RuntimeError("CoSLight training is not initialized")
    while _buffer_episodes:
        _ppo_update(episode_count=min(ACCUMULATE_EPISODES, len(_buffer_episodes)))
    return _save_checkpoint(path)


def _green_remaining(intersections: Dict[str, Any], tls_id: str) -> float:
    intersection = intersections.get(tls_id)
    if not intersection or intersection.get("stage") != "GREEN":
        return -1.0
    current_phase = int(intersection.get("current_phase", 0))
    total_green = _phase_durations.get(tls_id, {}).get(
        current_phase, DEFAULT_GREEN_DURATION
    )
    return max(0.0, total_green - float(intersection.get("stage_elapsed", 0.0)))


def _is_lane_allowed(road_id: str, lane_index: int, type_id: str) -> bool:
    for lane in _edge_lanes.get(road_id, []):
        if int(lane.get("lane_index", -1)) == int(lane_index):
            allowed = lane.get("allowed_vehicle_type_ids") or []
            return type_id in allowed
    return False


def _build_vehicle_actions(payload: dict) -> dict:
    """Conservative rule guidance kept separate from the learned signal policy."""
    vehicles = payload.get("vehicles", {})
    intersections = payload.get("intersections", {})
    if not vehicles:
        return {}

    edge_queues: Dict[str, List[Tuple[int, float]]] = {}
    for intersection in intersections.values():
        for lane_id, lane in intersection.get("lanes", {}).items():
            if not isinstance(lane, dict):
                continue
            edge_id = lane.get("edge_id") or lane_id.rsplit("_", 1)[0]
            try:
                lane_index = int(lane_id.rsplit("_", 1)[1])
            except (IndexError, ValueError):
                continue
            edge_queues.setdefault(str(edge_id), []).append(
                (lane_index, float(lane.get("queue_length_m", 0.0)))
            )

    previous_results = payload.get("previous_action_results", {}).get("vehicles", {})
    actions = {}
    for vehicle_id, vehicle in vehicles.items():
        location = vehicle.get("location", {}) or {}
        motion = vehicle.get("motion", {}) or {}
        next_signal = vehicle.get("next_signal")
        road_id = str(location.get("road_id", ""))
        speed = float(motion.get("speed_mps", 0.0))
        allowed_speed = max(float(motion.get("allowed_speed_mps", 13.9)), 0.0)
        target_speed = None
        target_lane = None

        if next_signal:
            distance = float(next_signal.get("distance_m", 999.0))
            signal_state = str(next_signal.get("state", ""))
            green_left = _green_remaining(
                intersections, str(next_signal.get("intersection_id") or next_signal.get("tls_id", ""))
            )
            if signal_state in {"G", "g", "GREEN"} and green_left > 0.0 and distance > 5.0:
                eta = distance / max(speed, 0.1)
                target_speed = min(speed, allowed_speed) if eta <= green_left else min(
                    distance / green_left, allowed_speed
                )
            elif (
                signal_state in {"r", "R", "RED"}
                and 10.0 < distance < 150.0
                and speed > 2.0
            ):
                # Do not halve speed in one command; cap the requested reduction to
                # 2 m/s per 5-second decision interval. Vehicles already at or
                # below 2 m/s receive no speed-up command.
                target_speed = max(speed - 2.0, 2.0)

        current_lane = location.get("lane_index")
        type_id = str(vehicle.get("type_id", ""))
        if (
            speed >= 0.5
            and road_id
            and not road_id.startswith(":")
            and next_signal
            and float(next_signal.get("distance_m", 999.0)) > 50.0
            and current_lane is not None
        ):
            queues = edge_queues.get(road_id, [])
            current_queue = next(
                (queue for lane, queue in queues if lane == int(current_lane)), None
            )
            candidates = [
                (queue, lane)
                for lane, queue in queues
                if lane != int(current_lane)
                and _is_lane_allowed(road_id, lane, type_id)
            ]
            if current_queue is not None and candidates:
                best_queue, best_lane = min(candidates)
                if current_queue > max(best_queue * 1.5, best_queue + 5.0):
                    target_lane = best_lane
                    previous = previous_results.get(vehicle_id, {})
                    requested = (previous.get("requested") or {}).get(
                        "target_lane_index"
                    )
                    if (
                        previous.get("lane_change_status") == "not_completed"
                        and requested == target_lane
                    ):
                        target_lane = None

        if target_speed is not None or target_lane is not None:
            actions[vehicle_id] = {
                "target_speed_mps": (
                    float(np.clip(target_speed, 0.0, allowed_speed))
                    if target_speed is not None
                    else None
                ),
                "target_lane_index": target_lane,
            }
    return actions
