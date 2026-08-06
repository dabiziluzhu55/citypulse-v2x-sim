from __future__ import annotations

import pytest

from algorithms.mappo.evaluate_paired import (
    aggregate_evaluation_reports,
    aggregate_paired_results,
)


def test_paired_report_preserves_missing_values_and_summarizes_deltas() -> None:
    ippo = {
        62001: {"avg_travel_time_s": 100.0, "fuel": None},
        62002: {"avg_travel_time_s": 110.0, "fuel": 14.0},
    }
    mappo = {
        62001: {"avg_travel_time_s": 95.0, "fuel": 13.0},
        62002: {"avg_travel_time_s": 115.0, "fuel": 15.0},
    }

    report = aggregate_paired_results(
        ippo,
        mappo,
        baseline_name="IPPO",
        candidate_name="MAPPO-v1",
        baseline_training_seed_range=(93001, 93008),
        candidate_training_seed_range=(94001, 94008),
        bootstrap_samples=500,
        bootstrap_seed=123,
    )

    assert report["seeds"] == (62001, 62002)
    assert report["raw"]["IPPO"][62001]["fuel"] is None
    assert report["raw"]["MAPPO-v1"][62001]["fuel"] == 13.0
    travel = report["metrics"]["avg_travel_time_s"]
    assert travel["delta_definition"] == "MAPPO-v1 - IPPO"
    assert travel["per_seed_delta"] == {62001: -5.0, 62002: 5.0}
    assert travel["summary"]["count"] == 2
    assert travel["summary"]["mean"] == pytest.approx(0.0)
    assert travel["summary"]["std"] == pytest.approx(5.0)
    assert travel["summary"]["median"] == pytest.approx(0.0)
    assert travel["summary"]["min"] == pytest.approx(-5.0)
    assert travel["summary"]["max"] == pytest.approx(5.0)
    assert travel["summary"]["bootstrap_ci_95"][0] <= 0.0
    assert travel["summary"]["bootstrap_ci_95"][1] >= 0.0

    fuel = report["metrics"]["fuel"]
    assert fuel["per_seed_delta"] == {62001: None, 62002: 1.0}
    assert fuel["summary"]["count"] == 1
    assert fuel["summary"]["mean"] == pytest.approx(1.0)


def test_all_missing_metric_remains_na_instead_of_zero() -> None:
    report = aggregate_paired_results(
        {1: {"latency": None}},
        {1: {"latency": None}},
        baseline_name="IPPO",
        candidate_name="MAPPO-v1",
        baseline_training_seed_range=(10, 20),
        candidate_training_seed_range=(30, 40),
    )

    metric = report["metrics"]["latency"]
    assert metric["per_seed_delta"] == {1: None}
    assert metric["summary"] == {
        "count": 0,
        "mean": None,
        "std": None,
        "median": None,
        "min": None,
        "max": None,
        "bootstrap_ci_95": (None, None),
    }


def test_paired_report_rejects_mismatched_seed_sets() -> None:
    with pytest.raises(ValueError, match="paired seed sets differ"):
        aggregate_paired_results(
            {62001: {"queue": 1.0}},
            {62002: {"queue": 1.0}},
            baseline_name="IPPO",
            candidate_name="MAPPO-v1",
            baseline_training_seed_range=(93001, 93008),
            candidate_training_seed_range=(94001, 94008),
        )


def test_paired_report_rejects_evaluation_seed_used_for_training() -> None:
    with pytest.raises(ValueError, match="training and evaluation.*overlap: 62001"):
        aggregate_paired_results(
            {62001: {"queue": 1.0}},
            {62001: {"queue": 2.0}},
            baseline_name="IPPO",
            candidate_name="MAPPO-v1",
            baseline_training_seed_range=(62001, 62020),
            candidate_training_seed_range=(93001, 93020),
        )


def test_evaluation_reports_preserve_failed_seed_as_na() -> None:
    shared_config = {
        "seeds": [63101, 63102],
        "duration_s": 300,
        "intersections": ["demo_1"],
        "period": "off_peak",
        "deterministic": True,
    }
    baseline = {
        "config": shared_config,
        "checkpoint_metadata": {
            "episode": 16,
            "policy_generation": 4,
            "actor_init_seed": 42,
            "critic_init_seed": 43,
            "training_seed_start": 93101,
            "training_seed_end": 93116,
            "training_periods": ["off_peak"],
        },
        "runs": [
            {
                "status": "complete",
                "seed": 63101,
                "arrived": 10,
                "official_metrics": {"avg_waiting_time_s": 20.0},
            },
            {"status": "failed", "seed": 63102, "error": "SUMO failed"},
        ],
    }
    candidate = {
        "config": shared_config,
        "checkpoint_metadata": {
            "episode": 16,
            "policy_generation": 4,
            "actor_init_seed": 42,
            "critic_init_seed": 43,
            "training_seed_start": 93101,
            "training_seed_end": 93116,
            "training_periods": ["off_peak"],
        },
        "runs": [
            {
                "status": "complete",
                "seed": 63101,
                "arrived": 12,
                "official_metrics": {"avg_waiting_time_s": 18.0},
            },
            {
                "status": "complete",
                "seed": 63102,
                "arrived": 11,
                "official_metrics": {"avg_waiting_time_s": None},
            },
        ],
    }

    report = aggregate_evaluation_reports(
        baseline,
        candidate,
        baseline_name="Local",
        candidate_name="MAPPO-v1",
        bootstrap_samples=500,
        bootstrap_seed=7,
    )

    assert report["metrics"]["arrived"]["per_seed_delta"] == {
        63101: 2.0,
        63102: None,
    }
    waiting = report["metrics"]["avg_waiting_time_s"]
    assert waiting["per_seed_delta"] == {63101: -2.0, 63102: None}
    assert waiting["summary"]["count"] == 1


