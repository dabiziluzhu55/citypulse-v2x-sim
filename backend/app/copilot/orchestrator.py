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
from .live_answers import (
    compact_tool_result_for_model,
    format_deterministic_answer,
    guard_answer,
)
from .query_intent import (
    CURRENT_TRAFFIC,
    DETERMINISTIC_INTENTS,
    KNOWLEDGE,
    LANE_COUNT,
    LIVE_INTENTS,
    NETWORK_RISK,
    PREDICTION,
    ROAD_CONTEXT,
    AI_STATUS,
    QueryIntent,
    route_query_intent,
)
from .traffic_tools import (
    LIVE_SESSION_TOOL_NAMES,
    SESSIONLESS_TOOL_NAMES,
    TOOL_HANDLERS,
    TrafficToolError,
    TrafficToolService,
    tool_definitions_for,
)


DEFAULT_SYSTEM_PROMPT = """你是 CityPulse-Qwen，CityPulse 车路云交通 Copilot。

必须遵守：
A. 实时事实必须来自 Tool；没有工具结果就明确说无法确认，不得编造实时数据。
B. 短时预测必须来自 get_prediction，只能使用工具返回的 horizon_seconds，不得外推到更长时段。
C. 路网结构、车道数量和连接关系来自 get_road_context。
D. 全网风险和热点来自 get_network_summary；不要先拉取一个路口的全部车道。
E. 项目知识、算法说明和标准条款来自 search_knowledge。点名 Max Pressure / SOTL / IPPO / MAPPO / Fixed / CoV2X / CityPulse-Qwen / Narrow-TDP 时必须先检索知识库，profile 使用 general。
F. 不得重复工具原始数组，不得输出 JSON、字段名或占位符。
G. 默认只总结最重要的 1~3 项；普通问答不超过 5~8 句，除非用户明确要求详细解释。
H. 不确定就明确说不知道。

绝对不要逐项复述大量结构相同的 lane 数据。不要重复相同结论。用户没有要求逐车道详情时，只给聚合结果和最重要异常。
fallback=true 的预测是降级结果，不要称为 Narrow-TDP 正式预测。
你只能查询和计算，不能启动、停止或修改仿真、信号灯、车辆或事件。
关于当前 AI 接管是否生效，调用 get_ai_takeover_status，以 is_currently_executing 和 execution_state 为准。
"""

SESSIONLESS_NO_LIVE_DATA_ANSWER = (
    "当前未启动交通仿真，因此暂无实时仿真交通数据。"
    "启动仿真后可以进一步查询实时车流、事件和预测结果。"
)

_PLACEHOLDER_PATTERN = re.compile(r"\[[^\[\]\n]{1,40}\]")
_PROTOCOL_FIELD_PATTERN = re.compile(
    r'"(?:name|arguments|properties|required|type|description)"\s*:'
)
_UNHELPFUL_LIVE_ANSWER_PATTERN = re.compile(
    r"(?:请.{0,12}提供.{0,12}(?:信息|路口)|输入有误|"
    r"可以.{0,12}(?:使用|调用).{0,24}get_current_traffic)",
    re.IGNORECASE,
)


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


def _requires_live_data(intent: QueryIntent) -> bool:
    return intent.name in LIVE_INTENTS


def _asks_for_live_simulation_data(question: str) -> bool:
    return route_query_intent(question).name in LIVE_INTENTS


