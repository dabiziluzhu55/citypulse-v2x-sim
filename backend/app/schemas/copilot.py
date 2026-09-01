"""Traffic Copilot HTTP 请求与响应 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CopilotHistoryMessage(BaseModel):
    """仅允许普通文本历史，工具消息由后端在一次请求内维护。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class CopilotChatRequest(BaseModel):
    """一次 Copilot 问答请求。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4_000)
    history: list[CopilotHistoryMessage] = Field(
        default_factory=list,
        max_length=20,
        description="仅传递最近的 user/assistant 文本消息，不包含工具消息。",
    )
    active_event_id: str | None = Field(default=None, max_length=256)
    active_scope: str | None = Field(default=None, max_length=256)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be blank")
        return value

    @field_validator("active_event_id", "active_scope")
    @classmethod
    def normalize_optional_context(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CopilotToolCallResponse(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] | str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CopilotChatResponse(BaseModel):
    """Copilot 最终回答及本次受控工具调用摘要。"""

    session_id: str
    answer: str
    rounds: int
    tool_calls: list[CopilotToolCallResponse] = Field(default_factory=list)
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