def test_evaluation_reports_must_have_identical_runtime_config() -> None:
    baseline = {
        "config": {
            "seeds": [63101],
            "duration_s": 300,
            "intersections": ["demo_1"],
            "period": "off_peak",
            "deterministic": True,
        },
        "checkpoint_metadata": {
            "episode": 16,
            "policy_generation": 4,
            "actor_init_seed": 42,
            "critic_init_seed": 43,
            "training_seed_start": 93101,
            "training_seed_end": 93116,
            "training_periods": ["off_peak"],
        },
        "runs": [],
    }
    candidate = {
        **baseline,
        "config": {**baseline["config"], "period": "morning_peak"},
    }

    with pytest.raises(ValueError, match="evaluation config"):
        aggregate_evaluation_reports(
            baseline,
            candidate,
            baseline_name="Local",
            candidate_name="MAPPO-v1",
        )


def test_evaluation_reports_allow_algorithm_and_critic_axes() -> None:
    config = {
        "seeds": [63101],
        "duration_s": 300,
        "intersections": ["demo_1"],
        "period": "off_peak",
        "deterministic": True,
    }
    shared = {
        "episode": 32,
        "policy_generation": 4,
        "actor_init_seed": 42,
        "critic_init_seed": 43,
        "training_seed_start": 95501,
        "training_seed_end": 95532,
        "training_periods": ["off_peak"],
        "reward_scope": "shared_team",
        "critic_target_scope": "team_return",
    }

    def report(variant: str, scope: str) -> dict[str, object]:
        return {
            "config": config,
            "checkpoint_metadata": {
                **shared,
                "algorithm_variant": variant,
                "critic_scope": scope,
            },
            "runs": [{"status": "failed", "seed": 63101}],
        }

    paired = aggregate_evaluation_reports(
        report("cooperative_ippo", "local"),
        report("cooperative_mappo", "global"),
        baseline_name="cooperative_ippo",
        candidate_name="cooperative_mappo",
    )

    metadata = paired["paired_checkpoint_metadata"]
    assert metadata["shared_training_metadata"] == shared
    assert metadata["algorithm_variants"] == {
        "cooperative_ippo": "cooperative_ippo",
        "cooperative_mappo": "cooperative_mappo",
    }
    assert metadata["critic_scopes"] == {
        "cooperative_ippo": "local",
        "cooperative_mappo": "global",
    }


def test_evaluation_reports_reject_mismatched_training_metadata() -> None:
    config = {
        "seeds": [63101],
        "duration_s": 300,
        "intersections": ["demo_1"],
        "period": "off_peak",
        "deterministic": True,
    }
    metadata = {
        "episode": 16,
        "policy_generation": 4,
        "actor_init_seed": 42,
        "critic_init_seed": 43,
        "training_seed_start": 93101,
        "training_seed_end": 93116,
        "training_periods": ["off_peak"],
        "critic_scope": "local",
    }
    baseline = {
        "config": config,
        "checkpoint_metadata": metadata,
        "runs": [{"status": "failed", "seed": 63101}],
    }
    candidate = {
        "config": config,
        "checkpoint_metadata": {
            **metadata,
            "episode": 12,
            "critic_scope": "global",
        },
        "runs": [{"status": "failed", "seed": 63101}],
    }

    with pytest.raises(ValueError, match="training metadata.*episode"):
        aggregate_evaluation_reports(
            baseline,
            candidate,
            baseline_name="Local",
            candidate_name="MAPPO-v1",
        )


def test_evaluation_reports_record_model_version_as_comparison_axis() -> None:
    config = {
        "seeds": [66711],
        "duration_s": 300,
        "intersections": ["demo_1"],
        "period": "off_peak",
        "deterministic": True,
    }
    shared = {
        "episode": 32,
        "policy_generation": 4,
        "actor_init_seed": 42,
        "critic_init_seed": 43,
        "training_seed_start": 95711,
        "training_seed_end": 95742,
        "training_periods": ["off_peak"],
        "algorithm_variant": "cooperative_mappo",
        "critic_scope": "global",
    }

    def report(model_version: str) -> dict[str, object]:
        return {
            "config": config,
            "checkpoint_metadata": {
                **shared,
                "model_version": model_version,
            },
            "runs": [{"status": "failed", "seed": 66711}],
        }

    paired = aggregate_evaluation_reports(
        report("cooperative_joint_v1"),
        report("cooperative_joint_owner_conditioned_v1"),
        baseline_name="v1",
        candidate_name="v1.1-AS",
    )

    metadata = paired["paired_checkpoint_metadata"]
    assert metadata["model_versions"] == {
        "v1": "cooperative_joint_v1",
        "v1.1-AS": "cooperative_joint_owner_conditioned_v1",
    }
    assert "model_version" not in metadata["shared_training_metadata"]
