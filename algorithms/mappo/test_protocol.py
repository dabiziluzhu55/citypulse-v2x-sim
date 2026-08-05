from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
import torch

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    REWARD_SCOPE_SHARED_TEAM,
    MAPPOConfig,
)
from algorithms.mappo.controller import MAPPOController
from algorithms.mappo.features import CentralizedState
from algorithms.mappo.joint_rollout import JointTransition
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.parallel_train import build_ppo_batch
from algorithms.mappo.rollout import ActionAlignmentError


def _metadata() -> dict:
    return {
        "episode_id": "protocol-test",
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": {
            "demo_1": {
                "phase_order": [0, 1],
                "incoming_lanes": ["in"],
                "outgoing_lanes": ["out"],
                "lanes": {
                    "in": {
                        "edge_id": "in_edge",
                        "length_m": 150.0,
                        "speed_limit_mps": 15.0,
                    },
                    "out": {
                        "edge_id": "out_edge",
                        "length_m": 150.0,
                        "speed_limit_mps": 15.0,
                    },
                },
                "connections": [
                    {
                        "connection_id": "movement",
                        "from_lane": "in",
                        "to_lane": "out",
                    }
                ],
                "phases": {
                    0: {
                        "green_seconds": 30.0,
                        "connection_priorities": {"movement": "protected"},
                    },
                    1: {
                        "green_seconds": 30.0,
                        "connection_priorities": {"movement": "protected"},
                    },
                },
            }
        },
    }


def _step(
    time_s: float,
    *,
    current_phase: int,
    stage: str = "GREEN",
    stage_elapsed: float = 5.0,
    pending_phase: int | None = None,
    waiting: float = 100.0,
) -> dict:
    return {
        "episode_id": "protocol-test",
        "step_id": int(time_s / 5),
        "simulation_time": time_s,
        "intersections": {
            "demo_1": {
                "current_phase": current_phase,
                "stage": stage,
                "stage_elapsed": stage_elapsed,
                "pending_phase": pending_phase,
                "lanes": {
                    "in": {
                        "vehicle_count": 10,
                        "halting_count": 5,
                        "waiting_time": waiting,
                        "mean_speed": 7.5,
                        "occupancy": 25.0,
                    },
                    "out": {
                        "vehicle_count": 2,
                        "halting_count": 1,
                        "waiting_time": 5.0,
                        "mean_speed": 12.0,
                        "occupancy": 10.0,
                    },
                },
            }
        },
        "vehicles": {},
    }


def _controller() -> MAPPOController:
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
    return MAPPOController(
        metadata=_metadata(),
        config=config,
        policy=policy,
        mode="fixed",
        policy_generation=3,
        expected_duration_s=120.0,
        record_evaluation=False,
    )


def _cooperative_metadata() -> dict:
    metadata = _metadata()
    metadata["intersections"]["demo_2"] = deepcopy(
        metadata["intersections"]["demo_1"]
    )
    return metadata


def _cooperative_step(
    time_s: float,
    *,
    first_phase: int = 0,
    second_phase: int = 0,
    first_stage: str = "GREEN",
    second_stage: str = "GREEN",
    first_stage_elapsed: float = 5.0,
    second_stage_elapsed: float = 5.0,
    first_pending_phase: int | None = None,
    second_pending_phase: int | None = None,
    first_waiting: float = 100.0,
    second_waiting: float = 150.0,
) -> dict:
    payload = _step(
        time_s,
        current_phase=first_phase,
        stage=first_stage,
        stage_elapsed=first_stage_elapsed,
        pending_phase=first_pending_phase,
        waiting=first_waiting,
    )
    second = deepcopy(payload["intersections"]["demo_1"])
    second.update(
        {
            "current_phase": second_phase,
            "stage": second_stage,
            "stage_elapsed": second_stage_elapsed,
            "pending_phase": second_pending_phase,
        }
    )
    second["lanes"]["in"]["waiting_time"] = second_waiting
    payload["intersections"]["demo_2"] = second
    return payload


def _cooperative_config(
    critic_scope: str = "global",
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> MAPPOConfig:
    return MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope=critic_scope,
        model_version=model_version,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
        hidden_dim=8,
    )


