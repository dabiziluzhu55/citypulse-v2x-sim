from __future__ import annotations

from dataclasses import replace
import math

import pytest
import torch

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.trainer import (
    MAPPOTrainer,
    PPOBatch,
    _validate_cooperative_joint_rows,
)


def _policy_and_trainer() -> tuple[MAPPOPolicy, MAPPOTrainer]:
    config = MAPPOConfig(("demo_1", "demo_2"))
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=8,
        phase_feature_dim=config.phase_feature_dim,
    )
    return policy, MAPPOTrainer(policy, config)


def _cooperative_policy_and_trainer(
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> tuple[MAPPOConfig, MAPPOPolicy, MAPPOTrainer]:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="global",
        model_version=model_version,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
    )
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=8,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
    )
    return config, policy, MAPPOTrainer(policy, config)


def _batch(policy: MAPPOPolicy, global_offset: float = 0.0) -> PPOBatch:
    """Two cooperative joints x two owners with shared (team) targets."""
    batch_size = 4
    obs_dim = policy.actor.obs_dim
    local_obs = torch.arange(
        batch_size * obs_dim, dtype=torch.float32
    ).reshape(batch_size, obs_dim) / 100.0
    phase_features = torch.zeros((batch_size, 2, 11), dtype=torch.float32)
    phase_features[:, 0, 0] = 1.0
    phase_features[:, 1, 1] = 1.0
    action_mask = torch.tensor(
        [[True, True], [True, False], [True, True], [False, True]]
    )
    actions = torch.tensor([0, 0, 1, 1])
    with torch.no_grad():
        old_log_probs = policy.actor(
            local_obs, phase_features, action_mask
        ).log_prob(actions)
    first_joint = torch.zeros((2, obs_dim), dtype=torch.float32)
    second_joint = torch.zeros((2, obs_dim), dtype=torch.float32)
    global_obs = torch.stack(
        (first_joint, first_joint, second_joint, second_joint)
    ) + global_offset
    agent_mask = torch.ones((batch_size, 2), dtype=torch.bool)
    owner = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_values = policy.value(
            global_obs, agent_mask, owner
        ).squeeze(-1)
    # The cooperative controller evaluates one team scalar per joint and
    # broadcasts it to every owner row.
    old_values = old_values.clone()
    old_values[1] = old_values[0]
    old_values[3] = old_values[2]
    advantages = torch.tensor([1.0, 1.0, -0.5, -0.5])
    returns = old_values + torch.tensor([0.5, 0.5, -0.25, -0.25])
    return PPOBatch(
        local_obs=local_obs,
        phase_features=phase_features,
        action_mask=action_mask,
        global_obs=global_obs,
        agent_mask=agent_mask,
        agent_index=owner,
        actions=actions,
        old_log_probs=old_log_probs,
        old_values=old_values,
        advantages=advantages,
        returns=returns,
        joint_step_index=torch.tensor([0, 0, 1, 1]),
    )


