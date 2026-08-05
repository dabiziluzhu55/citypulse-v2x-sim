from __future__ import annotations

import pytest

from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    COOPERATIVE_M1_MODEL_VERSION,
    COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
    MAPPO_V1_MODEL_VERSION,
    MAPPO_V2_RESIDUAL_MODEL_VERSION,
    MAPPO_V2_SHARED_MODEL_VERSION,
    MAPPOConfig,
    algorithm_label,
    assert_seed_disjoint,
    configuration_signature,
)


def test_cooperative_value_sharing_is_model_semantic_not_critic_scope() -> None:
    old_global = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="global",
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )
    old_local = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="local",
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )
    owner_conditioned = MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope="global",
        model_version=COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )

    assert old_global.requires_shared_values is True
    assert old_local.requires_shared_values is False
    assert owner_conditioned.requires_shared_values is False


def test_owner_conditioned_cooperative_model_rejects_local_reward() -> None:
    with pytest.raises(ValueError, match="shared_team"):
        MAPPOConfig(
            ("demo_1", "demo_2"),
            model_version=COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
            reward_scope="local",
        )


def test_v2_config_pins_actor_and_residual_schema() -> None:
    shared = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=MAPPO_V2_SHARED_MODEL_VERSION,
        actor_variant="shared",
    )
    residual = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=MAPPO_V2_RESIDUAL_MODEL_VERSION,
        actor_variant="residual",
    )

    assert shared.residual_hidden_dim == residual.residual_hidden_dim == 32
    assert shared.identity_offset == residual.identity_offset == 9


@pytest.mark.parametrize(
    ("model_version", "actor_variant"),
    [
        (MAPPO_V1_MODEL_VERSION, "residual"),
        (MAPPO_V2_SHARED_MODEL_VERSION, "residual"),
        (MAPPO_V2_RESIDUAL_MODEL_VERSION, "shared"),
    ],
)
def test_config_rejects_model_actor_mismatch(
    model_version: str, actor_variant: str
) -> None:
    with pytest.raises(ValueError, match="model version.*actor variant"):
        MAPPOConfig(
            ("demo_1", "demo_2"),
            model_version=model_version,
            actor_variant=actor_variant,
        )


def test_config_freezes_ippo_v8_training_contract() -> None:
    config = MAPPOConfig(intersection_ids=("demo_1", "demo_2"))

    assert config.model_version == "mappo_v1"
    assert config.critic_scope == "global"
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


def test_shared_reward_scope_changes_configuration_signature() -> None:
    local = MAPPOConfig(("demo_1", "demo_2"), reward_scope="local")
    shared = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=COOPERATIVE_MODEL_VERSION,
        reward_scope="shared_team",
        critic_target_scope="team_return",
    )
    assert configuration_signature(local) != configuration_signature(shared)


@pytest.mark.parametrize(
    ("reward_scope", "critic_scope", "expected"),
    [
        ("local", "local", "ippo_local_reward"),
        ("local", "global", "cc_ippo_local_reward"),
        ("shared_team", "local", "cooperative_ippo"),
        ("shared_team", "global", "cooperative_mappo"),
    ],
)
def test_algorithm_label_is_derived_from_reward_and_critic_scope(
    reward_scope: str, critic_scope: str, expected: str
) -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=(
            COOPERATIVE_MODEL_VERSION
            if reward_scope == "shared_team"
            else MAPPO_V1_MODEL_VERSION
        ),
        reward_scope=reward_scope,
        critic_scope=critic_scope,
        critic_target_scope=(
            "team_return" if reward_scope == "shared_team" else "local_return"
        ),
    )
    assert algorithm_label(config) == expected


def test_shared_reward_requires_team_return_target() -> None:
    with pytest.raises(ValueError, match="shared_team requires team_return"):
        MAPPOConfig(
            ("demo_1",),
            reward_scope="shared_team",
            critic_target_scope="local_return",
        )


def test_local_reward_requires_local_return_target() -> None:
    with pytest.raises(ValueError, match="local requires local_return"):
        MAPPOConfig(
            ("demo_1",),
            reward_scope="local",
            critic_target_scope="team_return",
        )


def test_cooperative_m1_requires_agent_conditioned_critic():
    cfg = MAPPOConfig(
        intersection_ids=("demo_1", "demo_2"),
        model_version=COOPERATIVE_M1_MODEL_VERSION,
        reward_scope="shared_team",
        critic_scope="global",
        critic_target_scope="team_return",
        m1_target_mode="per_agent",
        m1_arm="m1_a",
        m1_local_weight=0.95,
        m1_neighbor_weight=0.0,
        m1_team_weight=0.05,
        m1_adjacency_path="adj.json",
    )
    assert not cfg.requires_shared_values


def test_cooperative_m1_rejects_invalid_target_mode():
    with pytest.raises(ValueError):
        MAPPOConfig(
            intersection_ids=("demo_1",),
            model_version=COOPERATIVE_M1_MODEL_VERSION,
            reward_scope="shared_team",
            critic_target_scope="team_return",
            m1_target_mode="unknown",
        )
