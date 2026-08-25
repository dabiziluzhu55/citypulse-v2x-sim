"""后端评估指标单元测试：TripInfo 百公里油耗 + 急刹车率"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pytest

from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.metrics.collector import TrafficMetricsCollector
from backend.app.metrics.models import EvalResult
from backend.app.metrics.powertrain import (
    VehicleTypeFuelMeta,
    load_fuel_meta_by_type,
    load_powertrain_by_type,
)
from backend.app.metrics.session_hub import SessionMetricsHub
from backend.app.metrics.tripinfo import (
    apply_tripinfo_completed_metrics,
    apply_tripinfo_fuel_intensity,
)


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


def _fuel_meta() -> dict[str, VehicleTypeFuelMeta]:
    return {
        "passenger": VehicleTypeFuelMeta("gasoline", 745.0),
        "bus": VehicleTypeFuelMeta("diesel", 832.0),
        "hybrid_car": VehicleTypeFuelMeta("hybrid", 745.0),
        "official_electric_bicycle": VehicleTypeFuelMeta("electric", 1.0),
    }


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


def test_provisional_fuel_excludes_electric() -> None:
    collector = TrafficMetricsCollector("sotl")
    collector.set_fuel_meta_by_type(_fuel_meta())
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
    result = collector.result(finished=False)
    # (300ml /1000) / (3000m /100000) = 10
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)
    assert result.metric_sources["fuel_intensity_L_per_100km"] == "snapshot_provisional"


def test_provisional_fuel_skips_unknown_type_ids() -> None:
    collector = TrafficMetricsCollector("sotl")
    collector.set_fuel_meta_by_type(_fuel_meta())
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=3),
            vehicles=(
                _Vehicle("gas", type_id="passenger", distance=1000, fuel_total_ml=100),
                _Vehicle(
                    "evt",
                    type_id="citypulse_event_passenger",
                    distance=9000,
                    fuel_total_ml=999,
                ),
                _Vehicle(
                    "mystery",
                    type_id="mystery_car",
                    distance=5000,
                    fuel_total_ml=500,
                ),
            ),
        )
    )
    result = collector.result(finished=False)
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)
    assert result.metric_sources["fuel_intensity_L_per_100km"] == "snapshot_provisional"
    assert any("mystery_car" in w for w in result.warnings)
    assert any("citypulse_event_passenger" in w for w in result.warnings)


def test_provisional_fuel_none_when_only_unknown_types() -> None:
    collector = TrafficMetricsCollector("sotl")
    collector.set_fuel_meta_by_type(_fuel_meta())
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=1),
            vehicles=(
                _Vehicle("evt", type_id="mystery_car", distance=1000, fuel_total_ml=100),
            ),
        )
    )
    result = collector.result(finished=False)
    assert result.fuel_intensity_L_per_100km is None
    assert "fuel_intensity_L_per_100km" not in result.metric_sources
    assert any("mystery_car" in w for w in result.warnings)


def test_hub_reloads_session_manifest_for_unknown_type(tmp_path: Path) -> None:
    traffic = tmp_path / "traffic_manifest.json"
    traffic.write_text(
        json.dumps(
            {
                "vehicle_profiles": {
                    "passenger": {
                        "powertrain": "gasoline",
                        "fuel_density_mg_per_ml": 745.0,
                    }
                },
                "vehicle_type_profiles": {"official_passenger": "passenger"},
            }
        ),
        encoding="utf-8",
    )
    session_id = "late-manifest"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    hub = SessionMetricsHub(session_root=tmp_path, traffic_manifest_path=traffic)
    hub.start_session(session_id, "fixed")

    known = _Vehicle(
        "a", type_id="official_passenger", distance=1000, fuel_total_ml=100
    )
    late = _Vehicle("b", type_id="late_passenger", distance=1000, fuel_total_ml=300)
    hub.observe(
        _Snapshot(
            session_id=session_id,
            elapsed_seconds=1.0,
            metrics=_Metrics(departed_vehicles=2),
            vehicles=(known, late),
        )
    )
    before = hub.get_metrics_payload(session_id)
    assert before is not None
    assert before["fuel_consumption"] == pytest.approx(10.0)
    assert any("late_passenger" in w for w in before["warnings"])

    (session_dir / "session_manifest.json").write_text(
        json.dumps(
            {
                "vehicle_type_profiles": {
                    "official_passenger": "passenger",
                    "late_passenger": "passenger",
                }
            }
        ),
        encoding="utf-8",
    )
    hub.observe(
        _Snapshot(
            session_id=session_id,
            elapsed_seconds=2.0,
            metrics=_Metrics(departed_vehicles=2),
            vehicles=(known, late),
        )
    )
    after = hub.get_metrics_payload(session_id)
    assert after is not None
    assert after["fuel_consumption"] == pytest.approx(20.0)
    assert after["metric_sources"]["fuel_intensity_L_per_100km"] == "snapshot_provisional"


def test_hub_does_not_reread_unchanged_session_manifest(tmp_path: Path) -> None:
    traffic = tmp_path / "traffic_manifest.json"
    traffic.write_text(
        json.dumps(
            {
                "vehicle_profiles": {
                    "passenger": {
                        "powertrain": "gasoline",
                        "fuel_density_mg_per_ml": 745.0,
                    }
                },
                "vehicle_type_profiles": {"official_passenger": "passenger"},
            }
        ),
        encoding="utf-8",
    )
    session_id = "stable-manifest"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    (session_dir / "session_manifest.json").write_text(
        json.dumps({"vehicle_type_profiles": {"official_passenger": "passenger"}}),
        encoding="utf-8",
    )
    hub = SessionMetricsHub(session_root=tmp_path, traffic_manifest_path=traffic)
    hub.start_session(session_id, "fixed")
    first_mtime = hub._session_manifest_mtime[session_id]

    snapshot = _Snapshot(
        session_id=session_id,
        elapsed_seconds=1.0,
        metrics=_Metrics(departed_vehicles=2),
        vehicles=(
            _Vehicle("a", type_id="official_passenger", distance=1000, fuel_total_ml=100),
            _Vehicle("evt", type_id="citypulse_event_passenger", distance=10, fuel_total_ml=1),
        ),
    )
    hub.observe(snapshot)
    assert hub._session_manifest_mtime[session_id] == first_mtime
    live = hub.get_metrics_payload(session_id)
    assert live is not None
    assert live["fuel_consumption"] == pytest.approx(10.0)


def test_final_fuel_from_tripinfo_mixed_powertrains(tmp_path: Path) -> None:
    """汽油/柴油/混动计入；电动排除；不同密度换算正确。"""

    collector = TrafficMetricsCollector("sotl")
    collector.set_fuel_meta_by_type(_fuel_meta())
    # 快照临时值应被终态 TripInfo 覆盖，且不得影响终态正式结果
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=4, arrived_vehicles=0),
            vehicles=(
                _Vehicle("gas", type_id="passenger", distance=999, fuel_total_ml=1),
            ),
        )
    )
    tripinfo = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        # gas: 74500mg / 745 = 100ml, 1000m
        "<tripinfo id='gas' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>"
        # diesel: 83200mg / 832 = 100ml, 1000m
        "<tripinfo id='diesel' vType='bus' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'>"
        "<emissions fuel_abs='83200'/></tripinfo>"
        # hybrid: 74500mg / 745 = 100ml, 1000m
        "<tripinfo id='hybrid' vType='hybrid_car' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>"
        # electric excluded even with huge fuel_abs / distance
        "<tripinfo id='bike' vType='official_electric_bicycle' depart='0' "
        "arrival='10' duration='10' waitingTime='0' routeLength='9000'>"
        "<emissions fuel_abs='999999'/></tripinfo>",
    )
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=20.0,
            metrics=_Metrics(
                departed_vehicles=4,
                arrived_vehicles=4,
                hard_braking_events=0,
            ),
            state="COMPLETED",
            vehicles=(),
        ),
        decision_latency_ms=1.0,
        tripinfo_path=tripinfo,
    )
    # (300ml/1000) / (3000m/100000) = 10
    assert final.fuel_intensity_L_per_100km == pytest.approx(10.0)
    assert final.to_frontend_metrics()["fuel_consumption"] == pytest.approx(10.0)
    assert (
        final.metric_sources["fuel_intensity_L_per_100km"]
        == "tripinfo_completed_fuel_vehicles"
    )


def test_tripinfo_fuel_different_densities(tmp_path: Path) -> None:
    meta = {
        "passenger": VehicleTypeFuelMeta("gasoline", 745.0),
        "bus": VehicleTypeFuelMeta("diesel", 832.0),
    }
    result = EvalResult(arrived=2, departed=2)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' vType='passenger' depart='0' arrival='5' "
        "duration='5' waitingTime='0' routeLength='500'>"
        "<emissions fuel_abs='1490'/></tripinfo>"
        "<tripinfo id='b' vType='bus' depart='0' arrival='5' "
        "duration='5' waitingTime='0' routeLength='500'>"
        "<emissions fuel_abs='1664'/></tripinfo>",
    )
    apply_tripinfo_fuel_intensity(result, path, meta)
    # fuel_ml = 1490/745 + 1664/832 = 2 + 2 = 4ml; distance=1000m
    # (4/1000) / (1000/100000) = 0.004 / 0.01 = 0.4
    assert result.fuel_intensity_L_per_100km == pytest.approx(0.4)


def test_tripinfo_fuel_ignores_unfinished_and_vaporized(tmp_path: Path) -> None:
    meta = {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    result = EvalResult()
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='ok' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>"
        "<tripinfo id='unfin' vType='passenger' depart='0' arrival='-1' "
        "duration='5' waitingTime='1' routeLength='500'>"
        "<emissions fuel_abs='99999'/></tripinfo>"
        "<tripinfo id='vap' vType='passenger' depart='0' arrival='8' "
        "duration='8' waitingTime='1' routeLength='800' vaporized='true'>"
        "<emissions fuel_abs='99999'/></tripinfo>",
    )
    apply_tripinfo_fuel_intensity(result, path, meta)
    # only ok: 10ml / 1000m = 10 L/100km
    assert result.fuel_intensity_L_per_100km == pytest.approx(10.0)


def test_tripinfo_fuel_missing_emissions(tmp_path: Path) -> None:
    meta = {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    result = EvalResult()
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'/>",
    )
    apply_tripinfo_fuel_intensity(result, path, meta)
    assert result.fuel_intensity_L_per_100km is None
    assert any("emissions" in w for w in result.warnings)


def test_tripinfo_fuel_unknown_vtype_and_disturbance(tmp_path: Path) -> None:
    meta = {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}

    # 真正未知的官方外类型 → null
    unknown = EvalResult()
    apply_tripinfo_fuel_intensity(
        unknown,
        _write_tripinfo(
            tmp_path / "unknown.xml",
            "<tripinfo id='a' vType='mystery_car' depart='0' arrival='10' "
            "duration='10' waitingTime='1' routeLength='1000'>"
            "<emissions fuel_abs='74500'/></tripinfo>",
        ),
        meta,
    )
    assert unknown.fuel_intensity_L_per_100km is None
    assert any("未知" in w for w in unknown.warnings)

    # citypulse_* 扰动车跳过；与官方燃油车混合时不影响正式结果
    mixed = EvalResult()
    apply_tripinfo_fuel_intensity(
        mixed,
        _write_tripinfo(
            tmp_path / "mixed_unknown.xml",
            "<tripinfo id='evt' vType='citypulse_disturbance_vehicle' depart='0' "
            "arrival='10' duration='10' waitingTime='0' routeLength='9000'>"
            "<emissions fuel_abs='999999'/></tripinfo>"
            "<tripinfo id='ok' vType='passenger' depart='0' arrival='10' "
            "duration='10' waitingTime='1' routeLength='1000'>"
            "<emissions fuel_abs='74500'/></tripinfo>",
        ),
        meta,
    )
    assert mixed.fuel_intensity_L_per_100km == pytest.approx(10.0)

    # 空 vType 视为未知，整项不可用
    empty_type = EvalResult()
    apply_tripinfo_fuel_intensity(
        empty_type,
        _write_tripinfo(
            tmp_path / "empty_type.xml",
            "<tripinfo id='a' vType='' depart='0' arrival='10' "
            "duration='10' waitingTime='1' routeLength='1000'>"
            "<emissions fuel_abs='74500'/></tripinfo>",
        ),
        meta,
    )
    assert empty_type.fuel_intensity_L_per_100km is None
    assert any("空车辆类型" in w or "未知" in w for w in empty_type.warnings)


def test_tripinfo_fuel_illegal_density(tmp_path: Path) -> None:
    meta = {"passenger": VehicleTypeFuelMeta("gasoline", 0.0)}
    result = EvalResult()
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>",
    )
    apply_tripinfo_fuel_intensity(result, path, meta)
    assert result.fuel_intensity_L_per_100km is None
    assert any("密度" in w or "density" in w.lower() for w in result.warnings)


def test_tripinfo_fuel_zero_distance(tmp_path: Path) -> None:
    meta = {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    result = EvalResult()
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='1' routeLength='0'>"
        "<emissions fuel_abs='74500'/></tripinfo>",
    )
    apply_tripinfo_fuel_intensity(result, path, meta)
    assert result.fuel_intensity_L_per_100km is None
    assert any("里程" in w for w in result.warnings)


def test_tripinfo_fuel_missing_file(tmp_path: Path) -> None:
    result = EvalResult()
    apply_tripinfo_fuel_intensity(
        result,
        tmp_path / "missing.xml",
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)},
        retries=1,
        delay_s=0.0,
    )
    assert result.fuel_intensity_L_per_100km is None
    assert any("不存在" in w or "不可用" in w for w in result.warnings)


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
    result = collector.result(finished=False)
    assert result.fuel_intensity_L_per_100km is None
    assert any("powertrain" in w for w in result.warnings)


def test_hard_braking_not_summed_across_snapshots() -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=1.0,
            metrics=_Metrics(departed_vehicles=10, hard_braking_events=3),
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=2.0,
            metrics=_Metrics(departed_vehicles=10, hard_braking_events=5),
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=3.0,
            metrics=_Metrics(departed_vehicles=10, hard_braking_events=5),
        )
    )
    result = collector.result(finished=True, decision_latency_ms=0.1)
    # 单调累计：取最大值 5，禁止 3+5+5
    assert result.hard_braking_events == 5
    assert result.hard_braking_rate == pytest.approx(50.0)
    assert (
        result.metric_sources["hard_braking_rate"]
        == "final_snapshot_hard_braking_events_per_100_departed"
    )


def test_hard_braking_zero_events_returns_zero_not_null() -> None:
    collector = TrafficMetricsCollector("fixed")
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=10.0,
            metrics=_Metrics(
                departed_vehicles=20,
                arrived_vehicles=10,
                hard_braking_events=0,
            ),
            state="COMPLETED",
        ),
        decision_latency_ms=None,
        tripinfo_path=None,
    )
    assert final.hard_braking_events == 0
    assert final.hard_braking_rate == pytest.approx(0.0)


def test_hard_braking_rate_null_when_no_departures() -> None:
    collector = TrafficMetricsCollector("fixed")
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=10.0,
            metrics=_Metrics(
                departed_vehicles=0,
                arrived_vehicles=0,
                hard_braking_events=2,
            ),
            state="COMPLETED",
        ),
        tripinfo_path=None,
    )
    assert final.hard_braking_events == 2
    assert final.hard_braking_rate is None
    assert any("急刹车率" in w for w in final.warnings)


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
    assert result.metric_sources["avg_travel_time_s"] == "tripinfo_departed"
    assert not any("等待TripInfo回填" in w.replace(" ", "") for w in result.warnings)


def test_tripinfo_includes_unfinished(tmp_path: Path) -> None:
    """未到达车也计入：总和 / departed，避免只统计已到达的幸存者偏差"""

    result = EvalResult(arrived=1, departed=2)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='ok' depart='0' arrival='10' duration='10' waitingTime='2'/>"
        "<tripinfo id='stuck' depart='0' arrival='-1' duration='20' waitingTime='18'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s == pytest.approx(15.0)
    assert result.avg_waiting_time_s == pytest.approx(10.0)
    assert result.metric_sources["avg_travel_time_s"] == "tripinfo_departed"


def test_tripinfo_includes_vaporized(tmp_path: Path) -> None:
    result = EvalResult(arrived=1, departed=2)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='ok' depart='0' arrival='10' duration='10' waitingTime='2'/>"
        "<tripinfo id='vap' depart='0' arrival='-1' duration='8' waitingTime='1' "
        "vaporized='true'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s == pytest.approx(9.0)
    assert result.avg_waiting_time_s == pytest.approx(1.5)


def test_tripinfo_count_mismatch(tmp_path: Path) -> None:
    result = EvalResult(arrived=1, departed=2)
    path = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='a' depart='0' arrival='10' duration='10' waitingTime='2'/>",
    )
    apply_tripinfo_completed_metrics(result, path)
    assert result.avg_travel_time_s is None
    assert result.avg_waiting_time_s is None
    assert any("不一致" in w for w in result.warnings)


def test_tripinfo_missing_file(tmp_path: Path) -> None:
    result = EvalResult(arrived=1, departed=1)
    apply_tripinfo_completed_metrics(
        result,
        tmp_path / "missing.xml",
        expected_departed=1,
        retries=1,
        delay_s=0.0,
    )
    assert result.avg_travel_time_s is None
    assert any("不存在" in w or "不可用" in w for w in result.warnings)


def test_live_provisional_then_finalize_clears_without_tripinfo() -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector.set_fuel_meta_by_type(
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    )
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
    assert live.fuel_intensity_L_per_100km is not None

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
    assert final.fuel_intensity_L_per_100km is None
    assert final.avg_queue_length_veh == pytest.approx(1.5)
    assert final.throughput_veh_per_h == pytest.approx(180.0)
    assert final.completion_rate == pytest.approx(1.0)


def test_finalize_with_tripinfo(tmp_path: Path) -> None:
    collector = TrafficMetricsCollector("max_pressure")
    collector.set_fuel_meta_by_type(
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    )
    tripinfo = _write_tripinfo(
        tmp_path / "tripinfo.xml",
        "<tripinfo id='v1' vType='passenger' depart='2' arrival='20' "
        "duration='8' waitingTime='3' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>",
    )
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="s1",
            elapsed_seconds=20.0,
            metrics=_Metrics(
                departed_vehicles=1,
                arrived_vehicles=1,
                hard_braking_events=2,
            ),
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
    assert final.fuel_intensity_L_per_100km == pytest.approx(10.0)
    assert final.hard_braking_events == 2
    assert final.hard_braking_rate == pytest.approx(200.0)
    assert final.avg_decision_latency_ms == pytest.approx(2.0)
    assert final.metric_sources["avg_travel_time_s"] == "tripinfo_departed"
    assert (
        final.metric_sources["fuel_intensity_L_per_100km"]
        == "tripinfo_completed_fuel_vehicles"
    )


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


def test_active_metrics_ignore_finished_hint_until_finalize(tmp_path: Path) -> None:
    """仿真已终态但collector仍active时，不得把临时指标标成finished"""

    hub = SessionMetricsHub(session_root=tmp_path)
    hub.start_session("race", "max_pressure")
    hub.observe(
        _Snapshot(
            session_id="race",
            elapsed_seconds=5.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=0),
            vehicles=(
                _Vehicle("v1", waiting_time=4.0, distance=40.0, fuel_total_ml=5.0),
            ),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=2)})
            },
        )
    )
    hub.observe(
        _Snapshot(
            session_id="race",
            elapsed_seconds=10.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=1),
            vehicles=(),
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=1)})
            },
        )
    )
    premature = hub.get_metrics_payload(
        "race", decision_latency_ms=1.0, finished_hint=True
    )
    assert premature is not None
    assert premature["finished"] is False
    assert premature["metric_sources"]["avg_travel_time_s"] == "snapshot_provisional"
    assert premature["metric_sources"]["avg_waiting_time_s"] == "snapshot_provisional"
    assert hub.has_final_metrics("race") is False

    tripinfo_path = tmp_path / "race" / "tripinfo.xml"
    tripinfo_path.parent.mkdir(parents=True, exist_ok=True)
    tripinfo = _write_tripinfo(
        tripinfo_path,
        "<tripinfo id='v1' depart='0' arrival='10' duration='9' waitingTime='4'/>",
    )
    assert tripinfo.is_file()
    final = hub.finalize(
        _Snapshot(
            session_id="race",
            elapsed_seconds=20.0,
            metrics=_Metrics(
                departed_vehicles=1,
                arrived_vehicles=1,
                hard_braking_events=0,
            ),
            state="COMPLETED",
            intersections={
                "ix": _Intersection(lanes={"in": _Lane(halting_count=0)})
            },
        ),
        decision_latency_ms=1.0,
    )
    assert final.metric_sources["avg_travel_time_s"] == "tripinfo_departed"
    assert final.avg_travel_time_s == pytest.approx(9.0)
    assert final.avg_waiting_time_s == pytest.approx(4.0)

    done = hub.get_metrics_payload("race", finished_hint=True)
    assert done is not None
    assert done["finished"] is True
    assert done["metric_sources"]["avg_travel_time_s"] == "tripinfo_departed"
    assert done["metric_sources"]["avg_waiting_time_s"] == "tripinfo_departed"
    assert hub.has_final_metrics("race") is True


def test_session_hub_persist_and_restore_hard_braking_and_fuel(tmp_path: Path) -> None:
    class _Store:
        def __init__(self) -> None:
            self.data: dict[str, dict[str, Any]] = {}

        def save_metrics(self, session_id: str, payload: dict[str, Any]) -> None:
            self.data[session_id] = dict(payload)

        def get_metrics(self, session_id: str) -> dict[str, Any] | None:
            return self.data.get(session_id)

    store = _Store()
    hub = SessionMetricsHub(session_root=tmp_path, metadata_store=store)
    hub.start_session("persist", "sotl")
    (tmp_path / "persist").mkdir()
    _write_tripinfo(
        tmp_path / "persist" / "tripinfo.xml",
        "<tripinfo id='v1' vType='passenger' depart='0' arrival='10' "
        "duration='9' waitingTime='2' routeLength='1000'>"
        "<emissions fuel_abs='74500'/></tripinfo>",
    )
    # 无 traffic_manifest 时燃油元数据缺失；手工注入
    hub._active["persist"].set_fuel_meta_by_type(
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    )
    hub.finalize(
        _Snapshot(
            session_id="persist",
            elapsed_seconds=20.0,
            metrics=_Metrics(
                departed_vehicles=50,
                arrived_vehicles=1,
                hard_braking_events=7,
            ),
            state="COMPLETED",
        ),
        decision_latency_ms=0.5,
    )
    saved = store.get_metrics("persist")
    assert saved is not None
    assert saved["fuel_consumption"] == pytest.approx(10.0)
    assert saved["fuel_intensity_L_per_100km"] == pytest.approx(10.0)
    assert saved["hard_braking_events"] == 7
    assert saved["hard_braking_rate"] == pytest.approx(14.0)
    assert saved["finished"] is True

    hub2 = SessionMetricsHub(session_root=tmp_path, metadata_store=store)
    restored = hub2.get_metrics_payload("persist")
    assert restored is not None
    assert restored["finished"] is True
    assert restored["fuel_consumption"] == pytest.approx(10.0)
    assert restored["hard_braking_events"] == 7
    assert restored["hard_braking_rate"] == pytest.approx(14.0)
    assert (
        restored["metric_sources"]["fuel_intensity_L_per_100km"]
        == "tripinfo_completed_fuel_vehicles"
    )
    assert (
        restored["metric_sources"]["hard_braking_rate"]
        == "final_snapshot_hard_braking_events_per_100_departed"
    )


def test_serialize_terminal_snapshot_waits_for_delayed_tripinfo(
    tmp_path: Path,
) -> None:
    """终态快照先到、TripInfo稍后就绪：最终序列化须回填后再标记finished"""

    import threading
    import time
    from unittest.mock import MagicMock

    from backend.app.controllers.runtime import AlgorithmRuntimeStore
    from backend.app.core.config import Settings
    from backend.app.services.session_metadata import InMemorySessionMetadataStore
    from backend.app.services.simulation_service import SimulationService
    from backend.app.services.snapshot_serializer import SnapshotSerializer
    from simulation.sumo.engine.session import SessionMetrics, SimulationSnapshot

    session_root = tmp_path / "sessions"
    session_root.mkdir()
    session_id = "session-race-1"
    (session_root / session_id).mkdir()

    class _Mgr:
        def __init__(self) -> None:
            self._snap = SimulationSnapshot(
                session_id=session_id,
                state="RUNNING",
                sequence=1,
                elapsed_seconds=10.0,
                duration_seconds=20.0,
                progress=0.5,
                official_time="00:00:10",
                playback_speed=1.0,
                metrics=SessionMetrics(departed_vehicles=1, arrived_vehicles=0),
            )

        def snapshot(self, _sid: str) -> SimulationSnapshot:
            return self._snap

        def catalog(self):
            return MagicMock()

        def set_completed(self) -> SimulationSnapshot:
            self._snap = SimulationSnapshot(
                session_id=session_id,
                state="COMPLETED",
                sequence=2,
                elapsed_seconds=20.0,
                duration_seconds=20.0,
                progress=1.0,
                official_time="00:00:20",
                playback_speed=1.0,
                metrics=SessionMetrics(
                    departed_vehicles=1,
                    arrived_vehicles=1,
                    hard_braking_events=3,
                ),
            )
            return self._snap

    meta = InMemorySessionMetadataStore()
    meta.upsert(
        session_id,
        control_mode="max_pressure",
        scenario_preset_id="east_dense",
        state="RUNNING",
        metrics_status="collecting",
    )
    hub = SessionMetricsHub(session_root=session_root, metadata_store=meta)
    hub.start_session(session_id, "max_pressure")
    hub._active[session_id].set_fuel_meta_by_type(
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    )
    mgr = _Mgr()
    hub.observe(mgr.snapshot(session_id))

    premature = hub.get_metrics_payload(
        session_id, finished_hint=True, decision_latency_ms=None
    )
    assert premature is not None
    assert premature["finished"] is False

    tripinfo_path = session_root / session_id / "tripinfo.xml"

    def _write_later() -> None:
        time.sleep(0.35)
        _write_tripinfo(
            tripinfo_path,
            "<tripinfo id='v1' vType='passenger' depart='1' arrival='19' "
            "duration='11' waitingTime='2.5' routeLength='1000'>"
            "<emissions fuel_abs='74500'/></tripinfo>",
        )

    writer = threading.Thread(target=_write_later, daemon=True)
    writer.start()

    settings = Settings(
        simulation_manager_mode="local",
        sumo_session_root=str(session_root),
    )
    converter = MagicMock()
    converter.xy_to_lonlat.return_value = (116.0, 39.0)
    service = SimulationService(
        manager=mgr,
        serializer=SnapshotSerializer(converter),
        settings=settings,
        algorithm_store=AlgorithmRuntimeStore(),
        metrics_hub=hub,
        metadata_store=meta,
    )
    completed = mgr.set_completed()
    payload = service.serialize_terminal_snapshot(completed)
    writer.join(timeout=2.0)

    evaluation = payload["evaluation"]
    assert evaluation["finished"] is True
    assert evaluation["metric_sources"]["avg_travel_time_s"] == "tripinfo_departed"
    assert evaluation["metric_sources"]["avg_waiting_time_s"] == "tripinfo_departed"
    assert (
        evaluation["metric_sources"]["fuel_intensity_L_per_100km"]
        == "tripinfo_completed_fuel_vehicles"
    )
    assert evaluation["avg_travel_time"] == pytest.approx(11.0)
    assert evaluation["avg_waiting_time"] == pytest.approx(2.5)
    assert evaluation["fuel_consumption"] == pytest.approx(10.0)
    assert evaluation["hard_braking_events"] == 3
    assert evaluation["hard_braking_rate"] == pytest.approx(300.0)
    again = service.serialize_terminal_snapshot(completed)
    assert again["evaluation"]["finished"] is True
    assert again["evaluation"]["avg_travel_time"] == pytest.approx(11.0)
    assert again["evaluation"]["hard_braking_events"] == 3


def test_load_powertrain_from_manifests(tmp_path: Path) -> None:
    traffic = tmp_path / "traffic_manifest.json"
    traffic.write_text(
        """
        {
          "vehicle_profiles": {
            "passenger": {
              "powertrain": "gasoline",
              "fuel_density_mg_per_ml": 745.0
            },
            "electric_bicycle": {
              "powertrain": "electric",
              "fuel_density_mg_per_ml": 1.0
            }
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

    meta, meta_warnings = load_fuel_meta_by_type(traffic_manifest_path=traffic)
    assert meta["official_passenger"].fuel_density_mg_per_ml == pytest.approx(745.0)
    assert meta["official_electric_bicycle"].powertrain == "electric"
    assert meta_warnings == []


def test_frontend_metrics_round_none_safely() -> None:
    payload = EvalResult(algorithm="fixed").to_frontend_metrics()
    assert payload["avg_travel_time"] is None
    assert payload["fuel_consumption"] is None
    assert payload["fuel_intensity_L_per_100km"] is None
    assert payload["hard_braking_events"] is None
    assert payload["hard_braking_rate"] is None
    assert payload["avg_decision_latency_ms"] is None
    assert payload["completion_rate"] is None


def test_metrics_response_schema_accepts_new_fields() -> None:
    from backend.app.schemas.simulations import MetricsResponse

    model = MetricsResponse(
        episode_id="e1",
        algorithm="sotl",
        fuel_consumption=8.5,
        fuel_intensity_L_per_100km=8.5,
        hard_braking_events=0,
        hard_braking_rate=0.0,
        finished=True,
    )
    assert model.hard_braking_events == 0
    assert model.hard_braking_rate == 0.0
