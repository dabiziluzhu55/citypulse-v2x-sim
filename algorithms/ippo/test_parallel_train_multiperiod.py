from __future__ import annotations

import pytest

from algorithms.ippo import parallel_train
from traffic_control.common.environment_contract import JOINT_PERIODS


def test_joint_shape_requires_frozen_period_order():
    with pytest.raises(ValueError, match="exactly match"):
        parallel_train._validate_joint_run_shape(
            periods=("off_peak", "morning_peak", "evening_peak"),
            workers=6,
            episodes=12,
        )


@pytest.mark.parametrize("workers", (1, 3, 4, 5, 15))
def test_joint_shape_rejects_workers_outside_benchmark_candidates(workers):
    with pytest.raises(ValueError, match="6, 9, or 12"):
        parallel_train._validate_joint_run_shape(
            periods=JOINT_PERIODS,
            workers=workers,
            episodes=18,
        )


def test_joint_shape_rejects_partial_synchronous_tail():
    with pytest.raises(ValueError, match="complete synchronous batches"):
        parallel_train._validate_joint_run_shape(
            periods=JOINT_PERIODS,
            workers=6,
            episodes=13,
        )


@pytest.mark.parametrize("workers", (6, 9, 12))
def test_joint_shape_accepts_frozen_worker_candidates(workers):
    assert parallel_train._validate_joint_run_shape(
        periods=JOINT_PERIODS,
        workers=workers,
        episodes=workers * 2,
    ) == workers


def test_resume_seed_mapping_remains_balanced_and_deterministic():
    seeds = tuple(range(55, 61))
    periods = parallel_train._period_batch(
        seeds,
        periods=JOINT_PERIODS,
        training_seed_start=43,
    )

    assert periods == JOINT_PERIODS * 2
    assert parallel_train._validate_scheduled_periods(
        [
            {
                "seed": seed,
                "rollout": {"metadata": {"period": period}},
            }
            for seed, period in zip(seeds, periods)
        ],
        periods,
    ) == {
        "morning_peak": 2,
        "off_peak": 2,
        "evening_peak": 2,
    }


def test_worker_metadata_period_must_match_scheduled_period():
    scheduled = JOINT_PERIODS * 2
    results = [
        {
            "seed": 101 + index,
            "rollout": {"metadata": {"period": period}},
        }
        for index, period in enumerate(scheduled)
    ]
    results[1]["rollout"]["metadata"]["period"] = "evening_peak"

    with pytest.raises(RuntimeError, match="scheduled period"):
        parallel_train._validate_scheduled_periods(results, scheduled)


def test_worker_metadata_is_required_before_joint_update():
    scheduled = JOINT_PERIODS * 2
    results = [
        {
            "seed": 101 + index,
            "rollout": {},
        }
        for index in range(len(scheduled))
    ]

    with pytest.raises(RuntimeError, match="metadata"):
        parallel_train._validate_scheduled_periods(results, scheduled)


def test_joint_shape_accepts_480_episode_cap():
    assert parallel_train._validate_joint_run_shape(
        periods=JOINT_PERIODS,
        workers=6,
        episodes=480,
    ) == 6


def test_joint_shape_rejects_more_than_480_episodes():
    with pytest.raises(ValueError, match="480"):
        parallel_train._validate_joint_run_shape(
            periods=JOINT_PERIODS,
            workers=6,
            episodes=486,
        )


def test_joint_resume_cannot_exceed_480_cumulative_episodes():
    with pytest.raises(ValueError, match="cumulative"):
        parallel_train._validate_joint_run_shape(
            periods=JOINT_PERIODS,
            workers=6,
            episodes=6,
            completed_episodes=480,
        )


def test_joint_resume_requires_exact_intersection_order():
    saved = ("demo_1", "demo_2")

    assert parallel_train._validate_resume_intersections(
        saved,
        saved,
        joint_training=True,
    ) == saved
    with pytest.raises(ValueError, match="exactly match"):
        parallel_train._validate_resume_intersections(
            ("demo_1",),
            saved,
            joint_training=True,
        )
    with pytest.raises(ValueError, match="exactly match"):
        parallel_train._validate_resume_intersections(
            ("demo_2", "demo_1"),
            saved,
            joint_training=True,
        )


def test_legacy_resume_retains_checkpoint_superset_support():
    assert parallel_train._validate_resume_intersections(
        ("demo_1",),
        ("demo_1", "demo_2"),
        joint_training=False,
    ) == ("demo_1",)


def test_joint_resume_requires_the_same_episode_duration():
    assert parallel_train._validate_resume_duration(
        3600,
        3600.0,
        joint_training=True,
    ) == 3600.0

    with pytest.raises(ValueError, match="episode duration"):
        parallel_train._validate_resume_duration(
            3600,
            600.0,
            joint_training=True,
        )
    with pytest.raises(ValueError, match="lacks episode duration"):
        parallel_train._validate_resume_duration(
            3600,
            None,
            joint_training=True,
        )


def test_legacy_resume_without_duration_remains_supported():
    assert parallel_train._validate_resume_duration(
        3600,
        None,
        joint_training=False,
    ) == 3600.0
