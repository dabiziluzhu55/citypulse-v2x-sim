"""Traffic Copilot的受控工具调用编排器

编排器把用户问题、当前事件上下文和历史对话交给模型；模型只能从固定
的只读交通工具中选择。每次工具调用都会在后端白名单和工具自身参数
校验后执行，结果再以 ``role=tool`` 消息回传给Qwen

先用固定数据验证真实Qwen的多轮协议，下一步再把同一编排器挂到HTTP API
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .llm import LLMError, LLMProvider, ToolCall
from .traffic_tools import (
    TOOL_DEFINITIONS,
    TOOL_HANDLERS,
    TrafficToolError,
    TrafficToolService,
)


DEFAULT_SYSTEM_PROMPT = """你是 CityPulse 车路云交通 Copilot，负责解释当前仿真中的交通状态。

必须遵守：
1. 当前车辆数、停车数、速度、事件状态、历史趋势和预测等事实，必须先调用只读交通工具获得；没有工具结果就明确说无法确认，不得编造实时数据。
2. 工具结果中的 timestamp、scope、source 代表数据口径。回答时区分观测事实、预测结果、基于证据的推断和未知原因。
3. 事件的具体成因如果工具没有确认，就说“原因未确认”；不要把前端注入事件直接当成识别结论。
4. 如果用户是在查看前端选中的事件并请求事件简报，必须先查询该事件详情；同时查询相关路口当前状态和历史趋势，再生成简报。只有用户点击/主动提问时才生成简报，不要因为事件写入历史就主动调用模型。
5. 你只能查询和计算，不能启动、停止、暂停仿真，不能修改信号灯、车辆、事件或任何系统状态。用户要求控制操作时，说明当前 Copilot 只支持只读分析。
6. 查询事故、施工占道、限速、大型活动、回溢或多路口协同的处置原则时，优先调用 search_knowledge，并使用 profile="control"；普通算法说明或项目规划问题才使用 profile="general"。RAG 返回的 planning 内容表示规划，不是已经上线的能力。
7. 最终用简洁、清楚的中文回答；涉及多个数据来源时说明各自的范围和时间。
8. 最终回答只能是面向用户的自然中文，禁止返回 JSON、工具参数、字段定义、Schema 或 Python 字典。
9. 当前状态、车辆、速度、排队、拥堵等问题必须先调用工具；禁止使用“[车辆数量]”之类的占位符。
10. 工具调用结束后，只提炼用户关心的结论，不逐字段复述工具原始结果。
"""

_PLACEHOLDER_PATTERN = re.compile(r"\[[^\[\]\n]{1,40}\]")
_PROTOCOL_FIELD_PATTERN = re.compile(
    r'"(?:name|arguments|properties|required|type|description)"\s*:'
)
_UNHELPFUL_LIVE_ANSWER_PATTERN = re.compile(
    r"(?:请.{0,12}提供.{0,12}(?:信息|路口)|输入有误|"
    r"可以.{0,12}(?:使用|调用).{0,24}get_current_traffic)",
    re.IGNORECASE,
)
_LIVE_CONTEXT_KEYWORDS = (
    "当前",
    "实时",
    "现在",
    "本路口",
    "这个路口",
)
_LIVE_METRIC_KEYWORDS = (
    "车辆",
    "速度",
    "排队",
    "等待",
    "拥堵",
    "信号灯",
    "交通状态",
)
_LIVE_QUERY_INTENTS = ("多少", "几辆", "状态", "情况", "趋势", "是否", "多长")


def _invalid_visible_answer(answer: str) -> bool:
    normalized = answer.strip()
    if not normalized:
        return True
    if normalized.startswith("{") or normalized.startswith("["):
        return True
    if "<tool_call>" in normalized:
        return True
    if _PROTOCOL_FIELD_PATTERN.search(normalized):
        return True
    return _PLACEHOLDER_PATTERN.search(normalized) is not None


def _requires_live_data(question: str) -> bool:
    if any(keyword in question for keyword in _LIVE_CONTEXT_KEYWORDS):
        return True
    return (
        any(keyword in question for keyword in _LIVE_METRIC_KEYWORDS)
        and any(keyword in question for keyword in _LIVE_QUERY_INTENTS)
    )


def _intersection_from_scope(active_scope: str | None) -> str | None:
    normalized = str(active_scope or "").strip()
    prefix = "intersection:"
    if not normalized.startswith(prefix):
        return None
    intersection_id = normalized[len(prefix) :].strip()
    return intersection_id or None


class CopilotError(RuntimeError):
    """Copilot 编排过程中可映射给 API 的错误。"""

    def __init__(self, message: str, *, code: str = "COPILOT_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CopilotInputError(CopilotError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="COPILOT_INVALID_INPUT")


class CopilotLimitError(CopilotError):
    def __init__(self, message: str, *, code: str = "COPILOT_LIMIT_EXCEEDED") -> None:
        super().__init__(message, code=code)


class CopilotModelError(CopilotError):
    def __init__(self, message: str, *, code: str = "COPILOT_MODEL_ERROR") -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用及其后端结果，供日志或 API 调试信息使用。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any] | str
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
        }


@dataclass(frozen=True)
class CopilotResponse:
    """一次 Copilot 对话的最终回答与受控执行摘要。"""

    answer: str
    rounds: int
    tool_calls: tuple[ToolCallRecord, ...] = ()
    model: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "rounds": self.rounds,
            "tool_calls": [item.as_dict() for item in self.tool_calls],
            "model": self.model,
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
        }


class CopilotOrchestrator:
    """使用一个 LLM Provider 执行只读交通问答工具循环。"""

    def __init__(
        self,
        provider: LLMProvider,
        tool_service: TrafficToolService,
        *,
        max_rounds: int = 4,
        max_tool_calls: int = 8,
        max_tool_result_chars: int = 20_000,
        max_history_messages: int = 20,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._provider = provider
        self._tool_service = tool_service
        self.max_rounds = _positive_int(max_rounds, "max_rounds")
        self.max_tool_calls = _positive_int(max_tool_calls, "max_tool_calls")
        self.max_tool_result_chars = _positive_int(
            max_tool_result_chars, "max_tool_result_chars"
        )
        self.max_history_messages = _positive_int(
            max_history_messages, "max_history_messages"
        )
        self.system_prompt = str(system_prompt).strip()
        if not self.system_prompt:
            raise CopilotInputError("system_prompt must not be empty.")
        self._allowed_tools = frozenset(TOOL_HANDLERS)

    def run(
        self,
        user_message: str,
        *,
        history: Sequence[Mapping[str, Any]] = (),
        active_event_id: str | None = None,
        active_scope: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CopilotResponse:
        question = str(user_message).strip()
        if not question:
            raise CopilotInputError("user_message must not be empty.")
        messages = self._initial_messages(
            question,
            history=history,
            active_event_id=active_event_id,
            active_scope=active_scope,
        )
        records: list[ToolCallRecord] = []
        usage: dict[str, Any] = {}
        total_latency_ms = 0.0
        model: str | None = None

        # The compact Qwen smoke service is not guaranteed to honor
        # tool_choice="auto".  Current-state questions nevertheless require
        # authoritative data, so bind one read-only observation before asking
        # the model to write the user-facing summary.
        if _requires_live_data(question):
            intersection_id = _intersection_from_scope(active_scope)
            call = ToolCall(
                call_id="prefetch_current_traffic",
                name=(
                    "get_current_traffic"
                    if intersection_id
                    else "get_network_summary"
                ),
                arguments=(
                    {"intersection_id": intersection_id}
                    if intersection_id
                    else {}
                ),
            )
            result, error = self._execute_tool(call)
            records.append(
                ToolCallRecord(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                    result=result,
                    error=error,
                )
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [call.as_dict()],
                }
            )
            messages.append(self._tool_message(call, result=result, error=error))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "请根据上面的只读工具结果回答最初的问题。"
                        "只输出简洁自然的中文结论，不要复述字段名。"
                    ),
                }
            )

        for round_number in range(1, self.max_rounds + 1):
            try:
                completion = self._provider.complete(
                    messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice="auto",
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LLMError:
                raise
            except CopilotError:
                raise
            except Exception as exc:
                raise CopilotModelError(
                    "调用大模型失败，请稍后重试。", code="COPILOT_MODEL_UNAVAILABLE"
                ) from exc

            model = completion.model or model
            total_latency_ms += _nonnegative_number(completion.latency_ms)
            _merge_usage(usage, completion.usage)
            assistant = completion.message
            if not assistant.tool_calls:
                answer = (assistant.content or "").strip()
                missing_required_tool = _requires_live_data(question) and not records
                invalid_answer = _invalid_visible_answer(answer) or (
                    _requires_live_data(question)
                    and _UNHELPFUL_LIVE_ANSWER_PATTERN.search(answer) is not None
                )
                if (
                    (missing_required_tool or invalid_answer)
                    and round_number < self.max_rounds
                ):
                    messages.append(assistant.as_dict())
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "刚才的回答不符合要求。如果问题涉及当前仿真数据，"
                                "请先调用合适的只读工具；最终只输出简洁自然的中文结论，"
                                "禁止返回 JSON、字段定义和占位符。"
                            ),
                        }
                    )
                    continue
                if missing_required_tool or invalid_answer:
                    raise CopilotModelError(
                        "大模型未能生成有效的交通分析文字，请重新提问。",
                        code="COPILOT_INVALID_VISIBLE_ANSWER",
                    )
                return CopilotResponse(
                    answer=answer,
                    rounds=round_number,
                    tool_calls=tuple(records),
                    model=model,
                    usage=usage,
                    latency_ms=total_latency_ms,
                )

            if len(records) + len(assistant.tool_calls) > self.max_tool_calls:
                raise CopilotLimitError(
                    f"本次对话最多执行 {self.max_tool_calls} 次只读工具调用。",
                    code="COPILOT_TOOL_CALL_LIMIT",
                )

            messages.append(assistant.as_dict())
            for call in assistant.tool_calls:
                result, error = self._execute_tool(call)
                records.append(
                    ToolCallRecord(
                        call_id=call.call_id,
                        name=call.name,
                        arguments=call.arguments,
                        result=result,
                        error=error,
                    )
                )
                messages.append(
                    self._tool_message(
                        call,
                        result=result,
                        error=error,
                    )
                )

        raise CopilotLimitError(
            f"模型连续 {self.max_rounds} 轮仍未生成最终回答。",
            code="COPILOT_ROUND_LIMIT",
        )

    def _initial_messages(
        self,
        question: str,
        *,
        history: Sequence[Mapping[str, Any]],
        active_event_id: str | None,
        active_scope: str | None,
    ) -> list[dict[str, Any]]:
        context_lines = [
            "后端已经把当前仿真会话绑定到交通工具；工具参数中不要自行添加 session_id。"
        ]
        if active_event_id and str(active_event_id).strip():
            context_lines.append(f"当前前端选中的活动事件 ID：{str(active_event_id).strip()}")
        if active_scope and str(active_scope).strip():
            context_lines.append(f"当前前端选中的分析范围：{str(active_scope).strip()}")
        context = "\n\n当前会话上下文（只用于确定查询范围，不是实时事实）：\n" + "\n".join(
            f"- {line}" for line in context_lines
        )
        messages = [{"role": "system", "content": self.system_prompt + context}]
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": question})
        return messages

    def _history_messages(
        self, history: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
            raise CopilotInputError("history must be an array of messages.")
        result: list[dict[str, Any]] = []
        for index, item in enumerate(history[-self.max_history_messages :]):
            if not isinstance(item, Mapping):
                raise CopilotInputError(f"history[{index}] must be an object.")
            role = item.get("role")
            content = item.get("content")
            # 外部会话历史只允许普通 user/assistant 文本，避免调用方注入
            # system/tool 消息或伪造工具结果。
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            result.append({"role": role, "content": content})
        return result

    def _execute_tool(
        self, call: ToolCall
    ) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
        if call.name not in self._allowed_tools:
            return None, {
                "code": "UNSUPPORTED_TOOL",
                "message": "该 Copilot 只允许调用固定的只读交通工具。",
            }
        try:
            result = self._tool_service.execute(call.name, call.arguments)
        except TrafficToolError as exc:
            return None, {"code": exc.code, "message": exc.message}
        except Exception:
            # 不把内部堆栈、路径或连接信息暴露给模型和前端。
            return None, {
                "code": "TOOL_EXECUTION_ERROR",
                "message": "交通工具执行失败，当前数据暂不可用。",
            }
        return result, None

    def _tool_message(
        self,
        call: ToolCall,
        *,
        result: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any]
        if error is not None:
            payload = {"ok": False, "error": dict(error)}
        else:
            payload = {"ok": True, "result": result}
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(content) > self.max_tool_result_chars:
            preview_limit = max(100, self.max_tool_result_chars - 160)
            content = json.dumps(
                {
                    "ok": error is None,
                    "truncated": True,
                    "original_size": len(content),
                    "message": "工具结果过长，仅保留前缀供模型判断；如需精确范围请缩小查询。",
                    "preview": content[:preview_limit],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return {
            "role": "tool",
            "tool_call_id": call.call_id,
            "name": call.name,
            "content": content,
        }


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise CopilotInputError(f"{field_name} must be a positive integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CopilotInputError(f"{field_name} must be a positive integer.") from exc
    if result <= 0:
        raise CopilotInputError(f"{field_name} must be a positive integer.")
    return result


def _nonnegative_number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) and result >= 0 else 0.0


def _merge_usage(target: dict[str, Any], usage: Mapping[str, Any]) -> None:
    if not isinstance(usage, Mapping):
        return
    for key, value in usage.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            previous = target.get(key, 0)
            if isinstance(previous, (int, float)) and not isinstance(previous, bool):
                target[key] = previous + value
            else:
                target[key] = value
        elif key not in target:
            target[key] = value


__all__ = [
    "CopilotError",
    "CopilotInputError",
    "CopilotLimitError",
    "CopilotModelError",
    "CopilotOrchestrator",
    "CopilotResponse",
    "DEFAULT_SYSTEM_PROMPT",
    "ToolCallRecord",
]
