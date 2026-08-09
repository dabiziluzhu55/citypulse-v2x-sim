from __future__ import annotations

import copy

import numpy as np
import pytest
import torch

from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.diagnostics import (
    ActionServiceDiagnostics,
    merge_action_diagnostics,
    merge_reward_diagnostics,
    probe_critic_signal,
    reward_metric_alignment,
    summarize_reward_results,
)
from algorithms.mappo.features import CentralizedState
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.parallel_train import WorkerRollout
from algorithms.mappo.reward import V5ARewardResult
from algorithms.mappo.rollout import Transition


def test_action_diagnostics_preserve_candidate_semantics_and_na() -> None:
    tracker = ActionServiceDiagnostics({"demo_1": (0, 1, 2)})
    tracker.observe_state(
        "demo_1", simulation_time_s=0.0, stage="GREEN", current_phase=0
    )
    tracker.observe_state(
        "demo_1", simulation_time_s=5.0, stage="GREEN", current_phase=0
    )
    tracker.observe_decision(
        "demo_1",
        action_mask=np.asarray([True, True, False]),
        selected_action=1,
    )
    tracker.observe_state(
        "demo_1", simulation_time_s=10.0, stage="YELLOW", current_phase=0
    )
    tracker.observe_state(
        "demo_1", simulation_time_s=20.0, stage="GREEN", current_phase=1
    )
    tracker.observe_state(
        "demo_1", simulation_time_s=35.0, stage="GREEN", current_phase=1
    )
    tracker.observe_decision(
        "demo_1",
        action_mask=np.asarray([False, True, False]),
        selected_action=1,
    )

    snapshot = tracker.snapshot()
    tls = snapshot["intersections"]["demo_1"]
    phase_0, phase_1, phase_2 = tls["candidates"]

    assert tls["decision_count"] == 2
    assert phase_0["candidate_index"] == 0
    assert phase_0["phase"] == 0
    assert phase_0["available_count"] == 1
    assert phase_0["selected_count"] == 0
    assert phase_0["selection_rate_when_available"] == 0.0
    assert phase_0["never_selected_while_available"] is True
    assert phase_0["max_available_opportunities_without_selection"] == 1
    assert phase_1["selected_count"] == 2
    assert phase_1["green_entry_count"] == 1
    assert phase_2["available_count"] == 0
    assert phase_2["selection_rate_when_available"] is None
    assert phase_2["max_observed_service_gap_s"] == pytest.approx(35.0)


def test_action_diagnostics_merge_counts_and_preserves_missing_rate() -> None:
    first = ActionServiceDiagnostics({"demo_1": (0, 1)})
    first.observe_state(
        "demo_1", simulation_time_s=5.0, stage="GREEN", current_phase=0
    )
    first.observe_decision(
        "demo_1",
        action_mask=np.asarray([True, False]),
        selected_action=0,
    )
    second = ActionServiceDiagnostics({"demo_1": (0, 1)})
    second.observe_state(
        "demo_1", simulation_time_s=10.0, stage="GREEN", current_phase=0
    )
    second.observe_decision(
        "demo_1",
        action_mask=np.asarray([True, False]),
        selected_action=0,
    )

    merged = merge_action_diagnostics((first.snapshot(), second.snapshot()))
    candidates = merged["intersections"]["demo_1"]["candidates"]

    assert merged["episodes_available"] == 2
    assert candidates[0]["available_count"] == 2
    assert candidates[0]["selection_rate_when_available"] == 1.0
    assert candidates[1]["selection_rate_when_available"] is None
    assert candidates[1]["episodes_available"] == 0


def _reward_result(
    reward: float,
    raw_reward: float,
    *,
    d: float,
    flow: float,
    spillback: float,
    waiting_gain: float,
) -> V5ARewardResult:
    return V5ARewardResult(
        reward=reward,
        raw_reward=raw_reward,
        components={
            "D": d,
            "L": 0.2,
            "S": 0.3,
            "Qmax": 0.4,
            "F_safe": flow,
            "B": spillback,
            "H": waiting_gain,
        },
        observations=3,
        observed_seconds=15.0,
    )


