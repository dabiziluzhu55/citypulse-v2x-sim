"""Unit tests for the Protocol 2.0 → VehicleObservation extractor."""

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

from algorithms.cov2x.vehicle.observations import build_vehicle_observations


def _load_lane_state():
    """Load lane_state by file path to avoid importing the torch-dependent
    ``algorithms.cov2x.road`` package on machines without torch."""
    path = (
        Path(__file__).resolve().parents[1] / "road" / "lane_state.py"
    )
    spec = importlib.util.spec_from_file_location(
        "cov2x_test_lane_state", path
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
                "incoming_lanes": ["a_in_0", "a_in_1", "a_in_2"],
                "outgoing_lanes": ["a_out_0"],
                "lanes": {
                    f"a_in_{i}": {
                        "lane_id": f"a_in_{i}",
                        "edge_id": "a_in",
                        "lane_index": i,
                        "role": "incoming",
                        "length_m": 100.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                        "downstream_lane_ids": ["a_out_0"],
                    }
                    for i in range(3)
                } | {
                    "a_out_0": {
                        "lane_id": "a_out_0",
                        "edge_id": "a_out",
                        "lane_index": 0,
                        "role": "outgoing",
                        "length_m": 80.0,
                        "allowed_vehicle_type_ids": ["passenger"],
                        "downstream_lane_ids": [],
                    }
                },
                "phases": {
                    "10": {"movement": "through", "green_seconds": 25.0},
                    "20": {"movement": "left", "green_seconds": 20.0},
                },
                "connections": [],
            }
        },
        "edge_lanes": {
            "a_in": [
                {
                    "lane_id": f"a_in_{i}",
                    "lane_index": i,
                    "length_m": 100.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
                for i in range(3)
            ],
            "a_out": [
                {
                    "lane_id": "a_out_0",
                    "lane_index": 0,
                    "length_m": 80.0,
                    "allowed_vehicle_type_ids": ["passenger"],
                }
            ],
        },
        "vehicle_types": {"passenger": {"length_m": 5.0}},
    }


def _step_payload(green=True, speed=5.0, lane_position=40.0):
    stage = "GREEN" if green else "RED"
    return {
        "protocol_version": "2.0",
        "simulation_time": 10.0,
        "intersections": {
            "a": {
                "current_phase": 10,
                "stage": stage,
                "stage_elapsed": 5.0,
                "lanes": {
                    f"a_in_{i}": {
                        "vehicle_count": 0,
                        "halting_count": 0,
                        "occupancy": 0.0,
                        "queue_length_m": 0.0,
                        "mean_speed": 5.0,
                        "waiting_time": 0.0,
                    }
                    for i in range(3)
                },
            }
        },
        "vehicles": {
            "v": {
                "type_id": "passenger",
                "motion": {
                    "speed_mps": speed,
                    "acceleration_mps2": -0.8,
                    "allowed_speed_mps": 13.9,
                },
                "location": {
                    "road_id": "a_in",
                    "lane_id": "a_in_1",
                    "lane_index": 1,
                    "lane_position_m": lane_position,
                    "route_edges": ["a_in", "a_out"],
                },
                "next_signal": {
                    "intersection_id": "a",
                    "distance_m": 60.0,
                    "state": "G" if green else "r",
                },
                "traffic": {
                    "waiting_time_s": 0.0,
                    "accumulated_waiting_time_s": 0.0,
                    "time_loss_s": 0.0,
                    "distance_m": 120.0,
                },
                "leader_gap_m": 18.4,
                "time_since_last_lane_change_s": 6.5,
            }
        },
        "previous_action_results": {
            "step_id": 1,
            "vehicles": {
                "v": {
                    "lane_change_status": "completed",
                    "speed_status": "applied",
                }
            },
        },
    }


def test_builds_one_observation_per_active_slot():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    observations = build_vehicle_observations(
        _step_payload(), {"a": 10}, lane_state_module=LANE_STATE
    )

    assert len(observations) == 1
    obs = observations[0]
    assert obs.vehicle_id == "v"
    assert obs.tls_id == "a"
    assert obs.speed_mps == 5.0
    assert obs.accel_mps2 == -0.8
    assert obs.allowed_speed_mps == 13.9
    assert obs.dist_to_stopline_m == 60.0
    assert obs.phase_is_green is True
    assert obs.signal_remaining_s == 20.0
    assert obs.time_to_next_green_s is None
    assert obs.lane_index == 1
    assert obs.road_lane_indices == (0, 1, 2)
    assert obs.previous_lane_change_success is True
    assert obs.state_41.shape == (41,)
    assert obs.action_mask.shape == (3,)
    assert np.isfinite(obs.state_41).all()


def test_red_phase_zeroes_remaining_and_marks_not_green():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    observations = build_vehicle_observations(
        _step_payload(green=False), {"a": 10}, lane_state_module=LANE_STATE
    )

    assert len(observations) == 1
    obs = observations[0]
    assert obs.phase_is_green is False
    assert obs.signal_remaining_s == 0.0
    assert obs.time_to_next_green_s is None


def test_skips_slots_without_candidate_vehicle():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    # Speed <= 0.1 disqualifies the candidate (lane_state rule).
    observations = build_vehicle_observations(
        _step_payload(speed=0.0), {"a": 10}, lane_state_module=LANE_STATE
    )
    assert observations == []


def test_waiting_time_uses_approach_lane_aggregation():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    payload = _step_payload()
    for lane_id, waiting in (
        ("a_in_0", 40.0),
        ("a_in_1", 25.0),
        ("a_in_2", 0.0),
    ):
        payload["intersections"]["a"]["lanes"][lane_id]["waiting_time"] = waiting
    observations = build_vehicle_observations(
        payload, {"a": 10}, lane_state_module=LANE_STATE
    )
    assert len(observations) == 1
    assert observations[0].waiting_time_s == pytest.approx(65.0)


def test_waiting_time_falls_back_to_leader_accumulated_waiting():
    LANE_STATE.build_static_indices(_metadata(), _metadata()["edge_lanes"])
    payload = _step_payload()
    payload["vehicles"]["v"]["traffic"]["accumulated_waiting_time_s"] = 30.0
    observations = build_vehicle_observations(
        payload, {"a": 10}, lane_state_module=LANE_STATE
    )
    assert observations[0].waiting_time_s == pytest.approx(30.0)
