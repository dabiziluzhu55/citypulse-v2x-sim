from __future__ import annotations

import pytest

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    MAPPOConfig,
    algorithm_label,
    assert_seed_disjoint,
    configuration_signature,
)


def test_cooperative_value_sharing_is_model_semantic_not_critic_scope() -> None:
    global_critic = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="global",
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )
    local_critic = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="local",
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )

    assert global_critic.requires_shared_values is True
    assert local_critic.requires_shared_values is False


def test_config_rejects_model_actor_mismatch() -> None:
    with pytest.raises(ValueError, match="model version.*actor variant"):
        MAPPOConfig(
            ("demo_1", "demo_2"),
            model_version=COOPERATIVE_MODEL_VERSION,
            actor_variant="residual",
        )


def test_config_freezes_ippo_v8_training_contract() -> None:
    config = MAPPOConfig(intersection_ids=("demo_1", "demo_2"))

    assert config.model_version == "cooperative_joint_v1"
    assert config.critic_scope == "global"
    assert config.reward_scope == "shared_team"
    assert config.critic_target_scope == "team_return"
    assert config.obs_dim == 132
    assert config.phase_feature_dim == 11
    assert config.max_action_dim == 4
    assert config.hidden_dim == 128
    assert config.action_interval_s == 15.0
    assert config.max_green_factor == 2.0
    assert config.effective_demand_enabled is True
    assert config.actor_lr == 3e-4
    assert config.critic_lr == 1e-4
    assert config.gamma == 0.99
    assert config.gae_lambda == 0.95
    assert config.ppo_clip == 0.2
    assert config.entropy_coef == 0.01
    assert config.ppo_epochs == 4
    assert config.minibatch_size == 128
    assert config.max_grad_norm == 0.5
    assert config.huber_delta == 10.0
    assert config.centralized_state_schema == "centralized_local_obs_pool_v1"


@pytest.mark.parametrize("critic_scope", ["", "team", "mixed"])
def test_config_rejects_unknown_critic_scope(critic_scope: str) -> None:
    with pytest.raises(ValueError, match="critic scope"):
        MAPPOConfig(("demo_1",), critic_scope=critic_scope)


def test_seed_partition_rejects_training_evaluation_overlap() -> None:
    with pytest.raises(ValueError, match="overlap: 93002"):
        assert_seed_disjoint((93001, 93002), (63001, 93002))


def test_seed_partition_accepts_disjoint_sets() -> None:
    assert_seed_disjoint((93001, 93002), (63001, 63002))


def test_configuration_signature_is_stable_and_scope_sensitive() -> None:
    first = MAPPOConfig(("demo_1",), critic_scope="global")
    same = MAPPOConfig(("demo_1",), critic_scope="global")
    local = MAPPOConfig(("demo_1",), critic_scope="local")

    assert configuration_signature(first) == configuration_signature(same)
    assert configuration_signature(first) != configuration_signature(local)
    assert len(configuration_signature(first)) == 64


@pytest.mark.parametrize(
    ("critic_scope", "expected"),
    [
        ("local", "cooperative_ippo"),
        ("global", "cooperative_mappo"),
    ],
)
def test_algorithm_label_is_derived_from_critic_scope(
    critic_scope: str, expected: str
) -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_scope=critic_scope,
        critic_target_scope="team_return",
    )
    assert algorithm_label(config) == expected


def test_shared_reward_requires_team_return_target() -> None:
    with pytest.raises(ValueError, match="shared_team requires team_return"):
        MAPPOConfig(
            ("demo_1",),
            reward_scope="shared_team",
            critic_target_scope="local_return",
        )
