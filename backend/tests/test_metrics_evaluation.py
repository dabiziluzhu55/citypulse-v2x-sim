"""后端六指标评估口径单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.metrics.collector import TrafficMetricsCollector
from backend.app.metrics.models import EvalResult
from backend.app.metrics.powertrain import load_powertrain_by_type
from backend.app.metrics.session_hub import SessionMetricsHub
from backend.app.metrics.tripinfo import apply_tripinfo_completed_metrics


@dataclass
class _Lane:
    halting_count: int
    role: str = "incoming"
    vehicle_count: int = 0
    mean_speed: float = 0.0
    waiting_time: float = 0.0
    occupancy: float = 0.0


@dataclass
class _Intersection:
    lanes: Mapping[str, _Lane]
    current_phase: int = 0
    pending_phase: int | None = None
    stage: str = "GREEN"
    stage_elapsed: float = 0.0


@dataclass
class _Vehicle:
    vehicle_id: str
    type_id: str = "passenger"
    waiting_time: float = 0.0
    distance: float = 0.0
    fuel_total_ml: float = 0.0
    x: float = 0.0
    y: float = 0.0
    speed: float = 0.0
    angle: float = 0.0
    road_id: str = ""
    lane_id: str = ""
    time_loss: float = 0.0


@dataclass
class _Metrics:
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    fuel_consumed_ml: float = 0.0
    active_vehicles: int = 0
    remaining_vehicles: int = 0
    halting_vehicles: int = 0
    total_waiting_time: float = 0.0
    mean_speed: float = 0.0
    fuel_consumed_mg: float = 0.0
    hard_braking_events: int = 0


@dataclass
class _Snapshot:
    session_id: str
    elapsed_seconds: float
    metrics: _Metrics
    vehicles: tuple[_Vehicle, ...] = ()
    intersections: Mapping[str, _Intersection] = field(default_factory=dict)
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


def test_incoming_lane_queue_averages_per_frame_then_time() -> None:
    collector = TrafficMetricsCollector("max_pressure")
    snap1 = _Snapshot(
        session_id="s1",
        elapsed_seconds=1.0,
        metrics=_Metrics(),
        intersections={
            "ix": _Intersection(
                lanes={
                    "in_a": _Lane(halting_count=2, role="incoming"),
                    "in_b": _Lane(halting_count=6, role="incoming"),
                    "out_a": _Lane(halting_count=100, role="outgoing"),
                }
            )
        },
    )
    snap2 = _Snapshot(
        session_id="s1",
        elapsed_seconds=2.0,
        metrics=_Metrics(),
        intersections={
            "ix": _Intersection(
                lanes={
                    "in_a": _Lane(halting_count=0, role="incoming"),
                    "in_b": _Lane(halting_count=4, role="incoming"),
                    "out_a": _Lane(halting_count=100, role="outgoing"),
                }
            )
        },
    )
    collector.observe_snapshot(snap1)  # mean incoming = 4
    collector.observe_snapshot(snap2)  # mean incoming = 2
    result = collector.result(finished=False)
    assert result.avg_queue_length_veh == pytest.approx(3.0)
    assert result.metric_sources["avg_queue_length_veh"] == "incoming_lane_halting_count"


def test_fuel_intensity_same_population_excludes_electric() -> None:
    collector = TrafficMetricsCollector("sotl")
    collector.set_powertrain_by_type(
        {
            "passenger": "gasoline",
            "bus": "diesel",
            "hybrid_car": "hybrid",
            "official_electric_bicycle": "electric",
        }
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=4),
            vehicles=(
                _Vehicle("gas", type_id="passenger", distance=1000, fuel_total_ml=100),
                _Vehicle("diesel", type_id="bus", distance=1000, fuel_total_ml=100),
                _Vehicle(
                    "hybrid", type_id="hybrid_car", distance=1000, fuel_total_ml=100
                ),
                _Vehicle(
                    "bike",
                    type_id="official_electric_bicycle",
                    distance=9000,
                    fuel_total_ml=999,
                ),
            ),
        )
    )
    result = collector.result(finished=True, decision_latency_ms=1.0)
    # (300ml /1000) / (3000m /100000) = 0.3 / 0.03 = 10
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)
    assert (
        result.metric_sources["fuel_intensity_L_per_100km"]
        == "fuel_powertrain_vehicle_totals"
    )


def test_fuel_missing_powertrain_returns_none() -> None:
    collector = TrafficMetricsCollector("fixed")
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=1),
            vehicles=(
                _Vehicle("v1", type_id="passenger", distance=1000, fuel_total_ml=100),
            ),
        )
    )
    result = collector.result(finished=True)
    assert result.fuel_intensity_L_per_100km is None
    assert any("powertrain" in w for w in result.warnings)


def test_fixed_decision_latency_is_none() -> None:
    store = AlgorithmRuntimeStore()
    assert store.get_decision_latency_ms("missing") is None

    collector = TrafficMetricsCollector("fixed")
    result = collector.result(finished=True, decision_latency_ms=None)
    assert result.avg_decision_latency_ms is None
    assert "avg_decision_latency_ms" not in result.metric_sources


def test_throughput_uses_evaluation_duration() -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector._total_arrived = 10
    collector._final_sim_time = 100.0
    collector._finished = True
    result = collector.result(finished=True, decision_latency_ms=0.5)
    assert result.throughput_veh_per_h == pytest.approx(360.0)
    assert result.metric_sources["throughput_veh_per_h"] == "finish_totals"


def test_completion_rate_none_when_no_departures() -> None:
    collector = TrafficMetricsCollector("fixed")
    collector._finished = True
    result = collector.result(finished=True)
    assert result.completion_rate is None


def test_tripinfo_normal_override(tmp_path: Path) -> None:
    result = EvalResult(algorithm="mp", arrived=2, departed=2)
    result.warnings.append("终态平均行程时间和等待时间等待 TripInfo 回填。")
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' depart='0' arrival='10' duration='10' waitingTime='2'/>"
        "<tripinfo id='b' depart='1' arrival='11' duration='8' waitingTime='4'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s == pytest.approx(9.0)
    assert result.avg_waiting_time_s == pytest.approx(3.0)
    assert result.metric_sources["avg_travel_time_s"] == "tripinfo_completed"
    assert not any("等待 TripInfo" in w for w in result.warnings)


def test_tripinfo_no_completed_vehicles(tmp_path: Path) -> None:
    result = EvalResult(arrived=0)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' depart='0' arrival='-1' duration='5' waitingTime='1'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert any("没有已完成" in w for w in result.warnings)


def test_tripinfo_ignores_vaporized(tmp_path: Path) -> None:
    result = EvalResult(arrived=1)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='ok' depart='0' arrival='10' duration='10' waitingTime='2'/>"
        "<tripinfo id='vap' depart='0' arrival='8' duration='8' waitingTime='1' "
        "vaporized='true'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s == pytest.approx(10.0)
    assert result.avg_waiting_time_s == pytest.approx(2.0)


def test_tripinfo_count_mismatch(tmp_path: Path) -> None:
    result = EvalResult(arrived=2)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' depart='0' arrival='10' duration='10' waitingTime='2'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert any("不一致" in w for w in result.warnings)


def test_tripinfo_missing_file(tmp_path: Path) -> None:
    result = EvalResult(arrived=1)
    apply_tripinfo_completed_metrics(
        result,
        tmp_path / "missing.xml",
        expected_arrived=1,
        retries=1,
        delay_s=0.0,
    )
    assert result.avg_travel_time_s is None
    assert any("不存在" in w or "不可用" in w for w in result.warnings)


def test_live_provisional_then_finalize_clears_without_tripinfo() -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector.set_powertrain_by_type({"passenger": "gasoline"})
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=10.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=0),
            vehicles=(
                _Vehicle("v1", waiting_time=3.0, distance=50, fuel_total_ml=10),
            ),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=2)})
            },
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=20.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=1),
            vehicles=(),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=1)})
            },
        )
    )
    live = collector.result(finished=False, decision_latency_ms=1.2)
    assert live.avg_travel_time_s == pytest.approx(10.0)
    assert live.avg_waiting_time_s == pytest.approx(3.0)
    assert live.metric_sources["avg_travel_time_s"] == "snapshot_provisional"

    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=20.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=1),
            state="COMPLETED",
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=1)})
            },
        ),
        decision_latency_ms=1.2,
        tripinfo_path=None,
    )
    assert final.avg_travel_time_s is None
    assert final.avg_waiting_time_s is None
    assert final.avg_queue_length_veh == pytest.approx(1.5)
    assert final.throughput_veh_per_h == pytest.approx(180.0)
    assert final.completion_rate == pytest.approx(1.0)


def test_finalize_with_tripinfo(tmp_path: Path) -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector.set_powertrain_by_type({"passenger": "gasoline"})
    tripinfo = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='v1' depart='2' arrival='20' duration='8' waitingTime='3'/>",
    )
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=20.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=1),
            state="COMPLETED",
            vehicles=(),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=0)})
            },
        ),
        decision_latency_ms=2.0,
        tripinfo_path=tripinfo,
    )
    assert final.avg_travel_time_s == pytest.approx(8.0)
    assert final.avg_waiting_time_s == pytest.approx(3.0)
    assert final.avg_decision_latency_ms == pytest.approx(2.0)
    assert final.metric_sources["avg_travel_time_s"] == "tripinfo_completed"


def test_session_hub_payload_includes_sources_and_warnings(tmp_path: Path) -> None:
    hub = SessionMetricsHub(session_root=tmp_path)
    hub.start_session("ep", "fixed")
    hub.observe(
        _Snapshot(
            session_id="ep",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=0),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=3)})
            },
        )
    )
    payload = hub.get_metrics_payload("ep", decision_latency_ms=None)
    assert payload is not None
    assert payload["finished"] is False
    assert payload["avg_decision_latency_ms"] is None
    assert "metric_sources" in payload
    assert "warnings" in payload
    assert payload["avg_queue_length"] == pytest.approx(3.0)


def test_load_powertrain_from_manifests(tmp_path: Path) -> None:
    traffic = tmp_path / "traffic_manifest.json"
    traffic.write_text(
        """
        {
          "vehicle_profiles": {
            "passenger": {"powertrain": "gasoline"},
            "electric_bicycle": {"powertrain": "electric"}
          },
          "vehicle_type_profiles": {
            "official_passenger": "passenger",
            "official_electric_bicycle": "electric_bicycle"
          }
        }
        """,
        encoding="utf-8",
    )
    mapping, warnings = load_powertrain_by_type(traffic_manifest_path=traffic)
    assert mapping["official_passenger"] == "gasoline"
    assert mapping["official_electric_bicycle"] == "electric"
    assert warnings == []


def test_frontend_metrics_round_none_safely() -> None:
    payload = EvalResult(algorithm="fixed").to_frontend_metrics()
    assert payload["avg_travel_time"] is None
    assert payload["fuel_consumption"] is None
    assert payload["avg_decision_latency_ms"] is None
    assert payload["completion_rate"] is None
