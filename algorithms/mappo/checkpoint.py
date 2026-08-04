from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path
import random
from typing import Any, Mapping

import numpy as np
import torch

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSIONS,
    LOCAL_REWARD_AGGREGATION,
    LOCAL_REWARD_CLIP_STAGE,
    MAPPO_V1_MODEL_VERSION,
    MODEL_ACTOR_VARIANTS,
    MAPPOConfig,
    REWARD_SCOPE_LOCAL,
    REWARD_SCOPE_SHARED_TEAM,
    TEAM_REWARD_AGGREGATION,
    TEAM_REWARD_CLIP_STAGE,
    objective_provenance,
)
from algorithms.mappo.features import IPPO_V8_IDENTITY_OFFSET
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.trainer import MAPPOTrainer


CHECKPOINT_FORMAT_VERSION = 2
SUPPORTED_CHECKPOINT_FORMAT_VERSIONS = frozenset({1, 2})
NOT_APPLICABLE = "N/A"


def _legacy_v1_objective_metadata(critic_scope: str) -> dict[str, str]:
    base_variant = (
        "cc_ippo_local_reward"
        if critic_scope == "global"
        else "ippo_local_reward"
    )
    return {
        "algorithm_variant": f"{base_variant}_v1",
        "reward_scope": REWARD_SCOPE_LOCAL,
        "team_reward_schema": NOT_APPLICABLE,
        "reward_aggregation": LOCAL_REWARD_AGGREGATION,
        "reward_clip_stage": LOCAL_REWARD_CLIP_STAGE,
        "critic_target_scope": "local_return",
        "joint_step_schema": NOT_APPLICABLE,
    }


class CheckpointCompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class CheckpointMetadata:
    model_version: str
    critic_scope: str
    episode: int
    policy_generation: int
    intersection_ids: tuple[str, ...]
    obs_dim: int
    phase_feature_dim: int
    max_action_dim: int
    hidden_dim: int
    phase_feature_schema: str
    centralized_state_schema: str
    local_observation_schema: str
    action_interval_s: float
    max_green_factor: float
    effective_demand_enabled: bool
    actor_lr: float
    critic_lr: float
    gamma: float
    gae_lambda: float
    ppo_clip: float
    entropy_coef: float
    ppo_epochs: int
    minibatch_size: int
    max_grad_norm: float
    huber_delta: float
    reward_definition: str
    actor_init_seed: int
    critic_init_seed: int
    training_seed_start: int
    training_seed_end: int
    training_periods: tuple[str, ...]
    algorithm_variant: str
    reward_scope: str
    team_reward_schema: str
    reward_aggregation: str
    reward_clip_stage: str
    critic_target_scope: str
    joint_step_schema: str
    transfer_source_sha256: str | None = None
    training_workers: int | None = None
    episode_duration_s: float | None = None
    actor_variant: str | None = None
    residual_hidden_dim: int | None = None
    identity_offset: int | None = None
    residual_init_seed: int | None = None
    m1_target_mode: str | None = None
    m1_arm: str | None = None
    m1_local_weight: float | None = None
    m1_neighbor_weight: float | None = None
    m1_team_weight: float | None = None
    m1_adjacency_path: str | None = None

    def __post_init__(self) -> None:
        intersection_ids = tuple(str(value) for value in self.intersection_ids)
        periods = tuple(str(value) for value in self.training_periods)
        object.__setattr__(self, "intersection_ids", intersection_ids)
        object.__setattr__(self, "training_periods", periods)
        if int(self.episode) < 0 or int(self.policy_generation) < 0:
            raise ValueError(
                "episode and policy generation must be non-negative"
            )
        if int(self.training_seed_end) < int(self.training_seed_start):
            raise ValueError("training seed range is reversed")
        if not periods or any(not value for value in periods):
            raise ValueError("training periods must be non-empty")
        if (
            not intersection_ids
            or any(not value for value in intersection_ids)
            or len(intersection_ids) != len(set(intersection_ids))
        ):
            raise ValueError(
                "checkpoint intersection identifiers must be non-empty and unique"
            )
        if self.critic_scope not in {"local", "global"}:
            raise ValueError("checkpoint critic scope must be local or global")
        if self.reward_scope not in {
            REWARD_SCOPE_LOCAL,
            REWARD_SCOPE_SHARED_TEAM,
        }:
            raise ValueError("checkpoint reward scope is invalid")
        if self.critic_target_scope not in {
            "local_return",
            "team_return",
        }:
            raise ValueError("checkpoint critic target scope is invalid")
        legacy_variant = self.algorithm_variant.endswith("_v1")
        if self.reward_scope == REWARD_SCOPE_SHARED_TEAM:
            expected_variant = (
                "cooperative_mappo"
                if self.critic_scope == "global"
                else "cooperative_ippo"
            )
            if self.algorithm_variant != expected_variant:
                raise ValueError(
                    "checkpoint reward scope and algorithm variant mismatch"
                )
            if self.model_version not in COOPERATIVE_MODEL_VERSIONS:
                raise ValueError(
                    "checkpoint shared team reward requires cooperative model"
                )
            if self.critic_target_scope != "team_return":
                raise ValueError(
                    "checkpoint critic target scope must be team_return"
                )
            if self.team_reward_schema == NOT_APPLICABLE:
                raise ValueError(
                    "checkpoint team reward schema is not applicable"
                )
            if self.reward_aggregation != TEAM_REWARD_AGGREGATION:
                raise ValueError("checkpoint reward aggregation is invalid")
            if self.reward_clip_stage != TEAM_REWARD_CLIP_STAGE:
                raise ValueError("checkpoint reward clip stage is invalid")
            if self.joint_step_schema == NOT_APPLICABLE:
                raise ValueError(
                    "checkpoint joint step schema is not applicable"
                )
            if legacy_variant:
                raise ValueError(
                    "legacy checkpoint cannot declare shared team reward"
                )
        else:
            base_variant = (
                "cc_ippo_local_reward"
                if self.critic_scope == "global"
                else "ippo_local_reward"
            )
            expected_variant = (
                f"{base_variant}_v1" if legacy_variant else base_variant
            )
            if self.algorithm_variant != expected_variant:
                raise ValueError(
                    "checkpoint reward scope and algorithm variant mismatch"
                )
            if (
                not legacy_variant
                and self.model_version in COOPERATIVE_MODEL_VERSIONS
            ):
                raise ValueError(
                    "checkpoint cooperative model requires shared team reward"
                )
            if self.critic_target_scope != "local_return":
                raise ValueError(
                    "checkpoint critic target scope must be local_return"
                )
            if self.team_reward_schema != NOT_APPLICABLE:
                raise ValueError(
                    "checkpoint local reward team reward schema must be N/A"
                )
            if self.reward_aggregation != LOCAL_REWARD_AGGREGATION:
                raise ValueError("checkpoint reward aggregation is invalid")
            if self.reward_clip_stage != LOCAL_REWARD_CLIP_STAGE:
                raise ValueError("checkpoint reward clip stage is invalid")
            if self.joint_step_schema != NOT_APPLICABLE:
                raise ValueError(
                    "checkpoint local reward joint step schema must be N/A"
                )
        expected_actor_variant = MODEL_ACTOR_VARIANTS.get(self.model_version)
        if expected_actor_variant is None:
            raise ValueError(
                f"unknown checkpoint model version: {self.model_version!r}"
            )
        if self.model_version == MAPPO_V1_MODEL_VERSION:
            if self.actor_variant not in {None, expected_actor_variant}:
                raise ValueError(
                    "checkpoint model version and actor variant mismatch"
                )
            if self.residual_hidden_dim not in {None, 32}:
                raise ValueError(
                    "checkpoint residual hidden dimension is invalid"
                )
            if self.identity_offset not in {
                None,
                IPPO_V8_IDENTITY_OFFSET,
            }:
                raise ValueError("checkpoint identity offset is invalid")
            if self.residual_init_seed is not None:
                raise ValueError(
                    "legacy shared Actor cannot have a residual initialization seed"
                )
        else:
            if self.actor_variant != expected_actor_variant:
                raise ValueError(
                    "checkpoint model version and actor variant mismatch"
                )
            if self.residual_hidden_dim != 32:
                raise ValueError(
                    "checkpoint residual hidden dimension must be 32"
                )
            if self.identity_offset != IPPO_V8_IDENTITY_OFFSET:
                raise ValueError(
                    "checkpoint identity offset does not match IPPO-v8"
                )
            if (
                expected_actor_variant == "residual"
                and self.residual_init_seed is None
            ):
                raise ValueError(
                    "checkpoint residual initialization seed is missing"
                )
            if (
                expected_actor_variant == "shared"
                and self.residual_init_seed is not None
            ):
                raise ValueError(
                    "shared Actor cannot have a residual initialization seed"
                )
        positive_dimensions = (
            self.obs_dim,
            self.phase_feature_dim,
            self.max_action_dim,
            self.hidden_dim,
            self.ppo_epochs,
            self.minibatch_size,
        )
        if any(int(value) <= 0 for value in positive_dimensions):
            raise ValueError("checkpoint dimensions must be positive")
        positive_scalars = (
            self.action_interval_s,
            self.max_green_factor,
            self.actor_lr,
            self.critic_lr,
            self.ppo_clip,
            self.max_grad_norm,
            self.huber_delta,
        )
        if any(float(value) <= 0.0 for value in positive_scalars):
            raise ValueError("checkpoint positive hyperparameters are invalid")
        if not 0.0 <= float(self.gamma) <= 1.0:
            raise ValueError("checkpoint gamma is outside [0, 1]")
        if not 0.0 <= float(self.gae_lambda) <= 1.0:
            raise ValueError("checkpoint GAE lambda is outside [0, 1]")
        if float(self.entropy_coef) < 0.0:
            raise ValueError("checkpoint entropy coefficient is negative")
        if self.training_workers is not None and int(self.training_workers) <= 0:
            raise ValueError("checkpoint training worker count is invalid")
        if (
            self.episode_duration_s is not None
            and float(self.episode_duration_s) <= 0.0
        ):
            raise ValueError("checkpoint episode duration is invalid")
        required_strings = (
            self.model_version,
            self.algorithm_variant,
            self.phase_feature_schema,
            self.centralized_state_schema,
            self.local_observation_schema,
            self.reward_definition,
            self.reward_scope,
            self.team_reward_schema,
            self.reward_aggregation,
            self.reward_clip_stage,
            self.critic_target_scope,
            self.joint_step_schema,
        )
        if any(not str(value) for value in required_strings):
            raise ValueError("checkpoint schema metadata must be non-empty")

    @classmethod
    def from_config(
        cls,
        config: MAPPOConfig,
        *,
        episode: int,
        policy_generation: int,
        actor_init_seed: int,
        critic_init_seed: int,
        training_seed_start: int,
        training_seed_end: int,
        training_periods: tuple[str, ...],
        local_observation_schema: str,
        reward_definition: str,
        transfer_source_sha256: str | None = None,
        training_workers: int | None = None,
        episode_duration_s: float | None = None,
        residual_init_seed: int | None = None,
    ) -> "CheckpointMetadata":
        periods = tuple(str(value) for value in training_periods)
        objective = objective_provenance(config)
        return cls(
            model_version=config.model_version,
            critic_scope=config.critic_scope,
            episode=int(episode),
            policy_generation=int(policy_generation),
            intersection_ids=config.intersection_ids,
            obs_dim=config.obs_dim,
            phase_feature_dim=config.phase_feature_dim,
            max_action_dim=config.max_action_dim,
            hidden_dim=config.hidden_dim,
            phase_feature_schema=config.phase_feature_schema,
            centralized_state_schema=config.centralized_state_schema,
            local_observation_schema=str(local_observation_schema),
            action_interval_s=config.action_interval_s,
            max_green_factor=config.max_green_factor,
            effective_demand_enabled=config.effective_demand_enabled,
            actor_lr=config.actor_lr,
            critic_lr=config.critic_lr,
            gamma=config.gamma,
            gae_lambda=config.gae_lambda,
            ppo_clip=config.ppo_clip,
            entropy_coef=config.entropy_coef,
            ppo_epochs=config.ppo_epochs,
            minibatch_size=config.minibatch_size,
            max_grad_norm=config.max_grad_norm,
            huber_delta=config.huber_delta,
            reward_definition=str(reward_definition),
            actor_init_seed=int(actor_init_seed),
            critic_init_seed=int(critic_init_seed),
            training_seed_start=int(training_seed_start),
            training_seed_end=int(training_seed_end),
            training_periods=periods,
            algorithm_variant=objective["algorithm_variant"],
            reward_scope=objective["reward_scope"],
            team_reward_schema=objective["team_reward_schema"],
            reward_aggregation=objective["reward_aggregation"],
            reward_clip_stage=objective["reward_clip_stage"],
            critic_target_scope=objective["critic_target_scope"],
            joint_step_schema=objective["joint_step_schema"],
            transfer_source_sha256=transfer_source_sha256,
            training_workers=(
                None if training_workers is None else int(training_workers)
            ),
            episode_duration_s=(
                None
                if episode_duration_s is None
                else float(episode_duration_s)
            ),
            actor_variant=config.actor_variant,
            residual_hidden_dim=config.residual_hidden_dim,
            identity_offset=config.identity_offset,
            residual_init_seed=(
                None
                if residual_init_seed is None
                else int(residual_init_seed)
            ),
            m1_target_mode=config.m1_target_mode,
            m1_arm=config.m1_arm,
            m1_local_weight=config.m1_local_weight,
            m1_neighbor_weight=config.m1_neighbor_weight,
            m1_team_weight=config.m1_team_weight,
            m1_adjacency_path=config.m1_adjacency_path,
        )


