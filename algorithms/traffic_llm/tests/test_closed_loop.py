from __future__ import annotations

import json

from algorithms.traffic_llm.dataset.io_utils import dump_json, write_jsonl
from algorithms.traffic_llm.dataset.scenario_generator import event_spec_to_disturbance
from algorithms.traffic_llm.dataset.schema import EventSpec
from algorithms.traffic_llm.evaluation.closed_loop import replan_fire_times, replan_times
from algorithms.traffic_llm.evaluation.compare import (
    compare_closed_loop,
    outcome,
    relative_improvement,
    scenario_verdict,
)
from algorithms.traffic_llm.evaluation.policy import evaluate_generation
from algorithms.traffic_llm.evaluation.report import decide_next_step


def test_replan_times_covers_active_window_only():
    assert replan_times(120.0, 180.0, 30.0) == [120.0, 150.0]
    assert replan_times(120.0, 150.0, 30.0) == [120.0]
    assert replan_times(0.0, 90.0, 30.0) == [0.0, 30.0, 60.0]
    assert replan_fire_times(120.0, 180.0, 30.0, lead_seconds=1.0) == [120.0, 149.0]
    assert replan_fire_times(120.0, 150.0, 30.0, lead_seconds=1.0) == [120.0]


def test_event_spec_enables_ai_control():
    event = EventSpec(
        event_type="accident",
        start_seconds=120.0,
        end_seconds=180.0,
        intersection_id="demo_5",
        lane_id="E1_0",
        position_ratio=0.5,
    )
    off = event_spec_to_disturbance(event, "e1")
    on = event_spec_to_disturbance(event, "e2", ai_control_enabled=True)
    assert off.ai_control_enabled is False
    assert on.ai_control_enabled is True


def _legal_plan() -> str:
    return json.dumps(
        {
            "controlled_intersections": ["demo_5"],
            "valid_seconds": 30,
            "signal_plan": {"demo_5": [1, 1, 2, 2, 1, 1]},
            "objective": "drain local queue",
            "reason": "event lane is queued",
            "fallback_to_baseline": False,
        },
        ensure_ascii=False,
    )


def test_invalid_plan_falls_back():
    allowed = {"demo_5": [1, 2, 3]}
    region = ["demo_5"]
    illegal = evaluate_generation(
        json.dumps(
            {
                "controlled_intersections": ["demo_5"],
                "valid_seconds": 30,
                "signal_plan": {"demo_5": [9, 9, 9, 9, 9, 9]},
                "objective": "bad phase",
                "reason": "illegal",
                "fallback_to_baseline": False,
            }
        ),
        latency_ms=12.0,
        allowed_phases=allowed,
        allowed_region=region,
    )
    assert illegal.json_ok is True
    assert illegal.schema_ok is True
    assert illegal.phase_ok is False
    assert illegal.invalid_plan is True
    assert illegal.fallback is True
    assert illegal.parsed is None

    timeout = evaluate_generation(
        _legal_plan(),
        latency_ms=90_000.0,
        allowed_phases=allowed,
        allowed_region=region,
        timeout_s=60.0,
    )
    assert timeout.invalid_plan is True
    assert timeout.fallback is True
    assert "timeout" in (timeout.error or "")

    garbage = evaluate_generation(
        "not json",
        latency_ms=8.0,
        allowed_phases=allowed,
        allowed_region=region,
    )
    assert garbage.json_ok is False
    assert garbage.fallback is True
    assert garbage.parsed is None

    legal = evaluate_generation(
        _legal_plan(),
        latency_ms=20.0,
        allowed_phases=allowed,
        allowed_region=region,
    )
    assert legal.invalid_plan is False
    assert legal.fallback is False
    assert legal.parsed is not None


