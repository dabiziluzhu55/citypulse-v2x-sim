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
            "completion_rate": 0.80,
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
    observation = {
        "intersections": {
            "demo_5": {
                "lanes": {
                    "E1_0": {
                        "role": "incoming",
                        "halting_count": 6,
                        "mean_speed": 1.2,
                        "occupancy": 0.6,
                        "queue_length_m": 40.0,
                        "lane_length_m": 45.0,
                    }
                }
            },
            "demo_3": {
                "lanes": {
                    "E2_0": {"role": "incoming", "halting_count": 4, "mean_speed": 2.0}
                }
            },
        }
    }
    objective, reason = factual_reason(
        spec=spec,
        observation=observation,
        fallback=False,
    )
    assert "排队" in reason or "溢流" in reason or "速度" in reason
    assert "恢复" not in reason
    assert "composite" not in reason
    assert "思维" not in reason
    assert objective


SCORING_V2 = {
    "selection_version": "scoring_v2",
    "baseline_mode": "fixed",
    "min_composite_score_margin_over_baseline": 0.03,
    "ambiguous_margin": 0.02,
    "min_episode_seconds": 10,
    "baseline_gain_mode": "unified_composite",
    "ambiguity_compare": "best_pareto_front",
    "relative_range_epsilon": 0.0,
    "trip_metric_reliability": {
        "enabled": True,
        "min_completion_rate": 0.30,
        "unreliable_metrics": ["traffic_performance_index", "path_avg_speed_kmh"],
    },
    "groups": {
        "efficiency": {
            "weight": 0.5,
            "metrics": [
                {
                    "id": "traffic_performance_index",
                    "direction": "minimize",
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


def test_scoring_v2_tpi_is_minimize() -> None:
    scored = score_candidates(
        [
            _cand("fixed", 8.0, 10.0),
            _cand("max_pressure", 4.0, 10.0),
        ],
        {**SCORING_V2, "relative_range_epsilon": 0.0},
    )
    by_mode = {item["control_mode"]: item for item in scored}
    assert by_mode["max_pressure"]["normalized_metrics"]["traffic_eval.traffic_performance_index"] == 1.0
    assert by_mode["fixed"]["normalized_metrics"]["traffic_eval.traffic_performance_index"] == 0.0


def test_scoring_v2_uses_unified_composite_not_pairwise_renorm() -> None:
    selection = select_expert(
        [
            _cand("fixed", 5.0, 5.0),
            _cand("max_pressure", 4.6, 4.6),
            _cand("sotl", 9.0, 9.0),
        ],
        {**SCORING_V2, "min_composite_score_margin_over_baseline": 0.03},
    )
    unified = selection["candidate_scores"]["max_pressure"] - selection["candidate_scores"]["fixed"]
    assert selection["composite_score_margin_over_baseline"] == pytest.approx(unified)
    assert selection["pairwise_gain_over_baseline"] is None
    assert abs(selection["composite_score_margin_over_baseline"]) < 0.99


def test_ambiguity_only_compares_best_pareto_front() -> None:
    selection = select_expert(
        [
            _cand("max_pressure", 4.80, 4.80),
            _cand("sotl", 5.00, 5.00),
            _cand("fixed", 9.0, 20.0),
        ],
        {**SCORING_V2, "ambiguous_margin": 0.08},
    )
    assert selection["winner"] == "max_pressure"
    assert selection["pareto_rank"]["max_pressure"] == 1
    assert selection["pareto_rank"]["sotl"] > 1
    assert selection["ambiguous"] is False
    all_compare = select_expert(
        [
            _cand("max_pressure", 4.80, 4.80),
            _cand("sotl", 5.00, 5.00),
            _cand("fixed", 9.0, 20.0),
        ],
        {**SCORING_V2, "ambiguous_margin": 0.08, "ambiguity_compare": "all_candidates"},
    )
    assert all_compare["ambiguous"] is True


def test_tripinfo_reliability_gate_skips_metrics_not_episode() -> None:
    gated = _cand("max_pressure", 1.0, 20.0)
    gated["traffic_eval"]["completion_rate"] = 0.10
    gated["traffic_eval"]["path_avg_speed_kmh"] = 80.0
    ok = _cand("fixed", 8.0, 2.0)
    ok["traffic_eval"]["completion_rate"] = 0.80
    scored = score_candidates([gated, ok], SCORING_V2)
    by_mode = {item["control_mode"]: item for item in scored}
    assert by_mode["max_pressure"]["trip_reliability_gated"] is True
    assert "traffic_performance_index" in by_mode["max_pressure"]["ignored_trip_metrics"]
    assert by_mode["max_pressure"]["raw_metrics"]["traffic_eval.traffic_performance_index"] is None
    assert by_mode["fixed"]["raw_metrics"]["traffic_eval.traffic_performance_index"] == 8.0
    selection = select_expert([gated, ok], SCORING_V2)
    assert selection["winner"] is not None
    assert "max_pressure" in selection["ignored_trip_metrics"]


def test_post_event_horizon_rejects_no_recovery_window() -> None:
    from algorithms.traffic_llm.dataset.catalog import load_runtime_catalog
    from algorithms.traffic_llm.dataset.scenario_generator import generate_scenarios

    catalog = load_runtime_catalog()
    config = {
        "dataset_version": "horizon_test",
        "simulation": {
            "duration_seconds": 300,
            "step_length": 0.1,
            "decision_interval": 5.0,
            "snapshot_interval_seconds": 1.0,
            "required_post_event_horizon_s": 90,
        },
        "grid": {
            "periods": ["off_peak"],
            "scopes": ["east_dense"],
            "seeds": [42001],
            "event_types": ["accident"],
            "event_start_seconds": [210, 120],
            "event_duration_seconds": [90, 60],
            "severity": {"accident": {"position_ratio": [0.5]}},
        },
    }
    scenarios = generate_scenarios(config, catalog)
    windows = {
        (item.event.start_seconds, item.event.end_seconds - item.event.start_seconds)
        for item in scenarios
    }
    assert (210.0, 90.0) not in windows
    assert (210.0, 60.0) not in windows
    assert (120.0, 60.0) in windows
    assert (120.0, 90.0) in windows


def test_resume_skips_completed_and_failed_unless_retry() -> None:
    from algorithms.traffic_llm.dataset.io_utils import dump_json
    from algorithms.traffic_llm.dataset.pipeline import classify_resume_jobs

    tmp = Path("/tmp/traffic_llm_resume_test")
    runs = tmp / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    dump_json(runs / "scenario_000001_fixed.json", {"state": "COMPLETED"})
    dump_json(runs / "scenario_000001_sotl.json", {"state": "FAILED"})
    jobs = [
        {"spec": {"scenario_id": "scenario_000001"}, "control_mode": "fixed"},
        {"spec": {"scenario_id": "scenario_000001"}, "control_mode": "sotl"},
        {"spec": {"scenario_id": "scenario_000001"}, "control_mode": "max_pressure"},
    ]
    to_run, skipped_ok, skipped_fail = classify_resume_jobs(
        jobs, tmp, resume=True, retry_failed=False
    )
    assert [item["control_mode"] for item in to_run] == ["max_pressure"]
    assert skipped_ok == ["scenario_000001_fixed"]
    assert skipped_fail == ["scenario_000001_sotl"]
    to_retry, _, skipped_fail2 = classify_resume_jobs(
        jobs, tmp, resume=True, retry_failed=True
    )
    assert {item["control_mode"] for item in to_retry} == {"sotl", "max_pressure"}
    assert skipped_fail2 == []


def test_local_scope_uses_all_intersections_for_small_preset() -> None:
    from algorithms.traffic_llm.dataset.event_window import resolve_local_intersection_ids

    small = resolve_local_intersection_ids(
        ["demo_3", "demo_5", "demo_6", "demo_9"],
        "demo_5",
        {"demo_5": ("demo_3",)},
        hops=1,
        small_preset_max_intersections=6,
    )
    assert small == ("demo_3", "demo_5", "demo_6", "demo_9")
    large_ids = [f"n{i}" for i in range(20)]
    neighbors = {f"n{i}": (f"n{i-1}", f"n{i+1}") for i in range(1, 19)}
    neighbors["n0"] = ("n1",)
    neighbors["n19"] = ("n18",)
    local = resolve_local_intersection_ids(
        large_ids,
        "n10",
        neighbors,
        hops=1,
        small_preset_max_intersections=6,
    )
    assert set(local) == {"n9", "n10", "n11"}


def test_pilot_v2_plan_is_45_by_270() -> None:
    from algorithms.traffic_llm.dataset.io_utils import load_yaml
    from algorithms.traffic_llm.dataset.pipeline import plan_job

    config = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "pilot_v2.yaml")
    result = plan_job(config)
    assert result["n_scenarios"] == 45
    assert result["estimated_episodes"] == 270
    assert result["period_counts"] == {
        "morning_peak": 15,
        "off_peak": 15,
        "evening_peak": 15,
    }
    assert result["scope_counts"] == {
        "east_dense": 15,
        "west_dense": 15,
        "xiongan_20": 15,
    }
    assert result["event_type_counts"] == {
        "lane_closure": 9,
        "speed_limit": 9,
        "accident": 9,
        "major_event_opening": 9,
        "major_event_closing": 9,
    }


def test_lane_closure_rejects_unique_edge_lane() -> None:
    from algorithms.traffic_llm.dataset.catalog import load_runtime_catalog
    from algorithms.traffic_llm.dataset.scenario_generator import (
        lane_closure_is_safe,
        load_closure_safety_index,
    )

    catalog = load_runtime_catalog()
    safety = load_closure_safety_index()
    known_unroutable = [
        ("demo_5", ["-57586_2"]),
        ("demo_9", ["-56619_3"]),
        ("demo_19", ["-52215_1"]),
        ("demo_1", ["-manual_demo1_missing_arm_1"]),
        ("demo_9", ["-50339_3"]),
        ("demo_14", ["-46539_0"]),
        ("demo_10", ["-57445_0"]),
    ]
    for intersection_id, lane_ids in known_unroutable:
        ok, reason = lane_closure_is_safe(
            lane_ids,
            catalog=catalog,
            intersection_id=intersection_id,
            safety=safety,
        )
        assert ok is False, (intersection_id, lane_ids, reason)
        assert any(
            token in reason
            for token in (
                "only",
                "disconnect",
                "block",
                "missing_arm",
                "interior",
                "fewer than two",
                "synthetic",
            )
        )
    ok, reason = lane_closure_is_safe(
        ["-52215_1"],
        catalog=catalog,
        intersection_id="demo_19",
        safety=safety,
    )
    assert ok is False
    assert any(
        token in reason
        for token in ("only", "disconnect", "block", "missing_arm", "interior", "synthetic")
    )


def test_scoring_v2_records_active_and_ignored_metrics() -> None:
    gated = _cand("max_pressure", 1.0, 20.0)
    gated["traffic_eval"]["completion_rate"] = 0.10
    ok = _cand("fixed", 8.0, 2.0)
    ok["traffic_eval"]["completion_rate"] = 0.80
    selection = select_expert([gated, ok], SCORING_V2)
    assert selection["score_source"] == "unified_all_candidates"
    assert selection["pairwise_gain_over_baseline"] is None
    assert "max_pressure" in selection["ignored_trip_metrics"]
    assert selection["ignored_trip_metrics"]["max_pressure"]
    assert "active_metrics" in selection
    assert "participating_metrics" in selection


def test_phase_service_covers_all_allowed_phases_with_real_connections() -> None:
    from algorithms.traffic_llm.dataset.catalog import tls_phase_orders
    from algorithms.traffic_llm.dataset.phase_service import (
        load_phase_service_index,
        validate_phase_service,
    )

    allowed = tls_phase_orders()
    index = load_phase_service_index()
    errors = validate_phase_service(index, allowed_phases=allowed)
    assert errors == []
    empty = []
    for iid, phases in allowed.items():
        for phase in phases:
            tokens = list((index.get(iid) or {}).get(str(phase)) or ())
            if not tokens:
                empty.append(f"{iid}:{phase}")
    assert not empty, empty[:20]


def test_observation_v2_is_compact_and_has_phase_service() -> None:
    from algorithms.traffic_llm.dataset.feature_builder import build_observation_v2
    from algorithms.traffic_llm.dataset.schema import OBSERVATION_VERSION_V2

    spec = _scenario()
    snapshot = {
        "intersections": {
            "demo_5": {
                "current_phase": 1,
                "pending_phase": 2,
                "stage": "green",
                "lanes": {
                    "E1_0": {
                        "role": "incoming",
                        "vehicle_count": 8,
                        "halting_count": 5,
                        "mean_speed": 2.3,
                        "occupancy": 0.61,
                        "queue_length_m": 31.5,
                        "waiting_time": 12.0,
                        "lane_length_m": 80.0,
                    },
                    "E9_0": {"role": "outgoing", "vehicle_count": 3, "halting_count": 0},
                },
            },
            "demo_3": {"current_phase": 2, "lanes": {}},
            "demo_6": {"current_phase": 1, "lanes": {}},
            "demo_9": {"current_phase": 3, "lanes": {}},
        },
        "metrics": {"active_vehicles": 40, "halting_vehicles": 10, "mean_speed": 5.2},
    }
    obs = build_observation_v2(
        spec=spec,
        simulation_time=30.0,
        snapshot_summary=snapshot,
        allowed_phases={"demo_5": (1, 2, 3), "demo_3": (1, 2), "demo_6": (1,), "demo_9": (1, 2, 3)},
        neighbors={"demo_5": ("demo_3",)},
        scope_hops=1,
    )
    assert obs["observation_version"] == OBSERVATION_VERSION_V2
    lanes = obs["ix"]["demo_5"]["lanes"]
    assert lanes[0]["id"] == "E1_0"
    assert "veh" in lanes[0]
    assert all("lane_length_m" not in lane for lane in lanes)
    assert all(lane["id"] != "E9_0" for lane in lanes)
    assert "pending_phase" not in obs["ix"]["demo_5"]
    assert "1" in obs["phase_service"]["demo_5"]
    assert set(obs["phase_service"]["demo_5"]) == {"1", "2", "3"}


def test_factual_reason_reads_v2_short_keys() -> None:
    spec = _scenario()
    observation = {
        "ix": {
            "demo_5": {
                "lanes": [{"id": "E1_0", "halt": 6, "speed": 1.2, "occ": 0.6, "queue_m": 40.0}]
            },
            "demo_3": {"lanes": [{"id": "E2_0", "halt": 4, "speed": 2.0}]},
        }
    }
    objective, reason = factual_reason(spec=spec, observation=observation, fallback=False)
    assert "排队" in reason or "溢流" in reason or "速度" in reason
    assert objective


def test_prompt_completion_keeps_assistant_only() -> None:
    from algorithms.traffic_llm.dataset.sft_builder import SYSTEM_PROMPT, messages_to_prompt_completion

    sample = {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "{\"instruction\":\"x\"}"},
            {"role": "assistant", "content": "{\"fallback_to_baseline\":true}"},
        ],
        "metadata": {"split": "train"},
    }
    converted = messages_to_prompt_completion(sample)
    assert [item["role"] for item in converted["prompt"]] == ["system", "user"]
    assert converted["completion"][0]["role"] == "assistant"


def test_assistant_truncation_fails_closed() -> None:
    from algorithms.traffic_llm.dataset.token_budget import assistant_truncated

    intact = {"prompt_tokens": 2000, "completion_tokens": 200, "total_tokens": 2200, "has_assistant_target": True}
    cut = {"prompt_tokens": 4000, "completion_tokens": 200, "total_tokens": 4200, "has_assistant_target": True}
    missing = {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100, "has_assistant_target": False}
    assert assistant_truncated(intact, 4096) is False
    assert assistant_truncated(cut, 4096) is True
    assert assistant_truncated(missing, 4096) is True


def test_formal_v1_plan_is_135_by_810() -> None:
    from algorithms.traffic_llm.dataset.io_utils import load_yaml
    from algorithms.traffic_llm.dataset.pipeline import plan_job

    config = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "formal_v1.yaml")
    result = plan_job(config)
    assert result["n_scenarios"] == 135
    assert result["estimated_episodes"] == 810
    assert result["event_type_counts"] == {
        "lane_closure": 27,
        "speed_limit": 27,
        "accident": 27,
        "major_event_opening": 27,
        "major_event_closing": 27,
    }


def test_holdout_seed_42003_is_test_only() -> None:
    from algorithms.traffic_llm.dataset.split import assign_splits

    specs = [
        _scenario(scenario_id=f"s{seed}", seed=seed, scenario_group_id=f"g{seed}")
        for seed in (42001, 42002, 42003)
    ]
    assignment = assign_splits(
        specs,
        {"seed": 20260909, "train": 0.8, "val": 0.2, "test": 0.0, "holdout_seeds": [42003]},
    )
    assert assignment["g42003"] == "test"
    assert assignment["g42001"] in {"train", "val"}
    assert assignment["g42002"] in {"train", "val"}


def test_tripinfo_gate_override_does_not_block_xiongan() -> None:
    from algorithms.traffic_llm.dataset.io_utils import load_yaml
    from algorithms.traffic_llm.dataset.reporting import expert_diagnostics, tripinfo_gate_audit

    scoring = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "scoring_v2.yaml")
    scenarios = [
        {"scenario_id": "xa1", "scope": "xiongan_20", "event": {"event_type": "accident"}},
        {"scenario_id": "xa2", "scope": "xiongan_20", "event": {"event_type": "lane_closure"}},
    ]
    selected = [
        {
            "scenario_id": "xa1",
            "winner": "max_pressure",
            "ambiguous": False,
            "signal_sft_eligible": True,
            "winner_action_space": "signal_only",
            "trip_reliability_gated_modes": ["max_pressure", "fixed"],
        },
        {
            "scenario_id": "xa2",
            "winner": "cov2x",
            "ambiguous": False,
            "signal_sft_eligible": True,
            "winner_action_space": "signal_only",
            "trip_reliability_gated_modes": ["cov2x"],
        },
    ]
    diagnostics = expert_diagnostics(
        scenarios=scenarios,
        selections=selected,
        ambiguous=[],
        rejected=[],
        scoring=scoring,
        runs=[
            {"scenario_id": "xa1", "traffic_eval": {"completion_rate": 0.17}},
            {"scenario_id": "xa2", "traffic_eval": {"completion_rate": 0.16}},
        ],
    )
    assert diagnostics["flags"]["scope_tripinfo_gate_ge_50pct"]["xiongan_20"] == 1.0
    assert diagnostics["flags"]["unexpected_tripinfo_gate_ge_50pct"] == {}
    assert diagnostics["block_training"] is False
    assert "xiongan_20" in diagnostics["tripinfo_gate_override"]["acknowledged_scopes"]
    audit = tripinfo_gate_audit(diagnostics)
    assert audit["xiongan_20"]["tripinfo_metrics_excluded_from_scoring"] is True
    assert audit["xiongan_20"]["override"] is True


