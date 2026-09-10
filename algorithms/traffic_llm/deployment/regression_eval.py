"""Structured regression: LoRA / merged / AWQ vs frozen formal_v2 val.

Uses Observation V2 val.jsonl (70). Never uses seed=44001 as calibration.
Slot agreement drop > 5pp vs LoRA baseline stops the pipeline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import AIControlConfig, AIControlPlan, AIControlValidationError

from algorithms.traffic_llm.dataset.catalog import tls_phase_orders
from algorithms.traffic_llm.dataset.io_utils import dump_json, read_jsonl
from algorithms.traffic_llm.deployment.client import (
    chat_completion,
    decode_message,
    messages_from_sft_sample,
)
from algorithms.traffic_llm.deployment.paths import (
    DEFAULT_PORT,
    DEPLOY_REPORTS,
    PROMPT_COMPLETION_DIR,
    SERVED_MODEL_NAME,
    SLOT_DROP_HALT,
)
from algorithms.traffic_llm.evaluation.policy import extract_json_object
from algorithms.traffic_llm.training.eval_structured import _sample_parts, _slot_agreement


def evaluate_vllm(
    *,
    base_url: str,
    model: str,
    samples: Sequence[Mapping[str, Any]],
    structured: bool = True,
) -> dict[str, Any]:
    allowed = tls_phase_orders()
    policy = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
    n = 0
    json_ok = 0
    schema_ok = 0
    phase_ok = 0
    region_ok = 0
    slot_match = 0
    slot_total = 0
    seq_exact = 0
    errors: list[str] = []
    for sample in samples:
        n += 1
        _messages, user, teacher = _sample_parts(sample)
        observation = dict(user.get("observation") or {})
        allowed_region = set(observation.get("controlled_region") or ())
        payload = chat_completion(
            base_url,
            messages_from_sft_sample(sample),
            model=model,
            structured=structured,
            timeout_s=180.0,
        )
        decoded = decode_message(payload)
        pred = extract_json_object(decoded)
        if pred is None:
            errors.append("json_parse")
            continue
        json_ok += 1
        try:
            parsed = AIControlPlan.from_mapping(pred, config=policy)
            schema_ok += 1
        except (AIControlValidationError, Exception) as exc:
            errors.append(f"schema:{exc}")
            continue
        illegal = False
        for iid, phases in parsed.signal_plan.items():
            valid = {int(item) for item in allowed.get(iid) or ()}
            if valid and set(int(p) for p in phases) - valid:
                illegal = True
        if not illegal:
            phase_ok += 1
        if set(parsed.controlled_intersections) <= allowed_region:
            region_ok += 1
        m, t = _slot_agreement(pred, teacher)
        slot_match += m
        slot_total += t
        teacher_plan = dict(teacher.get("signal_plan") or {})
        pred_plan = dict(pred.get("signal_plan") or {})
        if teacher_plan == pred_plan and set(parsed.controlled_intersections) == set(
            teacher.get("controlled_intersections") or ()
        ):
            seq_exact += 1

    def rate(num: int) -> float:
        return num / n if n else 0.0

    return {
        "n": n,
        "json_parse_rate": rate(json_ok),
        "aicontrolplan_valid_rate": rate(schema_ok),
        "phase_legality_rate": rate(phase_ok),
        "controlled_region_legality_rate": rate(region_ok),
        "teacher_phase_slot_agreement": (slot_match / slot_total) if slot_total else 0.0,
        "whole_plan_exact_match": rate(seq_exact),
        "n_errors_logged": min(8, len(errors)),
        "error_head": errors[:8],
    }


def compare_runs(baseline: Mapping[str, Any], other: Mapping[str, Any], *, name: str) -> dict[str, Any]:
    slot_base = float(baseline.get("teacher_phase_slot_agreement") or 0.0)
    slot_other = float(other.get("teacher_phase_slot_agreement") or 0.0)
    drop = slot_base - slot_other
    halt = drop > SLOT_DROP_HALT
    return {
        "name": name,
        "slot_agreement_baseline": slot_base,
        "slot_agreement": slot_other,
        "slot_drop": drop,
        "halt": halt,
        "halt_reason": (
            f"slot agreement dropped {drop:.3f} > {SLOT_DROP_HALT:.3f}"
            if halt
            else None
        ),
    }


def load_val(limit: int | None) -> list[dict[str, Any]]:
    rows = list(read_jsonl(PROMPT_COMPLETION_DIR / "val.jsonl"))
    if limit is not None:
        rows = rows[: int(limit)]
    if not rows:
        raise SystemExit("formal_v2 val.jsonl is empty")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.regression_eval")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")
    parser.add_argument("--model", default=SERVED_MODEL_NAME)
    parser.add_argument("--tag", default="lora")
    parser.add_argument("--baseline", default="", help="JSON from a previous --tag run")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-structured", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    samples = load_val(args.limit)
    report = evaluate_vllm(
        base_url=args.base_url,
        model=args.model,
        samples=samples,
        structured=not args.no_structured,
    )
    report["tag"] = args.tag
    report["split"] = "formal_v2_val"
    report["holdout_44001_used"] = False
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        report["vs_baseline"] = compare_runs(baseline, report, name=args.tag)
    output = Path(args.output) if args.output else DEPLOY_REPORTS / f"regression_{args.tag}.json"
    dump_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {output}")
    if (report.get("vs_baseline") or {}).get("halt"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
