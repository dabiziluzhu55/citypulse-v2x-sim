import subprocess
import textwrap

import pytest

from algorithms.evaluation import collector as collector_module
from algorithms.evaluation import runtime
from algorithms.evaluation.collector import HttpMetricsCollector
from algorithms.evaluation.metrics import (
    apply_tripinfo_completed_metrics,
    compute_from_tripinfo,
    print_comparison_table,
)


def _metadata():
    return {
        "episode_id": "test-episode",
        "intersections": {
            "i1": {
                "incoming_lanes": ["in_0"],
                "outgoing_lanes": ["out_0"],
            }
        },
        "vehicle_types": {
            "passenger": {
                "powertrain": "gasoline",
                "fuel_density_mg_per_ml": 745.0,
            },
            "official_electric_bicycle": {
                "powertrain": "electric",
                "fuel_density_mg_per_ml": 1.0,
            },
        },
    }


def _vehicle(type_id, *, waiting, distance, fuel_ml):
    return {
        "type_id": type_id,
        "traffic": {
            "accumulated_waiting_time_s": waiting,
            "time_loss_s": waiting + 1.0,
            "distance_m": distance,
        },
        "energy": {"fuel_total_ml": fuel_ml},
    }


def _safety_vehicle(
    *, lane_id, lane_position, speed, hard_braking_total=0, distance_to_signal=20
):
    vehicle = _vehicle(
        "passenger", waiting=0, distance=lane_position, fuel_ml=1
    )
    vehicle.update(
        {
            "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
            "location": {
                "lane_id": lane_id,
                "lane_position_m": lane_position,
            },
            "next_signal": (
                {
                    "intersection_id": "i1",
                    "distance_m": distance_to_signal,
                }
                if distance_to_signal is not None
                else None
            ),
            "driving_events": {
                "hard_braking_total": hard_braking_total,
            },
        }
    )
    return vehicle


def _step(sim_time, vehicles, *, arrived=0, incoming_queue=4, outgoing_queue=99):
    return {
        "simulation_time": sim_time,
        "traffic": {"arrived_vehicles": arrived},
        "intersections": {
            "i1": {
                "lanes": {
                    "in_0": {"halting_count": incoming_queue},
                    "out_0": {"halting_count": outgoing_queue},
                }
            }
        },
        "vehicles": vehicles,
    }


def test_live_collector_uses_incoming_lanes_and_matching_fuel_population():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(
            5,
            {
                "car": _vehicle("passenger", waiting=2, distance=1000, fuel_ml=100),
                "bike": _vehicle(
                    "official_electric_bicycle",
                    waiting=0,
                    distance=9000,
                    fuel_ml=0,
                ),
            },
        )
    )
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 2,
            "arrived_vehicles": 0,
            "fuel_consumed_ml": 100,
        }
    )

    result = collector.result()

    assert result.avg_queue_length_veh == pytest.approx(4.0)
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)


def test_live_collector_reports_exposure_normalized_safety_metrics():
    metadata = _metadata()
    metadata["vehicle_types"]["passenger"].update(
        {"length_m": 5.0, "hard_braking_threshold_mps2": -3.0}
    )
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(metadata)

    # 10 m bumper gap and 5 m/s closing speed => TTC=2 s: one severe conflict.
    collector.on_step(
        _step(
            1,
            {
                "leader": _safety_vehicle(
                    lane_id="in_0", lane_position=80, speed=5
                ),
                "follower": _safety_vehicle(
                    lane_id="in_0", lane_position=65, speed=10
                ),
            },
        )
    )
    # The same pair remains unsafe, so it must not be counted again.  The
    # follower's cumulative hard-braking counter increases once in the zone.
    collector.on_step(
        _step(
            2,
            {
                "leader": _safety_vehicle(
                    lane_id="in_0", lane_position=85, speed=5
                ),
                "follower": _safety_vehicle(
                    lane_id="in_0",
                    lane_position=72,
                    speed=10,
                    hard_braking_total=1,
                ),
            },
        )
    )
    # Both vehicles leave the controlled incoming lane: two passage exposures.
    collector.on_step(
        _step(
            3,
            {
                "leader": _safety_vehicle(
                    lane_id="out_0",
                    lane_position=5,
                    speed=5,
                    distance_to_signal=None,
                ),
                "follower": _safety_vehicle(
                    lane_id="out_0",
                    lane_position=1,
                    speed=5,
                    hard_braking_total=1,
                    distance_to_signal=None,
                ),
            },
        )
    )
    collector.on_finish(
        {
            "simulation_time": 3,
            "departed_vehicles": 2,
            "arrived_vehicles": 0,
        }
    )

    result = collector.result()

    assert result.controlled_intersection_passages == 2
    assert result.severe_conflict_events == 1
    assert result.emergency_braking_events == 1
    assert result.severe_conflict_exposure_per_10000 == pytest.approx(5000.0)
    assert result.emergency_braking_exposure_per_1000 == pytest.approx(500.0)
    assert result.metric_sources["severe_conflict_exposure_per_10000"] == (
        "protocol_ttc_drac_per_controlled_intersection_passage"
    )


