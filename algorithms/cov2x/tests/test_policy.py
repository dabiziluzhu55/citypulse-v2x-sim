"""Unit tests for the learned vehicle policy (requires PyTorch)."""

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from algorithms.cov2x.vehicle.policy import (  # noqa: E402
    VehicleActorCritic,
    VehiclePPOAgent,
    VehiclePolicyConfig,
)


def _batch(n=8, hidden=16):
    rng = np.random.default_rng(0)
    states = rng.normal(size=(n, 41)).astype(np.float32)
    masks = np.ones((n, 3), dtype=bool)
    # A few slots only allow KEEP.
    masks[::3, 1:] = False
    return states, masks


def test_actor_critic_shapes_and_determinism():
    config = VehiclePolicyConfig(hidden_dim=16)
    net = VehicleActorCritic(config)
    states, masks = _batch()
    obs = torch.from_numpy(states)
    mask = torch.from_numpy(masks)
    step = net.act(obs, mask, deterministic=False)
    assert step.lane_action.shape == (8,)
    assert step.speed_bin.shape == (8,)
    assert step.logprob.shape == (8,)
    assert step.value.shape == (8,)
    assert torch.isfinite(step.logprob).all()

    det = net.act(obs, mask, deterministic=True)
    det2 = net.act(obs, mask, deterministic=True)
    assert torch.equal(det.lane_action, det2.lane_action)
    assert torch.equal(det.speed_bin, det2.speed_bin)


def test_masked_actions_never_select_invalid_lane():
    config = VehiclePolicyConfig(hidden_dim=16)
    agent = VehiclePPOAgent(config)
    states, masks = _batch()
    batch = agent.act_tensors(states, masks, deterministic=False)
    for index in range(len(states)):
        assert bool(masks[index][batch.lane_action[index]])


def test_invalid_action_has_nonfinite_logprob():
    config = VehiclePolicyConfig(hidden_dim=16)
    net = VehicleActorCritic(config)
    states, masks = _batch(n=2)
    obs = torch.from_numpy(states)
    mask = torch.from_numpy(masks)
    logprob, _, _ = net.evaluate(
        obs,
        mask,
        torch.tensor([1, 2]),  # left/right on slots where only KEEP is legal
        torch.tensor([0, 0]),
    )
    # Slot 0 only allows KEEP: action 1 must have zero probability.
    assert logprob[0] == -torch.inf
    assert torch.isfinite(logprob[1])  # slot 1 allows all lanes


def test_init_biases_favor_keep_and_full_speed():
    config = VehiclePolicyConfig(hidden_dim=16)
    net = VehicleActorCritic(config)
    states = torch.zeros(2, 41)
    mask = torch.ones(2, 3, dtype=torch.bool)
    step = net.act(states, mask, deterministic=True)
    assert (step.lane_action == 0).all()
    assert (step.speed_bin == config.speed_action_dim - 1).all()


def test_ppo_update_changes_parameters():
    config = VehiclePolicyConfig(
        hidden_dim=16,
        ppo_epochs=2,
        minibatch_size=4,
        lr_actor=1e-2,
        lr_critic=1e-2,
    )
    agent = VehiclePPOAgent(config)
    states, masks = _batch(n=16)
    initial = agent.act_tensors(states, masks, deterministic=False)
    returns = initial.value + 0.5
    advantages = np.ones(16, dtype=np.float32)
    diagnostics = agent.update(
        states=states,
        masks=masks,
        lane_actions=initial.lane_action,
        speed_bins=initial.speed_bin,
        old_logprobs=initial.logprob,
        advantages=advantages,
        returns=returns,
    )
    assert agent.policy_generation == 1
    assert diagnostics["parameter_delta_l2"] > 0.0
    for key in (
        "actor_loss",
        "critic_loss",
        "entropy",
        "clip_fraction",
        "actor_grad_norm",
        "critic_grad_norm",
    ):
        assert key in diagnostics
    assert diagnostics["advantage_mean"] == pytest.approx(0.0, abs=1e-6)


def test_checkpoint_round_trip():
    config = VehiclePolicyConfig(hidden_dim=16)
    agent = VehiclePPOAgent(config)
    state = agent.state_dict()
    clone = VehiclePPOAgent(config)
    clone.load_state_dict(state)
    for name, parameter in agent.net.named_parameters():
        assert torch.equal(parameter, dict(clone.net.named_parameters())[name])
