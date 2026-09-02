"""标准交通评测指标：TripInfo 路径类指标、TPI、Snapshot 排队/溢流与时间加权"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import pytest

from backend.app.metrics.collector import TrafficMetricsCollector
from backend.app.metrics.models import EvalResult
from backend.app.metrics.tripinfo import (
    DTP_SOURCE,
    PATH_AVG_SPEED_SOURCE,
    STOPS_SOURCE,
    TTI_SOURCE,
    apply_tripinfo_official_metrics,
    apply_tripinfo_path_metrics,
    parse_completed_tripinfo,
)
from simulation.sumo.engine.queue_estimate import estimate_queue_length_m
from traffic_eval.tpi import TPI_METHOD, tpi_from_dtp
from traffic_eval.powertrain import VehicleTypeFuelMeta


@dataclass
class _Lane:
    halting_count: int = 0
    role: str = "incoming"
    vehicle_count: int = 0
    mean_speed: float = 0.0
    waiting_time: float = 0.0
    occupancy: float = 0.0
    queue_length_m: Optional[float] = None
    queue_length_is_estimate: bool = True
    lane_length_m: Optional[float] = None


@dataclass
class _Intersection:
    lanes: Mapping[str, _Lane]
    current_phase: int = 0
    pending_phase: int | None = None
    stage: str = "GREEN"
    stage_elapsed: float = 0.0


@dataclass
class _Metrics:
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    hard_braking_events: int = 0


@dataclass
class _Snapshot:
    session_id: str
    elapsed_seconds: float
    metrics: _Metrics
    intersections: Mapping[str, _Intersection] = field(default_factory=dict)
    vehicles: tuple = ()
    state: str = "RUNNING"
    sequence: int = 1
    duration_seconds: float = 300.0
    progress: float = 0.0
    official_time: str = ""
    events: tuple = ()
    error: str | None = None


def _write_tripinfo(path: Path, body: str) -> Path:
    path.write_text(f"<tripinfos>{body}</tripinfos>", encoding="utf-8")
    return path


def _standard_trips_xml() -> str:
    return (
        "<tripinfo id='A' vType='passenger' depart='0' arrival='100' "
        "duration='100' routeLength='1000' timeLoss='20' waitingTime='8' "
        "waitingCount='2' vaporized='false'/>"
        "<tripinfo id='B' vType='passenger' depart='0' arrival='200' "
        "duration='200' routeLength='1500' timeLoss='50' waitingTime='12' "
        "waitingCount='4' vaporized='false'/>"
    )


def test_path_metrics_from_two_completed_vehicles(tmp_path: Path) -> None:
    path = _write_tripinfo(tmp_path / "tripinfo.xml", _standard_trips_xml())
    completed, warning = parse_completed_tripinfo(path)
    assert warning is None
    result = EvalResult(algorithm="fixed", departed=2, arrived=2)
    apply_tripinfo_path_metrics(result, completed)

    assert result.path_avg_speed_kmh == pytest.approx(30.0)
    assert result.delay_time_proportion == pytest.approx(70.0 / 300.0)
    assert result.travel_time_index == pytest.approx(300.0 / 230.0)
    assert result.avg_stops_per_vehicle == pytest.approx(3.0)
    assert result.metric_sources["path_avg_speed_kmh"] == PATH_AVG_SPEED_SOURCE
    assert result.metric_sources["delay_time_proportion"] == DTP_SOURCE
    assert result.metric_sources["travel_time_index"] == TTI_SOURCE
    assert result.metric_sources["avg_stops_per_vehicle"] == STOPS_SOURCE

    expected_tpi, expected_state = tpi_from_dtp(70.0 / 300.0)
    assert result.traffic_performance_index == pytest.approx(expected_tpi)
    assert result.traffic_state == expected_state
    assert result.tpi_method == TPI_METHOD
    assert expected_state == "畅通"


def test_unfinished_and_vaporized_excluded_from_path_metrics(tmp_path: Path) -> None:
    body = (
        _standard_trips_xml()
        + "<tripinfo id='U' vType='passenger' depart='0' arrival='-1' "
        "duration='50' routeLength='100' timeLoss='40' waitingCount='9'/>"
        "<tripinfo id='V' vType='passenger' depart='0' arrival='80' "
        "duration='80' routeLength='800' timeLoss='10' waitingCount='8' "
        "vaporized='true'/>"
    )
    path = _write_tripinfo(tmp_path / "tripinfo.xml", body)
    completed, _ = parse_completed_tripinfo(path)
    result = EvalResult()
    apply_tripinfo_path_metrics(result, completed)
    assert result.avg_stops_per_vehicle == pytest.approx(3.0)
    assert result.path_avg_speed_kmh == pytest.approx(30.0)


def test_tpi_boundaries_monotonic_and_clamped() -> None:
    samples = (0.0, 0.299, 0.3, 0.499, 0.5, 0.6, 0.7, 1.0)
    values = []
    states = []
    for dtp in samples:
        tpi, state = tpi_from_dtp(dtp)
        values.append(tpi)
        states.append(state)
        assert 0.0 <= tpi <= 10.0

    assert values[0] == pytest.approx(0.0)
    assert values[1] == pytest.approx(0.299 / 0.3 * 2.0)
    assert values[2] == pytest.approx(2.0)
    assert values[3] == pytest.approx(2.0 + (0.499 - 0.3) / 0.2 * 2.0)
    assert values[4] == pytest.approx(4.0)
    assert values[5] == pytest.approx(6.0)
    assert values[6] == pytest.approx(8.0)
    assert values[7] == pytest.approx(10.0)
    assert values == sorted(values)
    assert tpi_from_dtp(0.40)[0] == pytest.approx(3.0)
    assert tpi_from_dtp(-0.1)[0] == pytest.approx(0.0)
    assert tpi_from_dtp(1.2)[0] == pytest.approx(10.0)
    assert states == [
        "畅通",
        "畅通",
        "基本畅通",
        "基本畅通",
        "轻度拥堵",
        "中度拥堵",
        "严重拥堵",
        "严重拥堵",
    ]


def test_abnormal_tripinfo_does_not_crash(tmp_path: Path) -> None:
    body = (
        "<tripinfo id='zero' vType='passenger' depart='0' arrival='1' "
        "duration='0' routeLength='10' timeLoss='0' waitingCount='1'/>"
        "<tripinfo id='loss' vType='passenger' depart='0' arrival='10' "
        "duration='10' routeLength='100' timeLoss='12' waitingCount='1'/>"
        "<tripinfo id='missing' vType='passenger' depart='0' arrival='10' "
        "duration='10' routeLength='100' timeLoss='1'/>"
        "<tripinfo id='nan' vType='passenger' depart='0' arrival='10' "
        f"duration='10' routeLength='{math.nan}' timeLoss='1' waitingCount='1'/>"
        "<tripinfo id='neg' vType='passenger' depart='0' arrival='10' "
        "duration='10' routeLength='-5' timeLoss='1' waitingCount='1'/>"
        "<tripinfo id='ok' vType='passenger' depart='0' arrival='10' "
        "duration='10' routeLength='100' timeLoss='2' waitingCount='3'/>"
    )
    path = _write_tripinfo(tmp_path / "bad.xml", body)
    completed, warning = parse_completed_tripinfo(path)
    assert warning is None
    result = EvalResult()
    apply_tripinfo_path_metrics(result, completed)
    assert result.path_avg_speed_kmh is not None
    assert result.delay_time_proportion is not None
    assert result.travel_time_index is not None
    assert result.avg_stops_per_vehicle is not None
    assert any("duration" in w for w in result.warnings)
    assert any("timeLoss" in w for w in result.warnings)
    assert any("waitingCount" in w for w in result.warnings)
    assert any("routeLength" in w for w in result.warnings)


def test_path_metrics_none_when_no_valid_samples(tmp_path: Path) -> None:
    path = _write_tripinfo(
        tmp_path / "empty.xml",
        "<tripinfo id='bad' vType='passenger' depart='0' arrival='1' "
        "duration='0' routeLength='0' timeLoss='1' waitingCount='-1'/>",
    )
    completed, _ = parse_completed_tripinfo(path)
    result = EvalResult()
    apply_tripinfo_path_metrics(result, completed)
    assert result.path_avg_speed_kmh is None
    assert result.delay_time_proportion is None
    assert result.travel_time_index is None
    assert result.avg_stops_per_vehicle is None
    assert result.traffic_performance_index is None
    assert result.traffic_state is None


def test_regional_max_queue_incoming_only() -> None:
    collector = TrafficMetricsCollector("sotl")
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=1.0,
            metrics=_Metrics(),
            intersections={
                "ix_a": _Intersection(
                    lanes={
                        "in_a": _Lane(
                            role="incoming",
                            halting_count=2,
                            queue_length_m=12.0,
                            lane_length_m=100.0,
                        ),
                        "out_a": _Lane(
                            role="outgoing",
                            halting_count=9,
                            queue_length_m=90.0,
                            lane_length_m=100.0,
                        ),
                    }
                ),
                "ix_b": _Intersection(
                    lanes={
                        "in_b": _Lane(
                            role="incoming",
                            halting_count=3,
                            queue_length_m=40.0,
                            lane_length_m=80.0,
                        )
                    }
                ),
            },
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=3.0,
            metrics=_Metrics(),
            intersections={
                "ix_a": _Intersection(
                    lanes={
                        "in_a": _Lane(
                            role="incoming",
                            halting_count=1,
                            queue_length_m=8.0,
                            lane_length_m=100.0,
                        ),
                        "out_a": _Lane(
                            role="outgoing",
                            halting_count=9,
                            queue_length_m=95.0,
                            lane_length_m=100.0,
                        ),
                    }
                ),
                "ix_b": _Intersection(
                    lanes={
                        "in_b": _Lane(
                            role="incoming",
                            halting_count=4,
                            queue_length_m=25.0,
                            lane_length_m=80.0,
                        )
                    }
                ),
            },
        )
    )
    result = collector.result(finished=False)
    assert result.regional_max_queue_length_m == pytest.approx(40.0)
    assert result.regional_max_queue_intersection_id == "ix_b"
    assert result.regional_max_queue_lane_id == "in_b"
    assert result.regional_max_queue_sim_time_s == pytest.approx(1.0)
    assert result.metric_sources["regional_max_queue_length_m"].endswith(
        "queue_length_m"
    )


def test_time_rewind_is_ignored() -> None:
    collector = TrafficMetricsCollector("fixed")
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(),
            intersections={
                "ix": _Intersection(
                    lanes={"in": _Lane(halting_count=10, role="incoming")}
                )
            },
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=3.0,
            metrics=_Metrics(),
            intersections={
                "ix": _Intersection(
                    lanes={"in": _Lane(halting_count=99, role="incoming")}
                )
            },
        )
    )
    result = collector.result(finished=False)
    assert result.avg_queue_length_veh == pytest.approx(10.0)
    assert any("时间倒退" in w for w in result.warnings)


def test_queue_time_weighted_not_frame_average() -> None:
    collector = TrafficMetricsCollector("fixed")
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=0.0,
            metrics=_Metrics(),
            intersections={
                "ix": _Intersection(
                    lanes={"in": _Lane(halting_count=10, role="incoming")}
                )
            },
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=1.0,
            metrics=_Metrics(),
            intersections={
                "ix": _Intersection(
                    lanes={"in": _Lane(halting_count=20, role="incoming")}
                )
            },
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=4.0,
            metrics=_Metrics(),
            intersections={
                "ix": _Intersection(
                    lanes={"in": _Lane(halting_count=20, role="incoming")}
                )
            },
        )
    )
    result = collector.result(finished=False)
    assert result.avg_queue_length_veh != pytest.approx((10 + 20 + 20) / 3.0)
    assert result.avg_queue_length_veh == pytest.approx(20.0)


def test_spillback_is_time_weighted_not_frame_ratio() -> None:
    collector = TrafficMetricsCollector("fixed")
    frames = (
        (1.0, 50.0),
        (2.0, 100.0),
        (5.0, 100.0),
    )
    for sim_time, queue_m in frames:
        collector.observe_snapshot(
            _Snapshot(
                session_id="s1",
                elapsed_seconds=sim_time,
                metrics=_Metrics(),
                intersections={
                    "ix": _Intersection(
                        lanes={
                            "in": _Lane(
                                role="incoming",
                                halting_count=4,
                                queue_length_m=queue_m,
                                lane_length_m=100.0,
                            )
                        }
                    )
                },
            )
        )
    result = collector.result(finished=False)
    assert result.spillback_rate == pytest.approx(80.0)
    assert result.spillback_rate != pytest.approx(2.0 / 3.0 * 100.0)
    assert (
        result.metric_sources["spillback_rate"]
        == "simulation_snapshot_incoming_lane_queue_vs_storage_time_weighted"
    )


def test_eval_result_keeps_legacy_json_keys() -> None:
    payload = EvalResult(algorithm="sotl").to_dict()
    frontend = EvalResult(algorithm="sotl").to_frontend_metrics()
    for key in (
        "algorithm",
        "avg_travel_time_s",
        "avg_waiting_time_s",
        "avg_queue_length_veh",
        "throughput_veh_per_h",
        "avg_decision_latency_ms",
        "fuel_intensity_L_per_100km",
        "hard_braking_events",
        "hard_braking_rate",
        "departed",
        "arrived",
        "completion_rate",
        "metric_sources",
        "warnings",
    ):
        assert key in payload
    for key in (
        "avg_waiting_time",
        "avg_travel_time",
        "avg_queue_length",
        "throughput",
        "fuel_consumption",
        "fuel_intensity_L_per_100km",
        "hard_braking_events",
        "hard_braking_rate",
        "avg_decision_latency_ms",
        "departed",
        "arrived",
        "completion_rate",
    ):
        assert key in frontend
        assert frontend[key] is None or key in {"departed", "arrived"}
    for key in (
        "path_avg_speed_kmh",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "traffic_state",
        "tpi_method",
        "avg_stops_per_vehicle",
        "regional_max_queue_length_m",
        "spillback_rate",
    ):
        assert key in payload
        assert key in frontend
        assert payload[key] is None
        assert frontend[key] is None


def test_official_tripinfo_applies_path_and_fuel_once(tmp_path: Path) -> None:
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='A' vType='passenger' depart='0' arrival='100' "
        "duration='100' routeLength='1000' timeLoss='20' waitingCount='2'>"
        "<emissions fuel_abs='74500'/></tripinfo>"
        "<tripinfo id='B' vType='passenger' depart='0' arrival='200' "
        "duration='200' routeLength='1500' timeLoss='50' waitingCount='4'>"
        "<emissions fuel_abs='111750'/></tripinfo>",
    )
    result = EvalResult(departed=2, arrived=2)
    apply_tripinfo_official_metrics(
        result,
        path,
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)},
        expected_departed=2,
        include_vtypes=["passenger"],
    )
    assert result.path_avg_speed_kmh == pytest.approx(30.0)
    assert result.avg_travel_time_s == pytest.approx(150.0)
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)


def test_estimate_queue_length_matches_algorithm_formula() -> None:
    value = estimate_queue_length_m(
        lane_length_m=100.0,
        halting_count=4,
        occupancy=20.0,
        samples=(),
        default_vehicle_space=7.5,
    )
    assert value == pytest.approx(min(100.0, max(0.0, 4 * 7.5, 100.0 * 0.2)))
    assert estimate_queue_length_m(
        lane_length_m=100.0,
        halting_count=0,
        occupancy=50.0,
    ) == pytest.approx(0.0)