def _cooperative_controller(
    *,
    critic_scope: str = "global",
    mode: str = "fixed",
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> MAPPOController:
    config = _cooperative_config(critic_scope, model_version)
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=20,
        critic_scope=critic_scope,
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=model_version,
    )
    return MAPPOController(
        metadata=_cooperative_metadata(),
        config=config,
        policy=policy,
        mode=mode,
        policy_generation=7,
        expected_duration_s=120.0,
        record_evaluation=False,
    )


def test_shared_joint_values_use_one_scalar_critic_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _cooperative_controller()
    state = CentralizedState(
        observations=np.zeros(
            (20, controller.config.obs_dim), dtype=np.float32
        ),
        agent_mask=np.ones(20, dtype=np.bool_),
        intersection_ids=controller.config.intersection_ids,
    )
    calls: list[tuple[tuple[int, ...], tuple[int, ...]]] = []

    def value_spy(
        global_obs: object, agent_mask: object, agent_index: object
    ):
        del agent_mask
        owners = np.asarray(agent_index)
        calls.append((tuple(global_obs.shape), tuple(owners.tolist())))
        return torch.as_tensor(owners, dtype=torch.float32).unsqueeze(-1)

    monkeypatch.setattr(controller.policy.critic, "forward", value_spy)

    values = controller._joint_values(state)

    assert values == (0.0, 0.0)
    assert calls == [((1, 20, controller.config.obs_dim), (0,))]


@pytest.mark.parametrize(
    ("mismatch", "message"),
    (
        ("model_version", "model version"),
        ("actor_variant", "actor variant"),
        ("obs_dim", "observation dimension"),
        ("phase_feature_dim", "phase feature dimension"),
        ("hidden_dim", "hidden dimension"),
        ("critic_num_agents", "critic agent count"),
    ),
)
def test_cooperative_controller_rejects_incompatible_policy_architecture(
    mismatch: str, message: str
) -> None:
    config = _cooperative_config()
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim + (1 if mismatch == "obs_dim" else 0),
        num_agents=21 if mismatch == "critic_num_agents" else 20,
        critic_scope=config.critic_scope,
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=(
            config.hidden_dim + 4
            if mismatch == "hidden_dim"
            else config.hidden_dim
        ),
        phase_feature_dim=(
            config.phase_feature_dim + 1
            if mismatch == "phase_feature_dim"
            else config.phase_feature_dim
        ),
        model_version=COOPERATIVE_MODEL_VERSION,
    )
    if mismatch == "actor_variant":
        policy.actor_variant = "residual"
    if mismatch == "model_version":
        policy.model_version = "mappo_v1"

    with pytest.raises(ValueError, match=message):
        MAPPOController(
            metadata=_cooperative_metadata(),
            config=config,
            policy=policy,
            mode="fixed",
            policy_generation=7,
            expected_duration_s=120.0,
            record_evaluation=False,
        )


def test_cooperative_all_eligible_frame_begins_one_fixed_order_joint() -> None:
    controller = _cooperative_controller()

    response = controller.step(_cooperative_step(5.0))

    assert response["actions"]["signals"] == {
        "demo_1": {"target_phase": 0},
        "demo_2": {"target_phase": 0},
    }
    pending = controller._joint_rollout.pending
    assert pending is not None
    assert pending.joint_step_id == 0
    assert tuple(
        item.agent_index for item in pending.agent_pendings
    ) == (0, 1)
    assert tuple(
        item.decision_time_s for item in pending.agent_pendings
    ) == (5.0, 5.0)


def test_global_critic_value_is_computed_once_per_joint_state(
    monkeypatch,
) -> None:
    controller = _cooperative_controller()
    calls: list[tuple[int, int]] = []

    def value_spy(global_state, agent_index: int) -> float:
        calls.append((id(global_state), agent_index))
        return float(len(calls))

    monkeypatch.setattr(controller, "_value", value_spy)
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage_elapsed=10.0,
            second_stage_elapsed=10.0,
        )
    )
    controller.step(
        _cooperative_step(
            20.0,
            first_stage_elapsed=20.0,
            second_stage_elapsed=20.0,
        )
    )

    assert tuple(owner for _state, owner in calls) == (0, 0)
    assert len({state for state, _owner in calls}) == 2
    completed = controller._joint_transitions[0]
    pending = controller._joint_rollout.pending
    assert pending is not None
    assert completed.next_values == (2.0, 2.0)
    assert tuple(item.value for item in pending.agent_pendings) == (2.0, 2.0)


