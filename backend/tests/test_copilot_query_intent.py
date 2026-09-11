"""Copilot query intent routing, compact tool views, and live-answer guards."""

from __future__ import annotations

import json

from backend.app.copilot.llm import AssistantMessage, LLMCompletion, ToolCall
from backend.app.copilot.live_answers import guard_answer
from backend.app.copilot.orchestrator import CopilotOrchestrator, _model_facing_tool_result
from backend.app.copilot.query_intent import (
    CURRENT_TRAFFIC,
    KNOWLEDGE,
    LANE_COUNT,
    NETWORK_RISK,
    PREDICTION,
    route_query_intent,
)
from backend.app.copilot.traffic_tools import (
    InMemoryTrafficDataSource,
    TrafficToolService,
)
from backend.tests.test_copilot_llm import _SequenceProvider
from backend.tests.test_traffic_tools import _catalog, _snapshot, _topology


SLOW_PHRASE = "进口平均速度较低"


def _live_service(*, lane_count: int = 3) -> TrafficToolService:
    snapshot = _snapshot(10.0, worsening=True)
    extra_lanes = {
        f"L2_{index}": {
            "edge_id": f"E2_{index}",
            "vehicle_count": 1,
            "halting_count": 1 if index < 4 else 0,
            "mean_speed": 3.0 + index,
            "occupancy": 10.0,
            "waiting_time": 8.0,
            "role": "incoming",
            "approach_id": "west",
            "lane_has_green": True,
            "signal_state": "G",
            "downstream_lane_ids": [],
        }
        for index in range(lane_count)
    }
    snapshot["intersections"]["demo_2"]["lanes"] = extra_lanes
    snapshot["prediction"]["intersections"]["demo_2"] = {
        "current_vehicle_count": 32.0,
        "predicted_vehicle_count": 39.0,
        "delta": 7.0,
        "delta_ratio": 0.21875,
    }
    catalog = _catalog()
    catalog["intersections"]["demo_2"]["lanes"] = [
        {"lane_id": lane_id, "edge_id": payload["edge_id"], "role": "incoming"}
        for lane_id, payload in extra_lanes.items()
    ]
    return TrafficToolService(
        InMemoryTrafficDataSource(
            {"session-1": [snapshot]},
            catalog=catalog,
            topology=_topology(),
        ),
        session_id="session-1",
    )


def _answer_quality_ok(answer: str) -> None:
    assert not answer.strip().startswith("{")
    assert "tool_call" not in answer
    assert '"ok":' not in answer
    assert "demo_99" not in answer
    assert answer.count(SLOW_PHRASE) <= 2
    sentences = [item.strip() for item in answer.replace("！", "。").split("。") if item.strip()]
    repeats = 1
    for previous, current in zip(sentences, sentences[1:]):
        if previous == current:
            repeats += 1
            assert repeats < 3
        else:
            repeats = 1


def test_intent_router_maps_risk_prediction_lane_and_knowledge() -> None:
    assert route_query_intent("当前交通仿真出现的主要风险有哪些？").name == NETWORK_RISK
    prediction = route_query_intent("路口2未来的预测交通流会怎么变？")
    assert prediction.name == PREDICTION
    assert prediction.intersection_id == "demo_2"
    lane_count = route_query_intent("这个路口有几个车道？", active_scope="intersection:demo_2")
    assert lane_count.name == LANE_COUNT
    assert lane_count.intersection_id == "demo_2"
    assert route_query_intent("路口2有多少车道？").name == LANE_COUNT
    assert route_query_intent("路口2现在拥堵吗？").name == CURRENT_TRAFFIC
    assert route_query_intent("Max Pressure算法是什么？").name == KNOWLEDGE
    assert route_query_intent("Narrow-TDP是什么？").name == KNOWLEDGE
    assert route_query_intent("CoV2X是什么？").name == KNOWLEDGE
    assert route_query_intent("CityPulse-Qwen是什么？").name == KNOWLEDGE


def test_network_risk_uses_summary_tool_and_does_not_repeat() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    content=(SLOW_PHRASE + "。") * 20
                )
            )
        ]
    )
    response = CopilotOrchestrator(provider, _live_service()).run(
        "当前交通仿真出现的主要风险有哪些？"
    )
    names = [call.name for call in response.tool_calls]
    assert names == ["get_network_summary"]
    assert "get_current_traffic" not in names
    assert provider.requests == []
    assert "主要风险" in response.answer or "没有明显高风险" in response.answer
    _answer_quality_ok(response.answer)


