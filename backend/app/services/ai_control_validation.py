"""Validate that at most one disturbance target requests AI takeover."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..core.exceptions import AppError

MULTIPLE_AI_TARGETS_MESSAGE = (
    "当前Traffic-Qwen仅支持单一主要扰动事件接管，请只选择一个AI管控目标。"
)


def is_ai_control_enabled(event: Any) -> bool:
    if bool(getattr(event, "ai_control_enabled", False)):
        return True
    details = getattr(event, "details", None)
    if isinstance(details, Mapping):
        return bool(details.get("ai_control_enabled", False))
    return False


def ai_control_enabled_events(events: Sequence[Any]) -> list[Any]:
    return [event for event in events if is_ai_control_enabled(event)]


def ensure_at_most_one_ai_target(events: Sequence[Any]) -> None:
    enabled = ai_control_enabled_events(events)
    if len(enabled) > 1:
        raise AppError(
            code="MULTIPLE_AI_CONTROL_TARGETS",
            message=MULTIPLE_AI_TARGETS_MESSAGE,
            status_code=422,
        )


def active_ai_control_events(events: Sequence[Any]) -> list[Any]:
    return [
        event
        for event in events
        if str(getattr(event, "state", "")) == "ACTIVE" and is_ai_control_enabled(event)
    ]