def test_east_west_unexpected_gate_still_blocks() -> None:
    from algorithms.traffic_llm.dataset.io_utils import load_yaml
    from algorithms.traffic_llm.dataset.reporting import expert_diagnostics

    scoring = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "scoring_v2.yaml")
    scenarios = [{"scenario_id": "e1", "scope": "east_dense", "event": {"event_type": "accident"}}]
    selected = [
        {
            "scenario_id": "e1",
            "winner": "max_pressure",
            "ambiguous": False,
            "signal_sft_eligible": True,
            "winner_action_space": "signal_only",
            "trip_reliability_gated_modes": ["max_pressure"],
        }
    ]
    diagnostics = expert_diagnostics(
        scenarios=scenarios,
        selections=selected,
        ambiguous=[],
        rejected=[],
        scoring=scoring,
        runs=[{"scenario_id": "e1", "traffic_eval": {"completion_rate": 0.10}}],
    )
    assert "east_dense" in diagnostics["flags"]["unexpected_tripinfo_gate_ge_50pct"]
    assert diagnostics["block_training"] is True


def test_sensitivity_variants_do_not_retune_production_weights() -> None:
    from algorithms.traffic_llm.dataset.io_utils import load_yaml
    from algorithms.traffic_llm.dataset.sensitivity_audit import (
        scoring_variant_drop_tripinfo,
        scoring_variant_local_recovery,
    )

    scoring = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "scoring_v2.yaml")
    original_dr = scoring["groups"]["disturbance_response"]["weight"]
    local = scoring_variant_local_recovery(scoring)
    dropped = scoring_variant_drop_tripinfo(scoring)
    assert scoring["groups"]["disturbance_response"]["weight"] == original_dr
    assert local["groups"]["disturbance_response"]["weight"] > original_dr
    assert local["groups"]["efficiency"]["weight"] == 0.0
    assert dropped["trip_metric_reliability"]["min_completion_rate"] > 1.0
    assert "completion_rate" in dropped["trip_metric_reliability"]["unreliable_metrics"]
    assert scoring["trip_metric_reliability"]["min_completion_rate"] == 0.30


