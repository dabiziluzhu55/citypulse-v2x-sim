from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import random

import numpy as np
import pytest
import torch

from algorithms.mappo.checkpoint import (
    CheckpointCompatibilityError,
    CheckpointMetadata,
    _restore_rng_state,
    load_checkpoint,
    policy_digest,
    read_checkpoint_metadata,
    save_checkpoint,
)
from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
    MAPPO_V2_RESIDUAL_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_LOCAL,
    REWARD_SCOPE_SHARED_TEAM,
    algorithm_label,
)
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.trainer import MAPPOTrainer


LOCAL_SCHEMA = "ippo_v8_local_obs_v1"
REWARD_DEFINITION = (
    "pressure_v5a:delta_pressure-queue-wait-switch-local_blocking"
)
NEW_OBJECTIVE_FIELDS = (
    "algorithm_variant",
    "reward_scope",
    "team_reward_schema",
    "reward_aggregation",
    "reward_clip_stage",
    "critic_target_scope",
    "joint_step_schema",
)


def _objects(
    config: MAPPOConfig | None = None,
) -> tuple[MAPPOConfig, MAPPOPolicy, MAPPOTrainer]:
    current = config or MAPPOConfig(("demo_1", "demo_2"))
    policy = MAPPOPolicy(
        obs_dim=current.obs_dim,
        num_agents=len(current.intersection_ids),
        critic_scope=current.critic_scope,
        actor_init_seed=123,
        critic_init_seed=456,
        hidden_dim=8,
        phase_feature_dim=current.phase_feature_dim,
        model_version=current.model_version,
        actor_variant=current.actor_variant,
        residual_hidden_dim=current.residual_hidden_dim,
        identity_offset=current.identity_offset,
        residual_init_seed=342,
    )
    return current, policy, MAPPOTrainer(policy, current)


def _metadata(config: MAPPOConfig) -> CheckpointMetadata:
    return CheckpointMetadata.from_config(
        config,
        episode=8,
        policy_generation=1,
        actor_init_seed=123,
        critic_init_seed=456,
        training_seed_start=93001,
        training_seed_end=93008,
        training_periods=("off_peak",),
        local_observation_schema=LOCAL_SCHEMA,
        reward_definition=REWARD_DEFINITION,
        training_workers=4,
        episode_duration_s=300.0,
        residual_init_seed=(342 if config.actor_variant == "residual" else None),
    )


def _cooperative_config(
    *,
    critic_scope: str = "global",
    model_version: str = COOPERATIVE_MODEL_VERSION,
) -> MAPPOConfig:
    return MAPPOConfig(
        ("demo_1", "demo_2"),
        critic_scope=critic_scope,
        model_version=model_version,
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
    )


def _training_state_snapshot(
    policy: MAPPOPolicy, trainer: MAPPOTrainer
) -> tuple[dict, dict, dict, object, tuple, torch.Tensor]:
    return (
        {
            name: tensor.detach().clone()
            for name, tensor in policy.state_dict().items()
        },
        copy.deepcopy(trainer.actor_optimizer.state_dict()),
        copy.deepcopy(trainer.critic_optimizer.state_dict()),
        random.getstate(),
        copy.deepcopy(np.random.get_state()),
        torch.get_rng_state().clone(),
    )


def _assert_training_state_unchanged(
    before: tuple[dict, dict, dict, object, tuple, torch.Tensor],
    policy: MAPPOPolicy,
    trainer: MAPPOTrainer,
) -> None:
    (
        expected_policy,
        expected_actor_optimizer,
        expected_critic_optimizer,
        expected_python_rng,
        expected_numpy_rng,
        expected_torch_rng,
    ) = before
    _assert_nested_equal(expected_policy, policy.state_dict())
    _assert_nested_equal(
        expected_actor_optimizer, trainer.actor_optimizer.state_dict()
    )
    _assert_nested_equal(
        expected_critic_optimizer, trainer.critic_optimizer.state_dict()
    )
    assert random.getstate() == expected_python_rng
    actual_numpy_rng = np.random.get_state()
    assert actual_numpy_rng[0] == expected_numpy_rng[0]
    np.testing.assert_array_equal(actual_numpy_rng[1], expected_numpy_rng[1])
    assert actual_numpy_rng[2:] == expected_numpy_rng[2:]
    assert torch.equal(torch.get_rng_state(), expected_torch_rng)