def test_reward_summary_exposes_components_weights_and_clipping() -> None:
    summary = summarize_reward_results(
        {
            "demo_1": (
                _reward_result(
                    -3.0,
                    -4.0,
                    d=1.0,
                    flow=0.5,
                    spillback=0.2,
                    waiting_gain=0.1,
                ),
                _reward_result(
                    1.0,
                    2.0,
                    d=0.5,
                    flow=1.0,
                    spillback=0.0,
                    waiting_gain=0.2,
                ),
            )
        }
    )

    assert summary["transition_count"] == 2
    assert summary["observed_seconds"] == pytest.approx(30.0)
    assert summary["reward"]["mean"] == pytest.approx(-1.0)
    assert summary["raw_reward"]["mean"] == pytest.approx(-1.0)
    assert summary["clipped_count"] == 2
    assert summary["clipped_fraction"] == 1.0
    assert summary["components"]["D"]["mean"] == pytest.approx(0.75)
    assert summary["weighted_components"]["congestion"]["mean"] == pytest.approx(
        -0.45
    )
    assert summary["weighted_components"]["safe_flow"]["mean"] == pytest.approx(
        0.15
    )
    assert summary["intersections"]["demo_1"]["transition_count"] == 2

    merged = merge_reward_diagnostics((summary, summary))
    assert merged["episodes_available"] == 2
    assert merged["transition_count"] == 4
    assert merged["reward"]["mean"] == pytest.approx(-1.0)
    assert merged["components"]["D"]["mean"] == pytest.approx(0.75)


def test_empty_reward_diagnostics_preserve_missing_values_as_na() -> None:
    merged = merge_reward_diagnostics(())

    assert merged["episodes_available"] == 0
    assert merged["transition_count"] == 0
    assert merged["reward"] == {
        "sum": None,
        "mean": None,
        "min": None,
        "max": None,
    }
    assert merged["raw_reward"]["mean"] is None
    assert merged["clipped_fraction"] is None
    assert merged["intersections"] == {}


def _policy(config: MAPPOConfig, scope: str, critic_seed: int) -> MAPPOPolicy:
    return MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=len(config.intersection_ids),
        critic_scope=scope,
        actor_init_seed=123,
        critic_init_seed=critic_seed,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
    )


def _probe_transition(
    policy: MAPPOPolicy,
    config: MAPPOConfig,
    *,
    agent_index: int,
    decision_time: float,
    reward: float,
    action: int,
    terminal: bool,
) -> Transition:
    observations = np.zeros(
        (len(config.intersection_ids), config.obs_dim), dtype=np.float32
    )
    observations[0, 0] = decision_time / 10.0
    observations[1, 1] = reward
    next_observations = observations.copy()
    next_observations[:, 2] += 0.25
    mask = np.ones(len(config.intersection_ids), dtype=np.bool_)
    state = CentralizedState(observations, mask, config.intersection_ids)
    next_state = CentralizedState(
        next_observations, mask.copy(), config.intersection_ids
    )
    local_obs = observations[agent_index].copy()
    phase_features = np.zeros((2, config.phase_feature_dim), dtype=np.float32)
    phase_features[0, 0] = decision_time / 20.0
    phase_features[1, 1] = reward
    action_mask = np.ones(2, dtype=np.bool_)
    with torch.no_grad():
        distribution = policy.actor(
            torch.from_numpy(local_obs).unsqueeze(0),
            torch.from_numpy(phase_features).unsqueeze(0),
            torch.from_numpy(action_mask).unsqueeze(0),
        )
        action_tensor = torch.tensor([action], dtype=torch.long)
        log_prob = float(distribution.log_prob(action_tensor).item())
    return Transition(
        local_obs=local_obs,
        phase_features=phase_features,
        action_mask=action_mask,
        global_state=state,
        agent_index=agent_index,
        action=action,
        requested_phase=action,
        applied_phase=action,
        log_prob=log_prob,
        value=0.0,
        reward=reward,
        decision_time_s=decision_time,
        applied_time_s=decision_time,
        policy_generation=0,
        next_local_obs=next_observations[agent_index].copy(),
        next_global_state=next_state,
        next_value=0.0,
        terminated=terminal,
        truncated=False,
    )


