"""Decoupled generate / score / select / build-sft pipeline."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_control.registry import CONTROL_MODE_REGISTRY, list_control_modes

from .catalog import load_runtime_catalog, neighbor_map, tls_phase_orders
from .episode_runner import run_episode, run_id_for
from .event_window import (
    compute_local_event_window_metrics,
    resolve_local_intersection_ids,
)
from .io_utils import dump_json, load_json, read_jsonl, write_jsonl
from .reporting import render_markdown, summarize_dataset, wall_and_disk_report
from .scenario_generator import generate_scenarios, plan_summary, repair_unroutable_lane_closures
from .schema import DATASET_VERSION, ScenarioSpec
from .sft_builder import build_sft_samples_for_run, load_trace_file
from .split import assign_splits, split_manifest
from .teacher_selector import select_expert


def dataset_paths(root: Path) -> dict[str, Path]:
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "scenarios": root / "scenarios.jsonl",
        "runs": root / "runs",
        "traces": root / "traces",
        "evaluations": root / "evaluations",
        "teacher": root / "teacher_selection",
        "full_expert": root / "full_expert" / "full_expert.jsonl",
        "sft": root / "sft",
        "reports": root / "reports",
        "split": root / "split_manifest.json",
    }


def resolve_modes(requested: Sequence[str] | None) -> tuple[str, ...]:
    registry = list_control_modes()
    if not requested:
        return tuple(registry)
    modes = []
    for name in requested:
        name = str(name).strip()
        if not name:
            continue
        if name not in CONTROL_MODE_REGISTRY:
            raise ValueError(
                f"Unknown control_mode={name!r}; registry={registry}"
            )
        modes.append(name)
    return tuple(modes)


def plan_job(config: Mapping[str, Any], *, profile: str | None = None, modes: Sequence[str] | None = None) -> dict[str, Any]:
    catalog = load_runtime_catalog()
    scenarios = generate_scenarios(config, catalog, profile=profile)
    control_modes = resolve_modes(modes or config.get("control_modes") or (config.get("smoke") or {}).get("control_modes"))
    if profile == "smoke":
        control_modes = resolve_modes(
            modes or (config.get("smoke") or {}).get("control_modes") or config.get("control_modes")
        )
    return {
        "dataset_version": config.get("dataset_version"),
        **plan_summary(scenarios, control_modes),
        "n_scenarios_planned": len(scenarios),
    }


def _run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    spec = ScenarioSpec.from_dict(payload["spec"])
    scoring = payload["scoring"]
    return run_episode(
        spec,
        payload["control_mode"],
        output_dir=Path(payload["output_dir"]),
        scoring=scoring,
        session_root=Path(payload["session_root"]),
        store_full_vehicles=bool(payload.get("store_full_vehicles")),
        wall_timeout_s=payload.get("wall_timeout_s"),
        retention_mode=str(payload.get("retention_mode") or "audit"),
        anchor_offsets_seconds=payload.get("anchor_offsets_seconds"),
        neighbors=payload.get("neighbors"),
    )


def _existing_run_state(output_dir: Path, run_id: str) -> str | None:
    path = output_dir / "runs" / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        payload = load_json(path)
    except Exception:
        return None
    state = payload.get("state")
    return str(state) if state else None


def _progress(message: str) -> None:
    print(message, flush=True)


def classify_resume_jobs(
    jobs: Sequence[dict[str, Any]],
    output_dir: Path,
    *,
    resume: bool,
    retry_failed: bool,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Skip COMPLETED always when resume=True. FAILED only rerun with retry_failed."""

    to_run: list[dict[str, Any]] = []
    skipped_completed: list[str] = []
    skipped_failed: list[str] = []
    for job in jobs:
        run_id = run_id_for(job["spec"]["scenario_id"], job["control_mode"])
        state = _existing_run_state(output_dir, run_id) if resume else None
        if resume and state == "COMPLETED":
            skipped_completed.append(run_id)
            continue
        if resume and state == "FAILED" and not retry_failed:
            skipped_failed.append(run_id)
            continue
        to_run.append(job)
    return to_run, skipped_completed, skipped_failed