def _rewrite_as_format_v1(source: Path, destination: Path) -> None:
    payload = torch.load(source, map_location="cpu", weights_only=False)
    payload["checkpoint_format_version"] = 1
    for field in NEW_OBJECTIVE_FIELDS:
        payload["metadata"].pop(field, None)
    for optional_field in (
        "training_workers",
        "episode_duration_s",
        "actor_variant",
        "residual_hidden_dim",
        "identity_offset",
        "residual_init_seed",
    ):
        payload["metadata"].pop(optional_field, None)
    torch.save(payload, destination)


def _take_optimizer_step(trainer: MAPPOTrainer) -> None:
    trainer.actor_optimizer.zero_grad(set_to_none=True)
    actor_loss = sum(
        parameter.square().sum() for parameter in trainer.policy.actor.parameters()
    )
    actor_loss.backward()
    trainer.actor_optimizer.step()
    trainer.critic_optimizer.zero_grad(set_to_none=True)
    critic_loss = sum(
        parameter.square().sum() for parameter in trainer.policy.critic.parameters()
    )
    critic_loss.backward()
    trainer.critic_optimizer.step()


def _assert_nested_equal(expected, actual) -> None:
    if isinstance(expected, torch.Tensor):
        torch.testing.assert_close(expected, actual, rtol=0, atol=0)
    elif isinstance(expected, dict):
        assert expected.keys() == actual.keys()
        for key in expected:
            _assert_nested_equal(expected[key], actual[key])
    elif isinstance(expected, (list, tuple)):
        assert len(expected) == len(actual)
        for expected_value, actual_value in zip(expected, actual):
            _assert_nested_equal(expected_value, actual_value)
    else:
        assert expected == actual


def test_cuda_rng_restore_initializes_cuda_before_setting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    state = {
        "python": random.getstate(),
        "numpy": np.random.get_state(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": [torch.zeros(16, dtype=torch.uint8)],
    }
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "init", lambda: calls.append("init"))
    monkeypatch.setattr(
        torch.cuda,
        "set_rng_state_all",
        lambda _state: calls.append("set_rng_state_all"),
    )

    _restore_rng_state(state)

    assert calls == ["init", "set_rng_state_all"]


def test_checkpoint_round_trip_restores_complete_training_state(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    _take_optimizer_step(trainer)
    random.seed(7)
    np.random.seed(8)
    torch.manual_seed(9)
    path = tmp_path / "mappo.pt"

    save_checkpoint(path, policy, trainer, _metadata(config))

    expected_model = {
        name: value.detach().clone() for name, value in policy.state_dict().items()
    }
    expected_actor_optimizer = copy.deepcopy(
        trainer.actor_optimizer.state_dict()
    )
    expected_critic_optimizer = copy.deepcopy(
        trainer.critic_optimizer.state_dict()
    )
    expected_random = random.random()
    expected_numpy = float(np.random.random())
    expected_torch = torch.rand(3)
    with torch.no_grad():
        for parameter in policy.parameters():
            parameter.add_(100.0)
    trainer.actor_optimizer.state.clear()
    trainer.critic_optimizer.state.clear()
    random.seed(700)
    np.random.seed(800)
    torch.manual_seed(900)

    loaded = load_checkpoint(
        path,
        policy,
        trainer,
        expected_config=config,
        expected_local_observation_schema=LOCAL_SCHEMA,
    )

    assert loaded == _metadata(config)
    for name, value in policy.state_dict().items():
        torch.testing.assert_close(value, expected_model[name], rtol=0, atol=0)
    _assert_nested_equal(
        expected_actor_optimizer, trainer.actor_optimizer.state_dict()
    )
    _assert_nested_equal(
        expected_critic_optimizer, trainer.critic_optimizer.state_dict()
    )
    assert random.random() == expected_random
    assert float(np.random.random()) == expected_numpy
    torch.testing.assert_close(torch.rand(3), expected_torch, rtol=0, atol=0)
    assert path.exists()
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_policy_digest_is_stable_and_changes_with_policy() -> None:
    _, policy, _ = _objects()

    first = policy_digest(policy)
    second = policy_digest(policy)
    with torch.no_grad():
        next(policy.parameters()).add_(1.0)

    assert first == second
    assert policy_digest(policy) != first
    assert len(first) == 64


def test_checkpoint_metadata_can_be_inspected_without_runtime_objects(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))

    assert read_checkpoint_metadata(path) == _metadata(config)