def test_probe_uses_same_rollout_and_frozen_actor_for_both_critics() -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"), hidden_dim=8, minibatch_size=4
    )
    source = _policy(config, "global", 456)
    local = _policy(config, "local", 789)
    global_policy = _policy(config, "global", 987)
    transitions = (
        _probe_transition(
            source,
            config,
            agent_index=0,
            decision_time=5.0,
            reward=1.0,
            action=0,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=0,
            decision_time=10.0,
            reward=0.3,
            action=0,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=0,
            decision_time=15.0,
            reward=-0.4,
            action=1,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=0,
            decision_time=20.0,
            reward=0.7,
            action=1,
            terminal=True,
        ),
        _probe_transition(
            source,
            config,
            agent_index=1,
            decision_time=5.0,
            reward=0.2,
            action=1,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=1,
            decision_time=10.0,
            reward=-0.3,
            action=1,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=1,
            decision_time=15.0,
            reward=0.8,
            action=0,
            terminal=False,
        ),
        _probe_transition(
            source,
            config,
            agent_index=1,
            decision_time=20.0,
            reward=1.3,
            action=0,
            terminal=True,
        ),
    )
    worker = WorkerRollout(
        seed=9701,
        status="ok",
        policy_generation=0,
        policy_digest="digest",
        config_signature="config",
        local_observation_schema="local",
        centralized_state_schema="global",
        transitions=transitions,
        pending_count=0,
        invalid_reason=None,
        error=None,
        action_diagnostics={
            "schema": "mappo_action_service_v1",
            "intersections": {
                "demo_1": {
                    "decision_count": 4,
                    "candidates": (
                        {"candidate_index": 0, "phase": 1},
                        {"candidate_index": 1, "phase": 3},
                    ),
                },
                "demo_2": {
                    "decision_count": 4,
                    "candidates": (
                        {"candidate_index": 0, "phase": 2},
                        {"candidate_index": 1, "phase": 4},
                    ),
                },
            },
        },
    )
    before = copy.deepcopy(source.actor.state_dict())

    diagnostics = probe_critic_signal(
        (worker,),
        config=config,
        actor_policy=source,
        local_policy=local,
        global_policy=global_policy,
    )

    assert diagnostics["sample_count"] == 8
    assert diagnostics["actor_gradient_definition"] == (
        "first_step_score_function_surrogate_without_entropy_or_ppo_clipping"
    )
    assert diagnostics["actor_rollout_log_prob_max_abs_error"] < 1e-6
    assert 0.0 <= diagnostics["advantage_sign_disagreement_fraction"] <= 1.0
    assert diagnostics["advantage_correlation"] is not None
    assert diagnostics["actor_gradient_cosine"] is not None
    assert -1.0 <= diagnostics["actor_gradient_cosine"] <= 1.0
    assert set(diagnostics["per_agent"]) == {"0", "1"}
    assert diagnostics["per_agent"]["0"]["intersection_id"] == "demo_1"
    assert diagnostics["per_agent"]["1"]["intersection_id"] == "demo_2"
    ablations = diagnostics["normalization_ablations"]
    assert set(ablations) == {"global", "per_agent", "none"}
    assert ablations["global"]["local_global_gradient_cosine"] == pytest.approx(
        diagnostics["actor_gradient_cosine"]
    )
    for mode in ablations.values():
        assert mode["sample_count"] == 8
        assert mode["aggregate_reconstruction_max_abs_error"] < 1e-6
        assert 0.0 <= mode["local_gradient_coherence_ratio"] <= 1.0
        assert 0.0 <= mode["global_gradient_coherence_ratio"] <= 1.0
        assert set(mode["per_agent"]) == {"0", "1"}
        assert mode["per_agent"]["0"]["intersection_id"] == "demo_1"
        assert mode["per_agent"]["1"]["intersection_id"] == "demo_2"
        assert mode["local_global_gradient_cosine"] is not None
        assert -1.0 <= mode["local_global_gradient_cosine"] <= 1.0
    for agent in ablations["per_agent"]["per_agent"].values():
        assert agent["local_weight"]["mean"] == pytest.approx(0.0, abs=1e-5)
        assert agent["global_weight"]["mean"] == pytest.approx(0.0, abs=1e-5)
        assert agent["local_weight"]["std"] == pytest.approx(1.0)
        assert agent["global_weight"]["std"] == pytest.approx(1.0)
    assert diagnostics["normalization_comparison"][
        "local_global_cosine_delta_per_agent_minus_global"
    ] == pytest.approx(
        ablations["per_agent"]["local_global_gradient_cosine"]
        - ablations["global"]["local_global_gradient_cosine"]
    )
    action_signal = diagnostics["action_score_covariance"]
    assert action_signal["schema"] == "mappo_action_score_covariance_v1"
    assert action_signal["normalization"] == "global"
    assert action_signal["sample_count"] == 8
    assert action_signal[
        "aggregate_weighted_gradient_reconstruction_max_abs_error"
    ] < 1e-6
    assert action_signal[
        "aggregate_mean_plus_covariance_reconstruction_max_abs_error"
    ] < 1e-6
    assert action_signal["local_global_weighted_gradient_cosine"] == pytest.approx(
        diagnostics["actor_gradient_cosine"]
    )
    assert set(action_signal["per_agent"]) == {"0", "1"}
    assert action_signal["per_agent"]["0"]["intersection_id"] == "demo_1"
    assert action_signal["per_agent"]["1"]["intersection_id"] == "demo_2"
    assert sum(
        action["sample_count"]
        for action in action_signal["per_agent"]["0"]["actions"].values()
    ) == 4
    assert action_signal["local_within_action_covariance_component_norm"] > 0.0
    assert action_signal["global_within_action_covariance_component_norm"] > 0.0
    assert action_signal[
        "local_global_within_action_covariance_component_cosine"
    ] is not None
    assert action_signal["per_agent"]["0"]["actions"]["0"]["phase"] == 1
    assert action_signal["per_agent"]["0"]["actions"]["1"]["phase"] == 3
    assert action_signal["per_agent"]["1"]["actions"]["0"]["phase"] == 2
    assert action_signal["per_agent"]["1"]["actions"]["1"]["phase"] == 4
    response = diagnostics["one_step_policy_response"]
    assert response["schema"] == "mappo_one_step_policy_response_v1"
    assert response["step_size"] == pytest.approx(config.actor_lr)
    assert response["sample_count"] == 8
    assert response["local_parameter_step_norm"] == pytest.approx(
        config.actor_lr * diagnostics["local_actor_gradient_norm"]
    )
    assert response["global_parameter_step_norm"] == pytest.approx(
        config.actor_lr * diagnostics["global_actor_gradient_norm"]
    )
    assert response["parameter_step_reconstruction_max_abs_error"] < 1e-12
    assert response["probability_mass_conservation_max_abs_error"] < 1e-6
    assert response["local_global_valid_probability_response_cosine"] is not None
    assert -1.0 <= response[
        "local_global_valid_probability_response_cosine"
    ] <= 1.0
    assert 0.0 <= response[
        "selected_action_response_sign_disagreement_fraction"
    ] <= 1.0
    assert set(response["per_agent"]) == {"0", "1"}
    assert response["per_agent"]["0"]["intersection_id"] == "demo_1"
    assert response["per_agent"]["1"]["intersection_id"] == "demo_2"
    assert response["per_agent"]["0"]["actions"]["0"]["phase"] == 1
    assert response["per_agent"]["0"]["actions"]["1"]["phase"] == 3
    for name, value in before.items():
        torch.testing.assert_close(source.actor.state_dict()[name], value)