def test_win_tie_loss_relative_improvement():
    assert relative_improvement(8.0, 10.0, "minimize") == 20.0
    assert relative_improvement(12.0, 10.0, "maximize") == 20.0
    assert outcome(2.0) == "win"
    assert outcome(-2.0) == "loss"
    assert outcome(0.5) == "tie"
    assert scenario_verdict([5.0, 4.0, -1.0]) == "win"
    assert scenario_verdict([-5.0, -4.0, 1.0]) == "loss"
    assert scenario_verdict([2.0, -2.0, 0.0]) == "tie"


def _run(local_q: float, recovery: float = 20.0) -> dict:
    return {
        "state": "COMPLETED",
        "traffic_eval": {
            "completion_rate": 0.9,
            "spillback_rate": local_q,
            "regional_max_queue_length_m": local_q * 8,
            "hard_braking_rate": 0.01,
            "traffic_performance_index": local_q,
            "path_avg_speed_kmh": 20.0,
        },
        "local_event_window": {
            "local_avg_queue_veh": local_q,
            "local_max_queue_m": local_q * 8,
            "local_spillback_pct": local_q,
            "local_mean_speed_mps": max(0.5, 10.0 - local_q),
            "local_throughput_delta": -local_q,
        },
        "recovery": {"recovery_time_s": recovery},
        "n_plans": 2,
        "n_fallback": 0,
        "n_invalid_plans": 0,
        "inference_latency_ms": [11.0, 13.0],
        "decisions": [
            {"json_ok": True, "schema_ok": True, "phase_ok": True, "region_ok": True}
        ]
        * 2,
        "fallback_rate": 0.0,
        "invalid_plan_rate": 0.0,
    }


def test_compare_closed_loop_win_tie_loss(tmp_path):
    dataset = tmp_path / "dataset"
    loop = tmp_path / "closed_loop"
    (dataset / "runs").mkdir(parents=True)
    (dataset / "teacher_selection").mkdir(parents=True)
    (loop / "traffic_qwen" / "runs").mkdir(parents=True)
    (loop / "base_qwen" / "runs").mkdir(parents=True)
    sid = "scenario_000001"
    dump_json(dataset / "runs" / f"{sid}_fixed.json", _run(10.0))
    dump_json(dataset / "runs" / f"{sid}_max_pressure.json", _run(4.0))
    dump_json(dataset / "runs" / f"{sid}_sotl.json", _run(6.0))
    dump_json(loop / "traffic_qwen" / "runs" / f"{sid}_traffic_qwen.json", _run(5.0))
    dump_json(loop / "base_qwen" / "runs" / f"{sid}_base_qwen.json", _run(9.0))
    write_jsonl(
        dataset / "teacher_selection" / "selected_experts.jsonl",
        [{"scenario_id": sid, "winner": "sotl", "ambiguous": False}],
    )
    scoring = {
        "trip_metric_reliability": {
            "enabled": True,
            "min_completion_rate": 0.30,
            "unreliable_metrics": ["traffic_performance_index", "path_avg_speed_kmh"],
        }
    }
    scenarios = [
        {
            "scenario_id": sid,
            "period": "off_peak",
            "scope": "east_dense",
            "event": {"event_type": "accident"},
            "seed": 42003,
        }
    ]
    report = compare_closed_loop(
        dataset_dir=dataset,
        closed_loop_root=loop,
        scoring=scoring,
        scenarios=scenarios,
    )
    assert report["n_llm_completed"] == 1
    assert report["overall"]["base_qwen"]["win_tie_loss"]["win"] == 1
    assert report["overall"]["fixed"]["win_tie_loss"]["win"] == 1
    assert report["overall"]["max_pressure"]["win_tie_loss"]["loss"] == 1
    assert report["overall"]["selected_expert"]["win_tie_loss"]["win"] == 1
    assert report["by_event"]["accident"]["fixed"]["n"] == 1
    assert report["by_scope"]["east_dense"]["base_qwen"]["n"] == 1
    decision = decide_next_step(report)
    assert decision["improves_vs_base"] is True
    assert decision["improves_vs_fixed"] is True
    assert decision["competitive_vs_max_pressure"] is False
    assert decision["recommend_awq_vllm_backend"] is False