def test_checkpoint_metadata_rejects_reversed_training_seed_range(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    broken_path = tmp_path / "broken.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["metadata"]["training_seed_start"] = 94000
    payload["metadata"]["training_seed_end"] = 93000
    torch.save(payload, broken_path)

    with pytest.raises(
        CheckpointCompatibilityError, match="training seed range"
    ):
        read_checkpoint_metadata(broken_path)


def test_checkpoint_metadata_loads_legacy_worker_duration_as_na(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    legacy_path = tmp_path / "legacy.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["metadata"].pop("training_workers")
    payload["metadata"].pop("episode_duration_s")
    payload["metadata"].pop("actor_variant")
    payload["metadata"].pop("residual_hidden_dim")
    payload["metadata"].pop("identity_offset")
    payload["metadata"].pop("residual_init_seed")
    torch.save(payload, legacy_path)

    metadata = read_checkpoint_metadata(legacy_path)

    assert metadata.training_workers is None
    assert metadata.episode_duration_s is None
    assert metadata.actor_variant is None
    assert metadata.residual_hidden_dim is None
    assert metadata.identity_offset is None
    assert metadata.residual_init_seed is None


def test_v2_checkpoint_round_trip_records_residual_provenance(
    tmp_path: Path,
) -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=MAPPO_V2_RESIDUAL_MODEL_VERSION,
        actor_variant="residual",
    )
    _, policy, trainer = _objects(config)
    _take_optimizer_step(trainer)
    path = tmp_path / "mappo-v2-residual.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    _, restored_policy, restored_trainer = _objects(config)

    restored = load_checkpoint(
        path,
        restored_policy,
        restored_trainer,
        expected_config=config,
        expected_local_observation_schema=LOCAL_SCHEMA,
        expected_residual_init_seed=342,
    )

    assert restored.actor_variant == "residual"
    assert restored.residual_hidden_dim == 32
    assert restored.identity_offset == 9
    assert restored.residual_init_seed == 342
    for name, tensor in policy.state_dict().items():
        assert torch.equal(tensor, restored_policy.state_dict()[name])
    _assert_nested_equal(
        trainer.actor_optimizer.state_dict(),
        restored_trainer.actor_optimizer.state_dict(),
    )
    _assert_nested_equal(
        trainer.critic_optimizer.state_dict(),
        restored_trainer.critic_optimizer.state_dict(),
    )


def test_v2_checkpoint_rejects_residual_seed_before_state_load(
    tmp_path: Path,
) -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=MAPPO_V2_RESIDUAL_MODEL_VERSION,
        actor_variant="residual",
    )
    _, policy, trainer = _objects(config)
    path = tmp_path / "mappo-v2-residual.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    before = {
        name: tensor.detach().clone()
        for name, tensor in policy.state_dict().items()
    }

    with pytest.raises(
        CheckpointCompatibilityError, match="residual initialization seed"
    ):
        load_checkpoint(
            path,
            policy,
            trainer,
            expected_config=config,
            expected_local_observation_schema=LOCAL_SCHEMA,
            expected_residual_init_seed=999,
        )

    for name, tensor in policy.state_dict().items():
        assert torch.equal(tensor, before[name])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("actor_variant", "shared", "model version.*actor variant"),
        ("residual_hidden_dim", 16, "residual hidden dimension"),
        ("identity_offset", 8, "identity offset"),
    ],
)
def test_v2_checkpoint_rejects_malformed_variant_metadata(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        model_version=MAPPO_V2_RESIDUAL_MODEL_VERSION,
        actor_variant="residual",
    )
    _, policy, trainer = _objects(config)
    path = tmp_path / "mappo-v2-residual.pt"
    broken_path = tmp_path / f"broken-{field}.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["metadata"][field] = value
    torch.save(payload, broken_path)

    with pytest.raises(CheckpointCompatibilityError, match=message):
        read_checkpoint_metadata(broken_path)


