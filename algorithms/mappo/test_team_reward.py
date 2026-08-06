from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from algorithms.mappo.reward import (
    TeamRewardResult,
    V5ARewardResult,
    aggregate_team_reward,
)


COMPONENTS = {
    "D": 0.2,
    "L": 0.1,
    "S": 0.3,
    "Qmax": 0.4,
    "F_safe": 0.5,
    "B": 0.6,
    "H": -0.1,
}


def _result(
    raw_reward: float,
    *,
    component_offset: float = 0.0,
    observations: int = 3,
    observed_seconds: float = 15.0,
) -> V5ARewardResult:
    return V5ARewardResult(
        reward=raw_reward,
        raw_reward=raw_reward,
        components={
            name: value + component_offset for name, value in COMPONENTS.items()
        },
        observations=observations,
        observed_seconds=observed_seconds,
    )


def test_aggregate_team_reward_uses_fixed_order_and_raw_mean() -> None:
    first = _result(-4.0)
    second = _result(2.0, component_offset=0.2)

    team = aggregate_team_reward(
        {"demo_1": first, "demo_2": second},
        ("demo_1", "demo_2"),
        window_start_s=5.0,
        window_end_s=20.0,
    )

    assert team.raw_reward == pytest.approx(-1.0)
    assert team.reward == pytest.approx(-1.0)
    assert team.per_intersection_raw_rewards == (-4.0, 2.0)
    assert team.window_start_s == 5.0
    assert team.window_end_s == 20.0
    assert team.observations == 3
    assert team.observed_seconds == 15.0
    assert team.schema == "v5a_team_mean_raw_then_clip_v1"
    assert team.components == {
        name: pytest.approx(value + 0.1) for name, value in COMPONENTS.items()
    }


def test_aggregate_team_reward_clips_only_after_raw_mean() -> None:
    team = aggregate_team_reward(
        {"demo_1": _result(-8.0), "demo_2": _result(-4.0)},
        ("demo_1", "demo_2"),
        window_start_s=5.0,
        window_end_s=20.0,
    )

    assert team.raw_reward == pytest.approx(-6.0)
    assert team.reward == pytest.approx(-3.0)


def test_aggregate_team_reward_accepts_equivalent_float_window_duration() -> None:
    team = aggregate_team_reward(
        {
            "demo_1": _result(-1.0, observed_seconds=0.3),
            "demo_2": _result(-1.0, observed_seconds=0.3),
        },
        ("demo_1", "demo_2"),
        window_start_s=0.1,
        window_end_s=0.4,
    )

    assert team.observed_seconds == pytest.approx(0.3)


def test_aggregate_team_reward_includes_every_intersection_in_one_reward() -> None:
    first = _result(-4.0)
    baseline = aggregate_team_reward(
        {"demo_1": first, "demo_2": _result(2.0)},
        ("demo_1", "demo_2"),
        window_start_s=5.0,
        window_end_s=20.0,
    )
    changed = aggregate_team_reward(
        {"demo_1": first, "demo_2": _result(-2.0)},
        ("demo_1", "demo_2"),
        window_start_s=5.0,
        window_end_s=20.0,
    )

    assert baseline.reward == pytest.approx(-1.0)
    assert changed.reward == pytest.approx(-3.0)


@pytest.mark.parametrize(
    ("results", "intersection_ids"),
    [
        ({"demo_1": _result(-1.0)}, ("demo_1", "demo_2")),
        (
            {"demo_1": _result(-1.0), "demo_2": _result(-1.0), "extra": _result(-1.0)},
            ("demo_1", "demo_2"),
        ),
        ({"demo_1": _result(-1.0)}, ("demo_1", "demo_1")),
    ],
)
def test_aggregate_team_reward_rejects_missing_extra_or_duplicate_intersections(
    results: dict[str, V5ARewardResult], intersection_ids: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        aggregate_team_reward(
            results,
            intersection_ids,
            window_start_s=5.0,
            window_end_s=20.0,
        )


@pytest.mark.parametrize(
    ("first", "second", "window_start_s", "window_end_s"),
    [
        (_result(-1.0, observed_seconds=10.0), _result(-1.0, observed_seconds=15.0), 5.0, 20.0),
        (_result(-1.0, observed_seconds=10.0), _result(-1.0, observed_seconds=10.0), 5.0, 20.0),
        (_result(-1.0), _result(-1.0, observations=4), 5.0, 20.0),
        (_result(-1.0), _result(-1.0), 20.0, 20.0),
        (_result(-1.0), _result(-1.0), 21.0, 20.0),
    ],
)
def test_aggregate_team_reward_rejects_inconsistent_coverage(
    first: V5ARewardResult,
    second: V5ARewardResult,
    window_start_s: float,
    window_end_s: float,
) -> None:
    with pytest.raises(ValueError):
        aggregate_team_reward(
            {"demo_1": first, "demo_2": second},
            ("demo_1", "demo_2"),
            window_start_s=window_start_s,
            window_end_s=window_end_s,
        )


@pytest.mark.parametrize(
    "bad_result",
    [
        _result(float("nan")),
        _result(float("inf")),
        _result(-1.0, observed_seconds=float("nan")),
        _result(-1.0, observed_seconds=float("inf")),
        V5ARewardResult(
            reward=-1.0,
            raw_reward=-1.0,
            components={**COMPONENTS, "D": float("nan")},
            observations=3,
            observed_seconds=15.0,
        ),
    ],
)
def test_aggregate_team_reward_rejects_non_finite_result_values(
    bad_result: V5ARewardResult,
) -> None:
    with pytest.raises(ValueError):
        aggregate_team_reward(
            {"demo_1": _result(-1.0), "demo_2": bad_result},
            ("demo_1", "demo_2"),
            window_start_s=5.0,
            window_end_s=20.0,
        )


@pytest.mark.parametrize(
    ("window_start_s", "window_end_s"),
    [
        (float("nan"), 20.0),
        (5.0, float("inf")),
    ],
)
def test_aggregate_team_reward_rejects_non_finite_window(
    window_start_s: float, window_end_s: float
) -> None:
    with pytest.raises(ValueError):
        aggregate_team_reward(
            {"demo_1": _result(-1.0), "demo_2": _result(-1.0)},
            ("demo_1", "demo_2"),
            window_start_s=window_start_s,
            window_end_s=window_end_s,
        )


def test_team_reward_result_is_immutable() -> None:
    team = aggregate_team_reward(
        {"demo_1": _result(-1.0), "demo_2": _result(-1.0)},
        ("demo_1", "demo_2"),
        window_start_s=5.0,
        window_end_s=20.0,
    )

    with pytest.raises(FrozenInstanceError):
        team.reward = 0.0  # type: ignore[misc]

    with pytest.raises(TypeError):
        team.components["D"] = 0.0
