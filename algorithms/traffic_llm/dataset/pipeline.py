"""Decoupled generate / score / select / build-sft pipeline."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

from traffic_control.registry import CONTROL_MODE_REGISTRY, list_control_modes

from .catalog import load_runtime_catalog, neighbor_map, tls_phase_orders
from .episode_runner import run_episode, run_id_for
from .io_utils import dump_json, load_json, read_jsonl, write_jsonl
from .reporting import render_markdown, summarize_dataset
from .scenario_generator import generate_scenarios, plan_summary
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
    )


def generate_dataset(
    config: Mapping[str, Any],
    scoring: Mapping[str, Any],
    *,
    output_dir: Path,
    profile: str | None = None,
    modes: Sequence[str] | None = None,
    limit_scenarios: int | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    catalog = load_runtime_catalog()
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
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = dataset_paths(output_dir)
    write_jsonl(paths["scenarios"], (item.to_dict() for item in scenarios))
    dump_json(
        paths["manifest"],
        {
            "dataset_version": config.get("dataset_version") or DATASET_VERSION,
            "n_scenarios": len(scenarios),
            "control_modes": list(control_modes),
            "profile": profile,
            "config": dict(config),
        },
    )
    jobs = []
    for spec in scenarios:
        for mode in control_modes:
            jobs.append(
                {
                    "spec": spec.to_dict(),
                    "control_mode": mode,
                    "output_dir": str(output_dir),
                    "scoring": dict(scoring),
                    "session_root": str(output_dir / "sessions" / mode),
                    "store_full_vehicles": bool(
                        (config.get("simulation") or {}).get("store_full_vehicle_observation")
                    ),
                }
            )
    results: list[dict[str, Any]] = []
    if workers == 1:
        for job in jobs:
            results.append(_run_payload(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_payload, job) for job in jobs]
            for future in as_completed(futures):
                results.append(future.result())
    select_teachers(output_dir, scoring)
    build_sft(output_dir, config, scoring)
    return {"n_scenarios": len(scenarios), "n_runs": len(results), "output_dir": str(output_dir)}


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
    }


def score_existing(output_dir: Path, scoring: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute event-window/recovery are already stored; re-select with new weights."""

    return select_teachers(output_dir, scoring)


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
    # also conventional names
    write_jsonl(paths["sft"] / "sft_train.jsonl", buckets["train"])
    write_jsonl(paths["sft"] / "sft_val.jsonl", buckets["val"])
    write_jsonl(paths["sft"] / "sft_test.jsonl", buckets["test"])

    summary = summarize_dataset(
        scenarios=[item.to_dict() for item in scenarios.values()],
        runs=list(runs.values()),
        selections=list(selections.values()) + list(ambiguous.values()),
        sft_counts={key: len(value) for key, value in buckets.items()},
        ambiguous=list(ambiguous.values()),
        rejected=list(read_jsonl(paths["teacher"] / "rejected_candidates.jsonl")),
    )
    paths["reports"].mkdir(parents=True, exist_ok=True)
    dump_json(paths["reports"] / "dataset_summary.json", summary)
    (paths["reports"] / "dataset_summary.md").write_text(
        render_markdown(summary), encoding="utf-8"
    )
    return {
        "sft_counts": {key: len(value) for key, value in buckets.items()},
        "skipped_ambiguous": len(skipped_ambiguous),
    }