@pytest.mark.parametrize(
    ("changed_config", "message"),
    [
        (MAPPOConfig(("demo_2", "demo_1")), "intersection order"),
        (
            MAPPOConfig(
                ("demo_1", "demo_2"),
                centralized_state_schema="wrong-schema",
            ),
            "centralized state schema",
        ),
        (
            MAPPOConfig(("demo_1", "demo_2"), action_interval_s=30.0),
            "action interval",
        ),
    ],
)
def test_checkpoint_rejects_incompatible_runtime_before_loading(
    tmp_path: Path, changed_config: MAPPOConfig, message: str
) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    _, changed_policy, changed_trainer = _objects(changed_config)

    with pytest.raises(CheckpointCompatibilityError, match=message):
        load_checkpoint(
            path,
            changed_policy,
            changed_trainer,
            expected_config=changed_config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )


def test_checkpoint_rejects_missing_policy_generation(tmp_path: Path) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    broken_path = tmp_path / "broken.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    payload = torch.load(path, map_location="cpu", weights_only=False)
    payload["metadata"].pop("policy_generation")
    torch.save(payload, broken_path)

    with pytest.raises(
        CheckpointCompatibilityError, match="missing metadata.*policy_generation"
    ):
        load_checkpoint(
            broken_path,
            policy,
            trainer,
            expected_config=config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )


def test_checkpoint_rejects_reward_definition_before_loading_state(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    path = tmp_path / "mappo.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    before = {
        name: value.detach().clone()
        for name, value in policy.state_dict().items()
    }

    with pytest.raises(CheckpointCompatibilityError, match="reward definition"):
        load_checkpoint(
            path,
            policy,
            trainer,
            expected_config=config,
            expected_local_observation_schema=LOCAL_SCHEMA,
            expected_reward_definition="different-reward",
        )

    for name, value in policy.state_dict().items():
        torch.testing.assert_close(value, before[name], rtol=0, atol=0)


def test_cooperative_checkpoint_round_trip_records_full_objective_provenance(
    tmp_path: Path,
) -> None:
    config = _cooperative_config()
    _, policy, trainer = _objects(config)
    _take_optimizer_step(trainer)
    path = tmp_path / "cooperative-mappo.pt"

    save_checkpoint(path, policy, trainer, _metadata(config))
    payload = torch.load(path, map_location="cpu", weights_only=False)
    metadata = read_checkpoint_metadata(path)
    _, restored_policy, restored_trainer = _objects(config)
    restored = load_checkpoint(
        path,
        restored_policy,
        restored_trainer,
        expected_config=config,
        expected_local_observation_schema=LOCAL_SCHEMA,
        expected_reward_definition=REWARD_DEFINITION,
    )

    assert payload["checkpoint_format_version"] == 2
    assert metadata == restored
    assert metadata.algorithm_variant == "cooperative_mappo"
    assert metadata.algorithm_variant == algorithm_label(config)
    assert metadata.reward_scope == REWARD_SCOPE_SHARED_TEAM
    assert metadata.team_reward_schema == config.team_reward_schema
    assert metadata.reward_aggregation == "mean_raw_then_clip"
    assert metadata.reward_clip_stage == "after_team_aggregation"
    assert metadata.critic_target_scope == "team_return"
    assert metadata.joint_step_schema == config.joint_step_schema
    for name, tensor in policy.state_dict().items():
        assert torch.equal(tensor, restored_policy.state_dict()[name])
    _assert_nested_equal(
        trainer.actor_optimizer.state_dict(),
        restored_trainer.actor_optimizer.state_dict(),
    )
    _assert_nested_equal(
        trainer.critic_optimizer.state_dict(),
        restored_trainer.critic_optimizer.state_dict(),
    )