def _prefetch_tool_call(intent: QueryIntent) -> ToolCall | None:
    if intent.name == NETWORK_RISK:
        return ToolCall("prefetch_network_summary", "get_network_summary", {})
    if intent.name == PREDICTION:
        arguments = (
            {"intersection_id": intent.intersection_id}
            if intent.intersection_id
            else {}
        )
        return ToolCall("prefetch_prediction", "get_prediction", arguments)
    if intent.name in {LANE_COUNT, ROAD_CONTEXT}:
        if not intent.intersection_id and not intent.lane_id:
            return None
        arguments: dict[str, str] = {}
        if intent.intersection_id:
            arguments["intersection_id"] = intent.intersection_id
        if intent.lane_id:
            arguments["lane_id"] = intent.lane_id
        return ToolCall("prefetch_road_context", "get_road_context", arguments)
    if intent.name == AI_STATUS:
        return ToolCall("prefetch_ai_status", "get_ai_takeover_status", {})
    if intent.name == CURRENT_TRAFFIC:
        if intent.intersection_id or intent.lane_id:
            arguments = {}
            if intent.intersection_id:
                arguments["intersection_id"] = intent.intersection_id
            if intent.lane_id:
                arguments["lane_id"] = intent.lane_id
            return ToolCall("prefetch_current_traffic", "get_current_traffic", arguments)
        return ToolCall("prefetch_network_summary", "get_network_summary", {})
    return None


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
        session_available: bool | None = None,
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
        if session_available is None:
            session_available = bool(getattr(tool_service, "session_id", None))
        self.session_available = bool(session_available)
        self._allowed_tools = (
            SESSIONLESS_TOOL_NAMES
            if not self.session_available
            else frozenset(TOOL_HANDLERS)
        )
        self._tool_definitions = tool_definitions_for(
            session_available=self.session_available
        )

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
        intent = route_query_intent(question, active_scope=active_scope)
        if not self.session_available and _asks_for_live_simulation_data(question):
            return CopilotResponse(
                answer=SESSIONLESS_NO_LIVE_DATA_ANSWER,
                rounds=1,
                tool_calls=(),
            )
        messages = self._initial_messages(
            question,
            history=history,
            active_event_id=active_event_id,
            active_scope=active_scope,
            intent=intent,
        )
        records: list[ToolCallRecord] = []
        usage: dict[str, Any] = {}
        total_latency_ms = 0.0
        model: str | None = None

        if self.session_available and _requires_live_data(intent):
            call = _prefetch_tool_call(intent)
            if call is not None:
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
                if (
                    error is None
                    and intent.name in DETERMINISTIC_INTENTS
                ):
                    formatted = format_deterministic_answer(intent, records)
                    if formatted:
                        return CopilotResponse(
                            answer=guard_answer(
                                formatted,
                                intent=intent,
                                records=records,
                                question=question,
                            ),
                            rounds=1,
                            tool_calls=tuple(records),
                        )
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [call.as_dict()],
                    }
                )
                messages.append(
                    self._tool_message(
                        call,
                        result=result,
                        error=error,
                        question=question,
                    )
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "请根据上面的只读工具结果回答最初的问题。"
                            "只输出简洁自然的中文结论，不要复述字段名，不要逐项复述车道数组，不要重复同一句话。"
                        ),
                    }
                )

        for round_number in range(1, self.max_rounds + 1):
            try:
                completion = self._provider.complete(
                    messages,
                    tools=self._tool_definitions,
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
                missing_required_tool = (
                    self.session_available
                    and _requires_live_data(intent)
                    and not records
                    and intent.name != KNOWLEDGE
                )
                invalid_answer = _invalid_visible_answer(answer) or (
                    self.session_available
                    and _requires_live_data(intent)
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
                                "禁止返回 JSON、字段定义和占位符，禁止重复同一句话。"
                            ),
                        }
                    )
                    continue
                guarded = guard_answer(
                    answer,
                    intent=intent,
                    records=records,
                    question=question,
                )
                if missing_required_tool or _invalid_visible_answer(guarded):
                    formatted = format_deterministic_answer(intent, records)
                    if formatted:
                        return CopilotResponse(
                            answer=formatted,
                            rounds=round_number,
                            tool_calls=tuple(records),
                            model=model,
                            usage=usage,
                            latency_ms=total_latency_ms,
                        )
                    raise CopilotModelError(
                        "大模型未能生成有效的交通分析文字，请重新提问。",
                        code="COPILOT_INVALID_VISIBLE_ANSWER",
                    )
                return CopilotResponse(
                    answer=guarded,
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
                        question=question,
                    )
                )
                formatted_response = self._deterministic_response(
                    intent,
                    records,
                    question=question,
                    rounds=round_number,
                    model=model,
                    usage=usage,
                    latency_ms=total_latency_ms,
                )
                if formatted_response is not None:
                    return formatted_response

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
        intent: QueryIntent | None = None,
    ) -> list[dict[str, Any]]:
        if self.session_available:
            context_lines = [
                "当前 runtime：session_available=true。后端已经把当前仿真会话绑定到交通工具；工具参数中不要自行添加 session_id。"
            ]
        else:
            context_lines = [
                "当前 runtime：session_available=false。未绑定仿真会话。",
                "只允许调用 search_knowledge 和 calculator。",
                "不要调用 get_current_traffic、get_network_summary、get_prediction、get_event_details、get_ai_takeover_status、get_traffic_history、get_road_context。",
                "如果用户询问当前车辆数、路口拥堵或短时预测等实时仿真数据，明确说明："
                + SESSIONLESS_NO_LIVE_DATA_ANSWER,
                "一般交通知识、算法说明和扰动处置原则仍可回答。",
            ]
        if active_event_id and str(active_event_id).strip():
            context_lines.append(
                f"当前查询上下文中的事件 ID（前端传入或后端在单一事件会话中推断）：{str(active_event_id).strip()}"
            )
        if active_scope and str(active_scope).strip():
            context_lines.append(f"当前前端选中的分析范围：{str(active_scope).strip()}")
        if intent is not None and intent.name == KNOWLEDGE:
            context_lines.append(
                "当前问题已判定为项目知识问答，请调用 search_knowledge，不要调用实时交通工具。"
            )
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
            if call.name in LIVE_SESSION_TOOL_NAMES and not self.session_available:
                return None, {
                    "code": "SIMULATION_SESSION_REQUIRED",
                    "message": SESSIONLESS_NO_LIVE_DATA_ANSWER,
                }
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

    def _deterministic_response(
        self,
        intent: QueryIntent,
        records: Sequence[ToolCallRecord],
        *,
        question: str,
        rounds: int,
        model: str | None = None,
        usage: Mapping[str, Any] | None = None,
        latency_ms: float | None = None,
    ) -> CopilotResponse | None:
        if intent.name not in DETERMINISTIC_INTENTS:
            return None
        formatted = format_deterministic_answer(intent, records)
        if not formatted:
            return None
        return CopilotResponse(
            answer=guard_answer(
                formatted,
                intent=intent,
                records=records,
                question=question,
            ),
            rounds=rounds,
            tool_calls=tuple(records),
            model=model,
            usage=dict(usage or {}),
            latency_ms=latency_ms,
        )

    def _tool_message(
        self,
        call: ToolCall,
        *,
        result: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
        question: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any]
        if error is not None:
            payload = {"ok": False, "error": dict(error)}
        else:
            payload = {
                "ok": True,
                "result": _model_facing_tool_result(
                    call.name,
                    result,
                    question=question,
                    arguments=call.arguments,
                ),
            }
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


def _model_facing_tool_result(
    tool_name: str,
    result: Mapping[str, Any] | None,
    *,
    question: str = "",
    arguments: Mapping[str, Any] | str | None = None,
) -> Mapping[str, Any] | None:
    """Reduce verbose read-only results before sending them to Qwen.

    The complete result is still retained in ``ToolCallRecord`` for the API
    caller.  Compact views prevent 7B models from looping over lane arrays.
    """

    return compact_tool_result_for_model(
        tool_name,
        result,
        question=question,
        arguments=arguments,
    )


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