def _cooperative_batch(
    policy: MAPPOPolicy,
    *,
    advantages: torch.Tensor | None = None,
    returns: torch.Tensor | None = None,
) -> PPOBatch:
    batch_size = 4
    obs_dim = policy.actor.obs_dim
    local_obs = torch.arange(
        batch_size * obs_dim, dtype=torch.float32
    ).reshape(batch_size, obs_dim) / 100.0
    phase_features = torch.zeros((batch_size, 2, 11), dtype=torch.float32)
    phase_features[:, 0, 0] = 1.0
    phase_features[:, 1, 1] = 1.0
    action_mask = torch.ones((batch_size, 2), dtype=torch.bool)
    actions = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        distribution = policy.actor(local_obs, phase_features, action_mask)
        old_log_probs = distribution.log_prob(actions)

    first_joint = torch.arange(
        2 * obs_dim, dtype=torch.float32
    ).reshape(2, obs_dim) / 50.0
    second_joint = first_joint + 7.0
    global_obs = torch.stack(
        (first_joint, first_joint, second_joint, second_joint)
    )
    agent_mask = torch.ones((batch_size, 2), dtype=torch.bool)
    owners = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_values = policy.value(
            global_obs, agent_mask, owners
        ).squeeze(-1)
    if policy.model_version == COOPERATIVE_MODEL_VERSION:
        # The original cooperative controller evaluates one team scalar and
        # broadcasts it to every owner row.
        old_values = old_values.clone()
        old_values[1] = old_values[0]
        old_values[3] = old_values[2]
    if advantages is None:
        advantages = torch.tensor([1.0, 1.0, 3.0, 3.0])
    if returns is None:
        returns = old_values + torch.tensor([0.5, 0.5, -0.25, -0.25])
    return PPOBatch(
        local_obs=local_obs,
        phase_features=phase_features,
        action_mask=action_mask,
        global_obs=global_obs,
        agent_mask=agent_mask,
        agent_index=owners,
        actions=actions,
        old_log_probs=old_log_probs,
        old_values=old_values,
        advantages=advantages,
        returns=returns,
        joint_step_index=torch.tensor([0, 0, 1, 1]),
    )


def test_actor_loss_is_independent_of_global_critic_state() -> None:
    policy, trainer = _policy_and_trainer()
    batch_a = _batch(policy, global_offset=0.0)
    batch_b = replace(batch_a, global_obs=_batch(policy, global_offset=9.0).global_obs)

    loss_a, diagnostics_a = trainer.compute_actor_loss(batch_a)
    loss_b, diagnostics_b = trainer.compute_actor_loss(batch_b)

    torch.testing.assert_close(loss_a, loss_b, rtol=0, atol=0)
    torch.testing.assert_close(
        diagnostics_a["new_log_probs"],
        diagnostics_b["new_log_probs"],
        rtol=0,
        atol=0,
    )


def test_critic_loss_responds_to_global_state() -> None:
    policy, trainer = _policy_and_trainer()
    batch_a = _batch(policy, global_offset=0.0)
    batch_b = replace(batch_a, global_obs=_batch(policy, global_offset=9.0).global_obs)

    _, diagnostics_a = trainer.compute_critic_loss(batch_a)
    _, diagnostics_b = trainer.compute_critic_loss(batch_b)

    assert not torch.equal(diagnostics_a["values"], diagnostics_b["values"])


def test_one_update_changes_actor_and_critic_with_finite_diagnostics() -> None:
    policy, trainer = _policy_and_trainer()
    batch = _batch(policy, global_offset=3.0)
    actor_before = {
        name: value.detach().clone() for name, value in policy.actor.state_dict().items()
    }
    critic_before = {
        name: value.detach().clone() for name, value in policy.critic.state_dict().items()
    }

    diagnostics = trainer.update(batch)

    assert any(
        not torch.equal(actor_before[name], value)
        for name, value in policy.actor.state_dict().items()
    )
    assert any(
        not torch.equal(critic_before[name], value)
        for name, value in policy.critic.state_dict().items()
    )
    required = {
        "actor_loss",
        "critic_loss",
        "entropy",
        "approx_kl",
        "clip_fraction",
        "actor_grad_norm",
        "critic_grad_norm",
        "advantage_mean",
        "advantage_std",
        "advantage_abs_mean",
        "return_mean",
        "return_std",
        "value_mean",
        "value_std",
        "explained_variance",
        "explained_variance_pre",
        "explained_variance_post",
        "explained_variance_gain",
        "explained_variance_pre_agent_mean",
        "explained_variance_post_agent_mean",
        "rollout_value_max_abs_error",
        "unique_joint_state_count",
        "unique_critic_input_count",
        "joint_state_reuse_factor",
        "valid_action_count_mean",
        "unselected_valid_action_fraction",
        "action_0_fraction",
        "action_1_fraction",
    }
    assert required <= diagnostics.keys()
    assert all(math.isfinite(float(diagnostics[name])) for name in required)
    assert diagnostics["explained_variance"] == diagnostics[
        "explained_variance_post"
    ]
    assert diagnostics["explained_variance_gain"] == (
        diagnostics["explained_variance_post"]
        - diagnostics["explained_variance_pre"]
    )
    assert diagnostics["rollout_value_max_abs_error"] < 1e-6
    assert diagnostics["action_0_fraction"] == 0.5
    assert diagnostics["action_1_fraction"] == 0.5
    assert diagnostics["valid_action_count_mean"] == 1.5
    assert diagnostics["unselected_valid_action_fraction"] == 0.0