def test_owner_conditioned_cooperative_checkpoint_round_trip(
    tmp_path: Path,
) -> None:
    config = _cooperative_config(
        model_version=COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION
    )
    _, policy, trainer = _objects(config)
    _take_optimizer_step(trainer)
    path = tmp_path / "cooperative-owner-conditioned.pt"
    save_checkpoint(path, policy, trainer, _metadata(config))
    _, restored_policy, restored_trainer = _objects(config)

    metadata = load_checkpoint(
        path,
        restored_policy,
        restored_trainer,
        expected_config=config,
        expected_local_observation_schema=LOCAL_SCHEMA,
    )

    assert metadata.model_version == COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION
    for name, tensor in policy.state_dict().items():
        assert torch.equal(tensor, restored_policy.state_dict()[name])
    _assert_nested_equal(
        trainer.actor_optimizer.state_dict(),
        restored_trainer.actor_optimizer.state_dict(),
    )
    _assert_nested_equal(
        trainer.critic_optimizer.state_dict(),
        restored_trainer.critic_optimizer.state_dict(),
    )


@pytest.mark.parametrize(
    ("saved_version", "target_version"),
    [
        (
            COOPERATIVE_MODEL_VERSION,
            COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
        ),
        (
            COOPERATIVE_OWNER_CONDITIONED_MODEL_VERSION,
            COOPERATIVE_MODEL_VERSION,
        ),
    ],
)
def test_cooperative_model_versions_cannot_cross_load_before_mutation(
    tmp_path: Path, saved_version: str, target_version: str
) -> None:
    saved_config = _cooperative_config(model_version=saved_version)
    _, saved_policy, saved_trainer = _objects(saved_config)
    source = tmp_path / f"{saved_version}.pt"
    save_checkpoint(
        source, saved_policy, saved_trainer, _metadata(saved_config)
    )
    target_config = _cooperative_config(model_version=target_version)
    _, target_policy, target_trainer = _objects(target_config)
    _take_optimizer_step(target_trainer)
    before = _training_state_snapshot(target_policy, target_trainer)

    with pytest.raises(CheckpointCompatibilityError, match="model version"):
        load_checkpoint(
            source,
            target_policy,
            target_trainer,
            expected_config=target_config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )

    _assert_training_state_unchanged(
        before, target_policy, target_trainer
    )


@pytest.mark.parametrize(
    ("changed_config", "message"),
    [
        (
            MAPPOConfig(("demo_1", "demo_2")),
            "algorithm variant|reward scope|critic target|model version|legacy",
        ),
        (
            replace(
                _cooperative_config(),
                joint_step_schema="different-joint-schema",
            ),
            "joint step schema",
        ),
    ],
)
def test_cooperative_objective_mismatch_fails_before_any_state_mutation(
    tmp_path: Path, changed_config: MAPPOConfig, message: str
) -> None:
    saved_config = _cooperative_config()
    _, saved_policy, saved_trainer = _objects(saved_config)
    source = tmp_path / "cooperative-mappo.pt"
    save_checkpoint(
        source, saved_policy, saved_trainer, _metadata(saved_config)
    )
    _, target_policy, target_trainer = _objects(changed_config)
    _take_optimizer_step(target_trainer)
    with torch.no_grad():
        next(target_policy.parameters()).add_(17.0)
    random.seed(1701)
    np.random.seed(1702)
    torch.manual_seed(1703)
    before = _training_state_snapshot(target_policy, target_trainer)

    with pytest.raises(CheckpointCompatibilityError, match=message):
        load_checkpoint(
            source,
            target_policy,
            target_trainer,
            expected_config=changed_config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )

    _assert_training_state_unchanged(
        before, target_policy, target_trainer
    )


