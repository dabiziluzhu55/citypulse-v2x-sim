"""CLI for offline Traffic-Qwen closed-loop SUMO evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from algorithms.traffic_llm.dataset.io_utils import dump_json, load_json, load_yaml, read_jsonl
from algorithms.traffic_llm.dataset.schema import ScenarioSpec
from algorithms.traffic_llm.evaluation.closed_loop import run_closed_loop_episode
from algorithms.traffic_llm.evaluation.compare import compare_closed_loop
from algorithms.traffic_llm.evaluation.report import decide_next_step, render_markdown


DEFAULT_MODEL = "/home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct"
DEFAULT_ADAPTER = (
    "outputs/traffic_llm_dataset/formal_v1/training/qlora_formal_v1/adapter"
)


def _holdout_scenarios(dataset_dir: Path, *, seed: int = 42003) -> list[ScenarioSpec]:
    split = load_json(dataset_dir / "split_manifest.json")
    assignment = dict(split.get("assignment") or {})
    rows = []
    for item in read_jsonl(dataset_dir / "scenarios.jsonl"):
        spec = ScenarioSpec.from_dict(item)
        if assignment.get(spec.scenario_group_id) != "test":
            continue
        if int(spec.seed) != int(seed):
            continue
        rows.append(spec)
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)

    loop = sub.add_parser("closed-loop", help="Run holdout closed-loop SUMO with a frozen Qwen policy")
    loop.add_argument("--dataset", default="outputs/traffic_llm_dataset/formal_v1")
    loop.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    loop.add_argument("--policy", required=True, choices=("traffic_qwen", "base_qwen"))
    loop.add_argument("--model", default=DEFAULT_MODEL)
    loop.add_argument("--adapter", default=DEFAULT_ADAPTER)
    loop.add_argument("--output", default="")
    loop.add_argument("--limit", type=int, default=None)
    loop.add_argument("--scenario-ids", default="")
    loop.add_argument("--resume", action="store_true")
    loop.add_argument("--seed", type=int, default=42003)

    report = sub.add_parser("report", help="Aggregate Traffic-Qwen vs Base/Fixed/MP/expert")
    report.add_argument("--dataset", default="outputs/traffic_llm_dataset/formal_v1")
    report.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    report.add_argument(
        "--closed-loop-dir",
        default="outputs/traffic_llm_dataset/formal_v1/closed_loop",
    )
    report.add_argument("--seed", type=int, default=42003)
    return parser


def _ids(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "closed-loop":
        from algorithms.traffic_llm.evaluation.policy import FrozenQwenPolicy

        dataset_dir = Path(args.dataset)
        scoring = load_yaml(args.scoring)
        scenarios = _holdout_scenarios(dataset_dir, seed=args.seed)
        allow = set(_ids(args.scenario_ids))
        if allow:
            scenarios = [item for item in scenarios if item.scenario_id in allow]
        if args.limit:
            scenarios = scenarios[: int(args.limit)]
        output = Path(args.output or (dataset_dir / "closed_loop" / args.policy))
        output.mkdir(parents=True, exist_ok=True)
        adapter = None if args.policy == "base_qwen" else args.adapter
        print(
            json.dumps(
                {
                    "policy": args.policy,
                    "n_scenarios": len(scenarios),
                    "adapter": adapter,
                    "output": str(output),
                    "resume": bool(args.resume),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        policy = FrozenQwenPolicy(args.model, adapter=adapter)
        completed = 0
        failed = 0
        skipped = 0
        for idx, spec in enumerate(scenarios, start=1):
            run_path = output / "runs" / f"{spec.scenario_id}_{args.policy}.json"
            if args.resume and run_path.is_file():
                existing = load_json(run_path)
                if existing.get("state") == "COMPLETED":
                    skipped += 1
                    completed += 1
                    print(
                        f"[{idx}/{len(scenarios)}] skip {spec.scenario_id} already COMPLETED",
                        flush=True,
                    )
                    continue
            result = run_closed_loop_episode(
                spec,
                policy=policy,
                policy_name=args.policy,
                output_dir=output,
                scoring=scoring,
            )
            if result.get("state") == "COMPLETED":
                completed += 1
            else:
                failed += 1
            print(
                json.dumps(
                    {
                        "idx": idx,
                        "n": len(scenarios),
                        "scenario_id": spec.scenario_id,
                        "state": result.get("state"),
                        "n_plans": result.get("n_plans"),
                        "fallback_rate": result.get("fallback_rate"),
                        "elapsed_wall_s": result.get("elapsed_wall_s"),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        summary = {
            "policy": args.policy,
            "completed": completed,
            "failed": failed,
            "skipped_completed": skipped,
            "n": len(scenarios),
        }
        dump_json(output / "run_summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if failed == 0 else 2
    if args.command == "report":
        dataset_dir = Path(args.dataset)
        scoring = load_yaml(args.scoring)
        scenarios = [item.to_dict() for item in _holdout_scenarios(dataset_dir, seed=args.seed)]
        report = compare_closed_loop(
            dataset_dir=dataset_dir,
            closed_loop_root=Path(args.closed_loop_dir),
            scoring=scoring,
            scenarios=scenarios,
        )
        report["decision"] = decide_next_step(report)
        reports = dataset_dir / "reports"
        dump_json(reports / "closed_loop_eval.json", report)
        markdown = render_markdown(report)
        (reports / "closed_loop_eval.md").write_text(markdown, encoding="utf-8")
        keep = (
            "n_scenarios",
            "n_llm_completed",
            "n_base_completed",
            "json_ok_rate",
            "schema_ok_rate",
            "phase_ok_rate",
            "region_ok_rate",
            "fallback_rate",
            "invalid_plan_rate",
            "inference_latency_ms",
            "overall",
            "by_event",
            "by_period",
            "by_scope",
            "decision",
        )
        print(json.dumps({key: report.get(key) for key in keep}, ensure_ascii=False, indent=2))
        print(markdown)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
