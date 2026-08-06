from __future__ import annotations

import json
import logging

import pytest

import algorithms.mappo.train as train_module
from algorithms.mappo.config import (
    COOPERATIVE_MODEL_VERSION,
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
    algorithm_label,
    assert_seed_disjoint,
    configuration_signature,
)
from algorithms.mappo.features import IPPO_V8_LOCAL_OBSERVATION_SCHEMA
from algorithms.mappo.train import (
    _build_training_config,
    _checkpoint_metadata,
    _default_checkpoint_path,
    _failed_worker_rollout,
    _periodic_checkpoint_path,
    _period_batch,
    _parse_training_args,
    _seed_batches,
    _should_save_periodic_checkpoint,
    _training_run_label,
    _training_seed_range,
    _write_training_diagnostics,
)


def test_seed_batches_cover_each_training_seed_exactly_once() -> None:
    assert tuple(
        _seed_batches(base_seed=93001, episodes=5, workers=4)
    ) == ((93001, 93002, 93003, 93004), (93005,))
    assert _period_batch(
        (93001, 93002, 93003),
        periods=("off_peak", "morning_peak"),
        training_seed_start=93001,
    ) == ("off_peak", "morning_peak", "off_peak")


def test_failed_worker_rollout_is_generation_and_schema_identified() -> None:
    config = MAPPOConfig(("demo_1",))
    rollout = _failed_worker_rollout(
        seed=93001,
        error="SUMO failed",
        policy_generation=4,
        expected_policy_digest="abc",
        config=config,
    )

    assert rollout.seed == 93001
    assert rollout.status == "error"
    assert rollout.policy_generation == 4
    assert rollout.policy_digest == "abc"
    assert rollout.config_signature == configuration_signature(config)
    assert (
        rollout.local_observation_schema
        == IPPO_V8_LOCAL_OBSERVATION_SCHEMA
    )
    assert rollout.centralized_state_schema == config.centralized_state_schema
    assert rollout.reward_scope == config.reward_scope
    assert rollout.team_reward_schema == config.team_reward_schema
    assert rollout.joint_step_schema == config.joint_step_schema
    assert rollout.transitions == ()
    assert rollout.error == "SUMO failed"


def test_resume_training_seeds_must_continue_after_saved_range() -> None:
    assert _training_seed_range(
        base_seed=93005,
        episodes=4,
        previous_start=93001,
        previous_end=93004,
    ) == (93001, 93008)

    with pytest.raises(ValueError, match="must start at 93005"):
        _training_seed_range(
            base_seed=93004,
            episodes=4,
            previous_start=93001,
            previous_end=93004,
        )


def test_periodic_checkpoint_is_due_only_after_a_complete_interval() -> None:
    assert not _should_save_periodic_checkpoint(
        completed_episodes=163,
        last_saved_episode=160,
        checkpoint_every=4,
    )
    assert _should_save_periodic_checkpoint(
        completed_episodes=164,
        last_saved_episode=160,
        checkpoint_every=4,
    )
    assert _periodic_checkpoint_path(
        "/tmp/run/mappo.pt", episode=164
    ).as_posix() == "/tmp/run/checkpoints/mappo_ep000164.pt"


def test_training_diagnostics_are_atomically_replaced(tmp_path) -> None:
    output_path = tmp_path / "diagnostics.json"
    payload = {
        "status": "running",
        "config_signature": "abc",
        "batches": [
            {
                "batch_number": 1,
                "policy_generation": 1,
                "unique_joint_state_count": 157,
            }
        ],
    }

    _write_training_diagnostics(output_path, payload)

    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
    assert list(tmp_path.glob("diagnostics.json.tmp-*")) == []


@pytest.mark.parametrize(
    ("critic_scope", "expected_label"),
    [
        ("global", "cooperative_mappo"),
        ("local", "cooperative_ippo"),
    ],
)
def test_training_config_factory_builds_explicit_cooperative_variants(
    critic_scope: str, expected_label: str
) -> None:
    config = _build_training_config(
        ("demo_1", "demo_2"),
        critic_scope=critic_scope,
        model_version=COOPERATIVE_MODEL_VERSION,
    )

    assert config.model_version == COOPERATIVE_MODEL_VERSION
    assert config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    assert config.critic_target_scope == "team_return"
    assert algorithm_label(config) == expected_label


def test_cli_parser_accepts_explicit_cooperative_objective() -> None:
    args = _parse_training_args(
        [
            "--base-seed",
            "93001",
            "--model-version",
            COOPERATIVE_MODEL_VERSION,
            "--critic-scope",
            "global",
        ]
    )

    assert args.model_version == COOPERATIVE_MODEL_VERSION
    assert args.critic_scope == "global"


def test_training_config_factory_defaults_to_cooperative_joint_v1() -> None:
    config = _build_training_config(("demo_1", "demo_2"))

    assert config.model_version == COOPERATIVE_MODEL_VERSION
    assert config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    assert config.critic_target_scope == "team_return"
    assert algorithm_label(config) == "cooperative_mappo"


