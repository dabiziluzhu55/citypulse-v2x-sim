"""Unit and parity tests for pressure shaping module."""

from __future__ import annotations

import pytest
from algorithms.common.pressure_shaping import (
    PressureShaper,
    PressureRegretResult,
    density_gate,
)


def _make_lanes(lane_halting: dict[str, float] | None = None) -> dict:
    result = {}
    for lane_id, halting in (lane_halting or {}).items():
        result[lane_id] = {"halting_count": halting}
    return result


def _sample_topology() -> dict[int, list[tuple[str, str, float]]]:
    return {
        0: [("in_n", "out_s", 1.0), ("in_s", "out_n", 1.0)],
        1: [("in_n", "out_e", 1.0), ("in_s", "out_w", 1.0)],
        2: [("in_e", "out_w", 0.5), ("in_w", "out_e", 0.5)],
        3: [("in_e", "out_n", 0.5), ("in_w", "out_s", 0.5)],
    }


class TestDensityGate:
    def test_zero_occupancy(self) -> None:
        d, alpha = density_gate(0.0)
        assert d == 0.0
        assert alpha == pytest.approx(0.10)

    def test_at_threshold(self) -> None:
        d, alpha = density_gate(40.0)
        assert d == 1.0
        assert alpha == pytest.approx(0.025)

    def test_half_threshold(self) -> None:
        d, alpha = density_gate(20.0)
        assert d == 0.5
        assert alpha == pytest.approx(0.0625)

    def test_above_threshold_clips(self) -> None:
        d, alpha = density_gate(100.0)
        assert d == 1.0
        assert alpha == pytest.approx(0.025)


class TestPressureShaper:
    def test_positive_pressure(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({"in_n": 5.0, "out_s": 0.0, "in_s": 3.0, "out_n": 0.0})
        pressures = shaper.compute_phase_pressures(lanes, [0, 1, 2, 3])
        assert pressures[0] == pytest.approx(8.0)

    def test_negative_downstream_not_clipped(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({"in_n": 2.0, "out_s": 8.0, "in_s": 1.0, "out_n": 0.0})
        pressures = shaper.compute_phase_pressures(lanes, [0])
        assert pressures[0] == pytest.approx(-5.0)

    def test_protected_vs_permissive(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({
            "in_n": 10.0, "out_s": 0.0, "in_s": 10.0, "out_n": 0.0,
            "in_e": 10.0, "out_w": 0.0, "in_w": 10.0, "out_e": 0.0,
        })
        pressures = shaper.compute_phase_pressures(lanes, [0, 2])
        assert pressures[0] == pytest.approx(20.0)
        assert pressures[2] == pytest.approx(10.0)

    def test_missing_lane_fallback(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({"in_n": 5.0})
        pressures = shaper.compute_phase_pressures(lanes, [0])
        assert pressures[0] == pytest.approx(5.0)

    def test_regret_best_is_zero(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({"in_n": 10.0, "out_s": 0.0, "in_s": 0.0, "out_n": 0.0})
        result = shaper.compute_pressure_regret(lanes, [0, 1, 2, 3], selected_phase=0)
        assert result.regret == pytest.approx(0.0)
        assert result.mp_agreement == 1

    def test_regret_worst_is_minus_one(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({
            "in_n": 10.0, "out_s": 0.0, "in_s": 0.0, "out_n": 0.0,
            "in_e": 0.0, "out_w": 0.0, "in_w": 0.0, "out_e": 0.0,
        })
        result = shaper.compute_pressure_regret(lanes, [0, 2], selected_phase=2)
        assert result.regret == pytest.approx(-1.0)
        assert result.mp_agreement == 0

    def test_regret_zero_range(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({})
        result = shaper.compute_pressure_regret(lanes, [0, 1, 2, 3], selected_phase=0)
        assert result.regret == 0.0
        assert result.mp_agreement == 1

    def test_regret_legal_only(self) -> None:
        shaper = PressureShaper(_sample_topology())
        lanes = _make_lanes({
            "in_n": 20.0, "out_s": 0.0, "in_s": 0.0, "out_n": 0.0,
            "in_e": 2.0, "out_w": 0.0, "in_w": 0.0, "out_e": 0.0,
        })
        result = shaper.compute_pressure_regret(lanes, [2], selected_phase=2)
        assert result.regret == 0.0

    def test_regret_empty_legal(self) -> None:
        shaper = PressureShaper(_sample_topology())
        result = shaper.compute_pressure_regret({}, [], selected_phase=0)
        assert result.regret == 0.0

    def test_mp_agreement_tolerance(self) -> None:
        topo = {0: [("in_n", "out_s", 1.0)], 1: [("in_e", "out_w", 1.0)]}
        shaper = PressureShaper(topo)
        lanes = _make_lanes({
            "in_n": 10.0, "out_s": 0.0, "in_e": 10.0, "out_w": 0.0,
        })
        result = shaper.compute_pressure_regret(lanes, [0, 1], selected_phase=1)
        assert result.mp_agreement == 1

    def test_result_is_frozen(self) -> None:
        result = PressureRegretResult(
            regret=-0.5, selected_pressure=3.0, max_pressure=5.0,
            min_pressure=1.0, pressure_range=4.0, mp_agreement=0,
            all_pressures={0: 5.0, 1: 1.0},
        )
        with pytest.raises(Exception):
            result.regret = 0.0  # type: ignore[misc]

    def test_occupancy_unit_is_percent(self) -> None:
        from algorithms.ippo.controller import MAX_OCCUPANCY as IPPO_MAX
        from algorithms.mappo.reward import MAX_OCCUPANCY as MAPPO_MAX
        assert IPPO_MAX == 100.0
        assert MAPPO_MAX == 100.0
