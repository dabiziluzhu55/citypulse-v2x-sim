"""15-scenario AWQ-vLLM SUMO closed-loop subset. Does not modify Backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from algorithms.traffic_llm.dataset.io_utils import dump_json, load_json, load_yaml, read_jsonl
from algorithms.traffic_llm.dataset.schema import ScenarioSpec
from algorithms.traffic_llm.deployment.client import VllmHttpPolicy
from algorithms.traffic_llm.deployment.paths import (
    DEFAULT_PORT,
    DEPLOY_REPORTS,
    HOLDOUT_DIR,
    SERVED_MODEL_NAME,
)
from algorithms.traffic_llm.evaluation.cli import _ids, _scenarios_for_seed
from algorithms.traffic_llm.evaluation.closed_loop import run_closed_loop_episode
from algorithms.traffic_llm.evaluation.compare import compare_closed_loop
from algorithms.traffic_llm.evaluation.report import decide_next_step, render_markdown


EVENTS = (
    "lane_closure",
    "speed_limit",
    "accident",
    "major_event_opening",
    "major_event_closing",
)
SCOPES = ("east_dense", "west_dense", "xiongan_20")
PERIODS = ("morning_peak", "off_peak", "evening_peak")


def select_stratified_15(scenarios: list[ScenarioSpec]) -> list[ScenarioSpec]:
    """One cell per event×scope; rotate period so all 3 periods appear."""

    buckets: dict[tuple[str, str, str], ScenarioSpec] = {}
    for spec in scenarios:
        key = (spec.period, spec.scope, spec.event.event_type)
        buckets.setdefault(key, spec)
    selected: list[ScenarioSpec] = []
    for event_idx, event in enumerate(EVENTS):
        for scope_idx, scope in enumerate(SCOPES):
            period = PERIODS[(event_idx + scope_idx) % len(PERIODS)]
            spec = buckets.get((period, scope, event))
            if spec is None:
                raise SystemExit(f"missing scenario for {period}/{scope}/{event}")
            selected.append(spec)
    return selected


def coverage(scenarios: list[ScenarioSpec]) -> dict[str, Any]:
    return {
        "n": len(scenarios),
        "ids": [item.scenario_id for item in scenarios],
        "events": sorted({item.event.event_type for item in scenarios}),
        "scopes": sorted({item.scope for item in scenarios}),
        "periods": sorted({item.period for item in scenarios}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.deployment.closed_loop_vllm")
    parser.add_argument("--dataset", default=str(HOLDOUT_DIR))
    parser.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{DEFAULT_PORT}")
    parser.add_argument("--model", default=SERVED_MODEL_NAME)
    parser.add_argument("--policy", default="traffic_qwen_v2_awq")
    parser.add_argument("--seed", type=int, default=44001)
    parser.add_argument("--split", default="test")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--scenario-ids", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    dataset_dir = Path(args.dataset)
    scoring = load_yaml(args.scoring)
    all_scenarios = _scenarios_for_seed(dataset_dir, seed=args.seed, split=str(args.split))
    allow = set(_ids(args.scenario_ids))
    scenarios = (
        [item for item in all_scenarios if item.scenario_id in allow]
        if allow
        else select_stratified_15(all_scenarios)
    )
    output = Path(args.output or (dataset_dir / "closed_loop" / args.policy))
    output.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"policy": args.policy, **coverage(scenarios), "output": str(output)}, ensure_ascii=False), flush=True)
    policy = VllmHttpPolicy(args.base_url, model=args.model)
    completed = 0
    failed = 0
    skipped = 0
    for idx, spec in enumerate(scenarios, start=1):
        run_path = output / "runs" / f"{spec.scenario_id}_{args.policy}.json"
        if args.resume and run_path.is_file() and load_json(run_path).get("state") == "COMPLETED":
            skipped += 1
            completed += 1
            print(f"[{idx}/{len(scenarios)}] skip {spec.scenario_id}", flush=True)
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
                    "error": result.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    dump_json(
        output / "run_summary.json",
        {"policy": args.policy, "completed": completed, "failed": failed, "skipped_completed": skipped, "n": len(scenarios)},
    )
    report = compare_closed_loop(
        dataset_dir=dataset_dir,
        closed_loop_root=dataset_dir / "closed_loop",
        scoring=scoring,
        scenarios=[item.to_dict() for item in scenarios],
        llm_policy=args.policy,
        base_policy="base_qwen",
        v1_policy="traffic_qwen_v1",
    )
    report["decision"] = decide_next_step(report)
    report["subset"] = coverage(scenarios)
    reports = Path(DEPLOY_REPORTS)
    dump_json(reports / "closed_loop_awq_15.json", report)
    (reports / "closed_loop_awq_15.md").write_text(render_markdown(report), encoding="utf-8")
    keep = (
        "n_scenarios",
        "n_llm_completed",
        "n_invalid_event",
        "json_ok_rate",
        "schema_ok_rate",
        "phase_ok_rate",
        "region_ok_rate",
        "fallback_rate",
        "overall",
        "by_event",
        "by_scope",
        "by_period",
        "decision",
        "subset",
    )
    print(json.dumps({key: report.get(key) for key in keep}, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
