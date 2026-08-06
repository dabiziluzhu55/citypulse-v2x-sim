from __future__ import annotations

import inspect

import pytest
import torch
from torch import nn

from algorithms.mappo.config import COOPERATIVE_MODEL_VERSION
from algorithms.mappo.models import (
    CandidateActor,
    IsomorphicTeamValueCritic,
    MAPPOPolicy,
)


def _set_deterministic_nonzero_weights(module: nn.Module) -> None:
    with torch.no_grad():
        for child in module.modules():
            if isinstance(child, nn.Linear):
                child.weight.fill_(0.1)
                child.bias.fill_(0.05)


def test_actor_api_and_output_are_independent_of_other_agents() -> None:
    actor = CandidateActor(obs_dim=4, phase_feature_dim=3, hidden_dim=8)
    obs = torch.tensor([[1.0, 2.0, 3.0, 4.0]])
    phase_features = torch.tensor(
        [[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]]
    )
    action_mask = torch.tensor([[True, True]])

    logits_before = actor.masked_logits(obs, phase_features, action_mask)
    unrelated_global_state = torch.randn(1, 20, 4)
    assert unrelated_global_state.shape == (1, 20, 4)
    logits_after = actor.masked_logits(obs, phase_features, action_mask)

    torch.testing.assert_close(logits_before, logits_after, rtol=0, atol=0)
    assert "global" not in inspect.signature(actor.forward).parameters
    assert "global" not in inspect.signature(actor.masked_logits).parameters


def test_actor_masks_invalid_candidate() -> None:
    actor = CandidateActor(obs_dim=2, phase_feature_dim=2, hidden_dim=4)

    logits = actor.masked_logits(
        torch.zeros((1, 2)),
        torch.zeros((1, 3, 2)),
        torch.tensor([[True, False, True]]),
    )

    assert torch.isfinite(logits).all()
    assert logits[0, 1].item() <= -1e8
    assert logits[0, 0].item() > -1e8
    assert logits[0, 2].item() > -1e8


def test_actor_rejects_all_invalid_candidates() -> None:
    actor = CandidateActor(obs_dim=2, phase_feature_dim=2, hidden_dim=4)

    with pytest.raises(ValueError, match="at least one valid action"):
        actor.masked_logits(
            torch.zeros((1, 2)),
            torch.zeros((1, 2, 2)),
            torch.tensor([[False, False]]),
        )


def test_actor_initialization_is_identical_between_critic_scopes() -> None:
    local_policy = MAPPOPolicy(
        obs_dim=4,
        num_agents=2,
        critic_scope="local",
        actor_init_seed=1234,
        critic_init_seed=5678,
        hidden_dim=8,
        phase_feature_dim=3,
    )
    global_policy = MAPPOPolicy(
        obs_dim=4,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=1234,
        critic_init_seed=5678,
        hidden_dim=8,
        phase_feature_dim=3,
    )

    assert local_policy.actor.state_dict().keys() == global_policy.actor.state_dict().keys()
    for name, local_tensor in local_policy.actor.state_dict().items():
        torch.testing.assert_close(
            local_tensor,
            global_policy.actor.state_dict()[name],
            rtol=0,
            atol=0,
        )


def test_local_and_global_critics_share_one_value_call_contract() -> None:
    global_obs = torch.tensor(
        [[[1.0, 2.0], [3.0, 4.0]], [[5.0, 6.0], [7.0, 8.0]]]
    )
    mask = torch.tensor([[True, True], [True, True]])
    owner = torch.tensor([0, 1])

    for critic_scope in ("local", "global"):
        policy = MAPPOPolicy(
            obs_dim=2,
            num_agents=2,
            critic_scope=critic_scope,
            actor_init_seed=1,
            critic_init_seed=2,
            hidden_dim=4,
            phase_feature_dim=2,
        )
        assert policy.value(global_obs, mask, owner).shape == (2, 1)


def test_isomorphic_critics_have_equal_tensors_and_parameter_counts() -> None:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(242)
        local = IsomorphicTeamValueCritic(
            obs_dim=4, num_agents=3, context_scope="local", hidden_dim=8
        )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(242)
        global_ = IsomorphicTeamValueCritic(
            obs_dim=4, num_agents=3, context_scope="global", hidden_dim=8
        )

    assert list(local.state_dict()) == list(global_.state_dict())
    for name, left in local.state_dict().items():
        right = global_.state_dict()[name]
        assert left.shape == right.shape
        assert left.dtype == right.dtype
        assert left.numel() == right.numel()
        assert torch.equal(left, right)
    assert sum(parameter.numel() for parameter in local.parameters()) == sum(
        parameter.numel() for parameter in global_.parameters()
    )


