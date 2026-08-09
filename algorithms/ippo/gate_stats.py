# algorithms/ippo/gate_stats.py
"""Pre-registered statistical gate for zero-shot subset evaluation.

Engineering criteria (NOT industry standards), frozen in
algorithms/ippo/gates/preregistration.json before evaluation:
- primary: paired one-sided Wilcoxon signed-rank on controlled waiting time,
  H0: median(Delta_s) >= 0, H1: median(Delta_s) < 0, p < 0.05 AND
  win rate >= ceil(0.7 * N); ties do not count as wins.
- exact p by full sign enumeration when no zero diffs and no tied ranks;
  otherwise pre-registered sign-flip permutation (B=10000, seed 20260804);
  asymptotic approximation is forbidden.
- valid pairs < 8 -> insufficient evidence (never pass).
- strong: p < 0.05, win rate met, relative improvement >= 3%;
  weak: p < 0.05, win rate met, improvement in [0%, 3%); else fail.
- non-inferiority guardrails on paired relative degradation, with <=30% of
  seeds allowed over the bound; queue additionally reports an absolute cap.
- safety: aggregate R = sum(events)/sum(exposures)*k with bootstrap CI;
  availability rules; baseline-stability relative cap.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np


PREREGISTERED_SEEDS: tuple[int, ...] = (
    1042, 1142, 1242, 1342, 1442, 1542, 1642, 1742, 1842, 1942,
)
FORMAL_SEED_COUNT = 10
MIN_VALID_PAIRS = 8
ALPHA = 0.05
WIN_RATE = 0.70
STRONG_IMPROVEMENT = 0.03
PERMUTATION_B = 10_000
PERMUTATION_SEED = 20260804
BOOTSTRAP_B = 2_000
BOOTSTRAP_SEED = 20260804

NI_LOWER_IS_BETTER: dict[str, float] = {
    "avg_travel_time_s": 0.05,
    "avg_queue_length_veh": 0.05,
    "fuel_intensity_L_per_100km": 0.05,
}
NI_THROUGHPUT: float = 0.05
NI_MAX_SEED_VIOLATION_RATIO = 0.30
QUEUE_ABS_CAP_VEH_PER_LANE = 0.5

SAFETY_ABS: dict[str, float] = {
    "severe_conflict_exposure_per_10000": 2.0,
    "emergency_braking_exposure_per_1000": 5.0,
}
SAFETY_REL: float = 0.20
SAFETY_K: dict[str, float] = {
    "severe_conflict_exposure_per_10000": 10000.0,
    "emergency_braking_exposure_per_1000": 1000.0,
}
SAFETY_MIN_EXPOSURES = 5000
SAFETY_MIN_EVENTS = 20


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _rank(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: (values[i], i))
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2.0 + 1.0
        for k in range(index, end + 1):
            ranks[order[k]] = average
        index = end + 1
    return ranks


def _t_plus_observed(diffs: Sequence[float], ranks: Sequence[float]) -> float:
    return sum(rank for rank, diff in zip(ranks, diffs) if diff > 0)


def _wilcoxon_exact_p(diffs: Sequence[float]) -> float:
    """Exact one-sided p (H1: median < 0) over all 2^n sign assignments."""
    ranks = _rank([abs(diff) for diff in diffs])
    observed = _t_plus_observed(diffs, ranks)
    total = 0
    count = 0
    for signs in itertools.product((1.0, -1.0), repeat=len(diffs)):
        t_plus = sum(
            rank * sign for rank, sign in zip(ranks, signs) if sign > 0
        )
        if t_plus <= observed:
            count += 1
        total += 1
    return count / total


def permutation_sign_flip_p(
    diffs: Sequence[float],
    *,
    b: int = PERMUTATION_B,
    seed: int = PERMUTATION_SEED,
) -> float:
    """Pre-registered sign-flip permutation p (zeros dropped)."""
    nonzero = [float(diff) for diff in diffs if diff != 0.0]
    if not nonzero:
        return 1.0
    ranks = _rank([abs(diff) for diff in nonzero])
    observed = _t_plus_observed(nonzero, ranks)
    rng = random.Random(seed)
    count = 1
    for _ in range(b):
        t_plus = 0.0
        for rank, diff in zip(ranks, nonzero):
            flipped = rng.random() < 0.5
            if (diff > 0) != flipped:
                t_plus += rank
        if t_plus <= observed:
            count += 1
    return count / (b + 1)


def wilcoxon_one_sided_less(
    diffs: Sequence[float],
) -> tuple[float, str]:
    """Return (p, method). Exact when no zero diffs and no tied ranks."""
    values = [float(diff) for diff in diffs if diff is not None]
    if any(diff == 0.0 for diff in values):
        return permutation_sign_flip_p(values), "permutation"
    abs_values = sorted(abs(diff) for diff in values)
    if len(set(abs_values)) != len(abs_values):
        return permutation_sign_flip_p(values), "permutation"
    return _wilcoxon_exact_p(values), "exact"


def win_rate(
    model_values: Sequence[float | None],
    fixed_values: Sequence[float | None],
) -> tuple[int, int, int]:
    """Return (wins, ties, losses); ties never count as wins."""
    wins = ties = losses = 0
    for model, fixed in zip(model_values, fixed_values):
        if model is None or fixed is None:
            continue
        if abs(model - fixed) <= 1e-12:
            ties += 1
        elif model < fixed:
            wins += 1
        else:
            losses += 1
    return wins, ties, losses


def aggregate_rate(
    events: Sequence[int], exposures: Sequence[int], *, k: float
) -> float | None:
    total_events = int(sum(events))
    total_exposures = int(sum(exposures))
    if total_exposures <= 0:
        return None
    return total_events / total_exposures * k


def bootstrap_ci(
    events: Sequence[int],
    exposures: Sequence[int],
    *,
    k: float,
    b: int = BOOTSTRAP_B,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI over per-seed (events, exposures) pairs."""
    rng = np.random.default_rng(seed)
    n = len(events)
    ratios = np.empty(b, dtype=np.float64)
    for index in range(b):
        idx = rng.integers(0, n, size=n)
        total_events = float(sum(events[i] for i in idx))
        total_exposures = float(sum(exposures[i] for i in idx))
        ratios[index] = total_events / total_exposures * k if total_exposures > 0 else 0.0
    return float(np.quantile(ratios, alpha / 2.0)), float(
        np.quantile(ratios, 1.0 - alpha / 2.0)
    )


