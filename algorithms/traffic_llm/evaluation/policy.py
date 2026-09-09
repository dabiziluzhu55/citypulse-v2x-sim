"""Frozen Qwen policy for offline closed-loop SUMO evaluation. No Backend."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import (
    AIControlConfig,
    AIControlPlan,
    AIControlValidationError,
)

from algorithms.traffic_llm.dataset.sft_builder import SYSTEM_PROMPT


POLICY_INSTRUCTION = "根据当前观测生成未来 30 秒多路口信号计划。"


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None


@dataclass
class PolicyDecision:
    raw_text: str
    latency_ms: float
    plan: dict[str, Any] | None
    parsed: AIControlPlan | None
    json_ok: bool
    schema_ok: bool
    phase_ok: bool
    region_ok: bool
    invalid_plan: bool
    fallback: bool
    error: str | None


def evaluate_generation(
    decoded: str,
    *,
    latency_ms: float,
    allowed_phases: Mapping[str, Sequence[int]],
    allowed_region: Sequence[str],
    policy: AIControlConfig | None = None,
    error: str | None = None,
    timeout_s: float = 60.0,
) -> PolicyDecision:
    """Validate a generated 30s plan. Used by the frozen policy and unit tests."""

    cfg = policy or AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
    if latency_ms > timeout_s * 1000.0 and error is None:
        error = f"timeout: inference_latency_ms={latency_ms:.1f}"
    pred = extract_json_object(decoded) if not error else None
    json_ok = pred is not None
    parsed: AIControlPlan | None = None
    schema_ok = False
    phase_ok = False
    region_ok = False
    if pred is not None:
        try:
            parsed = AIControlPlan.from_mapping(pred, config=cfg)
            schema_ok = True
        except (AIControlValidationError, Exception) as exc:
            error = error or f"schema_invalid: {exc}"
    if parsed is not None:
        illegal_phase = False
        for iid, phases in parsed.signal_plan.items():
            valid = {int(item) for item in allowed_phases.get(iid) or ()}
            if valid and set(int(p) for p in phases) - valid:
                illegal_phase = True
        phase_ok = not illegal_phase
        region_ok = set(parsed.controlled_intersections) <= set(allowed_region)
        if not phase_ok:
            error = error or "illegal_phase"
        if not region_ok:
            error = error or "controlled_region_out_of_scope"
    fallback = bool(parsed is not None and parsed.fallback_to_baseline) or not (
        json_ok and schema_ok and phase_ok and region_ok
    )
    invalid = not (json_ok and schema_ok and phase_ok and region_ok)
    return PolicyDecision(
        raw_text=decoded,
        latency_ms=latency_ms,
        plan=pred if schema_ok else None,
        parsed=parsed if (schema_ok and phase_ok and region_ok) else None,
        json_ok=json_ok,
        schema_ok=schema_ok,
        phase_ok=phase_ok,
        region_ok=region_ok,
        invalid_plan=invalid,
        fallback=fallback,
        error=error,
    )


class FrozenQwenPolicy:
    """Qwen2.5-7B-Instruct, optionally with a frozen QLoRA adapter."""

    def __init__(
        self,
        model_path: str,
        *,
        adapter: str | None = None,
        max_new_tokens: int = 512,
        timeout_s: float = 60.0,
    ) -> None:
        from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

        ensure_cuda_runtime_libs()
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        self.max_new_tokens = int(max_new_tokens)
        self.timeout_s = float(timeout_s)
        self.policy = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        bnb = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_path,
            quantization_config=bnb,
            device_map={"": 0},
            trust_remote_code=True,
            torch_dtype=torch.bfloat16,
        )
        if adapter:
            model = PeftModel.from_pretrained(model, adapter)
        model.eval()
        if getattr(model, "generation_config", None) is not None:
            model.generation_config.do_sample = False
            model.generation_config.temperature = None
            model.generation_config.top_p = None
            model.generation_config.top_k = None
        self.model = model
        self.torch = torch
        self.adapter = adapter
        self.model_path = model_path

    def generate(
        self,
        observation: Mapping[str, Any],
        *,
        allowed_phases: Mapping[str, Sequence[int]],
        allowed_region: Sequence[str],
    ) -> PolicyDecision:
        user_payload = {
            "instruction": POLICY_INSTRUCTION,
            "observation": dict(observation),
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = self.tokenizer(prompt, return_tensors="pt")
        device = next(self.model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        started = time.perf_counter()
        error: str | None = None
        decoded = ""
        # Greedy decoding: do_sample=False is temperature=0. Transformers
        # rejects temperature=0 unless sampling is enabled.
        try:
            with self.torch.inference_mode():
                with self.torch.autocast(
                    device_type="cuda" if device.type == "cuda" else "cpu",
                    dtype=self.torch.bfloat16,
                    enabled=device.type == "cuda",
                ):
                    output = self.model.generate(
                        **inputs,
                        max_new_tokens=self.max_new_tokens,
                        do_sample=False,
                        pad_token_id=self.tokenizer.pad_token_id,
                        eos_token_id=self.tokenizer.eos_token_id,
                    )
            decoded = self.tokenizer.decode(
                output[0][inputs["input_ids"].shape[1] :],
                skip_special_tokens=True,
            )
        except Exception as exc:
            error = f"generate_failed: {type(exc).__name__}: {exc}"
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