def test_isomorphic_local_ignores_and_global_uses_non_owner_state() -> None:
    local = IsomorphicTeamValueCritic(
        obs_dim=2, num_agents=2, context_scope="local", hidden_dim=4
    )
    global_ = IsomorphicTeamValueCritic(
        obs_dim=2, num_agents=2, context_scope="global", hidden_dim=4
    )
    _set_deterministic_nonzero_weights(local)
    global_.load_state_dict(local.state_dict(), strict=True)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [2.0, 0.0]]])
    mask = torch.tensor([[True, True]])
    owner = torch.tensor([0])

    assert torch.equal(local(state_a, mask, owner), local(state_b, mask, owner))
    assert not torch.equal(
        global_(state_a, mask, owner), global_(state_b, mask, owner)
    )


def test_global_team_value_is_independent_of_owner() -> None:
    critic = IsomorphicTeamValueCritic(2, 2, "global", hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state = torch.tensor([[[1.0, 0.0], [0.0, 2.0]]])
    mask = torch.tensor([[True, True]])

    first = critic(state, mask, torch.tensor([0]))
    second = critic(state, mask, torch.tensor([1]))

    torch.testing.assert_close(first, second, rtol=0, atol=0)


def test_global_team_value_uses_unmasked_remote_agent() -> None:
    critic = IsomorphicTeamValueCritic(2, 2, "global", hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [2.0, 0.0]]])
    mask = torch.tensor([[True, True]])

    value_a = critic(state_a, mask, torch.tensor([0]))
    value_b = critic(state_b, mask, torch.tensor([0]))

    assert not torch.equal(value_a, value_b)


def test_local_team_value_ignores_remote_agent() -> None:
    critic = IsomorphicTeamValueCritic(2, 2, "local", hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [2.0, 0.0]]])
    mask = torch.tensor([[True, True]])

    torch.testing.assert_close(
        critic(state_a, mask, torch.tensor([0])),
        critic(state_b, mask, torch.tensor([0])),
        rtol=0,
        atol=0,
    )


@pytest.mark.parametrize("context_scope", ("local", "global"))
def test_team_value_ignores_masked_rows(context_scope: str) -> None:
    critic = IsomorphicTeamValueCritic(2, 2, context_scope, hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [999.0, 0.0]]])
    mask = torch.tensor([[True, False]])
    owner = torch.tensor([0])

    torch.testing.assert_close(
        critic(state_a, mask, owner),
        critic(state_b, mask, owner),
        rtol=0,
        atol=0,
    )


def test_team_value_critics_are_parameter_isomorphic() -> None:
    local = IsomorphicTeamValueCritic(4, 3, "local", hidden_dim=8)
    global_ = IsomorphicTeamValueCritic(4, 3, "global", hidden_dim=8)

    assert {
        name: tuple(value.shape) for name, value in local.state_dict().items()
    } == {
        name: tuple(value.shape) for name, value in global_.state_dict().items()
    }
    assert sum(p.numel() for p in local.parameters()) == sum(
        p.numel() for p in global_.parameters()
    )


def test_cooperative_policy_routes_to_team_value_critic() -> None:
    local_policy = MAPPOPolicy(
        obs_dim=4,
        num_agents=2,
        critic_scope="local",
        actor_init_seed=1234,
        critic_init_seed=5678,
        hidden_dim=8,
        phase_feature_dim=3,
        model_version=COOPERATIVE_MODEL_VERSION,
    )
    global_policy = MAPPOPolicy(
        obs_dim=4,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=1234,
        critic_init_seed=5678,
        hidden_dim=8,
        phase_feature_dim=3,
        model_version=COOPERATIVE_MODEL_VERSION,
    )

    assert isinstance(local_policy.actor, CandidateActor)
    assert isinstance(global_policy.actor, CandidateActor)
    assert isinstance(local_policy.critic, IsomorphicTeamValueCritic)
    assert isinstance(global_policy.critic, IsomorphicTeamValueCritic)
    for name, tensor in local_policy.actor.state_dict().items():
        assert torch.equal(tensor, global_policy.actor.state_dict()[name])
    assert list(local_policy.critic.state_dict()) == list(
        global_policy.critic.state_dict()
    )
    for name, tensor in local_policy.critic.state_dict().items():
        right = global_policy.critic.state_dict()[name]
        assert tensor.shape == right.shape
        assert torch.equal(tensor, right)