def paired_relative_degradation(
    model_values: Sequence[float | None],
    fixed_values: Sequence[float | None],
    *,
    lower_is_better: bool,
) -> list[float]:
    """Per-seed relative degradation; higher-is-better metrics (throughput)
    use the reciprocal definition from the design review."""
    result: list[float] = []
    for model, fixed in zip(model_values, fixed_values):
        if model is None or fixed is None or fixed == 0.0:
            continue
        if lower_is_better:
            result.append((float(model) - float(fixed)) / abs(float(fixed)))
        else:
            result.append((float(fixed) - float(model)) / abs(float(fixed)))
    return result


@dataclass
class GateVerdict:
    scenario: str
    metric: str
    status: str  # strong | weak | fail | insufficient
    p_value: float | None = None
    method: str | None = None
    wins: int = 0
    ties: int = 0
    losses: int = 0
    valid_pairs: int = 0
    relative_improvement: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "metric": self.metric,
            "status": self.status,
            "p_value": self.p_value,
            "method": self.method,
            "wins": self.wins,
            "ties": self.ties,
            "losses": self.losses,
            "valid_pairs": self.valid_pairs,
            "relative_improvement": self.relative_improvement,
            "details": self.details,
        }


def evaluate_primary_gate(
    scenario: str,
    model_waiting: Sequence[float | None],
    fixed_waiting: Sequence[float | None],
) -> GateVerdict:
    """Primary gate on controlled-area waiting time (lower is better)."""
    diffs = [
        float(model) - float(fixed)
        for model, fixed in zip(model_waiting, fixed_waiting)
        if model is not None and fixed is not None
    ]
    valid = len(diffs)
    if valid < MIN_VALID_PAIRS:
        return GateVerdict(
            scenario=scenario,
            metric="controlled_avg_waiting_time_s",
            status="insufficient",
            valid_pairs=valid,
            details={"reason": "valid pairs below pre-registered minimum"},
        )
    p_value, method = wilcoxon_one_sided_less(diffs)
    wins, ties, losses = win_rate(model_waiting, fixed_waiting)
    required_wins = math.ceil(WIN_RATE * valid)
    fixed_mean = _mean([float(v) for v in fixed_waiting if v is not None])
    model_mean = _mean([float(v) for v in model_waiting if v is not None])
    improvement = (
        (fixed_mean - model_mean) / fixed_mean if fixed_mean and fixed_mean > 0 else None
    )
    if p_value < ALPHA and wins >= required_wins:
        if improvement is not None and improvement >= STRONG_IMPROVEMENT:
            status = "strong"
        else:
            status = "weak"
    else:
        status = "fail"
    return GateVerdict(
        scenario=scenario,
        metric="controlled_avg_waiting_time_s",
        status=status,
        p_value=p_value,
        method=method,
        wins=wins,
        ties=ties,
        losses=losses,
        valid_pairs=valid,
        relative_improvement=improvement,
        details={
            "required_wins": required_wins,
            "win_rate": wins / valid if valid else None,
        },
    )