def test_local_critic_value_is_computed_once_per_owner_per_joint_state(
    monkeypatch,
) -> None:
    controller = _cooperative_controller(critic_scope="local")
    calls: list[tuple[int, int]] = []

    def value_spy(global_state, agent_index: int) -> float:
        calls.append((id(global_state), agent_index))
        return float(10 * len(calls) + agent_index)

    monkeypatch.setattr(controller, "_value", value_spy)
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage_elapsed=10.0,
            second_stage_elapsed=10.0,
        )
    )
    controller.step(
        _cooperative_step(
            20.0,
            first_stage_elapsed=20.0,
            second_stage_elapsed=20.0,
        )
    )

    assert tuple(owner for _state, owner in calls) == (0, 1, 0, 1)
    assert len({state for state, _owner in calls}) == 2
    completed = controller._joint_transitions[0]
    pending = controller._joint_rollout.pending
    assert pending is not None
    assert completed.next_values == (30.0, 41.0)
    assert tuple(item.value for item in pending.agent_pendings) == (
        30.0,
        41.0,
    )


def test_cooperative_partial_joint_eligibility_invalidates_worker() -> None:
    controller = _cooperative_controller()

    with pytest.raises(RuntimeError, match="partial joint eligibility"):
        controller.step(
            _cooperative_step(5.0, second_stage_elapsed=0.0)
        )

    assert controller.invalid is True
    assert "partial joint eligibility" in (controller.invalid_reason or "")


def test_cooperative_model_inference_is_actor_only_and_joint_synchronous(
    monkeypatch,
) -> None:
    controller = _cooperative_controller(mode="model")

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "cooperative model inference must not use training-only state"
        )

    monkeypatch.setattr(controller._central_builder, "build", forbidden)
    monkeypatch.setattr(controller.policy, "value", forbidden)
    monkeypatch.setattr(
        controller, "_vehicle_interval_statistics", forbidden
    )
    monkeypatch.setattr(controller, "_new_reward", forbidden)
    monkeypatch.setattr(controller._joint_rollout, "begin", forbidden)

    response = controller.step(_cooperative_step(5.0))
    assert set(response["actions"]["signals"]) == {"demo_1", "demo_2"}
    assert all(
        action["target_phase"] in {0, 1}
        for action in response["actions"]["signals"].values()
    )

    with pytest.raises(RuntimeError, match="partial joint eligibility"):
        controller.step(
            _cooperative_step(
                20.0,
                first_stage_elapsed=20.0,
                second_stage="YELLOW",
                second_pending_phase=1,
            )
        )
    assert controller.invalid is True


def test_cooperative_waits_for_both_applied_phases_before_completion() -> None:
    controller = _cooperative_controller()
    first = controller.step(
        _cooperative_step(
            5.0,
            first_stage_elapsed=61.0,
            second_stage_elapsed=61.0,
        )
    )
    assert first["actions"]["signals"] == {
        "demo_1": {"target_phase": 1},
        "demo_2": {"target_phase": 1},
    }

    controller.step(
        _cooperative_step(
            10.0,
            first_phase=1,
            second_phase=0,
            second_stage="YELLOW",
            second_pending_phase=1,
        )
    )
    assert controller._joint_transitions == []

    controller.step(
        _cooperative_step(
            15.0,
            first_phase=1,
            second_phase=1,
            first_stage_elapsed=10.0,
            second_stage_elapsed=5.0,
        )
    )
    assert controller._joint_transitions == []

    response = controller.step(
        _cooperative_step(
            20.0,
            first_phase=1,
            second_phase=1,
            first_stage_elapsed=15.0,
            second_stage_elapsed=10.0,
        )
    )
    assert set(response["actions"]["signals"]) == {"demo_1", "demo_2"}
    assert len(controller._joint_transitions) == 1
    completed = controller._joint_transitions[0]
    assert tuple(
        item.applied_phase for item in completed.agent_transitions
    ) == (1, 1)


