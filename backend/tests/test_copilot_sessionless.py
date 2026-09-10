"""Copilot sessionless mode and live-tool gating."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from backend.app.copilot.llm import AssistantMessage, LLMCompletion, ToolCall
from backend.app.copilot.orchestrator import (
    CopilotOrchestrator,
    SESSIONLESS_NO_LIVE_DATA_ANSWER,
)
from backend.app.copilot.traffic_tools import (
    InMemoryTrafficDataSource,
    LIVE_SESSION_TOOL_NAMES,
    SESSIONLESS_TOOL_NAMES,
    TrafficToolService,
    tool_definitions_for,
)


class _SequenceProvider:
    def __init__(self, completions: Sequence[LLMCompletion]) -> None:
        self.completions = list(completions)
        self.tools: list[Sequence[Mapping[str, Any]]] = []

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        self.tools.append(list(tools))
        if not self.completions:
            raise AssertionError("provider should not be called")
        return self.completions.pop(0)


def _sessionless_service() -> TrafficToolService:
    return TrafficToolService(InMemoryTrafficDataSource({}), session_id=None)


def test_sessionless_tool_definitions_exclude_live_traffic_tools() -> None:
    names = {item["function"]["name"] for item in tool_definitions_for(session_available=False)}
    assert names == SESSIONLESS_TOOL_NAMES
    assert LIVE_SESSION_TOOL_NAMES.isdisjoint(names)


def test_sessionless_live_traffic_question_does_not_call_the_model() -> None:
    provider = _SequenceProvider([])
    response = CopilotOrchestrator(
        provider,
        _sessionless_service(),
        session_available=False,
    ).run("现在雄安20个路口有多少辆车？")

    assert response.answer == SESSIONLESS_NO_LIVE_DATA_ANSWER
    assert response.tool_calls == ()
    assert provider.tools == []


def test_sessionless_knowledge_question_only_exposes_static_tools() -> None:
    provider = _SequenceProvider(
        [LLMCompletion(message=AssistantMessage(content="Max Pressure 根据上下游排队差选相位。"))]
    )
    response = CopilotOrchestrator(
        provider,
        _sessionless_service(),
        session_available=False,
    ).run("Max Pressure算法是什么？")

    assert "Max Pressure" in response.answer
    names = {item["function"]["name"] for item in provider.tools[0]}
    assert names == SESSIONLESS_TOOL_NAMES


def test_sessionless_orchestrator_blocks_live_tool_calls() -> None:
    provider = _SequenceProvider(
        [
            LLMCompletion(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall("call-live", "get_network_summary", "{}"),
                    )
                )
            ),
            LLMCompletion(message=AssistantMessage(content="暂无实时仿真数据。")),
        ]
    )
    response = CopilotOrchestrator(
        provider,
        _sessionless_service(),
        session_available=False,
    ).run("介绍一下固定配时")

    assert response.tool_calls[0].error == {
        "code": "SIMULATION_SESSION_REQUIRED",
        "message": SESSIONLESS_NO_LIVE_DATA_ANSWER,
    }
    assert "暂无实时仿真数据" in response.answer
