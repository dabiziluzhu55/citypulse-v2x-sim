"""Paired per-seed comparison layered on the unchanged official metrics."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from algorithms.ippo.evaluate_paired import (
    OFFICIAL_METRIC_NAMES,
    SUMMARY_METRICS,
)
from algorithms.mappo.config import assert_seed_disjoint


MetricValue = float | int | None
SeedMetrics = Mapping[int, Mapping[str, MetricValue]]
EVALUATION_METRICS = tuple(
    dict.fromkeys((*OFFICIAL_METRIC_NAMES, *SUMMARY_METRICS))
)


def _normalize_results(results: SeedMetrics) -> dict[int, dict[str, float | None]]:
    normalized: dict[int, dict[str, float | None]] = {}
    for raw_seed, metrics in results.items():
        seed = int(raw_seed)
        if seed in normalized:
            raise ValueError(f"duplicate normalized seed: {seed}")
        if not isinstance(metrics, Mapping):
            raise TypeError(f"metrics for seed {seed} must be a mapping")
        normalized[seed] = {}
        for raw_name, raw_value in metrics.items():
            name = str(raw_name)
            if not name:
                raise ValueError("metric names must be non-empty")
            if raw_value is None:
                normalized[seed][name] = None
                continue
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(
                    f"metric {name!r} for seed {seed} must be finite or None"
                )
            normalized[seed][name] = value
    return normalized


def _summary(
    deltas: list[float], *, bootstrap_samples: int, rng: np.random.Generator
) -> dict[str, object]:
    if not deltas:
        return {
            "count": 0,
            "mean": None,
            "std": None,
            "median": None,
            "min": None,
            "max": None,
            "bootstrap_ci_95": (None, None),
        }
    values = np.asarray(deltas, dtype=np.float64)
    sample_indices = rng.integers(
        0,
        len(values),
        size=(bootstrap_samples, len(values)),
        endpoint=False,
    )
    bootstrap_means = values[sample_indices].mean(axis=1)
    confidence_interval = np.percentile(bootstrap_means, [2.5, 97.5])
    return {
        "count": int(len(values)),
        "mean": float(values.mean()),
        "std": float(values.std(ddof=0)),
        "median": float(np.median(values)),
        "min": float(values.min()),
        "max": float(values.max()),
        "bootstrap_ci_95": (
            float(confidence_interval[0]),
            float(confidence_interval[1]),
        ),
    }


def aggregate_paired_results(
    baseline: SeedMetrics,
    candidate: SeedMetrics,
    *,
    baseline_name: str,
    candidate_name: str,
    baseline_training_seed_range: tuple[int, int],
    candidate_training_seed_range: tuple[int, int],
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 0,
) -> dict[str, object]:
    """Return candidate-minus-baseline deltas without imputing missing metrics."""

    baseline_label = str(baseline_name)
    candidate_label = str(candidate_name)
    if not baseline_label or not candidate_label:
        raise ValueError("method names must be non-empty")
    if baseline_label == candidate_label:
        raise ValueError("paired method names must differ")
    if int(bootstrap_samples) <= 0:
        raise ValueError("bootstrap_samples must be positive")
    baseline_training_range = tuple(
        int(value) for value in baseline_training_seed_range
    )
    candidate_training_range = tuple(
        int(value) for value in candidate_training_seed_range
    )
    for label, seed_range in (
        (baseline_label, baseline_training_range),
        (candidate_label, candidate_training_range),
    ):
        if len(seed_range) != 2 or seed_range[0] > seed_range[1]:
            raise ValueError(f"invalid {label} training seed range: {seed_range}")
    baseline_values = _normalize_results(baseline)
    candidate_values = _normalize_results(candidate)
    baseline_seeds = set(baseline_values)
    candidate_seeds = set(candidate_values)
    if baseline_seeds != candidate_seeds:
        missing_candidate = sorted(baseline_seeds - candidate_seeds)
        missing_baseline = sorted(candidate_seeds - baseline_seeds)
        raise ValueError(
            "paired seed sets differ: "
            f"missing candidate={missing_candidate}, "
            f"missing baseline={missing_baseline}"
        )
    seeds = tuple(sorted(baseline_seeds))
    assert_seed_disjoint(
        range(baseline_training_range[0], baseline_training_range[1] + 1),
        seeds,
    )
    assert_seed_disjoint(
        range(candidate_training_range[0], candidate_training_range[1] + 1),
        seeds,
    )
    metric_names = sorted(
        {
            metric
            for seed in seeds
            for metric in (
                set(baseline_values[seed]) | set(candidate_values[seed])
            )
        }
    )
    rng = np.random.default_rng(int(bootstrap_seed))
    metrics: dict[str, object] = {}
    for metric_name in metric_names:
        per_seed: dict[int, float | None] = {}
        available: list[float] = []
        for seed in seeds:
            baseline_value = baseline_values[seed].get(metric_name)
            candidate_value = candidate_values[seed].get(metric_name)
            if baseline_value is None or candidate_value is None:
                per_seed[seed] = None
                continue
            delta = float(candidate_value - baseline_value)
            per_seed[seed] = delta
            available.append(delta)
        metrics[metric_name] = {
            "delta_definition": f"{candidate_label} - {baseline_label}",
            "per_seed_delta": per_seed,
            "summary": _summary(
                available,
                bootstrap_samples=int(bootstrap_samples),
                rng=rng,
            ),
        }
    return {
        "seeds": seeds,
        "training_seed_ranges": {
            baseline_label: baseline_training_range,
            candidate_label: candidate_training_range,
        },
        "raw": {
            baseline_label: baseline_values,
            candidate_label: candidate_values,
        },
        "metrics": metrics,
    }


def _evaluation_seed_metrics(
    report: Mapping[str, object], *, report_name: str
) -> dict[int, dict[str, float | None]]:
    config = report.get("config")
    runs = report.get("runs")
    if not isinstance(config, Mapping):
        raise ValueError(f"{report_name} has no evaluation config")
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        raise ValueError(f"{report_name} has no evaluation runs")
    raw_seeds = config.get("seeds")
    if not isinstance(raw_seeds, Sequence) or isinstance(
        raw_seeds, (str, bytes)
    ):
        raise ValueError(f"{report_name} evaluation seeds are missing")
    seeds = tuple(int(seed) for seed in raw_seeds)
    if len(seeds) != len(set(seeds)):
        raise ValueError(f"{report_name} evaluation seeds are not unique")

    by_seed: dict[int, Mapping[str, object]] = {}
    for raw_row in runs:
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{report_name} evaluation run is malformed")
        if "seed" not in raw_row:
            raise ValueError(f"{report_name} evaluation run has no seed")
        seed = int(raw_row["seed"])
        if seed in by_seed:
            raise ValueError(
                f"{report_name} has duplicate evaluation seed {seed}"
            )
        by_seed[seed] = raw_row
    if set(by_seed) != set(seeds):
        raise ValueError(
            f"{report_name} run seeds do not match evaluation config"
        )

    result: dict[int, dict[str, float | None]] = {}
    for seed in seeds:
        row = by_seed[seed]
        metrics = {name: None for name in EVALUATION_METRICS}
        if row.get("status") == "complete":
            official = row.get("official_metrics")
            if not isinstance(official, Mapping):
                official = {}
            for name in OFFICIAL_METRIC_NAMES:
                value = official.get(name)
                metrics[name] = None if value is None else float(value)
            for name in SUMMARY_METRICS:
                value = row.get(name)
                metrics[name] = None if value is None else float(value)
        result[seed] = metrics
    return result


def _training_seed_range(
    report: Mapping[str, object], *, report_name: str
) -> tuple[int, int]:
    metadata = report.get("checkpoint_metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError(f"{report_name} has no checkpoint metadata")
    try:
        return (
            int(metadata["training_seed_start"]),
            int(metadata["training_seed_end"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{report_name} checkpoint training seed range is missing"
        ) from exc


def _validate_paired_training_metadata(
    baseline_report: Mapping[str, object],
    candidate_report: Mapping[str, object],
    *,
    baseline_name: str,
    candidate_name: str,
) -> tuple[
    dict[str, object], dict[str, dict[str, str | None]]
]:
    baseline_raw = baseline_report.get("checkpoint_metadata")
    candidate_raw = candidate_report.get("checkpoint_metadata")
    if not isinstance(baseline_raw, Mapping) or not isinstance(
        candidate_raw, Mapping
    ):
        raise ValueError("paired checkpoint training metadata is missing")
    required = {
        "episode",
        "policy_generation",
        "actor_init_seed",
        "critic_init_seed",
        "training_seed_start",
        "training_seed_end",
        "training_periods",
    }
    for label, metadata in (
        (baseline_name, baseline_raw),
        (candidate_name, candidate_raw),
    ):
        missing = sorted(required - set(metadata))
        if missing:
            raise ValueError(
                f"{label} checkpoint training metadata is missing: "
                + ", ".join(missing)
            )
    baseline_scope = baseline_raw.get("critic_scope")
    candidate_scope = candidate_raw.get("critic_scope")
    baseline_variant = baseline_raw.get("algorithm_variant")
    candidate_variant = candidate_raw.get("algorithm_variant")
    baseline_model_version = baseline_raw.get("model_version")
    candidate_model_version = candidate_raw.get("model_version")
    baseline = dict(baseline_raw)
    candidate = dict(candidate_raw)
    for comparison_axis in (
        "algorithm_variant",
        "critic_scope",
        "model_version",
    ):
        baseline.pop(comparison_axis, None)
        candidate.pop(comparison_axis, None)
    if baseline != candidate:
        differences = sorted(
            key
            for key in set(baseline) | set(candidate)
            if baseline.get(key) != candidate.get(key)
        )
        raise ValueError(
            "paired checkpoint training metadata differs: "
            + ", ".join(differences)
        )
    baseline_variant_name = (
        None if baseline_variant is None else str(baseline_variant)
    )
    candidate_variant_name = (
        None if candidate_variant is None else str(candidate_variant)
    )
    return baseline, {
        "algorithm_variants": {
            str(baseline_name): baseline_variant_name,
            str(candidate_name): candidate_variant_name,
        },
        "critic_scopes": {
            str(baseline_name): (
                None if baseline_scope is None else str(baseline_scope)
            ),
            str(candidate_name): (
                None if candidate_scope is None else str(candidate_scope)
            ),
        },
        "model_versions": {
            str(baseline_name): (
                None
                if baseline_model_version is None
                else str(baseline_model_version)
            ),
            str(candidate_name): (
                None
                if candidate_model_version is None
                else str(candidate_model_version)
            ),
        },
    }


def aggregate_evaluation_reports(
    baseline_report: Mapping[str, object],
    candidate_report: Mapping[str, object],
    *,
    baseline_name: str,
    candidate_name: str,
    bootstrap_samples: int = 10_000,
    bootstrap_seed: int = 0,
) -> dict[str, object]:
    """Aggregate two deterministic reports with failed/missing values as N/A."""

    baseline_config = baseline_report.get("config")
    candidate_config = candidate_report.get("config")
    if not isinstance(baseline_config, Mapping) or not isinstance(
        candidate_config, Mapping
    ):
        raise ValueError("evaluation config is missing")
    if dict(baseline_config) != dict(candidate_config):
        raise ValueError("evaluation config differs between paired reports")

    shared_metadata, comparison_axes = _validate_paired_training_metadata(
        baseline_report,
        candidate_report,
        baseline_name=str(baseline_name),
        candidate_name=str(candidate_name),
    )

    paired = aggregate_paired_results(
        _evaluation_seed_metrics(
            baseline_report, report_name=str(baseline_name)
        ),
        _evaluation_seed_metrics(
            candidate_report, report_name=str(candidate_name)
        ),
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        baseline_training_seed_range=_training_seed_range(
            baseline_report, report_name=str(baseline_name)
        ),
        candidate_training_seed_range=_training_seed_range(
            candidate_report, report_name=str(candidate_name)
        ),
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )
    paired["evaluation_config"] = dict(baseline_config)
    paired["source_reports"] = {
        str(baseline_name): baseline_report.get("checkpoint"),
        str(candidate_name): candidate_report.get("checkpoint"),
    }
    paired["paired_checkpoint_metadata"] = {
        "shared_training_metadata": shared_metadata,
        "shared_except_critic_scope": shared_metadata,
        **comparison_axes,
    }
    return paired


def _read_report(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"evaluation report must be a mapping: {path}")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--candidate-report", type=Path, required=True)
    parser.add_argument("--baseline-name", default="IPPO-compatible local")
    parser.add_argument("--candidate-name", default="MAPPO-v1 global")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--bootstrap-seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.bootstrap_samples <= 0:
        parser.error("bootstrap-samples must be positive")
    for name in ("baseline_report", "candidate_report"):
        path = getattr(args, name).expanduser().resolve()
        if not path.is_file():
            parser.error(f"{name.replace('_', '-')} must be an existing file")
        setattr(args, name, path)
    try:
        report = aggregate_evaluation_reports(
            _read_report(args.baseline_report),
            _read_report(args.candidate_report),
            baseline_name=args.baseline_name,
            candidate_name=args.candidate_name,
            bootstrap_samples=args.bootstrap_samples,
            bootstrap_seed=args.bootstrap_seed,
        )
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    _atomic_write_json(args.output.expanduser().resolve(), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
