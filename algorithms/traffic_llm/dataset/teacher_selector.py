"""Auditable expert selection: hard filters, Pareto, composite, margin, fallback."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .schema import ACTION_SPACE_SIGNAL_ONLY, ACTION_SPACE_SIGNAL_VEHICLE
from .scorer import pareto_ranks, score_candidates


HARD_FAIL_REASONS = (
    "simulation_failed",
    "algorithm_init_failed",
    "no_valid_action",
    "illegal_phase",
    "control_range_error",
    "missing_metrics",
    "episode_too_short",
)


def hard_validate(
    candidate: Mapping[str, Any],
    scoring: Mapping[str, Any],
    *,
    require_signal_only: bool = False,
) -> tuple[bool, str | None]:
    state = str(candidate.get("state") or "")
    if state == "FAILED" or candidate.get("error"):
        init_error = str(candidate.get("error") or "")
        if "not initialized" in init_error.lower() or "checkpoint" in init_error.lower():
            return False, "algorithm_init_failed"
        return False, "simulation_failed"
    if candidate.get("init_ok") is False:
        return False, "algorithm_init_failed"
    duration = float(candidate.get("elapsed_seconds") or 0.0)
    min_len = float(scoring.get("min_episode_seconds", 0.0))
    if duration + 1e-9 < min_len:
        return False, "episode_too_short"
    if candidate.get("illegal_phase"):
        return False, "illegal_phase"
    if candidate.get("control_range_error"):
        return False, "control_range_error"
    if not candidate.get("has_valid_action", True):
        return False, "no_valid_action"
    traffic_eval = dict(candidate.get("traffic_eval") or {})
    if not traffic_eval:
        return False, "missing_metrics"
    action_space = str(candidate.get("teacher_action_space") or ACTION_SPACE_SIGNAL_ONLY)
    if require_signal_only and action_space != ACTION_SPACE_SIGNAL_ONLY:
        return False, "teacher uses vehicle-level actions"
    if require_signal_only and candidate.get("has_vehicle_actions"):
        return False, "teacher uses vehicle-level actions"
    return True, None


def select_expert(
    candidates: Sequence[Mapping[str, Any]],
    scoring: Mapping[str, Any],
    *,
    baseline_mode: str | None = None,
) -> dict[str, Any]:
    baseline_mode = baseline_mode or str(scoring.get("baseline_mode") or "fixed")
    min_improve = float(scoring.get("minimum_improvement_over_baseline", 0.03))
    ambiguous_margin = float(scoring.get("ambiguous_margin", 0.02))
    selection_version = str(scoring.get("selection_version") or "scoring_v1")

    rejected: list[dict[str, Any]] = []
    valid: list[Mapping[str, Any]] = []
    for candidate in candidates:
        ok, reason = hard_validate(candidate, scoring, require_signal_only=False)
        if not ok:
            rejected.append(
                {
                    "control_mode": candidate.get("control_mode"),
                    "reason": reason,
                    "error": candidate.get("error"),
                }
            )
            continue
        valid.append(candidate)

    empty = {
        "selection_version": selection_version,
        "winner": None,
        "winner_score": None,
        "second_score": None,
        "score_margin": None,
        "expert_confidence": 0.0,
        "ambiguous": False,
        "fallback_to_baseline": True,
        "pareto_rank": {},
        "candidate_scores": {},
        "raw_metrics": {},
        "normalized_metrics": {},
        "rejected": rejected,
        "reason": "no valid candidates",
    }
    if not valid:
        return empty

    scored = score_candidates(valid, scoring)
    ranks = pareto_ranks(scored)
    by_mode = {
        item["control_mode"]: {**item, "pareto_rank": ranks[idx]}
        for idx, item in enumerate(scored)
    }
    ordered = sorted(
        scored,
        key=lambda item: (
            by_mode[item["control_mode"]]["pareto_rank"],
            -(item["composite_score"] if item["composite_score"] is not None else -1.0),
        ),
    )
    winner = ordered[0]
    second = ordered[1] if len(ordered) > 1 else None
    winner_score = float(winner["composite_score"] or 0.0)
    second_score = float(second["composite_score"] or 0.0) if second else None
    margin = None if second_score is None else winner_score - second_score
    ambiguous = bool(margin is not None and margin < ambiguous_margin)

    baseline_score = None
    pairwise_gain = None
    if baseline_mode in {item.get("control_mode") for item in valid}:
        pair = [item for item in valid if item.get("control_mode") in {winner["control_mode"], baseline_mode}]
        if len(pair) >= 1:
            pair_scored = {
                item["control_mode"]: item
                for item in score_candidates(pair, scoring)
            }
            if baseline_mode in pair_scored and pair_scored[baseline_mode]["composite_score"] is not None:
                baseline_score = float(pair_scored[baseline_mode]["composite_score"])
            if winner["control_mode"] in pair_scored and pair_scored[winner["control_mode"]]["composite_score"] is not None:
                pairwise_gain = float(pair_scored[winner["control_mode"]]["composite_score"]) - (
                    baseline_score if baseline_score is not None else 0.0
                )
    fallback = False
    if winner["control_mode"] == baseline_mode:
        fallback = True
    elif pairwise_gain is not None:
        fallback = pairwise_gain < min_improve

    return {
        "selection_version": selection_version,
        "winner": winner["control_mode"],
        "winner_score": winner_score,
        "second": None if second is None else second["control_mode"],
        "second_score": second_score,
        "score_margin": margin,
        "expert_confidence": 0.0 if margin is None else max(0.0, float(margin)),
        "ambiguous": ambiguous,
        "fallback_to_baseline": fallback,
        "baseline_mode": baseline_mode,
        "baseline_score": baseline_score,
        "pairwise_gain_over_baseline": pairwise_gain,
        "minimum_improvement_over_baseline": min_improve,
        "ambiguous_margin": ambiguous_margin,
        "pareto_rank": {
            item["control_mode"]: by_mode[item["control_mode"]]["pareto_rank"]
            for item in scored
        },
        "candidate_scores": {
            item["control_mode"]: item["composite_score"] for item in scored
        },
        "group_scores": {
            item["control_mode"]: item["group_scores"] for item in scored
        },
        "raw_metrics": {item["control_mode"]: item["raw_metrics"] for item in scored},
        "normalized_metrics": {
            item["control_mode"]: item["normalized_metrics"] for item in scored
        },
        "winner_action_space": next(
            (
                str(item.get("teacher_action_space") or ACTION_SPACE_SIGNAL_ONLY)
                for item in valid
                if item.get("control_mode") == winner["control_mode"]
            ),
            ACTION_SPACE_SIGNAL_ONLY,
        ),
        "signal_sft_eligible": next(
            (
                str(item.get("teacher_action_space") or ACTION_SPACE_SIGNAL_ONLY)
                == ACTION_SPACE_SIGNAL_ONLY
                and not item.get("has_vehicle_actions")
                for item in valid
                if item.get("control_mode") == winner["control_mode"]
            ),
            False,
        ),
        "rejected": rejected,
        "reason": "ambiguous" if ambiguous else ("fallback_to_baseline" if fallback else "selected"),
    }


def signal_sft_reason(selection: Mapping[str, Any]) -> str | None:
    if selection.get("winner") is None:
        return str(selection.get("reason") or "no expert")
    if selection.get("ambiguous"):
        return "ambiguous expert margin"
    if selection.get("winner_action_space") == ACTION_SPACE_SIGNAL_VEHICLE:
        return "teacher uses vehicle-level actions"
    if not selection.get("signal_sft_eligible", False):
        return "teacher uses vehicle-level actions"
    return None