@pytest.mark.parametrize("critic_scope", ["global", "local"])
def test_startup_output_and_checkpoint_use_algorithm_label(
    critic_scope: str,
) -> None:
    config = _build_training_config(
        ("demo_1", "demo_2"),
        critic_scope=critic_scope,
        model_version=COOPERATIVE_MODEL_VERSION,
    )
    expected_label = algorithm_label(config)

    assert _training_run_label(config) == expected_label
    assert _default_checkpoint_path(config, intersections=2).name == (
        f"{expected_label}_2tls.pt"
    )
    metadata = _checkpoint_metadata(
        config=config,
        episode=8,
        policy_generation=1,
        actor_init_seed=42,
        critic_init_seed=43,
        training_seed_start=93001,
        training_seed_end=93008,
        training_periods=("off_peak",),
        training_workers=8,
        episode_duration_s=300,
    )
    assert metadata.algorithm_variant == expected_label


def test_seed_disjoint_utility_regression_for_paired_experiments() -> None:
    assert_seed_disjoint(range(93001, 93009), range(91001, 91009))

    with pytest.raises(ValueError, match="overlap.*93004"):
        assert_seed_disjoint(range(93001, 93009), (91001, 93004))


class _StopBeforeSumo(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("extra_args", "expected_label"),
    [
        (
            (
                "--model-version",
                COOPERATIVE_MODEL_VERSION,
                "--critic-scope",
                "global",
            ),
            "cooperative_mappo",
        ),
        (
            (
                "--model-version",
                COOPERATIVE_MODEL_VERSION,
                "--critic-scope",
                "local",
            ),
            "cooperative_ippo",
        ),
        ((), "cooperative_mappo"),
    ],
)
def test_main_wires_cli_objective_before_any_sumo_worker(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    extra_args: tuple[str, ...],
    expected_label: str,
) -> None:
    captured: dict[str, MAPPOConfig] = {}

    def stop_before_sumo(**kwargs):
        captured["config"] = kwargs["config"]
        raise _StopBeforeSumo("intentional pre-SUMO test stop")

    monkeypatch.setattr(train_module, "_run_policy_batch", stop_before_sumo)
    caplog.set_level(logging.INFO, logger="mappo.train")
    result = train_module.main(
        [
            "--base-seed",
            "93001",
            "--episodes",
            "1",
            "--workers",
            "1",
            "--duration",
            "120",
            "--intersections",
            "2",
            *extra_args,
        ]
    )

    assert result == 1
    assert algorithm_label(captured["config"]) == expected_label
    assert any(
        expected_label in record.getMessage()
        and "start" in record.getMessage()
        for record in caplog.records
    )


def test_main_wires_scenario_preset_before_any_sumo_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, MAPPOConfig] = {}

    def stop_before_sumo(**kwargs):
        captured["config"] = kwargs["config"]
        raise _StopBeforeSumo("intentional pre-SUMO test stop")

    monkeypatch.setattr(train_module, "_run_policy_batch", stop_before_sumo)
    result = train_module.main(
        [
            "--base-seed",
            "93001",
            "--episodes",
            "1",
            "--workers",
            "1",
            "--duration",
            "120",
            "--scenario-preset",
            "east_dense",
        ]
    )

    assert result == 1
    assert captured["config"].intersection_ids == (
        "demo_3",
        "demo_5",
        "demo_6",
        "demo_9",
    )


def test_scenario_preset_and_intersections_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        train_module.main(
            [
                "--base-seed",
                "93001",
                "--scenario-preset",
                "east_dense",
                "--intersections",
                "4",
            ]
        )


@pytest.mark.parametrize(
    "resume_args",
    [
        ("--period", "morning_peak", "--base-seed", "93005"),
        ("--period", "off_peak", "--base-seed", "93004"),
    ],
)
def test_main_prevalidates_resume_contract_before_checkpoint_mutation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    resume_args: tuple[str, ...],
) -> None:
    config = _build_training_config(("demo_1", "demo_2"))
    metadata = _checkpoint_metadata(
        config=config,
        episode=4,
        policy_generation=1,
        actor_init_seed=42,
        critic_init_seed=43,
        training_seed_start=93001,
        training_seed_end=93004,
        training_periods=("off_peak",),
        training_workers=2,
        episode_duration_s=120,
    )
    checkpoint_path = tmp_path / "resume.pt"
    checkpoint_path.touch()
    load_called = False

    monkeypatch.setattr(
        train_module, "read_checkpoint_metadata", lambda _path: metadata
    )

    def unexpected_state_restore(*_args, **_kwargs):
        nonlocal load_called
        load_called = True
        raise AssertionError("checkpoint state must not be restored")

    monkeypatch.setattr(train_module, "load_checkpoint", unexpected_state_restore)

    with pytest.raises(SystemExit):
        train_module.main(
            [
                "--resume",
                str(checkpoint_path),
                "--intersections",
                "2",
                "--episodes",
                "2",
                "--workers",
                "2",
                "--duration",
                "120",
                *resume_args,
            ]
        )

    assert not load_called
