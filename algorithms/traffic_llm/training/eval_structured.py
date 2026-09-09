"""Structured control metrics for Base vs LoRA on frozen Signal SFT JSONL."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
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


def _sequence_exact(pred: Mapping[str, Any], teacher: Mapping[str, Any]) -> tuple[int, int]:
    teacher_plan = dict(teacher.get("signal_plan") or {})
    pred_plan = dict(pred.get("signal_plan") or {})
    match = 0
    total = 0
    for iid, phases in teacher_plan.items():
        total += 1
        other = pred_plan.get(iid) or []
        if list(other) == list(phases):
            match += 1
    return match, total


def _f1(pred_set: set[str], gold_set: set[str]) -> float:
    if not pred_set and not gold_set:
        return 1.0
    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _sample_parts(sample: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if sample.get("prompt") and sample.get("completion"):
        messages = list(sample["prompt"]) + list(sample["completion"])
    else:
        messages = list(sample.get("messages") or [])
    user = json.loads(messages[1]["content"])
    teacher = json.loads(messages[2]["content"])
    return messages, user, teacher


def _group_key(user: Mapping[str, Any], meta: Mapping[str, Any], field: str) -> str:
    if field == "event":
        event = dict((user.get("observation") or {}).get("event") or {})
        return str(meta.get("event_type") or event.get("type") or "unknown")
    scene = dict((user.get("observation") or {}).get("scene") or {})
    return str(scene.get(field) or "unknown")


def evaluate(
    *,
    dataset_dir: Path,
    split: str,
    model_path: str,
    adapter: str | None,
    max_samples: int | None,
    max_new_tokens: int,
    sft_dirname: str = "sft",
    prompt_completion_dirname: str | None = None,
) -> dict[str, Any]:
    from algorithms.traffic_llm.training.cuda_env import ensure_cuda_runtime_libs

    ensure_cuda_runtime_libs()
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    pc_dir = dataset_dir / (prompt_completion_dirname or "prompt_completion")
    sft_dir = dataset_dir / sft_dirname
    path = pc_dir / f"{split}.jsonl"
    if not path.is_file():
        path = sft_dir / f"{split}.jsonl"
    samples = list(read_jsonl(path))
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
    seq_match = 0
    seq_total = 0
    region_match = 0
    whole_plan = 0
    f1_sum = 0.0
    grouped: dict[str, dict[str, dict[str, float]]] = {
        "event": defaultdict(lambda: defaultdict(float)),
        "period": defaultdict(lambda: defaultdict(float)),
        "scope": defaultdict(lambda: defaultdict(float)),
    }
    examples: list[dict[str, Any]] = []
    for sample in samples:
        n += 1
        messages, user, teacher = _sample_parts(sample)
        meta = dict(sample.get("metadata") or {})
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
        observation = dict(user.get("observation") or {})
        allowed_region = set(observation.get("controlled_region") or ())
        pred = _extract_json(decoded)
        sample_flags = {
            "json": 0.0,
            "schema": 0.0,
            "phase": 0.0,
            "region": 0.0,
            "fallback": 0.0,
            "slot": 0.0,
            "seq": 0.0,
            "whole": 0.0,
            "f1": 0.0,
            "n": 1.0,
        }
        if pred is not None:
            json_ok += 1
            sample_flags["json"] = 1.0
        parsed = None
        if pred is not None:
            try:
                parsed = AIControlPlan.from_mapping(pred, config=policy)
                schema_ok += 1
                sample_flags["schema"] = 1.0
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
                sample_flags["phase"] = 1.0
            if set(parsed.controlled_intersections) <= allowed_region:
                region_ok += 1
                sample_flags["region"] = 1.0
            if bool(parsed.fallback_to_baseline) == bool(teacher.get("fallback_to_baseline")):
                fallback_ok += 1
                sample_flags["fallback"] = 1.0
            m, t = _slot_agreement(pred, teacher)
            slot_match += m
            slot_total += t
            sample_flags["slot"] = (m / t) if t else 0.0
            sm, st = _sequence_exact(pred, teacher)
            seq_match += sm
            seq_total += st
            sample_flags["seq"] = (sm / st) if st else 0.0
            teacher_ctrl = set(teacher.get("controlled_intersections") or ())
            pred_ctrl = set(parsed.controlled_intersections)
            if pred_ctrl == teacher_ctrl:
                region_match += 1
            sample_f1 = _f1(pred_ctrl, teacher_ctrl)
            f1_sum += sample_f1
            sample_flags["f1"] = sample_f1
            if (
                pred_ctrl == teacher_ctrl
                and bool(parsed.fallback_to_baseline) == bool(teacher.get("fallback_to_baseline"))
                and dict(pred.get("signal_plan") or {}) == dict(teacher.get("signal_plan") or {})
            ):
                whole_plan += 1
                sample_flags["whole"] = 1.0
        for field in ("event", "period", "scope"):
            key = _group_key(user, meta, field)
            bucket = grouped[field][key]
            for metric, value in sample_flags.items():
                bucket[metric] += value
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

    grouped_rates: dict[str, dict[str, dict[str, float]]] = {}
    for field, buckets in grouped.items():
        grouped_rates[field] = {}
        for key, stats in buckets.items():
            count = max(1.0, stats.get("n") or 1.0)
            grouped_rates[field][key] = {
                metric: (value / count) for metric, value in stats.items() if metric != "n"
            }
            grouped_rates[field][key]["n"] = stats.get("n") or 0.0

    return {
        "n": n,
        "adapter": adapter,
        "split": split,
        "json_parse_rate": rate(json_ok),
        "aicontrolplan_valid_rate": rate(schema_ok),
        "phase_legality_rate": rate(phase_ok),
        "controlled_region_legality_rate": rate(region_ok),
        "fallback_accuracy": rate(fallback_ok),
        "teacher_phase_slot_agreement": (slot_match / slot_total) if slot_total else 0.0,
        "per_intersection_phase_sequence_exact_match": (seq_match / seq_total) if seq_total else 0.0,
        "whole_plan_exact_match": rate(whole_plan),
        "controlled_intersection_exact_match": rate(region_match),
        "controlled_intersection_f1": (f1_sum / n) if n else 0.0,
        "grouped": grouped_rates,
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
    parser.add_argument("--sft-dir", default="sft")
    parser.add_argument("--prompt-completion-dir", default="")
    args = parser.parse_args(argv)
    report = evaluate(
        dataset_dir=Path(args.dataset),
        split=args.split,
        model_path=args.model,
        adapter=args.adapter or None,
        max_samples=args.max_samples,
        max_new_tokens=args.max_new_tokens,
        sft_dirname=args.sft_dir,
        prompt_completion_dirname=args.prompt_completion_dir or None,
    )
    dump_json(Path(args.output), report)
    print(json.dumps({k: v for k, v in report.items() if k != "example"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
