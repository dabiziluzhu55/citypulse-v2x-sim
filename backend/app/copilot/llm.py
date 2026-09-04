"""Traffic Copilot的大模型Provider抽象与Qwen HTTP实现

本模块只负责和OpenAI兼容的聊天补全接口通信，不知道SUMO、TraCI 或
具体交通业务。Qwen服务部署在独立GPU机器上时，后端只需要把
``base_url`` 指向内网地址或SSH隧道

Provider返回经过校验的轻量消息对象，避免编排层依赖某个SDK的响应
对象格式。测试时可以注入一个确定性的transport，不需要启动真实模型
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


class LLMError(RuntimeError):
    """模型请求或模型响应相关的可映射业务错误。"""

    def __init__(self, message: str, *, code: str = "LLM_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LLMInputError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="LLM_INVALID_INPUT")


class LLMUnavailableError(LLMError):
    def __init__(self, message: str, *, code: str = "LLM_UNAVAILABLE") -> None:
        super().__init__(message, code=code)


class LLMProtocolError(LLMError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="LLM_INVALID_RESPONSE")


@dataclass(frozen=True)
class ToolCall:
    """Provider 归一化后的工具调用。"""

    call_id: str
    name: str
    arguments: Mapping[str, Any] | str

    def as_dict(self) -> dict[str, Any]:
        arguments: Mapping[str, Any] | str = self.arguments
        if isinstance(arguments, Mapping):
            arguments = json.dumps(arguments, ensure_ascii=False)
        elif not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        return {
            "id": self.call_id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": arguments,
            },
        }


@dataclass(frozen=True)
class AssistantMessage:
    """与 OpenAI/Qwen chat message 对齐的内部消息。"""

    role: str = "assistant"
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            payload["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        return payload


@dataclass(frozen=True)
class LLMCompletion:
    """一次模型调用的归一化结果。"""

    message: AssistantMessage
    model: str | None = None
    response_id: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None


class LLMProvider(Protocol):
    """Copilot 编排层依赖的最小模型协议。"""

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        ...


class CompletionTransport(Protocol):
    """可注入的 HTTP transport，便于不启动模型的单元测试。"""

    def __call__(
        self,
        endpoint: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        ...


class QwenProvider:
    """调用 OpenAI 兼容 Qwen 服务的同步 Provider。

    ``base_url`` 通常是 ``http://127.0.0.1:18000/v1``。生产部署建议让
    Qwen 服务只监听 GPU 服务器内网/回环地址，再由后端通过内网或 SSH
    隧道访问，而不是把推理端口直接暴露到公网。
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18000/v1",
        *,
        model: str = "Qwen/Qwen2.5-7B-Instruct",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        default_temperature: float = 0.2,
        default_max_tokens: int = 512,
        top_p: float = 0.8,
        repetition_penalty: float = 1.05,
        transport: CompletionTransport | None = None,
    ) -> None:
        normalized_url = str(base_url).strip().rstrip("/")
        if not normalized_url:
            raise LLMInputError("Qwen base_url must not be empty.")
        if not str(model).strip():
            raise LLMInputError("Qwen model must not be empty.")
        if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
            raise LLMInputError("Qwen timeout_seconds must be positive.")
        self.base_url = normalized_url
        self.model = str(model).strip()
        self.api_key = str(api_key).strip() if api_key else None
        self.timeout_seconds = float(timeout_seconds)
        self.default_temperature = _temperature(
            default_temperature, field_name="default_temperature"
        )
        self.default_max_tokens = _max_tokens(default_max_tokens)
        self.top_p = _probability(top_p, field_name="top_p")
        if not math.isfinite(float(repetition_penalty)) or float(repetition_penalty) <= 0:
            raise LLMInputError("repetition_penalty must be positive.")
        self.repetition_penalty = float(repetition_penalty)
        self._transport = transport or _request_json

    @classmethod
    def from_env(cls) -> "QwenProvider":
        """从部署环境读取配置；不会把 API key 写入日志或响应。"""

        return cls(
            base_url=os.getenv(
                "CITYPULSE_QWEN_BASE_URL", "http://127.0.0.1:18000/v1"
            ),
            model=os.getenv("CITYPULSE_QWEN_MODEL", "Qwen/Qwen2.5-7B-Instruct"),
            api_key=os.getenv("CITYPULSE_QWEN_API_KEY") or None,
            timeout_seconds=_env_float("CITYPULSE_QWEN_TIMEOUT_SECONDS", 60.0),
            default_temperature=_env_float("CITYPULSE_QWEN_TEMPERATURE", 0.2),
            default_max_tokens=_env_int("CITYPULSE_QWEN_MAX_TOKENS", 512),
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "QwenProvider":
        """从项目 ``Settings`` 对象创建 Provider，不依赖 Pydantic。"""

        return cls(
            base_url=getattr(settings, "citypulse_qwen_base_url"),
            model=getattr(settings, "citypulse_qwen_model"),
            api_key=getattr(settings, "citypulse_qwen_api_key", None),
            timeout_seconds=getattr(settings, "citypulse_qwen_timeout_seconds"),
            default_temperature=getattr(settings, "citypulse_qwen_temperature"),
            default_max_tokens=getattr(settings, "citypulse_qwen_max_tokens"),
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        tool_choice: str | Mapping[str, Any] | None = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        if not messages:
            raise LLMInputError("messages must contain at least one message.")
        normalized_messages = _messages_payload(messages)
        normalized_tools = _tools_payload(tools)
        selected_temperature = (
            self.default_temperature
            if temperature is None
            else _temperature(temperature, field_name="temperature")
        )
        selected_max_tokens = (
            self.default_max_tokens
            if max_tokens is None
            else _max_tokens(max_tokens)
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": normalized_messages,
            "stream": False,
            "temperature": selected_temperature,
            "top_p": self.top_p,
            "max_tokens": selected_max_tokens,
            "repetition_penalty": self.repetition_penalty,
        }
        if normalized_tools:
            payload["tools"] = normalized_tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started = time.perf_counter()
        try:
            response = self._transport(
                self.endpoint,
                payload,
                headers,
                self.timeout_seconds,
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMUnavailableError(
                "无法连接 Qwen 推理服务，请检查服务状态、地址和网络隧道。"
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000
        return _parse_completion(response, elapsed_ms=elapsed_ms)


def _request_json(
    endpoint: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        # 只把状态码作为业务错误的一部分，避免把服务端详情或 token 回传给用户。
        raise LLMUnavailableError(
            f"Qwen 推理服务返回 HTTP {exc.code}。",
            code="QWEN_HTTP_ERROR",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMUnavailableError(
            "无法连接 Qwen 推理服务，请检查服务状态、地址和网络隧道。"
        ) from exc

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMProtocolError("Qwen 推理服务返回的不是有效 JSON。") from exc
    if not isinstance(decoded, Mapping):
        raise LLMProtocolError("Qwen 推理服务返回的 JSON 顶层必须是对象。")
    return decoded


def _parse_completion(
    payload: Mapping[str, Any],
    *,
    elapsed_ms: float,
) -> LLMCompletion:
    choices = payload.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
        raise LLMProtocolError("Qwen 响应缺少有效的 choices。")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise LLMProtocolError("Qwen 响应的 choice 不是对象。")
    raw_message = choice.get("message")
    if not isinstance(raw_message, Mapping):
        raise LLMProtocolError("Qwen 响应缺少有效的 message。")
    raw_role = raw_message.get("role", "assistant")
    role = str(raw_role or "assistant")
    if role != "assistant":
        raise LLMProtocolError(f"Qwen 响应的 message role 不受支持：{role!r}。")

    raw_tool_calls = raw_message.get("tool_calls", ())
    tool_calls: list[ToolCall] = []
    if raw_tool_calls is not None:
        if not isinstance(raw_tool_calls, Sequence) or isinstance(
            raw_tool_calls, (str, bytes)
        ):
            raise LLMProtocolError("Qwen 响应的 tool_calls 必须是数组。")
        for index, raw_call in enumerate(raw_tool_calls):
            if not isinstance(raw_call, Mapping):
                raise LLMProtocolError("Qwen 响应包含无效的工具调用。")
            function = raw_call.get("function")
            if not isinstance(function, Mapping):
                raise LLMProtocolError("Qwen 响应的工具调用缺少 function。")
            name = str(function.get("name", "") or "").strip()
            if not name:
                raise LLMProtocolError("Qwen 响应的工具调用缺少工具名。")
            arguments = function.get("arguments", {})
            if not isinstance(arguments, (str, Mapping)):
                arguments = json.dumps(arguments, ensure_ascii=False)
            call_id = str(raw_call.get("id", "") or f"call_{index}")
            tool_calls.append(
                ToolCall(call_id=call_id, name=name, arguments=arguments)
            )

    content = _message_content(raw_message.get("content"))
    usage = payload.get("usage", {})
    if not isinstance(usage, Mapping):
        usage = {}
    latency_ms = _finite_optional_number(payload.get("x_citypulse_latency_ms"))
    if latency_ms is None:
        latency_ms = elapsed_ms
    return LLMCompletion(
        message=AssistantMessage(
            role="assistant",
            content=content,
            tool_calls=tuple(tool_calls),
        ),
        model=_optional_string(payload.get("model")),
        response_id=_optional_string(payload.get("id")),
        finish_reason=_optional_string(choice.get("finish_reason")),
        usage=dict(usage),
        latency_ms=latency_ms,
    )


def _messages_payload(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise LLMInputError(f"messages[{index}] must be an object.")
        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            raise LLMInputError(f"messages[{index}].role must be a non-empty string.")
        result.append(dict(message))
    return result


def _tools_payload(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            raise LLMInputError(f"tools[{index}] must be an object.")
        result.append(dict(tool))
    return result


def _message_content(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
            elif isinstance(item, str):
                chunks.append(item)
        return "".join(chunks) or None
    raise LLMProtocolError("大模型返回的 message.content 不是合法文本。")


def _temperature(value: Any, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMInputError(f"{field_name} must be a number.") from exc
    if not math.isfinite(result) or result < 0 or result > 2:
        raise LLMInputError(f"{field_name} must be between 0 and 2.")
    return result


def _probability(value: Any, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LLMInputError(f"{field_name} must be a number.") from exc
    if not math.isfinite(result) or result <= 0 or result > 1:
        raise LLMInputError(f"{field_name} must be greater than 0 and at most 1.")
    return result


def _max_tokens(value: Any) -> int:
    if isinstance(value, bool):
        raise LLMInputError("max_tokens must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise LLMInputError("max_tokens must be an integer.") from exc
    if result < 1 or result > 4096:
        raise LLMInputError("max_tokens must be between 1 and 4096.")
    return result


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMInputError(f"{name} must be a number.") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMInputError(f"{name} must be an integer.") from exc


def _finite_optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "AssistantMessage",
    "CompletionTransport",
    "LLMCompletion",
    "LLMError",
    "LLMInputError",
    "LLMProtocolError",
    "LLMProvider",
    "LLMUnavailableError",
    "QwenProvider",
    "ToolCall",
]
