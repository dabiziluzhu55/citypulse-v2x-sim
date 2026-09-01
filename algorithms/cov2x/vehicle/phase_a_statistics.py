"""Pure Phase-A statistics for the Vehicle action utility test.

The module deliberately contains no simulator or NumPy/SciPy dependency.  A
row is one paired pre-action snapshot.  Its delta is defined as

    release_movement_time_loss - min(negative_cap_movement_time_loss)

so positive values favour a negative-cap counterfactual.  The statistical
bootstrap resamples complete seed/episode clusters independently inside each
period; it never resamples snapshots as independent observations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
import random
import re
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence


NEGATIVE_CAP_ACTIONS: tuple[float, ...] = (-0.25, -0.50, -0.75)
DEFAULT_PHASE_PERIODS: tuple[str, ...] = ("morning_peak", "off_peak", "evening_peak")

PHASE_B = "ENTER_PHASE_B_LOCAL_CREDIT"
PHASE_C = "ENTER_PHASE_C_GLOSA_RESIDUAL"
INCONCLUSIVE = "INCONCLUSIVE"
INVALID_HARD_STOP = "INVALID_HARD_STOP"

_ACTION_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


@dataclass(frozen=True)
class PhaseAThresholds:
    """Pre-registered Phase-A thresholds.

    The approved Phase-A protocol defines useful as a strict positive delta.
    The zero default therefore mirrors the protocol; callers may pass a
    separately preregistered practical threshold explicitly.
    """

    useful_delta_min_s: float = 0.0
    periods: tuple[str, ...] = DEFAULT_PHASE_PERIODS
    expected_snapshot_count: int = 90
    min_useful_total: int = 15
    min_useful_per_period: int = 3
    phase_b_wilson_lower_min: float = 0.05
    phase_b_bootstrap_lower_min_s: float = 0.0
    phase_c_max_useful_total: int = 3
    phase_c_wilson_upper_max: float = 0.10
    confidence: float = 0.95

    def __post_init__(self) -> None:
        threshold = float(self.useful_delta_min_s)
        if not math.isfinite(threshold) or threshold < 0.0:
            raise ValueError("useful_delta_min_s must be finite and non-negative")
        periods = tuple(str(period).strip() for period in self.periods)
        if not periods or any(not period for period in periods):
            raise ValueError("periods must contain non-empty names")
        if len(set(periods)) != len(periods):
            raise ValueError("periods must be unique")
        if int(self.expected_snapshot_count) <= 0:
            raise ValueError("expected_snapshot_count must be positive")
        if int(self.min_useful_total) < 0:
            raise ValueError("min_useful_total must be non-negative")
        if int(self.min_useful_per_period) < 0:
            raise ValueError("min_useful_per_period must be non-negative")
        if int(self.min_useful_total) > int(self.expected_snapshot_count):
            raise ValueError("min_useful_total cannot exceed expected_snapshot_count")
        if int(self.phase_c_max_useful_total) < 0:
            raise ValueError("phase_c_max_useful_total must be non-negative")
        for name, value in (
            ("phase_b_wilson_lower_min", self.phase_b_wilson_lower_min),
            ("phase_c_wilson_upper_max", self.phase_c_wilson_upper_max),
        ):
            value = float(value)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and in [0, 1]")
        bootstrap_floor = float(self.phase_b_bootstrap_lower_min_s)
        if not math.isfinite(bootstrap_floor):
            raise ValueError("phase_b_bootstrap_lower_min_s must be finite")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 < confidence < 1.0:
            raise ValueError("confidence must be strictly between 0 and 1")
        object.__setattr__(self, "useful_delta_min_s", threshold)
        object.__setattr__(self, "periods", periods)
        object.__setattr__(self, "expected_snapshot_count", int(self.expected_snapshot_count))
        object.__setattr__(self, "min_useful_total", int(self.min_useful_total))
        object.__setattr__(self, "min_useful_per_period", int(self.min_useful_per_period))
        object.__setattr__(self, "phase_c_max_useful_total", int(self.phase_c_max_useful_total))
        object.__setattr__(self, "phase_b_wilson_lower_min", float(self.phase_b_wilson_lower_min))
        object.__setattr__(self, "phase_b_bootstrap_lower_min_s", bootstrap_floor)
        object.__setattr__(self, "phase_c_wilson_upper_max", float(self.phase_c_wilson_upper_max))
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "useful_delta_min_s": self.useful_delta_min_s,
            "periods": list(self.periods),
            "expected_snapshot_count": self.expected_snapshot_count,
            "min_useful_total": self.min_useful_total,
            "min_useful_per_period": self.min_useful_per_period,
            "phase_b_wilson_lower_min": self.phase_b_wilson_lower_min,
            "phase_b_bootstrap_lower_min_s": self.phase_b_bootstrap_lower_min_s,
            "phase_c_max_useful_total": self.phase_c_max_useful_total,
            "phase_c_wilson_upper_max": self.phase_c_wilson_upper_max,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class WilsonInterval:
    successes: int
    trials: int
    confidence: float
    rate: float
    lower: float
    upper: float

    def __iter__(self):
        yield self.lower
        yield self.upper

    def to_dict(self) -> dict[str, Any]:
        return {
            "successes": self.successes,
            "trials": self.trials,
            "confidence": self.confidence,
            "rate": self.rate,
            "lower": self.lower,
            "upper": self.upper,
        }


@dataclass(frozen=True)
class ClusterBootstrapCI:
    mean: float
    lower: float
    upper: float
    repetitions: int
    seed: int
    confidence: float
    resampling: str = "period_stratified_episode_seed_cluster"

    def __iter__(self):
        yield self.lower
        yield self.upper

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "lower": self.lower,
            "upper": self.upper,
            "repetitions": self.repetitions,
            "seed": self.seed,
            "confidence": self.confidence,
            "resampling": self.resampling,
        }


@dataclass(frozen=True)
class PhaseAResult:
    status: str
    reason: str
    total_count: int
    valid_count: int
    invalid_snapshot_count: int
    useful_count: int
    period_counts: Mapping[str, int]
    period_useful_counts: Mapping[str, int]
    useful_rate_interval: WilsonInterval | None
    mean_delta_s: float | None
    mean_delta_bootstrap: ClusterBootstrapCI | None
    hard_failure_reasons: tuple[str, ...]
    thresholds: PhaseAThresholds

    @property
    def mean_delta(self) -> float | None:
        return self.mean_delta_s

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "total_snapshots": self.total_count,
            "valid_snapshots": self.valid_count,
            "invalid_snapshot_count": self.invalid_snapshot_count,
            "useful_count": self.useful_count,
            "useful_rate": (
                self.useful_rate_interval.rate
                if self.useful_rate_interval is not None
                else None
            ),
            "period_counts": dict(self.period_counts),
            "period_useful_counts": dict(self.period_useful_counts),
            "useful_rate_interval": (
                self.useful_rate_interval.to_dict()
                if self.useful_rate_interval is not None
                else None
            ),
            "mean_delta_s": self.mean_delta_s,
            "mean_delta_bootstrap": (
                self.mean_delta_bootstrap.to_dict()
                if self.mean_delta_bootstrap is not None
                else None
            ),
            "hard_failure_reasons": list(self.hard_failure_reasons),
            "thresholds": self.thresholds.to_dict(),
        }


@dataclass(frozen=True)
class _Snapshot:
    period: str
    cluster_id: str
    delta: float | None
    valid: bool
    hard_failure_reasons: tuple[str, ...]


def _first_value(row: Mapping[str, Any], names: Sequence[str]) -> tuple[Any, bool]:
    for name in names:
        if name in row:
            return row[name], True
    return None, False


def _flag_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for name in ("pass", "passed", "ok", "valid", "safe", "clean"):
            if name in value:
                return _flag_value(value[name])
        for name in ("failure", "failed", "invalid", "hard_stop", "error"):
            if name in value and _flag_value(value[name]):
                return False
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and float(value) != 0.0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "pass", "passed", "ok", "true", "valid", "safe", "clean", "none", "0"}:
            return True
        if normalized in {"fail", "failed", "false", "invalid", "unsafe", "error", "hard_stop", "1"}:
            return False
    return bool(value)


def _positive_failure(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value)) and float(value) > 0.0
    return not _flag_value(value)


def _hard_failure_reasons(row: Mapping[str, Any]) -> tuple[str, ...]:
    failures: list[str] = []
    definitions = {
        "safety": {
            "pass": ("safety_pass", "safety_ok", "safety_valid", "phase_safety_pass"),
            "status": ("safety", "safety_status", "phase_safety"),
            "failure": (
                "safety_failure",
                "hard_safety_failure",
                "safety_hard_failure",
                "safety_invalid",
                "safety_hard_stop",
                "true_red_entry",
                "true_red_yellow_entry",
                "true_red_entry_count",
                "true_red_yellow_entry_count",
            ),
        },
        "causal": {
            "pass": ("causal_pass", "causal_ok", "causal_valid"),
            "status": ("causal", "causal_status"),
            "failure": (
                "causal_failure",
                "hard_causal_failure",
                "causal_hard_failure",
                "causal_invalid",
                "causal_hard_stop",
                "causal_failure_count",
            ),
        },
        "ledger": {
            "pass": ("ledger_pass", "ledger_ok", "ledger_valid", "ledger_integrity_pass"),
            "status": ("ledger", "ledger_status", "ledger_integrity"),
            "failure": (
                "ledger_failure",
                "hard_ledger_failure",
                "ledger_hard_failure",
                "ledger_invalid",
                "ledger_hard_stop",
                "ledger_failure_count",
            ),
        },
    }
    for category, definition in definitions.items():
        failed = False
        for name in definition["pass"]:
            if name in row and not _flag_value(row[name]):
                failed = True
        for name in definition["status"]:
            if name in row and not _flag_value(row[name]):
                failed = True
        for name in definition["failure"]:
            if name in row and _positive_failure(row[name]):
                failed = True
        if failed:
            failures.append(category)
    return tuple(failures)


def _cluster_id(row: Mapping[str, Any]) -> str:
    value, present = _first_value(row, ("cluster_id", "episode_cluster", "cluster"))
    if present and value is not None and str(value).strip():
        return str(value)
    seed, has_seed = _first_value(row, ("seed", "episode_seed"))
    episode, has_episode = _first_value(row, ("episode_id", "episode", "episode_index"))
    if has_seed and has_episode:
        return f"seed={seed}|episode={episode}"
    if has_seed:
        return f"seed={seed}"
    if has_episode:
        return f"episode={episode}"
    raise ValueError("snapshot row is missing a seed/episode cluster identifier")


def _finite_nonnegative(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _action_from_key(key: Any) -> float | None:
    match = _ACTION_NUMBER.search(str(key))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _cap_values(row: Mapping[str, Any]) -> tuple[float, ...]:
    combined, present = _first_value(
        row,
        (
            "negative_cap_movement_time_loss",
            "negative_cap_movement_time_loss_s",
            "negative_cap_time_loss",
        ),
    )
    values: dict[float, Any] = {}
    if present:
        if isinstance(combined, Mapping):
            for key, value in combined.items():
                action = _action_from_key(key)
                if action is None:
                    continue
                for expected in NEGATIVE_CAP_ACTIONS:
                    if math.isclose(action, expected, rel_tol=0.0, abs_tol=1e-9):
                        values[expected] = value
                        break
        elif isinstance(combined, Sequence) and not isinstance(combined, (str, bytes)):
            if len(combined) != len(NEGATIVE_CAP_ACTIONS):
                raise ValueError(
                    "negative_cap_movement_time_loss sequence must contain exactly three arms"
                )
            values = dict(zip(NEGATIVE_CAP_ACTIONS, combined))
        else:
            raise ValueError(
                "negative_cap_movement_time_loss must be a mapping or three-value sequence"
            )
    if len(values) != len(NEGATIVE_CAP_ACTIONS):
        for action in NEGATIVE_CAP_ACTIONS:
            token = f"{action:.2f}"
            candidates = (
                f"negative_cap_movement_time_loss_{token}",
                f"negative_cap_movement_time_loss_s_{token}",
                f"negative_cap_movement_time_loss_u_{token}",
                f"movement_time_loss_u_{token}",
                f"movement_time_loss_{token}",
                f"u={token}",
            )
            value, found = _first_value(row, candidates)
            if found:
                values[action] = value
    if set(values) != set(NEGATIVE_CAP_ACTIONS):
        missing = sorted(set(NEGATIVE_CAP_ACTIONS) - set(values))
        raise ValueError(f"snapshot row is missing negative-cap arms: {missing}")
    return tuple(_finite_nonnegative(values[action], f"negative cap {action}") for action in NEGATIVE_CAP_ACTIONS)


def _normalise_row(row: Mapping[str, Any]) -> _Snapshot:
    if not isinstance(row, Mapping):
        raise TypeError("snapshot rows must be mappings")
    period_value, present = _first_value(row, ("period",))
    if not present or not str(period_value).strip():
        raise ValueError("snapshot row is missing period")
    period = str(period_value).strip()
    cluster = _cluster_id(row)
    valid_value, has_valid = _first_value(row, ("valid", "snapshot_valid", "is_valid"))
    valid = _flag_value(valid_value) if has_valid else True
    hard = _hard_failure_reasons(row)
    if not valid:
        return _Snapshot(period, cluster, None, False, hard)
    release_value, present = _first_value(
        row,
        (
            "release_movement_time_loss",
            "release_movement_time_loss_s",
            "release_time_loss",
        ),
    )
    if not present:
        raise ValueError("snapshot row is missing release_movement_time_loss")
    release = _finite_nonnegative(release_value, "release_movement_time_loss")
    caps = _cap_values(row)
    return _Snapshot(period, cluster, release - min(caps), True, hard)


def snapshot_delta(row: Mapping[str, Any]) -> float:
    """Return release minus the best of the three negative-cap arms."""

    snapshot = _normalise_row(row)
    if not snapshot.valid or snapshot.delta is None:
        raise ValueError("cannot compute delta for an invalid snapshot")
    return snapshot.delta


def _validate_bootstrap_args(repetitions: int, seed: int) -> tuple[int, int]:
    if isinstance(repetitions, bool) or int(repetitions) != repetitions or int(repetitions) <= 0:
        raise ValueError("repetitions must be a positive integer")
    if isinstance(seed, bool) or int(seed) != seed:
        raise ValueError("seed must be an integer")
    return int(repetitions), int(seed)


def _bootstrap_samples(
    snapshots: Sequence[_Snapshot],
    repetitions: int,
    seed: int,
    periods: Sequence[str] | None = None,
) -> tuple[float, ...]:
    repetitions, seed = _validate_bootstrap_args(repetitions, seed)
    groups: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    first_seen: list[str] = []
    seen_periods: set[str] = set()
    for snapshot in snapshots:
        if snapshot.hard_failure_reasons:
            raise ValueError(
                "hard safety/causal/ledger failure cannot enter bootstrap"
            )
        if not snapshot.valid or snapshot.delta is None:
            continue
        if snapshot.period not in seen_periods:
            first_seen.append(snapshot.period)
            seen_periods.add(snapshot.period)
        groups[snapshot.period][snapshot.cluster_id].append(snapshot.delta)
    if periods is None:
        period_order = tuple(first_seen)
    else:
        period_order = tuple(str(period) for period in periods)
        if len(set(period_order)) != len(period_order):
            raise ValueError("bootstrap periods must be unique")
    if not period_order:
        raise ValueError("bootstrap requires at least one valid period")
    if any(not groups.get(period) for period in period_order):
        missing = [period for period in period_order if not groups.get(period)]
        raise ValueError(f"bootstrap period has no valid clusters: {missing}")
    if periods is not None:
        unknown = [period for period in first_seen if period not in period_order]
        if unknown:
            raise ValueError(f"bootstrap periods omit observed periods: {unknown}")

    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(repetitions):
        values: list[float] = []
        for period in period_order:
            cluster_ids = tuple(sorted(groups[period]))
            for _ in range(len(cluster_ids)):
                selected = rng.choice(cluster_ids)
                values.extend(groups[period][selected])
        if not values:
            raise ValueError("bootstrap produced an empty resample")
        samples.append(float(sum(values) / len(values)))
    return tuple(samples)


def period_stratified_cluster_bootstrap_mean_deltas(
    rows: Iterable[Mapping[str, Any]],
    *,
    repetitions: int,
    seed: int,
    periods: Sequence[str] | None = None,
) -> tuple[float, ...]:
    """Return deterministic period-stratified episode/seed cluster means.

    Each period resamples its observed clusters with replacement, preserving
    the number of clusters in that period.  Every selected cluster contributes
    all of its snapshot deltas.  This is deliberately different from
    snapshot-level bootstrap.
    """

    snapshots = tuple(_normalise_row(row) for row in rows)
    return _bootstrap_samples(snapshots, repetitions, seed, periods)


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot compute a percentile of no values")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def wilson_interval(
    *,
    successes: int,
    trials: int,
    confidence: float = 0.95,
) -> WilsonInterval:
    """Return a two-sided Wilson score interval for a binomial rate."""

    if isinstance(successes, bool) or isinstance(trials, bool):
        raise ValueError("successes and trials must be integers")
    if int(successes) != successes or int(trials) != trials:
        raise ValueError("successes and trials must be integers")
    successes, trials = int(successes), int(trials)
    if trials <= 0:
        raise ValueError("trials must be positive")
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between zero and trials")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")
    alpha = 1.0 - confidence
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    rate = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (rate + z * z / (2.0 * trials)) / denominator
    half_width = (
        z
        * math.sqrt(
            rate * (1.0 - rate) / trials + z * z / (4.0 * trials * trials)
        )
        / denominator
    )
    return WilsonInterval(
        successes=successes,
        trials=trials,
        confidence=confidence,
        rate=rate,
        lower=max(0.0, centre - half_width),
        upper=min(1.0, centre + half_width),
    )


def _bootstrap_ci(
    snapshots: Sequence[_Snapshot],
    *,
    repetitions: int,
    seed: int,
    confidence: float,
) -> ClusterBootstrapCI:
    valid_deltas = [
        snapshot.delta
        for snapshot in snapshots
        if snapshot.valid and snapshot.delta is not None
    ]
    if not valid_deltas:
        raise ValueError("bootstrap requires at least one valid delta")
    samples = _bootstrap_samples(snapshots, repetitions, seed)
    alpha = (1.0 - confidence) / 2.0
    return ClusterBootstrapCI(
        mean=float(sum(valid_deltas) / len(valid_deltas)),
        lower=_percentile(samples, alpha),
        upper=_percentile(samples, 1.0 - alpha),
        repetitions=int(repetitions),
        seed=int(seed),
        confidence=float(confidence),
    )


def evaluate_phase_a(
    rows: Iterable[Mapping[str, Any]],
    thresholds: PhaseAThresholds,
    *,
    bootstrap_reps: int,
    bootstrap_seed: int,
) -> PhaseAResult:
    """Evaluate the pre-registered Phase-A B/C decision gates.

    B/C are evaluated only when exactly thresholds.expected_snapshot_count
    rows are valid and all configured periods are represented.  A row-level
    safety, causal, or ledger failure always returns INVALID_HARD_STOP before
    either statistical gate is considered.
    """

    if not isinstance(thresholds, PhaseAThresholds):
        raise TypeError("thresholds must be a PhaseAThresholds instance")
    bootstrap_reps, bootstrap_seed = _validate_bootstrap_args(
        bootstrap_reps, bootstrap_seed
    )
    snapshots = tuple(_normalise_row(row) for row in rows)
    total_count = len(snapshots)
    hard = tuple(
        category
        for category in ("safety", "causal", "ledger")
        if any(category in snapshot.hard_failure_reasons for snapshot in snapshots)
    )
    valid_snapshots = tuple(
        snapshot
        for snapshot in snapshots
        if snapshot.valid and snapshot.delta is not None
    )
    invalid_count = total_count - len(valid_snapshots)
    period_counts: dict[str, int] = defaultdict(int)
    period_useful_counts: dict[str, int] = defaultdict(int)
    for snapshot in valid_snapshots:
        period_counts[snapshot.period] += 1
        if snapshot.delta > thresholds.useful_delta_min_s:
            period_useful_counts[snapshot.period] += 1
    configured_period_counts = {
        period: period_counts.get(period, 0) for period in thresholds.periods
    }
    configured_period_useful_counts = {
        period: period_useful_counts.get(period, 0) for period in thresholds.periods
    }
    useful_count = sum(configured_period_useful_counts.values())
    rate_interval = (
        wilson_interval(
            successes=useful_count,
            trials=len(valid_snapshots),
            confidence=thresholds.confidence,
        )
        if valid_snapshots and not hard
        else None
    )
    observed_mean = (
        float(sum(snapshot.delta for snapshot in valid_snapshots) / len(valid_snapshots))
        if valid_snapshots
        else None
    )
    bootstrap_ci = (
        _bootstrap_ci(
            valid_snapshots,
            repetitions=bootstrap_reps,
            seed=bootstrap_seed,
            confidence=thresholds.confidence,
        )
        if valid_snapshots and not hard
        else None
    )

    coverage_ok = bool(
        not hard
        and total_count == thresholds.expected_snapshot_count
        and len(valid_snapshots) == thresholds.expected_snapshot_count
        and set(period_counts) == set(thresholds.periods)
        and all(period_counts[period] > 0 for period in thresholds.periods)
    )
    phase_b_ok = bool(
        coverage_ok
        and useful_count >= thresholds.min_useful_total
        and all(
            configured_period_useful_counts[period] >= thresholds.min_useful_per_period
            for period in thresholds.periods
        )
        and rate_interval is not None
        and rate_interval.lower > thresholds.phase_b_wilson_lower_min
        and bootstrap_ci is not None
        and bootstrap_ci.lower > thresholds.phase_b_bootstrap_lower_min_s
    )
    phase_c_ok = bool(
        coverage_ok
        and useful_count <= thresholds.phase_c_max_useful_total
        and rate_interval is not None
        and rate_interval.upper < thresholds.phase_c_wilson_upper_max
    )
    if hard:
        status = INVALID_HARD_STOP
        reason = "hard safety/causal/ledger failure"
    elif phase_b_ok:
        status = PHASE_B
        reason = "Phase-B utility and uncertainty gates passed"
    elif phase_c_ok:
        status = PHASE_C
        reason = "Phase-C low-utility and uncertainty gates passed"
    else:
        status = INCONCLUSIVE
        reason = (
            "incomplete valid snapshot coverage or neither Phase-B nor Phase-C "
            "statistical gate passed"
        )

    return PhaseAResult(
        status=status,
        reason=reason,
        total_count=total_count,
        valid_count=len(valid_snapshots),
        invalid_snapshot_count=invalid_count,
        useful_count=useful_count,
        period_counts={
            **dict(period_counts),
            **{
                period: configured_period_counts[period]
                for period in thresholds.periods
                if period not in period_counts
            },
        },
        period_useful_counts=configured_period_useful_counts,
        useful_rate_interval=rate_interval,
        mean_delta_s=observed_mean,
        mean_delta_bootstrap=bootstrap_ci,
        hard_failure_reasons=hard,
        thresholds=thresholds,
    )
