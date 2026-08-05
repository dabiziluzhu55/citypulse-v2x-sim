from __future__ import annotations

import inspect

import pytest
import torch
from torch import nn

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    COOPERATIVE_M1_MODEL_VERSION,
    COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
    MAPPO_V2_RESIDUAL_MODEL_VERSION,
    MAPPO_V2_SHARED_MODEL_VERSION,
)
from algorithms.mappo.models import (
    AgentConditionedCritic,
    CandidateActor,
    IsomorphicContextCritic,
    IsomorphicTeamValueCritic,
    MAPPOPolicy,
    ResidualCandidateActor,
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


def test_critic_changes_when_another_valid_agent_changes() -> None:
    critic = AgentConditionedCritic(obs_dim=2, num_agents=2, hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [2.0, 0.0]]])
    mask = torch.tensor([[True, True]])
    owner = torch.tensor([0])

    value_a = critic(state_a, mask, owner)
    value_b = critic(state_b, mask, owner)

    assert value_a.shape == (1, 1)
    assert not torch.equal(value_a, value_b)


def test_critic_ignores_masked_agent_values() -> None:
    critic = AgentConditionedCritic(obs_dim=2, num_agents=2, hidden_dim=4)
    _set_deterministic_nonzero_weights(critic)
    state_a = torch.tensor([[[1.0, 0.0], [0.0, 0.0]]])
    state_b = torch.tensor([[[1.0, 0.0], [999.0, -999.0]]])
    mask = torch.tensor([[True, False]])
    owner = torch.tensor([0])

    torch.testing.assert_close(
        critic(state_a, mask, owner),
        critic(state_b, mask, owner),
        rtol=0,
        atol=0,
    )


def test_critic_rejects_masked_owner() -> None:
    critic = AgentConditionedCritic(obs_dim=2, num_agents=2, hidden_dim=4)

    with pytest.raises(ValueError, match="owner agent must be valid"):
        critic(
            torch.zeros((1, 2, 2)),
            torch.tensor([[False, True]]),
            torch.tensor([0]),
        )


