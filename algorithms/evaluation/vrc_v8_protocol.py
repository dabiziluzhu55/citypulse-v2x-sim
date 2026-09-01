"""Pure, fail-closed protocol logic for the v8 bounded-residual experiment.

The module consumes explicit rows and artifacts.  It never launches an
experiment and never discovers evidence on disk.  Sealed-seed orchestration
belongs to ``run_vrc_v8.py`` (plan Task 7).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from dataclasses import dataclass
from numbers import Integral, Real
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np


LAMBDA_GRID = (0.0, 0.025, 0.05, 0.10, 0.20, 0.40, 1.0)
DEV_SEEDS = tuple(range(66501, 66509))
S0_VALIDATION_SEEDS = tuple(range(67501, 67509))
S1_TRAIN_SEEDS = tuple(range(2643, 3043))
S1_VALIDATION_SEEDS = tuple(range(68501, 68509))
SEALED_FINAL_SEEDS = tuple(range(77501, 77511))
S1_IMPLEMENTATION_CHECKS = (
    "startup_parity_failure_count",
    "baseline_drift_count",
    "beta_drift_count",
    "allowlist_violation_count",
    "span_violation_count",
    "high_margin_violation_count",
    "nonfinite_count",
    "illegal_action_count",
    "loader_violation_count",
    "recovery_violation_count",
)
BANDS = MappingProxyType(
    {
        "Narrow": (0.05, 0.15),
        "Medium": (0.10, 0.25),
        "Wide": (0.20, 0.40),
    }
)

_MARGIN_QUANTILES = (0.05, 0.10, 0.15, 0.20, 0.25, 0.40)
_POSITIVE_LAMBDAS = LAMBDA_GRID[1:]
_POSITIVE_LAMBDA_LABELS = tuple(str(value) for value in _POSITIVE_LAMBDAS)
_EVALUATION_SEED_ROLES = {
    DEV_SEEDS: "dev",
    S0_VALIDATION_SEEDS: "s0_validation",
    S1_VALIDATION_SEEDS: "s1_validation",
}
_GATE_CHECK_NAMES = (
    "pooled_ratio",
    "paired_wins",
    "worst_seed_degradation",
    "full_nocollab_flip",
    "full_shuffle_action_diff",
    "online_full_vs_shuffle",
    "illegal_actions",
    "nonfinite_values",
    "span_violations",
)
_GATE_RULES = MappingProxyType(
    {
        "pooled_ratio": ("<=", 0.995),
        "paired_wins": (">=", 5),
        "worst_seed_degradation": ("<=", 0.05),
        "full_nocollab_flip": ("inclusive_range", (0.02, 0.15)),
        "full_shuffle_action_diff": (">=", 0.01),
        "online_full_vs_shuffle": ("<=", None),
        "illegal_actions": ("==", 0),
        "nonfinite_values": ("==", 0),
        "span_violations": ("==", 0),
    }
)
_REQUIRED_INPUT_ARTIFACT_ROLES = frozenset(
    {"checkpoint", "code", "probe", "v2x_config"}
)
_SHA256_HEX = frozenset("0123456789abcdef")
_STAGE_STATUSES = MappingProxyType(
    {
        "S0-D": frozenset({"PASS", "FAIL", "INVALID", "HARD_EXTERNAL_BLOCKER"}),
        "S0-W": frozenset({"PASS", "FAIL", "INVALID", "HARD_EXTERNAL_BLOCKER"}),
        "S0-V": frozenset({"PASS", "FAIL", "INVALID", "HARD_EXTERNAL_BLOCKER"}),
        "S1-T": frozenset({"PASS", "S1_FAIL", "INVALID", "HARD_EXTERNAL_BLOCKER"}),
        "S1-V": frozenset(
            {
                "S1_FAIL",
                "S1_VALID",
                "GO_V8L",
                "INVALID",
                "HARD_EXTERNAL_BLOCKER",
            }
        ),
    }
)
_EVIDENCED_STAGE_STATUSES = frozenset(
    {"PASS", "FAIL", "S1_FAIL", "S1_VALID", "GO_V8L"}
)
_AUDITED_STAGE_STATUSES = _EVIDENCED_STAGE_STATUSES | frozenset(
    {"INVALID", "HARD_EXTERNAL_BLOCKER"}
)


def _is_sha256(value: Any) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in _SHA256_HEX for character in value)
    )


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _strict_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer")
    result = int(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _canonical_number(value: Any, name: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    if isinstance(value, Integral):
        return int(value)
    return _finite_number(value, name)


def _jsonable(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, Integral) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, Real) and not isinstance(value, bool):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("canonical JSON does not permit non-finite numbers")
        return result
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("canonical JSON object keys must be strings")
            result[key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise TypeError(f"value of type {type(value).__name__} is not canonical JSON")


def _freeze_json(value: Any) -> Any:
    value = _jsonable(value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _defensive_tuple(value: Any, name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of values")
    try:
        return tuple(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable of values") from exc


def _defensive_pairs(value: Any, name: str) -> tuple[tuple[Any, Any], ...]:
    pairs = []
    for item in _defensive_tuple(value, name):
        pair = _defensive_tuple(item, f"{name} item")
        if len(pair) != 2:
            raise ValueError(f"every {name} item must contain exactly two values")
        pairs.append((pair[0], pair[1]))
    return tuple(pairs)


@dataclass(frozen=True)
class GateCheck:
    name: str
    measured: float | int | None
    comparator: str
    threshold: float | int | tuple[float, float] | None
    passed: bool
    evidence: tuple[tuple[str, float | int | str], ...] = ()
    evaluated: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise ValueError("gate check name must be non-empty")
        if type(self.comparator) is not str or not self.comparator:
            raise ValueError("gate check comparator must be non-empty")
        evidence = []
        for key, value in _defensive_pairs(self.evidence, "evidence"):
            if type(key) is not str or not key:
                raise TypeError("gate check evidence keys must be non-empty strings")
            if type(value) is str:
                canonical_value = value
            elif isinstance(value, str):
                canonical_value = str(value)
            else:
                canonical_value = _canonical_number(
                    value, f"{self.name}.evidence[{key}]"
                )
            evidence.append((key, canonical_value))
        object.__setattr__(self, "evidence", tuple(evidence))
        if type(self.passed) is not bool or type(self.evaluated) is not bool:
            raise TypeError("gate check passed and evaluated fields must be exact booleans")
        evidence_keys = tuple(key for key, _ in self.evidence)
        if len(set(evidence_keys)) != len(evidence_keys):
            raise ValueError("gate check evidence keys must be unique")
        if self.comparator not in {"<=", ">=", "==", "inclusive_range"}:
            raise ValueError("gate check comparator is not supported")
        if self.evaluated:
            if self.measured is None or self.threshold is None:
                raise ValueError("evaluated gate checks require measured and threshold")
            measured = _canonical_number(self.measured, f"{self.name}.measured")
            object.__setattr__(self, "measured", measured)
            if self.comparator == "inclusive_range":
                if not isinstance(self.threshold, tuple) or len(self.threshold) != 2:
                    raise TypeError("inclusive_range requires a two-value tuple threshold")
                lower = _canonical_number(
                    self.threshold[0], f"{self.name}.threshold"
                )
                upper = _canonical_number(
                    self.threshold[1], f"{self.name}.threshold"
                )
                if lower > upper:
                    raise ValueError("inclusive_range lower threshold exceeds upper threshold")
                object.__setattr__(self, "threshold", (lower, upper))
                expected_pass = lower <= measured <= upper
            else:
                if isinstance(self.threshold, tuple):
                    raise TypeError("scalar comparators require a scalar threshold")
                threshold = _canonical_number(
                    self.threshold, f"{self.name}.threshold"
                )
                object.__setattr__(self, "threshold", threshold)
                expected_pass = {
                    "<=": measured <= threshold,
                    ">=": measured >= threshold,
                    "==": measured == threshold,
                }[self.comparator]
            if self.passed is not expected_pass:
                raise ValueError("gate check passed disagrees with measured comparison")
            if self.reason is not None:
                raise ValueError("evaluated gate checks cannot carry a skip reason")
        else:
            if self.measured is not None or self.threshold is not None:
                raise ValueError("unevaluated gate checks cannot carry measured values")
            if self.passed:
                raise ValueError("an unevaluated gate check cannot pass")
            if type(self.reason) is not str or not self.reason:
                raise ValueError("an unevaluated gate check requires a reason")


@dataclass(frozen=True)
class SeedMetric:
    seed: int
    full_m: float
    nocollab_m: float
    shuffle_online_m: float | None
    full_win: bool
    degradation: float
    full_waiting_total_s: float
    full_departed_count: float
    nocollab_waiting_total_s: float
    nocollab_departed_count: float
    shuffle_waiting_total_s: float | None
    shuffle_departed_count: float | None

    def __post_init__(self) -> None:
        if type(self.full_win) is not bool:
            raise TypeError("full_win must be an exact boolean")
        object.__setattr__(self, "seed", _strict_int(self.seed, "seed"))
        full_m = _finite_number(self.full_m, "full_m")
        nocollab_m = _finite_number(self.nocollab_m, "nocollab_m")
        degradation = _finite_number(self.degradation, "degradation")
        full_waiting = _finite_number(
            self.full_waiting_total_s, "full_waiting_total_s"
        )
        full_departed = _finite_number(
            self.full_departed_count, "full_departed_count"
        )
        nocollab_waiting = _finite_number(
            self.nocollab_waiting_total_s, "nocollab_waiting_total_s"
        )
        nocollab_departed = _finite_number(
            self.nocollab_departed_count, "nocollab_departed_count"
        )
        object.__setattr__(self, "full_m", full_m)
        object.__setattr__(self, "nocollab_m", nocollab_m)
        object.__setattr__(self, "degradation", degradation)
        object.__setattr__(self, "full_waiting_total_s", full_waiting)
        object.__setattr__(self, "full_departed_count", full_departed)
        object.__setattr__(self, "nocollab_waiting_total_s", nocollab_waiting)
        object.__setattr__(self, "nocollab_departed_count", nocollab_departed)
        if full_m < 0.0 or nocollab_m <= 0.0:
            raise ValueError("per-seed M requires Full >= 0 and NoCollab > 0")
        if (
            full_waiting < 0.0
            or nocollab_waiting < 0.0
            or full_departed <= 0.0
            or nocollab_departed <= 0.0
        ):
            raise ValueError("per-seed pooled inputs are outside their domain")
        if (
            full_m != full_waiting / full_departed
            or nocollab_m != nocollab_waiting / nocollab_departed
        ):
            raise ValueError("per-seed M disagrees with waiting/departed inputs")
        if self.full_win is not (full_m < nocollab_m):
            raise ValueError("full_win disagrees with per-seed M values")
        expected_degradation = (full_m - nocollab_m) / nocollab_m
        if degradation != expected_degradation:
            raise ValueError("degradation disagrees with per-seed M values")
        shuffle_values = (
            self.shuffle_online_m,
            self.shuffle_waiting_total_s,
            self.shuffle_departed_count,
        )
        if any(value is None for value in shuffle_values):
            if not all(value is None for value in shuffle_values):
                raise ValueError(
                    "per-seed Shuffle evidence must be all present or all absent"
                )
        else:
            shuffle_m = _finite_number(self.shuffle_online_m, "shuffle_online_m")
            shuffle_waiting = _finite_number(
                self.shuffle_waiting_total_s, "shuffle_waiting_total_s"
            )
            shuffle_departed = _finite_number(
                self.shuffle_departed_count, "shuffle_departed_count"
            )
            object.__setattr__(self, "shuffle_online_m", shuffle_m)
            object.__setattr__(self, "shuffle_waiting_total_s", shuffle_waiting)
            object.__setattr__(self, "shuffle_departed_count", shuffle_departed)
            if (
                shuffle_m < 0.0
                or shuffle_waiting < 0.0
                or shuffle_departed <= 0.0
            ):
                raise ValueError("per-seed Shuffle inputs are outside their domain")
            if shuffle_m != shuffle_waiting / shuffle_departed:
                raise ValueError("per-seed Shuffle M disagrees with its inputs")


@dataclass(frozen=True)
class GateVerdict:
    status: str
    passed: bool
    seed_role: str | None
    checks: tuple[GateCheck, ...] = ()
    per_seed: tuple[SeedMetric, ...] = ()
    reasons: tuple[str, ...] = ()
    full_pooled_m: float | None = None
    nocollab_pooled_m: float | None = None
    shuffle_pooled_m: float | None = None
    full_waiting_sum: float | None = None
    full_departed_sum: float | None = None
    nocollab_waiting_sum: float | None = None
    nocollab_departed_sum: float | None = None
    shuffle_waiting_sum: float | None = None
    shuffle_departed_sum: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", _defensive_tuple(self.checks, "checks"))
        object.__setattr__(self, "per_seed", _defensive_tuple(self.per_seed, "per_seed"))
        object.__setattr__(self, "reasons", _defensive_tuple(self.reasons, "reasons"))
        if type(self.status) is not str:
            raise TypeError("gate verdict status must be an exact string")
        if self.seed_role is not None and type(self.seed_role) is not str:
            raise TypeError("gate verdict seed_role must be an exact string or None")
        if type(self.passed) is not bool:
            raise TypeError("gate verdict passed must be an exact boolean")
        if any(type(check) is not GateCheck for check in self.checks):
            raise TypeError("gate verdict checks must be GateCheck records")
        if any(type(metric) is not SeedMetric for metric in self.per_seed):
            raise TypeError("gate verdict per_seed values must be SeedMetric records")
        if any(type(reason) is not str or not reason for reason in self.reasons):
            raise TypeError("gate verdict reasons must be non-empty strings")
        if self.status not in {"PASS", "FAIL", "INVALID"}:
            raise ValueError("gate verdict status must be PASS, FAIL, or INVALID")
        if self.status == "INVALID":
            if self.passed or not self.reasons:
                raise ValueError("INVALID verdicts must be non-passing with reasons")
            return
        if self.reasons:
            raise ValueError("complete PASS/FAIL verdicts cannot carry invalid reasons")
        if self.seed_role not in _EVALUATION_SEED_ROLES.values():
            raise ValueError("complete verdicts require a frozen evaluation seed role")
        if tuple(check.name for check in self.checks) != _GATE_CHECK_NAMES:
            raise ValueError("complete verdicts require the exact nine S0 checks")
        checks_by_name = {check.name: check for check in self.checks}
        for name, check in checks_by_name.items():
            expected_comparator, expected_threshold = _GATE_RULES[name]
            if check.comparator != expected_comparator:
                raise ValueError(f"{name} does not use its frozen comparator")
            if expected_threshold is not None and check.threshold != expected_threshold:
                raise ValueError(f"{name} does not use its frozen threshold")
        for name in (
            "paired_wins",
            "illegal_actions",
            "nonfinite_values",
            "span_violations",
        ):
            _strict_int(checks_by_name[name].measured, f"{name}.measured", minimum=0)
        for name in ("full_nocollab_flip", "full_shuffle_action_diff"):
            measured = _finite_number(checks_by_name[name].measured, f"{name}.measured")
            if not 0.0 <= measured <= 1.0:
                raise ValueError(f"{name} must be a unit-interval rate")
        computed_pass = all(check.evaluated and check.passed for check in self.checks)
        if self.passed != computed_pass:
            raise ValueError("GateVerdict.passed disagrees with its checks")
        if self.status != ("PASS" if computed_pass else "FAIL"):
            raise ValueError("GateVerdict.status disagrees with its checks")
        role_seeds = next(
            seeds
            for seeds, role in _EVALUATION_SEED_ROLES.items()
            if role == self.seed_role
        )
        if tuple(metric.seed for metric in self.per_seed) != role_seeds:
            raise ValueError("per-seed evidence does not match GateVerdict.seed_role")
        required_numbers = (
            self.full_pooled_m,
            self.nocollab_pooled_m,
            self.full_waiting_sum,
            self.full_departed_sum,
            self.nocollab_waiting_sum,
            self.nocollab_departed_sum,
        )
        if any(value is None for value in required_numbers):
            raise ValueError("complete verdicts require Full and NoCollab pooled inputs")
        full_m = _finite_number(self.full_pooled_m, "full_pooled_m")
        nocollab_m = _finite_number(self.nocollab_pooled_m, "nocollab_pooled_m")
        full_waiting = _finite_number(self.full_waiting_sum, "full_waiting_sum")
        full_departed = _finite_number(self.full_departed_sum, "full_departed_sum")
        nocollab_waiting = _finite_number(
            self.nocollab_waiting_sum, "nocollab_waiting_sum"
        )
        nocollab_departed = _finite_number(
            self.nocollab_departed_sum, "nocollab_departed_sum"
        )
        object.__setattr__(self, "full_pooled_m", full_m)
        object.__setattr__(self, "nocollab_pooled_m", nocollab_m)
        object.__setattr__(self, "full_waiting_sum", full_waiting)
        object.__setattr__(self, "full_departed_sum", full_departed)
        object.__setattr__(self, "nocollab_waiting_sum", nocollab_waiting)
        object.__setattr__(self, "nocollab_departed_sum", nocollab_departed)
        if (
            full_waiting < 0.0
            or nocollab_waiting < 0.0
            or full_departed <= 0.0
            or nocollab_departed <= 0.0
            or nocollab_m <= 0.0
        ):
            raise ValueError("pooled waiting/departed evidence is outside its domain")
        per_seed_sums = (
            math.fsum(metric.full_waiting_total_s for metric in self.per_seed),
            math.fsum(metric.full_departed_count for metric in self.per_seed),
            math.fsum(metric.nocollab_waiting_total_s for metric in self.per_seed),
            math.fsum(metric.nocollab_departed_count for metric in self.per_seed),
        )
        pooled_sums = (
            full_waiting,
            full_departed,
            nocollab_waiting,
            nocollab_departed,
        )
        if pooled_sums != per_seed_sums:
            raise ValueError("pooled evidence disagrees with per-seed rows")
        if (
            full_m != full_waiting / full_departed
            or nocollab_m != nocollab_waiting / nocollab_departed
        ):
            raise ValueError("pooled M disagrees with waiting/departed sums")
        pooled_ratio = full_m / nocollab_m
        if (
            _finite_number(checks_by_name["pooled_ratio"].measured, "pooled_ratio")
            != pooled_ratio
        ):
            raise ValueError("pooled_ratio check disagrees with pooled M evidence")
        wins = sum(metric.full_win for metric in self.per_seed)
        if checks_by_name["paired_wins"].measured != wins:
            raise ValueError("paired_wins check disagrees with per-seed evidence")
        if dict(checks_by_name["paired_wins"].evidence).get("total") != len(
            self.per_seed
        ):
            raise ValueError("paired_wins total disagrees with per-seed evidence")
        worst_degradation = max(metric.degradation for metric in self.per_seed)
        if (
            _finite_number(
                checks_by_name["worst_seed_degradation"].measured,
                "worst_seed_degradation",
            )
            != worst_degradation
        ):
            raise ValueError("worst degradation check disagrees with per-seed evidence")
        online = self.checks[_GATE_CHECK_NAMES.index("online_full_vs_shuffle")]
        shuffle_numbers = (
            self.shuffle_pooled_m,
            self.shuffle_waiting_sum,
            self.shuffle_departed_sum,
        )
        if online.evaluated:
            if any(value is None for value in shuffle_numbers):
                raise ValueError("evaluated online Shuffle requires pooled inputs")
            shuffle_m = _finite_number(self.shuffle_pooled_m, "shuffle_pooled_m")
            shuffle_waiting = _finite_number(
                self.shuffle_waiting_sum, "shuffle_waiting_sum"
            )
            shuffle_departed = _finite_number(
                self.shuffle_departed_sum, "shuffle_departed_sum"
            )
            object.__setattr__(self, "shuffle_pooled_m", shuffle_m)
            object.__setattr__(self, "shuffle_waiting_sum", shuffle_waiting)
            object.__setattr__(self, "shuffle_departed_sum", shuffle_departed)
            if shuffle_waiting < 0.0 or shuffle_departed <= 0.0:
                raise ValueError("Shuffle pooled inputs are outside their domain")
            if any(
                metric.shuffle_waiting_total_s is None
                or metric.shuffle_departed_count is None
                for metric in self.per_seed
            ):
                raise ValueError("Shuffle pooled evidence requires every per-seed row")
            per_seed_shuffle_waiting = math.fsum(
                metric.shuffle_waiting_total_s for metric in self.per_seed
            )
            per_seed_shuffle_departed = math.fsum(
                metric.shuffle_departed_count for metric in self.per_seed
            )
            if (
                shuffle_waiting != per_seed_shuffle_waiting
                or shuffle_departed != per_seed_shuffle_departed
            ):
                raise ValueError("Shuffle pooled evidence disagrees with per-seed rows")
            if shuffle_m != shuffle_waiting / shuffle_departed:
                raise ValueError("Shuffle pooled M disagrees with its sums")
            if (
                _finite_number(online.measured, "online_full_vs_shuffle.measured")
                != full_m
                or _finite_number(
                    online.threshold, "online_full_vs_shuffle.threshold"
                )
                != shuffle_m
            ):
                raise ValueError("online Shuffle check disagrees with pooled evidence")
            shuffle_check_evidence = dict(online.evidence).get("shuffle_pooled_m")
            if (
                _finite_number(
                    shuffle_check_evidence,
                    "online_full_vs_shuffle.evidence.shuffle_pooled_m",
                )
                != shuffle_m
            ):
                raise ValueError("online Shuffle audit evidence disagrees with pooled M")
        elif any(value is not None for value in shuffle_numbers):
            raise ValueError("skipped online Shuffle cannot carry pooled inputs")


def _normalize_implementation_failures(
    value: Mapping[str, Any] | Sequence[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    if isinstance(value, Mapping):
        if set(value) != set(S1_IMPLEMENTATION_CHECKS):
            raise ValueError(
                "implementation failures require the exact frozen check names"
            )
        pairs = tuple((name, value[name]) for name in S1_IMPLEMENTATION_CHECKS)
    else:
        pairs = _defensive_pairs(value, "implementation_failures")
    if tuple(name for name, _ in pairs) != S1_IMPLEMENTATION_CHECKS:
        raise ValueError(
            "implementation failures must use the frozen check order"
        )
    return tuple(
        (name, _strict_int(count, name, minimum=0)) for name, count in pairs
    )


@dataclass(frozen=True)
class S1TrainingAudit:
    status: str
    subtype: str | None
    completed_batches: int
    completed_episodes: int
    policy_generation: int
    first_traffic_seed: int
    last_traffic_seed: int
    workers: int
    candidate_kind: str
    candidate_completed_episodes: int
    recovery_kind: str
    implementation_failures: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.status) is not str:
            raise TypeError("S1 training status must be an exact string")
        if self.subtype is not None and type(self.subtype) is not str:
            raise TypeError("S1 training subtype must be an exact string or None")
        for name in (
            "completed_batches",
            "completed_episodes",
            "policy_generation",
            "first_traffic_seed",
            "last_traffic_seed",
            "workers",
            "candidate_completed_episodes",
        ):
            object.__setattr__(
                self,
                name,
                _strict_int(getattr(self, name), name, minimum=0),
            )
        for name in ("candidate_kind", "recovery_kind"):
            if type(getattr(self, name)) is not str:
                raise TypeError(f"{name} must be an exact string")
        object.__setattr__(
            self,
            "implementation_failures",
            _normalize_implementation_failures(self.implementation_failures),
        )
        if self.status not in {"PASS", "S1_FAIL"}:
            raise ValueError("S1 training status must be PASS or S1_FAIL")
        has_failure = any(count for _, count in self.implementation_failures)
        if self.status == "S1_FAIL":
            if self.subtype != "IMPLEMENTATION_INVALID" or not has_failure:
                raise ValueError(
                    "S1_FAIL training requires IMPLEMENTATION_INVALID evidence"
                )
            return
        if self.subtype is not None or has_failure:
            raise ValueError("passing S1 training cannot carry failures")
        exact_values = (
            self.completed_batches,
            self.completed_episodes,
            self.policy_generation,
            self.first_traffic_seed,
            self.last_traffic_seed,
            self.workers,
            self.candidate_kind,
            self.candidate_completed_episodes,
            self.recovery_kind,
        )
        if exact_values != (25, 400, 25, 2643, 3042, 16, "candidate", 400, "recovery"):
            raise ValueError("passing S1 training requires the exact ep400 audit")


@dataclass(frozen=True)
class S1SeedComparison:
    seed: int
    s1_m: float
    s0_wrapper_m: float
    senior_m: float
    s1_win: bool
    s1_waiting_total_s: float
    s1_departed_count: float
    s0_wrapper_waiting_total_s: float
    s0_wrapper_departed_count: float
    senior_waiting_total_s: float
    senior_departed_count: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "seed", _strict_int(self.seed, "seed"))
        if type(self.s1_win) is not bool:
            raise TypeError("s1_win must be an exact boolean")
        for name in (
            "s1_m",
            "s0_wrapper_m",
            "senior_m",
            "s1_waiting_total_s",
            "s1_departed_count",
            "s0_wrapper_waiting_total_s",
            "s0_wrapper_departed_count",
            "senior_waiting_total_s",
            "senior_departed_count",
        ):
            object.__setattr__(self, name, _finite_number(getattr(self, name), name))
        if (
            self.s1_waiting_total_s < 0.0
            or self.s0_wrapper_waiting_total_s < 0.0
            or self.senior_waiting_total_s < 0.0
            or self.s1_departed_count <= 0.0
            or self.s0_wrapper_departed_count <= 0.0
            or self.senior_departed_count <= 0.0
            or self.s0_wrapper_m <= 0.0
        ):
            raise ValueError("S1 per-seed evidence is outside its domain")
        if (
            self.s1_m != self.s1_waiting_total_s / self.s1_departed_count
            or self.s0_wrapper_m
            != self.s0_wrapper_waiting_total_s / self.s0_wrapper_departed_count
            or self.senior_m
            != self.senior_waiting_total_s / self.senior_departed_count
        ):
            raise ValueError("S1 per-seed M disagrees with raw official metrics")
        if self.s1_win is not (self.s1_m < self.s0_wrapper_m):
            raise ValueError("S1 per-seed win disagrees with M values")


@dataclass(frozen=True)
class S1Adjudication:
    status: str
    subtype: str | None
    seed_role: str
    mechanism: GateVerdict
    implementation_failures: tuple[tuple[str, int], ...]
    per_seed: tuple[S1SeedComparison, ...]
    checks: tuple[GateCheck, ...]
    s1_pooled_m: float
    s0_wrapper_pooled_m: float
    senior_pooled_m: float
    s1_waiting_sum: float
    s1_departed_sum: float
    s0_wrapper_waiting_sum: float
    s0_wrapper_departed_sum: float
    senior_waiting_sum: float
    senior_departed_sum: float

    @property
    def pooled_ratio(self) -> float:
        return self.s1_pooled_m / self.s0_wrapper_pooled_m

    @property
    def paired_wins(self) -> int:
        return sum(metric.s1_win for metric in self.per_seed)

    def __post_init__(self) -> None:
        if type(self.status) is not str or type(self.seed_role) is not str:
            raise TypeError("S1 adjudication status and seed_role must be exact strings")
        if self.subtype is not None and type(self.subtype) is not str:
            raise TypeError("S1 adjudication subtype must be an exact string or None")
        if type(self.mechanism) is not GateVerdict:
            raise TypeError("S1 adjudication mechanism must be a GateVerdict")
        object.__setattr__(
            self,
            "implementation_failures",
            _normalize_implementation_failures(self.implementation_failures),
        )
        object.__setattr__(self, "per_seed", _defensive_tuple(self.per_seed, "per_seed"))
        object.__setattr__(self, "checks", _defensive_tuple(self.checks, "checks"))
        if any(type(item) is not S1SeedComparison for item in self.per_seed):
            raise TypeError("S1 per_seed values must be S1SeedComparison records")
        if any(type(item) is not GateCheck for item in self.checks):
            raise TypeError("S1 checks must be GateCheck records")
        if self.seed_role not in {"dev", "s1_validation"}:
            raise ValueError("S1 adjudication requires dev or s1_validation seeds")
        expected_seeds = DEV_SEEDS if self.seed_role == "dev" else S1_VALIDATION_SEEDS
        if tuple(item.seed for item in self.per_seed) != expected_seeds:
            raise ValueError("S1 per-seed evidence does not match its frozen seed role")
        if self.mechanism.status == "INVALID":
            if self.mechanism.seed_role is not None:
                raise ValueError("invalid S1 mechanism must not claim a seed role")
        else:
            if self.mechanism.seed_role != self.seed_role:
                raise ValueError("S1 mechanism seed role disagrees with adjudication")
            mechanism_rows = tuple(
                (item.seed, item.full_waiting_total_s, item.full_departed_count)
                for item in self.mechanism.per_seed
            )
            s1_rows = tuple(
                (item.seed, item.s1_waiting_total_s, item.s1_departed_count)
                for item in self.per_seed
            )
            if mechanism_rows != s1_rows:
                raise ValueError("S1 mechanism Full rows disagree with S1 Full evidence")
        numeric_names = (
            "s1_pooled_m",
            "s0_wrapper_pooled_m",
            "senior_pooled_m",
            "s1_waiting_sum",
            "s1_departed_sum",
            "s0_wrapper_waiting_sum",
            "s0_wrapper_departed_sum",
            "senior_waiting_sum",
            "senior_departed_sum",
        )
        for name in numeric_names:
            object.__setattr__(self, name, _finite_number(getattr(self, name), name))
        raw_sums = (
            math.fsum(item.s1_waiting_total_s for item in self.per_seed),
            math.fsum(item.s1_departed_count for item in self.per_seed),
            math.fsum(item.s0_wrapper_waiting_total_s for item in self.per_seed),
            math.fsum(item.s0_wrapper_departed_count for item in self.per_seed),
            math.fsum(item.senior_waiting_total_s for item in self.per_seed),
            math.fsum(item.senior_departed_count for item in self.per_seed),
        )
        claimed_sums = (
            self.s1_waiting_sum,
            self.s1_departed_sum,
            self.s0_wrapper_waiting_sum,
            self.s0_wrapper_departed_sum,
            self.senior_waiting_sum,
            self.senior_departed_sum,
        )
        if claimed_sums != raw_sums:
            raise ValueError("S1 pooled sums disagree with per-seed evidence")
        expected_m = (
            self.s1_waiting_sum / self.s1_departed_sum,
            self.s0_wrapper_waiting_sum / self.s0_wrapper_departed_sum,
            self.senior_waiting_sum / self.senior_departed_sum,
        )
        if (
            self.s1_pooled_m,
            self.s0_wrapper_pooled_m,
            self.senior_pooled_m,
        ) != expected_m:
            raise ValueError("S1 pooled M disagrees with raw official sums")
        expected_check_names = (
            ("s1_s0_dev_noninferiority",)
            if self.seed_role == "dev"
            else ("s1_s0_validation_ratio", "s1_s0_paired_wins")
        )
        if tuple(check.name for check in self.checks) != expected_check_names:
            raise ValueError("S1 adjudication checks do not match the frozen seed role")
        ratio = self.pooled_ratio
        wins = self.paired_wins
        if self.seed_role == "dev":
            check = self.checks[0]
            if (
                check.comparator != "<="
                or check.threshold != 1.0
                or check.measured != ratio
            ):
                raise ValueError("S1 dev non-inferiority check is not canonical")
            incremental_pass = check.passed
        else:
            ratio_check, wins_check = self.checks
            if (
                ratio_check.comparator != "<="
                or ratio_check.threshold != 0.995
                or ratio_check.measured != ratio
                or wins_check.comparator != ">="
                or wins_check.threshold != 5
                or wins_check.measured != wins
                or dict(wins_check.evidence).get("total") != 8
            ):
                raise ValueError("S1 validation checks are not canonical")
            incremental_pass = ratio_check.passed and wins_check.passed
        has_implementation_failure = any(
            count for _, count in self.implementation_failures
        ) or self.mechanism.status == "INVALID"
        if has_implementation_failure:
            expected_status, expected_subtype = "S1_FAIL", "IMPLEMENTATION_INVALID"
        elif not self.mechanism.passed:
            expected_status, expected_subtype = "S1_FAIL", "MECHANISM_FAIL"
        elif self.seed_role == "dev":
            expected_status = "PASS" if incremental_pass else "S1_FAIL"
            expected_subtype = None if incremental_pass else "MECHANISM_FAIL"
        else:
            expected_status = "GO_V8L" if incremental_pass else "S1_VALID"
            expected_subtype = None
        if (self.status, self.subtype) != (expected_status, expected_subtype):
            raise ValueError("S1 adjudication status disagrees with typed evidence")


@dataclass(frozen=True)
class BandCalibration:
    name: str
    tau1: float
    tau2: float
    c_star: float

    def __post_init__(self) -> None:
        if type(self.name) is not str:
            raise TypeError("calibration band name must be an exact string")
        if self.name not in BANDS:
            raise ValueError("unknown calibration band")
        tau1 = _finite_number(self.tau1, "tau1")
        tau2 = _finite_number(self.tau2, "tau2")
        c_star = _finite_number(self.c_star, "c_star")
        object.__setattr__(self, "tau1", tau1)
        object.__setattr__(self, "tau2", tau2)
        object.__setattr__(self, "c_star", c_star)
        if not tau1 < tau2 or c_star <= 0.0:
            raise ValueError("band calibration requires tau1 < tau2 and C* > 0")


@dataclass(frozen=True)
class Calibration:
    lambda_star: float
    quantile_method: str
    margin_quantiles: tuple[float, float, float, float, float, float]
    margin_row_count: int
    eligible_row_count: int
    c_star: float
    scaled_span_source: str
    bands: tuple[BandCalibration, BandCalibration, BandCalibration]
    source_seeds: tuple[int, ...]
    probe_hashes: tuple[tuple[int, str], ...]
    config_hash: str

    def __post_init__(self) -> None:
        for name in ("quantile_method", "scaled_span_source", "config_hash"):
            if type(getattr(self, name)) is not str:
                raise TypeError(f"{name} must be an exact string")
        object.__setattr__(
            self,
            "margin_quantiles",
            _defensive_tuple(self.margin_quantiles, "margin_quantiles"),
        )
        object.__setattr__(self, "bands", _defensive_tuple(self.bands, "bands"))
        object.__setattr__(
            self, "source_seeds", _defensive_tuple(self.source_seeds, "source_seeds")
        )
        object.__setattr__(
            self, "probe_hashes", _defensive_pairs(self.probe_hashes, "probe_hashes")
        )
        if any(type(digest) is not str for _, digest in self.probe_hashes):
            raise TypeError("probe hash digests must be exact strings")
        object.__setattr__(
            self,
            "source_seeds",
            tuple(_strict_int(seed, "source seed") for seed in self.source_seeds),
        )
        object.__setattr__(
            self,
            "probe_hashes",
            tuple(
                (_strict_int(seed, "probe hash seed"), digest)
                for seed, digest in self.probe_hashes
            ),
        )
        lambda_star = _finite_number(self.lambda_star, "lambda_star")
        object.__setattr__(self, "lambda_star", lambda_star)
        if lambda_star not in _POSITIVE_LAMBDAS:
            raise ValueError("calibration lambda must be a positive frozen grid point")
        if self.quantile_method != "linear":
            raise ValueError("calibration quantile method must be linear")
        if len(self.margin_quantiles) != 6:
            raise ValueError("calibration requires exactly six margin quantiles")
        quantiles = tuple(
            _finite_number(value, "margin quantile")
            for value in self.margin_quantiles
        )
        object.__setattr__(self, "margin_quantiles", quantiles)
        if tuple(sorted(quantiles)) != quantiles:
            raise ValueError("margin quantiles must be non-decreasing")
        margin_row_count = _strict_int(
            self.margin_row_count, "margin_row_count", minimum=1
        )
        eligible_row_count = _strict_int(
            self.eligible_row_count, "eligible_row_count", minimum=1
        )
        object.__setattr__(self, "margin_row_count", margin_row_count)
        object.__setattr__(self, "eligible_row_count", eligible_row_count)
        if margin_row_count <= 0:
            raise ValueError("margin_row_count must be positive")
        if eligible_row_count <= 0:
            raise ValueError("eligible_row_count must be positive")
        c_star = _finite_number(self.c_star, "c_star")
        object.__setattr__(self, "c_star", c_star)
        if c_star <= 0.0:
            raise ValueError("C* must be positive")
        if self.scaled_span_source != "lambda_star*gated_collaboration_residual":
            raise ValueError("calibration span source is not canonical")
        if any(type(band) is not BandCalibration for band in self.bands):
            raise TypeError("calibration bands must be BandCalibration records")
        if tuple(band.name for band in self.bands) != tuple(BANDS):
            raise ValueError("calibration bands must be Narrow, Medium, Wide")
        expected_indices = ((0, 2), (1, 4), (3, 5))
        for band, (lower, upper) in zip(self.bands, expected_indices):
            if (
                band.c_star != c_star
                or band.tau1 != quantiles[lower]
                or band.tau2 != quantiles[upper]
            ):
                raise ValueError("band thresholds and C* must match global calibration")
        if self.source_seeds != DEV_SEEDS:
            raise ValueError("calibration source seeds must be exactly DEV_SEEDS")
        if tuple(seed for seed, _ in self.probe_hashes) != DEV_SEEDS:
            raise ValueError("probe hashes must cover DEV_SEEDS in order")
        if not all(_is_sha256(digest) for _, digest in self.probe_hashes):
            raise ValueError("every probe hash must be lowercase SHA-256")
        if not _is_sha256(self.config_hash):
            raise ValueError("config_hash must be lowercase SHA-256")


@dataclass(frozen=True)
class ArtifactDigest:
    role: str
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.role) is not str
            or not self.role
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in self.role)
        ):
            raise ValueError("artifact role must use lowercase letters, digits, or underscore")
        if type(self.path) is not str or not self.path:
            raise ValueError("artifact path must be a non-empty string")
        if not _is_sha256(self.sha256):
            raise ValueError("artifact digest must be lowercase SHA-256")


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    verdicts: tuple[tuple[str, GateVerdict], ...]
    selection: Mapping[str, Any]
    calibration: Calibration | None
    manifest: Mapping[str, Any]
    config: Mapping[str, Any]
    input_artifacts: tuple[ArtifactDigest, ...]
    evidence: Mapping[str, Any]
    s1_training_audit: S1TrainingAudit | None = None
    s1_adjudication: S1Adjudication | None = None

    def __post_init__(self) -> None:
        if type(self.stage) is not str or type(self.status) is not str:
            raise TypeError("stage and status must be exact strings")
        object.__setattr__(
            self, "verdicts", _defensive_pairs(self.verdicts, "verdicts")
        )
        object.__setattr__(
            self,
            "input_artifacts",
            _defensive_tuple(self.input_artifacts, "input_artifacts"),
        )
        if self.stage not in _STAGE_STATUSES:
            raise ValueError("stage must be one of S0-D, S0-W, S0-V, S1-T, or S1-V")
        if self.status not in _STAGE_STATUSES[self.stage]:
            raise ValueError(
                f"status {self.status!r} is not valid for stage {self.stage}"
            )
        for name in ("selection", "manifest", "config", "evidence"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            object.__setattr__(self, name, _freeze_json(value))
        if self.calibration is not None and type(self.calibration) is not Calibration:
            raise TypeError("calibration must be a Calibration record or None")
        if (
            self.s1_training_audit is not None
            and type(self.s1_training_audit) is not S1TrainingAudit
        ):
            raise TypeError("s1_training_audit must be an S1TrainingAudit or None")
        if (
            self.s1_adjudication is not None
            and type(self.s1_adjudication) is not S1Adjudication
        ):
            raise TypeError("s1_adjudication must be an S1Adjudication or None")
        labels = []
        for label, verdict in self.verdicts:
            if type(label) is not str or not label:
                raise ValueError("every stage verdict requires a non-empty label")
            if type(verdict) is not GateVerdict:
                raise TypeError("stage verdict values must be GateVerdict objects")
            labels.append(label)
        if len(set(labels)) != len(labels):
            raise ValueError("stage verdict labels must be unique")
        if any(
            type(artifact) is not ArtifactDigest
            for artifact in self.input_artifacts
        ):
            raise TypeError("input artifacts must be ArtifactDigest records")
        roles = [artifact.role for artifact in self.input_artifacts]
        if len(set(roles)) != len(roles):
            raise ValueError("input artifact roles must be unique")
        if not _REQUIRED_INPUT_ARTIFACT_ROLES.issubset(roles):
            raise ValueError("stage result is missing required input artifacts")
        reserved_manifest = {"schema", "stage", "status", "input_artifacts"}
        if reserved_manifest.intersection(self.manifest):
            raise ValueError("manifest extras contain a reserved key")
        if (
            self.status in _EVIDENCED_STAGE_STATUSES
            and self.stage != "S1-T"
            and not self.verdicts
        ):
            raise ValueError("a complete stage requires gate verdict evidence")
        if self.status in _AUDITED_STAGE_STATUSES and not self.evidence:
            raise ValueError("a stage result requires structured audit evidence")
        if self.status in _AUDITED_STAGE_STATUSES:
            reason = self.selection.get("reason")
            if not isinstance(reason, str) or not reason:
                raise ValueError("a stage result selection requires an audit reason")
        if self.stage == "S0-D" and self.status in {"PASS", "FAIL"}:
            if tuple(labels) != _POSITIVE_LAMBDA_LABELS:
                raise ValueError("S0-D verdict labels must be the canonical lambda grid")
            verdict_map = {
                value: verdict
                for value, (_, verdict) in zip(_POSITIVE_LAMBDAS, self.verdicts)
            }
            selected = choose_smallest_passing_lambda(verdict_map)
            if self.status == "PASS":
                selected_claim = _finite_number(
                    self.selection.get("lambda_star"), "selection.lambda_star"
                )
                if selected is None or selected_claim != selected:
                    raise ValueError("S0-D selection is not the smallest passing lambda")
            elif selected is not None or self.selection.get("lambda_star") is not None:
                raise ValueError("S0-D FAIL requires a complete grid with no passing lambda")
            if self.calibration is not None:
                raise ValueError("S0-D cannot carry wrapper calibration")
            config_grid = tuple(self.config.get("lambda_grid", ()))
            if (
                config_grid != LAMBDA_GRID
                or any(type(value) is not float for value in config_grid)
            ):
                raise ValueError("S0-D config must record the exact lambda grid")
            if self.config.get("quantile_method") != "linear":
                raise ValueError("S0-D config must freeze quantile_method=linear")
            if self.evidence.get("lambda_grid_complete") is not True:
                raise ValueError("S0-D must record a complete lambda grid")
        if self.stage == "S0-W" and self.status in {"PASS", "FAIL"}:
            verdict_map = dict(self.verdicts)
            selected_band = choose_first_passing_band(verdict_map)
            band_names = tuple(BANDS)
            expected_labels = (
                band_names
                if selected_band is None
                else band_names[: band_names.index(selected_band) + 1]
            )
            if tuple(labels) != expected_labels:
                raise ValueError("S0-W verdict labels must be the canonical band prefix")
            if self.status == "PASS":
                if selected_band is None or self.selection.get("band") != selected_band:
                    raise ValueError("S0-W selection is not the first passing band")
            elif selected_band is not None or self.selection.get("band") is not None:
                raise ValueError("S0-W FAIL requires all three bands to fail")
            if self.calibration is None:
                raise ValueError("S0-W requires bound calibration evidence")
            selected_lambda = _finite_number(
                self.selection.get("lambda_star"), "selection.lambda_star"
            )
            if selected_lambda != self.calibration.lambda_star:
                raise ValueError("S0-W lambda does not match calibration")
            config_grid = tuple(self.config.get("lambda_grid", ()))
            if (
                config_grid != LAMBDA_GRID
                or any(type(value) is not float for value in config_grid)
            ):
                raise ValueError("S0-W config must record the exact lambda grid")
            if self.config.get("quantile_method") != "linear":
                raise ValueError("S0-W config must freeze quantile_method=linear")
            if self.evidence.get("band_prefix_complete") is not True:
                raise ValueError("S0-W must record complete band-prefix evidence")
        if self.stage != "S1-T" and self.s1_training_audit is not None:
            raise ValueError("only S1-T can carry an S1 training audit")
        if self.stage != "S1-V" and self.s1_adjudication is not None:
            raise ValueError("only S1-V can carry an S1 adjudication")
        if self.stage == "S0-V" and self.status in {"PASS", "FAIL"}:
            if tuple(labels) != ("frozen_wrapper",):
                raise ValueError("S0-V requires one frozen_wrapper verdict")
            verdict = self.verdicts[0][1]
            if verdict.seed_role != "s0_validation" or verdict.status != self.status:
                raise ValueError("S0-V status disagrees with its frozen validation gate")
            if self.calibration is not None:
                raise ValueError("S0-V cannot recalibrate the frozen wrapper")
            if self.evidence.get("frozen_wrapper_unchanged") is not True:
                raise ValueError("S0-V must prove the frozen wrapper was unchanged")
        if self.stage == "S1-T" and self.status in {"PASS", "S1_FAIL"}:
            if self.verdicts:
                raise ValueError("S1-T uses a training audit, not gate verdicts")
            if self.calibration is not None or self.s1_adjudication is not None:
                raise ValueError("S1-T cannot carry calibration or adjudication")
            if (
                self.s1_training_audit is None
                or self.s1_training_audit.status != self.status
            ):
                raise ValueError("S1-T status disagrees with its exact training audit")
            if self.evidence.get("training_audit_complete") is not True:
                raise ValueError("S1-T requires complete training audit evidence")
        if self.stage == "S1-V" and self.status in {
            "S1_FAIL",
            "S1_VALID",
            "GO_V8L",
        }:
            if tuple(labels) != ("mechanism",):
                raise ValueError("S1-V requires one mechanism verdict")
            if self.calibration is not None or self.s1_training_audit is not None:
                raise ValueError("S1-V cannot carry calibration or training audit")
            if self.s1_adjudication is None:
                raise ValueError("S1-V requires typed adjudication evidence")
            if (
                self.s1_adjudication.status != self.status
                or self.s1_adjudication.seed_role != "s1_validation"
                or self.verdicts[0][1] != self.s1_adjudication.mechanism
            ):
                raise ValueError("S1-V status disagrees with typed adjudication")
            if self.evidence.get("adjudication_complete") is not True:
                raise ValueError("S1-V requires complete adjudication evidence")


@dataclass(frozen=True)
class _RunMetric:
    seed: int
    waiting: float
    departed: float
    m: float


def _normalize_run_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[_RunMetric, ...]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError("run rows must be a non-empty sequence")
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("each run row must be a mapping")
        if row.get("status") != "complete":
            raise ValueError("every run row must have status='complete'")
        seed = _strict_int(row.get("seed"), "run seed")
        if seed in seen:
            raise ValueError(f"duplicate run seed {seed}")
        seen.add(seed)
        official = row["official_metrics"]
        if not isinstance(official, Mapping):
            raise TypeError("official_metrics must be a mapping")
        waiting = _finite_number(
            official["all_waiting_total_s"], "all_waiting_total_s"
        )
        departed = _finite_number(official["departed_count"], "departed_count")
        if waiting < 0.0:
            raise ValueError("all_waiting_total_s must be non-negative")
        if departed <= 0.0:
            raise ValueError("departed_count must be positive")
        m = waiting / departed
        if not math.isfinite(m):
            raise ValueError("per-seed M must be finite")
        result.append(_RunMetric(seed, waiting, departed, m))
    return tuple(sorted(result, key=lambda item: item.seed))


def pooled_m(rows: Sequence[Mapping[str, Any]]) -> float:
    """Return sum(waiting) / sum(departed) from nested official metrics."""

    normalized = _normalize_run_rows(rows)
    waiting = math.fsum(item.waiting for item in normalized)
    departed = math.fsum(item.departed for item in normalized)
    result = waiting / departed
    if not math.isfinite(result):
        raise ValueError("pooled M must be finite")
    return result


def _pooled_components(rows: tuple[_RunMetric, ...]) -> tuple[float, float, float]:
    waiting = math.fsum(item.waiting for item in rows)
    departed = math.fsum(item.departed for item in rows)
    value = waiting / departed
    if not all(math.isfinite(item) for item in (waiting, departed, value)):
        raise ValueError("pooled M inputs and result must be finite")
    return waiting, departed, value


def adjudicate_s1(
    *,
    mechanism: GateVerdict,
    s1_full_rows: Sequence[Mapping[str, Any]],
    s0_wrapper_rows: Sequence[Mapping[str, Any]],
    senior_rows: Sequence[Mapping[str, Any]],
    implementation_failures: Mapping[str, Any] | Sequence[tuple[str, int]],
    seed_role: str,
) -> S1Adjudication:
    """Derive the frozen S1 decision from raw official metrics.

    Senior is deliberately retained only as auditable report data.  No senior
    value participates in ``status`` or ``subtype``.
    """

    if type(mechanism) is not GateVerdict:
        raise TypeError("mechanism must be an exact GateVerdict")
    if type(seed_role) is not str or seed_role not in {"dev", "s1_validation"}:
        raise ValueError("seed_role must be dev or s1_validation")
    expected_seeds = DEV_SEEDS if seed_role == "dev" else S1_VALIDATION_SEEDS
    s1_rows = _normalize_run_rows(s1_full_rows)
    s0_rows = _normalize_run_rows(s0_wrapper_rows)
    senior_normalized = _normalize_run_rows(senior_rows)
    for label, rows in (
        ("S1 Full", s1_rows),
        ("Frozen S0 Wrapper", s0_rows),
        ("senior", senior_normalized),
    ):
        if tuple(item.seed for item in rows) != expected_seeds:
            raise ValueError(f"{label} rows do not match the frozen {seed_role} seeds")
    failures = _normalize_implementation_failures(implementation_failures)
    per_seed = tuple(
        S1SeedComparison(
            seed=s1.seed,
            s1_m=s1.m,
            s0_wrapper_m=s0.m,
            senior_m=senior.m,
            s1_win=s1.m < s0.m,
            s1_waiting_total_s=s1.waiting,
            s1_departed_count=s1.departed,
            s0_wrapper_waiting_total_s=s0.waiting,
            s0_wrapper_departed_count=s0.departed,
            senior_waiting_total_s=senior.waiting,
            senior_departed_count=senior.departed,
        )
        for s1, s0, senior in zip(s1_rows, s0_rows, senior_normalized)
    )
    s1_waiting, s1_departed, s1_m = _pooled_components(s1_rows)
    s0_waiting, s0_departed, s0_m = _pooled_components(s0_rows)
    senior_waiting, senior_departed, senior_m = _pooled_components(
        senior_normalized
    )
    if s0_m <= 0.0:
        raise ValueError("Frozen S0 Wrapper M must be positive")
    ratio = s1_m / s0_m
    wins = sum(item.s1_win for item in per_seed)
    if seed_role == "dev":
        checks = (
            GateCheck(
                "s1_s0_dev_noninferiority",
                ratio,
                "<=",
                1.0,
                ratio <= 1.0,
            ),
        )
    else:
        checks = (
            GateCheck(
                "s1_s0_validation_ratio",
                ratio,
                "<=",
                0.995,
                ratio <= 0.995,
            ),
            GateCheck(
                "s1_s0_paired_wins",
                wins,
                ">=",
                5,
                wins >= 5,
                (("total", 8),),
            ),
        )
    has_implementation_failure = any(count for _, count in failures)
    if has_implementation_failure or mechanism.status == "INVALID":
        status, subtype = "S1_FAIL", "IMPLEMENTATION_INVALID"
    elif not mechanism.passed:
        status, subtype = "S1_FAIL", "MECHANISM_FAIL"
    elif seed_role == "dev":
        if checks[0].passed:
            status, subtype = "PASS", None
        else:
            status, subtype = "S1_FAIL", "MECHANISM_FAIL"
    elif all(check.passed for check in checks):
        status, subtype = "GO_V8L", None
    else:
        status, subtype = "S1_VALID", None
    return S1Adjudication(
        status=status,
        subtype=subtype,
        seed_role=seed_role,
        mechanism=mechanism,
        implementation_failures=failures,
        per_seed=per_seed,
        checks=checks,
        s1_pooled_m=s1_m,
        s0_wrapper_pooled_m=s0_m,
        senior_pooled_m=senior_m,
        s1_waiting_sum=s1_waiting,
        s1_departed_sum=s1_departed,
        s0_wrapper_waiting_sum=s0_waiting,
        s0_wrapper_departed_sum=s0_departed,
        senior_waiting_sum=senior_waiting,
        senior_departed_sum=senior_departed,
    )


def _require_evidence_float(
    evidence: Mapping[str, Any], name: str, *, unit_interval: bool = False
) -> float:
    if name not in evidence:
        raise KeyError(f"same-state evidence is missing {name}")
    value = _finite_number(evidence[name], name)
    if unit_interval and not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return value


def _require_evidence_count(evidence: Mapping[str, Any], name: str) -> int:
    if name not in evidence:
        raise KeyError(f"same-state evidence is missing {name}")
    return _strict_int(evidence[name], name, minimum=0)


def _invalid_verdict(reason: str) -> GateVerdict:
    return GateVerdict(
        status="INVALID",
        passed=False,
        seed_role=None,
        reasons=(reason,),
    )


def _evaluate_s0_gate(
    full: Sequence[Mapping[str, Any]],
    nocollab: Sequence[Mapping[str, Any]],
    shuffle_online: Sequence[Mapping[str, Any]] | None,
    same_state: Mapping[str, Any],
) -> GateVerdict:
    if not isinstance(same_state, Mapping):
        raise TypeError("same_state must be a mapping")
    full_rows = _normalize_run_rows(full)
    nocollab_rows = _normalize_run_rows(nocollab)
    seed_tuple = tuple(item.seed for item in full_rows)
    if seed_tuple not in _EVALUATION_SEED_ROLES:
        raise ValueError("run seeds do not match a frozen eight-seed evaluation role")
    if tuple(item.seed for item in nocollab_rows) != seed_tuple:
        raise ValueError("NoCollab seed rows do not match Full")

    full_wait, full_dep, full_m = _pooled_components(full_rows)
    nc_wait, nc_dep, nc_m = _pooled_components(nocollab_rows)
    if nc_m <= 0.0:
        raise ValueError("NoCollab M must be positive for relative gates")
    pooled_ratio = full_m / nc_m
    if not math.isfinite(pooled_ratio):
        raise ValueError("pooled Full/NoCollab ratio must be finite")

    base_metrics = []
    for full_row, nc_row in zip(full_rows, nocollab_rows):
        if nc_row.m <= 0.0:
            raise ValueError("each NoCollab per-seed M must be positive")
        degradation = (full_row.m - nc_row.m) / nc_row.m
        if not math.isfinite(degradation):
            raise ValueError("per-seed degradation must be finite")
        base_metrics.append((full_row, nc_row, degradation))
    wins = sum(full_row.m < nc_row.m for full_row, nc_row, _ in base_metrics)
    worst_degradation = max(item[2] for item in base_metrics)

    flip = _require_evidence_float(
        same_state, "full_nocollab_flip", unit_interval=True
    )
    action_diff = _require_evidence_float(
        same_state, "full_shuffle_action_diff", unit_interval=True
    )
    illegal = _require_evidence_count(same_state, "illegal_action_count")
    nonfinite = _require_evidence_count(same_state, "nonfinite_count")
    violations = _require_evidence_count(same_state, "span_violation_count")

    prelim_checks = {
        "pooled_ratio": GateCheck(
            "pooled_ratio", pooled_ratio, "<=", 0.995, pooled_ratio <= 0.995
        ),
        "paired_wins": GateCheck(
            "paired_wins", wins, ">=", 5, wins >= 5, (("total", 8),)
        ),
        "worst_seed_degradation": GateCheck(
            "worst_seed_degradation",
            worst_degradation,
            "<=",
            0.05,
            worst_degradation <= 0.05,
        ),
        "full_nocollab_flip": GateCheck(
            "full_nocollab_flip",
            flip,
            "inclusive_range",
            (0.02, 0.15),
            0.02 <= flip <= 0.15,
        ),
        "full_shuffle_action_diff": GateCheck(
            "full_shuffle_action_diff",
            action_diff,
            ">=",
            0.01,
            action_diff >= 0.01,
        ),
        "illegal_actions": GateCheck(
            "illegal_actions", illegal, "==", 0, illegal == 0
        ),
        "nonfinite_values": GateCheck(
            "nonfinite_values", nonfinite, "==", 0, nonfinite == 0
        ),
        "span_violations": GateCheck(
            "span_violations", violations, "==", 0, violations == 0
        ),
    }
    preliminary_pass = all(check.passed for check in prelim_checks.values())

    shuffle_rows = None
    sh_wait = sh_dep = sh_m = None
    if shuffle_online is None:
        if preliminary_pass:
            raise ValueError("online Shuffle evidence is required after preliminary PASS")
        online_check = GateCheck(
            "online_full_vs_shuffle",
            None,
            "<=",
            None,
            False,
            evaluated=False,
            reason="not_run_after_preliminary_failure",
        )
    else:
        shuffle_rows = _normalize_run_rows(shuffle_online)
        if tuple(item.seed for item in shuffle_rows) != seed_tuple:
            raise ValueError("Shuffle seed rows do not match Full")
        sh_wait, sh_dep, sh_m = _pooled_components(shuffle_rows)
        online_check = GateCheck(
            "online_full_vs_shuffle",
            full_m,
            "<=",
            sh_m,
            full_m <= sh_m,
            (("shuffle_pooled_m", sh_m),),
        )

    per_seed = []
    for index, (full_row, nc_row, degradation) in enumerate(base_metrics):
        shuffle_row = None if shuffle_rows is None else shuffle_rows[index]
        per_seed.append(
            SeedMetric(
                seed=full_row.seed,
                full_m=full_row.m,
                nocollab_m=nc_row.m,
                shuffle_online_m=None if shuffle_row is None else shuffle_row.m,
                full_win=full_row.m < nc_row.m,
                degradation=degradation,
                full_waiting_total_s=full_row.waiting,
                full_departed_count=full_row.departed,
                nocollab_waiting_total_s=nc_row.waiting,
                nocollab_departed_count=nc_row.departed,
                shuffle_waiting_total_s=(
                    None if shuffle_row is None else shuffle_row.waiting
                ),
                shuffle_departed_count=(
                    None if shuffle_row is None else shuffle_row.departed
                ),
            )
        )

    checks = (
        prelim_checks["pooled_ratio"],
        prelim_checks["paired_wins"],
        prelim_checks["worst_seed_degradation"],
        prelim_checks["full_nocollab_flip"],
        prelim_checks["full_shuffle_action_diff"],
        online_check,
        prelim_checks["illegal_actions"],
        prelim_checks["nonfinite_values"],
        prelim_checks["span_violations"],
    )
    passed = all(check.evaluated and check.passed for check in checks)
    return GateVerdict(
        status="PASS" if passed else "FAIL",
        passed=passed,
        seed_role=_EVALUATION_SEED_ROLES[seed_tuple],
        checks=checks,
        per_seed=tuple(per_seed),
        full_pooled_m=full_m,
        nocollab_pooled_m=nc_m,
        shuffle_pooled_m=sh_m,
        full_waiting_sum=full_wait,
        full_departed_sum=full_dep,
        nocollab_waiting_sum=nc_wait,
        nocollab_departed_sum=nc_dep,
        shuffle_waiting_sum=sh_wait,
        shuffle_departed_sum=sh_dep,
    )


def evaluate_s0_gate(
    full: Sequence[Mapping[str, Any]],
    nocollab: Sequence[Mapping[str, Any]],
    shuffle_online: Sequence[Mapping[str, Any]] | None,
    same_state: Mapping[str, Any],
) -> GateVerdict:
    """Evaluate S0, returning structured INVALID for malformed evidence."""

    try:
        return _evaluate_s0_gate(full, nocollab, shuffle_online, same_state)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return _invalid_verdict(f"{type(exc).__name__}: {exc}")


def choose_smallest_passing_lambda(
    verdicts: Mapping[float, GateVerdict],
) -> float | None:
    if not isinstance(verdicts, Mapping):
        raise TypeError("lambda verdicts must be a mapping")
    normalized = {}
    for raw_lambda, verdict in verdicts.items():
        value = _finite_number(raw_lambda, "lambda")
        if value not in _POSITIVE_LAMBDAS:
            raise ValueError(f"lambda {value} is not a positive frozen grid point")
        if not isinstance(verdict, GateVerdict):
            raise TypeError("each lambda verdict must be a GateVerdict")
        normalized[value] = verdict
    if set(normalized) != set(_POSITIVE_LAMBDAS):
        raise ValueError("lambda selection requires every positive frozen grid point")
    for verdict in normalized.values():
        if verdict.seed_role != "dev" or verdict.status == "INVALID":
            raise ValueError("lambda selection requires complete DEV PASS/FAIL verdicts")
    for value in _POSITIVE_LAMBDAS:
        if normalized[value].passed:
            return value
    return None


def choose_first_passing_band(verdicts: Mapping[str, GateVerdict]) -> str | None:
    if not isinstance(verdicts, Mapping):
        raise TypeError("band verdicts must be a mapping")
    unknown = set(verdicts) - set(BANDS)
    if unknown:
        raise ValueError(f"unknown margin bands: {sorted(unknown)}")
    for verdict in verdicts.values():
        if not isinstance(verdict, GateVerdict):
            raise TypeError("each band verdict must be a GateVerdict")
        if verdict.seed_role != "dev" or verdict.status == "INVALID":
            raise ValueError("band selection requires complete DEV PASS/FAIL verdicts")

    names = tuple(BANDS)
    first_pass_index = next(
        (index for index, name in enumerate(names) if name in verdicts and verdicts[name].passed),
        None,
    )
    if first_pass_index is None:
        if set(verdicts) != set(names):
            raise ValueError("all three bands are required before concluding no PASS")
        return None
    expected_prefix = set(names[: first_pass_index + 1])
    if set(verdicts) != expected_prefix:
        raise ValueError("band evidence must be the exact prefix through first PASS")
    if any(verdicts[name].passed for name in names[:first_pass_index]):
        raise ValueError("a wider band cannot follow an earlier PASS")
    return names[first_pass_index]


def calibrate_wrapper(
    full_probe_rows: Sequence[Mapping[str, Any]], lambda_star: float
) -> Calibration:
    """Calibrate six margins and one shared Q95 C* from bound raw probe rows."""

    lambda_star = _finite_number(lambda_star, "lambda_star")
    if lambda_star not in _POSITIVE_LAMBDAS:
        raise ValueError("lambda_star must be a positive frozen grid point")
    if (
        isinstance(full_probe_rows, (str, bytes))
        or not isinstance(full_probe_rows, Sequence)
        or not full_probe_rows
    ):
        raise ValueError("full_probe_rows must be a non-empty sequence")

    margins = []
    eligible_spans = []
    provenance: dict[int, dict[str, Any]] = {}
    config_hashes = set()
    for row in full_probe_rows:
        if not isinstance(row, Mapping):
            raise TypeError("each probe row must be a mapping")
        seed = _strict_int(row["traffic_seed"], "traffic_seed")
        row_lambda = _finite_number(row["lambda_scale"], "lambda_scale")
        if row_lambda != lambda_star:
            raise ValueError("probe row lambda_scale does not match lambda_star")
        probe_hash = row["probe_hash"]
        config_hash = row["config_hash"]
        if not _is_sha256(probe_hash) or not _is_sha256(config_hash):
            raise ValueError("probe_hash and config_hash must be lowercase SHA-256")
        config_hashes.add(config_hash)
        row_index = _strict_int(row["probe_row_index"], "probe_row_index", minimum=0)
        row_count = _strict_int(row["probe_row_count"], "probe_row_count", minimum=1)
        if row_index >= row_count:
            raise ValueError("probe_row_index must be less than probe_row_count")
        seed_info = provenance.setdefault(
            seed,
            {"probe_hash": probe_hash, "row_count": row_count, "indices": set()},
        )
        if seed_info["probe_hash"] != probe_hash or seed_info["row_count"] != row_count:
            raise ValueError("per-seed probe provenance is inconsistent")
        if row_index in seed_info["indices"]:
            raise ValueError("duplicate probe row index")
        seed_info["indices"].add(row_index)

        logits = np.asarray(row["baseline_logits"])
        residual = np.asarray(row["gated_collaboration_residual"])
        action_masks = np.asarray(row["action_masks"])
        candidate_mask = np.asarray(row["candidate_mask"])
        if logits.ndim != 1 or residual.ndim != 1 or action_masks.ndim != 1:
            raise ValueError("probe logits, residual, and action masks must be 1-D")
        if logits.shape != residual.shape or logits.shape != action_masks.shape:
            raise ValueError("probe logits, residual, and action mask shapes must match")
        if action_masks.dtype != np.bool_:
            raise TypeError("action_masks must have exact boolean dtype")
        if candidate_mask.ndim != 1 or candidate_mask.dtype != np.bool_:
            raise TypeError("candidate_mask must be a 1-D exact boolean array")
        valid_action_count = _strict_int(
            row["valid_action_counts"], "valid_action_counts", minimum=0
        )
        if valid_action_count > logits.shape[0]:
            raise ValueError("valid_action_counts exceeds the action dimension")
        if logits.dtype.kind not in "fiu" or residual.dtype.kind not in "fiu":
            raise TypeError("probe logits and residual must be numeric")
        logits = logits.astype(np.float64, copy=False)
        residual = residual.astype(np.float64, copy=False)
        if not np.isfinite(logits).all():
            raise ValueError("baseline logits must all be finite")
        if not np.isfinite(residual).all():
            raise ValueError("gated collaboration residual must all be finite")

        legal_mask = action_masks & (
            np.arange(action_masks.shape[0]) < valid_action_count
        )
        if int(legal_mask.sum()) < 2:
            continue
        legal_logits = logits[legal_mask]
        top_two = np.partition(legal_logits, -2)[-2:]
        margin = float(np.max(top_two) - np.min(top_two))
        if not math.isfinite(margin):
            raise ValueError("baseline margin must be finite")
        margins.append(margin)

        if bool(candidate_mask.any()):
            scaled_legal = residual[legal_mask] * lambda_star
            if not np.isfinite(scaled_legal).all():
                raise ValueError("scaled collaboration residual must be finite")
            span = float(np.max(scaled_legal) - np.min(scaled_legal))
            if not math.isfinite(span):
                raise ValueError("scaled collaboration span must be finite")
            if span > 0.0:
                eligible_spans.append(span)

    if tuple(sorted(provenance)) != DEV_SEEDS:
        raise ValueError("calibration rows must cover exactly DEV_SEEDS")
    for seed in DEV_SEEDS:
        info = provenance[seed]
        if info["indices"] != set(range(info["row_count"])):
            raise ValueError(f"calibration rows are incomplete for seed {seed}")
    if len(config_hashes) != 1:
        raise ValueError("calibration rows must share one config_hash")
    if not margins:
        raise ValueError("margin calibration population is empty")
    if not eligible_spans:
        raise ValueError("C* eligible residual population is empty")

    margin_quantiles = np.quantile(
        np.asarray(margins, dtype=np.float64),
        _MARGIN_QUANTILES,
        method="linear",
    )
    c_star = float(
        np.quantile(
            np.asarray(eligible_spans, dtype=np.float64), 0.95, method="linear"
        )
    )
    if not np.isfinite(margin_quantiles).all():
        raise ValueError("margin quantiles must be finite")
    if not math.isfinite(c_star) or c_star <= 0.0:
        raise ValueError("C* must be finite and positive")
    quantiles = tuple(float(value) for value in margin_quantiles)
    band_indices = (("Narrow", 0, 2), ("Medium", 1, 4), ("Wide", 3, 5))
    bands = []
    for name, lower, upper in band_indices:
        tau1, tau2 = quantiles[lower], quantiles[upper]
        bands.append(BandCalibration(name, tau1, tau2, c_star))
    return Calibration(
        lambda_star=lambda_star,
        quantile_method="linear",
        margin_quantiles=quantiles,
        margin_row_count=len(margins),
        eligible_row_count=len(eligible_spans),
        c_star=c_star,
        scaled_span_source="lambda_star*gated_collaboration_residual",
        bands=tuple(bands),
        source_seeds=DEV_SEEDS,
        probe_hashes=tuple(
            (seed, provenance[seed]["probe_hash"]) for seed in DEV_SEEDS
        ),
        config_hash=next(iter(config_hashes)),
    )


def _path_mentions_sealed_seed(path: Path) -> bool:
    return any(str(seed) in part for part in path.parts for seed in SEALED_FINAL_SEEDS)


def _reject_symlink_components(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if any(component.is_symlink() for component in (absolute, *absolute.parents)):
        raise ValueError(f"refusing to follow a symlinked protocol path: {path}")
    return absolute


def _open_directory_fd(path: Path, *, create: bool) -> int:
    absolute = Path(os.path.abspath(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.open(os.path.sep, flags)
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        os.close(current_fd)
        raise ValueError(f"protocol directory path is not safe: {path}") from exc


def _open_regular_fd_at(directory_fd: int, name: str) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError(f"protocol input is not a safe regular file: {name}") from exc
    if not stat.S_ISREG(os.fstat(file_fd).st_mode):
        os.close(file_fd)
        raise ValueError(f"protocol input is not a regular file: {name}")
    return file_fd


def hash_file(path: Path) -> str:
    path = _reject_symlink_components(Path(path))
    if _path_mentions_sealed_seed(path):
        raise ValueError("refusing to read a sealed-final seed path")
    directory_fd = _open_directory_fd(path.parent, create=False)
    try:
        file_fd = _open_regular_fd_at(directory_fd, path.name)
    finally:
        os.close(directory_fd)
    digest = hashlib.sha256()
    with os.fdopen(file_fd, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_regular_bytes(path: Path) -> bytes:
    """Read one regular file through a no-follow dirfd path walk."""

    path = _reject_symlink_components(Path(path))
    if _path_mentions_sealed_seed(path):
        raise ValueError("refusing to read a sealed-final seed path")
    directory_fd = _open_directory_fd(path.parent, create=False)
    try:
        file_fd = _open_regular_fd_at(directory_fd, path.name)
    finally:
        os.close(directory_fd)
    with os.fdopen(file_fd, "rb") as handle:
        return handle.read()


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _check_mapping(check: GateCheck) -> dict:
    return {
        "name": check.name,
        "measured": check.measured,
        "comparator": check.comparator,
        "threshold": check.threshold,
        "passed": check.passed,
        "evaluated": check.evaluated,
        "reason": check.reason,
        "evidence": dict(check.evidence),
    }


def _verdict_mapping(verdict: GateVerdict) -> dict:
    return {
        "status": verdict.status,
        "passed": verdict.passed,
        "seed_role": verdict.seed_role,
        "reasons": verdict.reasons,
        "checks": tuple(_check_mapping(check) for check in verdict.checks),
        "per_seed": tuple(
            {
                "seed": metric.seed,
                "full_m": metric.full_m,
                "nocollab_m": metric.nocollab_m,
                "shuffle_online_m": metric.shuffle_online_m,
                "full_win": metric.full_win,
                "degradation": metric.degradation,
                "full_waiting_total_s": metric.full_waiting_total_s,
                "full_departed_count": metric.full_departed_count,
                "nocollab_waiting_total_s": metric.nocollab_waiting_total_s,
                "nocollab_departed_count": metric.nocollab_departed_count,
                "shuffle_waiting_total_s": metric.shuffle_waiting_total_s,
                "shuffle_departed_count": metric.shuffle_departed_count,
            }
            for metric in verdict.per_seed
        ),
        "pooled": {
            "full": {
                "waiting_sum": verdict.full_waiting_sum,
                "departed_sum": verdict.full_departed_sum,
                "m": verdict.full_pooled_m,
            },
            "nocollab": {
                "waiting_sum": verdict.nocollab_waiting_sum,
                "departed_sum": verdict.nocollab_departed_sum,
                "m": verdict.nocollab_pooled_m,
            },
            "shuffle": {
                "waiting_sum": verdict.shuffle_waiting_sum,
                "departed_sum": verdict.shuffle_departed_sum,
                "m": verdict.shuffle_pooled_m,
            },
        },
    }


def _calibration_mapping(calibration: Calibration | None) -> dict | None:
    if calibration is None:
        return None
    return {
        "lambda_star": calibration.lambda_star,
        "quantile_method": calibration.quantile_method,
        "margin_quantile_levels": _MARGIN_QUANTILES,
        "margin_quantiles": calibration.margin_quantiles,
        "margin_row_count": calibration.margin_row_count,
        "eligible_row_count": calibration.eligible_row_count,
        "c_star": calibration.c_star,
        "scaled_span_source": calibration.scaled_span_source,
        "source_seeds": calibration.source_seeds,
        "probe_hashes": tuple(
            {"seed": seed, "sha256": digest}
            for seed, digest in calibration.probe_hashes
        ),
        "config_hash": calibration.config_hash,
        "bands": tuple(
            {
                "name": band.name,
                "tau1": band.tau1,
                "tau2": band.tau2,
                "c_star": band.c_star,
            }
            for band in calibration.bands
        ),
    }


def _s1_training_mapping(audit: S1TrainingAudit | None) -> dict | None:
    if audit is None:
        return None
    return {
        "status": audit.status,
        "subtype": audit.subtype,
        "completed_batches": audit.completed_batches,
        "completed_episodes": audit.completed_episodes,
        "policy_generation": audit.policy_generation,
        "first_traffic_seed": audit.first_traffic_seed,
        "last_traffic_seed": audit.last_traffic_seed,
        "workers": audit.workers,
        "candidate_kind": audit.candidate_kind,
        "candidate_completed_episodes": audit.candidate_completed_episodes,
        "recovery_kind": audit.recovery_kind,
        "implementation_failures": dict(audit.implementation_failures),
    }


def _s1_adjudication_mapping(
    adjudication: S1Adjudication | None,
) -> dict | None:
    if adjudication is None:
        return None
    return {
        "status": adjudication.status,
        "subtype": adjudication.subtype,
        "seed_role": adjudication.seed_role,
        "mechanism": _verdict_mapping(adjudication.mechanism),
        "implementation_failures": dict(adjudication.implementation_failures),
        "checks": tuple(_check_mapping(check) for check in adjudication.checks),
        "per_seed": tuple(
            {
                "seed": item.seed,
                "s1_m": item.s1_m,
                "s0_wrapper_m": item.s0_wrapper_m,
                "senior_m": item.senior_m,
                "s1_win": item.s1_win,
                "s1_waiting_total_s": item.s1_waiting_total_s,
                "s1_departed_count": item.s1_departed_count,
                "s0_wrapper_waiting_total_s": item.s0_wrapper_waiting_total_s,
                "s0_wrapper_departed_count": item.s0_wrapper_departed_count,
                "senior_waiting_total_s": item.senior_waiting_total_s,
                "senior_departed_count": item.senior_departed_count,
            }
            for item in adjudication.per_seed
        ),
        "pooled": {
            "s1": {
                "waiting_sum": adjudication.s1_waiting_sum,
                "departed_sum": adjudication.s1_departed_sum,
                "m": adjudication.s1_pooled_m,
            },
            "s0_wrapper": {
                "waiting_sum": adjudication.s0_wrapper_waiting_sum,
                "departed_sum": adjudication.s0_wrapper_departed_sum,
                "m": adjudication.s0_wrapper_pooled_m,
            },
            "senior_report_only": {
                "waiting_sum": adjudication.senior_waiting_sum,
                "departed_sum": adjudication.senior_departed_sum,
                "m": adjudication.senior_pooled_m,
            },
            "s1_s0_ratio": adjudication.pooled_ratio,
            "paired_wins": adjudication.paired_wins,
        },
    }


def s1_adjudication_to_mapping(adjudication: S1Adjudication) -> dict:
    """Return the canonical public JSON mapping for one typed S1 decision."""

    if type(adjudication) is not S1Adjudication:
        raise TypeError("adjudication must be an exact S1Adjudication")
    S1Adjudication.__post_init__(adjudication)
    result = _s1_adjudication_mapping(adjudication)
    assert result is not None
    return result


def _artifact_mappings(result: StageResult) -> tuple[dict, ...]:
    return tuple(
        {"role": artifact.role, "path": artifact.path, "sha256": artifact.sha256}
        for artifact in result.input_artifacts
    )


def _stage_payloads(result: StageResult) -> dict[str, Any]:
    artifacts = _artifact_mappings(result)
    input_hashes = {
        f"{artifact['role']}_hash": artifact["sha256"] for artifact in artifacts
    }
    manifest = {
        "schema": "vrc_v8_stage_manifest_v1",
        "stage": result.stage,
        "status": result.status,
        "input_artifacts": artifacts,
        **_jsonable(result.manifest),
    }
    config = _jsonable(result.config)
    report = {
        "schema": "vrc_v8_stage_report_v1",
        "stage": result.stage,
        "verdict": result.status,
        "selection": _jsonable(result.selection),
        "input_artifacts": artifacts,
        "input_hashes": input_hashes,
        "verdicts": tuple(
            {"candidate": label, "verdict": _verdict_mapping(verdict)}
            for label, verdict in result.verdicts
        ),
        "calibration": _calibration_mapping(result.calibration),
        "s1_training_audit": _s1_training_mapping(result.s1_training_audit),
        "s1_adjudication": _s1_adjudication_mapping(result.s1_adjudication),
        "evidence": _jsonable(result.evidence),
    }
    hashes = {
        **input_hashes,
        "manifest_hash": canonical_json_hash(manifest),
        "report_hash": canonical_json_hash(report),
        "config_hash": canonical_json_hash(config),
    }
    return {
        "manifest": manifest,
        "report": report,
        "config": config,
        "hashes": hashes,
    }


def _require_exact_mapping(
    value: Any, keys: set[str], name: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} schema is incomplete or unknown")
    return value


def _gate_check_from_mapping(value: Any) -> GateCheck:
    value = _require_exact_mapping(
        value,
        {
            "name",
            "measured",
            "comparator",
            "threshold",
            "passed",
            "evaluated",
            "reason",
            "evidence",
        },
        "gate check",
    )
    evidence = value["evidence"]
    if not isinstance(evidence, Mapping):
        raise ValueError("gate check evidence must be a mapping")
    threshold = value["threshold"]
    if isinstance(threshold, list):
        threshold = tuple(threshold)
    return GateCheck(
        name=value["name"],
        measured=value["measured"],
        comparator=value["comparator"],
        threshold=threshold,
        passed=value["passed"],
        evidence=tuple(evidence.items()),
        evaluated=value["evaluated"],
        reason=value["reason"],
    )


def _seed_metric_from_mapping(value: Any) -> SeedMetric:
    keys = {
        "seed",
        "full_m",
        "nocollab_m",
        "shuffle_online_m",
        "full_win",
        "degradation",
        "full_waiting_total_s",
        "full_departed_count",
        "nocollab_waiting_total_s",
        "nocollab_departed_count",
        "shuffle_waiting_total_s",
        "shuffle_departed_count",
    }
    value = _require_exact_mapping(value, keys, "seed metric")
    return SeedMetric(**{key: value[key] for key in keys})


def _pooled_line(value: Any, name: str) -> tuple[Any, Any, Any]:
    value = _require_exact_mapping(
        value, {"waiting_sum", "departed_sum", "m"}, name
    )
    return value["waiting_sum"], value["departed_sum"], value["m"]


def gate_verdict_from_mapping(value: Any) -> GateVerdict:
    """Rebuild one public verdict mapping through all typed invariants."""

    value = _require_exact_mapping(
        value,
        {
            "status",
            "passed",
            "seed_role",
            "reasons",
            "checks",
            "per_seed",
            "pooled",
        },
        "gate verdict",
    )
    pooled = _require_exact_mapping(
        value["pooled"], {"full", "nocollab", "shuffle"}, "gate pooled"
    )
    full_waiting, full_departed, full_m = _pooled_line(
        pooled["full"], "Full pooled"
    )
    nocollab_waiting, nocollab_departed, nocollab_m = _pooled_line(
        pooled["nocollab"], "NoCollab pooled"
    )
    shuffle_waiting, shuffle_departed, shuffle_m = _pooled_line(
        pooled["shuffle"], "Shuffle pooled"
    )
    checks = value["checks"]
    per_seed = value["per_seed"]
    reasons = value["reasons"]
    if not isinstance(checks, (list, tuple)) or not isinstance(
        per_seed, (list, tuple)
    ) or not isinstance(reasons, (list, tuple)):
        raise ValueError("gate verdict sequences are malformed")
    return GateVerdict(
        status=value["status"],
        passed=value["passed"],
        seed_role=value["seed_role"],
        checks=tuple(_gate_check_from_mapping(item) for item in checks),
        per_seed=tuple(_seed_metric_from_mapping(item) for item in per_seed),
        reasons=tuple(reasons),
        full_pooled_m=full_m,
        nocollab_pooled_m=nocollab_m,
        shuffle_pooled_m=shuffle_m,
        full_waiting_sum=full_waiting,
        full_departed_sum=full_departed,
        nocollab_waiting_sum=nocollab_waiting,
        nocollab_departed_sum=nocollab_departed,
        shuffle_waiting_sum=shuffle_waiting,
        shuffle_departed_sum=shuffle_departed,
    )


def _calibration_from_mapping(value: Any) -> Calibration | None:
    if value is None:
        return None
    value = _require_exact_mapping(
        value,
        {
            "lambda_star",
            "quantile_method",
            "margin_quantile_levels",
            "margin_quantiles",
            "margin_row_count",
            "eligible_row_count",
            "c_star",
            "scaled_span_source",
            "source_seeds",
            "probe_hashes",
            "config_hash",
            "bands",
        },
        "calibration",
    )
    if tuple(value["margin_quantile_levels"]) != _MARGIN_QUANTILES:
        raise ValueError("calibration quantile levels are not frozen")
    bands = []
    for item in value["bands"]:
        item = _require_exact_mapping(
            item, {"name", "tau1", "tau2", "c_star"}, "calibration band"
        )
        bands.append(BandCalibration(**dict(item)))
    probe_hashes = []
    for item in value["probe_hashes"]:
        item = _require_exact_mapping(
            item, {"seed", "sha256"}, "calibration probe hash"
        )
        probe_hashes.append((item["seed"], item["sha256"]))
    return Calibration(
        lambda_star=value["lambda_star"],
        quantile_method=value["quantile_method"],
        margin_quantiles=tuple(value["margin_quantiles"]),
        margin_row_count=value["margin_row_count"],
        eligible_row_count=value["eligible_row_count"],
        c_star=value["c_star"],
        scaled_span_source=value["scaled_span_source"],
        bands=tuple(bands),
        source_seeds=tuple(value["source_seeds"]),
        probe_hashes=tuple(probe_hashes),
        config_hash=value["config_hash"],
    )


def _s1_training_from_mapping(value: Any) -> S1TrainingAudit | None:
    if value is None:
        return None
    keys = {
        "status",
        "subtype",
        "completed_batches",
        "completed_episodes",
        "policy_generation",
        "first_traffic_seed",
        "last_traffic_seed",
        "workers",
        "candidate_kind",
        "candidate_completed_episodes",
        "recovery_kind",
        "implementation_failures",
    }
    value = _require_exact_mapping(value, keys, "S1 training audit")
    return S1TrainingAudit(**{key: value[key] for key in keys})


def _s1_comparison_from_mapping(value: Any) -> S1SeedComparison:
    keys = {
        "seed",
        "s1_m",
        "s0_wrapper_m",
        "senior_m",
        "s1_win",
        "s1_waiting_total_s",
        "s1_departed_count",
        "s0_wrapper_waiting_total_s",
        "s0_wrapper_departed_count",
        "senior_waiting_total_s",
        "senior_departed_count",
    }
    value = _require_exact_mapping(value, keys, "S1 seed comparison")
    return S1SeedComparison(**{key: value[key] for key in keys})


def _s1_adjudication_from_mapping(value: Any) -> S1Adjudication | None:
    if value is None:
        return None
    value = _require_exact_mapping(
        value,
        {
            "status",
            "subtype",
            "seed_role",
            "mechanism",
            "implementation_failures",
            "checks",
            "per_seed",
            "pooled",
        },
        "S1 adjudication",
    )
    pooled = _require_exact_mapping(
        value["pooled"],
        {
            "s1",
            "s0_wrapper",
            "senior_report_only",
            "s1_s0_ratio",
            "paired_wins",
        },
        "S1 pooled",
    )
    s1_waiting, s1_departed, s1_m = _pooled_line(pooled["s1"], "S1 pooled")
    s0_waiting, s0_departed, s0_m = _pooled_line(
        pooled["s0_wrapper"], "S0 wrapper pooled"
    )
    senior_waiting, senior_departed, senior_m = _pooled_line(
        pooled["senior_report_only"], "senior pooled"
    )
    adjudication = S1Adjudication(
        status=value["status"],
        subtype=value["subtype"],
        seed_role=value["seed_role"],
        mechanism=gate_verdict_from_mapping(value["mechanism"]),
        implementation_failures=value["implementation_failures"],
        per_seed=tuple(
            _s1_comparison_from_mapping(item) for item in value["per_seed"]
        ),
        checks=tuple(_gate_check_from_mapping(item) for item in value["checks"]),
        s1_pooled_m=s1_m,
        s0_wrapper_pooled_m=s0_m,
        senior_pooled_m=senior_m,
        s1_waiting_sum=s1_waiting,
        s1_departed_sum=s1_departed,
        s0_wrapper_waiting_sum=s0_waiting,
        s0_wrapper_departed_sum=s0_departed,
        senior_waiting_sum=senior_waiting,
        senior_departed_sum=senior_departed,
    )
    if (
        pooled["s1_s0_ratio"] != adjudication.pooled_ratio
        or pooled["paired_wins"] != adjudication.paired_wins
    ):
        raise ValueError("S1 derived pooled values disagree with typed evidence")
    return adjudication


def stage_result_from_payloads(
    manifest: Any, report: Any, config: Any
) -> StageResult:
    """Accept only stage JSON that the canonical typed writer can produce."""

    if not isinstance(manifest, Mapping) or not isinstance(config, Mapping):
        raise ValueError("typed stage manifest/config must be mappings")
    report = _require_exact_mapping(
        report,
        {
            "schema",
            "stage",
            "verdict",
            "selection",
            "input_artifacts",
            "input_hashes",
            "verdicts",
            "calibration",
            "s1_training_audit",
            "s1_adjudication",
            "evidence",
        },
        "typed stage report",
    )
    if report["schema"] != "vrc_v8_stage_report_v1":
        raise ValueError("typed stage report schema is invalid")
    required_manifest = {"schema", "stage", "status", "input_artifacts"}
    if not required_manifest.issubset(manifest) or manifest.get(
        "schema"
    ) != "vrc_v8_stage_manifest_v1":
        raise ValueError("typed stage manifest schema is invalid")
    if (
        manifest["stage"] != report["stage"]
        or manifest["status"] != report["verdict"]
        or manifest["input_artifacts"] != report["input_artifacts"]
    ):
        raise ValueError("typed stage report and manifest disagree")
    artifacts = []
    for item in report["input_artifacts"]:
        item = _require_exact_mapping(
            item, {"role", "path", "sha256"}, "stage input artifact"
        )
        artifacts.append(ArtifactDigest(**dict(item)))
    expected_input_hashes = {
        f"{artifact.role}_hash": artifact.sha256 for artifact in artifacts
    }
    if report["input_hashes"] != expected_input_hashes:
        raise ValueError("typed stage input hashes disagree")
    verdicts = []
    for item in report["verdicts"]:
        item = _require_exact_mapping(
            item, {"candidate", "verdict"}, "stage verdict entry"
        )
        verdicts.append(
            (item["candidate"], gate_verdict_from_mapping(item["verdict"]))
        )
    result = StageResult(
        stage=report["stage"],
        status=report["verdict"],
        verdicts=tuple(verdicts),
        selection=report["selection"],
        calibration=_calibration_from_mapping(report["calibration"]),
        manifest={
            key: value for key, value in manifest.items() if key not in required_manifest
        },
        config=config,
        input_artifacts=tuple(artifacts),
        evidence=report["evidence"],
        s1_training_audit=_s1_training_from_mapping(
            report["s1_training_audit"]
        ),
        s1_adjudication=_s1_adjudication_from_mapping(
            report["s1_adjudication"]
        ),
    )
    canonical = _stage_payloads(result)
    if (
        canonical_json_hash(canonical["manifest"]) != canonical_json_hash(manifest)
        or canonical_json_hash(canonical["report"]) != canonical_json_hash(report)
        or canonical_json_hash(canonical["config"]) != canonical_json_hash(config)
    ):
        raise ValueError("typed stage payload is not canonical")
    return result


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _render_report_markdown(report: Mapping[str, Any]) -> bytes:
    report_json = _pretty_json_bytes(report).decode("utf-8")
    return (
        f"# {report['stage']}\n\n"
        f"Verdict: {report['verdict']}\n\n"
        "The JSON below is the authoritative report rendered verbatim.\n\n"
        f"```json\n{report_json}```\n"
    ).encode("utf-8")


def _read_regular_file_at(directory_fd: int, name: str) -> bytes | None:
    try:
        file_fd = _open_regular_fd_at(directory_fd, name)
    except ValueError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise
    with os.fdopen(file_fd, "rb") as handle:
        return handle.read()


def _atomic_write_new(directory_fd: int, name: str, payload: bytes) -> None:
    temporary_name = f".{name}.{os.getpid()}.{os.urandom(8).hex()}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        file_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        raise ValueError(f"cannot create stage temporary file for {name}") from exc
    try:
        with os.fdopen(file_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except OSError as exc:
        raise ValueError(f"cannot atomically publish stage artifact {name}") from exc
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _assert_directory_identity(path: Path, directory_fd: int) -> None:
    try:
        path_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("stage directory changed during publication") from exc
    fd_stat = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(path_stat.st_mode)
        or (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino)
    ):
        raise ValueError("stage directory changed during publication")


def _revalidate_stage_result(result: StageResult) -> None:
    if type(result) is not StageResult:
        raise TypeError("result must be an exact StageResult")
    StageResult.__post_init__(result)
    for _, verdict in result.verdicts:
        for check in verdict.checks:
            GateCheck.__post_init__(check)
        for metric in verdict.per_seed:
            SeedMetric.__post_init__(metric)
        GateVerdict.__post_init__(verdict)
    if result.calibration is not None:
        for band in result.calibration.bands:
            BandCalibration.__post_init__(band)
        Calibration.__post_init__(result.calibration)
    if result.s1_training_audit is not None:
        S1TrainingAudit.__post_init__(result.s1_training_audit)
    if result.s1_adjudication is not None:
        adjudication = result.s1_adjudication
        for check in adjudication.mechanism.checks:
            GateCheck.__post_init__(check)
        for metric in adjudication.mechanism.per_seed:
            SeedMetric.__post_init__(metric)
        GateVerdict.__post_init__(adjudication.mechanism)
        for comparison in adjudication.per_seed:
            S1SeedComparison.__post_init__(comparison)
        for check in adjudication.checks:
            GateCheck.__post_init__(check)
        S1Adjudication.__post_init__(adjudication)
    for artifact in result.input_artifacts:
        ArtifactDigest.__post_init__(artifact)
    StageResult.__post_init__(result)


def write_stage_artifacts(stage_dir: Path, result: StageResult) -> None:
    """Verify inputs, then publish the five deterministic stage artifacts."""

    _revalidate_stage_result(result)
    payload_values = _stage_payloads(result)
    payloads = {
        "manifest.json": _pretty_json_bytes(payload_values["manifest"]),
        "report.json": _pretty_json_bytes(payload_values["report"]),
        "report.md": _render_report_markdown(payload_values["report"]),
        "config.json": _pretty_json_bytes(payload_values["config"]),
        "hashes.json": _pretty_json_bytes(payload_values["hashes"]),
    }
    stage_dir = _reject_symlink_components(Path(stage_dir))
    if _path_mentions_sealed_seed(stage_dir):
        raise ValueError("refusing to write a sealed-final seed path")
    for artifact in payload_values["report"]["input_artifacts"]:
        if hash_file(Path(artifact["path"])) != artifact["sha256"]:
            raise ValueError(f"input artifact hash mismatch: {artifact['role']}")
    directory_fd = _open_directory_fd(stage_dir, create=True)
    try:
        existing_payloads = {}
        for name, payload in payloads.items():
            _reject_symlink_components(stage_dir / name)
            existing = _read_regular_file_at(directory_fd, name)
            if existing is not None and existing != payload:
                raise ValueError(f"existing stage artifact differs: {name}")
            existing_payloads[name] = existing
        _assert_directory_identity(stage_dir, directory_fd)
        for name, payload in payloads.items():
            if existing_payloads[name] is None:
                _atomic_write_new(directory_fd, name, payload)
        _assert_directory_identity(stage_dir, directory_fd)
    finally:
        os.close(directory_fd)
