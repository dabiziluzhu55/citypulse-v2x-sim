"""内部算法协议与 Max Pressure / 指标采集单元测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.controllers.max_pressure import MaxPressureController
from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.metrics import MetricsCollector


def _metadata(*, permissive_weight: float = 0.5) -> dict:
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-1",
        "decision_interval": 5.0,
        "max_pressure_permissive_weight": permissive_weight,
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


def _shared_lane_metadata() -> dict:
    """单进口道含直行+左转两个 movement。"""
    return {
        "episode_id": "ep-shared",
        "intersections": {
            "ix_shared": {
                "intersection_id": "ix_shared",
                "phase_order": [1, 2],
                "lanes": {
                    "in_main": {"edge_id": "edge_in"},
                    "out_through": {"edge_id": "edge_through"},
                    "out_left": {"edge_id": "edge_left"},
                },
                "phases": {
                    "1": {
                        "connection_priorities": {
                            "conn_through": "protected",
                        }
                    },
                    "2": {
                        "connection_priorities": {
                            "conn_left": "protected",
                        }
                    },
                },
                "connections": [
                    {
                        "connection_id": "conn_through",
                        "from_lane": "in_main",
                        "to_lane": "out_through",
                        "movement": "through",
                    },
                    {
                        "connection_id": "conn_left",
                        "from_lane": "in_main",
                        "to_lane": "out_left",
                        "movement": "left",
                    },
                ],
                "incoming_lanes": ["in_main"],
                "outgoing_lanes": ["out_through", "out_left"],
            }
        },
    }


def _downstream_chain_metadata() -> dict:
    """出口车道有后续受控 movement，用于测试转向加权下游队列。"""
    return {
        "episode_id": "ep-chain",
        "intersections": {
            "ix_chain": {
                "intersection_id": "ix_chain",
                "phase_order": [1, 2],
                "lanes": {
                    "in_a": {"edge_id": "e_in"},
                    "mid_a": {"edge_id": "e_mid"},
                    "out_far": {"edge_id": "e_far"},
                    "in_b": {"edge_id": "e_in_b"},
                    "out_b": {"edge_id": "e_out_b"},
                },
                "phases": {
                    "1": {"connection_priorities": {"c_in_mid": "protected"}},
                    "2": {"connection_priorities": {"c_in_b": "protected"}},
                },
                "connections": [
                    {
                        "connection_id": "c_in_mid",
                        "from_lane": "in_a",
                        "to_lane": "mid_a",
                    },
                    {
                        "connection_id": "c_mid_far",
                        "from_lane": "mid_a",
                        "to_lane": "out_far",
                    },
                    {
                        "connection_id": "c_in_b",
                        "from_lane": "in_b",
                        "to_lane": "out_b",
                    },
                ],
                "incoming_lanes": ["in_a", "in_b"],
                "outgoing_lanes": ["mid_a", "out_b", "out_far"],
            }
        },
    }


def _lane_obs(*, halting_count: int = 0, vehicle_count: int | None = None) -> dict:
    return {
        "halting_count": halting_count,
        "vehicle_count": halting_count if vehicle_count is None else vehicle_count,
    }


def _vehicle(
    *,
    lane_id: str,
    route_edges: list[str],
    route_index: int = 0,
    speed_mps: float = 0.0,
) -> dict:
    return {
        "location": {
            "lane_id": lane_id,
            "route_index": route_index,
            "route_edges": route_edges,
        },
        "motion": {"speed_mps": speed_mps},
    }


def test_max_pressure_picks_higher_upstream_queue() -> None:
    controller = MaxPressureController(_metadata())
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "stage": "GREEN",
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


def test_shared_lane_halting_not_double_counted() -> None:
    controller = MaxPressureController(_shared_lane_metadata())
    actions = controller.compute_actions(
        {
            "intersections": {
                "ix_shared": {
                    "current_phase": 1,
                    "stage": "GREEN",
                    "lanes": {
                        "in_main": _lane_obs(halting_count=4),
                        "out_through": _lane_obs(halting_count=0),
                        "out_left": _lane_obs(halting_count=0),
                    },
                }
            },
            "vehicles": {},
        }
    )
    ix = controller._ix["ix_shared"]
    queues = __import__(
        "backend.app.controllers.max_pressure", fromlist=["_estimate_movement_queues"]
    )._estimate_movement_queues(
        ix,
        {
            "in_main": _lane_obs(halting_count=4),
            "out_through": _lane_obs(halting_count=0),
            "out_left": _lane_obs(halting_count=0),
        },
        {},
    )
    assert queues["conn_through"] == 2.0
    assert queues["conn_left"] == 2.0
    assert queues["conn_through"] + queues["conn_left"] == 4.0
    assert actions["ix_shared"] in (1, 2)


def test_downstream_congestion_reduces_pressure() -> None:
    controller = MaxPressureController(_metadata())
    actions = controller.compute_actions(
        {
            "intersections": {
                "demo_2": {
                    "current_phase": 1,
                    "stage": "GREEN",
                    "lanes": {
                        "in_a": _lane_obs(halting_count=10),
                        "out_a": _lane_obs(halting_count=8),
                        "in_b": _lane_obs(halting_count=5),
                        "out_b": _lane_obs(halting_count=0),
                    },
                }
            }
        }
    )
    assert actions["demo_2"] == 2


def test_vehicle_route_identifies_turn_on_shared_lane() -> None:
    controller = MaxPressureController(_shared_lane_metadata())
    observation = {
        "intersections": {
            "ix_shared": {
                "current_phase": 1,
                "stage": "GREEN",
                "lanes": {
                    "in_main": _lane_obs(halting_count=3),
                    "out_through": _lane_obs(halting_count=0),
                    "out_left": _lane_obs(halting_count=0),
                },
            }
        },
        "vehicles": {
            "v1": _vehicle(
                lane_id="in_main",
                route_edges=["edge_in", "edge_through"],
                speed_mps=0.0,
            ),
            "v2": _vehicle(
                lane_id="in_main",
                route_edges=["edge_in", "edge_through"],
                speed_mps=0.0,
            ),
            "v3": _vehicle(
                lane_id="in_main",
                route_edges=["edge_in", "edge_left"],
                speed_mps=0.0,
            ),
        },
    }
    mp = __import__(
        "backend.app.controllers.max_pressure", fromlist=["_estimate_movement_queues"]
    )
    queues = mp._estimate_movement_queues(
        controller._ix["ix_shared"],
        observation["intersections"]["ix_shared"]["lanes"],
        observation["vehicles"],
    )
    assert queues["conn_through"] == 2.0
    assert queues["conn_left"] == 1.0
    # 直行排队更多，应优先相位 1（验证路径识别而非简单选相位 2）
    assert controller.compute_actions(observation)["ix_shared"] == 1


def test_missing_route_falls_back_to_equal_split() -> None:
    controller = MaxPressureController(_shared_lane_metadata())
    mp = __import__(
        "backend.app.controllers.max_pressure", fromlist=["_estimate_movement_queues"]
    )
    lanes = {
        "in_main": _lane_obs(halting_count=6),
        "out_through": _lane_obs(halting_count=0),
        "out_left": _lane_obs(halting_count=0),
    }
    queues = mp._estimate_movement_queues(controller._ix["ix_shared"], lanes, {})
    assert queues["conn_through"] == 3.0
    assert queues["conn_left"] == 3.0
    assert sum(queues.values()) == 6.0


def test_partial_route_uses_observed_turn_proportions() -> None:
    controller = MaxPressureController(_shared_lane_metadata())
    mp = __import__(
        "backend.app.controllers.max_pressure", fromlist=["_estimate_movement_queues"]
    )
    lanes = {
        "in_main": _lane_obs(halting_count=6),
        "out_through": _lane_obs(halting_count=0),
        "out_left": _lane_obs(halting_count=0),
    }
    vehicles = {
        "v_t1": _vehicle(
            lane_id="in_main",
            route_edges=["edge_in", "edge_through"],
            speed_mps=0.0,
        ),
        "v_t2": _vehicle(
            lane_id="in_main",
            route_edges=["edge_in", "edge_through"],
            speed_mps=0.0,
        ),
        "v_l1": _vehicle(
            lane_id="in_main",
            route_edges=["edge_in", "edge_left"],
            speed_mps=0.0,
        ),
    }
    queues = mp._estimate_movement_queues(controller._ix["ix_shared"], lanes, vehicles)
    assert queues["conn_through"] == 4.0
    assert queues["conn_left"] == 2.0
    assert sum(queues.values()) == 6.0


def test_permissive_weight_lower_than_protected() -> None:
    metadata = _metadata(permissive_weight=0.5)
    metadata["intersections"]["demo_2"]["phases"]["2"]["connection_priorities"] = {
        "connection_1": "permissive"
    }
    controller = MaxPressureController(metadata)
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 2,
                "stage": "GREEN",
                "lanes": {
                    "in_a": _lane_obs(halting_count=6),
                    "out_a": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=6),
                    "out_b": _lane_obs(halting_count=0),
                },
            }
        }
    }
    assert controller.compute_actions(observation)["demo_2"] == 1


def test_tie_keeps_current_phase() -> None:
    controller = MaxPressureController(_metadata())
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "stage": "GREEN",
                "lanes": {
                    "in_a": _lane_obs(halting_count=4),
                    "out_a": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=4),
                    "out_b": _lane_obs(halting_count=0),
                },
            }
        }
    }
    assert controller.compute_actions(observation)["demo_2"] == 1


def test_yellow_stage_keeps_pending_phase() -> None:
    controller = MaxPressureController(_metadata())
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "pending_phase": 2,
                "stage": "YELLOW",
                "lanes": {
                    "in_a": _lane_obs(halting_count=0),
                    "out_a": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=20),
                    "out_b": _lane_obs(halting_count=0),
                },
            }
        }
    }
    assert controller.compute_actions(observation)["demo_2"] == 2


def test_clearance_without_pending_keeps_current_phase() -> None:
    controller = MaxPressureController(_metadata())
    observation = {
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "pending_phase": None,
                "stage": "CLEARANCE",
                "lanes": {
                    "in_a": _lane_obs(halting_count=0),
                    "out_a": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=20),
                    "out_b": _lane_obs(halting_count=0),
                },
            }
        }
    }
    assert controller.compute_actions(observation)["demo_2"] == 1


def test_multi_intersection_state_isolation() -> None:
    metadata = {
        "episode_id": "ep-multi",
        "intersections": {
            "ix_a": _metadata()["intersections"]["demo_2"] | {"intersection_id": "ix_a"},
            "ix_b": {
                "intersection_id": "ix_b",
                "phase_order": [1, 2],
                "phases": {
                    "1": {"connection_priorities": {"c0": "protected"}},
                    "2": {"connection_priorities": {"c1": "protected"}},
                },
                "connections": [
                    {"connection_id": "c0", "from_lane": "in_x", "to_lane": "out_x"},
                    {"connection_id": "c1", "from_lane": "in_y", "to_lane": "out_y"},
                ],
                "incoming_lanes": ["in_x", "in_y"],
                "outgoing_lanes": ["out_x", "out_y"],
            },
        },
    }
    metadata["intersections"]["ix_a"]["intersection_id"] = "ix_a"
    controller = MaxPressureController(metadata)
    observation = {
        "intersections": {
            "ix_a": {
                "current_phase": 1,
                "stage": "GREEN",
                "lanes": {
                    "in_a": _lane_obs(halting_count=1),
                    "out_a": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=1),
                    "out_b": _lane_obs(halting_count=0),
                },
            },
            "ix_b": {
                "current_phase": 1,
                "stage": "GREEN",
                "lanes": {
                    "in_x": _lane_obs(halting_count=1),
                    "out_x": _lane_obs(halting_count=0),
                    "in_y": _lane_obs(halting_count=50),
                    "out_y": _lane_obs(halting_count=0),
                },
            },
        }
    }
    actions = controller.compute_actions(observation)
    assert actions["ix_a"] == 1
    assert actions["ix_b"] == 2


def test_downstream_movement_weighted_queue() -> None:
    controller = MaxPressureController(_downstream_chain_metadata())
    observation = {
        "intersections": {
            "ix_chain": {
                "current_phase": 1,
                "stage": "GREEN",
                "lanes": {
                    "in_a": _lane_obs(halting_count=10),
                    "mid_a": _lane_obs(halting_count=0),
                    "out_far": _lane_obs(halting_count=0),
                    "in_b": _lane_obs(halting_count=4),
                    "out_b": _lane_obs(halting_count=0),
                },
            }
        },
        "vehicles": {
            "mid_q": _vehicle(
                lane_id="mid_a",
                route_edges=["e_mid", "e_far"],
                speed_mps=0.0,
            ),
        },
    }
    mp = __import__(
        "backend.app.controllers.max_pressure", fromlist=["_downstream_queue"]
    )
    ix = controller._ix["ix_chain"]
    movement = ix.movements["c_in_mid"]
    queues = mp._estimate_movement_queues(
        ix, observation["intersections"]["ix_chain"]["lanes"], observation["vehicles"]
    )
    downstream = mp._downstream_queue(
        ix,
        movement,
        queues,
        observation["intersections"]["ix_chain"]["lanes"],
    )
    assert downstream == 1.0
    assert controller.compute_actions(observation)["ix_chain"] == 1


def test_metrics_collector_tracks_arrival() -> None:
    collector = MetricsCollector(algorithm="max_pressure")
    collector.set_powertrain_by_type({"passenger": "gasoline"})
    collector._observe(
        sim_time=10.0,
        vehicles={
            "v1": {
                "waiting": 3.0,
                "distance": 50.0,
                "fuel_ml": 10.0,
                "type_id": "passenger",
            }
        },
        incoming_halting=[2.0],
    )
    collector._observe(
        sim_time=20.0,
        vehicles={},
        incoming_halting=[1.0],
    )
    collector._total_departed = 1
    collector._total_arrived = 1
    collector._final_sim_time = 20.0
    live = collector.result(finished=False, decision_latency_ms=1.0)
    assert live.arrived == 1
    assert live.avg_travel_time_s == 10.0
    assert live.avg_waiting_time_s == 3.0
    assert live.avg_queue_length_veh == 1.5

    collector._finished = True
    final = collector.result(finished=True, decision_latency_ms=1.0)
    assert final.avg_travel_time_s is None
    assert final.avg_waiting_time_s is None
    assert final.avg_queue_length_veh == 1.5
    assert final.throughput_veh_per_h == pytest.approx(180.0)


def test_internal_algorithm_protocol_endpoints(
    client: TestClient,
    algorithm_store: AlgorithmRuntimeStore,
) -> None:
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
                    "stage": "GREEN",
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

    completed = algorithm_store.get_completed_metrics("ep-1")
    assert completed is not None
    assert completed["algorithm"] == "max_pressure"
    assert completed["avg_decision_latency_ms"] >= 0.0

    metrics = client.get("/api/v1/simulations/ep-1/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["avg_decision_latency_ms"] >= 0.0