def test_prediction_uses_horizon_and_formatter() -> None:
    response = CopilotOrchestrator(_SequenceProvider([]), _live_service()).run(
        "路口2未来的预测交通流会怎么变？"
    )
    assert [call.name for call in response.tool_calls] == ["get_prediction"]
    result = response.tool_calls[0].result["data"]
    assert result["horizon_seconds"] == 60.0
    assert result["intersections"][0]["current_vehicle_count"] == 32.0
    assert result["intersections"][0]["predicted_vehicle_count"] == 39.0
    assert "Narrow-TDP" in response.answer
    assert "32" in response.answer
    assert "39" in response.answer
    assert SLOW_PHRASE not in response.answer
    _answer_quality_ok(response.answer)


def test_lane_count_uses_road_context_not_live_lanes() -> None:
    service = _live_service(lane_count=20)
    response = CopilotOrchestrator(_SequenceProvider([]), service).run(
        "路口2有多少车道？"
    )
    assert [call.name for call in response.tool_calls] == ["get_road_context"]
    data = response.tool_calls[0].result["data"]
    assert data["lane_count"] == 20
    assert data["lane_count"] == len(data["lane_ids"])
    assert "20" in response.answer
    assert "km/h" not in response.answer
    assert SLOW_PHRASE not in response.answer
    _answer_quality_ok(response.answer)

    scoped = CopilotOrchestrator(_SequenceProvider([]), service).run(
        "这个路口有几个车道？",
        active_scope="intersection:demo_2",
    )
    assert [call.name for call in scoped.tool_calls] == ["get_road_context"]
    assert "20" in scoped.answer


def test_current_traffic_model_facing_payload_is_compact() -> None:
    service = _live_service(lane_count=20)
    full = service.execute("get_current_traffic", {"intersection_id": "demo_2"})
    full_summary = json.dumps(full["data"]["model_summary"], ensure_ascii=False)
    provider = _SequenceProvider(
        [LLMCompletion(message=AssistantMessage(content="路口2当前偏拥堵，进口排队较多。"))]
    )
    orchestrator = CopilotOrchestrator(provider, service)
    response = orchestrator.run("路口2现在拥堵吗？")
    assert [call.name for call in response.tool_calls] == ["get_current_traffic"]
    tool_message = next(
        message
        for message in provider.requests[0]
        if message.get("role") == "tool"
    )
    payload = json.loads(tool_message["content"])["result"]["data"]
    compact_text = json.dumps(payload, ensure_ascii=False)
    assert payload["lane_count"] == 20
    assert len(payload["top_slow_lanes"]) <= 3
    assert len(payload["top_queued_lanes"]) <= 3
    assert "lanes" not in payload
    assert len(compact_text) < len(full_summary)
    assert response.answer.count(SLOW_PHRASE) <= 2
    _answer_quality_ok(response.answer)
    test_current_traffic_model_facing_payload_is_compact.full_chars = len(full_summary)
    test_current_traffic_model_facing_payload_is_compact.compact_chars = len(compact_text)


def test_knowledge_query_still_uses_search_knowledge() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="call-rag",
                            name="search_knowledge",
                            arguments='{"query":"Max Pressure算法是什么","profile":"general"}',
                        ),
                    )
                )
            ),
            LLMCompletion(
                message=AssistantMessage(
                    content="Max Pressure 根据进口排队压力选择相位。"
                )
            ),
        ]
    )
    response = CopilotOrchestrator(provider, _live_service()).run(
        "Max Pressure算法是什么？"
    )
    assert [call.name for call in response.tool_calls] == ["search_knowledge"]
    assert "Max Pressure" in response.answer
    first_roles = [message.get("role") for message in provider.requests[0]]
    assert "tool" not in first_roles


def test_answer_guard_collapses_repeated_slow_lane_sentences() -> None:
    repeated = (SLOW_PHRASE + "，速度为 12.17 km/h。") * 12
    cleaned = guard_answer(repeated)
    assert cleaned.count(SLOW_PHRASE) <= 2
    assert "12.17" in cleaned


def test_model_facing_current_traffic_keeps_full_lanes_only_when_asked() -> None:
    lanes = [
        {
            "lane_id": f"L_{index}",
            "vehicle_count": index,
            "halting_count": index,
            "mean_speed_kmh": 30 - index,
        }
        for index in range(20)
    ]
    full = {
        "source": "get_current_traffic",
        "scope": "intersection:demo_2",
        "timestamp": 10.0,
        "data": {
            "as_of_seconds": 10.0,
            "model_summary": {
                "intersections": [
                    {
                        "intersection_id": "demo_2",
                        "current_phase": 1,
                        "totals": {"vehicle_count": 20},
                        "lanes": lanes,
                    }
                ]
            },
            "intersections": [{"lanes": lanes}],
            "lanes": lanes,
        },
    }
    compact = _model_facing_tool_result("get_current_traffic", full, question="路口2现在拥堵吗？")
    detailed = _model_facing_tool_result(
        "get_current_traffic",
        full,
        question="请给出路口2逐车道详情",
    )
    assert "lanes" not in compact["data"]
    assert len(detailed["data"]["lanes"]) == 20
