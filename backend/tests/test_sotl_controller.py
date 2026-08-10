"""Gershenson SOTL-phase / platoon控制器单元测试"""

from __future__ import annotations

from backend.app.controllers.sotl import SOTLController


def _metadata(*, minimum_green: float = 5.0, decision_interval: float = 5.0) -> dict:
    return {
        "episode_id": "ep-sotl",
        "decision_interval": decision_interval,
        "minimum_green": minimum_green,
        "sotl_threshold": 30.0,
        "sotl_omega": 25.0,
        "sotl_mu": 3,
        "intersections": {
            "ix_a": {
                "intersection_id": "ix_a",
                "phase_order": [1, 2],
                "incoming_lanes": ["in_a", "in_b"],
                "outgoing_lanes": ["out_a", "out_b"],
                "lanes": {
                    "in_a": {"role": "incoming", "length_m": 100.0},
                    "in_b": {"role": "incoming", "length_m": 100.0},
                },
                "phases": {
                    "1": {"connection_priorities": {"c0": "protected"}},
                    "2": {"connection_priorities": {"c1": "protected"}},
                },
                "connections": [
                    {"connection_id": "c0", "from_lane": "in_a", "to_lane": "out_a"},
                    {"connection_id": "c1", "from_lane": "in_b", "to_lane": "out_b"},
                ],
            },
            "ix_b": {
                "intersection_id": "ix_b",
                "phase_order": [1, 2],
                "incoming_lanes": ["in_c", "in_d"],
                "outgoing_lanes": ["out_c", "out_d"],
                "lanes": {
                    "in_c": {"role": "incoming", "length_m": 80.0},
                    "in_d": {"role": "incoming", "length_m": 80.0},
                },
                "phases": {
                    "1": {"connection_priorities": {"c2": "protected"}},
                    "2": {"connection_priorities": {"c3": "protected"}},
                },
                "connections": [
                    {"connection_id": "c2", "from_lane": "in_c", "to_lane": "out_c"},
                    {"connection_id": "c3", "from_lane": "in_d", "to_lane": "out_d"},
                ],
            },
        },
    }


def _lane_obs(*, vehicle_count: int = 0) -> dict:
    return {"vehicle_count": vehicle_count, "halting_count": vehicle_count}


def _intersection_obs(
    *,
    current_phase: int = 1,
    stage: str = "GREEN",
    stage_elapsed: float = 10.0,
    pending_phase: int | None = None,
    lanes: dict[str, dict] | None = None,
) -> dict:
    return {
        "current_phase": current_phase,
        "pending_phase": pending_phase,
        "stage": stage,
        "stage_elapsed": stage_elapsed,
        "lanes": lanes
        or {
            "in_a": _lane_obs(vehicle_count=0),
            "in_b": _lane_obs(vehicle_count=0),
        },
    }


def _observation(
    *,
    intersections: dict[str, dict] | None = None,
    vehicles: dict[str, dict] | None = None,
) -> dict:
    return {
        "simulation_time": 100.0,
        "intersections": intersections
        or {
            "ix_a": _intersection_obs(),
            "ix_b": _intersection_obs(
                lanes={
                    "in_c": _lane_obs(vehicle_count=0),
                    "in_d": _lane_obs(vehicle_count=0),
                }
            ),
        },
        "vehicles": vehicles or {},
    }


def test_kappa_accumulates_for_non_current_phases() -> None:
    controller = SOTLController(_metadata())
    runtime = controller._state["ix_a"]

    controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=2),
                    },
                )
            }
        )
    )

    assert runtime.kappa[1] == 0.0
    assert runtime.kappa[2] == 10.0


def test_threshold_triggers_switch_after_minimum_green() -> None:
    controller = SOTLController(_metadata(minimum_green=5.0))
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 30.0

    action = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=6.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                )
            }
        )
    )

    assert action["ix_a"] == 2


def test_minimum_green_blocks_early_switch() -> None:
    controller = SOTLController(_metadata(minimum_green=10.0))
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 40.0

    action = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=4.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=6),
                    },
                )
            }
        )
    )

    assert action["ix_a"] is None


