"""Audit frozen Signal SFT JSONL before any training run."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import AIControlPlan, AIControlConfig, AIControlValidationError

from .catalog import neighbor_map, tls_phase_orders
from .io_utils import dump_json, load_json, read_jsonl
from .phase_service import validate_phase_service
from .sft_builder import FUTURE_LEAK_PATTERNS, SYSTEM_PROMPT
from .split import leak_check
from .token_budget import assistant_truncated, tokenize_prompt_completion


FUTURE_USER_KEYS = (
    "recovery_time_s",
    "recovery",
    "winner_score",
    "composite_score",
    "traffic_performance_index",
    "post_event_avg_queue",
)


def _percentile(values: Sequence[int | float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(item) for item in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _token_stats(values: Sequence[int]) -> dict[str, Any]:
    ints = [int(item) for item in values]
    return {
        "n": len(ints),
        "p50": _percentile(ints, 50),
        "p95": _percentile(ints, 95),
        "max": max(ints) if ints else None,
    }


def _token_len(text: str, tokenizer: Any | None) -> int:
    if tokenizer is None:
        return max(1, len(text) // 4)
    encoded = tokenizer(text, add_special_tokens=False)
    ids = encoded["input_ids"] if isinstance(encoded, Mapping) else encoded
    return len(ids)


def _load_tokenizer(model_path: str | None) -> Any | None:
    if not model_path:
        return None
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    except Exception:
        return None


def _sample_parts(sample: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]:
    if sample.get("prompt") and sample.get("completion"):
        prompt = list(sample["prompt"])
        completion = list(sample["completion"])
        messages = prompt + completion
    else:
        messages = list(sample.get("messages") or [])
        prompt = [item for item in messages if item.get("role") in {"system", "user"}]
        completion = [item for item in messages if item.get("role") == "assistant"]
    user_payload: dict[str, Any] = {}
    assistant_payload: dict[str, Any] = {}
    user_raw = ""
    for item in prompt:
        if item.get("role") == "user":
            user_raw = str(item.get("content") or "")
            user_payload = json.loads(user_raw)
    for item in completion:
        if item.get("role") == "assistant":
            assistant_payload = json.loads(str(item.get("content") or ""))
    return messages, user_payload, assistant_payload, user_raw


def audit_sft(
    output_dir: Path,
    *,
    model_path: str | None = None,
    sft_dirname: str = "sft",
    prompt_completion_dirname: str | None = None,
    max_length: int | None = None,
) -> dict[str, Any]:
    sft_dir = output_dir / sft_dirname
    pc_dir = output_dir / (prompt_completion_dirname or "")
    if prompt_completion_dirname and pc_dir.is_dir():
        paths = {
            "train": pc_dir / "train.jsonl",
            "val": pc_dir / "val.jsonl",
            "test": pc_dir / "test.jsonl",
        }
    else:
        paths = {
            "train": sft_dir / "train.jsonl",
            "val": sft_dir / "val.jsonl",
            "test": sft_dir / "test.jsonl",
        }
    split_payload = load_json(output_dir / "split_manifest.json") if (output_dir / "split_manifest.json").is_file() else {}
    assignment = dict(split_payload.get("assignment") or {})
    allowed = tls_phase_orders()
    neighbors = neighbor_map()
    _ = neighbors
    policy = AIControlConfig(plan_valid_seconds=30.0, slot_seconds=5.0)
    tokenizer = _load_tokenizer(model_path)

    errors: list[str] = []
    checks: dict[str, Any] = {}
    samples_by_split: dict[str, list[dict[str, Any]]] = {}
    distributions: dict[str, Counter[str]] = {
        "event": Counter(),
        "period": Counter(),
        "scope": Counter(),
        "teacher": Counter(),
        "split": Counter(),
        "observation_version": Counter(),
    }
    split_event: dict[str, Counter[str]] = {
        "train": Counter(),
        "val": Counter(),
        "test": Counter(),
    }
    prompt_lens: list[int] = []
    completion_lens: list[int] = []
    total_lens: list[int] = []
    phase_service_ok = 0
    phase_service_n = 0
    truncated = 0
    n_total = 0
    holdout_leaks = 0
    holdout_seeds = {int(item) for item in split_payload.get("holdout_seeds") or ()}

    all_samples: list[dict[str, Any]] = []
    for split, path in paths.items():
        rows = list(read_jsonl(path)) if path.is_file() else []
        samples_by_split[split] = rows
        for idx, sample in enumerate(rows):
            n_total += 1
            distributions["split"][split] += 1
            prefix = f"{split}:{idx}"
            try:
                messages, user_payload, assistant_payload, user_raw = _sample_parts(sample)
            except Exception as exc:
                errors.append(f"{prefix} JSON parse failed: {exc}")
                continue
            if len(messages) != 3:
                errors.append(f"{prefix} messages length != 3")
                continue
            if messages[0].get("content") != SYSTEM_PROMPT:
                errors.append(f"{prefix} system prompt mismatch")
            observation = dict(user_payload.get("observation") or {})
            for key in FUTURE_USER_KEYS:
                if f'"{key}"' in user_raw:
                    errors.append(f"{prefix} observation contains future key {key}")
            reason = str(assistant_payload.get("reason") or "")
            for token in FUTURE_LEAK_PATTERNS:
                if token in reason:
                    errors.append(f"{prefix} assistant reason leaks {token!r}")
                    break
            if re.search(r"事件结束后约\s*\d+", reason):
                errors.append(f"{prefix} reason cites future recovery time")
            try:
                plan = AIControlPlan.from_mapping(assistant_payload, config=policy)
            except (AIControlValidationError, Exception) as exc:
                errors.append(f"{prefix} AIControlPlan invalid: {exc}")
                continue
            if abs(float(plan.valid_seconds) - 30.0) > 1e-6:
                errors.append(f"{prefix} valid_seconds != 30")
            for iid, phases in plan.signal_plan.items():
                if len(phases) != 6:
                    errors.append(f"{prefix} signal_plan[{iid}] length {len(phases)} != 6")
                allowed_phases = set(allowed.get(iid) or ())
                illegal = set(phases) - allowed_phases
                if allowed_phases and illegal:
                    errors.append(f"{prefix} illegal phases {sorted(illegal)} at {iid}")
            obs_controlled = set(observation.get("controlled_region") or ())
            if plan.controlled_intersections and not set(plan.controlled_intersections) <= obs_controlled:
                errors.append(f"{prefix} controlled_intersections outside observation region")
            obs_allowed = dict(observation.get("allowed_phases") or {})
            phase_service = dict(observation.get("phase_service") or {})
            if observation.get("observation_version") == "traffic_observation_v2":
                phase_service_n += 1
                service_errors = validate_phase_service(phase_service, allowed_phases=obs_allowed)
                if service_errors:
                    errors.extend(f"{prefix} {item}" for item in service_errors[:5])
                else:
                    phase_service_ok += 1
            meta = dict(sample.get("metadata") or {})
            if meta.get("ambiguous"):
                errors.append(f"{prefix} ambiguous expert entered SFT")
            if meta.get("action_space") == "signal_vehicle":
                errors.append(f"{prefix} signal_vehicle expert entered Signal SFT")
            seed = observation.get("scene", {}).get("seed")
            try:
                if holdout_seeds and int(seed) in holdout_seeds and split != "test":
                    holdout_leaks += 1
                    errors.append(f"{prefix} holdout seed {seed} in {split}")
            except (TypeError, ValueError):
                pass
            distributions["event"][str(meta.get("event_type") or "unknown")] += 1
            split_event.setdefault(split, Counter())[str(meta.get("event_type") or "unknown")] += 1
            distributions["teacher"][str(meta.get("teacher") or "unknown")] += 1
            distributions["observation_version"][str(observation.get("observation_version") or "unknown")] += 1
            scene = dict((observation.get("scene") or {}))
            distributions["period"][str(scene.get("period") or "unknown")] += 1
            distributions["scope"][str(scene.get("scope") or "unknown")] += 1
            prompt_msgs = [item for item in messages if item.get("role") in {"system", "user"}]
            completion_msgs = [item for item in messages if item.get("role") == "assistant"]
            if tokenizer is not None:
                counts = tokenize_prompt_completion(tokenizer, prompt_msgs, completion_msgs)
                prompt_lens.append(int(counts["prompt_tokens"]))
                completion_lens.append(int(counts["completion_tokens"]))
                total_lens.append(int(counts["total_tokens"]))
                if max_length is not None and assistant_truncated(counts, max_length):
                    truncated += 1
                    errors.append(
                        f"{prefix} assistant truncated at max_length={max_length} "
                        f"total={counts['total_tokens']}"
                    )
            else:
                prompt_text = "\n".join(str(item.get("content") or "") for item in prompt_msgs)
                completion_text = "\n".join(str(item.get("content") or "") for item in completion_msgs)
                p_n = _token_len(prompt_text, None)
                c_n = _token_len(completion_text, None)
                prompt_lens.append(p_n)
                completion_lens.append(c_n)
                total_lens.append(p_n + c_n)
            all_samples.append(sample)

    leak_errors = leak_check(all_samples, assignment) if assignment else []
    errors.extend(leak_errors)
    checks["json_parseable"] = not any("JSON parse failed" in item for item in errors)
    checks["aicontrolplan_valid"] = not any("AIControlPlan invalid" in item for item in errors)
    checks["valid_seconds_30"] = not any("valid_seconds" in item for item in errors)
    checks["signal_plan_len_6"] = not any("length" in item and "!= 6" in item for item in errors)
    checks["phase_in_allowed"] = not any("illegal phases" in item for item in errors)
    checks["controlled_in_scope"] = not any("outside observation" in item for item in errors)
    checks["no_group_leak"] = not leak_errors
    checks["no_ambiguous"] = not any("ambiguous expert" in item for item in errors)
    checks["no_signal_vehicle"] = not any("signal_vehicle" in item for item in errors)
    checks["no_future_in_user"] = not any("future key" in item for item in errors)
    checks["no_future_in_reason"] = not any("leaks" in item or "recovery time" in item for item in errors)
    checks["no_holdout_in_train_val"] = holdout_leaks == 0
    checks["phase_service_complete"] = (phase_service_n == 0) or (phase_service_ok == phase_service_n)
    checks["assistant_truncation_rate"] = (truncated / n_total) if n_total else 0.0
    checks["n_samples"] = n_total
    train_event_counts = dict(split_event.get("train") or {})
    all_events = sorted(set(distributions["event"]) | set(train_event_counts))
    sparse_train_events = {
        event: int(train_event_counts.get(event, 0))
        for event in all_events
        if int(train_event_counts.get(event, 0)) < 8
    }
    checks["train_event_counts"] = train_event_counts
    checks["sparse_train_events"] = sparse_train_events
    halt_for_sparse_events = bool(sparse_train_events)
    passed = n_total > 0 and not errors and checks["assistant_truncation_rate"] == 0.0
    report = {
        "passed": passed,
        "halt_training_recommended": halt_for_sparse_events,
        "n_samples": n_total,
        "n_errors": len(errors),
        "errors": errors[:200],
        "checks": checks,
        "counts": {key: dict(value) for key, value in distributions.items()},
        "counts_by_split": {
            "event": {split: dict(counter) for split, counter in split_event.items()},
        },
        "prompt_tokens": {**_token_stats(prompt_lens), "tokenizer": model_path or "char/4 fallback"},
        "completion_tokens": _token_stats(completion_lens),
        "total_tokens": _token_stats(total_lens),
        "phase_service_complete_rate": (phase_service_ok / phase_service_n) if phase_service_n else None,
        "assistant_truncation_rate": checks["assistant_truncation_rate"],
        "max_length": max_length,
        "sft_dirname": sft_dirname,
        "prompt_completion_dirname": prompt_completion_dirname,
    }
    reports = output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    dump_json(reports / f"sft_audit_{sft_dirname}.json", report)
    if sft_dirname == "sft":
        dump_json(reports / "sft_audit.json", report)
    lines = ["# SFT audit", ""]
    lines.append(f"- passed: **{'PASS' if passed else 'FAIL'}**")
    lines.append(f"- n_samples: {n_total}")
    lines.append(f"- n_errors: {len(errors)}")
    lines.append(f"- assistant_truncation_rate: {report['assistant_truncation_rate']}")
    lines.append("")
    lines.append("## Checks")
    for key, value in checks.items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Distributions")
    for key, value in report["counts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append(f"## Prompt tokens: `{report['prompt_tokens']}`")
    lines.append(f"## Completion tokens: `{report['completion_tokens']}`")
    lines.append(f"## Total tokens: `{report['total_tokens']}`")
    if errors:
        lines.append("")
        lines.append("## Errors (truncated)")
        for item in errors[:50]:
            lines.append(f"- {item}")
    (reports / f"sft_audit_{sft_dirname}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
