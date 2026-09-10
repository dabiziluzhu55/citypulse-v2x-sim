"""OpenAI-compatible vLLM client for Traffic-Qwen. Temperature=0 greedy.

Always run evaluate_generation / AIControlPlan.from_mapping after decode.
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from algorithms.traffic_llm.dataset.sft_builder import SYSTEM_PROMPT
from algorithms.traffic_llm.deployment.paths import DEFAULT_MAX_NEW_TOKENS, SERVED_MODEL_NAME
from algorithms.traffic_llm.deployment.schema import OPENAI_JSON_SCHEMA_RESPONSE_FORMAT, PLAN_JSON_SCHEMA
from algorithms.traffic_llm.evaluation.policy import POLICY_INSTRUCTION, PolicyDecision, evaluate_generation
from simulation.sumo.engine.ai_control import AIControlConfig


def chat_messages(observation: Mapping[str, Any]) -> list[dict[str, str]]:
    user_payload = {
        "instruction": POLICY_INSTRUCTION,
        "observation": dict(observation),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def messages_from_sft_sample(sample: Mapping[str, Any]) -> list[dict[str, str]]:
    if sample.get("prompt"):
        return [
            {"role": str(item.get("role")), "content": str(item.get("content") or "")}
            for item in sample["prompt"]
            if item.get("role") in {"system", "user"}
        ]
    messages = list(sample.get("messages") or ())
    return [
        {"role": str(item.get("role")), "content": str(item.get("content") or "")}
        for item in messages
        if item.get("role") in {"system", "user"}
    ]


def _http_json(url: str, payload: Mapping[str, Any] | None = None, timeout_s: float = 120.0) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    request = Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {body[:800]}") from exc
    except URLError as exc:
        raise RuntimeError(f"request failed {url}: {exc}") from exc


def list_models(base_url: str, timeout_s: float = 30.0) -> dict[str, Any]:
    return _http_json(base_url.rstrip("/") + "/v1/models", timeout_s=timeout_s)


def chat_completion(
    base_url: str,
    messages: Sequence[Mapping[str, str]],
    *,
    model: str = SERVED_MODEL_NAME,
    max_tokens: int = DEFAULT_MAX_NEW_TOKENS,
    timeout_s: float = 120.0,
    stream: bool = False,
    structured: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": int(max_tokens),
        "stream": bool(stream),
    }
    if structured:
        payload["response_format"] = OPENAI_JSON_SCHEMA_RESPONSE_FORMAT
        payload["guided_json"] = PLAN_JSON_SCHEMA
    return _http_json(
        base_url.rstrip("/") + "/v1/chat/completions",
        payload,
        timeout_s=timeout_s,
    )


def stream_chat_completion(
    base_url: str,
    messages: Sequence[Mapping[str, str]],
    *,
    model: str = SERVED_MODEL_NAME,
    max_tokens: int = DEFAULT_MAX_NEW_TOKENS,
    timeout_s: float = 120.0,
    structured: bool = True,
) -> dict[str, Any]:
    """Collect a streamed completion; TTFT is first content delta."""

    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": 0,
        "top_p": 1,
        "max_tokens": int(max_tokens),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if structured:
        payload["response_format"] = OPENAI_JSON_SCHEMA_RESPONSE_FORMAT
        payload["guided_json"] = PLAN_JSON_SCHEMA
    request = Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    ttft_s: float | None = None
    chunks: list[str] = []
    usage: dict[str, Any] = {}
    with urlopen(request, timeout=timeout_s) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            if event.get("usage"):
                usage = dict(event["usage"])
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if delta and ttft_s is None:
                ttft_s = time.perf_counter() - started
            if delta:
                chunks.append(delta)
    total_s = time.perf_counter() - started
    text = "".join(chunks)
    return {
        "text": text,
        "ttft_ms": None if ttft_s is None else ttft_s * 1000.0,
        "total_ms": total_s * 1000.0,
        "usage": usage,
    }


def decode_message(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "")


class VllmHttpPolicy:
    """Same generate() contract as FrozenQwenPolicy, over OpenAI-compatible vLLM."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = SERVED_MODEL_NAME,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
        timeout_s: float = 60.0,
        structured: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_new_tokens = int(max_new_tokens)
        self.timeout_s = float(timeout_s)
        self.structured = bool(structured)
        self.policy = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
        self.adapter = f"vllm:{self.base_url}:{self.model}"

    def generate(
        self,
        observation: Mapping[str, Any],
        *,
        allowed_phases: Mapping[str, Sequence[int]],
        allowed_region: Sequence[str],
    ) -> PolicyDecision:
        messages = chat_messages(observation)
        started = time.perf_counter()
        error: str | None = None
        decoded = ""
        try:
            try:
                payload = chat_completion(
                    self.base_url,
                    messages,
                    model=self.model,
                    max_tokens=self.max_new_tokens,
                    timeout_s=self.timeout_s,
                    structured=self.structured,
                )
            except RuntimeError:
                if not self.structured:
                    raise
                payload = chat_completion(
                    self.base_url,
                    messages,
                    model=self.model,
                    max_tokens=self.max_new_tokens,
                    timeout_s=self.timeout_s,
                    structured=False,
                )
            decoded = decode_message(payload)
        except Exception as exc:
            error = f"vllm_generate_failed: {type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - started) * 1000.0
        return evaluate_generation(
            decoded,
            latency_ms=latency_ms,
            allowed_phases=allowed_phases,
            allowed_region=allowed_region,
            policy=self.policy,
            error=error,
            timeout_s=self.timeout_s,
        )
