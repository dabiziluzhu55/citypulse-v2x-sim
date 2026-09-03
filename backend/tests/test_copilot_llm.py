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
from backend.app.copilot.orchestrator import (
    CopilotLimitError,
    CopilotOrchestrator,
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
    assert "当前前端选中的活动事件 ID：event-001" in provider.requests[0][0]["content"]

    tool_message = provider.requests[1][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["result"]["data"]["value"] == 5.0
    assistant_message = provider.requests[1][-2]
    assert assistant_message["tool_calls"][0]["function"]["arguments"] == (
        '{"operation":"sum","values":[2,3]}'
    )


def test_orchestrator_retries_placeholder_live_answer_until_tool_is_called() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(content="当前车辆数：[车辆数量]")
            ),
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            call_id="call-current",
                            name="get_current_traffic",
                            arguments={},
                        ),
                    )
                )
            ),
            LLMCompletion(
                message=AssistantMessage(content="当前仿真中暂无活动车辆。")
            ),
        ]
    )

    response = CopilotOrchestrator(provider, _calculator_service()).run(
        "当前路口的交通状态怎么样？"
    )

    assert response.answer == "当前仿真中暂无活动车辆。"
    assert response.rounds == 3
    assert {record.name for record in response.tool_calls} == {
        "get_network_summary",
        "get_current_traffic",
    }
    assert "禁止返回 JSON" in provider.requests[1][-1]["content"]


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