def test_cooperative_one_applied_mismatch_invalidates_whole_worker() -> None:
    controller = _cooperative_controller()
    controller.step(
        _cooperative_step(
            5.0,
            first_stage_elapsed=61.0,
            second_stage_elapsed=61.0,
        )
    )
    controller.step(
        _cooperative_step(
            10.0,
            first_phase=1,
            second_phase=0,
            second_stage="YELLOW",
            second_pending_phase=1,
        )
    )

    with pytest.raises(
        ActionAlignmentError, match=r"requested phase 1.*applied phase 0"
    ):
        controller.step(
            _cooperative_step(
                20.0,
                first_phase=1,
                second_phase=0,
                first_stage_elapsed=15.0,
                second_stage_elapsed=15.0,
            )
        )

    assert controller.invalid is True
    assert controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    ) is None


def test_cooperative_complete_window_returns_one_team_reward_joint() -> None:
    controller = _cooperative_controller()
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage_elapsed=10.0,
            second_stage_elapsed=10.0,
            first_waiting=90.0,
            second_waiting=130.0,
        )
    )
    controller.step(
        _cooperative_step(
            20.0,
            first_stage="YELLOW",
            second_stage="YELLOW",
            first_stage_elapsed=5.0,
            second_stage_elapsed=5.0,
            first_pending_phase=1,
            second_pending_phase=1,
            first_waiting=80.0,
            second_waiting=110.0,
        )
    )

    rollout = controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 20.0,
        }
    )

    assert rollout is not None
    assert rollout.reward_scope == controller.config.reward_scope
    assert rollout.team_reward_schema == controller.config.team_reward_schema
    assert rollout.joint_step_schema == controller.config.joint_step_schema
    assert len(rollout.transitions) == 1
    joint = rollout.transitions[0]
    assert isinstance(joint, JointTransition)
    assert joint.window_start_s == 5.0
    assert joint.window_end_s == 20.0
    assert len(joint.agent_transitions) == 2
    assert tuple(
        item.agent_index for item in joint.agent_transitions
    ) == (0, 1)
    assert tuple(
        item.reward for item in joint.agent_transitions
    ) == (joint.team_reward, joint.team_reward)
    diagnostics = rollout.reward_diagnostics
    assert diagnostics["transition_count"] == 1
    assert diagnostics["observed_seconds"] == pytest.approx(15.0)
    assert diagnostics["reward"]["mean"] == pytest.approx(
        joint.team_reward
    )
    assert diagnostics["raw_reward"]["mean"] == pytest.approx(
        joint.team_raw_reward
    )
    assert set(diagnostics["intersections"]) == {"demo_1", "demo_2"}
    assert diagnostics["intersections"]["demo_1"][
        "transition_count"
    ] == 1
    assert diagnostics["intersections"]["demo_2"][
        "transition_count"
    ] == 1
    assert diagnostics["intersections"]["demo_1"][
        "observed_seconds"
    ] == pytest.approx(15.0)
    assert diagnostics["intersections"]["demo_2"][
        "observed_seconds"
    ] == pytest.approx(15.0)


@pytest.mark.parametrize(
    ("final_time", "terminated", "truncated"),
    ((10.0, True, False), (120.0, False, True)),
)
def test_cooperative_finish_broadcasts_uniform_done_flags(
    final_time: float,
    terminated: bool,
    truncated: bool,
    monkeypatch,
) -> None:
    controller = _cooperative_controller()
    bootstrap = (7.5, 7.5)
    monkeypatch.setattr(
        controller, "_joint_values", lambda _global_state: bootstrap
    )
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage="YELLOW",
            second_stage="YELLOW",
            first_pending_phase=1,
            second_pending_phase=1,
        )
    )
    if truncated:
        controller.step(
            _cooperative_step(
                final_time,
                first_stage="YELLOW",
                second_stage="YELLOW",
                first_pending_phase=1,
                second_pending_phase=1,
            )
        )

    rollout = controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": final_time,
        }
    )

    assert rollout is not None
    joint = rollout.transitions[0]
    assert joint.window_end_s == final_time
    assert joint.terminated is terminated
    assert joint.truncated is truncated
    assert tuple(
        item.terminated for item in joint.agent_transitions
    ) == (terminated, terminated)
    assert tuple(
        item.truncated for item in joint.agent_transitions
    ) == (truncated, truncated)
    expected_next_values = (0.0, 0.0) if terminated else bootstrap
    assert joint.next_values == expected_next_values
    assert joint.next_team_value == expected_next_values[0]
    assert tuple(
        item.next_value for item in joint.agent_transitions
    ) == expected_next_values