def generate_dataset(
    config: Mapping[str, Any],
    scoring: Mapping[str, Any],
    *,
    output_dir: Path,
    profile: str | None = None,
    modes: Sequence[str] | None = None,
    limit_scenarios: int | None = None,
    workers: int | None = None,
    resume: bool = True,
    retry_failed: bool = False,
    scenario_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    catalog = load_runtime_catalog()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = dataset_paths(output_dir)
    existing_scenarios = paths["scenarios"].is_file()
    if resume and existing_scenarios:
        scenarios = _load_scenarios(output_dir)
        if limit_scenarios is not None:
            scenarios = scenarios[: int(limit_scenarios)]
        failed_ids = {
            str(run.get("scenario_id"))
            for run in _load_runs(output_dir)
            if run.get("state") != "COMPLETED"
        }
        if failed_ids:
            scenarios = repair_unroutable_lane_closures(scenarios, catalog, failed_ids)
    else:
        scenarios = generate_scenarios(
            config, catalog, profile=profile, limit=limit_scenarios
        )
    if profile == "smoke":
        control_modes = resolve_modes(
            modes or (config.get("smoke") or {}).get("control_modes")
        )
        smoke = dict(config.get("smoke") or {})
        workers = int(smoke.get("workers", 1) if workers is None else workers)
    else:
        control_modes = resolve_modes(modes or config.get("control_modes"))
        workers = int((config.get("simulation") or {}).get("workers", 1) if workers is None else workers)
    workers = max(1, int(workers))
    write_jsonl(paths["scenarios"], (item.to_dict() for item in scenarios))
    retention_mode = str((config.get("retention") or {}).get("mode") or "audit")
    anchors = [float(item) for item in (config.get("anchors") or {}).get("offsets_seconds") or (0, 30, 60)]
    neighbors = neighbor_map()
    sim = dict(config.get("simulation") or {})
    dump_json(
        paths["manifest"],
        {
            "dataset_version": config.get("dataset_version") or DATASET_VERSION,
            "n_scenarios": len(scenarios),
            "control_modes": list(control_modes),
            "profile": profile,
            "retention_mode": retention_mode,
            "resume": bool(resume),
            "retry_failed": bool(retry_failed),
            "config": dict(config),
        },
    )
    jobs = []
    factor = float(sim.get("wall_timeout_factor") or 25.0)
    for spec in scenarios:
        timeout = max(180.0, float(spec.duration_seconds) * factor)
        for mode in control_modes:
            jobs.append(
                {
                    "spec": spec.to_dict(),
                    "control_mode": mode,
                    "output_dir": str(output_dir),
                    "scoring": dict(scoring),
                    "session_root": str(output_dir / "sessions" / mode / spec.scenario_id),
                    "store_full_vehicles": bool(sim.get("store_full_vehicle_observation")),
                    "retention_mode": retention_mode,
                    "anchor_offsets_seconds": anchors,
                    "neighbors": {key: list(value) for key, value in neighbors.items()},
                    "wall_timeout_s": timeout,
                }
            )
    if scenario_ids:
        allow = {str(item) for item in scenario_ids}
        jobs = [job for job in jobs if str(job["spec"]["scenario_id"]) in allow]

    to_run, skipped_completed, skipped_failed = classify_resume_jobs(
        jobs,
        output_dir,
        resume=resume,
        retry_failed=retry_failed,
    )

    _progress(
        f"jobs total={len(jobs)} run={len(to_run)} "
        f"skip_completed={len(skipped_completed)} skip_failed={len(skipped_failed)}"
    )
    results: list[dict[str, Any]] = []
    failed_now: list[str] = []
    if workers == 1:
        for idx, job in enumerate(to_run, start=1):
            result = _run_payload(job)
            results.append(result)
            if result.get("state") != "COMPLETED":
                failed_now.append(str(result.get("run_id")))
            _progress(
                f"[{idx}/{len(to_run)}] {result.get('run_id')} "
                f"state={result.get('state')} wall={float(result.get('elapsed_wall_s') or 0):.1f}s"
            )
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_payload, job): job for job in to_run}
            done = 0
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                done += 1
                if result.get("state") != "COMPLETED":
                    failed_now.append(str(result.get("run_id")))
                _progress(
                    f"[{done}/{len(to_run)}] {result.get('run_id')} "
                    f"state={result.get('state')} wall={float(result.get('elapsed_wall_s') or 0):.1f}s"
                )

    select_teachers(output_dir, scoring)
    build_sft(output_dir, config, scoring)
    all_runs = _load_runs(output_dir)
    completed_n = sum(1 for item in all_runs if item.get("state") == "COMPLETED")
    failed_n = sum(1 for item in all_runs if item.get("state") != "COMPLETED")
    remaining = max(0, len(jobs) - completed_n)
    cost = wall_and_disk_report(
        scenarios=[item.to_dict() for item in scenarios],
        runs=all_runs,
        output_dir=output_dir,
        workers=workers,
        target_episodes=len(jobs),
    )
    paths["reports"].mkdir(parents=True, exist_ok=True)
    dump_json(paths["reports"] / "cost_report.json", cost)
    summary = {
        "n_scenarios": len(scenarios),
        "n_jobs": len(jobs),
        "n_runs_this_invocation": len(results),
        "completed": completed_n,
        "skipped_completed": len(skipped_completed),
        "skipped_failed": len(skipped_failed),
        "failed": failed_n,
        "failed_this_invocation": failed_now,
        "remaining": remaining,
        "output_dir": str(output_dir),
        "cost": cost,
    }
    dump_json(paths["reports"] / "generate_progress.json", summary)
    _progress(
        "generate done: "
        f"completed={completed_n} skipped={len(skipped_completed)} "
        f"failed={failed_n} remaining={remaining}"
    )
    return summary


