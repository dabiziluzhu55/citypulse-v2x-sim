"""典型场景评估口径：scene_metrics 与 network_metrics 双口径。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from backend.app.scenario.presets import SCENARIO_PRESET_REGISTRY
from backend.app.services.evaluation_report_service import build_evaluation_scope_caption
from backend.app.schemas.evaluation_reports import EvaluationReportScenario
from simulation.sumo.engine.evaluation_scope import build_evaluation_scope_payload
from simulation.sumo.engine.session import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    SimulationCatalog,
)
from traffic_eval.collector import TrafficMetricsCollector
from traffic_eval.models import EvalResult
from traffic_eval.powertrain import VehicleTypeFuelMeta
from traffic_eval.scope import build_evaluation_scope
from traffic_eval.tripinfo import apply_tripinfo_official_metrics


@dataclass
class _Lane:
    halting_count: int = 0
    role: str = "incoming"
    queue_length_m: float | None = 10.0
    lane_length_m: float | None = 100.0


@dataclass
class _Intersection:
    lanes: Mapping[str, _Lane]


@dataclass
class _Vehicle:
    vehicle_id: str
    type_id: str = "passenger"
    waiting_time: float = 0.0
    distance: float = 0.0
    fuel_total_ml: float = 0.0
    speed: float = 5.0
    road_id: str = ""
    lane_id: str = ""
    time_loss: float = 0.0
    hard_braking_events: int = 0


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
    vehicles: tuple[_Vehicle, ...] = ()
    intersections: Mapping[str, _Intersection] = field(default_factory=dict)
    evaluation_scope: Mapping[str, Any] | None = None


def _west_scope(**overrides: Any) -> dict[str, Any]:
    preset = SCENARIO_PRESET_REGISTRY["west_dense"]
    payload = {
        "preset_id": preset.preset_id,
        "intersection_ids": list(preset.intersection_ids),
        "lane_ids": ["west_in_0", "west_out_0"],
        "edge_ids": ["west_in", "west_out"],
        "covers_full_network": False,
    }
    payload.update(overrides)
    return payload


def _east_scope() -> dict[str, Any]:
    preset = SCENARIO_PRESET_REGISTRY["east_dense"]
    return {
        "preset_id": preset.preset_id,
        "intersection_ids": list(preset.intersection_ids),
        "lane_ids": ["east_in_0"],
        "edge_ids": ["east_in"],
        "covers_full_network": False,
    }


def _xiongan_scope() -> dict[str, Any]:
    preset = SCENARIO_PRESET_REGISTRY["xiongan_20"]
    return {
        "preset_id": preset.preset_id,
        "intersection_ids": list(preset.intersection_ids),
        "lane_ids": ["net_0"],
        "edge_ids": ["net"],
        "covers_full_network": True,
    }


def _scene_intersections(*intersection_ids: str) -> dict[str, _Intersection]:
    return {
        intersection_id: _Intersection(lanes={"in_0": _Lane(halting_count=2)})
        for intersection_id in intersection_ids
    }


def _collector() -> TrafficMetricsCollector:
    collector = TrafficMetricsCollector("fixed")
    collector.set_fuel_meta_by_type(
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)}
    )
    return collector


def test_west_dense_main_scope_matches_preset_intersections() -> None:
    preset = SCENARIO_PRESET_REGISTRY["west_dense"]
    scope = build_evaluation_scope(
        preset_id=preset.preset_id,
        intersection_ids=preset.intersection_ids,
        catalog_intersection_ids=SCENARIO_PRESET_REGISTRY["xiongan_20"].intersection_ids,
        lane_ids=("west_in_0",),
        edge_ids=("west_in",),
    )
    assert scope.intersection_ids == preset.intersection_ids
    assert scope.intersection_ids == ("demo_14", "demo_15", "demo_19")
    assert scope.covers_full_network is False

    collector = _collector()
    snapshot = _Snapshot(
        session_id="west",
        elapsed_seconds=5.0,
        metrics=_Metrics(departed_vehicles=1, arrived_vehicles=0),
        vehicles=(_Vehicle("v_in", lane_id="west_in_0", road_id="west_in"),),
        intersections=_scene_intersections(*preset.intersection_ids),
        evaluation_scope=scope.to_dict(),
    )
    collector.observe_snapshot(snapshot)
    result = collector.result(finished=False)
    assert result.evaluation_scope is not None
    assert result.evaluation_scope["intersection_ids"] == list(preset.intersection_ids)
    assert result.scene_metrics is not None
    assert result.network_metrics is not None
    assert result.scene_metrics["departed"] == result.departed


def test_east_dense_main_scope_matches_preset_intersections() -> None:
    preset = SCENARIO_PRESET_REGISTRY["east_dense"]
    scope = build_evaluation_scope(
        preset_id=preset.preset_id,
        intersection_ids=preset.intersection_ids,
        catalog_intersection_ids=SCENARIO_PRESET_REGISTRY["xiongan_20"].intersection_ids,
        lane_ids=("east_in_0",),
        edge_ids=("east_in",),
    )
    assert scope.intersection_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    collector = _collector()
    collector.observe_snapshot(
        _Snapshot(
            session_id="east",
            elapsed_seconds=4.0,
            metrics=_Metrics(departed_vehicles=1),
            vehicles=(_Vehicle("v_in", lane_id="east_in_0", road_id="east_in"),),
            intersections=_scene_intersections(*preset.intersection_ids),
            evaluation_scope=scope.to_dict(),
        )
    )
    result = collector.result(finished=False)
    assert result.evaluation_scope["intersection_ids"] == list(preset.intersection_ids)
    assert result.sample_sizes["intersection_count"] == 4


def test_outsider_does_not_affect_scene_metrics_but_enters_network() -> None:
    scope = _west_scope()
    collector = _collector()
    intersections = _scene_intersections("demo_14", "demo_15", "demo_19")
    outsider = _Vehicle(
        "v_out",
        waiting_time=40.0,
        distance=800.0,
        fuel_total_ml=80.0,
        lane_id="east_far_0",
        road_id="east_far",
        time_loss=20.0,
        hard_braking_events=4,
    )
    insider = _Vehicle(
        "v_in",
        waiting_time=5.0,
        distance=100.0,
        fuel_total_ml=10.0,
        lane_id="west_in_0",
        road_id="west_in",
        time_loss=2.0,
        hard_braking_events=1,
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="mix",
            elapsed_seconds=1.0,
            metrics=_Metrics(departed_vehicles=1, arrived_vehicles=0, hard_braking_events=4),
            vehicles=(outsider,),
            intersections=intersections,
            evaluation_scope=scope,
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="mix",
            elapsed_seconds=2.0,
            metrics=_Metrics(departed_vehicles=2, arrived_vehicles=0, hard_braking_events=5),
            vehicles=(
                _Vehicle(
                    "v_out",
                    waiting_time=80.0,
                    distance=900.0,
                    fuel_total_ml=90.0,
                    lane_id="east_far_0",
                    road_id="east_far",
                    time_loss=30.0,
                    hard_braking_events=5,
                ),
                insider,
            ),
            intersections=intersections,
            evaluation_scope=scope,
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="mix",
            elapsed_seconds=3.0,
            metrics=_Metrics(departed_vehicles=2, arrived_vehicles=1, hard_braking_events=6),
            vehicles=(
                _Vehicle(
                    "v_out",
                    waiting_time=120.0,
                    distance=1000.0,
                    fuel_total_ml=100.0,
                    lane_id="east_far_0",
                    road_id="east_far",
                    time_loss=40.0,
                    hard_braking_events=6,
                ),
                _Vehicle(
                    "v_in",
                    waiting_time=15.0,
                    distance=180.0,
                    fuel_total_ml=18.0,
                    lane_id="west_in_0",
                    road_id="west_in",
                    time_loss=6.0,
                    hard_braking_events=2,
                ),
            ),
            intersections=intersections,
            evaluation_scope=scope,
        )
    )
    result = collector.result(finished=False)
    assert result.departed == 1
    assert result.sample_sizes["scene_entered_vehicles"] == 1
    assert result.sample_sizes["network_departed"] == 2
    assert result.network_metrics["departed"] == 2
    assert result.scene_metrics["departed"] == 1
    assert result.avg_waiting_time_s is not None
    assert result.network_metrics["avg_waiting_time"] is not None
    assert result.avg_waiting_time_s < result.network_metrics["avg_waiting_time"]
    assert result.hard_braking_events == 1
    assert result.network_metrics["hard_braking_events"] == 6


def test_xiongan_20_keeps_full_network_behavior(tmp_path: Path) -> None:
    scope = _xiongan_scope()
    collector = _collector()
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos>"
        "<tripinfo id='a' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='4' routeLength='1000' timeLoss='2' waitingCount='1'>"
        "<emissions fuel_abs='74500'/></tripinfo>"
        "<tripinfo id='b' vType='passenger' depart='0' arrival='10' "
        "duration='20' waitingTime='6' routeLength='2000' timeLoss='4' waitingCount='2'>"
        "<emissions fuel_abs='149000'/></tripinfo>"
        "</tripinfos>",
        encoding="utf-8",
    )
    snapshot = _Snapshot(
        session_id="full",
        elapsed_seconds=20.0,
        metrics=_Metrics(departed_vehicles=2, arrived_vehicles=2, hard_braking_events=3),
        vehicles=(
            _Vehicle("a", lane_id="net_0", road_id="net", waiting_time=4, distance=1000),
            _Vehicle("b", lane_id="net_0", road_id="net", waiting_time=6, distance=2000),
        ),
        intersections=_scene_intersections("demo_1"),
        evaluation_scope=scope,
    )
    collector.observe_snapshot(snapshot)
    result = collector.finalize_from_snapshot(snapshot, tripinfo_path=tripinfo)
    assert result.evaluation_scope["covers_full_network"] is True
    assert result.departed == 2
    assert result.scene_metrics["departed"] == result.network_metrics["departed"] == 2
    assert result.avg_travel_time_s == 15.0
    assert result.scene_metrics["avg_travel_time"] == result.network_metrics["avg_travel_time"]
    assert result.sample_sizes["scene_entered_vehicles"] == 2
    assert result.sample_sizes["network_departed"] == 2


def test_missing_scope_keeps_legacy_full_network_path(tmp_path: Path) -> None:
    collector = _collector()
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos>"
        "<tripinfo id='only' vType='passenger' depart='0' arrival='8' "
        "duration='8' waitingTime='2' routeLength='800' timeLoss='1' waitingCount='1'>"
        "<emissions fuel_abs='59600'/></tripinfo>"
        "</tripinfos>",
        encoding="utf-8",
    )
    snapshot = _Snapshot(
        session_id="legacy",
        elapsed_seconds=8.0,
        metrics=_Metrics(departed_vehicles=1, arrived_vehicles=1),
        vehicles=(_Vehicle("only", waiting_time=2, distance=800),),
        intersections=_scene_intersections("demo_1"),
    )
    result = collector.finalize_from_snapshot(snapshot, tripinfo_path=tripinfo)
    assert result.evaluation_scope is None
    assert result.departed == 1
    assert result.avg_travel_time_s == 8.0
    assert result.scene_metrics["departed"] == result.network_metrics["departed"]


def test_scene_affected_tripinfo_filters_vehicles_that_never_entered(
    tmp_path: Path,
) -> None:
    collector = _collector()
    scope = _west_scope()
    intersections = _scene_intersections("demo_14", "demo_15", "demo_19")
    collector.observe_snapshot(
        _Snapshot(
            session_id="trip",
            elapsed_seconds=1.0,
            metrics=_Metrics(departed_vehicles=2, arrived_vehicles=0),
            vehicles=(
                _Vehicle("scene_car", lane_id="west_in_0", road_id="west_in"),
                _Vehicle("bypass_car", lane_id="east_far_0", road_id="east_far"),
            ),
            intersections=intersections,
            evaluation_scope=scope,
        )
    )
    collector.observe_snapshot(
        _Snapshot(
            session_id="trip",
            elapsed_seconds=2.0,
            metrics=_Metrics(departed_vehicles=2, arrived_vehicles=0),
            vehicles=(
                _Vehicle(
                    "scene_car",
                    waiting_time=3.0,
                    distance=50.0,
                    lane_id="west_in_0",
                    road_id="west_in",
                ),
                _Vehicle(
                    "bypass_car",
                    waiting_time=9.0,
                    distance=90.0,
                    lane_id="east_far_0",
                    road_id="east_far",
                ),
            ),
            intersections=intersections,
            evaluation_scope=scope,
        )
    )
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos>"
        "<tripinfo id='scene_car' vType='passenger' depart='0' arrival='10' "
        "duration='10' waitingTime='3' routeLength='100' timeLoss='1' waitingCount='1'>"
        "<emissions fuel_abs='7450'/></tripinfo>"
        "<tripinfo id='bypass_car' vType='passenger' depart='0' arrival='40' "
        "duration='40' waitingTime='30' routeLength='4000' timeLoss='20' waitingCount='8'>"
        "<emissions fuel_abs='298000'/></tripinfo>"
        "</tripinfos>",
        encoding="utf-8",
    )
    final = collector.finalize_from_snapshot(
        _Snapshot(
            session_id="trip",
            elapsed_seconds=10.0,
            metrics=_Metrics(departed_vehicles=2, arrived_vehicles=2),
            vehicles=(),
            intersections=intersections,
            evaluation_scope=scope,
        ),
        tripinfo_path=tripinfo,
    )
    assert final.departed == 1
    assert final.network_metrics["departed"] == 2
    assert final.scene_metrics["departed"] == 1
    affected = final.scene_affected_trip_metrics
    assert affected is not None
    assert affected["metric_kind"] == "scene_affected_trip_metrics"
    assert affected["sample_vehicle_count"] == 1
    assert affected["avg_travel_time"] == 10.0
    assert "整段行程" in affected["note"]
    assert str(affected["metric_sources"]["avg_travel_time_s"]).startswith(
        "scene_affected_trip_metrics:"
    )
    assert final.metric_sources.get("avg_travel_time_s") != "tripinfo_departed"


def test_tripinfo_vehicle_ids_filter_ignores_unrelated_trips(tmp_path: Path) -> None:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text(
        "<tripinfos>"
        "<tripinfo id='keep' vType='passenger' depart='0' arrival='5' "
        "duration='5' waitingTime='1' routeLength='500' timeLoss='1' waitingCount='0'>"
        "<emissions fuel_abs='37250'/></tripinfo>"
        "<tripinfo id='drop' vType='passenger' depart='0' arrival='50' "
        "duration='50' waitingTime='40' routeLength='5000' timeLoss='20' waitingCount='9'>"
        "<emissions fuel_abs='372500'/></tripinfo>"
        "</tripinfos>",
        encoding="utf-8",
    )
    result = EvalResult(algorithm="fixed", departed=1)
    apply_tripinfo_official_metrics(
        result,
        tripinfo,
        {"passenger": VehicleTypeFuelMeta("gasoline", 745.0)},
        vehicle_ids=("keep",),
    )
    assert result.avg_travel_time_s == 5.0
    assert result.avg_waiting_time_s == 1.0


def test_session_payload_resolves_lanes_from_catalog_without_eval_hardcode() -> None:
    preset = SCENARIO_PRESET_REGISTRY["west_dense"]
    catalog = SimulationCatalog(
        intersections={
            intersection_id: IntersectionCapability(
                intersection_id=intersection_id,
                longitude=116.0,
                latitude=38.9,
                periods=("morning_peak",),
                origins=(
                    OriginCapability(
                        origin_id="incoming",
                        label="Incoming",
                        lane_ids=(f"{intersection_id}_in_0",),
                    ),
                ),
                lanes=(
                    LaneCapability(
                        lane_id=f"{intersection_id}_in_0",
                        edge_id=f"{intersection_id}_in",
                        lane_index=0,
                        role="incoming",
                        approach="west",
                        approach_label="West",
                        length=80.0,
                        max_speed=13.9,
                    ),
                ),
            )
            for intersection_id in preset.intersection_ids
        }
        | {
            "demo_1": IntersectionCapability(
                intersection_id="demo_1",
                longitude=116.0,
                latitude=38.9,
                periods=("morning_peak",),
                origins=(),
                lanes=(
                    LaneCapability(
                        lane_id="demo_1_in_0",
                        edge_id="demo_1_in",
                        lane_index=0,
                        role="incoming",
                        approach="east",
                        approach_label="East",
                        length=80.0,
                        max_speed=13.9,
                    ),
                ),
            )
        }
    )
    payload = build_evaluation_scope_payload(
        preset_id=preset.preset_id,
        intersection_ids=preset.intersection_ids,
        catalog_intersections=catalog.intersections,
    )
    assert payload["preset_id"] == "west_dense"
    assert payload["intersection_ids"] == list(preset.intersection_ids)
    assert payload["covers_full_network"] is False
    assert "demo_14_in_0" in payload["lane_ids"]
    assert "demo_1_in_0" not in payload["lane_ids"]


def test_traffic_eval_does_not_hardcode_demo_intersection_ids() -> None:
    root = Path(__file__).resolve().parents[2] / "traffic_eval"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "demo_14" not in text
        assert "demo_15" not in text
        assert "demo_19" not in text
        assert "demo_3" not in text


def test_report_caption_states_object_intersections_and_samples() -> None:
    caption = build_evaluation_scope_caption(
        EvaluationReportScenario(
            scenario_preset_id="west_dense",
            period="morning_peak",
            window_start_seconds=0,
            duration_seconds=900,
        ),
        {
            "evaluation_scope": _west_scope(),
            "sample_sizes": {
                "scene_entered_vehicles": 12,
                "scene_exited_vehicles": 9,
                "network_departed": 80,
                "network_arrived": 70,
            },
        },
    )
    assert "窄路密网片区场景" in caption
    assert "demo_14" in caption and "demo_15" in caption and "demo_19" in caption
    assert "场景进入 12 辆" in caption
    assert "全网出发 80 辆" in caption