def test_resume_preflight_metadata_change_fails_before_state_mutation(
    tmp_path: Path,
) -> None:
    config = _cooperative_config()
    _, saved_policy, saved_trainer = _objects(config)
    source = tmp_path / "cooperative-mappo.pt"
    save_checkpoint(source, saved_policy, saved_trainer, _metadata(config))
    stale_preflight = replace(read_checkpoint_metadata(source), episode=9)

    _, target_policy, target_trainer = _objects(config)
    _take_optimizer_step(target_trainer)
    with torch.no_grad():
        next(target_policy.parameters()).add_(18.0)
    random.seed(1801)
    np.random.seed(1802)
    torch.manual_seed(1803)
    before = _training_state_snapshot(target_policy, target_trainer)

    with pytest.raises(
        CheckpointCompatibilityError, match="changed after resume preflight"
    ):
        load_checkpoint(
            source,
            target_policy,
            target_trainer,
            expected_config=config,
            expected_local_observation_schema=LOCAL_SCHEMA,
            expected_metadata=stale_preflight,
        )

    _assert_training_state_unchanged(
        before, target_policy, target_trainer
    )


@pytest.mark.parametrize(
    ("field", "wrong_value", "message"),
    [
        ("algorithm_variant", "not-cooperative", "algorithm variant"),
        ("reward_scope", REWARD_SCOPE_LOCAL, "reward scope"),
        ("team_reward_schema", "wrong-team-schema", "team reward schema"),
        ("reward_aggregation", "wrong-aggregation", "reward aggregation"),
        ("reward_clip_stage", "wrong-clip-stage", "reward clip stage"),
        ("critic_target_scope", "local_return", "critic target scope"),
        ("joint_step_schema", "wrong-joint-schema", "joint step schema"),
    ],
)
def test_each_objective_field_is_validated_before_training_state_load(
    tmp_path: Path, field: str, wrong_value: str, message: str
) -> None:
    config = _cooperative_config()
    _, saved_policy, saved_trainer = _objects(config)
    source = tmp_path / "cooperative.pt"
    broken = tmp_path / f"broken-{field}.pt"
    save_checkpoint(source, saved_policy, saved_trainer, _metadata(config))
    payload = torch.load(source, map_location="cpu", weights_only=False)
    payload["metadata"][field] = wrong_value
    torch.save(payload, broken)

    _, target_policy, target_trainer = _objects(config)
    _take_optimizer_step(target_trainer)
    with torch.no_grad():
        next(target_policy.parameters()).add_(19.0)
    random.seed(1901)
    np.random.seed(1902)
    torch.manual_seed(1903)
    before = _training_state_snapshot(target_policy, target_trainer)

    with pytest.raises(CheckpointCompatibilityError, match=message):
        load_checkpoint(
            broken,
            target_policy,
            target_trainer,
            expected_config=config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )

    _assert_training_state_unchanged(
        before, target_policy, target_trainer
    )


def test_format_v1_metadata_is_read_only_legacy_cc_ippo(
    tmp_path: Path,
) -> None:
    legacy_config, policy, trainer = _objects()
    current_path = tmp_path / "current.pt"
    legacy_path = tmp_path / "legacy-v1.pt"
    save_checkpoint(current_path, policy, trainer, _metadata(legacy_config))
    _rewrite_as_format_v1(current_path, legacy_path)
    original_bytes = legacy_path.read_bytes()

    metadata = read_checkpoint_metadata(legacy_path)

    assert metadata.algorithm_variant == "cc_ippo_local_reward_v1"
    assert metadata.reward_scope == REWARD_SCOPE_LOCAL
    assert metadata.critic_target_scope == "local_return"
    assert metadata.training_workers is None
    assert metadata.episode_duration_s is None
    assert metadata.actor_variant is None
    assert metadata.residual_hidden_dim is None
    assert metadata.identity_offset is None
    assert metadata.residual_init_seed is None
    assert legacy_path.read_bytes() == original_bytes