def test_reward_metric_alignment_marks_degenerate_values_na() -> None:
    records = (
        {
            "reward_diagnostics": {
                "reward": {"mean": -2.0},
                "raw_reward": {"mean": -2.5},
                "components": {"D": {"mean": 0.8}},
            },
            "metrics": {"arrived": 10, "waiting": 100.0},
        },
        {
            "reward_diagnostics": {
                "reward": {"mean": -1.0},
                "raw_reward": {"mean": -1.5},
                "components": {"D": {"mean": 0.4}},
            },
            "metrics": {"arrived": 20, "waiting": 100.0},
        },
    )

    alignment = reward_metric_alignment(records)

    assert alignment["workers_available"] == 2
    assert alignment["correlations"]["reward_mean"]["arrived"] == pytest.approx(
        1.0
    )
    assert alignment["correlations"]["reward_mean"]["waiting"] is None


import numpy as np
import torch

from algorithms.mappo.diagnostics import (
    advantage_quantiles,
    td_target_duplicate_stats,
    actor_grad_cosine,
)


def test_advantage_quantiles():
    adv = torch.tensor([-2.0, -1.0, 0.0, 1.0, 2.0])
    out = advantage_quantiles(adv)
    assert out["mean"] == 0.0
    assert out["p10"] <= out["p50"] <= out["p90"]
    assert out["positive_fraction"] == 0.4
    assert out["std"] > 0


def test_td_target_duplicate_stats_detects_broadcast():
    # 两个 joint step，各 2 个 agent：joint0 共享 target（广播），joint1 独立
    returns = torch.tensor([1.0, 1.0, 2.0, 3.0])
    joint_idx = torch.tensor([0, 0, 1, 1])
    out = td_target_duplicate_stats(returns, joint_idx)
    assert out["duplicate_rate"] == 0.5  # 4 行中 2 行是重复 target
    assert out["variance"] >= 0.0


def test_actor_grad_cosine_shape():
    # 用最小模型：2 agent、obs_dim=4、phase_dim=3
    from algorithms.mappo.models import CandidateActor
    actor = CandidateActor(obs_dim=4, phase_feature_dim=3, hidden_dim=8)
    local_obs = torch.randn(4, 4)
    phase = torch.randn(4, 1, 3)
    mask = torch.ones(4, 1, dtype=torch.bool)
    actions = torch.zeros(4, dtype=torch.long)
    out = actor_grad_cosine(actor, local_obs, phase, mask, actions)
    assert {"mean", "p10", "p50", "p90"} <= set(out)
    assert -1.0 <= out["mean"] <= 1.0
