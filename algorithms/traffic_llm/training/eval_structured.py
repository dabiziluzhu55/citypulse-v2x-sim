"""Structured control metrics for Base vs LoRA on frozen Signal SFT test JSONL."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

from simulation.sumo.engine.ai_control import AIControlConfig, AIControlPlan, AIControlValidationError

from algorithms.traffic_llm.dataset.catalog import tls_phase_orders
from algorithms.traffic_llm.dataset.io_utils import dump_json, read_jsonl


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
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


def _slot_agreement(pred: Mapping[str, Any], teacher: Mapping[str, Any]) -> tuple[int, int]:
    match = 0
    total = 0
    teacher_plan = dict(teacher.get("signal_plan") or {})
    pred_plan = dict(pred.get("signal_plan") or {})
    for iid, phases in teacher_plan.items():
        other = pred_plan.get(iid) or []
        for idx, phase in enumerate(phases):
            total += 1
            if idx < len(other) and int(other[idx]) == int(phase):
                match += 1
    return match, total


def evaluate(
    *,
    dataset_dir: Path,
    split: str,
    model_path: str,
    adapter: str | None,
    max_samples: int | None,
    max_new_tokens: int,
) -> dict[str, Any]:
    from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

    ensure_cuda_runtime_libs()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    samples = list(read_jsonl(dataset_dir / "sft" / f"{split}.jsonl"))
    if max_samples is not None:
        samples = samples[: int(max_samples)]
    if not samples:
        raise SystemExit(f"no {split} samples")
    allowed = tls_phase_orders()
    policy = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )
    if adapter:
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()

    n = 0
    json_ok = 0
    schema_ok = 0
    phase_ok = 0
    region_ok = 0
    fallback_ok = 0
    slot_match = 0
    slot_total = 0
    region_match = 0
    examples: list[dict[str, Any]] = []
    for sample in samples:
        n += 1
        messages = sample["messages"]
        prompt = tokenizer.apply_chat_template(
            messages[:2],
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(prompt, return_tensors="pt")
        device = next(model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        decoded = tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)
        teacher = json.loads(messages[2]["content"])
        user = json.loads(messages[1]["content"])
        observation = dict(user.get("observation") or {})
        allowed_region = set(observation.get("controlled_region") or ())
        pred = _extract_json(decoded)
        if pred is not None:
            json_ok += 1
        parsed = None
        if pred is not None:
            try:
                parsed = AIControlPlan.from_mapping(pred, config=policy)
                schema_ok += 1
            except (AIControlValidationError, Exception):
                parsed = None
        if parsed is not None:
            illegal = False
            for iid, phases in parsed.signal_plan.items():
                valid = set(allowed.get(iid) or ())
                if valid and set(phases) - valid:
                    illegal = True
            if not illegal:
                phase_ok += 1
            if set(parsed.controlled_intersections) <= allowed_region:
                region_ok += 1
            if bool(parsed.fallback_to_baseline) == bool(teacher.get("fallback_to_baseline")):
                fallback_ok += 1
            m, t = _slot_agreement(pred, teacher)
            slot_match += m
            slot_total += t
            teacher_ctrl = set(teacher.get("controlled_intersections") or ())
            if set(parsed.controlled_intersections) == teacher_ctrl:
                region_match += 1
        if len(examples) < 1:
            examples.append(
                {
                    "prompt_user": user,
                    "teacher": teacher,
                    "model_raw": decoded,
                    "model_json": pred,
                }
            )
    def rate(num: int) -> float:
        return num / n if n else 0.0

    return {
        "n": n,
        "adapter": adapter,
        "json_parse_rate": rate(json_ok),
        "aicontrolplan_valid_rate": rate(schema_ok),
        "phase_legality_rate": rate(phase_ok),
        "controlled_region_legality_rate": rate(region_ok),
        "fallback_accuracy": rate(fallback_ok),
        "teacher_phase_slot_agreement": (slot_match / slot_total) if slot_total else 0.0,
        "controlled_intersection_agreement": rate(region_match),
        "example": examples[0] if examples else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.training.eval_structured")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args(argv)
    report = evaluate(
        dataset_dir=Path(args.dataset),
        split=args.split,
        model_path=args.model,
        adapter=args.adapter or None,
        max_samples=args.max_samples,
        max_new_tokens=args.max_new_tokens,
    )
    dump_json(Path(args.output), report)
    print(json.dumps({k: v for k, v in report.items() if k != "example"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
