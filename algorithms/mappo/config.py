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


REWARD_SCOPE_LOCAL = "local"
REWARD_SCOPE_SHARED_TEAM = "shared_team"
TEAM_REWARD_SCHEMA = "v5a_team_mean_raw_then_clip_v1"
JOINT_STEP_SCHEMA = "synchronized_all_intersections_v1"
TEAM_REWARD_AGGREGATION = "mean_raw_then_clip"
TEAM_REWARD_CLIP_STAGE = "after_team_aggregation"
LOCAL_REWARD_AGGREGATION = "per_agent_raw_then_clip"
LOCAL_REWARD_CLIP_STAGE = "before_local_return"
COOPERATIVE_MODEL_VERSION = "cooperative_joint_v1"
COOPERATIVE_MODEL_VERSIONS = frozenset({COOPERATIVE_MODEL_VERSION})
MODEL_ACTOR_VARIANTS = {
    COOPERATIVE_MODEL_VERSION: "shared",
}


@dataclass(frozen=True)
class MAPPOConfig:
    intersection_ids: tuple[str, ...]
    critic_scope: str = "global"
    model_version: str = COOPERATIVE_MODEL_VERSION
    actor_variant: str = "shared"
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
    reward_scope: str = REWARD_SCOPE_SHARED_TEAM
    team_reward_schema: str = TEAM_REWARD_SCHEMA
    joint_step_schema: str = JOINT_STEP_SCHEMA
    critic_target_scope: str = "team_return"

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
        if int(self.identity_offset) != IPPO_V8_IDENTITY_OFFSET:
            raise ValueError(
                "identity offset must match the IPPO-v8 schema"
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
