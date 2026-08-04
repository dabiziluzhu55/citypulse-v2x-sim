from __future__ import annotations

from dataclasses import dataclass, replace
import math

import torch
from torch import nn
from torch.nn import functional as F

from algorithms.mappo.config import (
    COOPERATIVE_M1_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.m1_advantage import mix_advantages
from algorithms.mappo.models import MAPPOPolicy


@dataclass(frozen=True)
class PPOBatch:
    local_obs: torch.Tensor
    phase_features: torch.Tensor
    action_mask: torch.Tensor
    global_obs: torch.Tensor
    agent_mask: torch.Tensor
    agent_index: torch.Tensor
    actions: torch.Tensor
    old_log_probs: torch.Tensor
    old_values: torch.Tensor
    advantages: torch.Tensor
    returns: torch.Tensor
    joint_step_index: torch.Tensor | None = None
    component_advantages: dict[str, torch.Tensor] | None = None

    def __post_init__(self) -> None:
        batch_size = int(self.local_obs.shape[0])
        if self.local_obs.ndim != 2 or batch_size == 0:
            raise ValueError("local observations must be a non-empty matrix")
        if self.phase_features.ndim != 3 or self.phase_features.shape[0] != batch_size:
            raise ValueError("phase features must have one row per sample")
        if self.action_mask.shape != self.phase_features.shape[:2]:
            raise ValueError("action mask must match phase feature candidates")
        if self.global_obs.ndim != 3 or self.global_obs.shape[0] != batch_size:
            raise ValueError("global observations must have one row per sample")
        if self.agent_mask.shape != self.global_obs.shape[:2]:
            raise ValueError("agent mask must match global observations")
        vectors = (
            self.agent_index,
            self.actions,
            self.old_log_probs,
            self.old_values,
            self.advantages,
            self.returns,
        )
        if any(value.shape != (batch_size,) for value in vectors):
            raise ValueError("PPO vector fields must have one value per sample")
        if self.joint_step_index is None:
            object.__setattr__(
                self,
                "joint_step_index",
                torch.arange(
                    batch_size,
                    dtype=torch.long,
                    device=self.local_obs.device,
                ),
            )
        elif self.joint_step_index.shape != (batch_size,):
            raise ValueError(
                "joint step index must have one value per sample"
            )
        elif self.joint_step_index.dtype != torch.long:
            raise ValueError("joint step index must use torch.long dtype")
        if self.component_advantages is not None:
            for key, tensor in self.component_advantages.items():
                if key not in {"local", "neighbor", "team"}:
                    raise ValueError(f"unknown advantage component: {key}")
                if tensor.shape != (batch_size,):
                    raise ValueError(
                        "component advantages must have one value per sample"
                    )
        finite_tensors = (
            self.local_obs,
            self.phase_features,
            self.global_obs,
            self.old_log_probs,
            self.old_values,
            self.advantages,
            self.returns,
        )
        if any(not torch.isfinite(value).all() for value in finite_tensors):
            raise ValueError("PPO batch contains non-finite values")

    @property
    def batch_size(self) -> int:
        return int(self.local_obs.shape[0])

    def select(self, indices: torch.Tensor) -> "PPOBatch":
        assert self.joint_step_index is not None
        component = (
            None
            if self.component_advantages is None
            else {
                key: value[indices]
                for key, value in self.component_advantages.items()
            }
        )
        return PPOBatch(
            local_obs=self.local_obs[indices],
            phase_features=self.phase_features[indices],
            action_mask=self.action_mask[indices],
            global_obs=self.global_obs[indices],
            agent_mask=self.agent_mask[indices],
            agent_index=self.agent_index[indices],
            actions=self.actions[indices],
            old_log_probs=self.old_log_probs[indices],
            old_values=self.old_values[indices],
            advantages=self.advantages[indices],
            returns=self.returns[indices],
            joint_step_index=self.joint_step_index[indices],
            component_advantages=component,
        )


def _explained_variance(
    targets: torch.Tensor, predictions: torch.Tensor
) -> float:
    target_variance = targets.var(unbiased=False)
    if float(target_variance) <= 1e-8:
        return 0.0
    value = 1.0 - (
        (targets - predictions).var(unbiased=False) / target_variance
    )
    return float(value)


def _agent_explained_variance(
    batch: PPOBatch, predictions: torch.Tensor
) -> dict[str, float]:
    values = []
    for owner in torch.unique(batch.agent_index, sorted=True):
        owner_mask = batch.agent_index == owner
        values.append(
            _explained_variance(
                batch.returns[owner_mask], predictions[owner_mask]
            )
        )
    return {
        "agent_mean": float(sum(values) / len(values)),
        "agent_min": float(min(values)),
        "agent_max": float(max(values)),
    }


def _batch_structure_diagnostics(
    batch: PPOBatch, *, owner_conditioned_critic: bool
) -> dict[str, float]:
    assert batch.joint_step_index is not None
    joint_rows = batch.global_obs.detach().cpu().contiguous().numpy()
    mask_rows = batch.agent_mask.detach().cpu().contiguous().numpy()
    joint_keys = [
        (observation.tobytes(), mask.tobytes())
        for observation, mask in zip(joint_rows, mask_rows, strict=True)
    ]
    owners = [int(value) for value in batch.agent_index.detach().cpu()]
    unique_joint_states = len(set(joint_keys))
    unique_critic_inputs = (
        len(set(zip(joint_keys, owners, strict=True)))
        if owner_conditioned_critic
        else unique_joint_states
    )
    unique_joint_steps = int(
        torch.unique(batch.joint_step_index.detach()).numel()
    )

    action_dimension = int(batch.action_mask.shape[1])
    action_counts = torch.bincount(
        batch.actions.detach().cpu(), minlength=action_dimension
    )
    diagnostics = {
        "unique_joint_state_count": float(unique_joint_states),
        "unique_critic_input_count": float(unique_critic_inputs),
        "joint_state_reuse_factor": float(
            batch.batch_size / unique_joint_states
        ),
        "unique_joint_step_count": float(unique_joint_steps),
        "joint_step_reuse_factor": float(
            batch.batch_size / unique_joint_steps
        ),
        "valid_action_count_mean": float(
            batch.action_mask.sum(dim=1).float().mean()
        ),
    }
    for action_index, count in enumerate(action_counts):
        diagnostics[f"action_{action_index}_fraction"] = float(
            count / batch.batch_size
        )

    starved = 0
    available = 0
    for owner in torch.unique(batch.agent_index, sorted=True):
        owner_mask = batch.agent_index == owner
        valid_for_owner = batch.action_mask[owner_mask].any(dim=0)
        selected_for_owner = torch.zeros_like(valid_for_owner)
        selected_for_owner[batch.actions[owner_mask]] = True
        available += int(valid_for_owner.sum())
        starved += int((valid_for_owner & ~selected_for_owner).sum())
    diagnostics["unselected_valid_action_fraction"] = float(
        starved / available if available else 0.0
    )
    return diagnostics


def _validate_cooperative_joint_rows(
    batch: PPOBatch,
    *,
    num_agents: int,
    target_mode: str,
) -> torch.Tensor | None:
    """Validate atomic owner rows and optionally literal shared targets.

    target_mode: "shared" | "per_agent" | "m1_0_scalar"
      - shared / m1_0_scalar: advantages/returns/old_values must be
        identical within each joint; returns one joint advantage per joint.
      - per_agent: only validates joint context/owner completeness.
    """
    require_shared = target_mode in {"shared", "m1_0_scalar"}

    assert batch.joint_step_index is not None
    joint_advantages: list[torch.Tensor] = []
    expected_owners = torch.arange(
        num_agents, dtype=torch.long, device=batch.agent_index.device
    )
    for joint_id in torch.unique(batch.joint_step_index, sorted=True):
        rows = torch.nonzero(
            batch.joint_step_index == joint_id, as_tuple=False
        ).squeeze(-1)
        if int(rows.numel()) != num_agents:
            raise ValueError(
                f"joint {int(joint_id)} must contain exactly {num_agents} owners"
            )
        owners = torch.sort(batch.agent_index[rows]).values
        if not torch.equal(owners, expected_owners):
            raise ValueError(
                f"joint {int(joint_id)} owner set is incomplete or duplicated"
            )

        global_obs = batch.global_obs[rows]
        if not torch.equal(
            global_obs, global_obs[0].expand_as(global_obs)
        ):
            raise ValueError(
                f"joint {int(joint_id)} global state differs across owners"
            )
        agent_mask = batch.agent_mask[rows]
        if not torch.equal(
            agent_mask, agent_mask[0].expand_as(agent_mask)
        ):
            raise ValueError(
                f"joint {int(joint_id)} agent mask differs across owners"
            )
        if require_shared:
            advantages = batch.advantages[rows]
            if not torch.equal(
                advantages, advantages[0].expand_as(advantages)
            ):
                raise ValueError(
                    f"joint {int(joint_id)} advantage differs across owners"
                )
            returns = batch.returns[rows]
            if not torch.equal(returns, returns[0].expand_as(returns)):
                raise ValueError(
                    f"joint {int(joint_id)} return differs across owners"
                )
            old_values = batch.old_values[rows]
            if not torch.equal(
                old_values, old_values[0].expand_as(old_values)
            ):
                raise ValueError(
                    f"joint {int(joint_id)} rollout value differs across owners"
                )
            joint_advantages.append(advantages[0])
    if not require_shared:
        return None
    return torch.stack(joint_advantages)


class MAPPOTrainer:
    def __init__(self, policy: MAPPOPolicy, config: MAPPOConfig) -> None:
        self.policy = policy
        self.config = config
        self.actor_optimizer = torch.optim.Adam(
            policy.actor_parameters(), lr=config.actor_lr, eps=1e-5
        )
        self.critic_optimizer = torch.optim.Adam(
            policy.critic_parameters(), lr=config.critic_lr, eps=1e-5
        )

    def compute_actor_loss(
        self, batch: PPOBatch
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        distribution = self.policy.actor(
            batch.local_obs, batch.phase_features, batch.action_mask
        )
        new_log_probs = distribution.log_prob(batch.actions)
        ratios = torch.exp(new_log_probs - batch.old_log_probs)
        unclipped = ratios * batch.advantages
        clipped = torch.clamp(
            ratios,
            1.0 - self.config.ppo_clip,
            1.0 + self.config.ppo_clip,
        ) * batch.advantages
        policy_loss = -torch.minimum(unclipped, clipped).mean()
        entropy = distribution.entropy().mean()
        total_loss = policy_loss - self.config.entropy_coef * entropy
        diagnostics = {
            "new_log_probs": new_log_probs.detach(),
            "entropy": entropy.detach(),
            "approx_kl": (batch.old_log_probs - new_log_probs).mean().detach(),
            "clip_fraction": (
                (ratios - 1.0).abs() > self.config.ppo_clip
            ).float().mean().detach(),
        }
        return total_loss, diagnostics

    def compute_critic_loss(
        self, batch: PPOBatch
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        if self.config.m1_target_mode == "m1_0_scalar":
            assert batch.joint_step_index is not None
            values = self.policy.value(
                batch.global_obs, batch.agent_mask, batch.agent_index
            ).squeeze(-1)
            joint_ids = batch.joint_step_index
            scalar_means = torch.zeros_like(values)
            for jid in torch.unique(joint_ids, sorted=True):
                mask = joint_ids == jid
                scalar_means[mask] = values[mask].mean()
            loss = F.huber_loss(
                scalar_means,
                batch.returns,
                reduction="mean",
                delta=self.config.huber_delta,
            )
            return loss, {"values": scalar_means.detach()}
        values = self.policy.value(
            batch.global_obs, batch.agent_mask, batch.agent_index
        ).squeeze(-1)
        loss = F.huber_loss(
            values,
            batch.returns,
            reduction="mean",
            delta=self.config.huber_delta,
        )
        return loss, {"values": values.detach()}

    def update(self, batch: PPOBatch) -> dict[str, float]:
        joint_advantages: torch.Tensor | None = None
        if self.config.reward_scope == REWARD_SCOPE_SHARED_TEAM:
            if self.config.model_version == COOPERATIVE_M1_MODEL_VERSION:
                target_mode = {
                    "shared": "shared",
                    "per_agent": "per_agent",
                    "m1_0_scalar": "m1_0_scalar",
                }[self.config.m1_target_mode]
            else:
                target_mode = (
                    "shared"
                    if self.config.requires_shared_values
                    else "per_agent"
                )
            validated = _validate_cooperative_joint_rows(
                batch,
                num_agents=len(self.config.intersection_ids),
                target_mode=target_mode,
            )
            joint_advantages = (
                None if validated is None else validated.detach()
            )
        with torch.no_grad():
            pre_values = self.policy.value(
                batch.global_obs, batch.agent_mask, batch.agent_index
            ).squeeze(-1)
        explained_variance_pre = _explained_variance(
            batch.returns, pre_values
        )
        pre_agent_ev = _agent_explained_variance(batch, pre_values)
        rollout_value_max_abs_error = float(
            (pre_values - batch.old_values).abs().max()
        )
        structure_diagnostics = _batch_structure_diagnostics(
            batch,
            owner_conditioned_critic=not self.config.requires_shared_values,
        )

        training_advantages = batch.advantages
        if batch.component_advantages is not None:
            training_advantages = mix_advantages(
                local=batch.component_advantages["local"],
                neighbor=batch.component_advantages.get("neighbor"),
                team=batch.component_advantages["team"],
                m1_arm=self.config.m1_arm,
                weights=(
                    self.config.m1_local_weight,
                    self.config.m1_neighbor_weight,
                    self.config.m1_team_weight,
                ),
            )
        raw_advantages = training_advantages.detach()
        advantage_std = raw_advantages.std(unbiased=False)
        normalized_advantages = (
            raw_advantages - raw_advantages.mean()
        ) / advantage_std.clamp_min(1e-8)
        training_batch = replace(batch, advantages=normalized_advantages)
        totals = {
            "actor_loss": 0.0,
            "critic_loss": 0.0,
            "entropy": 0.0,
            "approx_kl": 0.0,
            "clip_fraction": 0.0,
            "actor_grad_norm": 0.0,
            "critic_grad_norm": 0.0,
        }
        update_count = 0
        device = batch.local_obs.device
        for _ in range(self.config.ppo_epochs):
            permutation = torch.randperm(batch.batch_size, device=device)
            for start in range(0, batch.batch_size, self.config.minibatch_size):
                indices = permutation[start : start + self.config.minibatch_size]
                minibatch = training_batch.select(indices)

                actor_loss, actor_info = self.compute_actor_loss(minibatch)
                self.actor_optimizer.zero_grad(set_to_none=True)
                actor_loss.backward()
                actor_grad_norm = nn.utils.clip_grad_norm_(
                    tuple(self.policy.actor_parameters()),
                    self.config.max_grad_norm,
                )
                self.actor_optimizer.step()

                critic_loss, _ = self.compute_critic_loss(minibatch)
                self.critic_optimizer.zero_grad(set_to_none=True)
                critic_loss.backward()
                critic_grad_norm = nn.utils.clip_grad_norm_(
                    tuple(self.policy.critic_parameters()),
                    self.config.max_grad_norm,
                )
                self.critic_optimizer.step()

                totals["actor_loss"] += float(actor_loss.detach())
                totals["critic_loss"] += float(critic_loss.detach())
                totals["entropy"] += float(actor_info["entropy"])
                totals["approx_kl"] += float(actor_info["approx_kl"])
                totals["clip_fraction"] += float(
                    actor_info["clip_fraction"]
                )
                totals["actor_grad_norm"] += float(actor_grad_norm)
                totals["critic_grad_norm"] += float(critic_grad_norm)
                update_count += 1

        if update_count == 0:
            raise RuntimeError("PPO update received no minibatches")
        diagnostics = {
            name: value / update_count for name, value in totals.items()
        }
        with torch.no_grad():
            final_values = self.policy.value(
                batch.global_obs, batch.agent_mask, batch.agent_index
            ).squeeze(-1)
        explained_variance_post = _explained_variance(
            batch.returns, final_values
        )
        post_agent_ev = _agent_explained_variance(batch, final_values)
        diagnostics.update(
            {
                "advantage_mean": float(raw_advantages.mean()),
                "advantage_std": float(advantage_std),
                "advantage_abs_mean": float(raw_advantages.abs().mean()),
                "return_mean": float(batch.returns.mean()),
                "return_std": float(batch.returns.std(unbiased=False)),
                "value_mean": float(final_values.mean()),
                "value_std": float(final_values.std(unbiased=False)),
                "explained_variance": explained_variance_post,
                "explained_variance_pre": explained_variance_pre,
                "explained_variance_post": explained_variance_post,
                "explained_variance_gain": (
                    explained_variance_post - explained_variance_pre
                ),
                "explained_variance_pre_agent_mean": pre_agent_ev[
                    "agent_mean"
                ],
                "explained_variance_pre_agent_min": pre_agent_ev[
                    "agent_min"
                ],
                "explained_variance_pre_agent_max": pre_agent_ev[
                    "agent_max"
                ],
                "explained_variance_post_agent_mean": post_agent_ev[
                    "agent_mean"
                ],
                "explained_variance_post_agent_min": post_agent_ev[
                    "agent_min"
                ],
                "explained_variance_post_agent_max": post_agent_ev[
                    "agent_max"
                ],
                "rollout_value_max_abs_error": rollout_value_max_abs_error,
            }
        )
        diagnostics.update(structure_diagnostics)
        if joint_advantages is not None:
            diagnostics.update(
                {
                    "joint_advantage_mean": float(
                        joint_advantages.mean()
                    ),
                    "joint_advantage_std": float(
                        joint_advantages.std(unbiased=False)
                    ),
                    "joint_advantage_abs_mean": float(
                        joint_advantages.abs().mean()
                    ),
                }
            )
        if not all(math.isfinite(value) for value in diagnostics.values()):
            raise FloatingPointError("PPO update produced non-finite diagnostics")
        return diagnostics