def state_dict_digest(state_dict: Mapping[str, torch.Tensor]) -> str:
    """Return the canonical digest used for immutable policy snapshots."""

    digest = hashlib.sha256()
    for name, tensor in sorted(state_dict.items()):
        if not isinstance(tensor, torch.Tensor):
            raise TypeError(f"policy state {name!r} must be a tensor")
        value = tensor.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(json.dumps(tuple(value.shape)).encode("ascii"))
        digest.update(value.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def policy_digest(policy: MAPPOPolicy) -> str:
    return state_dict_digest(policy.state_dict())


def _rng_state() -> dict[str, Any]:
    return {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": torch.cuda.get_rng_state_all()
        if torch.cuda.is_available()
        else None,
    }


def _restore_rng_state(state: Mapping[str, Any]) -> None:
    required = {"python", "numpy", "torch_cpu", "torch_cuda"}
    missing = sorted(required - set(state))
    if missing:
        raise CheckpointCompatibilityError(
            "checkpoint RNG state is incomplete: " + ", ".join(missing)
        )
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch_cpu"])
    if state["torch_cuda"] is not None and torch.cuda.is_available():
        # Flush any queued lazy CUDA seed callbacks before applying the exact
        # checkpoint state. Otherwise a prior manual_seed_all() can run after
        # set_rng_state_all() during first-use initialization and overwrite it.
        torch.cuda.init()
        torch.cuda.set_rng_state_all(state["torch_cuda"])


def save_checkpoint(
    path: str | os.PathLike[str],
    policy: MAPPOPolicy,
    trainer: MAPPOTrainer,
    metadata: CheckpointMetadata,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    payload = {
        "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
        "metadata": asdict(metadata),
        "policy_state_dict": policy.state_dict(),
        "actor_optimizer_state_dict": trainer.actor_optimizer.state_dict(),
        "critic_optimizer_state_dict": trainer.critic_optimizer.state_dict(),
        "rng_state": _rng_state(),
    }
    try:
        with temporary.open("wb") as stream:
            torch.save(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _metadata_from_payload(payload: Mapping[str, Any]) -> CheckpointMetadata:
    raw = payload.get("metadata")
    if not isinstance(raw, Mapping):
        raise CheckpointCompatibilityError("checkpoint metadata is missing")
    format_version = payload.get("checkpoint_format_version")
    normalized = dict(raw)
    if format_version == 1:
        critic_scope = normalized.get("critic_scope")
        if critic_scope not in {"local", "global"}:
            raise CheckpointCompatibilityError(
                "legacy checkpoint critic scope is missing or invalid"
            )
        normalized.update(_legacy_v1_objective_metadata(critic_scope))
    metadata_fields = fields(CheckpointMetadata)
    required = {
        field.name
        for field in metadata_fields
        if field.default is MISSING and field.default_factory is MISSING
    }
    missing = sorted(required - set(normalized))
    if missing:
        raise CheckpointCompatibilityError(
            "checkpoint missing metadata: " + ", ".join(missing)
        )
    try:
        for field in metadata_fields:
            if field.name not in normalized and field.default is not MISSING:
                normalized[field.name] = field.default
        normalized["intersection_ids"] = tuple(normalized["intersection_ids"])
        normalized["training_periods"] = tuple(normalized["training_periods"])
        return CheckpointMetadata(**normalized)
    except (TypeError, ValueError) as exc:
        raise CheckpointCompatibilityError(
            f"checkpoint metadata is malformed: {exc}"
        ) from exc


def _load_payload(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    payload = torch.load(Path(path), map_location="cpu", weights_only=False)
    if not isinstance(payload, Mapping):
        raise CheckpointCompatibilityError(
            "checkpoint payload must be a mapping"
        )
    if (
        payload.get("checkpoint_format_version")
        not in SUPPORTED_CHECKPOINT_FORMAT_VERSIONS
    ):
        raise CheckpointCompatibilityError("checkpoint format version mismatch")
    return payload


def read_checkpoint_metadata(
    path: str | os.PathLike[str],
) -> CheckpointMetadata:
    """Read and validate checkpoint metadata without constructing a policy."""

    return _metadata_from_payload(_load_payload(path))


def _validate_intersection_scope(
    metadata: CheckpointMetadata, expected_config: MAPPOConfig
) -> None:
    """Checkpoint identity contract: controlled subset within trained IDs.

    The MAPPO model uses the fixed 20-slot IPPO-v8 identity contract, so an
    evaluation/deployment subset must be a subset of the checkpoint training
    set.  A same-size configuration must match the saved order exactly
    (resume-training contract); proper subsets are allowed for zero-shot
    scenario scopes (east_dense / west_dense / custom subsets).
    """
    saved_ids = tuple(metadata.intersection_ids)
    expected_ids = tuple(expected_config.intersection_ids)
    if not set(expected_ids) <= set(saved_ids):
        raise CheckpointCompatibilityError(
            "checkpoint was not trained on this intersection subset: "
            f"current={expected_ids}, saved={saved_ids}"
        )
    if len(expected_ids) == len(saved_ids) and expected_ids != saved_ids:
        raise CheckpointCompatibilityError(
            "checkpoint intersection order mismatch: "
            f"saved={saved_ids}, expected={expected_ids}"
        )


def _validate_metadata(
    metadata: CheckpointMetadata,
    checkpoint_format_version: int,
    expected_config: MAPPOConfig,
    expected_local_observation_schema: str,
    expected_reward_definition: str | None,
    expected_residual_init_seed: int | None,
) -> None:
    if (
        checkpoint_format_version == 1
        and expected_config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    ):
        raise CheckpointCompatibilityError(
            "legacy checkpoint cannot resume vanilla cooperative training"
        )
    expected_objective = (
        _legacy_v1_objective_metadata(expected_config.critic_scope)
        if checkpoint_format_version == 1
        else objective_provenance(expected_config)
    )
    _validate_intersection_scope(metadata, expected_config)
    checks = (
        (
            "algorithm variant",
            metadata.algorithm_variant,
            expected_objective["algorithm_variant"],
        ),
        (
            "reward scope",
            metadata.reward_scope,
            expected_objective["reward_scope"],
        ),
        (
            "team reward schema",
            metadata.team_reward_schema,
            expected_objective["team_reward_schema"],
        ),
        (
            "reward aggregation",
            metadata.reward_aggregation,
            expected_objective["reward_aggregation"],
        ),
        (
            "reward clip stage",
            metadata.reward_clip_stage,
            expected_objective["reward_clip_stage"],
        ),
        (
            "critic target scope",
            metadata.critic_target_scope,
            expected_objective["critic_target_scope"],
        ),
        (
            "joint step schema",
            metadata.joint_step_schema,
            expected_objective["joint_step_schema"],
        ),
        ("model version", metadata.model_version, expected_config.model_version),
        ("critic scope", metadata.critic_scope, expected_config.critic_scope),
        ("observation dimension", metadata.obs_dim, expected_config.obs_dim),
        (
            "phase feature dimension",
            metadata.phase_feature_dim,
            expected_config.phase_feature_dim,
        ),
        (
            "action dimension",
            metadata.max_action_dim,
            expected_config.max_action_dim,
        ),
        ("hidden dimension", metadata.hidden_dim, expected_config.hidden_dim),
        (
            "phase feature schema",
            metadata.phase_feature_schema,
            expected_config.phase_feature_schema,
        ),
        (
            "centralized state schema",
            metadata.centralized_state_schema,
            expected_config.centralized_state_schema,
        ),
        (
            "local observation schema",
            metadata.local_observation_schema,
            expected_local_observation_schema,
        ),
        (
            "action interval",
            metadata.action_interval_s,
            expected_config.action_interval_s,
        ),
        (
            "maximum green factor",
            metadata.max_green_factor,
            expected_config.max_green_factor,
        ),
        (
            "effective demand flag",
            metadata.effective_demand_enabled,
            expected_config.effective_demand_enabled,
        ),
        ("actor learning rate", metadata.actor_lr, expected_config.actor_lr),
        ("critic learning rate", metadata.critic_lr, expected_config.critic_lr),
        ("gamma", metadata.gamma, expected_config.gamma),
        ("GAE lambda", metadata.gae_lambda, expected_config.gae_lambda),
        ("PPO clip", metadata.ppo_clip, expected_config.ppo_clip),
        (
            "entropy coefficient",
            metadata.entropy_coef,
            expected_config.entropy_coef,
        ),
        ("PPO epochs", metadata.ppo_epochs, expected_config.ppo_epochs),
        (
            "minibatch size",
            metadata.minibatch_size,
            expected_config.minibatch_size,
        ),
        (
            "gradient norm",
            metadata.max_grad_norm,
            expected_config.max_grad_norm,
        ),
        ("Huber delta", metadata.huber_delta, expected_config.huber_delta),
    )
    for label, saved, expected in checks:
        if saved != expected:
            raise CheckpointCompatibilityError(
                f"checkpoint {label} mismatch: saved={saved!r}, expected={expected!r}"
            )
    if metadata.actor_variant is not None:
        v2_checks = (
            (
                "actor variant",
                metadata.actor_variant,
                expected_config.actor_variant,
            ),
            (
                "residual hidden dimension",
                metadata.residual_hidden_dim,
                expected_config.residual_hidden_dim,
            ),
            (
                "identity offset",
                metadata.identity_offset,
                expected_config.identity_offset,
            ),
        )
        for label, saved, expected in v2_checks:
            if saved != expected:
                raise CheckpointCompatibilityError(
                    f"checkpoint {label} mismatch: "
                    f"saved={saved!r}, expected={expected!r}"
                )
    if metadata.m1_target_mode is not None:
        m1_checks = (
            ("m1 target mode", metadata.m1_target_mode, expected_config.m1_target_mode),
            ("m1 arm", metadata.m1_arm, expected_config.m1_arm),
            ("m1 local weight", metadata.m1_local_weight, expected_config.m1_local_weight),
            ("m1 neighbor weight", metadata.m1_neighbor_weight, expected_config.m1_neighbor_weight),
            ("m1 team weight", metadata.m1_team_weight, expected_config.m1_team_weight),
            ("m1 adjacency path", metadata.m1_adjacency_path, expected_config.m1_adjacency_path),
        )
        for label, saved, expected in m1_checks:
            if saved != expected:
                raise CheckpointCompatibilityError(
                    f"checkpoint {label} mismatch: "
                    f"saved={saved!r}, expected={expected!r}"
                )
    if metadata.actor_variant == "residual":
        if expected_residual_init_seed is None:
            raise CheckpointCompatibilityError(
                "expected residual initialization seed is required"
            )
        if metadata.residual_init_seed != int(expected_residual_init_seed):
            raise CheckpointCompatibilityError(
                "checkpoint residual initialization seed mismatch: "
                f"saved={metadata.residual_init_seed!r}, "
                f"expected={int(expected_residual_init_seed)!r}"
            )
    if (
        expected_reward_definition is not None
        and metadata.reward_definition != expected_reward_definition
    ):
        raise CheckpointCompatibilityError(
            "checkpoint reward definition mismatch: "
            f"saved={metadata.reward_definition!r}, "
            f"expected={expected_reward_definition!r}"
        )


def load_checkpoint(
    path: str | os.PathLike[str],
    policy: MAPPOPolicy,
    trainer: MAPPOTrainer,
    *,
    expected_config: MAPPOConfig,
    expected_local_observation_schema: str,
    expected_reward_definition: str | None = None,
    expected_residual_init_seed: int | None = None,
    expected_metadata: CheckpointMetadata | None = None,
    restore_rng: bool = True,
) -> CheckpointMetadata:
    payload = _load_payload(path)
    metadata = _metadata_from_payload(payload)
    if expected_metadata is not None and metadata != expected_metadata:
        raise CheckpointCompatibilityError(
            "checkpoint metadata changed after resume preflight"
        )
    _validate_metadata(
        metadata,
        int(payload["checkpoint_format_version"]),
        expected_config,
        expected_local_observation_schema,
        expected_reward_definition,
        expected_residual_init_seed,
    )
    required = {
        "policy_state_dict",
        "actor_optimizer_state_dict",
        "critic_optimizer_state_dict",
        "rng_state",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise CheckpointCompatibilityError(
            "checkpoint training state is incomplete: " + ", ".join(missing)
        )
    policy.load_state_dict(payload["policy_state_dict"], strict=True)
    trainer.actor_optimizer.load_state_dict(
        payload["actor_optimizer_state_dict"]
    )
    trainer.critic_optimizer.load_state_dict(
        payload["critic_optimizer_state_dict"]
    )
    if restore_rng:
        _restore_rng_state(payload["rng_state"])
    return metadata
