"""Traffic Copilot的受控工具调用编排器

编排器把用户问题、当前事件上下文和历史对话交给模型；模型只能从固定
的只读交通工具中选择。每次工具调用都会在后端白名单和工具自身参数
校验后执行，结果再以 ``role=tool`` 消息回传给Qwen

先用固定数据验证真实Qwen的多轮协议，下一步再把同一编排器挂到HTTP API
"""

from __future__ import annotations

import json
import math
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
1. 当前车辆数、停车数、速度、事件状态、历史趋势和预测等事实，必须先调用只读交通工具获得；没有工具结果就明确说无法确认，不得编造实时数据。凡是需要交通工程知识、处置原则或标准依据的问题，也必须先调用 search_knowledge，不得凭模型记忆直接回答。
2. 工具结果中的 timestamp、scope、source 代表数据口径。回答时区分观测事实、预测结果、基于证据的推断和未知原因。`get_current_traffic` 返回的 `model_summary` 是逐车道精确摘要，以其中的车道值为准，不要把路口总量分摊或推算到单条车道。用户未指定路口或车道，却询问“当前车流”“全网交通”“整体拥堵”等范围问题时，必须调用 `get_network_summary`，不要先反问路口 ID；只有用户明确指定路口或车道时才调用 `get_current_traffic`。没有指定路口的全网预测问题，调用 `get_prediction` 时不要填写任何路口 ID，直接依据工具返回的 `top_increases` 排序结果回答。预测只能使用工具返回的 horizon_seconds（当前运行配置通常约为 60 秒）；用户要求超过该范围（例如 5 分钟）时，直接说明当前不支持，不要虚构路口 ID 重试，也不要把短时预测外推成更长预测。
3. 事件的具体成因如果工具没有确认，就说“原因未确认”；不要把前端注入事件直接当成识别结论。事件类型（例如“事故”“施工”）不是事件 ID；如果当前会话上下文已经提供唯一事件 ID，使用该 ID 查询，不要把事件类型当作 ID。
4. 如果用户是在查看前端选中的事件并请求事件简报，必须先查询该事件详情；同时查询相关路口当前状态和历史趋势，再生成简报。只有用户点击/主动提问时才生成简报，不要因为事件写入历史就主动调用模型。
5. 你只能查询和计算，不能启动、停止、暂停仿真，不能修改信号灯、车辆、事件或任何系统状态。用户要求控制操作时，说明当前 Copilot 只支持只读分析。
6. 查询事故、施工占道、限速、大型活动、回溢或多路口协同的一般处置原则时，search_knowledge 是必需的第一步，使用 profile="control" 和 knowledge_sources=["traffic"]；如果用户只问处置原则而没有提供具体路口/车道 ID，不要索要 ID。一般交通原则查询不要自行填写 information_types，除非用户明确要求按已知类别筛选；不要创造 `disposal_principle` 等不存在的类别。知识来源最终由后端按用户原问题再次约束：普通项目指标/公式问题只检索当前正式指标文档；明确的国家/行业标准问题只检索标准索引；只有用户明确要求比较项目口径与国家/行业标准时才同时检索两者；AI 管控评估问题检索 AI 评估规范。不要为了猜测而同时选择多个知识来源。不得引用检索结果中没有明确出现的标准编号、条款或数值；没有直接标准条款时明确说明没有直接条款。比较项目口径和标准时，只有公式、对象、时间窗口以及边界/统计处理都明确一致才说“一致”，否则说“部分对应”；没有直接条款就明确写“没有直接条款”。查询雄安规划时，使用 profile="general" 和 knowledge_sources=["policy"]。普通算法说明或项目规划问题才使用 profile="general"。RAG 返回的 planning/policy 内容表示规划，不是已经上线的能力；回答时保留标准编号、章节、页码和文档状态。
7. 最终用简洁、清楚的中文回答；涉及多个数据来源时说明各自的范围和时间。
8. 关于当前或最近一次 AI 接管的问题（是否真正生效、当前状态、接管事件、控制范围、已安装计划、目标相位、失败或回退原因），必须先调用 get_ai_takeover_status；不要根据事件的 ai_control_enabled 字段自行推断接管成功。回答“当前正在管控吗”时，以工具返回的 `is_currently_executing` 和 `execution_state` 为准：只有 `is_currently_executing=true` 或 `execution_state=EXECUTING` 才能说当前管控正在执行；`PLANNING_PAUSED` 只能说仿真正在暂停并进行规划，不能说信号控制动作正在执行；终态仿真（FAILED/STOPPED/COMPLETED）不能说仍在管控。`installed_plan_active` 表示计划仍安装在非终态会话中，不等于当前正在执行。get_ai_takeover_status 返回的目标相位序列是已安装的控制请求，不是 SUMO 当前实测相位；要回答当前实际相位，另调 get_current_traffic。询问“管控后车流如何变化”时，先调用 get_ai_takeover_status，再调用 get_traffic_history，将管控动作与实际交通指标分开说明。AI 接管状态和计划信息是运行时事实，不要调用 search_knowledge 替代。
"""


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
                if not answer:
                    raise CopilotModelError(
                        "大模型没有返回可用的文字回答。",
                        code="COPILOT_EMPTY_ANSWER",
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
            context_lines.append(
                f"当前查询上下文中的事件 ID（前端传入或后端在单一事件会话中推断）：{str(active_event_id).strip()}"
            )
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
            payload = {
                "ok": True,
                "result": _model_facing_tool_result(call.name, result),
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
) -> Mapping[str, Any] | None:
    """Reduce verbose read-only results before sending them to Qwen.

    The complete result is still retained in ``ToolCallRecord`` for the API
    caller.  Qwen only needs the authoritative compact view for current
    traffic and the deterministic ranking for a network prediction; sending
    the duplicated lane/all-intersection payload makes small local models
    more likely to confuse adjacent rows.
    """

    if not isinstance(result, Mapping):
        return result
    data = result.get("data")
    if not isinstance(data, Mapping):
        return result

    envelope = {
        key: result[key]
        for key in ("source", "scope", "timestamp")
        if key in result
    }
    if tool_name == "get_current_traffic":
        summary = data.get("model_summary")
        if isinstance(summary, Mapping):
            envelope["data"] = {
                "as_of_seconds": data.get("as_of_seconds"),
                "model_summary": summary,
                "model_view": "compact_current_traffic",
            }
            return envelope

    if tool_name == "get_ai_takeover_status":
        execution_state, execution_note = _ai_execution_view(data)
        envelope["data"] = {
            "available": data.get("available", False),
            "as_of_seconds": data.get("as_of_seconds"),
            "simulation_state": data.get("simulation_state"),
            "takeover_state": data.get("takeover_state"),
            "execution_state": execution_state,
            "is_currently_executing": execution_state == "EXECUTING",
            "execution_note": execution_note,
            "ai_enabled": data.get("ai_enabled", False),
            "active_event_id": data.get("active_event_id"),
            "allowed_scope": data.get("allowed_scope", []),
            "controlled_intersections": data.get("controlled_intersections", []),
            "plan_sequence": data.get("plan_sequence", 0),
            "installed_plan_active": data.get("installed_plan_active", False),
            "installed_plan": data.get("installed_plan"),
            "last_error": data.get("last_error"),
            "fallback_reason": data.get("fallback_reason"),
            "rag_status": data.get("rag_status"),
        }
        return envelope

    if tool_name == "get_road_context":
        upstream = data.get("upstream_intersections", [])
        downstream = data.get("downstream_intersections", [])
        upstream_values = (
            list(upstream)
            if isinstance(upstream, Sequence) and not isinstance(upstream, (str, bytes))
            else []
        )
        downstream_values = (
            list(downstream)
            if isinstance(downstream, Sequence) and not isinstance(downstream, (str, bytes))
            else []
        )
        direct_values = sorted(
            {
                str(value)
                for value in [*upstream_values, *downstream_values]
                if str(value).strip()
            }
        )
        envelope["data"] = {
            "target": data.get("target"),
            "topology_available": data.get("topology_available", False),
            "upstream_intersections": upstream_values,
            "downstream_intersections": downstream_values,
            "directly_connected_intersections": direct_values,
            "connection_note": (
                "只回答当前 TLS manifest 能证明的直接相连路口；同一路口同时出现在上游和下游时，"
                "表示双向直接连接，不要重复计数，也不要扩展到其他走廊或路径邻居。"
            ),
        }
        return envelope

    if tool_name == "get_prediction" and "top_increases" in data:
        compact_keys = (
            "available",
            "as_of_seconds",
            "supported_horizon_seconds",
            "horizon_seconds",
            "model",
            "model_version",
            "ready",
            "fallback",
            "fallback_reason",
            "top_increases",
            "not_found",
            "predicted_affected_intersections",
        )
        compact_data = {
            key: data[key] for key in compact_keys if key in data
        }
        rows = data.get("intersections")
        # A scoped prediction is already small and should retain the exact
        # requested row. A network prediction uses top_increases instead of
        # forwarding all rows for the model to sort itself.
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            if len(rows) <= 5:
                compact_data["intersections"] = rows
            else:
                compact_data["model_view"] = "network_prediction_summary"
        envelope["data"] = compact_data
        return envelope

    return result


def _ai_execution_view(data: Mapping[str, Any]) -> tuple[str, str]:
    """Return one unambiguous execution state for the model-facing payload."""

    simulation_state = str(data.get("simulation_state", "")).upper()
    takeover_state = str(data.get("takeover_state", "")).upper()
    if simulation_state in {"STOPPED", "COMPLETED", "FAILED"}:
        return "FINISHED", "仿真已经结束，AI 信号控制当前没有执行。"
    if bool(data.get("control_active", False)):
        return "EXECUTING", "仿真正在运行，AI 信号控制计划当前正在执行。"
    if simulation_state == "PAUSED" and takeover_state == "ACTIVE":
        return "PLANNING_PAUSED", "仿真当前暂停，AI 正在规划或安装计划，信号控制动作尚未执行。"
    if takeover_state in {"RECOVERY", "FALLBACK"}:
        return "RECOVERY", "AI 接管正在恢复或回退到基线，当前不能视为正常 AI 计划执行。"
    return "BASELINE", "当前没有正在执行的 AI 信号控制计划，仿真使用基线控制。"


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
