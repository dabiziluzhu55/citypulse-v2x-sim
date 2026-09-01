"""Unit tests for the frozen signal-side policies (torch-free)."""

import importlib.util
from pathlib import Path
import sys

from algorithms.cov2x.road.signal import fixed_actions, max_pressure_actions


def _load_lane_state():
    path = (
        Path(__file__).resolve().parents[1] / "road" / "lane_state.py"
    )
    spec = importlib.util.spec_from_file_location(
        "cov2x_signal_test_lane_state", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LANE_STATE = _load_lane_state()


def _metadata():
    return {
        "protocol_version": "2.0",
        "minimum_green": 5.0,
        "intersections": {
            "a": {
                "phase_order": [10, 20],
                "incoming_lanes": ["a_in_0", "a_in_1"],
                "lanes": {
                    "a_in_0": {
                        "lane_id": "a_in_0",
                        "edge_id": "a_in",
                        "lane_index": 0,
                        "length_m": 100.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                    },
                    "a_in_1": {
                        "lane_id": "a_in_1",
                        "edge_id": "a_in",
                        "lane_index": 1,
                        "length_m": 100.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                    },
                },
                "phases": {
                    "10": {"movement": "through", "green_seconds": 25.0},
                    "20": {"movement": "left", "green_seconds": 20.0},
                },
                "connections": [
                    {
                        "from_lane": "a_in_0",
                        "to_lane": "a_out_0",
                        "movement": "through",
                    },
                    {
                        "from_lane": "a_in_1",
                        "to_lane": "a_out_1",
                        "movement": "left",
                    },
                ],
            }
        },
        "edge_lanes": {
            "a_in": [
                {
                    "lane_id": "a_in_0",
                    "lane_index": 0,
                    "length_m": 100.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                },
                {
                    "lane_id": "a_in_1",
                    "lane_index": 1,
                    "length_m": 100.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                },
            ]
        },
        "vehicle_types": {"passenger": {"length_m": 5.0}},
    }


def _payload(current_phase=10, queue_through=5.0, queue_left=10.0):
    return {
        "simulation_time": 10.0,
        "intersections": {
            "a": {
                "current_phase": current_phase,
                "stage": "GREEN",
                "stage_elapsed": 5.0,
                "lanes": {
                    "a_in_0": {
                        "queue_length_m": queue_through,
                        "vehicle_count": 3,
                    },
                    "a_in_1": {
                        "queue_length_m": queue_left,
                        "vehicle_count": 5,
                    },
                },
            }
        },
        "vehicles": {},
    }


def test_max_pressure_selects_highest_queue_phase():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    phase_orders = {"a": (10, 20)}
    actions = max_pressure_actions(
        _payload(queue_through=5.0, queue_left=10.0),
        phase_orders,
        LANE_STATE,
    )
    assert actions == {"a": {"target_phase": 20}}


def test_max_pressure_tie_breaks_to_current_phase():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    phase_orders = {"a": (10, 20)}
    actions = max_pressure_actions(
        _payload(current_phase=10, queue_through=5.0, queue_left=5.0),
        phase_orders,
        LANE_STATE,
    )
    assert actions == {"a": {"target_phase": 10}}


def test_fixed_holds_current_phase():
    phase_orders = {"a": (10, 20)}
    actions = fixed_actions(
        _payload(current_phase=20),
        phase_orders,
    )
    assert actions == {"a": {"target_phase": 20}}


def test_fixed_falls_back_to_first_phase_when_current_illegal():
    phase_orders = {"a": (10, 20)}
    payload = _payload(current_phase=30)
    actions = fixed_actions(payload, phase_orders)
    assert actions == {"a": {"target_phase": 10}}
