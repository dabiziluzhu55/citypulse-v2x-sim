from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest

import algorithms.mappo as entrypoint

from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.features import IPPO_V8_LOCAL_OBSERVATION_SCHEMA
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.test_protocol import _metadata, _step
from traffic_control.common.environment_contract import JOINT_PERIODS, build_environment_contract
from traffic_control.mappo.contract import build_mappo_policy_spec




def _live_metadata(period: str) -> dict:
    metadata = deepcopy(_metadata())
    metadata["protocol_version"] = "2.0"
    metadata["period"] = period
    metadata["episode_id"] = f"entrypoint-{period}"
    return metadata


def _joint_environment_contract(config: MAPPOConfig) -> dict:
    values = asdict(config)
    values["obs_dim"] = config.obs_dim
    values["local_observation_schema"] = IPPO_V8_LOCAL_OBSERVATION_SCHEMA
    return build_environment_contract(
        {
            period: _live_metadata(period)
            for period in JOINT_PERIODS
        },
        policy_spec=build_mappo_policy_spec(values),
    )


def _prepare_joint_collector() -> tuple[MAPPOConfig, dict]:
    config = MAPPOConfig(("demo_1",), hidden_dim=8)
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=20,
        critic_scope=config.critic_scope,
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        identity_offset=config.identity_offset,
    )
    contract = _joint_environment_contract(config)
    entrypoint.prepare_collector(
        policy_state=policy.state_dict(),
        config=config,
        policy_generation=7,
        rollout_seed=93001,
        actor_init_seed=123,
        critic_init_seed=456,
        expected_duration_s=120.0,
        mode="model",
        record_evaluation=True,
        environment_contract=contract,
    )
    return config, contract


def test_protocol_entrypoint_accepts_a_signed_v3_period() -> None:
    _prepare_joint_collector()

    ready = entrypoint.initialize(_live_metadata("off_peak"))

    assert ready["ready"] is True
    entrypoint.finish(
        {
            "episode_id": "entrypoint-off_peak",
            "reason": "completed",
            "simulation_time": 0.0,
        }
    )
    entrypoint.pop_collected_rollout()
    entrypoint.pop_collected_diagnostics()


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("unsupported", "not supported"),
        ("program", "green_seconds"),
    ),
)
def test_protocol_entrypoint_rejects_unsigned_or_drifted_program_before_use(
    mutation: str,
    message: str,
) -> None:
    _prepare_joint_collector()
    live = _live_metadata("off_peak")
    if mutation == "unsupported":
        live["period"] = "weekend"
    else:
        live["intersections"]["demo_1"]["phases"][0][
            "green_seconds"
        ] += 1.0

    with pytest.raises(ValueError, match=message):
        entrypoint.initialize(live)
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