def test_short_natural_terminal_joint_builds_valid_team_gae_batch() -> None:
    controller = _cooperative_controller()
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage="YELLOW",
            second_stage="YELLOW",
            first_pending_phase=1,
            second_pending_phase=1,
        )
    )

    rollout = controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 10.0,
        }
    )

    assert rollout is not None
    joint = rollout.transitions[0]
    assert joint.window_start_s == 5.0
    assert joint.window_end_s == 10.0
    assert joint.terminated is True
    assert joint.truncated is False
    assert joint.next_values == (0.0, 0.0)

    batch = build_ppo_batch((rollout,), config=controller.config)

    assert batch.batch_size == 2
    assert batch.agent_index.tolist() == [0, 1]
    assert batch.joint_step_index.tolist() == [0, 0]
    expected_advantage = joint.team_reward - joint.team_value
    assert batch.advantages.tolist() == pytest.approx(
        [expected_advantage, expected_advantage]
    )
    assert batch.returns.tolist() == pytest.approx(
        [joint.team_reward, joint.team_reward]
    )


def test_cooperative_unobserved_final_joint_discards_completed_prefix() -> None:
    controller = _cooperative_controller()
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage_elapsed=10.0,
            second_stage_elapsed=10.0,
        )
    )
    controller.step(
        _cooperative_step(
            20.0,
            first_stage_elapsed=20.0,
            second_stage_elapsed=20.0,
        )
    )
    assert len(controller._joint_transitions) == 1

    assert controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    ) is None


def test_cooperative_partially_applied_final_joint_discards_completed_prefix() -> None:
    controller = _cooperative_controller()
    controller.step(_cooperative_step(5.0))
    controller.step(
        _cooperative_step(
            10.0,
            first_stage_elapsed=10.0,
            second_stage_elapsed=10.0,
        )
    )
    second = controller.step(
        _cooperative_step(
            20.0,
            first_stage_elapsed=61.0,
            second_stage_elapsed=61.0,
        )
    )
    assert second["actions"]["signals"] == {
        "demo_1": {"target_phase": 1},
        "demo_2": {"target_phase": 1},
    }
    assert len(controller._joint_transitions) == 1
    controller.step(
        _cooperative_step(
            25.0,
            first_phase=1,
            second_phase=0,
            second_stage="YELLOW",
            second_pending_phase=1,
        )
    )

    assert controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 25.0,
        }
    ) is None


def test_requested_action_commits_only_after_target_green_is_observed() -> None:
    controller = _controller()

    first = controller.step(
        _step(5.0, current_phase=0, stage_elapsed=61.0)
    )
    clearance = controller.step(
        _step(
            10.0,
            current_phase=0,
            stage="YELLOW",
            stage_elapsed=5.0,
            pending_phase=1,
            waiting=95.0,
        )
    )
    next_decision = controller.step(
        _step(20.0, current_phase=1, stage_elapsed=5.0, waiting=90.0)
    )

    assert first["actions"]["signals"]["demo_1"] == {"target_phase": 1}
    assert clearance["actions"]["signals"] == {}
    assert "demo_1" in next_decision["actions"]["signals"]
    assert len(controller._joint_transitions) == 1
    joint = controller._joint_transitions[0]
    transition = joint.agent_transitions[0]
    assert transition.requested_phase == 1
    assert transition.applied_phase == 1
    assert transition.applied_time_s == 20.0
    assert transition.decision_time_s == 5.0
    assert transition.policy_generation == 3
    assert joint.global_state.observations.shape == (1, 132)


def test_unapplied_request_at_next_eligible_decision_invalidates_worker() -> None:
    controller = _controller()
    controller.step(_step(5.0, current_phase=0, stage_elapsed=61.0))

    with pytest.raises(
        ActionAlignmentError, match=r"requested phase 1.*applied phase 0"
    ):
        controller.step(_step(20.0, current_phase=0, stage_elapsed=5.0))

    assert controller.invalid is True


