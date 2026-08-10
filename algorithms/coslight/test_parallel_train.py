import argparse
from pathlib import Path

import pytest
import torch

from algorithms.coslight import parallel_train


def test_parse_args_disables_vehicle_guidance_by_default(monkeypatch):
    original_parse_args = argparse.ArgumentParser.parse_args

    def parse_and_stop(parser, args=None, namespace=None):
        parsed = original_parse_args(parser, args, namespace)
        assert parsed.vehicle_guidance == "off"
        assert parsed.ppo_epochs is None
        assert parsed.warm_start is None
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_and_stop)
    with pytest.raises(SystemExit, match="0"):
        parallel_train.main([])


def test_parse_args_accepts_explicit_ppo_epochs(monkeypatch):
    original_parse_args = argparse.ArgumentParser.parse_args

    def parse_and_stop(parser, args=None, namespace=None):
        parsed = original_parse_args(parser, args, namespace)
        assert parsed.ppo_epochs == 1
        raise SystemExit(0)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_and_stop)
    with pytest.raises(SystemExit, match="0"):
        parallel_train.main(["--ppo-epochs", "1"])


def test_resume_and_warm_start_are_mutually_exclusive(tmp_path):
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"placeholder")

    with pytest.raises(SystemExit, match="2"):
        parallel_train.main(
            [
                "--resume",
                str(checkpoint),
                "--warm-start",
                str(checkpoint),
            ]
        )


def test_learned_topk_rejects_selecting_every_intersection():
    with pytest.raises(SystemExit, match="2"):
        parallel_train.main(
            [
                "--episodes",
                "1",
                "--intersections",
                "4",
                "--top-k",
                "4",
            ]
        )


def test_checkpoint_tls_order_uses_controller_canonical_order():
    assert parallel_train._canonical_tls_order(
        ("demo_1", "demo_2", "demo_10")
    ) == ("demo_1", "demo_10", "demo_2")


def test_seed_batches_are_unique_and_cover_remainder():
    assert list(
        parallel_train._seed_batches(first_seed=101, episodes=5, workers=2)
    ) == [
        (101, 102),
        (103, 104),
        (105,),
    ]


def test_worker_failure_invalidates_entire_policy_batch():
    completed = [
        {
            "status": "complete",
            "seed": 102,
            "rollout": {"sample_count": 1, "policy_generation": 4},
        },
        {
            "status": "complete",
            "seed": 101,
            "rollout": {"sample_count": 1, "policy_generation": 4},
        },
    ]
    assert [
        result["seed"]
        for result in parallel_train._validated_worker_results(
            completed, expected_generation=4
        )
    ] == [101, 102]

    with pytest.raises(RuntimeError, match="seed=102"):
        parallel_train._validated_worker_results(
            [
                completed[0],
                {"status": "failed", "seed": 102, "error": "synthetic"},
            ],
            expected_generation=4,
        )


def test_stale_policy_generation_invalidates_batch():
    with pytest.raises(RuntimeError, match="policy generation"):
        parallel_train._validated_worker_results(
            [
                {
                    "status": "complete",
                    "seed": 101,
                    "rollout": {"sample_count": 1, "policy_generation": 3},
                }
            ],
            expected_generation=4,
        )


def test_legacy_checkpoint_seed_range_continues_after_saved_episode(
    monkeypatch, tmp_path
):
    checkpoint = tmp_path / "legacy.pt"
    checkpoint.write_bytes(b"placeholder")
    monkeypatch.setattr(
        parallel_train,
        "load_checkpoint_metadata",
        lambda _path: {"episode": 16},
    )

    assert parallel_train._training_seed_range(42, 8, checkpoint) == (59, 66, 43)


def test_policy_state_equality_detects_parameter_difference():
    state = {"weight": torch.tensor([1.0])}
    changed = {"weight": torch.tensor([2.0])}

    assert parallel_train._policy_states_equal(state, state)
    assert not parallel_train._policy_states_equal(state, changed)


def test_value_stats_equality_detects_stale_collector_snapshot():
    current = {"mean": -2.0, "m2": 8.0, "count": 4}
    stale = {"mean": -1.0, "m2": 8.0, "count": 4}

    assert parallel_train._value_stats_equal(current, dict(current))
    assert not parallel_train._value_stats_equal(current, stale)


def test_signal_diagnostics_are_aggregated_with_observation_weighted_delay():
    rollouts = [
        {
            "signal_execution": {
                "commands": 10,
                "change_requests": 4,
                "observed_changes": 3,
                "unresolved_changes": 1,
                "mean_change_delay_s": 5.0,
                "max_change_delay_s": 10.0,
                "max_observed_green_s": 45.0,
                "mean_phase_dominance": 0.6,
                "max_phase_dominance": 0.8,
            }
        },
        {
            "signal_execution": {
                "commands": 8,
                "change_requests": 2,
                "observed_changes": 1,
                "unresolved_changes": 1,
                "mean_change_delay_s": 15.0,
                "max_change_delay_s": 15.0,
                "max_observed_green_s": 75.0,
                "mean_phase_dominance": 0.8,
                "max_phase_dominance": 1.0,
            }
        },
    ]

    diagnostics = parallel_train._aggregate_signal_diagnostics(rollouts)

    assert diagnostics["commands"] == 18
    assert diagnostics["change_requests"] == 6
    assert diagnostics["observed_changes"] == 4
    assert diagnostics["unresolved_changes"] == 2
    assert diagnostics["change_execution_rate"] == pytest.approx(2 / 3)
    assert diagnostics["mean_change_delay_s"] == pytest.approx(7.5)
    assert diagnostics["max_change_delay_s"] == pytest.approx(15.0)
    assert diagnostics["max_observed_green_s"] == pytest.approx(75.0)
    assert diagnostics["mean_phase_dominance"] == pytest.approx(0.7)
    assert diagnostics["max_phase_dominance"] == pytest.approx(1.0)
