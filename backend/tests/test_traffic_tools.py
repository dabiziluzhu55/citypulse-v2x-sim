"""Traffic Copilot 第 2 步：只读工具契约与固定快照测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.copilot.traffic_tools import (
    InMemoryTrafficDataSource,
    RoadTopology,
    TOOL_DEFINITIONS,
    ToolInputError,
    TrafficToolService,
)


def _lane(
    *,
    edge_id: str,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy: float,
    waiting_time: float,
    downstream_lane_ids: tuple[str, ...] = (),
    approach_id: str = "west",
) -> dict:
    return {
        "edge_id": edge_id,
        "vehicle_count": vehicle_count,
        "halting_count": halting_count,
        "mean_speed": mean_speed,
        "occupancy": occupancy,
        "waiting_time": waiting_time,
        "role": "incoming",
        "approach_id": approach_id,
        "lane_has_green": True,
        "signal_state": "G",
        "downstream_lane_ids": list(downstream_lane_ids),
    }


def _snapshot(elapsed: float, *, worsening: bool) -> dict:
    lane_1 = _lane(
        edge_id="E1",
        vehicle_count=6 if worsening else 4,
        halting_count=3 if worsening else 1,
        mean_speed=5.0 if worsening else 8.0,
        occupancy=20.0 if worsening else 10.0,
        waiting_time=20.0 if worsening else 5.0,
        downstream_lane_ids=("L2_0",),
    )
    lane_2 = _lane(
        edge_id="E2",
        vehicle_count=3 if worsening else 2,
        halting_count=1,
        mean_speed=9.0,
        occupancy=8.0,
        waiting_time=4.0,
        approach_id="east",
    )
    lane_3 = _lane(
        edge_id="E3",
        vehicle_count=1,
        halting_count=0,
        mean_speed=12.0,
        occupancy=2.0,
        waiting_time=0.0,
        approach_id="south",
    )
    return {
        "session_id": "session-1",
        "state": "RUNNING",
        "sequence": int(elapsed),
        "elapsed_seconds": elapsed,
        "official_time": f"t{int(elapsed)}",
        "intersections": {
            "demo_1": {
                "current_phase": 1,
                "pending_phase": 2,
                "stage": "through",
                "stage_elapsed": elapsed,
                "lanes": {"L1_0": lane_1, "L1_1": lane_2},
            },
            "demo_2": {
                "current_phase": 2,
                "pending_phase": None,
                "stage": "left",
                "stage_elapsed": 3.0,
                "lanes": {"L3_0": lane_3},
            },
        },
        "events": [
            {
                "event_id": "dist-1",
                "event_type": "lane_closure",
                "state": "ACTIVE",
                "start_seconds": 2.0,
                "end_seconds": 30.0,
                "error": None,
                "details": {"lane_ids": ["L1_0"]},
            }
        ],
        "event_detection": {
            "as_of_seconds": elapsed,
            "cards": [
                {
                    "event_id": "det-1",
                    "status": "active",
                    "event_type": "lane_blocked",
                    "traffic_state": "localized_blockage",
                    "cause": "unknown",
                    "cause_confidence": 0.0,
                    "intersection_id": "demo_1",
                    "lane_ids": ["L1_0"],
                    "edge_id": "E1",
                    "severity": "medium",
                    "confidence": 0.8,
                    "start_seconds": 2.0,
                    "end_seconds": None,
                    "duration_seconds": max(0.0, elapsed - 2.0),
                    "evidence": ["停车车辆数偏高"],
                    "suggestion": "关注该进口车道",
                }
            ],
        },
        "prediction": {
            "horizon_seconds": 60.0,
            "as_of_seconds": elapsed,
            "model": "NarrowNet-TDP",
            "model_version": "test-v1",
            "ready": True,
            "fallback": False,
            "fallback_reason": "",
            "inference_latency_ms": 4.2,
            "intersections": {
                "demo_1": {
                    "current_vehicle_count": 9.0 if worsening else 6.0,
                    "predicted_vehicle_count": 13.0 if worsening else 8.0,
                    "delta": 4.0 if worsening else 2.0,
                    "delta_ratio": 0.4444 if worsening else 0.3333,
                },
                "demo_2": {
                    "current_vehicle_count": 1.0,
                    "predicted_vehicle_count": 1.0,
                    "delta": 0.0,
                    "delta_ratio": 0.0,
                },
            },
        },
        "traffic_style": {
            "as_of_seconds": elapsed,
            "edges": {
                "E1": {
                    "level": "congested" if worsening else "slow",
                    "score": 0.75 if worsening else 0.45,
                    "mean_speed": 5.0 if worsening else 8.0,
                    "occupancy_pct": 20.0 if worsening else 10.0,
                    "vehicle_count": 6 if worsening else 4,
                    "halting_count": 3 if worsening else 1,
                },
                "E2": {
                    "level": "slow",
                    "score": 0.45,
                    "mean_speed": 9.0,
                    "occupancy_pct": 8.0,
                    "vehicle_count": 3 if worsening else 2,
                    "halting_count": 1,
                },
                "E3": {
                    "level": "free",
                    "score": 0.0,
                    "mean_speed": 12.0,
                    "occupancy_pct": 2.0,
                    "vehicle_count": 1,
                    "halting_count": 0,
                },
            },
        },
    }


def _catalog() -> dict:
    return {
        "intersections": {
            "demo_1": {
                "lanes": [
                    {
                        "lane_id": "L1_0",
                        "edge_id": "E1",
                        "lane_index": 0,
                        "role": "incoming",
                        "approach": "west",
                        "approach_label": "West",
                        "length": 100.0,
                        "max_speed": 13.9,
                    },
                    {
                        "lane_id": "L1_1",
                        "edge_id": "E2",
                        "lane_index": 1,
                        "role": "incoming",
                        "approach": "east",
                        "approach_label": "East",
                        "length": 90.0,
                        "max_speed": 13.9,
                    },
                ]
            },
            "demo_2": {
                "lanes": [
                    {
                        "lane_id": "L3_0",
                        "edge_id": "E3",
                        "lane_index": 0,
                        "role": "incoming",
                        "approach": "south",
                        "approach_label": "South",
                        "length": 110.0,
                        "max_speed": 13.9,
                    }
                ]
            },
        }
    }


def _topology() -> RoadTopology:
    return RoadTopology(
        lane_to_intersection={"L1_0": "demo_1", "L1_1": "demo_1", "L2_0": "demo_2"},
        downstream_by_lane={"L1_0": ("L2_0",)},
        upstream_by_lane={"L2_0": ("L1_0",)},
        upstream_intersections={"demo_1": (), "demo_2": ("demo_1",)},
        downstream_intersections={"demo_1": ("demo_2",), "demo_2": ()},
        adjacent_intersections={"demo_1": ("demo_2",), "demo_2": ("demo_1",)},
        connections_by_intersection={
            "demo_1": (
                {
                    "connection_id": "demo_1:0",
                    "intersection_id": "demo_1",
                    "approach": "west",
                    "movement": "through",
                    "from_lane": "L1_0",
                    "to_lane": "L2_0",
                    "direction": "s",
                },
            )
        },
    )


@pytest.fixture
def service() -> TrafficToolService:
    source = InMemoryTrafficDataSource(
        {"session-1": [_snapshot(0.0, worsening=False), _snapshot(10.0, worsening=True)]},
        catalog=_catalog(),
        topology=_topology(),
        knowledge_documents=(
            {
                "document_id": "rule-1",
                "title": "拥堵事件处置原则",
                "content": "先核实当前交通状态，再结合上游和下游排队情况制定建议。",
                "tags": ["拥堵", "处置"],
                "source": "project-rule",
            },
        ),
    )
    return TrafficToolService(source, session_id="session-1")


def test_tool_definitions_are_fixed_read_only_tools() -> None:
    names = [item["function"]["name"] for item in TOOL_DEFINITIONS]
    assert names == [
        "get_event_details",
        "get_current_traffic",
        "get_traffic_history",
        "get_prediction",
        "get_network_summary",
        "get_road_context",
        "search_knowledge",
        "calculator",
    ]
    assert all("write" not in name and "control" not in name for name in names)


def test_current_traffic_uses_existing_snapshot_shape(service: TrafficToolService) -> None:
    result = service.execute("get_current_traffic", {"intersection_id": "demo_1"})
    assert set(result) == {"source", "scope", "timestamp", "data"}
    assert result["source"] == "get_current_traffic"
    assert result["timestamp"] == 10.0
    intersection = result["data"]["intersections"][0]
    assert intersection["totals"]["vehicle_count"] == 9
    assert intersection["totals"]["halting_count"] == 4
    assert intersection["totals"]["congestion_level"] == "congested"
    assert result["data"]["lanes"][0]["mean_speed_kmh"] > 0


def test_event_details_merges_detection_and_injected_event(service: TrafficToolService) -> None:
    result = service.execute(
        "get_event_details",
        {"event_ids": ["det-1", "dist-1", "missing"]},
    )
    assert result["data"]["count"] == 2
    assert result["data"]["not_found"] == ["missing"]
    detected = next(item for item in result["data"]["events"] if item["event_id"] == "det-1")
    injected = next(item for item in result["data"]["events"] if item["event_id"] == "dist-1")
    assert detected["traffic_state"] == "localized_blockage"
    assert detected["cause_status"] == "unconfirmed"
    assert injected["source"] == "disturbance"
    assert injected["lane_ids"] == ["L1_0"]


def test_history_returns_series_and_observed_trend(service: TrafficToolService) -> None:
    result = service.execute(
        "get_traffic_history",
        {"intersection_ids": ["demo_1"], "lookback_seconds": 60},
    )
    assert result["data"]["sample_count"] == 2
    assert len(result["data"]["series"]) == 2
    assert result["data"]["trends"][0]["direction"] == "worsening"
    assert result["data"]["trends"][0]["halting_count_delta"] == 2


def test_history_supports_metric_selection_and_explicit_time_range(
    service: TrafficToolService,
) -> None:
    result = service.execute(
        "get_traffic_history",
        {
            "intersection_ids": ["demo_1"],
            "from_seconds": 0,
            "to_seconds": 10,
            "metrics": ["vehicle_count", "prediction"],
        },
    )
    assert result["scope"] == "intersection:demo_1;range:explicit"
    assert result["data"]["metrics"] == ["vehicle_count", "prediction"]
    assert result["data"]["sample_count"] == 2
    assert set(result["data"]["series"][0]) == {
        "scope",
        "timestamp",
        "vehicle_count",
        "prediction",
    }
    assert result["data"]["series"][0]["prediction"]["predicted_vehicle_count"] == 8.0


def test_history_rejects_mixed_window_and_invalid_metric(
    service: TrafficToolService,
) -> None:
    with pytest.raises(ToolInputError):
        service.execute(
            "get_traffic_history",
            {
                "intersection_ids": ["demo_1"],
                "lookback_seconds": 60,
                "from_seconds": 0,
            },
        )
    with pytest.raises(ToolInputError):
        service.execute(
            "get_traffic_history",
            {"intersection_ids": ["demo_1"], "metrics": ["not_a_metric"]},
        )


def test_prediction_and_network_summary_are_read_only(service: TrafficToolService) -> None:
    prediction = service.execute("get_prediction", {"intersection_id": "demo_1"})
    assert prediction["data"]["model"] == "NarrowNet-TDP"
    assert prediction["data"]["intersections"][0]["trend"] == "increasing"
    assert prediction["data"]["intersections"][0]["risk"] == "medium"

    summary = service.execute("get_network_summary", {})
    assert summary["data"]["active_event_count"] == 2
    assert summary["data"]["traffic_levels"]["congested"] == 1
    assert summary["data"]["hotspot_intersections"][0]["intersection_id"] == "demo_1"
    assert summary["data"]["network_trend"]["direction"] == "increasing"


def test_road_context_knowledge_and_calculator(service: TrafficToolService) -> None:
    context = service.execute("get_road_context", {"lane_id": "L1_0"})
    assert context["data"]["target"]["intersection_id"] == "demo_1"
    assert context["data"]["downstream_intersections"] == ["demo_2"]
    lane = next(item for item in context["data"]["lanes"] if item["lane_id"] == "L1_0")
    assert lane["downstream_lane_ids"] == ["L2_0"]
    assert lane["length_m"] == 100.0

    knowledge = service.execute("search_knowledge", {"query": "拥堵 处置"})
    assert knowledge["data"]["matched_count"] == 1
    assert knowledge["data"]["results"][0]["document_id"] == "rule-1"

    calculation = service.execute(
        "calculator",
        {"operation": "percentage_change", "values": [10, 15]},
    )
    assert calculation["data"]["value"] == 50.0


def test_invalid_input_is_rejected_before_data_access(service: TrafficToolService) -> None:
    with pytest.raises(ToolInputError):
        service.execute("get_current_traffic", {})
    with pytest.raises(ToolInputError):
        service.execute("get_current_traffic", {"intersection_id": "demo_1", "extra": 1})
    with pytest.raises(ToolInputError):
        service.execute("calculator", {"operation": "percentage_change", "values": [0, 1]})
    with pytest.raises(ToolInputError):
        service.execute("not_a_tool", {})


def test_real_tls_manifest_builds_cross_intersection_topology() -> None:
    manifest = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "maps"
        / "sumo"
        / "generated"
        / "manifests"
        / "tls_manifest.json"
    )
    topology = RoadTopology.from_tls_manifest(manifest)
    assert "demo_1" in topology.connections_by_intersection
    assert topology.connections_by_intersection["demo_1"]
    assert topology.lane_to_intersection
    assert "-56384_0" in topology.downstream_by_lane
