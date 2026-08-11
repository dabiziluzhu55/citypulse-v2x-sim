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
    _group_metrics_by_period,
    _periodic_checkpoint_path,
    _period_batch,
    _parse_training_args,
    _seed_batches,
    _should_save_periodic_checkpoint,
    _training_run_label,
    _training_seed_range,
    _validate_joint_run_shape,
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



def test_training_metrics_are_grouped_by_period_without_hiding_a_period() -> None:
    periods = (
        "morning_peak",
        "off_peak",
        "evening_peak",
        "morning_peak",
        "off_peak",
        "evening_peak",
    )
    results = [
        {
            "metrics": {
                "departed": 100 + index,
                "arrived": 90 + index,
                "waiting": 10.0 + index,
            }
        }
        for index in range(6)
    ]

    grouped = _group_metrics_by_period(results, periods)

    assert tuple(grouped) == (
        "morning_peak",
        "off_peak",
        "evening_peak",
        "overall",
    )
    assert grouped["morning_peak"]["episode_count"] == 2
    assert grouped["morning_peak"]["metrics_mean"]["departed"] == 101.5
    assert grouped["off_peak"]["metrics_mean"]["waiting"] == 12.5
    assert grouped["evening_peak"]["metrics_mean"]["arrived"] == 93.5
    assert grouped["overall"]["episode_count"] == 6

def test_failed_worker_rollout_is_generation_and_schema_identified() -> None:
    config = MAPPOConfig(("demo_1",))
    rollout = _failed_worker_rollout(
        seed=93001,
        error="SUMO failed",
        policy_generation=4,
        expected_policy_digest="abc",
        config=config,
        period="evening_peak",
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
    assert rollout.period == "evening_peak"
    assert rollout.metadata is None


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


@pytest.mark.parametrize(
    ("workers", "episodes"),
    [(6, 12), (9, 18), (12, 24)],
)
def test_joint_run_shape_accepts_only_complete_balanced_batches(
    workers: int, episodes: int
) -> None:
    assert _validate_joint_run_shape(
        periods=("morning_peak", "off_peak", "evening_peak"),
        workers=workers,
        episodes=episodes,
    ) == workers
    for seeds in _seed_batches(
        base_seed=95001,
        episodes=episodes,
        workers=workers,
    ):
        periods = _period_batch(
            seeds,
            periods=("morning_peak", "off_peak", "evening_peak"),
            training_seed_start=95001,
        )
        assert periods.count("morning_peak") == workers // 3
        assert periods.count("off_peak") == workers // 3
        assert periods.count("evening_peak") == workers // 3


@pytest.mark.parametrize(
    ("periods", "workers", "episodes", "message"),
    [
        (("off_peak",), 6, 12, "periods"),
        (("morning_peak", "off_peak", "evening_peak"), 4, 12, "6, 9, or 12"),
        (("morning_peak", "off_peak", "evening_peak"), 6, 13, "complete"),
        (("morning_peak", "off_peak", "evening_peak"), 6, 486, "480"),
    ],
)
def test_joint_run_shape_rejects_non_frozen_or_tail_batches(
    periods: tuple[str, ...],
    workers: int,
    episodes: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_joint_run_shape(
            periods=periods,
            workers=workers,
            episodes=episodes,
        )


def test_sumo_worker_passes_frozen_contract_to_collector_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopBeforeSumo(RuntimeError):
        pass

    captured = {}

    def capture_collector(**kwargs):
        captured.update(kwargs)
        raise StopBeforeSumo("collector preflight reached")

    monkeypatch.setattr(
        train_module.entrypoint,
        "prepare_collector",
        capture_collector,
    )
    contract = {"environment_contract_version": 3}
    request = {
        "seed": 93001,
        "config": MAPPOConfig(("demo_1",)),
        "policy_generation": 4,
        "policy_digest": "digest",
        "policy_state": {},
        "actor_init_seed": 42,
        "critic_init_seed": 43,
        "duration": 120,
        "period": "morning_peak",
        "step_length": 0.1,
        "environment_contract": contract,
    }

    with pytest.raises(StopBeforeSumo):
        train_module._run_sumo_worker(request)

    assert captured["environment_contract"] is contract


def test_joint_resume_cannot_exceed_480_cumulative_episodes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _build_training_config(("demo_1",))
    metadata = _checkpoint_metadata(
        config=config,
        episode=480,
        policy_generation=80,
        actor_init_seed=42,
        critic_init_seed=43,
        training_seed_start=93001,
        training_seed_end=93480,
        training_periods=(
            "morning_peak",
            "off_peak",
            "evening_peak",
        ),
        training_workers=6,
        episode_duration_s=120,
    )
    checkpoint_path = tmp_path / "joint-ep480.pt"
    checkpoint_path.touch()
    monkeypatch.setattr(
        train_module,
        "read_checkpoint_metadata",
        lambda _path: metadata,
    )
    monkeypatch.setattr(
        train_module,
        "read_checkpoint_environment_contract",
        lambda _path: {},
    )
    load_calls = []

    def should_not_restore(*_args, **_kwargs):
        load_calls.append(True)
        raise AssertionError("checkpoint state must not be restored")

    monkeypatch.setattr(
        train_module,
        "load_checkpoint",
        should_not_restore,
    )

    with pytest.raises(SystemExit) as error:
        train_module.main(
            [
                "--base-seed",
                "93481",
                "--episodes",
                "6",
                "--workers",
                "6",
                "--intersections",
                "1",
                "--duration",
                "120",
                "--periods",
                "morning_peak",
                "off_peak",
                "evening_peak",
                "--resume",
                str(checkpoint_path),
            ]
        )

    assert error.value.code == 2
    assert load_calls == []
