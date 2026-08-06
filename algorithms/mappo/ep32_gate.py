"""ep32 hard gate and arm selection (spec §4.4)."""

from __future__ import annotations

from typing import Mapping

DEFAULTS = {
    "waiting_delta_vs_m10_mean_max": 0.0,
    "arrival_delta_vs_m10_mean_min": 0.0,
    "waiting_vs_vanilla_anchor_ucb95_max": 0.5,
    "target_duplicate_rate_max": 0.05,
    "win_rate_vs_m10_min": 5 / 8,
    "nan_inf": 0,
}


def evaluate_arm_gate(
    stats: Mapping[str, float],
    thresholds: Mapping[str, float] | None = None,
) -> tuple[bool, list[str]]:
    thr = dict(DEFAULTS)
    if thresholds:
        thr.update(thresholds)
    reasons: list[str] = []
    if (
        stats.get("waiting_delta_vs_m10", 0.0)
        > thr["waiting_delta_vs_m10_mean_max"]
    ):
        reasons.append("waiting not improved vs m1_0")
    if (
        stats.get("arrival_delta_vs_m10", 0.0)
        < thr["arrival_delta_vs_m10_mean_min"]
    ):
        reasons.append("arrival degraded vs m1_0")
    if (
        stats.get("waiting_vs_vanilla_ucb95", 1e9)
        > thr["waiting_vs_vanilla_anchor_ucb95_max"]
    ):
        reasons.append("worse than vanilla anchor beyond UCB95 bound")
    if (
        stats.get("target_duplicate_rate", 1.0)
        > thr["target_duplicate_rate_max"]
    ):
        reasons.append(
            "target_duplicate_rate too high (per-agent target not distinguishable)"
        )
    if stats.get("win_rate_vs_m10", 0.0) < thr["win_rate_vs_m10_min"]:
        reasons.append("win rate below 5/8")
    if stats.get("nan_inf", 0) != 0:
        reasons.append("non-finite training values")
    return (len(reasons) == 0), reasons


def select_arm(
    arm_a: Mapping[str, object], arm_b: Mapping[str, object]
) -> str:
    """spec §4.4: prefer m1_a unless m1_b proves extra value."""

    a_ok = bool(arm_a["ok"])
    b_ok = bool(arm_b["ok"])
    if a_ok and not b_ok:
        return "m1_a"
    if b_ok and not a_ok:
        return "m1_b"
    if not a_ok and not b_ok:
        return "none"
    improvement = float(arm_a["waiting_mean"]) - float(arm_b["waiting_mean"])
    if (
        improvement >= 0.25
        and float(arm_b["head_to_head_win_rate"]) >= 5 / 8
        and float(arm_b["arrival_mean"])
        >= float(arm_a["arrival_mean"]) - 1e-9
    ):
        return "m1_b"
    return "m1_a"