def test_xiongan_sensitivity_audit_on_synthetic_runs(tmp_path: Path) -> None:
    from algorithms.traffic_llm.dataset.io_utils import dump_json, load_yaml, write_jsonl
    from algorithms.traffic_llm.dataset.sensitivity_audit import audit_xiongan_sensitivity

    scoring = load_yaml(Path(__file__).resolve().parents[1] / "configs" / "scoring_v2.yaml")
    scenarios = [
        {
            "scenario_id": "xa_s1",
            "scope": "xiongan_20",
            "period": "off_peak",
            "event": {"event_type": "accident"},
        },
        {
            "scenario_id": "xa_s2",
            "scope": "xiongan_20",
            "period": "off_peak",
            "event": {"event_type": "lane_closure"},
        },
    ]
    write_jsonl(tmp_path / "scenarios.jsonl", scenarios)
    (tmp_path / "runs").mkdir()

    def _run(scenario_id: str, mode: str, local_q: float, tpi: float) -> dict:
        return {
            "run_id": f"{scenario_id}__{mode}",
            "scenario_id": scenario_id,
            "control_mode": mode,
            "state": "COMPLETED",
            "init_ok": True,
            "elapsed_seconds": 300.0,
            "has_valid_action": True,
            "teacher_action_space": ACTION_SPACE_SIGNAL_ONLY,
            "traffic_eval": {
                "traffic_performance_index": tpi,
                "spillback_rate": 2.0,
                "regional_max_queue_length_m": 20.0,
                "hard_braking_rate": 0.01,
                "throughput_veh_per_h": 400.0,
                "path_avg_speed_kmh": 20.0,
                "fuel_intensity_L_per_100km": 8.0,
                "completion_rate": 0.17,
                "avg_decision_latency_ms": 5.0,
            },
            "local_event_window": {
                "local_avg_queue_veh": local_q,
                "local_max_queue_m": local_q * 8,
                "local_spillback_pct": local_q,
                "local_mean_speed_mps": max(0.5, 10.0 - local_q),
                "local_throughput_delta": -local_q,
                "local_waiting_time_delta": local_q,
                "local_hard_braking_delta": 0.0,
            },
            "recovery": {"recovery_time_s": local_q * 10.0, "post_event_avg_queue": local_q},
        }

    for sid in ("xa_s1", "xa_s2"):
        for mode, local_q, tpi in (
            ("fixed", 12.0, 8.0),
            ("max_pressure", 3.0, 9.5),
            ("cov2x", 11.0, 2.0),
        ):
            payload = _run(sid, mode, local_q, tpi)
            dump_json(tmp_path / "runs" / f"{payload['run_id']}.json", payload)

    report = audit_xiongan_sensitivity(tmp_path, scoring, scope="xiongan_20", min_agreement=0.70)
    assert report["n_scenarios"] == 2
    assert report["stop_training"] is False
    assert report["min_pairwise_winner_agreement"] >= 0.70
    assert report["winner_counts"]["A_scoring_v2"].get("max_pressure") == 2
