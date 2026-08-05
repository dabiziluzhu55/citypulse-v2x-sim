from __future__ import annotations

import numpy as np
import pytest

from algorithms.mappo.features import CentralizedStateBuilder


def test_global_state_has_stable_intersection_order() -> None:
    builder = CentralizedStateBuilder(("demo_2", "demo_1"), obs_dim=2)

    state = builder.build(
        {
            "demo_1": np.array([1.0, 10.0], dtype=np.float32),
            "demo_2": np.array([2.0, 20.0], dtype=np.float32),
        }
    )

    np.testing.assert_array_equal(
        state.observations,
        np.array([[2.0, 20.0], [1.0, 10.0]], dtype=np.float32),
    )
    np.testing.assert_array_equal(state.agent_mask, np.array([True, True]))
    assert state.intersection_ids == ("demo_2", "demo_1")
    assert state.schema == "centralized_local_obs_pool_v1"


def test_global_state_copies_source_observations() -> None:
    source = np.array([1.0, 2.0], dtype=np.float32)
    state = CentralizedStateBuilder(("demo_1",), obs_dim=2).build(
        {"demo_1": source}
    )

    source[0] = 99.0

    np.testing.assert_array_equal(
        state.observations,
        np.array([[1.0, 2.0]], dtype=np.float32),
    )


def test_global_state_rejects_missing_intersection() -> None:
    builder = CentralizedStateBuilder(("demo_1", "demo_2"), obs_dim=2)

    with pytest.raises(ValueError, match="missing controlled intersections: demo_2"):
        builder.build({"demo_1": np.zeros(2, dtype=np.float32)})


def test_global_state_rejects_extra_intersection() -> None:
    builder = CentralizedStateBuilder(("demo_1",), obs_dim=2)

    with pytest.raises(ValueError, match="unexpected intersections: demo_2"):
        builder.build(
            {
                "demo_1": np.zeros(2, dtype=np.float32),
                "demo_2": np.zeros(2, dtype=np.float32),
            }
        )


def test_global_state_rejects_wrong_observation_shape() -> None:
    builder = CentralizedStateBuilder(("demo_1",), obs_dim=2)

    with pytest.raises(ValueError, match=r"demo_1 observation shape .* expected \(2,\)"):
        builder.build({"demo_1": np.zeros(3, dtype=np.float32)})


def test_global_state_rejects_nonfinite_observation() -> None:
    builder = CentralizedStateBuilder(("demo_1",), obs_dim=2)

    with pytest.raises(ValueError, match="finite"):
        builder.build(
            {"demo_1": np.array([0.0, np.nan], dtype=np.float32)}
        )


@pytest.mark.parametrize(
    ("intersection_ids", "obs_dim", "message"),
    [
        ((), 2, "at least one"),
        (("demo_1", "demo_1"), 2, "unique"),
        (("demo_1",), 0, "positive"),
    ],
)
def test_global_state_builder_rejects_invalid_schema(
    intersection_ids: tuple[str, ...], obs_dim: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CentralizedStateBuilder(intersection_ids, obs_dim)