def test_safety_metrics_are_na_without_complete_exposure_observation():
    metadata = _metadata()
    metadata["vehicle_types"]["passenger"].update({"length_m": 5.0})
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(metadata)
    collector.on_step(
        _step(
            1,
            {
                "car": _safety_vehicle(
                    lane_id="in_0", lane_position=20, speed=5
                )
            },
        )
    )
    collector.on_finish(
        {
            "simulation_time": 1,
            "departed_vehicles": 1,
            "arrived_vehicles": 0,
            "observer_frames": {"dropped": 1},
        }
    )

    result = collector.result()

    assert result.severe_conflict_exposure_per_10000 is None
    assert result.emergency_braking_exposure_per_1000 is None
    assert any("安全暴露率" in warning for warning in result.warnings)


def test_live_collector_includes_all_fuel_powertrains_but_excludes_electric():
    metadata = _metadata()
    metadata["vehicle_types"].update(
        {
            "bus": {"powertrain": "diesel", "fuel_density_mg_per_ml": 830.0},
            "hybrid_car": {
                "powertrain": "hybrid",
                "fuel_density_mg_per_ml": 745.0,
            },
        }
    )
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(metadata)
    collector.on_step(
        _step(
            5,
            {
                "gas": _vehicle("passenger", waiting=0, distance=1000, fuel_ml=100),
                "diesel": _vehicle("bus", waiting=0, distance=1000, fuel_ml=100),
                "hybrid": _vehicle(
                    "hybrid_car", waiting=0, distance=1000, fuel_ml=100
                ),
                "electric": _vehicle(
                    "official_electric_bicycle",
                    waiting=0,
                    distance=9000,
                    fuel_ml=999,
                ),
            },
        )
    )
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 4,
            "arrived_vehicles": 0,
        }
    )

    assert collector.result().fuel_intensity_L_per_100km == pytest.approx(10.0)


def test_live_collector_combines_closed_and_active_fuel_vehicles():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(
            0,
            {
                "arrived-car": _vehicle(
                    "passenger", waiting=3, distance=100, fuel_ml=10
                ),
                "bike": _vehicle(
                    "official_electric_bicycle",
                    waiting=0,
                    distance=1000,
                    fuel_ml=0,
                ),
            },
        )
    )
    collector.on_step(
        _step(
            5,
            {
                "active-car": _vehicle(
                    "passenger", waiting=1, distance=200, fuel_ml=20
                ),
                "bike": _vehicle(
                    "official_electric_bicycle",
                    waiting=0,
                    distance=1500,
                    fuel_ml=0,
                ),
            },
            arrived=1,
        )
    )
    collector.record_latency(2.0)
    collector.record_latency(4.0)
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 3,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 30,
        }
    )

    result = collector.result()

    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert result.avg_decision_latency_ms == pytest.approx(3.0)
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)


def test_live_collector_marks_unobserved_arrivals_unavailable():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(_step(5, {}, arrived=1))
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 1,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 1,
        }
    )

    result = collector.result()

    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert result.fuel_intensity_L_per_100km is None
    assert any("未完整观测" in warning for warning in result.warnings)


