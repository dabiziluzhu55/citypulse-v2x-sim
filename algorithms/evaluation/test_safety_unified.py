"""Boundary tests for unified braking/passage exposure (Task B).

Covers zero-passage, zero-event, multi-event same vehicle, missing
driving_events and missing location under the frozen 1.0 s observer
interval.  Raw counts are asserted (not pre-scaled rates), and the report
semantics must not collapse multiple events from one vehicle into a
"vehicle share" interpretation.
"""

import pytest

from algorithms.evaluation.collector import HttpMetricsCollector


def _metadata():
    return {
        "episode_id": "safety-boundary",
        "intersections": {
            "i1": {
                "incoming_lanes": ["in_0"],
                "outgoing_lanes": ["out_0"],
            }
        },
        "vehicle_types": {"passenger": {"powertrain": "gasoline"}},
    }


def _vehicle(
    lane_id,
    lane_position,
    *,
    hard_braking_total=0,
    driving_events=True,
    location=True,
):
    vehicle = {
        "type_id": "passenger",
        "traffic": {
            "accumulated_waiting_time_s": 0.0,
            "time_loss_s": 0.0,
            "distance_m": 0.0,
        },
        "energy": {"fuel_total_ml": 0.0},
    }
    if location:
        vehicle["location"] = {
            "lane_id": lane_id,
            "lane_position_m": lane_position,
        }
        vehicle["next_signal"] = {"intersection_id": "i1", "distance_m": 20.0}
    if driving_events:
        vehicle["driving_events"] = {
            "hard_braking_total": hard_braking_total,
        }
    return vehicle


def _step(sim_time, vehicles):
    return {
        "simulation_time": sim_time,
        "traffic": {"arrived_vehicles": 0},
        "intersections": {
            "i1": {
                "lanes": {
                    "in_0": {"halting_count": 0},
                    "out_0": {"halting_count": 0},
                }
            }
        },
        "vehicles": vehicles,
    }


def _finish(sim_time):
    return {
        "simulation_time": sim_time,
        "departed_vehicles": 0,
        "arrived_vehicles": 0,
        "fuel_consumed_ml": 0,
    }


def test_zero_passage_skips_rate_denominator():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    # The vehicle stays on the same incoming lane for the whole run: no
    # controlled-intersection passage is observed.
    collector.on_step(_step(1.0, {"car": _vehicle("in_0", 50)}))
    collector.on_step(_step(2.0, {"car": _vehicle("in_0", 60)}))
    collector.on_finish(_finish(2.0))
    result = collector.result()
    assert result.controlled_intersection_passages == 0
    assert result.to_dict()["passage_count"] == 0
    assert result.emergency_braking_exposure_per_1000 is None
    assert (
        result.emergency_braking_availability["status"] == "unavailable"
    )


def test_zero_braking_events_is_available_with_zero_rate():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(_step(1.0, {"car": _vehicle("in_0", 50)}))
    # Vehicle leaves the incoming lane: one passage, no braking events.
    collector.on_step(_step(2.0, {"car": _vehicle("out_0", 5)}))
    collector.on_finish(_finish(2.0))
    result = collector.result()
    assert result.controlled_intersection_passages == 1
    assert result.emergency_braking_events == 0
    assert result.emergency_braking_exposure_per_1000 == pytest.approx(0.0)
    assert result.emergency_braking_availability["status"] == "available"


def test_same_vehicle_multiple_braking_events_counted_separately():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    # One vehicle increments its cumulative onset counter in the zone twice:
    # the event count must be 2, not 1 ("event count", not "vehicle share").
    collector.on_step(
        _step(1.0, {"car": _vehicle("in_0", 50, hard_braking_total=1)})
    )
    collector.on_step(
        _step(2.0, {"car": _vehicle("in_0", 55, hard_braking_total=2)})
    )
    collector.on_finish(_finish(2.0))
    result = collector.result()
    assert result.emergency_braking_events == 2
    assert result.to_dict()["emergency_braking_event_count"] == 2
    # Report semantics: multiple events from the same vehicle are counted.
    assert result.emergency_braking_events > 1


def test_missing_driving_events_marks_exposure_unavailable():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(1.0, {"car": _vehicle("in_0", 50, driving_events=False)})
    )
    collector.on_finish(_finish(1.0))
    result = collector.result()
    assert result.emergency_braking_exposure_per_1000 is None
    assert result.emergency_braking_availability["status"] == "unavailable"
    assert result.availability["emergency_braking_event_count"] == "unavailable"


def test_missing_location_marks_passage_tracking_unavailable():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(1.0, {"car": _vehicle("in_0", 50, location=False)})
    )
    collector.on_finish(_finish(1.0))
    result = collector.result()
    assert result.availability["passage_count"] == "unavailable"
    assert result.availability["emergency_braking_event_count"] == "unavailable"
    assert result.emergency_braking_exposure_per_1000 is None
