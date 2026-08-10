from __future__ import annotations

import algorithms.mappo as entrypoint

from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.test_protocol import _metadata, _step


def test_protocol_entrypoint_collects_one_immutable_generation() -> None:
    config = MAPPOConfig(("demo_1",), hidden_dim=8)
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=20,
        critic_scope="global",
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=8,
        phase_feature_dim=11,
    )
    entrypoint.prepare_collector(
        policy_state=policy.state_dict(),
        config=config,
        policy_generation=7,
        rollout_seed=93001,
        actor_init_seed=123,
        critic_init_seed=456,
        expected_duration_s=120.0,
        mode="fixed",
        record_evaluation=False,
    )

    ready = entrypoint.initialize(_metadata())
    first = entrypoint.step(
        _step(5.0, current_phase=0, stage_elapsed=5.0)
    )
    entrypoint.step(
        _step(
            120.0,
            current_phase=0,
            stage="YELLOW",
            stage_elapsed=5.0,
            pending_phase=1,
            waiting=90.0,
        )
    )
    assert entrypoint.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    ) is None
    rollout = entrypoint.pop_collected_rollout()
    action_diagnostics = entrypoint.pop_collected_diagnostics()

    assert ready == {
        "protocol_version": "2.0",
        "episode_id": "protocol-test",
        "ready": True,
    }
    assert first["actions"]["signals"]["demo_1"] == {"target_phase": 0}
    assert rollout is not None
    assert rollout.seed == 93001
    assert rollout.policy_generation == 7
    assert len(rollout.transitions) == 1
    assert action_diagnostics is not None
    assert action_diagnostics["intersections"]["demo_1"]["decision_count"] == 1
    assert entrypoint.pop_collected_rollout() is None
    assert entrypoint.pop_collected_diagnostics() is None