def evaluate_non_inferiority(
    model_values: Sequence[float | None],
    fixed_values: Sequence[float | None],
    *,
    metric: str,
    lower_is_better: bool,
    bound: float,
    max_violation_ratio: float = NI_MAX_SEED_VIOLATION_RATIO,
    queue_abs_cap: float | None = QUEUE_ABS_CAP_VEH_PER_LANE,
) -> dict[str, Any]:
    degradations = paired_relative_degradation(
        model_values, fixed_values, lower_is_better=lower_is_better
    )
    if not degradations:
        return {"metric": metric, "status": "insufficient", "reason": "no paired values"}
    violations = [value for value in degradations if value > bound]
    abs_deltas: list[float] = []
    if metric == "avg_queue_length_veh" and queue_abs_cap is not None:
        abs_deltas = [
            float(model) - float(fixed)
            for model, fixed in zip(model_values, fixed_values)
            if model is not None and fixed is not None
        ]
        abs_violations = [value for value in abs_deltas if value > queue_abs_cap]
    else:
        abs_violations = []
    worst = max(degradations)
    ok = (
        len(violations) / len(degradations) <= max_violation_ratio
        and (not abs_violations or len(abs_violations) / len(degradations) <= max_violation_ratio)
    )
    return {
        "metric": metric,
        "status": "pass" if ok else "fail",
        "paired_mean": _mean(degradations),
        "paired_median": float(np.median(degradations)) if degradations else None,
        "worst_seed": worst,
        "violation_count": len(violations),
        "violation_ratio": len(violations) / len(degradations),
        "abs_violation_count": len(abs_violations) if abs_deltas else None,
        "bound": bound,
        "max_violation_ratio": max_violation_ratio,
    }


def evaluate_safety(
    model_rows: Sequence[Mapping[str, Any]],
    fixed_rows: Sequence[Mapping[str, Any]],
    *,
    metric: str,
) -> dict[str, Any]:
    """model/fixed_rows: [{events, exposures, availability}] per seed."""
    k = SAFETY_K[metric]
    abs_bound = SAFETY_ABS[metric]

    def _unavailable(rows: Sequence[Mapping[str, Any]]) -> list[str]:
        return [
            f"seed#{index}:{row.get('availability')}"
            for index, row in enumerate(rows)
            if row.get("availability") != "available"
        ]

    unavailable = _unavailable(model_rows) + _unavailable(fixed_rows)
    if unavailable:
        return {
            "metric": metric,
            "status": "incomplete",
            "reason": "safety data unavailable: " + "; ".join(unavailable),
        }
    model_events = [int(row["events"]) for row in model_rows]
    model_exposures = [int(row["exposures"]) for row in model_rows]
    fixed_events = [int(row["events"]) for row in fixed_rows]
    fixed_exposures = [int(row["exposures"]) for row in fixed_rows]
    if sum(model_exposures) < SAFETY_MIN_EXPOSURES or sum(fixed_exposures) < SAFETY_MIN_EXPOSURES:
        return {
            "metric": metric,
            "status": "insufficient",
            "reason": "total controlled passages below 5000",
        }
    model_rate = aggregate_rate(model_events, model_exposures, k=k)
    fixed_rate = aggregate_rate(fixed_events, fixed_exposures, k=k)
    absolute_delta = (model_rate or 0.0) - (fixed_rate or 0.0)
    relative_ok: bool | None = None
    fixed_events_total = sum(fixed_events)
    if (
        fixed_events_total >= SAFETY_MIN_EVENTS
        and sum(fixed_exposures) >= SAFETY_MIN_EXPOSURES
        and fixed_rate
    ):
        relative_ok = (model_rate - fixed_rate) / fixed_rate <= SAFETY_REL
    ok = absolute_delta <= abs_bound and (relative_ok is None or relative_ok)
    ci = bootstrap_ci(model_events, model_exposures, k=k)
    return {
        "metric": metric,
        "status": "pass" if ok else "fail",
        "model_rate": model_rate,
        "fixed_rate": fixed_rate,
        "absolute_delta": absolute_delta,
        "abs_bound": abs_bound,
        "relative_ok": relative_ok,
        "relative_bound": SAFETY_REL if relative_ok is not None else None,
        "model_events_total": sum(model_events),
        "model_exposures_total": sum(model_exposures),
        "fixed_events_total": fixed_events_total,
        "fixed_exposures_total": sum(fixed_exposures),
        "model_bootstrap_ci": ci,
    }
