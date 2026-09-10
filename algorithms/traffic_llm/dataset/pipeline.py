"""Decoupled generate / score / select / build-sft pipeline."""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_control.registry import CONTROL_MODE_REGISTRY, list_control_modes

from .catalog import git_commit, load_runtime_catalog, neighbor_map, tls_phase_orders
from .episode_runner import run_episode, run_id_for
from .event_window import (
    compute_local_event_window_metrics,
    resolve_local_intersection_ids,
)
from .io_utils import dump_json, load_json, read_jsonl, write_jsonl
from .phase_service import load_phase_service_index
from .reporting import (
    expert_diagnostics,
    render_markdown,
    summarize_dataset,
    tripinfo_gate_audit,
    wall_and_disk_report,
)
from .scenario_generator import generate_scenarios, plan_summary, repair_unroutable_lane_closures
from .schema import DATASET_VERSION, OBSERVATION_VERSION_V2, ScenarioSpec
from .sft_builder import build_sft_samples_for_run, load_trace_file, messages_to_prompt_completion
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
        "sft_v2": root / "sft_v2",
        "prompt_completion": root / "prompt_completion",
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


def freeze_split(
    output_dir: Path,
    scenarios: Sequence[ScenarioSpec],
    split_cfg: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write split_manifest once before generate. Holdout seeds stay out of train/val."""

    paths = dataset_paths(output_dir)
    if paths["split"].is_file() and not overwrite:
        return load_json(paths["split"])
    assignment = assign_splits(list(scenarios), dict(split_cfg or {}))
    payload = split_manifest(list(scenarios), assignment, dict(split_cfg or {}))
    payload["frozen"] = True
    payload["git_commit"] = git_commit()
    dump_json(paths["split"], payload)
    return payload


def load_frozen_assignment(
    output_dir: Path,
    scenarios: Sequence[ScenarioSpec],
    split_cfg: Mapping[str, Any],
) -> dict[str, str]:
    paths = dataset_paths(output_dir)
    if paths["split"].is_file():
        payload = load_json(paths["split"])
        assignment = dict(payload.get("assignment") or {})
        if assignment:
            return {str(key): str(value) for key, value in assignment.items()}
    frozen = freeze_split(output_dir, scenarios, split_cfg, overwrite=False)
    return {str(key): str(value) for key, value in dict(frozen.get("assignment") or {}).items()}


def _pipeline_flags(config: Mapping[str, Any]) -> dict[str, bool]:
    pipe = dict(config.get("pipeline") or {})
    return {
        "freeze_split_before_generate": bool(pipe.get("freeze_split_before_generate", True)),
        "score_on_generate": bool(pipe.get("score_on_generate", True)),
        "build_sft_on_generate": bool(pipe.get("build_sft_on_generate", True)),
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
    flags = _pipeline_flags(config)
    if flags["freeze_split_before_generate"]:
        freeze_split(output_dir, scenarios, dict(config.get("split") or {}), overwrite=False)
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
            "git_commit": git_commit(),
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
        f"skip_completed={len(skipped_completed)} skip_failed={len(skipped_failed)} "
        f"git={git_commit()}"
    )
    results: list[dict[str, Any]] = []
    failed_now: list[str] = []
    started = time.monotonic()
    paths["reports"].mkdir(parents=True, exist_ok=True)
    failed_log = paths["reports"] / "failed_episodes.jsonl"

    def _emit_progress(done: int, result: Mapping[str, Any]) -> None:
        elapsed = max(1e-6, time.monotonic() - started)
        remaining_now = max(0, len(to_run) - done)
        eta_s = (elapsed / done) * remaining_now if done else None
        all_runs_now = _load_runs(output_dir)
        completed_n = sum(1 for item in all_runs_now if item.get("state") == "COMPLETED")
        failed_n = sum(1 for item in all_runs_now if item.get("state") != "COMPLETED")
        remaining_all = max(0, len(jobs) - completed_n)
        eta_txt = "n/a" if eta_s is None else f"{eta_s / 3600.0:.2f}h"
        _progress(
            f"[{done}/{len(to_run)}] {result.get('run_id')} "
            f"state={result.get('state')} wall={float(result.get('elapsed_wall_s') or 0):.1f}s "
            f"completed={completed_n} failed={failed_n} remaining={remaining_all} eta={eta_txt}"
        )
        if done == 1 or done % 10 == 0 or remaining_now == 0:
            dump_json(
                paths["reports"] / "generate_progress.json",
                {
                    "completed": completed_n,
                    "failed": failed_n,
                    "remaining": remaining_all,
                    "skipped_completed": len(skipped_completed),
                    "skipped_failed": len(skipped_failed),
                    "failed_this_invocation": list(failed_now),
                    "eta_seconds": eta_s,
                    "elapsed_seconds": elapsed,
                    "git_commit": git_commit(),
                },
            )

    if workers == 1:
        for idx, job in enumerate(to_run, start=1):
            try:
                result = _run_payload(job)
            except Exception as exc:
                result = {
                    "run_id": run_id_for(job["spec"]["scenario_id"], job["control_mode"]),
                    "state": "FAILED",
                    "error": str(exc),
                    "elapsed_wall_s": 0.0,
                }
            results.append(result)
            if result.get("state") != "COMPLETED":
                failed_now.append(str(result.get("run_id")))
                with failed_log.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "run_id": result.get("run_id"),
                                "state": result.get("state"),
                                "error": result.get("error"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            _emit_progress(idx, result)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_payload, job): job for job in to_run}
            done = 0
            for future in as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "run_id": run_id_for(job["spec"]["scenario_id"], job["control_mode"]),
                        "state": "FAILED",
                        "error": str(exc),
                        "elapsed_wall_s": 0.0,
                    }
                results.append(result)
                done += 1
                if result.get("state") != "COMPLETED":
                    failed_now.append(str(result.get("run_id")))
                    with failed_log.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "run_id": result.get("run_id"),
                                    "state": result.get("state"),
                                    "error": result.get("error"),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                _emit_progress(done, result)

    if flags["score_on_generate"]:
        select_teachers(output_dir, scoring)
    if flags["build_sft_on_generate"]:
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
    diagnostics = expert_diagnostics(
        scenarios=[item.to_dict() for item in _load_scenarios(output_dir)],
        selections=selected + ambiguous,
        ambiguous=ambiguous,
        rejected=rejected,
        scoring=scoring,
        runs=runs,
    )
    paths["reports"].mkdir(parents=True, exist_ok=True)
    audit = tripinfo_gate_audit(diagnostics)
    diagnostics["tripinfo_gate_audit"] = audit
    dump_json(paths["reports"] / "expert_diagnostics.json", diagnostics)
    dump_json(paths["reports"] / "tripinfo_gate_audit.json", audit)
    if paths["manifest"].is_file():
        manifest = load_json(paths["manifest"])
        manifest["tripinfo_gate_override"] = dict(scoring.get("tripinfo_gate_override") or {})
        manifest["tripinfo_gate_audit"] = audit
        dump_json(paths["manifest"], manifest)
    if diagnostics.get("block_training"):
        _progress(
            "expert diagnostics FLAG block_training="
            f"{diagnostics['flags']}. Do not start formal training until reviewed."
        )
    else:
        acknowledged = (diagnostics.get("tripinfo_gate_override") or {}).get("acknowledged_scopes") or {}
        if acknowledged:
            _progress(
                "tripinfo_gate_override acknowledged "
                f"{sorted(acknowledged)}; block_training=false"
            )
    return {
        "n_selected": len(selected),
        "n_ambiguous": len(ambiguous),
        "n_rejected": len(rejected),
        "selection_version": scoring.get("selection_version"),
        "diagnostics": diagnostics,
        "tripinfo_gate_audit": audit,
        "block_training": bool(diagnostics.get("block_training")),
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
    *,
    observation_version: str = "v1",
    sft_dirname: str | None = None,
    write_prompt_completion: bool | None = None,
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
    use_v2 = str(observation_version).lower() in {"v2", OBSERVATION_VERSION_V2, "traffic_observation_v2"}
    if sft_dirname is None:
        sft_dirname = "sft_v2" if use_v2 else "sft"
    if write_prompt_completion is None:
        write_prompt_completion = use_v2
    assignment = load_frozen_assignment(
        output_dir,
        list(scenarios.values()),
        dict(dataset_config.get("split") or {}),
    )
    phase_index = load_phase_service_index() if use_v2 else None

    buckets: dict[str, list[dict[str, Any]]] = {"train": [], "val": [], "test": []}
    skipped_ambiguous = []
    skipped_signal_vehicle = []
    for scenario_id, spec in scenarios.items():
        selection = selections.get(scenario_id) or ambiguous.get(scenario_id)
        if selection is None:
            continue
        split = assignment.get(spec.scenario_group_id, "train")
        if split not in buckets:
            continue
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
            observation_version="v2" if use_v2 else "v1",
            phase_service=phase_index,
        )
        if not samples and str(run.get("teacher_action_space")) == "signal_vehicle":
            skipped_signal_vehicle.append(scenario_id)
        for sample in samples:
            sample["metadata"]["split"] = split
            sample["metadata"]["sample_source"] = str(
                dataset_config.get("sample_source")
                or spec.dataset_version
                or DATASET_VERSION
            )
            buckets[split].append(sample)

    sft_dir = output_dir / sft_dirname
    sft_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(sft_dir / "train.jsonl", buckets["train"])
    write_jsonl(sft_dir / "val.jsonl", buckets["val"])
    write_jsonl(sft_dir / "test.jsonl", buckets["test"])
    write_jsonl(sft_dir / "sft_train.jsonl", buckets["train"])
    write_jsonl(sft_dir / "sft_val.jsonl", buckets["val"])
    write_jsonl(sft_dir / "sft_test.jsonl", buckets["test"])
    pc_counts = {"train": 0, "val": 0, "test": 0}
    if write_prompt_completion:
        pc_root = output_dir / "prompt_completion"
        if sft_dirname not in {"sft", "sft_v1"}:
            pc_root = output_dir / "prompt_completion_v2"
        pc_root.mkdir(parents=True, exist_ok=True)
        for split, rows in buckets.items():
            converted = [messages_to_prompt_completion(item) for item in rows]
            write_jsonl(pc_root / f"{split}.jsonl", converted)
            pc_counts[split] = len(converted)

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
    summary["observation_version"] = OBSERVATION_VERSION_V2 if use_v2 else "traffic_qwen_observation_v1"
    summary["sft_dirname"] = sft_dirname
    summary["prompt_completion_counts"] = pc_counts
    diag_path = paths["reports"] / "expert_diagnostics.json"
    if diag_path.is_file():
        diagnostics = load_json(diag_path)
        summary["tripinfo_gate_audit"] = diagnostics.get("tripinfo_gate_audit") or tripinfo_gate_audit(
            diagnostics
        )
        if diagnostics.get("block_training"):
            raise RuntimeError(
                "block_training=true; refuse build-sft. "
                f"flags={diagnostics.get('flags')}"
            )
    paths["reports"].mkdir(parents=True, exist_ok=True)
    report_name = "dataset_summary_v2.json" if use_v2 else "dataset_summary.json"
    dump_json(paths["reports"] / report_name, summary)
    if not use_v2:
        dump_json(paths["reports"] / "cost_report.json", cost)
        (paths["reports"] / "dataset_summary.md").write_text(
            render_markdown(summary, cost), encoding="utf-8"
        )
    else:
        dump_json(paths["reports"] / "cost_report_v2.json", cost)
        (paths["reports"] / "dataset_summary_v2.md").write_text(
            render_markdown(summary, cost), encoding="utf-8"
        )
    return {
        "sft_counts": {key: len(value) for key, value in buckets.items()},
        "prompt_completion_counts": pc_counts,
        "skipped_ambiguous": len(skipped_ambiguous),
        "skipped_signal_vehicle": len(skipped_signal_vehicle),
        "observation_version": summary["observation_version"],
        "sft_dirname": sft_dirname,
        "cost": cost,
    }


def _sft_split_rows(dataset_dir: Path, split: str, sft_dirname: str = "sft") -> list[dict[str, Any]]:
    path = dataset_dir / sft_dirname / f"{split}.jsonl"
    return list(read_jsonl(path)) if path.is_file() else []


def _tag_sft_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    sample_source: str,
    split: str,
) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for row in rows:
        sample = dict(row)
        meta = dict(sample.get("metadata") or {})
        meta["sample_source"] = sample_source
        meta["split"] = split
        sample["metadata"] = meta
        tagged.append(sample)
    return tagged


def merge_sft_datasets(
    *,
    v1_dir: Path,
    hard_dir: Path,
    output_dir: Path,
    hard_weight: float = 2.0,
    relabel_dir: Path | None = None,
    v1_sft_dirname: str = "sft",
    hard_sft_dirname: str = "sft",
    analysis_seeds: Sequence[int] = (42003,),
    final_test_seeds: Sequence[int] = (44001,),
) -> dict[str, Any]:
    """V2 train = formal_v1 train + weighted hard_case train + optional relabel.

    42003 stays analysis-only. 44001 is never mixed in. Val is hard_case 43004 only.
    Closed-loop relabel is optional; if missing, count is zero.
    """

    output_dir = Path(output_dir)
    v1_dir = Path(v1_dir)
    hard_dir = Path(hard_dir)
    copies = max(1, int(round(float(hard_weight))))
    analysis_seed_set = {int(item) for item in analysis_seeds}
    final_seed_set = {int(item) for item in final_test_seeds}

    v1_train = _tag_sft_rows(
        _sft_split_rows(v1_dir, "train", v1_sft_dirname),
        sample_source="formal_v1",
        split="train",
    )
    hard_train_unique = _tag_sft_rows(
        _sft_split_rows(hard_dir, "train", hard_sft_dirname),
        sample_source="hard_case_v2",
        split="train",
    )
    hard_train: list[dict[str, Any]] = []
    for copy_idx in range(copies):
        for row in hard_train_unique:
            sample = dict(row)
            meta = dict(sample.get("metadata") or {})
            meta["upsample_copy"] = copy_idx
            sample["metadata"] = meta
            hard_train.append(sample)
    relabel_rows: list[dict[str, Any]] = []
    if relabel_dir is not None and Path(relabel_dir).exists():
        relabel_path = Path(relabel_dir)
        candidate_files = [
            relabel_path / "sft" / "train.jsonl",
            relabel_path / "train.jsonl",
            relabel_path if relabel_path.is_file() else None,
        ]
        for path in candidate_files:
            if path is not None and path.is_file():
                relabel_rows = _tag_sft_rows(
                    list(read_jsonl(path)),
                    sample_source="closed_loop_relabel",
                    split="train",
                )
                break
    train_rows = [*v1_train, *hard_train, *relabel_rows]
    val_rows = _tag_sft_rows(
        _sft_split_rows(hard_dir, "val", hard_sft_dirname),
        sample_source="hard_case_v2",
        split="val",
    )
    analysis_rows = _tag_sft_rows(
        _sft_split_rows(v1_dir, "test", v1_sft_dirname),
        sample_source="formal_v1_analysis_42003",
        split="analysis",
    )

    leaked_seeds = []
    for split, rows in (("train", train_rows), ("val", val_rows)):
        for row in rows:
            seed = ((row.get("metadata") or {}).get("seed"))
            if seed is None:
                user = ""
                for message in row.get("messages") or ():
                    if message.get("role") == "user":
                        user = str(message.get("content") or "")
                        break
                try:
                    payload = json.loads(user)
                    seed = (payload.get("observation") or {}).get("scene", {}).get("seed")
                except Exception:
                    seed = None
            try:
                seed_i = int(seed)
            except (TypeError, ValueError):
                continue
            if seed_i in analysis_seed_set or seed_i in final_seed_set:
                leaked_seeds.append({"split": split, "seed": seed_i})
    if leaked_seeds:
        raise RuntimeError(f"analysis/final-test seeds leaked into V2 train/val: {leaked_seeds[:20]}")

    paths = dataset_paths(output_dir)
    sft_dir = paths["sft"]
    pc_dir = paths["prompt_completion"]
    sft_dir.mkdir(parents=True, exist_ok=True)
    pc_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(sft_dir / "train.jsonl", train_rows)
    write_jsonl(sft_dir / "val.jsonl", val_rows)
    write_jsonl(sft_dir / "test.jsonl", [])
    write_jsonl(sft_dir / "sft_train.jsonl", train_rows)
    write_jsonl(sft_dir / "sft_val.jsonl", val_rows)
    write_jsonl(sft_dir / "sft_test.jsonl", [])
    for split, rows in (("train", train_rows), ("val", val_rows), ("test", [])):
        write_jsonl(pc_dir / f"{split}.jsonl", [messages_to_prompt_completion(item) for item in rows])
    write_jsonl(analysis_dir / "sft_42003.jsonl", analysis_rows)

    v1_split = load_json(v1_dir / "split_manifest.json") if (v1_dir / "split_manifest.json").is_file() else {}
    hard_split = load_json(hard_dir / "split_manifest.json") if (hard_dir / "split_manifest.json").is_file() else {}
    assignment: dict[str, str] = {}
    for group_id, split in dict(v1_split.get("assignment") or {}).items():
        if split == "test":
            assignment[str(group_id)] = "analysis"
        elif split == "train":
            assignment[str(group_id)] = "train"
    for group_id, split in dict(hard_split.get("assignment") or {}).items():
        assignment[str(group_id)] = str(split)
    counts = {"train": 0, "val": 0, "test": 0, "analysis": 0}
    for split in assignment.values():
        counts[split] = counts.get(split, 0) + 1
    split_payload = {
        "rule": "V2 merge: formal_v1 train + hard_case_v2 train (weighted) + optional closed_loop_relabel; val=43004 only; 42003 analysis; 44001 held out",
        "seed": 20260909,
        "ratios": {"train": None, "val": None, "test": 0.0},
        "holdout_seeds": [],
        "val_seeds": [43004],
        "analysis_seeds": sorted(analysis_seed_set),
        "final_test_seeds": sorted(final_seed_set),
        "ood_event_fraction": 0.0,
        "n_scenarios": counts,
        "n_groups": len(assignment),
        "assignment": assignment,
        "frozen": True,
        "git_commit": git_commit(),
    }
    dump_json(paths["split"], split_payload)

    source_counts: dict[str, int] = {}
    for row in [*train_rows, *val_rows]:
        source = str((row.get("metadata") or {}).get("sample_source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    report = {
        "n_v1_train": len(v1_train),
        "n_hard_train_unique": len(hard_train_unique),
        "n_hard_train_weighted": len(hard_train),
        "hard_weight": copies,
        "n_closed_loop_relabel": len(relabel_rows),
        "closed_loop_relabel_implemented": bool(relabel_rows),
        "closed_loop_relabel_deferred_reason": (
            None
            if relabel_rows
            else "simulation kernel has no SUMO saveState/loadState; skipped rather than rewriting the engine"
        ),
        "n_train": len(train_rows),
        "n_val": len(val_rows),
        "n_analysis_42003": len(analysis_rows),
        "n_test": 0,
        "sample_source_counts": source_counts,
        "analysis_seeds": sorted(analysis_seed_set),
        "final_test_seeds": sorted(final_seed_set),
        "note": "Do not inspect 44001 LLM closed-loop until the V2 adapter is frozen.",
    }
    paths["reports"].mkdir(parents=True, exist_ok=True)
    dump_json(paths["reports"] / "v2_merge.json", report)
    dump_json(
        paths["manifest"],
        {
            "dataset_version": "traffic_qwen_sft_formal_v2",
            "n_train": len(train_rows),
            "n_val": len(val_rows),
            "git_commit": git_commit(),
            "sources": {
                "formal_v1": str(v1_dir),
                "hard_case_v2": str(hard_dir),
                "closed_loop_relabel": str(relabel_dir) if relabel_dir else None,
            },
        },
    )
    return report
