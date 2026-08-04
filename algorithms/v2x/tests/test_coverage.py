# algorithms/v2x/tests/test_coverage.py
from algorithms.v2x.coverage import is_in_rsu_coverage, should_use_next_signal_fallback
from algorithms.v2x.entities import RSU

RSU_A = RSU(rsu_id="a", covered_lane_ids=frozenset({"A_0", ":a_0"}),
            position=(100.0, 100.0))


def test_lane_covered():
    assert is_in_rsu_coverage("A_0", (0.0, 0.0), RSU_A)


def test_internal_lane_covered():
    assert is_in_rsu_coverage(":a_0", (0.0, 0.0), RSU_A)


def test_distance_covered():
    assert is_in_rsu_coverage("X_0", (105.0, 100.0), RSU_A, detection_radius_m=10.0)


def test_distance_outside():
    assert not is_in_rsu_coverage("X_0", (200.0, 100.0), RSU_A, detection_radius_m=10.0)


def test_null_protection():
    # rsu 无坐标 + radius 开启：距离不可判定，仅看车道
    rsu_no_pos = RSU(rsu_id="a", covered_lane_ids=frozenset({"A_0"}), position=None)
    assert not is_in_rsu_coverage("X_0", None, rsu_no_pos, detection_radius_m=10.0)
    assert is_in_rsu_coverage("A_0", None, rsu_no_pos, detection_radius_m=10.0)


def test_fallback_only_when_data_missing():
    # lane 缺失 且 半径不可判定 → 用 next_signal
    assert should_use_next_signal_fallback(
        lane_id=None, position=None, rsu=RSU_A,
        next_signal_intersection_id="a", detection_radius_m=None) is True
    assert should_use_next_signal_fallback(
        lane_id=None, position=None, rsu=RSU_A,
        next_signal_intersection_id="b", detection_radius_m=None) is False
    # lane 完整但不在覆盖区 → 不用 fallback 拉回
    assert should_use_next_signal_fallback(
        lane_id="X_0", position=(0.0, 0.0), rsu=RSU_A,
        next_signal_intersection_id="a", detection_radius_m=None) is False
