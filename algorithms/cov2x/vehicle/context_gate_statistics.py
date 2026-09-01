"""Period-stratified seed-cluster inference for the context-gate screen."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import math
import random
from typing import Any, Mapping, Sequence

from algorithms.cov2x.vehicle.context_gate import (
    ADVICE_ELIGIBLE,
    FIXED_CAP_ARMS,
    NO_FEASIBLE_ADVICE,
    PASS_CURRENT_GREEN,
    QUEUE_AWARE_GLOSA_ARM,
)


CONTEXT_GATE_SUPPORTED = "CONTEXT_GATE_SUPPORTED"
CONTEXT_GATE_UNSUPPORTED = "CONTEXT_GATE_UNSUPPORTED"
INCONCLUSIVE_2 = "INCONCLUSIVE_2"
INVALID_HARD_STOP = "INVALID_HARD_STOP"

DEFAULT_PERIODS = ("morning_peak", "off_peak", "evening_peak")
ELIGIBLE_ADVICE_ARMS = FIXED_CAP_ARMS + (QUEUE_AWARE_GLOSA_ARM,)


@dataclass(frozen=True)
class ContextGateThresholds:
    periods: tuple[str, ...] = DEFAULT_PERIODS
    eligible_per_period: int = 12
    pass_current_green_per_period: int = 6
    no_feasible_per_period: int = 6
    confidence: float = 0.95
    bootstrap_repetitions: int = 20_000
    bootstrap_seed: int = 14899

    def __post_init__(self) -> None:
        periods = tuple(str(value) for value in self.periods)
        if not periods or len(periods) != len(set(periods)):
            raise ValueError("periods must be non-empty and unique")
        for name in (
            "eligible_per_period",
            "pass_current_green_per_period",
            "no_feasible_per_period",
            "bootstrap_repetitions",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 < float(self.confidence) < 1.0:
            raise ValueError("confidence must be strictly between zero and one")
        object.__setattr__(self, "periods", periods)

    def expected_counts(self) -> dict[str, dict[str, int]]:
        return {
            period: {
                ADVICE_ELIGIBLE: int(self.eligible_per_period),
                PASS_CURRENT_GREEN: int(self.pass_current_green_per_period),
                NO_FEASIBLE_ADVICE: int(self.no_feasible_per_period),
            }
            for period in self.periods
        }


@dataclass(frozen=True)
class ClusterBootstrapCI:
    mean: float
    lower: float
    upper: float
    confidence: float
    repetitions: int
    seed: int
    resampling: str = "period_stratified_seed_cluster"
    endpoint: str = "fixed_arm_mean"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextGateResult:
    status: str
    reason: str
    category_counts: Mapping[str, Mapping[str, int]]
    seed_clusters_by_period: Mapping[str, tuple[int, ...]]
    eligible_arm_cis: Mapping[str, ClusterBootstrapCI]
    eligible_glosa_ci: ClusterBootstrapCI | None
    eligible_max_advice_ci: ClusterBootstrapCI | None
    pass_current_green_max_advice_ci: ClusterBootstrapCI | None
    hard_failure_reasons: tuple[str, ...]
    invalid_snapshot_count: int
    thresholds: ContextGateThresholds

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "category_counts": {
                period: dict(values) for period, values in self.category_counts.items()
            },
            "seed_clusters_by_period": {
                period: list(values)
                for period, values in self.seed_clusters_by_period.items()
            },
            "eligible_arm_cis": {
                arm: interval.to_dict()
                for arm, interval in self.eligible_arm_cis.items()
            },
            "eligible_glosa_ci": (
                None if self.eligible_glosa_ci is None else self.eligible_glosa_ci.to_dict()
            ),
            "eligible_max_advice_ci": (
                None
                if self.eligible_max_advice_ci is None
                else self.eligible_max_advice_ci.to_dict()
            ),
            "pass_current_green_max_advice_ci": (
                None
                if self.pass_current_green_max_advice_ci is None
                else self.pass_current_green_max_advice_ci.to_dict()
            ),
            "hard_failure_reasons": list(self.hard_failure_reasons),
            "invalid_snapshot_count": self.invalid_snapshot_count,
            "thresholds": asdict(self.thresholds),
        }


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("cannot compute percentile of an empty sample")
    position = max(0.0, min(1.0, float(probability))) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _metric(row: Mapping[str, Any], arm: str) -> float:
    arms = row.get("arms") or {}
    metrics = arms.get(arm) or {}
    value = float(metrics["movement_local_time_loss_s"])
    if not math.isfinite(value):
        raise ValueError(f"non-finite movement-local time loss for {arm}")
    return value


def _benefit(row: Mapping[str, Any], arm: str) -> float:
    return _metric(row, "NATIVE_RELEASE") - _metric(row, arm)


def _category_counts(
    rows: Sequence[Mapping[str, Any]], periods: Sequence[str]
) -> dict[str, dict[str, int]]:
    categories = (ADVICE_ELIGIBLE, PASS_CURRENT_GREEN, NO_FEASIBLE_ADVICE)
    result = {
        str(period): {category: 0 for category in categories} for period in periods
    }
    for row in rows:
        period = str(row.get("period", ""))
        category = str(row.get("category", ""))
        if period in result and category in result[period]:
            result[period][category] += 1
    return result


def _clustered_benefits(
    rows: Sequence[Mapping[str, Any]],
    *,
    periods: Sequence[str],
    category: str,
    arms: Sequence[str],
) -> dict[str, dict[int, list[dict[str, float]]]]:
    result: dict[str, dict[int, list[dict[str, float]]]] = {
        period: defaultdict(list) for period in periods
    }
    for row in rows:
        if str(row.get("category")) != category:
            continue
        period = str(row.get("period", ""))
        if period not in result:
            continue
        seed = int(row["seed"])
        result[period][seed].append({arm: _benefit(row, arm) for arm in arms})
    return result


def _period_balanced_means(
    clusters: Mapping[str, Mapping[int, Sequence[Mapping[str, float]]]],
    *,
    periods: Sequence[str],
    arms: Sequence[str],
) -> dict[str, float]:
    by_arm: dict[str, list[float]] = {arm: [] for arm in arms}
    for period in periods:
        observations = [
            row
            for seed_rows in clusters[period].values()
            for row in seed_rows
        ]
        if not observations:
            raise ValueError(f"no observations for period {period}")
        for arm in arms:
            by_arm[arm].append(
                sum(float(row[arm]) for row in observations) / len(observations)
            )
    return {
        arm: sum(period_values) / len(period_values)
        for arm, period_values in by_arm.items()
    }


def _bootstrap_joint(
    clusters: Mapping[str, Mapping[int, Sequence[Mapping[str, float]]]],
    *,
    periods: Sequence[str],
    arms: Sequence[str],
    thresholds: ContextGateThresholds,
) -> tuple[dict[str, ClusterBootstrapCI], ClusterBootstrapCI]:
    observed = _period_balanced_means(clusters, periods=periods, arms=arms)
    samples_by_arm: dict[str, list[float]] = {arm: [] for arm in arms}
    maximum_samples: list[float] = []
    rng = random.Random(int(thresholds.bootstrap_seed))
    seed_lists = {period: tuple(sorted(clusters[period])) for period in periods}
    if any(not values for values in seed_lists.values()):
        raise ValueError("every period needs at least one seed cluster")
    for _ in range(int(thresholds.bootstrap_repetitions)):
        period_arm_means: dict[str, list[float]] = {arm: [] for arm in arms}
        for period in periods:
            seeds = seed_lists[period]
            sampled = [rng.choice(seeds) for _ in range(len(seeds))]
            observations = [
                row for seed in sampled for row in clusters[period][seed]
            ]
            for arm in arms:
                period_arm_means[arm].append(
                    sum(float(row[arm]) for row in observations) / len(observations)
                )
        replicate = {
            arm: sum(values) / len(values)
            for arm, values in period_arm_means.items()
        }
        for arm, value in replicate.items():
            samples_by_arm[arm].append(value)
        maximum_samples.append(max(replicate.values()))
    alpha = (1.0 - float(thresholds.confidence)) / 2.0
    intervals = {
        arm: ClusterBootstrapCI(
            mean=observed[arm],
            lower=_percentile(samples_by_arm[arm], alpha),
            upper=_percentile(samples_by_arm[arm], 1.0 - alpha),
            confidence=float(thresholds.confidence),
            repetitions=int(thresholds.bootstrap_repetitions),
            seed=int(thresholds.bootstrap_seed),
            endpoint=f"mean_benefit:{arm}",
        )
        for arm in arms
    }
    maximum = ClusterBootstrapCI(
        mean=max(observed.values()),
        lower=_percentile(maximum_samples, alpha),
        upper=_percentile(maximum_samples, 1.0 - alpha),
        confidence=float(thresholds.confidence),
        repetitions=int(thresholds.bootstrap_repetitions),
        seed=int(thresholds.bootstrap_seed),
        endpoint="max_of_arm_level_mean_benefits",
    )
    return intervals, maximum


def _empty_result(
    *,
    status: str,
    reason: str,
    counts: Mapping[str, Mapping[str, int]],
    clusters: Mapping[str, tuple[int, ...]],
    hard: Sequence[str],
    invalid_count: int,
    thresholds: ContextGateThresholds,
) -> ContextGateResult:
    return ContextGateResult(
        status=status,
        reason=reason,
        category_counts=counts,
        seed_clusters_by_period=clusters,
        eligible_arm_cis={},
        eligible_glosa_ci=None,
        eligible_max_advice_ci=None,
        pass_current_green_max_advice_ci=None,
        hard_failure_reasons=tuple(sorted(set(hard))),
        invalid_snapshot_count=int(invalid_count),
        thresholds=thresholds,
    )


def evaluate_context_gate(
    rows: Sequence[Mapping[str, Any]],
    *,
    thresholds: ContextGateThresholds | None = None,
) -> ContextGateResult:
    """Evaluate the single preregistered context-gate screen."""
    thresholds = thresholds or ContextGateThresholds()
    frozen_rows = [dict(row) for row in rows]
    counts = _category_counts(frozen_rows, thresholds.periods)
    clusters = {
        period: tuple(
            sorted(
                {
                    int(row["seed"])
                    for row in frozen_rows
                    if str(row.get("period")) == period
                }
            )
        )
        for period in thresholds.periods
    }
    hard = sorted(
        {
            str(failure)
            for row in frozen_rows
            for failure in (row.get("hard_validity_failures") or ())
        }
    )
    invalid_count = sum(not bool(row.get("valid", False)) for row in frozen_rows)
    if hard:
        return _empty_result(
            status=INVALID_HARD_STOP,
            reason="hard_gate_failure",
            counts=counts,
            clusters=clusters,
            hard=hard,
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    expected = thresholds.expected_counts()
    if counts != expected:
        return _empty_result(
            status=INCONCLUSIVE_2,
            reason="category_quota_mismatch",
            counts=counts,
            clusters=clusters,
            hard=(),
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    expected_total = sum(
        sum(int(value) for value in period_counts.values())
        for period_counts in expected.values()
    )
    if len(frozen_rows) != expected_total:
        return _empty_result(
            status=INCONCLUSIVE_2,
            reason="snapshot_count_mismatch",
            counts=counts,
            clusters=clusters,
            hard=(),
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    if invalid_count:
        return _empty_result(
            status=INCONCLUSIVE_2,
            reason="invalid_counterfactual_snapshot",
            counts=counts,
            clusters=clusters,
            hard=(),
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    if any(len(values) != 4 for values in clusters.values()):
        return _empty_result(
            status=INCONCLUSIVE_2,
            reason="seed_cluster_coverage_mismatch",
            counts=counts,
            clusters=clusters,
            hard=(),
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    try:
        eligible_clusters = _clustered_benefits(
            frozen_rows,
            periods=thresholds.periods,
            category=ADVICE_ELIGIBLE,
            arms=ELIGIBLE_ADVICE_ARMS,
        )
        eligible_cis, eligible_max = _bootstrap_joint(
            eligible_clusters,
            periods=thresholds.periods,
            arms=ELIGIBLE_ADVICE_ARMS,
            thresholds=thresholds,
        )
        pass_clusters = _clustered_benefits(
            frozen_rows,
            periods=thresholds.periods,
            category=PASS_CURRENT_GREEN,
            arms=FIXED_CAP_ARMS,
        )
        _, pass_max = _bootstrap_joint(
            pass_clusters,
            periods=thresholds.periods,
            arms=FIXED_CAP_ARMS,
            thresholds=thresholds,
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
        return _empty_result(
            status=INVALID_HARD_STOP,
            reason=f"numerical_or_schema_failure:{type(error).__name__}",
            counts=counts,
            clusters=clusters,
            hard=("numerical_or_schema_failure",),
            invalid_count=invalid_count,
            thresholds=thresholds,
        )
    glosa = eligible_cis[QUEUE_AWARE_GLOSA_ARM]
    if glosa.lower > 0.0 and pass_max.upper <= 0.0:
        status = CONTEXT_GATE_SUPPORTED
        reason = "eligible_glosa_positive_and_current_green_release_not_worse"
    elif eligible_max.upper <= 0.0:
        status = CONTEXT_GATE_UNSUPPORTED
        reason = "no_tested_eligible_advice_has_stable_positive_utility"
    else:
        status = INCONCLUSIVE_2
        reason = "preregistered_statistical_gates_not_separated"
    return ContextGateResult(
        status=status,
        reason=reason,
        category_counts=counts,
        seed_clusters_by_period=clusters,
        eligible_arm_cis=eligible_cis,
        eligible_glosa_ci=glosa,
        eligible_max_advice_ci=eligible_max,
        pass_current_green_max_advice_ci=pass_max,
        hard_failure_reasons=(),
        invalid_snapshot_count=0,
        thresholds=thresholds,
    )