def test_live_collector_corrects_pre_114_sumo_fuel_units():
    collector = HttpMetricsCollector(
        "IPPO", fuel_telemetry_unit="legacy_ml_as_mg"
    )
    collector.on_initialize(_metadata())
    vehicle = _vehicle("passenger", waiting=0, distance=1000, fuel_ml=0)
    # In SUMO < 1.14 getFuelConsumption is mL/s.  The current telemetry layer
    # stores that numeric cumulative value in the field named fuel_total_mg.
    vehicle["energy"]["fuel_total_mg"] = 100
    collector.on_step(_step(5, {"car": vehicle}))
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 1,
            "arrived_vehicles": 0,
            "fuel_consumed_ml": 0.1,
        }
    )

    assert collector.result().fuel_intensity_L_per_100km == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.12.0", "legacy_ml_as_mg"), ("1.14.0", "protocol_ml")],
)
def test_auto_fuel_unit_follows_sumo_114_boundary(monkeypatch, version, expected):
    monkeypatch.setattr(collector_module, "_AUTO_UNIT_CACHE", None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=f"Eclipse SUMO sumo Version {version}\n"
        ),
    )

    resolved, warning = collector_module._resolve_fuel_telemetry_unit("auto")

    assert resolved == expected
    assert (warning is not None) is (expected == "legacy_ml_as_mg")


def test_tripinfo_replaces_sampled_completed_trip_metrics(tmp_path):
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(
            5,
            {"car": _vehicle("passenger", waiting=2, distance=100, fuel_ml=10)},
        )
    )
    collector.on_step(_step(10, {}, arrived=1))
    collector.on_finish(
        {
            "simulation_time": 10,
            "departed_vehicles": 1,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 10,
        }
    )
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos><tripinfo id='car' depart='2' arrival='10' duration='8' "
        "waitingTime='3'/></tripinfos>",
        encoding="utf-8",
    )

    result = apply_tripinfo_completed_metrics(collector.result(), str(tripinfo))

    assert result.avg_travel_time_s == pytest.approx(8.0)
    assert result.avg_waiting_time_s == pytest.approx(3.0)
    assert result.metric_sources["avg_travel_time_s"] == "tripinfo_completed"


def test_runtime_observer_suppresses_decision_frames_and_preserves_drop_state():
    metadata = _metadata() | {"episode_id": "runtime-episode"}
    runtime.start("IPPO", metadata)
    runtime.enable_high_frequency_observer(metadata)
    runtime.observe_decision(
        _step(1, {}, incoming_queue=100) | {"episode_id": "runtime-episode"}
    )
    runtime.observe_frame(
        _step(1, {}, incoming_queue=4) | {"episode_id": "runtime-episode"}
    )
    first = runtime.finish(
        {
            "episode_id": "runtime-episode",
            "simulation_time": 1,
            "departed_vehicles": 0,
            "arrived_vehicles": 0,
            "observer_frames": {"dropped": 1},
        }
    )
    second = runtime.finish(
        {
            "episode_id": "runtime-episode",
            "simulation_time": 1,
            "departed_vehicles": 0,
            "arrived_vehicles": 0,
        }
    )

    assert first is second
    assert second.avg_queue_length_veh is None
    assert any("丢弃了 1 帧" in warning for warning in second.warnings)


def test_live_collector_does_not_publish_timeseries_metrics_after_frame_drop():
    collector = HttpMetricsCollector("IPPO", fuel_telemetry_unit="protocol_ml")
    collector.on_initialize(_metadata())
    collector.on_step(
        _step(
            0,
            {
                "car": _vehicle(
                    "passenger", waiting=2, distance=1000, fuel_ml=100
                )
            },
        )
    )
    collector.on_step(_step(5, {}, arrived=1))
    collector.on_finish(
        {
            "simulation_time": 5,
            "departed_vehicles": 1,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 100,
            "observer_frames": {"dropped": 1},
        }
    )

    result = collector.result()

    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert result.avg_queue_length_veh is None
    assert result.fuel_intensity_L_per_100km is None
    assert result.throughput_veh_per_h == pytest.approx(720.0)
    assert any("丢弃了 1 帧" in warning for warning in result.warnings)


def test_tripinfo_missing_auxiliary_inputs_is_na_not_zero(tmp_path):
    path = tmp_path / "tripinfo.xml"
    path.write_text(
        textwrap.dedent(
            """\
            <tripinfos>
              <tripinfo id="v0" depart="0" arrival="10" duration="10"
                        waitingTime="2" routeLength="100" vType="passenger"/>
            </tripinfos>
            """
        ),
        encoding="utf-8",
    )

    result = compute_from_tripinfo(str(path), eval_duration_s=20)

    assert result.avg_travel_time_s == pytest.approx(10.0)
    assert result.avg_waiting_time_s == pytest.approx(2.0)
    assert result.avg_queue_length_veh is None
    assert result.avg_decision_latency_ms is None
    assert result.fuel_intensity_L_per_100km is None
    table = print_comparison_table([result])
    assert "N/A" in table
    assert "路网吞吐" in table
    assert "严重冲突/万次" in table
    assert "紧急制动/千次" in table


