"""Validate, compare and hard-stop the frozen MAPPO-v2 factorial experiment."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import torch

from algorithms.mappo.checkpoint import state_dict_digest
from algorithms.mappo.experiment_v2 import (
    EPISODE_DURATION_S,
    EPISODES,
    EVALUATION_SEEDS,
    INTERSECTION_COUNT,
    PERIOD,
    RUN_ROOT,
    WORKERS,
    ExperimentJob,
    build_jobs,
)


PRIMARY_METRICS = (
    "throughput_veh_per_h",
    "completed_waiting_mean_s",
    "completion_rate",
    "all_waiting_total_s",
    "all_time_loss_total_s",
)
ATTRIBUTION = {
    "reward_setting_gap": (
        "Yu et al. evaluate MAPPO/IPPO in common-reward cooperative tasks; "
        "CityPulse v5a instead trains each intersection against its local "
        "reward, so global context only predicts local returns and does not "
        "create a shared network-level objective."
    ),
    "shared_actor_cancellation": {
        "aggregate_gradient_cosine": 0.9993,
        "probability_response_cosine": 0.999989,
        "selected_action_sign_disagreements": "1/3200",
    },
}


@dataclass(frozen=True)
class CellEvidence:
    job: ExperimentJob
    diagnostics_path: Path
    evaluation_path: Path
    checkpoint_path: Path
    policy_digest: str
    metrics: Mapping[str, tuple[float, ...]]
    unique_joint_state_counts: tuple[int, ...]
    starved_candidates: frozenset[tuple[str, int]]
    checkpoint_metadata: Mapping[str, object]


def _read_json(path: Path, *, cell_id: str, kind: str) -> Mapping[str, object]:
    if not path.is_file():
        raise ValueError(f"missing cell {cell_id} {kind}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cell {cell_id} invalid {kind}: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"cell {cell_id} {kind} must be a JSON object")
    return value


def _expect(
    condition: bool, *, cell_id: str, message: str
) -> None:
    if not condition:
        raise ValueError(f"cell {cell_id} {message}")


def _finite_metric(value: object, *, cell_id: str, metric: str) -> float:
    if value is None:
        raise ValueError(f"cell {cell_id} primary metric {metric} is N/A")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"cell {cell_id} primary metric {metric} is invalid"
        ) from error
    if not math.isfinite(number):
        raise ValueError(f"cell {cell_id} primary metric {metric} is not finite")
    return number


def _metric_from_run(
    run: Mapping[str, object], metric: str, *, cell_id: str
) -> float:
    if metric == "throughput_veh_per_h":
        official = run.get("official_metrics")
        value = official.get(metric) if isinstance(official, Mapping) else None
    else:
        value = run.get(metric)
    return _finite_metric(value, cell_id=cell_id, metric=metric)


def _validate_checkpoint(
    path: Path, *, cell_id: str, expected_policy_digest: str
) -> None:
    if not path.is_file():
        raise ValueError(f"missing cell {cell_id} checkpoint: {path}")
    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except BaseException as error:
        raise ValueError(f"cell {cell_id} checkpoint cannot be read: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError(f"cell {cell_id} checkpoint payload is invalid")
    policy_state = payload.get("policy_state_dict")
    if not isinstance(policy_state, Mapping):
        raise ValueError(f"cell {cell_id} checkpoint has no policy state")
    try:
        actual_policy_digest = state_dict_digest(policy_state)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"cell {cell_id} checkpoint policy state is invalid: {error}"
        ) from error
    if actual_policy_digest != expected_policy_digest:
        raise ValueError(
            f"cell {cell_id} checkpoint policy digest does not match "
            "training evidence"
        )
    for name in (
        "actor_optimizer_state_dict",
        "critic_optimizer_state_dict",
    ):
        state_dict = payload.get(name)
        state = state_dict.get("state") if isinstance(state_dict, Mapping) else None
        if not isinstance(state, Mapping) or not state:
            raise ValueError(f"cell {cell_id} {name} has no optimizer state")


def _validate_training(job: ExperimentJob) -> tuple[Mapping[str, object], tuple[int, ...]]:
    cell_id = job.cell_id
    payload = _read_json(
        job.diagnostics_path, cell_id=cell_id, kind="training diagnostics"
    )
    _expect(
        payload.get("status") == "complete",
        cell_id=cell_id,
        message="training diagnostics must be complete",
    )
    frozen = payload.get("frozen_config")
    _expect(
        isinstance(frozen, Mapping),
        cell_id=cell_id,
        message="training diagnostics have no frozen config",
    )
    assert isinstance(frozen, Mapping)
    expected_residual_seed = (
        job.lineage.residual_init_seed
        if job.arm.actor_variant == "residual"
        else None
    )
    expected_values = {
        "model_version": job.arm.model_version,
        "actor_variant": job.arm.actor_variant,
        "actor_init_seed": job.lineage.actor_init_seed,
        "critic_init_seed": job.lineage.critic_init_seed,
        "residual_init_seed": expected_residual_seed,
        "training_seed_start": job.lineage.training_seed_start,
        "training_seed_end": job.lineage.training_seed_end,
        "periods": [PERIOD],
        "workers": WORKERS,
        "episode_duration_s": EPISODE_DURATION_S,
    }
    for name, expected in expected_values.items():
        _expect(
            frozen.get(name) == expected,
            cell_id=cell_id,
            message=f"training {name} mismatch (expected {expected!r})",
        )
    config = frozen.get("config")
    _expect(
        isinstance(config, Mapping),
        cell_id=cell_id,
        message="training config is missing",
    )
    assert isinstance(config, Mapping)
    intersections = config.get("intersection_ids")
    _expect(
        isinstance(intersections, Sequence)
        and len(intersections) == INTERSECTION_COUNT,
        cell_id=cell_id,
        message=f"training TLS count must be {INTERSECTION_COUNT}",
    )
    _expect(
        config.get("critic_scope") == job.arm.critic_scope,
        cell_id=cell_id,
        message="training critic context mismatch",
    )
    _expect(
        payload.get("completed_episodes") == EPISODES,
        cell_id=cell_id,
        message=f"training episodes must be {EPISODES}",
    )
    _expect(
        payload.get("final_policy_generation") == EPISODES // WORKERS,
        cell_id=cell_id,
        message="training final policy generation mismatch",
    )
    batches = payload.get("batches")
    _expect(
        isinstance(batches, Sequence) and len(batches) == EPISODES // WORKERS,
        cell_id=cell_id,
        message="training batch count mismatch",
    )
    assert isinstance(batches, Sequence)
    joint_counts: list[int] = []
    for index, raw_batch in enumerate(batches):
        _expect(
            isinstance(raw_batch, Mapping),
            cell_id=cell_id,
            message=f"training batch {index + 1} is invalid",
        )
        assert isinstance(raw_batch, Mapping)
        expected_seed_start = job.lineage.training_seed_start + index * WORKERS
        _expect(
            raw_batch.get("seeds")
            == list(range(expected_seed_start, expected_seed_start + WORKERS)),
            cell_id=cell_id,
            message=f"training batch {index + 1} seed mismatch",
        )
        for name, expected in (
            ("batch_number", index + 1),
            ("sampled_policy_generation", index),
            ("policy_generation", index + 1),
            ("worker_success_count", WORKERS),
            ("worker_expected_count", WORKERS),
            ("finite", True),
        ):
            _expect(
                raw_batch.get(name) == expected,
                cell_id=cell_id,
                message=f"training batch {index + 1} {name} mismatch",
            )
        joint_count = int(raw_batch.get("unique_joint_state_count", 0))
        _expect(
            joint_count > 0,
            cell_id=cell_id,
            message=f"training batch {index + 1} has no joint states",
        )
        joint_counts.append(joint_count)
    final_digest = payload.get("final_policy_digest")
    _expect(
        isinstance(final_digest, str)
        and bool(final_digest)
        and final_digest == batches[-1].get("policy_digest"),
        cell_id=cell_id,
        message="training final policy digest mismatch",
    )
    _expect(
        payload.get("final_checkpoint") == str(job.checkpoint_path),
        cell_id=cell_id,
        message="training final checkpoint path mismatch",
    )
    return payload, tuple(joint_counts)


def _metadata_neutral(metadata: Mapping[str, object]) -> dict[str, object]:
    ignored = {
        "model_version",
        "actor_variant",
        "critic_scope",
        "actor_init_seed",
        "critic_init_seed",
        "residual_init_seed",
        "training_seed_start",
        "training_seed_end",
    }
    return {name: value for name, value in metadata.items() if name not in ignored}


def _validate_evaluation(
    job: ExperimentJob,
) -> tuple[Mapping[str, object], Mapping[str, tuple[float, ...]], frozenset[tuple[str, int]]]:
    cell_id = job.cell_id
    payload = _read_json(
        job.evaluation_path, cell_id=cell_id, kind="evaluation report"
    )
    _expect(
        payload.get("label") == cell_id,
        cell_id=cell_id,
        message="evaluation label mismatch",
    )
    _expect(
        payload.get("checkpoint") == str(job.checkpoint_path),
        cell_id=cell_id,
        message="evaluation checkpoint path mismatch",
    )
    metadata = payload.get("checkpoint_metadata")
    _expect(
        isinstance(metadata, Mapping),
        cell_id=cell_id,
        message="evaluation checkpoint metadata is missing",
    )
    assert isinstance(metadata, Mapping)
    expected_residual_seed = (
        job.lineage.residual_init_seed
        if job.arm.actor_variant == "residual"
        else None
    )
    expected_metadata = {
        "model_version": job.arm.model_version,
        "actor_variant": job.arm.actor_variant,
        "critic_scope": job.arm.critic_scope,
        "episode": EPISODES,
        "policy_generation": EPISODES // WORKERS,
        "actor_init_seed": job.lineage.actor_init_seed,
        "critic_init_seed": job.lineage.critic_init_seed,
        "residual_init_seed": expected_residual_seed,
        "training_seed_start": job.lineage.training_seed_start,
        "training_seed_end": job.lineage.training_seed_end,
        "training_periods": [PERIOD],
        "training_workers": WORKERS,
        "episode_duration_s": float(EPISODE_DURATION_S),
    }
    for name, expected in expected_metadata.items():
        _expect(
            metadata.get(name) == expected,
            cell_id=cell_id,
            message=f"checkpoint metadata {name} mismatch",
        )
    intersections = metadata.get("intersection_ids")
    _expect(
        isinstance(intersections, Sequence)
        and len(intersections) == INTERSECTION_COUNT,
        cell_id=cell_id,
        message=f"checkpoint TLS count must be {INTERSECTION_COUNT}",
    )
    config = payload.get("config")
    _expect(
        isinstance(config, Mapping),
        cell_id=cell_id,
        message="evaluation config is missing",
    )
    assert isinstance(config, Mapping)
    for name, expected in (
        ("seeds", list(EVALUATION_SEEDS)),
        ("duration_s", EPISODE_DURATION_S),
        ("period", PERIOD),
        ("deterministic", True),
        ("action_interval_s", 15.0),
        ("parallel_workers", WORKERS),
    ):
        _expect(
            config.get(name) == expected,
            cell_id=cell_id,
            message=f"evaluation {name} mismatch",
        )
    _expect(
        isinstance(config.get("intersections"), Sequence)
        and len(config["intersections"]) == INTERSECTION_COUNT,
        cell_id=cell_id,
        message=f"evaluation TLS count must be {INTERSECTION_COUNT}",
    )
    summary = payload.get("summary")
    _expect(
        isinstance(summary, Mapping)
        and summary.get("episodes_requested") == len(EVALUATION_SEEDS)
        and summary.get("episodes_complete") == len(EVALUATION_SEEDS)
        and summary.get("episodes_failed") == 0,
        cell_id=cell_id,
        message="evaluation summary is incomplete",
    )
    assert isinstance(summary, Mapping)
    runs = payload.get("runs")
    _expect(
        isinstance(runs, Sequence) and len(runs) == len(EVALUATION_SEEDS),
        cell_id=cell_id,
        message="evaluation run count mismatch",
    )
    assert isinstance(runs, Sequence)
    metrics = {name: [] for name in PRIMARY_METRICS}
    for expected_seed, raw_run in zip(EVALUATION_SEEDS, runs, strict=True):
        _expect(
            isinstance(raw_run, Mapping),
            cell_id=cell_id,
            message="evaluation run is invalid",
        )
        assert isinstance(raw_run, Mapping)
        _expect(
            raw_run.get("seed") == expected_seed
            and raw_run.get("status") == "complete",
            cell_id=cell_id,
            message=f"evaluation run {expected_seed} must be complete",
        )
        for metric in PRIMARY_METRICS:
            metrics[metric].append(
                _metric_from_run(raw_run, metric, cell_id=cell_id)
            )
    action_diagnostics = summary.get("action_diagnostics")
    _expect(
        isinstance(action_diagnostics, Mapping),
        cell_id=cell_id,
        message="evaluation action diagnostics are missing",
    )
    assert isinstance(action_diagnostics, Mapping)
    raw_intersections = action_diagnostics.get("intersections", {})
    _expect(
        isinstance(raw_intersections, Mapping),
        cell_id=cell_id,
        message="evaluation action diagnostics are invalid",
    )
    starved: set[tuple[str, int]] = set()
    for intersection_id, raw_tls in raw_intersections.items():
        if not isinstance(raw_tls, Mapping):
            continue
        candidates = raw_tls.get("candidates", ())
        if not isinstance(candidates, Sequence):
            continue
        for candidate in candidates:
            if isinstance(candidate, Mapping) and candidate.get(
                "never_selected_while_available"
            ):
                starved.add(
                    (str(intersection_id), int(candidate["candidate_index"]))
                )
    return metadata, {name: tuple(values) for name, values in metrics.items()}, frozenset(starved)


def validate_factorial_inputs(run_root: Path) -> tuple[CellEvidence, ...]:
    root = Path(run_root).expanduser().resolve()
    jobs = build_jobs(run_root=root)
    cells: list[CellEvidence] = []
    neutral_metadata: dict[str, object] | None = None
    for job in jobs:
        training, joint_counts = _validate_training(job)
        _validate_checkpoint(
            job.checkpoint_path,
            cell_id=job.cell_id,
            expected_policy_digest=str(training["final_policy_digest"]),
        )
        metadata, metrics, starved = _validate_evaluation(job)
        neutral = _metadata_neutral(metadata)
        if neutral_metadata is None:
            neutral_metadata = neutral
        elif neutral != neutral_metadata:
            raise ValueError(
                f"cell {job.cell_id} checkpoint metadata differs beyond "
                "the registered Actor/context/lineage factors"
            )
        cells.append(
            CellEvidence(
                job=job,
                diagnostics_path=job.diagnostics_path,
                evaluation_path=job.evaluation_path,
                checkpoint_path=job.checkpoint_path,
                policy_digest=str(training["final_policy_digest"]),
                metrics=metrics,
                unique_joint_state_counts=joint_counts,
                starved_candidates=starved,
                checkpoint_metadata=metadata,
            )
        )
    return tuple(cells)


def hierarchical_paired_bootstrap(
    deltas_by_lineage: Mapping[str, Sequence[float]],
    *,
    seed: int,
    samples: int,
) -> dict[str, float]:
    if samples <= 0:
        raise ValueError("bootstrap samples must be positive")
    labels = tuple(deltas_by_lineage)
    if not labels or any(not deltas_by_lineage[label] for label in labels):
        raise ValueError("each bootstrap lineage must have paired deltas")
    arrays = {
        label: np.asarray(deltas_by_lineage[label], dtype=np.float64)
        for label in labels
    }
    if any(not np.isfinite(values).all() for values in arrays.values()):
        raise ValueError("bootstrap deltas must be finite")
    rng = np.random.default_rng(seed)
    draws = np.empty(samples, dtype=np.float64)
    for sample_index in range(samples):
        selected_lineages = rng.integers(0, len(labels), size=len(labels))
        lineage_means = []
        for lineage_index in selected_lineages:
            values = arrays[labels[int(lineage_index)]]
            paired_indices = rng.integers(0, len(values), size=len(values))
            lineage_means.append(float(values[paired_indices].mean()))
        draws[sample_index] = float(np.mean(lineage_means))
    all_values = np.concatenate(tuple(arrays.values()))
    return {
        "mean": float(all_values.mean()),
        "lower": float(np.percentile(draws, 2.5)),
        "upper": float(np.percentile(draws, 97.5)),
    }


def _paired_deltas(
    cells: Mapping[tuple[str, str], CellEvidence],
    *,
    global_arm: str,
    local_arm: str,
) -> dict[str, dict[str, tuple[float, ...]]]:
    result: dict[str, dict[str, tuple[float, ...]]] = {}
    for metric in PRIMARY_METRICS:
        by_lineage: dict[str, tuple[float, ...]] = {}
        for lineage in ("A", "B", "C"):
            global_values = cells[(lineage, global_arm)].metrics[metric]
            local_values = cells[(lineage, local_arm)].metrics[metric]
            by_lineage[lineage] = tuple(
                global_value - local_value
                for global_value, local_value in zip(
                    global_values, local_values, strict=True
                )
            )
        result[metric] = by_lineage
    return result


def _summarize_comparison(
    deltas: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    bootstrap_seed: int,
    bootstrap_samples: int,
) -> dict[str, object]:
    return {
        metric: {
            "per_lineage_mean": {
                lineage: float(np.mean(values))
                for lineage, values in by_lineage.items()
            },
            "per_seed_deltas": {
                lineage: list(map(float, values))
                for lineage, values in by_lineage.items()
            },
            "hierarchical_bootstrap": hierarchical_paired_bootstrap(
                by_lineage,
                seed=bootstrap_seed,
                samples=bootstrap_samples,
            ),
        }
        for metric, by_lineage in deltas.items()
    }


def _interaction_deltas(
    residual: Mapping[str, Mapping[str, Sequence[float]]],
    shared: Mapping[str, Mapping[str, Sequence[float]]],
) -> dict[str, dict[str, tuple[float, ...]]]:
    return {
        metric: {
            lineage: tuple(
                residual_value - shared_value
                for residual_value, shared_value in zip(
                    residual[metric][lineage],
                    shared[metric][lineage],
                    strict=True,
                )
            )
            for lineage in ("A", "B", "C")
        }
        for metric in PRIMARY_METRICS
    }


def _gate(
    residual_summary: Mapping[str, Mapping[str, object]],
    cells: Mapping[tuple[str, str], CellEvidence],
) -> tuple[dict[str, object], list[str]]:
    reasons: list[str] = []

    def lineage_count(metric: str, predicate) -> int:
        means = residual_summary[metric]["per_lineage_mean"]
        assert isinstance(means, Mapping)
        return sum(predicate(float(value)) for value in means.values())

    throughput_ci = residual_summary["throughput_veh_per_h"][
        "hierarchical_bootstrap"
    ]
    waiting_ci = residual_summary["completed_waiting_mean_s"][
        "hierarchical_bootstrap"
    ]
    completion_ci = residual_summary["completion_rate"][
        "hierarchical_bootstrap"
    ]
    all_waiting_ci = residual_summary["all_waiting_total_s"][
        "hierarchical_bootstrap"
    ]
    time_loss_ci = residual_summary["all_time_loss_total_s"][
        "hierarchical_bootstrap"
    ]
    assert all(
        isinstance(value, Mapping)
        for value in (
            throughput_ci,
            waiting_ci,
            completion_ci,
            all_waiting_ci,
            time_loss_ci,
        )
    )
    throughput_means = residual_summary["throughput_veh_per_h"][
        "per_lineage_mean"
    ]
    waiting_means = residual_summary["completed_waiting_mean_s"][
        "per_lineage_mean"
    ]
    assert isinstance(throughput_means, Mapping)
    assert isinstance(waiting_means, Mapping)
    joint_primary_wins = sum(
        float(throughput_means[lineage]) > 0
        and float(waiting_means[lineage]) < 0
        for lineage in ("A", "B", "C")
    )
    if joint_primary_wins < 2:
        reasons.append(
            "fewer than two lineages have both higher throughput and lower "
            "completed-trip waiting for R-G versus R-L"
        )
    if lineage_count("throughput_veh_per_h", lambda value: value > 0) < 2:
        reasons.append("R-G throughput is not higher in at least two lineages")
    if float(throughput_ci["lower"]) <= 0:
        reasons.append("throughput hierarchical CI lower bound is not above zero")
    if lineage_count("completed_waiting_mean_s", lambda value: value < 0) < 2:
        reasons.append(
            "R-G completed-trip waiting is not lower in at least two lineages"
        )
    if float(waiting_ci["upper"]) >= 0:
        reasons.append("waiting hierarchical CI upper bound is not below zero")
    if lineage_count("completion_rate", lambda value: value >= 0) < 2:
        reasons.append("completion rate is lower in too many lineages")
    if float(completion_ci["lower"]) < 0:
        reasons.append("completion-rate hierarchical CI lower bound is below zero")
    for metric, ci, label in (
        ("all_waiting_total_s", all_waiting_ci, "all-traffic waiting"),
        ("all_time_loss_total_s", time_loss_ci, "all-traffic timeLoss"),
    ):
        if lineage_count(metric, lambda value: value <= 0) < 2:
            reasons.append(f"{label} worsens in too many lineages")
        if float(ci["upper"]) > 0:
            reasons.append(f"{label} hierarchical CI upper bound is above zero")
    for lineage in ("A", "B", "C"):
        new_starvation = (
            cells[(lineage, "R-G")].starved_candidates
            - cells[(lineage, "R-L")].starved_candidates
        )
        if new_starvation:
            reasons.append(
                f"R-G introduces action starvation in lineage {lineage}: "
                f"{sorted(new_starvation)}"
            )
    decision = (
        "pass-short-gate" if not reasons else "freeze-to-ippo-v8-ep160"
    )
    return {
        "decision": decision,
        "full_training_authorized": False,
    }, reasons


def aggregate_factorial(
    run_root: Path,
    *,
    bootstrap_seed: int = 20260803,
    bootstrap_samples: int = 10_000,
) -> dict[str, object]:
    evidence = validate_factorial_inputs(run_root)
    cells = {
        (cell.job.lineage.name, cell.job.arm.name): cell for cell in evidence
    }
    residual = _paired_deltas(
        cells, global_arm="R-G", local_arm="R-L"
    )
    shared = _paired_deltas(cells, global_arm="S-G", local_arm="S-L")
    interaction = _interaction_deltas(residual, shared)
    residual_summary = _summarize_comparison(
        residual,
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
    )
    shared_summary = _summarize_comparison(
        shared,
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
    )
    interaction_summary = _summarize_comparison(
        interaction,
        bootstrap_seed=bootstrap_seed,
        bootstrap_samples=bootstrap_samples,
    )
    gate, reasons = _gate(residual_summary, cells)
    actual_joint_counts = {
        cell.job.cell_id: list(cell.unique_joint_state_counts)
        for cell in evidence
    }
    attribution = {
        **ATTRIBUTION,
        "joint_state_diversity": {
            "formula": "8 workers * (300 seconds / 15-second decisions)",
            "expected_ceiling": 160,
            "actual_by_cell": actual_joint_counts,
        },
    }
    return {
        "schema": "mappo_v2_factorial_hard_stop_v1",
        "gate": gate,
        "gate_reasons": reasons,
        "bootstrap": {
            "method": "hierarchical paired: lineages then evaluation seeds",
            "seed": bootstrap_seed,
            "samples": bootstrap_samples,
        },
        "comparisons": {
            "residual_global_minus_local": residual_summary,
            "shared_global_minus_local": shared_summary,
            "factorial_interaction": interaction_summary,
        },
        "cells": {
            cell.job.cell_id: {
                "training_diagnostics": str(cell.diagnostics_path),
                "evaluation_report": str(cell.evaluation_path),
                "checkpoint": str(cell.checkpoint_path),
                "policy_digest": cell.policy_digest,
                "evaluation_seeds": list(EVALUATION_SEEDS),
            }
            for cell in evidence
        },
        "attribution": attribution,
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _markdown(report: Mapping[str, object]) -> str:
    gate = report["gate"]
    attribution = report["attribution"]
    assert isinstance(gate, Mapping) and isinstance(attribution, Mapping)
    cancellation = attribution["shared_actor_cancellation"]
    diversity = attribution["joint_state_diversity"]
    assert isinstance(cancellation, Mapping) and isinstance(diversity, Mapping)
    reasons = report.get("gate_reasons", ())
    reason_lines = (
        "\n".join(f"- {reason}" for reason in reasons)
        if reasons
        else "- All registered short-gate conditions passed."
    )
    return (
        "# MAPPO-v2 factorial hard-stop report\n\n"
        f"Decision: `{gate['decision']}`. Full training authorized: `false`.\n\n"
        "## Gate reasons\n\n"
        f"{reason_lines}\n\n"
        "## Three-layer attribution\n\n"
        f"1. Reward-setting gap: {attribution['reward_setting_gap']}\n"
        "2. Shared-Actor cancellation: gradient cosine "
        f"{cancellation['aggregate_gradient_cosine']}, probability-response "
        f"cosine {cancellation['probability_response_cosine']}, selected-action "
        f"sign disagreements {cancellation['selected_action_sign_disagreements']}.\n"
        "3. Joint-state diversity: expected ceiling "
        f"{diversity['expected_ceiling']}; exact per-cell batch counts are in JSON.\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--bootstrap-seed", type=int, default=20260803)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args(argv)
    if args.bootstrap_samples <= 0:
        parser.error("bootstrap samples must be positive")
    try:
        report = aggregate_factorial(
            args.run_root,
            bootstrap_seed=args.bootstrap_seed,
            bootstrap_samples=args.bootstrap_samples,
        )
    except ValueError as error:
        parser.error(str(error))
    _atomic_write(
        args.output_json.expanduser().resolve(),
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
    )
    _atomic_write(
        args.output_markdown.expanduser().resolve(), _markdown(report)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
