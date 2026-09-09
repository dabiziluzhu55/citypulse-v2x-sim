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
from .sft_builder import FUTURE_LEAK_PATTERNS, SYSTEM_PROMPT
from .split import leak_check


FUTURE_USER_KEYS = (
    "recovery_time_s",
    "recovery",
    "winner_score",
    "composite_score",
    "traffic_performance_index",
    "post_event_avg_queue",
)


def _percentile(values: Sequence[int], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


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


def audit_sft(
    output_dir: Path,
    *,
    model_path: str | None = None,
) -> dict[str, Any]:
    paths = {
        "train": output_dir / "sft" / "train.jsonl",
        "val": output_dir / "sft" / "val.jsonl",
        "test": output_dir / "sft" / "test.jsonl",
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
    }
    token_lens: list[int] = []
    n_total = 0

    all_samples: list[dict[str, Any]] = []
    for split, path in paths.items():
        rows = list(read_jsonl(path)) if path.is_file() else []
        samples_by_split[split] = rows
        for idx, sample in enumerate(rows):
            n_total += 1
            distributions["split"][split] += 1
            prefix = f"{split}:{idx}"
            messages = sample.get("messages") or []
            if len(messages) != 3:
                errors.append(f"{prefix} messages length != 3")
                continue
            if messages[0].get("content") != SYSTEM_PROMPT:
                errors.append(f"{prefix} system prompt mismatch")
            try:
                user_payload = json.loads(messages[1]["content"])
                assistant_payload = json.loads(messages[2]["content"])
            except Exception as exc:
                errors.append(f"{prefix} JSON parse failed: {exc}")
                continue
            observation = dict(user_payload.get("observation") or {})
            user_raw = messages[1]["content"]
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
            meta = dict(sample.get("metadata") or {})
            if meta.get("ambiguous"):
                errors.append(f"{prefix} ambiguous expert entered SFT")
            if meta.get("action_space") == "signal_vehicle":
                errors.append(f"{prefix} signal_vehicle expert entered Signal SFT")
            distributions["event"][str(meta.get("event_type") or "unknown")] += 1
            distributions["teacher"][str(meta.get("teacher") or "unknown")] += 1
            scene = dict((observation.get("scene") or {}))
            distributions["period"][str(scene.get("period") or "unknown")] += 1
            distributions["scope"][str(scene.get("scope") or "unknown")] += 1
            prompt = "\n".join(str(item.get("content") or "") for item in messages[:2])
            token_lens.append(_token_len(prompt, tokenizer))
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
    checks["n_samples"] = n_total
    passed = n_total > 0 and not errors
    report = {
        "passed": passed,
        "n_samples": n_total,
        "n_errors": len(errors),
        "errors": errors[:200],
        "checks": checks,
        "counts": {key: dict(value) for key, value in distributions.items()},
        "prompt_tokens": {
            "n": len(token_lens),
            "p50": _percentile(token_lens, 50),
            "p95": _percentile(token_lens, 95),
            "max": max(token_lens) if token_lens else None,
            "tokenizer": model_path or "char/4 fallback",
        },
    }
    reports = output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    dump_json(reports / "sft_audit.json", report)
    lines = ["# SFT audit", ""]
    lines.append(f"- passed: **{'PASS' if passed else 'FAIL'}**")
    lines.append(f"- n_samples: {n_total}")
    lines.append(f"- n_errors: {len(errors)}")
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
    if errors:
        lines.append("")
        lines.append("## Errors (truncated)")
        for item in errors[:50]:
            lines.append(f"- {item}")
    (reports / "sft_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
