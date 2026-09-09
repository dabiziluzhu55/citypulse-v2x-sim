"""Traffic-Qwen dataset CLI (no FastAPI / no Qwen training)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .io_utils import load_yaml
from .pipeline import (
    build_sft,
    generate_dataset,
    plan_job,
    score_existing,
    select_teachers,
)
from .sft_audit import audit_sft


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.dataset.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Count scenarios without running SUMO")
    plan.add_argument("--config", required=True)
    plan.add_argument("--smoke", action="store_true")
    plan.add_argument("--modes", default="")

    gen = sub.add_parser("generate", help="Run SUMO episodes and build dataset")
    gen.add_argument("--config", required=True)
    gen.add_argument("--scoring", default="")
    gen.add_argument("--output", default="")
    gen.add_argument("--limit-scenarios", type=int, default=None)
    gen.add_argument("--modes", default="")
    gen.add_argument("--smoke", action="store_true")
    gen.add_argument("--workers", type=int, default=None)
    gen.add_argument("--resume", action="store_true", help="Skip COMPLETED runs; do not overwrite valid results")
    gen.add_argument("--retry-failed", action="store_true", help="Re-run FAILED pairs; default leaves them in place")
    gen.add_argument("--scenario-ids", default="", help="Comma-separated scenario ids to run")

    score = sub.add_parser("score", help="Re-score / re-select from existing runs")
    score.add_argument("--dataset", required=True)
    score.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")

    select = sub.add_parser("select", help="Re-run teacher selection")
    select.add_argument("--dataset", required=True)
    select.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")

    sft = sub.add_parser("build-sft", help="Rebuild SFT JSONL from existing runs")
    sft.add_argument("--dataset", required=True)
    sft.add_argument("--config", default="algorithms/traffic_llm/configs/dataset_v1.yaml")
    sft.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    sft.add_argument("--observation-version", default="v1", choices=("v1", "v2"))
    sft.add_argument("--sft-dir", default="")
    sft.add_argument("--write-prompt-completion", action="store_true")

    sft_v2 = sub.add_parser("build-sft-v2", help="Rebuild Observation V2 SFT without overwriting Pilot V1")
    sft_v2.add_argument("--dataset", required=True)
    sft_v2.add_argument("--config", default="algorithms/traffic_llm/configs/pilot_v2.yaml")
    sft_v2.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    sft_v2.add_argument("--sft-dir", default="sft_v2")

    audit = sub.add_parser("audit", help="Audit frozen Signal SFT JSONL")
    audit.add_argument("--dataset", required=True)
    audit.add_argument("--model", default="")
    audit.add_argument("--sft-dir", default="sft")
    audit.add_argument("--prompt-completion-dir", default="")
    audit.add_argument("--max-length", type=int, default=None)
    return parser


def _modes(raw: str) -> list[str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        config = load_yaml(args.config)
        result = plan_job(
            config,
            profile="smoke" if args.smoke else None,
            modes=_modes(args.modes),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "\n".join(
                [
                    f"总场景数: {result['n_scenarios']}",
                    f"每类事件数: {result['event_type_counts']}",
                    f"每时段数量: {result['period_counts']}",
                    f"每scope数量: {result['scope_counts']}",
                    f"预计总仿真episode数量: {result['estimated_episodes']}",
                    f"各算法预计运行次数: {result['estimated_algorithm_runs']}",
                    f"候选算法: {result['control_modes']}",
                ]
            )
        )
        return 0
    if args.command == "generate":
        config = load_yaml(args.config)
        scoring_path = args.scoring or config.get("scoring_config")
        scoring = load_yaml(scoring_path)
        output = Path(args.output or config.get("output_dir") or "outputs/traffic_llm_dataset/v1")
        result = generate_dataset(
            config,
            scoring,
            output_dir=output,
            profile="smoke" if args.smoke else None,
            modes=_modes(args.modes),
            limit_scenarios=args.limit_scenarios,
            workers=args.workers,
            resume=bool(getattr(args, "resume", False)),
            retry_failed=bool(getattr(args, "retry_failed", False)),
            scenario_ids=_modes(getattr(args, "scenario_ids", "")),
        )
        print(
            "\n".join(
                [
                    f"completed: {result.get('completed')}",
                    f"skipped_completed: {result.get('skipped_completed')}",
                    f"skipped_failed: {result.get('skipped_failed')}",
                    f"failed: {result.get('failed')}",
                    f"remaining: {result.get('remaining')}",
                ]
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"score", "select"}:
        scoring = load_yaml(args.scoring)
        fn = score_existing if args.command == "score" else select_teachers
        result = fn(Path(args.dataset), scoring)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-sft":
        config = load_yaml(args.config)
        scoring = load_yaml(args.scoring)
        result = build_sft(
            Path(args.dataset),
            config,
            scoring,
            observation_version=args.observation_version,
            sft_dirname=args.sft_dir or None,
            write_prompt_completion=True if args.write_prompt_completion else None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-sft-v2":
        config = load_yaml(args.config)
        scoring = load_yaml(args.scoring)
        result = build_sft(
            Path(args.dataset),
            config,
            scoring,
            observation_version="v2",
            sft_dirname=args.sft_dir or "sft_v2",
            write_prompt_completion=True,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "audit":
        result = audit_sft(
            Path(args.dataset),
            model_path=args.model or None,
            sft_dirname=args.sft_dir,
            prompt_completion_dirname=args.prompt_completion_dir or None,
            max_length=args.max_length,
        )
        keep = (
            "passed",
            "n_samples",
            "n_errors",
            "checks",
            "counts",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "phase_service_complete_rate",
            "assistant_truncation_rate",
        )
        print(json.dumps({k: result[k] for k in keep if k in result}, ensure_ascii=False, indent=2))
        return 0 if result.get("passed") else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