def test_time_limit_completion_bootstraps_confirmed_partial_transition() -> None:
    controller = _controller()
    controller.step(_step(5.0, current_phase=0, stage_elapsed=5.0))
    controller.step(
        _step(
            10.0,
            current_phase=0,
            stage="YELLOW",
            stage_elapsed=5.0,
            pending_phase=1,
            waiting=90.0,
        )
    )
    controller.step(
        _step(
            120.0,
            current_phase=0,
            stage="YELLOW",
            stage_elapsed=5.0,
            pending_phase=1,
            waiting=90.0,
        )
    )

    rollout = controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    )

    assert rollout is not None
    assert rollout.status == "ok"
    assert rollout.pending_count == 0
    assert len(rollout.transitions) == 1
    assert rollout.action_diagnostics["intersections"]["demo_1"]["decision_count"] == 1
    assert rollout.reward_diagnostics["transition_count"] == 1
    assert rollout.reward_diagnostics["components"]["D"]["mean"] >= 0.0
    joint = rollout.transitions[0]
    assert joint.terminated is False
    assert joint.truncated is True
    assert np.isfinite(joint.next_team_value)
    assert tuple(item.next_value for item in joint.agent_transitions) == (
        joint.next_team_value,
    )


def test_early_natural_completion_is_terminal_without_bootstrap() -> None:
    controller = _controller()
    controller.step(_step(5.0, current_phase=0, stage_elapsed=5.0))
    controller.step(
        _step(10.0, current_phase=0, stage_elapsed=10.0, waiting=90.0)
    )

    rollout = controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 10.0,
        }
    )

    assert rollout is not None
    joint = rollout.transitions[0]
    assert joint.terminated is True
    assert joint.truncated is False
    assert joint.next_team_value == 0.0
    assert tuple(item.next_value for item in joint.agent_transitions) == (0.0,)


def test_unobserved_final_pending_invalidates_whole_worker() -> None:
    controller = _controller()
    controller.step(_step(5.0, current_phase=0, stage_elapsed=5.0))
    controller.step(
        _step(10.0, current_phase=0, stage_elapsed=10.0, waiting=90.0)
    )
    controller.step(
        _step(20.0, current_phase=0, stage_elapsed=61.0, waiting=80.0)
    )

    assert controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    ) is None
    assert controller.invalid is True
    assert "incomplete final joint transition" in (controller.invalid_reason or "")


def test_error_finish_discards_complete_rollout() -> None:
    controller = _controller()
    controller.step(_step(5.0, current_phase=0, stage_elapsed=5.0))
    controller.step(
        _step(10.0, current_phase=0, stage_elapsed=10.0, waiting=90.0)
    )

    assert controller.finish(
        {"episode_id": "protocol-test", "reason": "error"}
    ) is None
    assert controller.trajectories == {"demo_1": ()}


def test_missing_controlled_intersection_invalidates_worker() -> None:
    controller = _controller()
    payload = _step(5.0, current_phase=0)
    payload["intersections"] = {}

    with pytest.raises(ValueError, match="missing controlled intersections"):
        controller.step(payload)

    assert controller.invalid is True


def test_model_inference_does_not_build_global_state_or_call_critic(
    monkeypatch,
) -> None:
    config = MAPPOConfig(("demo_1",))
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=20,
        critic_scope="global",
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
    )
    controller = MAPPOController(
        metadata=_metadata(),
        config=config,
        policy=policy,
        mode="model",
        policy_generation=4,
        expected_duration_s=120.0,
        record_evaluation=False,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("model inference must not use centralized Critic state")

    monkeypatch.setattr(controller._central_builder, "build", forbidden)
    monkeypatch.setattr(policy, "value", forbidden)
    monkeypatch.setattr(
        controller, "_vehicle_interval_statistics", forbidden
    )
    monkeypatch.setattr(controller, "_new_reward", forbidden)
    monkeypatch.setattr(
        controller._rollouts["demo_1"], "begin", forbidden
    )

    response = controller.step(
        _step(5.0, current_phase=0, stage_elapsed=5.0)
    )

    assert response["actions"]["signals"]["demo_1"]["target_phase"] in {
        0,
        1,
    }
    assert controller.finish(
        {
            "episode_id": "protocol-test",
            "reason": "completed",
            "simulation_time": 120.0,
        }
    ) is None