def test_format_v1_fully_restores_legacy_policy_optimizers_rng_and_metadata(
    tmp_path: Path,
) -> None:
    config, policy, trainer = _objects()
    _take_optimizer_step(trainer)
    random.seed(71)
    np.random.seed(72)
    torch.manual_seed(73)
    current_path = tmp_path / "current.pt"
    legacy_path = tmp_path / "legacy-v1.pt"
    save_checkpoint(current_path, policy, trainer, _metadata(config))
    _rewrite_as_format_v1(current_path, legacy_path)

    expected_policy = {
        name: tensor.detach().clone()
        for name, tensor in policy.state_dict().items()
    }
    expected_actor_optimizer = copy.deepcopy(
        trainer.actor_optimizer.state_dict()
    )
    expected_critic_optimizer = copy.deepcopy(
        trainer.critic_optimizer.state_dict()
    )
    expected_python = random.random()
    expected_numpy = float(np.random.random())
    expected_torch = torch.rand(3)

    _, restored_policy, restored_trainer = _objects(config)
    with torch.no_grad():
        for parameter in restored_policy.parameters():
            parameter.add_(101.0)
    _take_optimizer_step(restored_trainer)
    random.seed(710)
    np.random.seed(720)
    torch.manual_seed(730)

    metadata = load_checkpoint(
        legacy_path,
        restored_policy,
        restored_trainer,
        expected_config=config,
        expected_local_observation_schema=LOCAL_SCHEMA,
    )

    assert metadata.algorithm_variant == "cc_ippo_local_reward_v1"
    assert metadata.reward_scope == REWARD_SCOPE_LOCAL
    assert metadata.critic_target_scope == "local_return"
    _assert_nested_equal(expected_policy, restored_policy.state_dict())
    _assert_nested_equal(
        expected_actor_optimizer,
        restored_trainer.actor_optimizer.state_dict(),
    )
    _assert_nested_equal(
        expected_critic_optimizer,
        restored_trainer.critic_optimizer.state_dict(),
    )
    assert random.random() == expected_python
    assert float(np.random.random()) == expected_numpy
    torch.testing.assert_close(
        torch.rand(3), expected_torch, rtol=0, atol=0
    )


def test_vanilla_cooperative_resume_rejects_format_v1_critic_and_optimizers(
    tmp_path: Path,
) -> None:
    legacy_config, legacy_policy, legacy_trainer = _objects()
    current_path = tmp_path / "current.pt"
    legacy_path = tmp_path / "legacy-v1.pt"
    save_checkpoint(
        current_path,
        legacy_policy,
        legacy_trainer,
        _metadata(legacy_config),
    )
    _rewrite_as_format_v1(current_path, legacy_path)

    cooperative_config = _cooperative_config()
    _, cooperative_policy, cooperative_trainer = _objects(
        cooperative_config
    )
    _take_optimizer_step(cooperative_trainer)
    with torch.no_grad():
        next(cooperative_policy.parameters()).add_(23.0)
    before = _training_state_snapshot(
        cooperative_policy, cooperative_trainer
    )

    with pytest.raises(
        CheckpointCompatibilityError,
        match="legacy.*cooperative|cooperative.*legacy|vanilla.*resume",
    ):
        load_checkpoint(
            legacy_path,
            cooperative_policy,
            cooperative_trainer,
            expected_config=cooperative_config,
            expected_local_observation_schema=LOCAL_SCHEMA,
        )

    _assert_training_state_unchanged(
        before, cooperative_policy, cooperative_trainer
    )