def test_batch_diagnostics_collapse_owner_rows_for_shared_joint() -> None:
    policy, trainer = _policy_and_trainer()
    original = _batch(policy)
    shared_state = original.global_obs[0].expand(4, -1, -1).clone()
    owner = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_values = policy.value(
            shared_state, original.agent_mask, owner
        ).squeeze(-1)
    old_values = old_values.clone()
    old_values[1] = old_values[0]
    old_values[3] = old_values[2]
    batch = replace(
        original,
        global_obs=shared_state,
        agent_index=owner,
        old_values=old_values,
        returns=old_values + torch.tensor([0.5, 0.5, -0.25, -0.25]),
    )

    diagnostics = trainer.update(batch)

    assert diagnostics["unique_joint_state_count"] == 1
    assert diagnostics["unique_critic_input_count"] == 1
    assert diagnostics["joint_state_reuse_factor"] == 4.0


def test_batch_diagnostics_include_agent_mask_in_joint_state_identity() -> None:
    policy, trainer = _policy_and_trainer()
    original = _batch(policy)
    masks = original.agent_mask.clone()
    # Mask difference applies to the whole second joint (both owners).
    masks[2, 0] = False
    masks[3, 0] = False
    owner = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_values = policy.value(
            original.global_obs, masks, owner
        ).squeeze(-1)
    old_values = old_values.clone()
    old_values[1] = old_values[0]
    old_values[3] = old_values[2]
    batch = replace(
        original,
        agent_mask=masks,
        agent_index=owner,
        old_values=old_values,
        returns=old_values + torch.tensor([0.5, 0.5, -0.25, -0.25]),
    )

    diagnostics = trainer.update(batch)

    assert diagnostics["unique_joint_state_count"] == 2
    assert diagnostics["unique_critic_input_count"] == 2
    assert diagnostics["joint_state_reuse_factor"] == 2.0


def test_cooperative_global_critic_is_owner_invariant_for_repeated_joint() -> None:
    _, policy, trainer = _cooperative_policy_and_trainer()
    original = _cooperative_batch(policy)
    one_joint = original.global_obs[0].expand(4, -1, -1).clone()
    first_owner_assignment = replace(
        original,
        global_obs=one_joint,
        agent_index=torch.tensor([0, 0, 1, 1]),
    )
    second_owner_assignment = replace(
        first_owner_assignment,
        agent_index=torch.tensor([0, 1, 1, 0]),
    )

    _, first_diagnostics = trainer.compute_critic_loss(
        first_owner_assignment
    )
    _, second_diagnostics = trainer.compute_critic_loss(
        second_owner_assignment
    )

    torch.testing.assert_close(
        first_diagnostics["values"],
        second_diagnostics["values"],
        rtol=0,
        atol=0,
    )


