#!/usr/bin/env python3
"""Minimal OpenAI-compatible Qwen service for deployment smoke tests.

This service deliberately has no CityPulse or SUMO dependencies.  It loads a
local Transformers model and exposes only read-only inference endpoints so the
model server can be validated before the traffic Copilot is integrated.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from transformers import AutoModelForCausalLM, AutoTokenizer


app = FastAPI(title="CityPulse Qwen Smoke Service", version="0.1.0")
_service: "QwenService | None" = None


def _tool_call_from_text(
    text: str,
    allowed_names: set[str],
) -> dict[str, Any] | None:
    """Parse tagged or bare-JSON Qwen tool calls from tool-enabled requests."""

    # AI takeover planning deliberately returns plain JSON without tools.  An
    # empty allow-list therefore must never be interpreted as a tool call.
    if not allowed_names:
        return None

    cleaned = text.strip()
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", cleaned, re.DOTALL)
    candidate = match.group(1) if match else cleaned
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("name")
    arguments = payload.get("arguments", {})
    if not isinstance(name, str) or name not in allowed_names:
        return None
    if not isinstance(arguments, dict):
        return None
    return {
        "id": f"call_{uuid4().hex}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class QwenService:
    def __init__(self, model_path: Path, served_model_name: str, max_input_tokens: int) -> None:
        self.model_path = model_path
        self.served_model_name = served_model_name
        self.max_input_tokens = max_input_tokens
        self.generation_lock = threading.Lock()

        self.tokenizer = AutoTokenizer.from_pretrained(
            str(model_path),
            trust_remote_code=True,
            use_fast=True,
        )
        self.model = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            torch_dtype=torch.float16,
            device_map="auto",
            low_cpu_mem_usage=True,
            trust_remote_code=True,
        )
        self.model.eval()
        self.input_device = next(self.model.parameters()).device

    def _prompt(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        template_kwargs: dict[str, Any] = {
            "tokenize": False,
            "add_generation_prompt": True,
        }
        if tools:
            template_kwargs["tools"] = tools
        try:
            return self.tokenizer.apply_chat_template(messages, **template_kwargs)
        except (TypeError, ValueError):
            # Keep the smoke service usable with tokenizer templates that do
            # not accept a tools argument.  The Copilot orchestrator will still
            # validate tool names and arguments on the backend side.
            if not tools:
                raise
            tool_text = json.dumps(tools, ensure_ascii=False)
            fallback_messages = list(messages)
            fallback_messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "可用工具如下。需要调用工具时，只输出一个 JSON 对象，格式为 "
                        '{"name":"工具名","arguments":{}}，不要输出其他内容。\n'
                        f"工具定义：{tool_text}"
                    ),
                },
            )
            return self.tokenizer.apply_chat_template(
                fallback_messages,
                tokenize=False,
                add_generation_prompt=True,
            )

    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_new_tokens: int,
        temperature: float,
        top_p: float,
        repetition_penalty: float,
    ) -> tuple[str, dict[str, Any] | None, int, int]:
        prompt = self._prompt(messages, tools)
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_input_tokens,
        )
        inputs = {name: value.to(self.input_device) for name, value in inputs.items()}
        input_tokens = int(inputs["input_ids"].shape[-1])

        generation_kwargs: dict[str, Any] = {
            **inputs,
            "max_new_tokens": max_new_tokens,
            "repetition_penalty": repetition_penalty,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if temperature > 0:
            generation_kwargs.update(
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
            )
        else:
            generation_kwargs["do_sample"] = False

        with self.generation_lock, torch.inference_mode():
            output_ids = self.model.generate(**generation_kwargs)
        new_ids = output_ids[0, input_tokens:]
        raw_text = self.tokenizer.decode(new_ids, skip_special_tokens=False).strip()
        visible_text = self.tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        allowed_names = {
            str(tool.get("function", {}).get("name", ""))
            for tool in tools
            if isinstance(tool, dict)
        }
        tool_call = _tool_call_from_text(visible_text, allowed_names)
        return visible_text, tool_call, input_tokens, int(new_ids.shape[-1])


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if _service is not None else "starting",
        "model_loaded": _service is not None,
        "model": _service.served_model_name if _service else None,
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }


@app.get("/v1/models")
def models() -> dict[str, Any]:
    if _service is None:
        raise HTTPException(status_code=503, detail="model is still loading")
    return {
        "object": "list",
        "data": [
            {
                "id": _service.served_model_name,
                "object": "model",
                "owned_by": "citypulse",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(payload: dict[str, Any]) -> dict[str, Any]:
    if _service is None:
        raise HTTPException(status_code=503, detail="model is still loading")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages must be a non-empty list")
    if payload.get("stream"):
        raise HTTPException(status_code=400, detail="streaming is not enabled in smoke service")

    tools = payload.get("tools") or []
    if not isinstance(tools, list):
        raise HTTPException(status_code=400, detail="tools must be a list")
    max_new_tokens = min(max(int(payload.get("max_tokens", 512)), 1), 2048)
    temperature = float(payload.get("temperature", 0.2))
    top_p = float(payload.get("top_p", 0.8))
    repetition_penalty = float(payload.get("repetition_penalty", 1.05))

    started = time.perf_counter()
    text, tool_call, prompt_tokens, completion_tokens = _service.generate(
        messages=messages,
        tools=tools,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        top_p=top_p,
        repetition_penalty=repetition_penalty,
    )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": None if tool_call else text,
    }
    finish_reason = "tool_calls" if tool_call else "stop"
    if tool_call:
        message["tool_calls"] = [tool_call]
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _service.served_model_name,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "x_citypulse_latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--served-model-name", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--max-input-tokens", type=int, default=4096)
    args = parser.parse_args()

    global _service
    _service = QwenService(
        model_path=args.model_path,
        served_model_name=args.served_model_name,
        max_input_tokens=args.max_input_tokens,
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