def test_emission_parser_filters_electric_and_integrates_rate(tmp_path):
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos><tripinfo id='car' depart='0' arrival='2' duration='2' "
        "waitingTime='0' routeLength='20' vType='passenger'/></tripinfos>",
        encoding="utf-8",
    )
    emissions = tmp_path / "emissions.xml"
    emissions.write_text(
        textwrap.dedent(
            """\
            <emission-export>
              <timestep time="1">
                <vehicle id="car" type="passenger" fuel="745" speed="10"/>
                <vehicle id="bike" type="official_electric_bicycle" fuel="0" speed="20"/>
              </timestep>
              <timestep time="2">
                <vehicle id="car" type="passenger" fuel="745" speed="10"/>
                <vehicle id="bike" type="official_electric_bicycle" fuel="0" speed="20"/>
              </timestep>
            </emission-export>
            """
        ),
        encoding="utf-8",
    )

    result = compute_from_tripinfo(
        str(tripinfo),
        eval_duration_s=2,
        emission_path=str(emissions),
        vehicle_type_metadata=_metadata()["vehicle_types"],
        emission_fuel_unit="mg_per_s",
    )

    # 2 s × 745 mg/s / 745 mg/mL = 2 mL; 2 s × 10 m/s = 20 m.
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)


def _step_with_controlled_passage(sim_time, vehicles):
    """One vehicle leaves incoming lane in_0 for non-incoming lane in_1 (passage)."""
    return {
        "simulation_time": sim_time,
        "traffic": {"arrived_vehicles": 0},
        "vehicles": {
            "v1": {
                "type_id": "passenger",
                "location": {"lane_id": "in_1", "lane_position_m": 10.0},
                "traffic": {"accumulated_waiting_time_s": 42.0},
                "motion": {"speed_mps": 5.0},
                "driving_events": {"hard_braking_total": 0},
            }
        },
    }


def test_safety_availability_zero_events_is_available():
    collector = HttpMetricsCollector(algorithm="model")
    metadata = _metadata()
    # in_0 stays the controlled incoming lane; in_1 is a non-incoming lane the
    # vehicle appears on after crossing the stop line (this triggers a passage).
    metadata["intersections"]["i1"]["lanes"] = {
        "in_0": {"length_m": 150.0},
        "in_1": {"length_m": 150.0},
    }
    collector.on_initialize(metadata)
    # First frame establishes previous_incoming on in_0; second frame crosses out.
    collector.on_step(
        {
            "simulation_time": 1.0,
            "traffic": {"arrived_vehicles": 0},
            "vehicles": {
                "v1": {
                    "type_id": "passenger",
                    "location": {"lane_id": "in_0", "lane_position_m": 10.0},
                    "traffic": {"accumulated_waiting_time_s": 10.0},
                    "motion": {"speed_mps": 5.0},
                    "driving_events": {"hard_braking_total": 0},
                }
            },
        }
    )
    collector.on_step(_step_with_controlled_passage(2.0, {}))
    collector.on_finish(
        {
            "simulation_time": 60.0,
            "departed_vehicles": 1,
            "arrived_vehicles": 1,
            "fuel_consumed_ml": 0.0,
            "observer_frames": {"dropped": 0},
        }
    )
    result = collector.result()
    assert result.controlled_intersection_passages == 1
    assert result.controlled_avg_waiting_time_s == 42.0
    assert result.controlled_waiting_availability == {
        "status": "available", "reason": None
    }
    assert result.severe_conflict_availability == {
        "status": "available", "reason": None
    }
    assert result.emergency_braking_availability == {
        "status": "available", "reason": None
    }
    assert result.severe_conflict_exposure_per_10000 == 0.0  # zero events, NOT None


def test_safety_availability_missing_frames_is_unavailable():
    collector = HttpMetricsCollector(algorithm="model")
    metadata = _metadata()
    collector.on_initialize(metadata)
    collector.on_step(_step_with_controlled_passage(1.0, {}))
    collector.on_finish(
        {
            "simulation_time": 60.0,
            "departed_vehicles": 0,
            "arrived_vehicles": 0,
            "fuel_consumed_ml": 0.0,
            "observer_frames": {"dropped": 3},
        }
    )
    result = collector.result()
    assert result.severe_conflict_availability["status"] == "unavailable"
    assert result.emergency_braking_availability["status"] == "unavailable"
    assert result.severe_conflict_exposure_per_10000 is None