def test_shared_losses_use_team_targets_and_only_local_actor_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, policy, trainer = _cooperative_policy_and_trainer()
    team_advantages = torch.tensor([2.0, 2.0, -1.0, -1.0])
    team_returns = torch.tensor([1.0, 1.0, 2.0, 2.0])
    batch = _cooperative_batch(
        policy,
        advantages=team_advantages,
        returns=team_returns,
    )

    actor_calls: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor]] = []
    original_actor_forward = policy.actor.forward

    def actor_spy(
        local_obs: torch.Tensor,
        phase_features: torch.Tensor,
        action_mask: torch.Tensor,
    ):
        actor_calls.append((local_obs, phase_features, action_mask))
        return original_actor_forward(
            local_obs, phase_features, action_mask
        )

    value_calls: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor]] = []

    def zero_value_spy(
        global_obs: torch.Tensor,
        agent_mask: torch.Tensor,
        agent_index: torch.Tensor,
    ) -> torch.Tensor:
        value_calls.append((global_obs, agent_mask, agent_index))
        return torch.zeros(
            (global_obs.shape[0], 1),
            dtype=global_obs.dtype,
            device=global_obs.device,
            requires_grad=True,
        )

    monkeypatch.setattr(policy.actor, "forward", actor_spy)
    monkeypatch.setattr(policy.critic, "forward", zero_value_spy)

    actor_loss, actor_diagnostics = trainer.compute_actor_loss(batch)
    critic_loss, _ = trainer.compute_critic_loss(batch)

    assert len(actor_calls) == 1
    assert len(actor_calls[0]) == 3
    torch.testing.assert_close(actor_calls[0][0], batch.local_obs)
    torch.testing.assert_close(actor_calls[0][1], batch.phase_features)
    assert torch.equal(actor_calls[0][2], batch.action_mask)
    expected_actor_loss = -torch.tensor(0.5) - (
        config.entropy_coef * actor_diagnostics["entropy"]
    )
    torch.testing.assert_close(actor_loss, expected_actor_loss)

    assert len(value_calls) == 1
    torch.testing.assert_close(value_calls[0][0], batch.global_obs)
    assert torch.equal(value_calls[0][1], batch.agent_mask)
    assert torch.equal(value_calls[0][2], batch.agent_index)
    # Huber(0 -> [1,1,2,2], delta=10) = mean([.5,.5,2,2]) = 1.25.
    assert critic_loss.item() == pytest.approx(1.25)


def test_joint_diagnostics_use_ids_not_equal_global_state_bytes() -> None:
    _, policy, trainer = _cooperative_policy_and_trainer()
    original = _cooperative_batch(policy)
    identical_state = original.global_obs[0].expand(4, -1, -1).clone()
    with torch.no_grad():
        old_values = policy.value(
            identical_state,
            original.agent_mask,
            original.agent_index,
        ).squeeze(-1)
    old_values = old_values.clone()
    old_values[1] = old_values[0]
    old_values[3] = old_values[2]
    batch = replace(
        original,
        global_obs=identical_state,
        old_values=old_values,
        returns=old_values + torch.tensor([0.5, 0.5, -0.25, -0.25]),
    )

    diagnostics = trainer.update(batch)

    assert diagnostics["unique_joint_state_count"] == 1
    assert diagnostics["unique_critic_input_count"] == 1
    assert diagnostics["unique_joint_step_count"] == 2
    assert diagnostics["joint_step_reuse_factor"] == 2.0


def test_shared_advantage_diagnostics_collapse_owner_repeats() -> None:
    _, policy, trainer = _cooperative_policy_and_trainer()
    batch = _cooperative_batch(
        policy, advantages=torch.tensor([1.0, 1.0, 3.0, 3.0])
    )

    diagnostics = trainer.update(batch)

    assert diagnostics["joint_advantage_mean"] == pytest.approx(2.0)
    assert diagnostics["joint_advantage_std"] == pytest.approx(1.0)
    assert diagnostics["joint_advantage_abs_mean"] == pytest.approx(2.0)
    assert diagnostics["unique_critic_input_count"] == 2


def test_shared_advantage_mismatch_within_joint_fails_fast() -> None:
    _, policy, trainer = _cooperative_policy_and_trainer()
    damaged = _cooperative_batch(
        policy, advantages=torch.tensor([1.0, 1.25, 3.0, 3.0])
    )

    with pytest.raises(ValueError, match="joint.*advantage|advantage.*joint"):
        trainer.update(damaged)
