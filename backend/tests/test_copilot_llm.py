"""Traffic Copilot 第 3 步：Qwen Provider 与只读工具循环测试。"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import pytest

from backend.app.copilot.llm import (
    AssistantMessage,
    LLMCompletion,
    LLMProtocolError,
    LLMUnavailableError,
    QwenProvider,
    ToolCall,
)
from backend.app.api.v1.copilot import _resolve_event_context
from backend.app.copilot.orchestrator import (
    CopilotLimitError,
    CopilotOrchestrator,
    _model_facing_tool_result,
)
from backend.app.copilot.traffic_tools import (
    InMemoryTrafficDataSource,
    TOOL_DEFINITIONS,
    TrafficToolService,
)


def _qwen_response(
    *,
    content: str | None = None,
    tool_calls: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = list(tool_calls)
    return {
        "id": "chatcmpl-test",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        "x_citypulse_latency_ms": 12.5,
    }


def test_qwen_provider_normalizes_openai_tool_call_and_request() -> None:
    captured: dict[str, Any] = {}

    def transport(
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        captured.update(
            endpoint=endpoint,
            payload=dict(payload),
            headers=dict(headers),
            timeout_seconds=timeout_seconds,
        )
        return _qwen_response(
            tool_calls=(
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_current_traffic",
                        "arguments": '{"intersection_id":"demo_1"}',
                    },
                },
            )
        )

    provider = QwenProvider(
        "http://qwen.internal:18000/v1",
        api_key="test-key",
        transport=transport,
    )
    completion = provider.complete(
        [{"role": "user", "content": "现在交通怎么样？"}],
        tools=TOOL_DEFINITIONS,
    )

    assert captured["endpoint"] == "http://qwen.internal:18000/v1/chat/completions"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["model"] == "Qwen/Qwen2.5-7B-Instruct"
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert completion.message.tool_calls[0].name == "get_current_traffic"
    assert completion.message.tool_calls[0].arguments == '{"intersection_id":"demo_1"}'
    assert completion.latency_ms == 12.5

    provider.complete(
        [
            {"role": "user", "content": "现在交通怎么样？"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_current_traffic",
                            "arguments": '{"intersection_id":"demo_1"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "get_current_traffic",
                "content": '{"ok":true}',
            },
        ],
        tools=TOOL_DEFINITIONS,
    )
    assert captured["payload"]["messages"][1]["tool_calls"][0]["function"][
        "arguments"
    ] == {"intersection_id": "demo_1"}


def test_qwen_provider_reports_transport_and_protocol_errors() -> None:
    def unavailable(
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        raise OSError("connection refused")

    with pytest.raises(LLMUnavailableError):
        QwenProvider(transport=unavailable).complete(
            [{"role": "user", "content": "你好"}]
        )

    with pytest.raises(LLMProtocolError):
        QwenProvider(transport=lambda *args: {}).complete(
            [{"role": "user", "content": "你好"}]
        )

    with pytest.raises(LLMProtocolError):
        QwenProvider(
            transport=lambda *args: _qwen_response(content={"answer": "不应展示"})
        ).complete([{"role": "user", "content": "你好"}])



class _SequenceProvider:
    def __init__(self, completions: Sequence[LLMCompletion]) -> None:
        self.completions = list(completions)
        self.requests: list[list[dict[str, Any]]] = []
        self.tools: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        self.requests.append([dict(message) for message in messages])
        self.tools.append([dict(item) for item in tools])
        assert [item["function"]["name"] for item in tools] == [
            item["function"]["name"] for item in TOOL_DEFINITIONS
        ]
        assert tool_choice == "auto"
        if not self.completions:
            raise AssertionError("provider called more times than expected")
        return self.completions.pop(0)


def _calculator_service() -> TrafficToolService:
    source = InMemoryTrafficDataSource(
        {
            "session-1": [
                {
                    "session_id": "session-1",
                    "elapsed_seconds": 10.0,
                    "intersections": {},
                    "events": [],
                    "event_detection": {"cards": []},
                    "traffic_style": {"edges": {}},
                }
            ]
        }
    )
    return TrafficToolService(source, session_id="session-1")


def test_event_context_uses_only_an_unambiguous_event_id() -> None:
    snapshot = {
        "events": [
            {"event_id": "event-1", "state": "ACTIVE"},
        ],
        "event_detection": {
            "cards": [
                {"event_id": "event-1", "status": "active"},
            ]
        },
    }

    assert _resolve_event_context(snapshot) == "event-1"
    assert _resolve_event_context(snapshot, "  explicit-event  ") == "explicit-event"
    assert _resolve_event_context(
        {
            "events": [
                {"event_id": "configured-accident", "state": "COMPLETED"},
            ],
            "event_detection": {
                "cards": [
                    {"event_id": "derived-card", "status": "active"},
                ]
            },
        }
    ) == "configured-accident"
    assert _resolve_event_context(
        {
            "events": [
                {"event_id": "event-1", "state": "ACTIVE"},
                {"event_id": "event-2", "state": "ACTIVE"},
            ]
        }
    ) is None


def test_model_facing_ai_status_exposes_one_effective_execution_state() -> None:
    result = _model_facing_tool_result(
        "get_ai_takeover_status",
        {
            "source": "get_ai_takeover_status",
            "scope": "session:session-1",
            "timestamp": 10.0,
            "data": {
                "available": True,
                "simulation_state": "PAUSED",
                "takeover_state": "ACTIVE",
                "control_active": False,
                "installed_plan_active": True,
                "ai_enabled": True,
                "active_event_id": "event-1",
            },
        },
    )

    assert result is not None
    data = result["data"]
    assert data["execution_state"] == "PLANNING_PAUSED"
    assert data["is_currently_executing"] is False
    assert "暂停" in data["execution_note"]


def test_model_facing_road_context_lists_only_direct_connections() -> None:
    result = _model_facing_tool_result(
        "get_road_context",
        {
            "source": "get_road_context",
            "scope": "intersection:demo_14",
            "timestamp": 10.0,
            "data": {
                "target": {"type": "intersection", "id": "demo_14"},
                "topology_available": True,
                "upstream_intersections": ["demo_19"],
                "downstream_intersections": ["demo_19"],
                "adjacent_intersections": ["demo_19"],
                "lanes": [],
                "connections": [],
            },
        },
    )

    assert result is not None
    data = result["data"]
    assert data["directly_connected_intersections"] == ["demo_19"]
    assert "corridor_neighbors" not in data
    assert "路径邻居" in data["connection_note"]


def _ai_takeover_service() -> TrafficToolService:
    source = InMemoryTrafficDataSource(
        {
            "session-1": [
                {
                    "session_id": "session-1",
                    "state": "RUNNING",
                    "elapsed_seconds": 10.0,
                    "intersections": {},
                    "events": [],
                    "event_detection": {"cards": []},
                    "traffic_style": {"edges": {}},
                    "ai_takeover": {
                        "state": "ACTIVE",
                        "ai_enabled": True,
                        "active_event_id": "event-1",
                        "allowed_scope": ["j1"],
                        "controlled_intersections": ["j1"],
                        "plan_sequence": 1,
                        "plan_id": "plan-1",
                        "plan_started_at": 5.0,
                        "plan_valid_until": 35.0,
                        "baseline_controller": "fixed",
                        "last_objective": "protect the blocked approach",
                        "last_reason": "keep the affected junction safe",
                        "last_plan": {
                            "event_id": "event-1",
                            "plan_id": "plan-1",
                            "sequence": 1,
                            "plan_started_at": 5.0,
                            "plan_valid_until": 35.0,
                            "controlled_intersections": ["j1"],
                            "target_phase_sequence": {"j1": [1, 0, 1, 0, 1, 0]},
                            "objective": "protect the blocked approach",
                            "reason": "keep the affected junction safe",
                        },
                    },
                }
            ]
        }
    )
    return TrafficToolService(source, session_id="session-1")


def test_orchestrator_runs_real_provider_protocol_with_read_only_tool() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            call_id="call-1",
                            name="calculator",
                            arguments='{"operation":"sum","values":[2,3]}',
                        ),
                    ),
                ),
                model="Qwen/Qwen2.5-7B-Instruct",
                usage={"prompt_tokens": 10, "completion_tokens": 2},
                latency_ms=100.0,
            ),
            LLMCompletion(
                message=AssistantMessage(content="计算结果是 5。"),
                model="Qwen/Qwen2.5-7B-Instruct",
                usage={"prompt_tokens": 20, "completion_tokens": 5},
                latency_ms=120.0,
            ),
        ]
    )
    orchestrator = CopilotOrchestrator(provider, _calculator_service())

    response = orchestrator.run(
        "帮我计算 2 加 3。",
        active_event_id="event-001",
        active_scope="intersection:demo_1",
    )

    assert response.answer == "计算结果是 5。"
    assert response.rounds == 2
    assert response.latency_ms == 220.0
    assert response.usage == {
        "prompt_tokens": 30,
        "completion_tokens": 7,
    }
    assert response.tool_calls[0].result is not None
    assert response.tool_calls[0].result["data"]["value"] == 5.0
    assert (
        "当前查询上下文中的事件 ID（前端传入或后端在单一事件会话中推断）：event-001"
        in provider.requests[0][0]["content"]
    )

    tool_message = provider.requests[1][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["result"]["data"]["value"] == 5.0
    assistant_message = provider.requests[1][-2]
    assert assistant_message["tool_calls"][0]["function"]["arguments"] == (
        '{"operation":"sum","values":[2,3]}'
    )


def test_orchestrator_can_answer_current_ai_takeover_from_status_tool() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="call-ai-status",
                            name="get_ai_takeover_status",
                            arguments="{}",
                        ),
                    )
                )
            ),
            LLMCompletion(message=AssistantMessage(content="AI 接管当前正在生效。")),
        ]
    )

    response = CopilotOrchestrator(provider, _ai_takeover_service()).run(
        "现在 AI 管控是否真正生效？"
    )

    assert response.answer == "AI 接管当前正在生效。"
    assert response.tool_calls[0].name == "get_ai_takeover_status"
    assert response.tool_calls[0].result["data"]["control_active"] is True
    assert response.tool_calls[0].result["data"]["installed_plan"][
        "target_phase_sequence"
    ]["j1"] == [1, 0, 1, 0, 1, 0]


def test_orchestrator_sends_compact_views_for_verbose_traffic_tools() -> None:
    orchestrator = CopilotOrchestrator(_SequenceProvider([]), _calculator_service())

    current_result = {
        "source": "get_current_traffic",
        "scope": "intersection:demo_14",
        "timestamp": 60.0,
        "data": {
            "as_of_seconds": 60.0,
            "model_summary": {
                "as_of_seconds": 60.0,
                "intersections": [
                    {
                        "intersection_id": "demo_14",
                        "totals": {"vehicle_count": 3},
                        "lanes": [
                            {"lane_id": "-52216_0", "vehicle_count": 0},
                            {"lane_id": "-52216_1", "vehicle_count": 2},
                        ],
                    }
                ],
            },
            "intersections": [{"verbose": True}],
            "lanes": [{"verbose": True}],
        },
    }
    current_message = orchestrator._tool_message(
        ToolCall("call-current", "get_current_traffic", {}),
        result=current_result,
        error=None,
    )
    current_data = json.loads(current_message["content"])["result"]["data"]
    assert current_data["model_view"] == "compact_current_traffic"
    assert current_data["model_summary"]["intersections"][0]["lanes"][0] == {
        "lane_id": "-52216_0",
        "vehicle_count": 0,
    }
    assert "intersections" not in current_data
    assert "lanes" not in current_data

    prediction_result = {
        "source": "get_prediction",
        "scope": "prediction:network",
        "timestamp": 60.0,
        "data": {
            "available": True,
            "supported_horizon_seconds": 60.0,
            "horizon_seconds": 60.0,
            "top_increases": [{"intersection_id": "demo_14", "delta": 5.0}],
            "intersections": [
                {"intersection_id": f"demo_{index}", "delta": float(index)}
                for index in range(1, 7)
            ],
        },
    }
    prediction_message = orchestrator._tool_message(
        ToolCall("call-prediction", "get_prediction", {}),
        result=prediction_result,
        error=None,
    )
    prediction_data = json.loads(prediction_message["content"])["result"]["data"]
    assert prediction_data["model_view"] == "network_prediction_summary"
    assert prediction_data["top_increases"] == [
        {"intersection_id": "demo_14", "delta": 5.0}
    ]
    assert "intersections" not in prediction_data


def test_orchestrator_rejects_unknown_write_tool_without_executing_it() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="call-write",
                            name="set_signal_phase",
                            arguments={"intersection_id": "demo_1"},
                        ),
                    )
                )
            ),
            LLMCompletion(message=AssistantMessage(content="当前只能提供只读分析。")),
        ]
    )

    response = CopilotOrchestrator(provider, _calculator_service()).run(
        "把 demo_1 的信号灯切到绿灯。"
    )

    assert response.answer == "当前只能提供只读分析。"
    assert response.tool_calls[0].error == {
        "code": "UNSUPPORTED_TOOL",
        "message": "该 Copilot 只允许调用固定的只读交通工具。",
    }
    assert "set_signal_phase" not in {
        item["function"]["name"] for item in provider.tools[0]
    }


def test_orchestrator_enforces_tool_call_limit() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall("call-1", "calculator", '{"operation":"sum","values":[1]}'),
                        ToolCall("call-2", "calculator", '{"operation":"sum","values":[2]}'),
                    )
                )
            )
        ]
    )
    with pytest.raises(CopilotLimitError) as exc_info:
        CopilotOrchestrator(
            provider,
            _calculator_service(),
            max_tool_calls=1,
        ).run("计算两个结果")
    assert exc_info.value.code == "COPILOT_TOOL_CALL_LIMIT"
