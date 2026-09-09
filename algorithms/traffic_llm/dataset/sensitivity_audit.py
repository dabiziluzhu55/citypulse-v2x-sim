"""Offline xiongan expert-selection sensitivity. Does not re-run SUMO or retune weights."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from .io_utils import dump_json, load_json, load_yaml, read_jsonl
from .scorer import DEFAULT_UNRELIABLE_TRIP_METRICS
from .teacher_selector import select_expert


VARIANT_A = "A_scoring_v2"
VARIANT_B = "B_local_recovery"
VARIANT_C = "C_no_tripinfo"


def scoring_variant_local_recovery(base: Mapping[str, Any]) -> dict[str, Any]:
    """Emphasize local event-window + recovery. Not used for production selection."""

    cfg = copy.deepcopy(dict(base))
    groups = dict(cfg.get("groups") or {})
    if "disturbance_response" in groups:
        groups["disturbance_response"]["weight"] = 0.55
    if "recovery" in groups:
        groups["recovery"]["weight"] = 0.30
    if "congestion_safety" in groups:
        groups["congestion_safety"]["weight"] = 0.15
    for name in ("efficiency", "green", "completeness", "realtime"):
        if name in groups:
            groups[name]["weight"] = 0.0
    cfg["groups"] = groups
    cfg["sensitivity_variant"] = VARIANT_B
    return cfg


def scoring_variant_drop_tripinfo(base: Mapping[str, Any]) -> dict[str, Any]:
    """Force-ignore TripInfo metrics, including completion_rate, for every candidate."""

    cfg = copy.deepcopy(dict(base))
    rel = dict(cfg.get("trip_metric_reliability") or {})
    rel["enabled"] = True
    rel["min_completion_rate"] = 1.01
    metrics = list(rel.get("unreliable_metrics") or DEFAULT_UNRELIABLE_TRIP_METRICS)
    if "completion_rate" not in metrics:
        metrics.append("completion_rate")
    rel["unreliable_metrics"] = metrics
    cfg["trip_metric_reliability"] = rel
    cfg["sensitivity_variant"] = VARIANT_C
    return cfg


def _decision(selection: Mapping[str, Any]) -> dict[str, Any]:
    winner = selection.get("winner")
    if winner is None:
        return {"state": "rejected", "winner": None}
    if selection.get("ambiguous"):
        return {"state": "ambiguous", "winner": str(winner)}
    return {"state": "selected", "winner": str(winner)}


def _pair_stats(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(left)
    winner_match = 0
    decision_match = 0
    selected_ambiguous_flips = 0
    winner_changes: Counter[str] = Counter()
    flips: list[dict[str, Any]] = []
    for a, b in zip(left, right):
        same_winner = a["winner"] == b["winner"]
        same_state = a["state"] == b["state"]
        if same_winner:
            winner_match += 1
        else:
            winner_changes[f"{a['winner']}->{b['winner']}"] += 1
        if same_winner and same_state:
            decision_match += 1
        states = {a["state"], b["state"]}
        if states == {"selected", "ambiguous"}:
            selected_ambiguous_flips += 1
        if not (same_winner and same_state):
            flips.append(
                {
                    "scenario_id": a.get("scenario_id"),
                    "left": a,
                    "right": b,
                }
            )
    return {
        "n": n,
        "winner_agreement": (winner_match / n) if n else 0.0,
        "decision_agreement": (decision_match / n) if n else 0.0,
        "selected_ambiguous_flips": selected_ambiguous_flips,
        "winner_changes": dict(winner_changes),
        "n_flips": len(flips),
        "flips": flips,
    }


def _winner_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(item["winner"]) for item in rows if item.get("state") == "selected"))


def _load_xiongan_groups(
    output_dir: Path,
    *,
    scope: str,
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    scenarios = list(read_jsonl(output_dir / "scenarios.jsonl"))
    wanted = {
        str(item["scenario_id"])
        for item in scenarios
        if str(item.get("scope") or "") == scope
    }
    runs_dir = output_dir / "runs"
    by_scenario: dict[str, list[dict[str, Any]]] = {sid: [] for sid in wanted}
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*.json")):
            run = load_json(path)
            sid = str(run.get("scenario_id") or "")
            if sid in by_scenario:
                by_scenario[sid].append(run)
    missing = [sid for sid, rows in by_scenario.items() if not rows]
    if missing:
        raise RuntimeError(f"{scope} missing run files for {len(missing)} scenarios; first={missing[:3]}")
    return sorted(wanted), by_scenario


def audit_xiongan_sensitivity(
    output_dir: Path,
    scoring: Mapping[str, Any],
    *,
    scope: str = "xiongan_20",
    min_agreement: float = 0.70,
) -> dict[str, Any]:
    scenario_ids, by_scenario = _load_xiongan_groups(output_dir, scope=scope)
    variants = {
        VARIANT_A: copy.deepcopy(dict(scoring)),
        VARIANT_B: scoring_variant_local_recovery(scoring),
        VARIANT_C: scoring_variant_drop_tripinfo(scoring),
    }
    decisions: dict[str, list[dict[str, Any]]] = {name: [] for name in variants}
    details: list[dict[str, Any]] = []
    for scenario_id in scenario_ids:
        row: dict[str, Any] = {"scenario_id": scenario_id}
        for name, cfg in variants.items():
            selection = select_expert(by_scenario[scenario_id], cfg)
            decision = {"scenario_id": scenario_id, **_decision(selection)}
            decisions[name].append(decision)
            row[name] = {
                **decision,
                "score_margin": selection.get("score_margin"),
                "candidate_scores": selection.get("candidate_scores"),
            }
        details.append(row)

    pairs = {
        "A_vs_B": _pair_stats(decisions[VARIANT_A], decisions[VARIANT_B]),
        "A_vs_C": _pair_stats(decisions[VARIANT_A], decisions[VARIANT_C]),
        "B_vs_C": _pair_stats(decisions[VARIANT_B], decisions[VARIANT_C]),
    }
    three_way = 0
    for a, b, c in zip(decisions[VARIANT_A], decisions[VARIANT_B], decisions[VARIANT_C]):
        if a["winner"] == b["winner"] == c["winner"]:
            three_way += 1
    n = len(scenario_ids)
    winner_agreements = {key: value["winner_agreement"] for key, value in pairs.items()}
    min_pair = min(winner_agreements.values()) if winner_agreements else 0.0
    stop = bool(n == 0 or min_pair < float(min_agreement))
    payload = {
        "scope": scope,
        "n_scenarios": n,
        "min_agreement_threshold": float(min_agreement),
        "variants": {
            VARIANT_A: "current scoring_v2 (TripInfo gated on low completion_rate)",
            VARIANT_B: "local_event_window + recovery dominate; TripInfo group weights zeroed",
            VARIANT_C: "all TripInfo metrics including completion_rate ignored",
        },
        "winner_counts": {name: _winner_counts(rows) for name, rows in decisions.items()},
        "state_counts": {
            name: dict(Counter(item["state"] for item in rows)) for name, rows in decisions.items()
        },
        "pairwise": {
            key: {k: v for k, v in value.items() if k != "flips"} | {"n_flip_examples": len(value["flips"])}
            for key, value in pairs.items()
        },
        "pairwise_flips": {key: value["flips"][:20] for key, value in pairs.items()},
        "winner_agreement": winner_agreements,
        "three_way_winner_agreement": (three_way / n) if n else 0.0,
        "min_pairwise_winner_agreement": min_pair,
        "stop_training": stop,
        "stop_reason": (
            f"min pairwise winner agreement {min_pair:.3f} < {min_agreement:.2f}"
            if stop
            else None
        ),
        "details": details,
        "note": "Sensitivity only. Production expert selection remains scoring_v2. Weights were not retuned.",
    }
    reports = output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    dump_json(reports / "xiongan_sensitivity.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="algorithms.traffic_llm.dataset.sensitivity_audit")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--scoring", default="algorithms/traffic_llm/configs/scoring_v2.yaml")
    parser.add_argument("--scope", default="xiongan_20")
    parser.add_argument("--min-agreement", type=float, default=0.70)
    args = parser.parse_args(argv)
    scoring = load_yaml(Path(args.scoring))
    report = audit_xiongan_sensitivity(
        Path(args.dataset),
        scoring,
        scope=args.scope,
        min_agreement=args.min_agreement,
    )
    keep = (
        "scope",
        "n_scenarios",
        "winner_agreement",
        "three_way_winner_agreement",
        "min_pairwise_winner_agreement",
        "winner_counts",
        "state_counts",
        "pairwise",
        "stop_training",
        "stop_reason",
    )
    print(json.dumps({k: report[k] for k in keep}, ensure_ascii=False, indent=2))
    return 2 if report.get("stop_training") else 0


if __name__ == "__main__":
    raise SystemExit(main())
