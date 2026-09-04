"""Traffic Copilot 第 4 步：FastAPI 路由与错误边界测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from backend.app.api.v1.copilot import chat, router
from backend.app.copilot.llm import (
    AssistantMessage,
    LLMCompletion,
    LLMUnavailableError,
)
from backend.app.core.exceptions import AppError
from backend.app.schemas.copilot import CopilotChatRequest


class _Provider:
    def __init__(self, completion: LLMCompletion | Exception) -> None:
        self.completion = completion

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        if isinstance(self.completion, Exception):
            raise self.completion
        return self.completion


class _SimulationService:
    def __init__(self) -> None:
        self.snapshot_calls: list[str] = []

    def snapshot(self, session_id: str) -> dict[str, Any]:
        self.snapshot_calls.append(session_id)
        return {"session_id": session_id}


def _request() -> SimpleNamespace:
    settings = SimpleNamespace(
        copilot_max_rounds=4,
        copilot_max_tool_calls=8,
        copilot_max_tool_result_chars=20_000,
    )
    state = SimpleNamespace(settings=settings, copilot_topology=None)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_copilot_route_returns_answer_and_binds_session() -> None:
    # 该测试只验证 HTTP 层绑定和响应模型，因此使用可直接回答的 Provider。
    provider = _Provider(LLMCompletion(message=AssistantMessage(content="可以查询。")))
    service = _SimulationService()

    response = chat(
        "session-001",
        CopilotChatRequest(
            message="你支持查询哪些内容？",
            active_event_id="event-001",
            active_scope="intersection:demo_1",
        ),
        _request(),
        service,
        provider,
    )

    assert response.session_id == "session-001"
    assert response.answer == "可以查询。"
    assert response.rounds == 1
    assert service.snapshot_calls == ["session-001"]


def test_copilot_route_maps_qwen_unavailable_to_503() -> None:
    service = _SimulationService()
    provider = _Provider(LLMUnavailableError("connection refused"))

    with pytest.raises(AppError) as exc_info:
        chat(
            "session-001",
            CopilotChatRequest(message="现在交通怎么样？"),
            _request(),
            service,
            provider,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == "COPILOT_LLM_UNAVAILABLE"
    assert "connection refused" not in exc_info.value.message


def test_copilot_schema_rejects_tool_history_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CopilotChatRequest(
            message="你好",
            history=[{"role": "tool", "content": "实时数据"}],
        )
    with pytest.raises(ValidationError):
        CopilotChatRequest(message="你好", unexpected="write operation")


def test_copilot_router_exposes_session_chat_path() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    # FastAPI 0.141 uses a lazy _IncludedRouter entry in ``app.routes``;
    # OpenAPI is the stable public view of the registered endpoint.
    assert "/api/v1/simulations/{session_id}/copilot/chat" in app.openapi()["paths"]