def _load_runs(output_dir: Path) -> list[dict[str, Any]]:
    runs_dir = output_dir / "runs"
    rows = []
    if not runs_dir.is_dir():
        return rows
    for path in sorted(runs_dir.glob("*.json")):
        rows.append(load_json(path))
    return rows


def _load_scenarios(output_dir: Path) -> list[ScenarioSpec]:
    return [ScenarioSpec.from_dict(item) for item in read_jsonl(output_dir / "scenarios.jsonl")]


def hydrate_local_event_windows(output_dir: Path, scoring: Mapping[str, Any]) -> int:
    """Recompute local event-window from stored snapshots without re-running SUMO."""

    neighbors = neighbor_map()
    local_cfg = dict(scoring.get("local_event_windows") or {})
    event_cfg = dict(scoring.get("event_windows") or {})
    updated = 0
    for run in _load_runs(output_dir):
        scenario_payload = run.get("scenario")
        if not scenario_payload:
            continue
        spec = ScenarioSpec.from_dict(scenario_payload)
        snap_path = output_dir / "traces" / f"{run['run_id']}.snapshots.jsonl.gz"
        snapshots = load_trace_file(snap_path) if snap_path.is_file() else []
        if not snapshots:
            continue
        existing_local = dict(run.get("local_event_window") or {})
        if existing_local.get("local_avg_queue_veh") is not None:
            continue
        local_ids = resolve_local_intersection_ids(
            spec.intersection_ids,
            spec.event.intersection_id,
            neighbors,
            hops=int(local_cfg.get("hops", 1)),
            small_preset_max_intersections=int(
                local_cfg.get("small_preset_max_intersections", 6)
            ),
        )
        local_event_window = compute_local_event_window_metrics(
            snapshots,
            event_start=spec.event.start_seconds,
            event_end=spec.event.end_seconds,
            offsets_seconds=tuple(
                local_cfg.get("offsets_seconds")
                or event_cfg.get("offsets_seconds")
                or (30, 60)
            ),
            include_active_span=bool(
                local_cfg.get(
                    "include_active_event_span",
                    event_cfg.get("include_active_event_span", True),
                )
            ),
            intersection_ids=local_ids,
        )
        run["local_event_window"] = local_event_window
        dump_json(output_dir / "runs" / f"{run['run_id']}.json", run)
        eval_path = output_dir / "evaluations" / f"{run['run_id']}.json"
        evaluation = load_json(eval_path) if eval_path.is_file() else {"run_id": run["run_id"]}
        evaluation["local_event_window"] = local_event_window
        evaluation["event_window"] = run.get("event_window") or evaluation.get("event_window") or {}
        evaluation["traffic_eval"] = run.get("traffic_eval") or evaluation.get("traffic_eval") or {}
        evaluation["recovery"] = run.get("recovery") or evaluation.get("recovery") or {}
        dump_json(eval_path, evaluation)
        updated += 1
    return updated


def select_teachers(output_dir: Path, scoring: Mapping[str, Any]) -> dict[str, Any]:
    paths = dataset_paths(output_dir)
    paths["teacher"].mkdir(parents=True, exist_ok=True)
    runs = _load_runs(output_dir)
    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_scenario.setdefault(str(run["scenario_id"]), []).append(run)
    selected = []
    ambiguous = []
    rejected = []
    full_expert = []
    for scenario_id, candidates in sorted(by_scenario.items()):
        selection = select_expert(candidates, scoring)
        record = {"scenario_id": scenario_id, **selection}
        if selection.get("winner") is None:
            rejected.append(record)
        elif selection.get("ambiguous"):
            ambiguous.append(record)
            full_expert.append(record)
        else:
            selected.append(record)
            full_expert.append(record)
        for item in selection.get("rejected") or ():
            rejected.append({"scenario_id": scenario_id, **item})
    write_jsonl(paths["teacher"] / "selected_experts.jsonl", selected)
    write_jsonl(paths["teacher"] / "ambiguous_candidates.jsonl", ambiguous)
    write_jsonl(paths["teacher"] / "rejected_candidates.jsonl", rejected)
    write_jsonl(paths["full_expert"], full_expert)
    return {
        "n_selected": len(selected),
        "n_ambiguous": len(ambiguous),
        "n_rejected": len(rejected),
        "selection_version": scoring.get("selection_version"),
    }


