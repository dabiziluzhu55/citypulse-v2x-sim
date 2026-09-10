"""Benchmark a running Traffic-Qwen OpenAI-compatible vLLM server."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from algorithms.traffic_llm.dataset.io_utils import dump_json, read_jsonl
from algorithms.traffic_llm.deployment.client import (
    chat_completion,
    list_models,
    messages_from_sft_sample,
    stream_chat_completion,
)
from algorithms.traffic_llm.deployment.paths import (
    DEFAULT_PORT,
    DEPLOY_REPORTS,
    PROMPT_COMPLETION_DIR,
    SERVED_MODEL_NAME,
    TRANSFORMERS_P50_MS,
    TRANSFORMERS_P95_MS,
)


def _percentile(values: Sequence[float], pct: float) -> float | None:
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


def gpu_memory_mib() -> dict[str, Any]:
    if not shutil.which("nvidia-smi"):
        return {}
    raw = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=index,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    )
    rows = []
    for line in raw.strip().splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 4:
            continue
        rows.append(
            {
                "index": int(parts[0]),
                "memory_used_mib": float(parts[1]),
                "memory_total_mib": float(parts[2]),
                "utilization_pct": float(parts[3]),
            }
        )
    return {"gpus": rows}


def load_val_samples(limit: int) -> list[dict[str, Any]]:
    path = PROMPT_COMPLETION_DIR / "val.jsonl"
    rows = list(read_jsonl(path))
    if limit:
        rows = rows[: int(limit)]
    if not rows:
        raise SystemExit(f"no Observation V2 val samples at {path}")
    return rows


def benchmark(
    *,
    base_url: str,
    model: str,
    n: int,
    warmup: int,
    structured: bool,
) -> dict[str, Any]:
    health = list_models(base_url)
    samples = load_val_samples(max(n + warmup, 1))
    warmup_n = min(warmup, len(samples))
    bench = samples[warmup_n : warmup_n + n] or samples[:n]
    for sample in samples[:warmup_n]:
        chat_completion(
            base_url,
            messages_from_sft_sample(sample),
            model=model,
            structured=structured,
        )
    ttft: list[float] = []
    total: list[float] = []
    tps: list[float] = []
    completion_tokens: list[float] = []
    for sample in bench:
        result = stream_chat_completion(
            base_url,
            messages_from_sft_sample(sample),
            model=model,
            structured=structured,
        )
        if result.get("ttft_ms") is not None:
            ttft.append(float(result["ttft_ms"]))
        total.append(float(result["total_ms"]))
        usage = dict(result.get("usage") or {})
        out_tokens = float(usage.get("completion_tokens") or 0)
        completion_tokens.append(out_tokens)
        elapsed_s = float(result["total_ms"]) / 1000.0
        if out_tokens > 0 and elapsed_s > 0:
            tps.append(out_tokens / elapsed_s)
    p50 = _percentile(total, 50)
    p95 = _percentile(total, 95)
    return {
        "base_url": base_url,
        "model": model,
        "n": len(bench),
        "warmup": warmup_n,
        "structured": structured,
        "models_endpoint": health,
        "gpu": gpu_memory_mib(),
        "ttft_ms": {
            "p50": _percentile(ttft, 50),
            "p95": _percentile(ttft, 95),
            "mean": (sum(ttft) / len(ttft)) if ttft else None,
            "n": len(ttft),
        },
        "total_latency_ms": {
            "p50": p50,
            "p95": p95,
            "mean": (sum(total) / len(total)) if total else None,
            "max": max(total) if total else None,
            "n": len(total),
        },
        "tokens_per_s": {
            "p50": _percentile(tps, 50),
            "mean": (sum(tps) / len(tps)) if tps else None,
            "n": len(tps),
        },
        "completion_tokens_mean": (sum(completion_tokens) / len(completion_tokens))
        if completion_tokens
        else None,
        "transformers_offline": {"p50_ms": TRANSFORMERS_P50_MS, "p95_ms": TRANSFORMERS_P95_MS},
        "speedup_vs_transformers": {
            "p50": None if not p50 else TRANSFORMERS_P50_MS / p50,
            "p95": None if not p95 else TRANSFORMERS_P95_MS / p95,
            "p50_delta_ms": None if not p50 else TRANSFORMERS_P50_MS - p50,
            "p95_delta_ms": None if not p95 else TRANSFORMERS_P95_MS - p95,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.benchmark_server")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")
    parser.add_argument("--model", default=SERVED_MODEL_NAME)
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--no-structured", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--tag", default="lora")
    args = parser.parse_args(argv)
    t0 = time.perf_counter()
    report = benchmark(
        base_url=args.base_url,
        model=args.model,
        n=int(args.n),
        warmup=int(args.warmup),
        structured=not args.no_structured,
    )
    report["wall_s"] = time.perf_counter() - t0
    report["tag"] = args.tag
    output = Path(args.output) if args.output else DEPLOY_REPORTS / f"benchmark_{args.tag}.json"
    dump_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
