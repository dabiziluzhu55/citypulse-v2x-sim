from algorithms.traffic_llm.dataset.schema import EventSpec, ScenarioSpec
from algorithms.traffic_llm.deployment.closed_loop_vllm import EVENTS, PERIODS, SCOPES, select_stratified_15
from algorithms.traffic_llm.deployment.schema import PLAN_JSON_SCHEMA


def _spec(period: str, scope: str, event: str, idx: int) -> ScenarioSpec:
    return ScenarioSpec.from_dict(
        {
            "scenario_id": f"scenario_{idx:06d}",
            "scenario_group_id": f"g{idx}",
            "period": period,
            "scope": scope,
            "scenario_preset_id": scope,
            "scenario_scope": scope,
            "seed": 44001,
            "intersection_ids": ["demo_3"],
            "duration_seconds": 300,
            "step_length": 0.1,
            "decision_interval": 5.0,
            "snapshot_interval_seconds": 1.0,
            "event": EventSpec(
                event_type=event,
                start_seconds=120,
                end_seconds=180,
                intersection_id="demo_3",
            ).to_dict(),
        }
    )


def test_plan_schema_requires_core_fields():
    required = set(PLAN_JSON_SCHEMA["required"])
    assert required == {
        "controlled_intersections",
        "valid_seconds",
        "signal_plan",
        "objective",
        "reason",
        "fallback_to_baseline",
    }


def test_stratified_15_covers_event_scope_period():
    rows = []
    idx = 1
    for period in PERIODS:
        for scope in SCOPES:
            for event in EVENTS:
                rows.append(_spec(period, scope, event, idx))
                idx += 1
    selected = select_stratified_15(rows)
    assert len(selected) == 15
    assert {item.event.event_type for item in selected} == set(EVENTS)
    assert {item.scope for item in selected} == set(SCOPES)
    assert {item.period for item in selected} == set(PERIODS)
