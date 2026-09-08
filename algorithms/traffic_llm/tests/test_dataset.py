from __future__ import annotations

import json
from pathlib import Path

import pytest

from algorithms.traffic_llm.dataset.action_parser import (
    classify_action_space,
    has_material_vehicle_actions,
    parse_target_phases,
    validate_phases_against_allowed,
)
from algorithms.traffic_llm.dataset.schema import (
    ACTION_SPACE_SIGNAL_ONLY,
    ACTION_SPACE_SIGNAL_VEHICLE,
    EventSpec,
    ScenarioSpec,
)
from algorithms.traffic_llm.dataset.scorer import score_candidates
from algorithms.traffic_llm.dataset.split import assign_splits, leak_check
from algorithms.traffic_llm.dataset.sft_builder import SYSTEM_PROMPT, extract_signal_plan, factual_reason
from algorithms.traffic_llm.dataset.teacher_selector import select_expert
from traffic_control.registry import CONTROL_MODE_REGISTRY, list_control_modes


def _event(**kwargs) -> EventSpec:
    payload = {
        "event_type": "accident",
        "start_seconds": 30.0,
        "end_seconds": 50.0,
        "intersection_id": "demo_5",
        "lane_id": "E1_0",
        "position_ratio": 0.5,
        "severity": {"position_ratio": 0.5},
    }
    payload.update(kwargs)
    return EventSpec(**payload)


def _scenario(scenario_id: str = "scenario_000001", seed: int = 1, **kwargs) -> ScenarioSpec:
    payload = {
        "scenario_id": scenario_id,
        "scenario_group_id": f"group-{seed}",
        "period": "off_peak",
        "scope": "east_dense",
        "scenario_preset_id": "east_dense",
        "scenario_scope": "east_dense",
        "seed": seed,
        "intersection_ids": ("demo_3", "demo_5", "demo_6", "demo_9"),
        "duration_seconds": 90.0,
        "step_length": 0.1,
        "decision_interval": 5.0,
        "snapshot_interval_seconds": 1.0,
        "event": _event(),
    }
    payload.update(kwargs)
    return ScenarioSpec(**payload)


SCORING = {
    "selection_version": "scoring_v1",
    "baseline_mode": "fixed",
    "minimum_improvement_over_baseline": 0.03,
    "ambiguous_margin": 0.02,
    "min_episode_seconds": 10,
    "groups": {
        "efficiency": {
            "weight": 0.5,
            "metrics": [
                {
                    "id": "traffic_performance_index",
                    "direction": "maximize",
                    "weight": 1.0,
                    "source": "traffic_eval",
                }
            ],
        },
        "congestion_safety": {
            "weight": 0.5,
            "metrics": [
                {
                    "id": "spillback_rate",
                    "direction": "minimize",
                    "weight": 1.0,
                    "source": "traffic_eval",
                }
            ],
        },
    },
}


def _cand(mode: str, tpi: float | None, spill: float | None, **extra) -> dict:
    payload = {
        "control_mode": mode,
        "state": "COMPLETED",
        "init_ok": True,
        "elapsed_seconds": 90.0,
        "has_valid_action": True,
        "teacher_action_space": ACTION_SPACE_SIGNAL_ONLY,
        "traffic_eval": {
            "traffic_performance_index": tpi,
            "spillback_rate": spill,
        },
        "event_window": {},
        "recovery": {},
    }
    payload.update(extra)
    return payload


def test_scenario_spec_roundtrip() -> None:
    spec = _scenario()
    restored = ScenarioSpec.from_dict(spec.to_dict())
    assert restored == spec
    assert restored.event.event_type == "accident"


def test_illegal_event_unknown_lane_rejected() -> None:
    from algorithms.traffic_llm.dataset.catalog import load_runtime_catalog
    from algorithms.traffic_llm.dataset.scenario_generator import IllegalEventError, _validate_speed_limit

    catalog = load_runtime_catalog()
    intersection_id = "demo_5"
    with pytest.raises(IllegalEventError):
        _validate_speed_limit(catalog, intersection_id, ["does_not_exist_lane"], 5.0)


def test_algorithm_registry_is_source_of_truth() -> None:
    names = list_control_modes()
    assert names == list(CONTROL_MODE_REGISTRY)
    for expected in ("fixed", "max_pressure", "sotl", "ippo", "mappo", "cov2x"):
        assert expected in CONTROL_MODE_REGISTRY


def test_score_maximize_minimize_and_none() -> None:
    scored = score_candidates(
        [
            _cand("a", 8.0, 10.0),
            _cand("b", 4.0, 2.0),
            _cand("c", None, 2.0),
        ],
        {**SCORING, "relative_range_epsilon": 0.0},
    )
    by_mode = {item["control_mode"]: item for item in scored}
    assert by_mode["a"]["normalized_metrics"]["traffic_eval.traffic_performance_index"] == 1.0
    assert by_mode["b"]["normalized_metrics"]["traffic_eval.traffic_performance_index"] == 0.0
    assert by_mode["c"]["normalized_metrics"]["traffic_eval.traffic_performance_index"] is None
    assert by_mode["b"]["normalized_metrics"]["traffic_eval.spillback_rate"] == 1.0
    assert by_mode["a"]["normalized_metrics"]["traffic_eval.spillback_rate"] == 0.0
    assert by_mode["c"]["composite_score"] is not None


def test_pareto_and_fixed_fallback() -> None:
    selection = select_expert(
        [
            _cand("fixed", 5.0, 5.0),
            _cand("max_pressure", 5.1, 4.9),
            _cand("sotl", 4.0, 8.0),
        ],
        SCORING,
    )
    assert selection["winner"] in {"fixed", "max_pressure"}
    assert selection["fallback_to_baseline"] is True
    assert selection["pareto_rank"]["sotl"] >= 1


