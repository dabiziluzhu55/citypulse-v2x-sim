"""内部算法协议与 Max Pressure / 指标采集单元测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.controllers.max_pressure import MaxPressureController
from backend.app.metrics import MetricsCollector


def _metadata() -> dict:
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-1",
        "decision_interval": 5.0,
        "intersections": {
            "demo_2": {
                "intersection_id": "demo_2",
                "phase_order": [1, 2],
                "phases": {
                    "1": {
                        "phase_id": 1,
                        "name": "phase-1",
                        "connection_priorities": {"connection_0": "protected"},
                    },
                    "2": {
                        "phase_id": 2,
                        "name": "phase-2",
                        "connection_priorities": {"connection_1": "protected"},
                    },
                },
                "connections": [
                    {
                        "connection_id": "connection_0",
                        "from_lane": "in_a",
                        "to_lane": "out_a",
                    },
                    {
                        "connection_id": "connection_1",
                        "from_lane": "in_b",
                        "to_lane": "out_b",
                    },
                ],
                "incoming_lanes": ["in_a", "in_b"],
                "outgoing_lanes": ["out_a", "out_b"],
            }
        },
    }


def test_max_pressure_picks_higher_upstream_queue() -> None:
    controller = MaxPressureController(_metadata())
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "lanes": {
                    "in_a": {"halting_count": 1},
                    "out_a": {"halting_count": 0},
                    "in_b": {"halting_count": 8},
                    "out_b": {"halting_count": 0},
                },
            }
        }
    }
    actions = controller.compute_actions(observation)
    assert actions["demo_2"] == 2


def test_metrics_collector_tracks_arrival() -> None:
    collector = MetricsCollector(algorithm="max_pressure")
    collector.on_initialize({})
    collector.on_step(
        {
            "simulation_time": 10.0,
            "intersections": {"demo_2": {"lanes": {"in_a": {"halting_count": 2}}}},
            "vehicles": {
                "v1": {
                    "traffic": {
                        "accumulated_waiting_time_s": 3.0,
                        "time_loss_s": 1.0,
                        "distance_m": 50.0,
                    },
                    "energy": {"fuel_total_ml": 10.0},
                }
            },
        }
    )
    collector.on_step(
        {
            "simulation_time": 20.0,
            "intersections": {"demo_2": {"lanes": {"in_a": {"halting_count": 1}}}},
            "vehicles": {},
        }
    )
    collector.on_finish(
        {
            "simulation_time": 20.0,
            "departed_vehicles": 1,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 10.0,
        }
    )
    result = collector.result()
    assert result.arrived == 1
    assert result.avg_travel_time_s == 10.0
    assert result.avg_waiting_time_s == 3.0
    assert result.avg_queue_length_veh == 1.5


def test_internal_algorithm_protocol_endpoints(client: TestClient) -> None:
    init = client.post(
        "/api/v1/internal/algorithm/max_pressure/initialize",
        json=_metadata(),
    )
    assert init.status_code == 200
    assert init.json()["ready"] is True

    step = client.post(
        "/api/v1/internal/algorithm/max_pressure/step",
        json={
            "protocol_version": "2.0",
            "episode_id": "ep-1",
            "step_id": 1,
            "simulation_time": 5.0,
            "intersections": {
                "demo_2": {
                    "current_phase": 1,
                    "lanes": {
                        "in_a": {"halting_count": 5},
                        "out_a": {"halting_count": 0},
                        "in_b": {"halting_count": 1},
                        "out_b": {"halting_count": 0},
                    },
                }
            },
            "vehicles": {},
        },
    )
    assert step.status_code == 200
    body = step.json()
    assert body["step_id"] == 1
    assert body["actions"]["signals"]["demo_2"]["target_phase"] == 1

    finish = client.post(
        "/api/v1/internal/algorithm/max_pressure/finish",
        json={
            "protocol_version": "2.0",
            "episode_id": "ep-1",
            "reason": "completed",
            "simulation_time": 5.0,
            "departed_vehicles": 0,
            "arrived_vehicles": 0,
            "fuel_consumed_ml": 0.0,
        },
    )
    assert finish.status_code == 200
    assert finish.json()["ok"] is True

    metrics = client.get("/api/v1/simulations/ep-1/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["algorithm"] == "max_pressure"
