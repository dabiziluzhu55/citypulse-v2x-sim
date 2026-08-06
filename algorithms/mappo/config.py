from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Iterable

from algorithms.mappo.features import (
    CENTRALIZED_STATE_SCHEMA,
    IPPO_V8_IDENTITY_OFFSET,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


MAPPO_V1_MODEL_VERSION = "mappo_v1"
MAPPO_V2_SHARED_MODEL_VERSION = "mappo_v2_isomorphic_shared_actor"
MAPPO_V2_RESIDUAL_MODEL_VERSION = "mappo_v2_isomorphic_residual_actor"
REWARD_SCOPE_LOCAL = "local"
REWARD_SCOPE_SHARED_TEAM = "shared_team"
TEAM_REWARD_SCHEMA = "v5a_team_mean_raw_then_clip_v1"
JOINT_STEP_SCHEMA = "synchronized_all_intersections_v1"
TEAM_REWARD_AGGREGATION = "mean_raw_then_clip"
TEAM_REWARD_CLIP_STAGE = "after_team_aggregation"
LOCAL_REWARD_AGGREGATION = "per_agent_raw_then_clip"
LOCAL_REWARD_CLIP_STAGE = "before_local_return"
COOPERATIVE_MODEL_VERSION = "cooperative_joint_v1"
COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION = (
    "cooperative_joint_owner_conditioned_v1"
)
COOPERATIVE_M1_MODEL_VERSION = "cooperative_m1_v1"
COOPERATIVE_MODEL_VERSIONS = frozenset(
    {
        COOPERATIVE_MODEL_VERSION,
        COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
        COOPERATIVE_M1_MODEL_VERSION,
    }
)
MODEL_ACTOR_VARIANTS = {
    MAPPO_V1_MODEL_VERSION: "shared",
    MAPPO_V2_SHARED_MODEL_VERSION: "shared",
    MAPPO_V2_RESIDUAL_MODEL_VERSION: "residual",
    COOPERATIVE_MODEL_VERSION: "shared",
    COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION: "shared",
    COOPERATIVE_M1_MODEL_VERSION: "shared",
}


@dataclass(frozen=True)
class MAPPOConfig:
    intersection_ids: tuple[str, ...]
    critic_scope: str = "global"
    model_version: str = MAPPO_V1_MODEL_VERSION
    actor_variant: str = "shared"
    residual_hidden_dim: int = 32
    identity_offset: int = IPPO_V8_IDENTITY_OFFSET
    phase_feature_schema: str = (
        "connection_pressure_service_age_eta_demand_v2"
    )
    phase_feature_dim: int = 11
    max_action_dim: int = 4
    hidden_dim: int = 128
    action_interval_s: float = 15.0
    max_green_factor: float = 2.0
    effective_demand_enabled: bool = True
    actor_lr: float = 3e-4
    critic_lr: float = 1e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    ppo_clip: float = 0.2
    entropy_coef: float = 0.01
    ppo_epochs: int = 4
    minibatch_size: int = 128
    max_grad_norm: float = 0.5
    huber_delta: float = 10.0
    centralized_state_schema: str = CENTRALIZED_STATE_SCHEMA
    reward_scope: str = REWARD_SCOPE_LOCAL
    team_reward_schema: str = TEAM_REWARD_SCHEMA
    joint_step_schema: str = JOINT_STEP_SCHEMA
    critic_target_scope: str = "local_return"
    m1_target_mode: str = "shared"
    m1_arm: str = "m1_0"
    m1_local_weight: float = 0.0
    m1_neighbor_weight: float = 0.0
    m1_team_weight: float = 1.0
    m1_adjacency_path: str | None = None

    def __post_init__(self) -> None:
        ids = tuple(str(value) for value in self.intersection_ids)
        object.__setattr__(self, "intersection_ids", ids)
        if not ids:
            raise ValueError("MAPPO requires at least one controlled intersection")
        if any(not value for value in ids):
            raise ValueError("intersection identifiers must be non-empty")
        if len(ids) != len(set(ids)):
            raise ValueError("intersection identifiers must be unique")
        if self.critic_scope not in {"local", "global"}:
            raise ValueError("critic scope must be 'local' or 'global'")
        if self.reward_scope not in {
            REWARD_SCOPE_LOCAL,
            REWARD_SCOPE_SHARED_TEAM,
        }:
            raise ValueError("reward scope must be 'local' or 'shared_team'")
        if self.critic_target_scope not in {"local_return", "team_return"}:
            raise ValueError(
                "critic target scope must be 'local_return' or 'team_return'"
            )
        if self.reward_scope == REWARD_SCOPE_SHARED_TEAM:
            if self.critic_target_scope != "team_return":
                raise ValueError("shared_team requires team_return target")
            if self.model_version not in COOPERATIVE_MODEL_VERSIONS:
                raise ValueError(
                    "shared_team requires a cooperative model version"
                )
        else:
            if self.critic_target_scope != "local_return":
                raise ValueError("local requires local_return target")
            if self.model_version in COOPERATIVE_MODEL_VERSIONS:
                raise ValueError(
                    "cooperative model versions require shared_team reward scope"
                )
        expected_actor_variant = MODEL_ACTOR_VARIANTS.get(self.model_version)
        if expected_actor_variant is None:
            raise ValueError(f"unknown MAPPO model version: {self.model_version!r}")
        if self.actor_variant != expected_actor_variant:
            raise ValueError(
                "model version and actor variant mismatch: "
                f"{self.model_version!r} requires {expected_actor_variant!r}"
            )
        if int(self.residual_hidden_dim) != 32:
            raise ValueError("MAPPO-v2 residual hidden dimension must be 32")
        if int(self.identity_offset) != IPPO_V8_IDENTITY_OFFSET:
            raise ValueError(
                "MAPPO-v2 identity offset must match the IPPO-v8 schema"
            )
        if self.model_version == COOPERATIVE_M1_MODEL_VERSION:
            if self.reward_scope != REWARD_SCOPE_SHARED_TEAM:
                raise ValueError(
                    "cooperative_m1 requires shared_team reward"
                )
            if self.critic_scope != "global":
                raise ValueError(
                    "cooperative_m1 requires global critic scope"
                )
            if self.m1_target_mode not in {"per_agent", "m1_0_scalar"}:
                raise ValueError(
                    "cooperative_m1 target mode must be per_agent or m1_0_scalar"
                )
            if self.m1_target_mode == "m1_0_scalar":
                if (
                    self.m1_local_weight,
                    self.m1_neighbor_weight,
                    self.m1_team_weight,
                ) != (0.0, 0.0, 1.0):
                    raise ValueError(
                        "m1_0 requires pure team advantage weights"
                    )
            else:
                weights = (
                    self.m1_local_weight,
                    self.m1_neighbor_weight,
                    self.m1_team_weight,
                )
                if any(not (0.0 <= w <= 1.0) for w in weights):
                    raise ValueError("m1 weights must be in [0,1]")
                if abs(sum(weights) - 1.0) > 1e-6:
                    raise ValueError("m1 weights must sum to 1")
                if self.m1_arm == "m1_a" and self.m1_neighbor_weight != 0.0:
                    raise ValueError("m1_a requires zero neighbor weight")
                if self.m1_arm == "m1_b" and self.m1_neighbor_weight <= 0.0:
                    raise ValueError("m1_b requires positive neighbor weight")
                if self.m1_adjacency_path is None:
                    raise ValueError(
                        "m1 per-agent arms require adjacency path"
                    )

    @property
    def obs_dim(self) -> int:
        """Fixed 20-slot observation dimension (IPPO-v8 identity contract)."""
        return 112 + len(IDENTITY_SLOT_IDS)

    @property
    def requires_shared_values(self) -> bool:
        """Whether one literal team value is broadcast to every owner."""

        return (
            self.reward_scope == REWARD_SCOPE_SHARED_TEAM
            and self.critic_scope == "global"
            and self.model_version == COOPERATIVE_MODEL_VERSION
        )


def assert_seed_disjoint(
    training_seeds: Iterable[int], evaluation_seeds: Iterable[int]
) -> None:
    overlap = sorted(
        {int(value) for value in training_seeds}
        & {int(value) for value in evaluation_seeds}
    )
    if overlap:
        raise ValueError(
            "training and evaluation seed sets overlap: "
            + ", ".join(str(value) for value in overlap)
        )


def configuration_signature(config: MAPPOConfig) -> str:
    frozen = asdict(config)
    frozen.update(objective_provenance(config))
    payload = json.dumps(
        frozen, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def algorithm_label(config: MAPPOConfig) -> str:
    if config.reward_scope == REWARD_SCOPE_SHARED_TEAM:
        return (
            "cooperative_mappo"
            if config.critic_scope == "global"
            else "cooperative_ippo"
        )
    return (
        "cc_ippo_local_reward"
        if config.critic_scope == "global"
        else "ippo_local_reward"
    )


def objective_provenance(config: MAPPOConfig) -> dict[str, str]:
    shared = config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    return {
        "algorithm_variant": algorithm_label(config),
        "reward_scope": config.reward_scope,
        "team_reward_schema": (
            config.team_reward_schema if shared else "N/A"
        ),
        "reward_aggregation": (
            TEAM_REWARD_AGGREGATION
            if shared
            else LOCAL_REWARD_AGGREGATION
        ),
        "reward_clip_stage": (
            TEAM_REWARD_CLIP_STAGE
            if shared
            else LOCAL_REWARD_CLIP_STAGE
        ),
        "critic_target_scope": config.critic_target_scope,
        "joint_step_schema": (
            config.joint_step_schema if shared else "N/A"
        ),
    }
