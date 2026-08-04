"""Protocol 2.0 entrypoint for generation-pinned MAPPO workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import torch

from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.controller import MAPPOController
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.parallel_train import WorkerRollout
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


@dataclass(frozen=True)
class _PreparedCollector:
    policy_state: dict[str, torch.Tensor]
    config: MAPPOConfig
    policy_generation: int
    rollout_seed: int
    actor_init_seed: int
    critic_init_seed: int
    residual_init_seed: int | None
    expected_duration_s: float
    mode: str
    record_evaluation: bool


_prepared: _PreparedCollector | None = None
_controller: MAPPOController | None = None
_collected_rollout: WorkerRollout | None = None
_collected_diagnostics: dict[str, object] | None = None


def prepare_collector(
    *,
    policy_state: Mapping[str, torch.Tensor],
    config: MAPPOConfig,
    policy_generation: int,
    rollout_seed: int,
    actor_init_seed: int,
    critic_init_seed: int,
    residual_init_seed: int | None = None,
    expected_duration_s: float,
    mode: str = "collect",
    record_evaluation: bool = False,
) -> None:
    """Install an immutable learner snapshot before SUMO initializes a worker."""

    global _prepared, _controller, _collected_rollout, _collected_diagnostics
    if _controller is not None:
        raise RuntimeError("cannot replace policy while a MAPPO episode is active")
    if config.actor_variant == "residual" and residual_init_seed is None:
        raise ValueError("residual actor requires residual_init_seed")
    _prepared = _PreparedCollector(
        policy_state={
            str(name): tensor.detach().cpu().clone()
            for name, tensor in policy_state.items()
        },
        config=config,
        policy_generation=int(policy_generation),
        rollout_seed=int(rollout_seed),
        actor_init_seed=int(actor_init_seed),
        critic_init_seed=int(critic_init_seed),
        residual_init_seed=(
            None if residual_init_seed is None else int(residual_init_seed)
        ),
        expected_duration_s=float(expected_duration_s),
        mode=str(mode),
        record_evaluation=bool(record_evaluation),
    )
    _collected_rollout = None
    _collected_diagnostics = None


def initialize(metadata: dict) -> dict:
    global _controller
    if _prepared is None:
        raise RuntimeError("prepare_collector() must be called before initialize()")
    if _controller is not None:
        raise RuntimeError("MAPPO controller is already initialized")
    prepared = _prepared
    policy = MAPPOPolicy(
        obs_dim=prepared.config.obs_dim,
        num_agents=len(IDENTITY_SLOT_IDS),
        critic_scope=prepared.config.critic_scope,
        actor_init_seed=prepared.actor_init_seed,
        critic_init_seed=prepared.critic_init_seed,
        hidden_dim=prepared.config.hidden_dim,
        phase_feature_dim=prepared.config.phase_feature_dim,
        model_version=prepared.config.model_version,
        actor_variant=prepared.config.actor_variant,
        residual_hidden_dim=prepared.config.residual_hidden_dim,
        identity_offset=prepared.config.identity_offset,
        residual_init_seed=(prepared.residual_init_seed or 44),
    )
    policy.load_state_dict(prepared.policy_state, strict=True)
    policy.eval()
    _controller = MAPPOController(
        metadata=metadata,
        config=prepared.config,
        policy=policy,
        mode=prepared.mode,
        policy_generation=prepared.policy_generation,
        expected_duration_s=prepared.expected_duration_s,
        record_evaluation=prepared.record_evaluation,
        rollout_seed=prepared.rollout_seed,
    )
    return {
        "protocol_version": "2.0",
        "episode_id": metadata["episode_id"],
        "ready": True,
    }


def step(payload: dict) -> dict:
    if _controller is None:
        raise RuntimeError("MAPPO is not initialized")
    return _controller.step(payload)


def finish(payload: dict) -> None:
    global _controller, _collected_rollout, _collected_diagnostics
    if _controller is None:
        raise RuntimeError("MAPPO is not initialized")
    try:
        _collected_rollout = _controller.finish(payload)
    finally:
        _collected_diagnostics = _controller.action_diagnostics
        _controller = None


def pop_collected_rollout() -> WorkerRollout | None:
    global _collected_rollout
    rollout = _collected_rollout
    _collected_rollout = None
    return rollout


def pop_collected_diagnostics() -> dict[str, object] | None:
    global _collected_diagnostics
    diagnostics = _collected_diagnostics
    _collected_diagnostics = None
    return diagnostics