def test_ambiguous_expert() -> None:
    selection = select_expert(
        [
            _cand("fixed", 5.00, 5.00),
            _cand("max_pressure", 5.01, 4.99),
        ],
        {**SCORING, "ambiguous_margin": 0.2, "minimum_improvement_over_baseline": 0.2},
    )
    assert selection["ambiguous"] is True
    assert selection["winner"] is not None


def test_clear_winner_does_not_fallback() -> None:
    selection = select_expert(
        [
            _cand("fixed", 4.0, 20.0),
            _cand("max_pressure", 8.0, 2.0),
        ],
        {**SCORING, "relative_range_epsilon": 0.0, "minimum_improvement_over_baseline": 0.03},
    )
    assert selection["winner"] == "max_pressure"
    assert selection["fallback_to_baseline"] is False
    assert selection["ambiguous"] is False
    selection = select_expert(
        [
            _cand("fixed", 5.0, 5.0),
            _cand("cov2x", 9.0, 0.1, state="FAILED", error="boom"),
        ],
        SCORING,
    )
    assert selection["winner"] == "fixed"
    assert any(item["reason"] == "simulation_failed" for item in selection["rejected"])


def test_signal_vehicle_not_signal_only() -> None:
    actions = {
        "signals": {"demo_5": {"target_phase": 2}},
        "vehicles": {"veh_1": {"target_speed_mps": 8.3, "target_lane_index": 1}},
    }
    assert classify_action_space(actions) == ACTION_SPACE_SIGNAL_VEHICLE
    assert has_material_vehicle_actions(actions["vehicles"]) is True
    empty = {"signals": {"demo_5": {"target_phase": 1}}, "vehicles": {}}
    assert classify_action_space(empty) == ACTION_SPACE_SIGNAL_ONLY


def test_signal_vehicle_cannot_enter_signal_sft() -> None:
    from algorithms.traffic_llm.dataset.teacher_selector import signal_sft_reason

    selection = {
        "winner": "cov2x",
        "ambiguous": False,
        "winner_action_space": ACTION_SPACE_SIGNAL_VEHICLE,
        "signal_sft_eligible": False,
    }
    assert signal_sft_reason(selection) == "teacher uses vehicle-level actions"


def test_scenario_level_split_no_leak() -> None:
    scenarios = [
        _scenario("scenario_000001", seed=1, scenario_group_id="g1"),
        _scenario("scenario_000002", seed=1, scenario_group_id="g1"),
        _scenario("scenario_000003", seed=2, scenario_group_id="g2"),
        _scenario("scenario_000004", seed=42003, scenario_group_id="g3"),
    ]
    assignment = assign_splits(
        scenarios,
        {"seed": 1, "train": 0.5, "val": 0.25, "test": 0.25, "holdout_seeds": [42003]},
    )
    assert assignment["g3"] == "test"
    assert assignment["g1"] == assignment["g1"]
    samples = [
        {"metadata": {"scenario_group_id": "g1", "split": assignment["g1"]}},
        {"metadata": {"scenario_group_id": "g1", "split": assignment["g1"]}},
        {"metadata": {"scenario_group_id": "g3", "split": "test"}},
    ]
    assert leak_check(samples, assignment) == []
    leaked = [
        {"metadata": {"scenario_group_id": "g1", "split": "train"}},
        {"metadata": {"scenario_group_id": "g1", "split": "test"}},
    ]
    assert leak_check(leaked, assignment)


def test_thirty_second_phase_sequence_and_json_assistant() -> None:
    records = []
    for step in range(8):
        t = 30.0 + step * 5.0
        records.append(
            {
                "simulation_time": t,
                "actions": {
                    "signals": {"demo_5": {"target_phase": 1 if step < 3 else 2}},
                    "vehicles": {},
                },
                "executed_signal_state": {"demo_5": {"current_phase": 1 if step < 3 else 2}},
            }
        )
    plan = extract_signal_plan(
        records,
        anchor_time=30.0,
        slot_seconds=5.0,
        slot_count=6,
        controlled=["demo_5"],
    )
    assert plan is not None
    assert plan["demo_5"] == [1, 1, 1, 2, 2, 2]
    payload = {
        "controlled_intersections": ["demo_5"],
        "valid_seconds": 30.0,
        "signal_plan": plan,
        "objective": "缓解扰动路口排队并抑制上游回溢",
        "reason": "目标进口排队增加",
        "fallback_to_baseline": False,
    }
    parsed = json.loads(json.dumps(payload, ensure_ascii=False))
    from simulation.sumo.engine.ai_control import AIControlPlan

    AIControlPlan.from_mapping(parsed)
    assert SYSTEM_PROMPT


def test_illegal_phase_detected() -> None:
    errors = validate_phases_against_allowed(
        {"demo_5": 99},
        {"demo_5": (1, 2, 3)},
    )
    assert errors
    range_errors = validate_phases_against_allowed(
        {"demo_99": 1},
        {"demo_5": (1, 2)},
    )
    assert range_errors


def test_factual_reason_is_template() -> None:
    spec = _scenario()
    objective, reason = factual_reason(
        spec=spec,
        event_window={"window_avg_queue_veh": 4.0, "window_spillback_pct": 8.0},
        recovery={"recovery_time_s": None, "reason": "did not recover"},
        fallback=False,
    )
    assert "排队" in reason or "溢流" in reason
    assert "思维" not in reason
    assert objective