def test_critic_rejects_all_masked_state() -> None:
    critic = AgentConditionedCritic(obs_dim=2, num_agents=2, hidden_dim=4)

    with pytest.raises(ValueError, match="at least one valid agent"):
        critic(
            torch.zeros((1, 2, 2)),
            torch.tensor([[False, False]]),
            torch.tensor([0]),
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


def _v2_policy(
    *, actor_variant: str, critic_scope: str
) -> MAPPOPolicy:
    model_version = (
        MAPPO_V2_RESIDUAL_MODEL_VERSION
        if actor_variant == "residual"
        else MAPPO_V2_SHARED_MODEL_VERSION
    )
    return MAPPOPolicy(
        obs_dim=132,
        num_agents=20,
        critic_scope=critic_scope,
        actor_init_seed=142,
        critic_init_seed=242,
        hidden_dim=128,
        phase_feature_dim=11,
        model_version=model_version,
        actor_variant=actor_variant,
        residual_hidden_dim=32,
        identity_offset=9,
        residual_init_seed=342,
    )


def _actor_fixture(owner: int = 3) -> tuple[torch.Tensor, ...]:
    obs = torch.linspace(-0.5, 0.5, 132).unsqueeze(0)
    obs[:, 9:29] = 0.0
    obs[:, 9 + owner] = 1.0
    phase_features = torch.zeros((1, 3, 11))
    phase_features[0, 0, 0] = 1.0
    phase_features[0, 1, 1] = 1.0
    phase_features[0, 2, 2] = 1.0
    action_mask = torch.tensor([[True, True, False]])
    return obs, phase_features, action_mask


def test_residual_actor_parameter_shapes_and_exact_initial_parity() -> None:
    shared_policy = _v2_policy(actor_variant="shared", critic_scope="local")
    residual_policy = _v2_policy(
        actor_variant="residual", critic_scope="local"
    )
    assert isinstance(residual_policy.actor, ResidualCandidateActor)
    residual = residual_policy.actor
    assert residual.residual_w1.shape == (20, 32, 143)
    assert residual.residual_b1.shape == (20, 32)
    assert residual.residual_w2.shape == (20, 1, 32)
    assert torch.count_nonzero(residual.residual_w2) == 0

    shared_state = shared_policy.actor.state_dict()
    residual_state = residual.state_dict()
    for name, tensor in shared_state.items():
        assert torch.equal(tensor, residual_state[name])

    obs, phase_features, action_mask = _actor_fixture()
    shared_logits = shared_policy.actor.unmasked_logits(obs, phase_features)
    residual_logits = residual.unmasked_logits(obs, phase_features)
    assert torch.equal(shared_logits, residual_logits)
    shared_probs = shared_policy.actor(obs, phase_features, action_mask).probs
    residual_probs = residual(obs, phase_features, action_mask).probs
    assert torch.equal(shared_probs, residual_probs)
    assert torch.equal(shared_probs.argmax(dim=1), residual_probs.argmax(dim=1))


@pytest.mark.parametrize(
    "identity",
    [
        torch.zeros(20),
        torch.cat((torch.ones(2), torch.zeros(18))),
        torch.cat((torch.tensor([0.5]), torch.zeros(19))),
    ],
)
def test_residual_actor_rejects_invalid_identity(identity: torch.Tensor) -> None:
    actor = _v2_policy(
        actor_variant="residual", critic_scope="local"
    ).actor
    obs, phase_features, _ = _actor_fixture()
    obs[:, 9:29] = identity

    with pytest.raises(ValueError, match="identity.*one-hot"):
        actor.unmasked_logits(obs, phase_features)


def test_residual_actor_two_step_gradient_path_and_owner_isolation() -> None:
    actor = _v2_policy(
        actor_variant="residual", critic_scope="local"
    ).actor
    assert isinstance(actor, ResidualCandidateActor)
    obs, phase_features, action_mask = _actor_fixture(owner=3)
    action = torch.tensor([0])

    first_loss = -actor(obs, phase_features, action_mask).log_prob(action).mean()
    first_loss.backward()
    assert actor.residual_w2.grad is not None
    assert actor.residual_w1.grad is not None
    assert actor.residual_b1.grad is not None
    assert actor.residual_w2.grad[3].abs().sum() > 0
    assert torch.count_nonzero(actor.residual_w2.grad[:3]) == 0
    assert torch.count_nonzero(actor.residual_w2.grad[4:]) == 0
    assert torch.count_nonzero(actor.residual_w1.grad) == 0
    assert torch.count_nonzero(actor.residual_b1.grad) == 0

    optimizer = torch.optim.SGD([actor.residual_w2], lr=0.1)
    optimizer.step()
    actor.zero_grad(set_to_none=True)
    second_loss = -actor(obs, phase_features, action_mask).log_prob(action).mean()
    second_loss.backward()
    assert actor.residual_w1.grad is not None
    assert actor.residual_b1.grad is not None
    assert actor.residual_w1.grad[3].abs().sum() > 0
    assert actor.residual_b1.grad[3].abs().sum() > 0
    assert torch.count_nonzero(actor.residual_w1.grad[:3]) == 0
    assert torch.count_nonzero(actor.residual_w1.grad[4:]) == 0


def test_residual_actor_can_score_candidates_differently() -> None:
    actor = _v2_policy(
        actor_variant="residual", critic_scope="local"
    ).actor
    assert isinstance(actor, ResidualCandidateActor)
    obs, phase_features, _ = _actor_fixture(owner=3)
    with torch.no_grad():
        actor.residual_w2[3].fill_(0.1)

    deltas = actor.residual_logits(obs, phase_features)

    assert deltas.shape == (1, 3)
    assert torch.unique(deltas).numel() > 1


def test_isomorphic_critics_have_equal_tensors_and_parameter_counts() -> None:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(242)
        local = IsomorphicContextCritic(
            obs_dim=4, num_agents=3, context_scope="local", hidden_dim=8
        )
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(242)
        global_ = IsomorphicContextCritic(
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
    local = IsomorphicContextCritic(
        obs_dim=2, num_agents=2, context_scope="local", hidden_dim=4
    )
    global_ = IsomorphicContextCritic(
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


def test_owner_conditioned_cooperative_policy_uses_isomorphic_critic() -> None:
    policies = tuple(
        MAPPOPolicy(
            obs_dim=132,
            num_agents=20,
            critic_scope=scope,
            actor_init_seed=1234,
            critic_init_seed=5678,
            hidden_dim=128,
            phase_feature_dim=11,
            model_version=COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
        )
        for scope in ("local", "global")
    )
    local_policy, global_policy = policies

    assert isinstance(local_policy.actor, CandidateActor)
    assert isinstance(global_policy.actor, CandidateActor)
    assert isinstance(local_policy.critic, IsomorphicContextCritic)
    assert isinstance(global_policy.critic, IsomorphicContextCritic)
    assert {
        name: tuple(tensor.shape)
        for name, tensor in local_policy.critic.state_dict().items()
    } == {
        name: tuple(tensor.shape)
        for name, tensor in global_policy.critic.state_dict().items()
    }
    assert sum(
        parameter.numel() for parameter in local_policy.critic.parameters()
    ) == 69_121
    assert sum(
        parameter.numel() for parameter in global_policy.critic.parameters()
    ) == 69_121
    for name, tensor in local_policy.critic.state_dict().items():
        assert torch.equal(tensor, global_policy.critic.state_dict()[name])


def test_owner_conditioned_cooperative_critic_can_distinguish_owners() -> None:
    policy = MAPPOPolicy(
        obs_dim=4,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=1234,
        critic_init_seed=5678,
        hidden_dim=8,
        phase_feature_dim=3,
        model_version=COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
    )
    state = torch.tensor(
        [[[1.0, 0.0, 0.0, 0.0], [0.0, 2.0, 0.0, 0.0]]]
    ).expand(2, -1, -1)
    mask = torch.ones((2, 2), dtype=torch.bool)
    owners = torch.tensor([0, 1])

    values = policy.value(state, mask, owners).squeeze(-1)

    assert values[0] != values[1]


def test_v2_four_arm_initialization_pairing() -> None:
    shared_local = _v2_policy(actor_variant="shared", critic_scope="local")
    shared_global = _v2_policy(actor_variant="shared", critic_scope="global")
    residual_local = _v2_policy(
        actor_variant="residual", critic_scope="local"
    )
    residual_global = _v2_policy(
        actor_variant="residual", critic_scope="global"
    )

    for left, right in (
        (shared_local.actor, shared_global.actor),
        (residual_local.actor, residual_global.actor),
        (shared_local.critic, shared_global.critic),
        (residual_local.critic, residual_global.critic),
    ):
        assert list(left.state_dict()) == list(right.state_dict())
        for name, tensor in left.state_dict().items():
            assert torch.equal(tensor, right.state_dict()[name])

    for name, tensor in shared_local.actor.state_dict().items():
        assert torch.equal(tensor, residual_local.actor.state_dict()[name])


def test_m1_model_version_uses_agent_conditioned_critic():
    policy = MAPPOPolicy(
        obs_dim=8,
        num_agents=2,
        critic_scope="global",
        actor_init_seed=1,
        critic_init_seed=2,
        model_version=COOPERATIVE_M1_MODEL_VERSION,
    )
    assert isinstance(policy.critic, AgentConditionedCritic)