def test_platoon_protection_blocks_switch() -> None:
    controller = SOTLController(_metadata())
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 35.0

    action = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=2),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                )
            },
            vehicles={
                "v1": {
                    "location": {"lane_id": "in_a", "lane_position_m": 90.0},
                },
                "v2": {
                    "location": {"lane_id": "in_a", "lane_position_m": 85.0},
                },
            },
        )
    )

    assert action["ix_a"] is None


def test_yellow_stage_returns_none_without_switch() -> None:
    controller = SOTLController(_metadata())
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 50.0

    action = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage="YELLOW",
                    stage_elapsed=1.0,
                    pending_phase=2,
                )
            }
        )
    )

    assert action["ix_a"] is None


def test_kappa_resets_only_after_actual_green_entry() -> None:
    controller = SOTLController(_metadata())
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 40.0

    controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                )
            }
        )
    )
    assert controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                )
            }
        )
    )["ix_a"] == 2
    assert runtime.kappa[2] == 40.0

    controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage="YELLOW",
                    stage_elapsed=1.0,
                    pending_phase=2,
                )
            }
        )
    )
    assert runtime.kappa[2] == 40.0

    controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=2,
                    stage="GREEN",
                    stage_elapsed=0.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                )
            }
        )
    )
    assert runtime.kappa[2] == 0.0


def test_low_flow_eventually_receives_green() -> None:
    controller = SOTLController(_metadata(decision_interval=5.0))
    runtime = controller._state["ix_a"]

    action = None
    for _ in range(7):
        action = controller.compute_actions(
            _observation(
                intersections={
                    "ix_a": _intersection_obs(
                        current_phase=1,
                        stage_elapsed=10.0,
                        lanes={
                            "in_a": _lane_obs(vehicle_count=0),
                            "in_b": _lane_obs(vehicle_count=1),
                        },
                    )
                }
            )
        )["ix_a"]

    assert runtime.kappa[2] >= 30.0
    assert action == 2


def test_multiple_intersections_are_isolated() -> None:
    controller = SOTLController(_metadata())
    runtime_a = controller._state["ix_a"]
    runtime_b = controller._state["ix_b"]
    runtime_a.kappa[2] = 35.0

    actions = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_a": _lane_obs(vehicle_count=0),
                        "in_b": _lane_obs(vehicle_count=0),
                    },
                ),
                "ix_b": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_c": _lane_obs(vehicle_count=0),
                        "in_d": _lane_obs(vehicle_count=5),
                    },
                ),
            }
        )
    )

    assert actions["ix_a"] == 2
    assert actions["ix_b"] is None
    assert runtime_b.kappa[2] == 25.0
    assert runtime_a.kappa[2] == 35.0


def test_tie_break_uses_cyclic_order_after_current() -> None:
    metadata = _metadata()
    metadata["intersections"] = {
        "ix_a": {
            "intersection_id": "ix_a",
            "phase_order": [1, 2, 3],
            "incoming_lanes": ["in_1", "in_2", "in_3"],
            "lanes": {
                "in_1": {"length_m": 100.0},
                "in_2": {"length_m": 100.0},
                "in_3": {"length_m": 100.0},
            },
            "phases": {
                "1": {"connection_priorities": {"c0": "protected"}},
                "2": {"connection_priorities": {"c1": "protected"}},
                "3": {"connection_priorities": {"c2": "protected"}},
            },
            "connections": [
                {"connection_id": "c0", "from_lane": "in_1", "to_lane": "out_1"},
                {"connection_id": "c1", "from_lane": "in_2", "to_lane": "out_2"},
                {"connection_id": "c2", "from_lane": "in_3", "to_lane": "out_3"},
            ],
        }
    }
    controller = SOTLController(metadata)
    runtime = controller._state["ix_a"]
    runtime.kappa[2] = 35.0
    runtime.kappa[3] = 35.0

    action = controller.compute_actions(
        _observation(
            intersections={
                "ix_a": _intersection_obs(
                    current_phase=1,
                    stage_elapsed=8.0,
                    lanes={
                        "in_1": _lane_obs(vehicle_count=0),
                        "in_2": _lane_obs(vehicle_count=0),
                        "in_3": _lane_obs(vehicle_count=0),
                    },
                )
            }
        )
    )["ix_a"]

    assert action == 2
