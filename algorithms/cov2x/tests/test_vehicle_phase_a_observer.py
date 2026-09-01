from __future__ import annotations

import pytest

from algorithms.cov2x.vehicle.phase_a_observer import PhaseAHorizonMetrics


def _metadata():
    return {
        "episode_id": "observer-test",
        "vehicle_types": {"passenger": {"min_gap_m": 2.5}},
        "edge_lanes": {
            "terminal": [{"lane_id": "terminal_0", "edge_id": "terminal"}],
            "out": [{"lane_id": "out_0", "edge_id": "out"}],
        },
        "intersections": {
            "tls": {
                "connections": [
                    {
                        "connection_id": "c0",
                        "movement": "through",
                        "from_lane": "terminal_0",
                        "to_lane": "out_0",
                    }
                ]
            }
        },
    }


def _vehicle(*, speed, time_loss, waiting, gap=5.0, route=("terminal", "out")):
    return {
        "type_id": "passenger",
        "position": {"x_m": 1.0, "y_m": 2.0},
        "motion": {
            "speed_mps": speed,
            "acceleration_mps2": 0.0,
            "allowed_speed_mps": 12.0,
        },
        "location": {
            "road_id": "terminal",
            "lane_id": "terminal_0",
            "route_edges": list(route),
            "route_index": 0,
        },
        "traffic": {
            "time_loss_s": time_loss,
            "waiting_time_s": waiting,
            "accumulated_waiting_time_s": waiting,
        },
        "next_signal": {
            "intersection_id": "tls",
            "state": "G",
            "distance_m": 20.0,
        },
        "driving_events": {"hard_braking_total": 0},
        "leader_gap_m": gap,
    }


def _frame(time_s, local, outside, *, action_step=None):
    frame = {
        "frame_id": int(round(time_s * 20)),
        "simulation_time": time_s,
        "vehicles": {"target": local, "outside": outside},
        "intersections": {},
        "traffic": {"hard_braking_events": 0},
        "previous_action_results": {"step_id": action_step, "vehicles": {}},
    }
    if action_step is not None:
        frame["previous_action_results"]["vehicles"]["target"] = {
            "requested": {"target_speed_mps": 11.4},
            "actual_speed_mps": local["motion"]["speed_mps"],
            "speed_status": "applied",
        }
    return frame


def test_horizon_metrics_are_dynamic_movement_local_and_network_paired():
    metrics = PhaseAHorizonMetrics(
        _metadata(),
        treatment={
            "simulation_time": 5.0,
            "horizon_s": 20.0,
            "opportunity": {
                "vehicle_id": "target",
                "intersection_id": "tls",
                "movement_id": "through",
            },
        },
    )
    metrics.on_frame(
        _frame(
            4.95,
            _vehicle(speed=2.0, time_loss=1.0, waiting=0.5),
            _vehicle(speed=4.0, time_loss=2.0, waiting=0.0, route=("other", "out")),
        )
    )
    metrics.on_frame(
        _frame(
            5.0,
            _vehicle(speed=2.0, time_loss=1.0, waiting=0.5),
            _vehicle(speed=4.0, time_loss=2.0, waiting=0.0, route=("other", "out")),
        )
    )
    metrics.on_frame(
        _frame(
            5.05,
            _vehicle(speed=0.0, time_loss=1.02, waiting=0.51),
            _vehicle(speed=4.0, time_loss=2.03, waiting=0.0, route=("other", "out")),
            action_step=1,
        )
    )
    summary = metrics.summary()

    assert summary["movement_local_time_loss_s"] == pytest.approx(0.02)
    assert summary["movement_waiting_s"] == pytest.approx(0.01)
    assert summary["movement_stop_count"] == 1
    assert summary["network_time_loss_s"] == pytest.approx(0.05)
    assert [row["vehicle_id"] for row in summary["target_trajectory"]] == [
        "target",
        "target",
    ]
    assert summary["minimum_gap_breaches"] == 0
    assert summary["command_audit"] == [
        {
            "step_id": 1,
            "requested": {"target_speed_mps": 11.4},
            "actual_speed_mps": 0.0,
            "speed_status": "applied",
        }
    ]


def test_horizon_records_target_minimum_gap_breach():
    metrics = PhaseAHorizonMetrics(
        _metadata(),
        treatment={
            "simulation_time": 5.0,
            "horizon_s": 20.0,
            "opportunity": {
                "vehicle_id": "target",
                "intersection_id": "tls",
                "movement_id": "through",
            },
        },
    )
    metrics.on_frame(
        _frame(
            5.0,
            _vehicle(speed=1.0, time_loss=0.0, waiting=0.0, gap=2.0),
            _vehicle(speed=4.0, time_loss=0.0, waiting=0.0),
        )
    )
    assert metrics.summary()["minimum_gap_breaches"] == 1


def test_horizon_fails_closed_on_nonfinite_traffic_metric():
    metrics = PhaseAHorizonMetrics(
        _metadata(),
        treatment={
            "simulation_time": 5.0,
            "horizon_s": 20.0,
            "opportunity": {
                "vehicle_id": "target",
                "intersection_id": "tls",
                "movement_id": "through",
            },
        },
    )

    with pytest.raises(ValueError, match="must be finite"):
        metrics.on_frame(
            _frame(
                5.0,
                _vehicle(speed=1.0, time_loss=float("nan"), waiting=0.0),
                _vehicle(speed=4.0, time_loss=0.0, waiting=0.0),
            )
        )