def score_existing(output_dir: Path, scoring: Mapping[str, Any]) -> dict[str, Any]:
    """Hydrate local event metrics from snapshots, then re-select with new weights."""

    hydrated = hydrate_local_event_windows(output_dir, scoring)
    selected = select_teachers(output_dir, scoring)
    return {"hydrated_runs": hydrated, **selected}


def build_sft(
    output_dir: Path,
    dataset_config: Mapping[str, Any],
    scoring: Mapping[str, Any],
) -> dict[str, Any]:
    paths = dataset_paths(output_dir)
    scenarios = {item.scenario_id: item for item in _load_scenarios(output_dir)}
    runs = {item["run_id"]: item for item in _load_runs(output_dir)}
    selections = {
        item["scenario_id"]: item
        for item in read_jsonl(paths["teacher"] / "selected_experts.jsonl")
    }
    ambiguous = {
        item["scenario_id"]: item
        for item in read_jsonl(paths["teacher"] / "ambiguous_candidates.jsonl")
    }
    neighbors = neighbor_map()
    allowed = tls_phase_orders()
    assignment = assign_splits(list(scenarios.values()), dict(dataset_config.get("split") or {}))
    dump_json(paths["split"], split_manifest(list(scenarios.values()), assignment, dict(dataset_config.get("split") or {})))

    buckets = {"train": [], "val": [], "test": []}
    skipped_ambiguous = []
    for scenario_id, spec in scenarios.items():
        selection = selections.get(scenario_id) or ambiguous.get(scenario_id)
        if selection is None:
            continue
        split = assignment.get(spec.scenario_group_id, "train")
        if selection.get("ambiguous"):
            skipped_ambiguous.append(scenario_id)
            continue
        teacher_mode = selection.get("winner")
        if selection.get("fallback_to_baseline"):
            teacher_mode = selection.get("baseline_mode") or "fixed"
        run = runs.get(run_id_for(scenario_id, str(teacher_mode)))
        if run is None:
            continue
        traces = load_trace_file(paths["traces"] / f"{run['run_id']}.jsonl.gz")
        snapshots = load_trace_file(paths["traces"] / f"{run['run_id']}.snapshots.jsonl.gz")
        samples = build_sft_samples_for_run(
            spec=spec,
            run=run,
            selection=selection,
            traces=traces,
            snapshots=snapshots,
            dataset_config=dataset_config,
            neighbors=neighbors,
            allowed_phases=allowed,
        )
        for sample in samples:
            sample["metadata"]["split"] = split
            buckets[split].append(sample)

    paths["sft"].mkdir(parents=True, exist_ok=True)
    write_jsonl(paths["sft"] / "train.jsonl", buckets["train"])
    write_jsonl(paths["sft"] / "val.jsonl", buckets["val"])
    write_jsonl(paths["sft"] / "test.jsonl", buckets["test"])
    write_jsonl(paths["sft"] / "sft_train.jsonl", buckets["train"])
    write_jsonl(paths["sft"] / "sft_val.jsonl", buckets["val"])
    write_jsonl(paths["sft"] / "sft_test.jsonl", buckets["test"])

    run_list = list(runs.values())
    selection_list = list(selections.values()) + list(ambiguous.values())
    summary = summarize_dataset(
        scenarios=[item.to_dict() for item in scenarios.values()],
        runs=run_list,
        selections=selection_list,
        sft_counts={key: len(value) for key, value in buckets.items()},
        ambiguous=list(ambiguous.values()),
        rejected=list(read_jsonl(paths["teacher"] / "rejected_candidates.jsonl")),
    )
    workers = int((dataset_config.get("simulation") or {}).get("workers", 1))
    cost = wall_and_disk_report(
        scenarios=[item.to_dict() for item in scenarios.values()],
        runs=run_list,
        output_dir=output_dir,
        workers=workers,
        target_episodes=max(1, len(run_list)),
    )
    summary["cost"] = cost
    paths["reports"].mkdir(parents=True, exist_ok=True)
    dump_json(paths["reports"] / "dataset_summary.json", summary)
    dump_json(paths["reports"] / "cost_report.json", cost)
    (paths["reports"] / "dataset_summary.md").write_text(
        render_markdown(summary, cost), encoding="utf-8"
    )
    return {
        "sft_counts": {key: len(value) for key, value in buckets.items()},
        "skipped_ambiguous": len(skipped_ambiguous),
        "cost": cost,
    }
