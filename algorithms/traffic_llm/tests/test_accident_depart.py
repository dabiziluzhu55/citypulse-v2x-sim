"""Accident depart-lane validation must reject bicycle-only / missing lanes."""

from __future__ import annotations

import pytest

from simulation.sumo.engine.events import (
    AccidentEvent,
    DisturbanceScheduler,
    EventValidationError,
    LaneTarget,
    lane_allows_vehicle_class,
)


BICYCLE_ONLY = "-57582_0"
PASSENGER_OK = "-57582_1"


class _LaneAPI:
    def __init__(self, table: dict[str, dict[str, tuple[str, ...]]]) -> None:
        self.table = table

    def getAllowed(self, lane_id: str) -> tuple[str, ...]:
        return self.table[lane_id]["allow"]

    def getDisallowed(self, lane_id: str) -> tuple[str, ...]:
        return self.table[lane_id]["disallow"]


class _FakeTraci:
    def __init__(self, table: dict[str, dict[str, tuple[str, ...]]]) -> None:
        self.lane = _LaneAPI(table)


def test_lane_allows_vehicle_class_matches_sumo_semantics() -> None:
    assert lane_allows_vehicle_class(("bicycle",), (), "passenger") is False
    assert lane_allows_vehicle_class(("passenger",), (), "passenger") is True
    assert lane_allows_vehicle_class(("all",), (), "passenger") is True
    assert lane_allows_vehicle_class((), ("bicycle",), "passenger") is True
    assert lane_allows_vehicle_class((), (), "passenger") is True
    assert lane_allows_vehicle_class((), ("passenger",), "passenger") is False
    assert lane_allows_vehicle_class((), ("all",), "passenger") is False


def test_scheduler_rejects_bicycle_only_accident_before_activate() -> None:
    traci = _FakeTraci(
        {
            BICYCLE_ONLY: {"allow": ("bicycle",), "disallow": ()},
            PASSENGER_OK: {"allow": ("passenger", "taxi"), "disallow": ()},
        }
    )
    scheduler = DisturbanceScheduler(
        traci,
        {
            BICYCLE_ONLY: LaneTarget(
                lane_id=BICYCLE_ONLY, edge_id="-57582", lane_index=0, length=40.0
            ),
            PASSENGER_OK: LaneTarget(
                lane_id=PASSENGER_OK, edge_id="-57582", lane_index=1, length=40.0
            ),
        },
        300.0,
    )
    with pytest.raises(EventValidationError, match="does not allow passenger"):
        scheduler.schedule(
            AccidentEvent(
                event_id="fake-accident",
                start_seconds=120.0,
                end_seconds=180.0,
                lane_id=BICYCLE_ONLY,
                position_ratio=0.5,
            )
        )
    assert scheduler.schedule(
        AccidentEvent(
            event_id="ok-accident",
            start_seconds=120.0,
            end_seconds=180.0,
            lane_id=PASSENGER_OK,
            position_ratio=0.5,
        )
    ) == "ok-accident"


def test_scheduler_rejects_short_accident_lane() -> None:
    traci = _FakeTraci({PASSENGER_OK: {"allow": ("passenger",), "disallow": ()}})
    scheduler = DisturbanceScheduler(
        traci,
        {
            PASSENGER_OK: LaneTarget(
                lane_id=PASSENGER_OK, edge_id="-57582", lane_index=1, length=3.0
            )
        },
        300.0,
    )
    with pytest.raises(EventValidationError, match="too short"):
        scheduler.schedule(
            AccidentEvent(
                event_id="short",
                start_seconds=120.0,
                end_seconds=180.0,
                lane_id=PASSENGER_OK,
                position_ratio=0.5,
            )
        )


def test_known_bicycle_lane_is_unsafe_for_accident() -> None:
    from algorithms.traffic_llm.dataset.catalog import load_runtime_catalog
    from algorithms.traffic_llm.dataset.scenario_generator import (
        accident_lane_is_safe,
        load_closure_safety_index,
        load_lane_permission_index,
    )

    catalog = load_runtime_catalog()
    safety = load_closure_safety_index()
    permissions = load_lane_permission_index()
    ok, reason = accident_lane_is_safe(
        BICYCLE_ONLY,
        catalog=catalog,
        intersection_id="demo_3",
        permissions=permissions,
        safety=safety,
    )
    assert ok is False
    assert "passenger" in reason or "allow" in reason
    sibling_ok, sibling_reason = accident_lane_is_safe(
        PASSENGER_OK,
        catalog=catalog,
        intersection_id="demo_3",
        permissions=permissions,
        safety=safety,
    )
    assert sibling_ok is True, sibling_reason


def test_generated_accidents_never_use_bicycle_only_lanes() -> None:
    from algorithms.traffic_llm.dataset.catalog import load_runtime_catalog
    from algorithms.traffic_llm.dataset.scenario_generator import (
        generate_scenarios,
        load_lane_permission_index,
        lane_allows_vehicle_class,
    )
    from simulation.sumo.engine.events import ACCIDENT_VEHICLE_CLASS

    catalog = load_runtime_catalog()
    permissions = load_lane_permission_index()
    bicycle_only = {
        lane_id
        for lane_id, meta in permissions.items()
        if meta.get("allow") == ("bicycle",)
    }
    assert BICYCLE_ONLY in bicycle_only
    config = {
        "dataset_version": "accident_safety_test",
        "simulation": {
            "duration_seconds": 300,
            "step_length": 0.1,
            "decision_interval": 5.0,
            "snapshot_interval_seconds": 1.0,
            "required_post_event_horizon_s": 90,
        },
        "grid": {
            "periods": ["morning_peak", "evening_peak"],
            "scopes": ["east_dense", "west_dense", "xiongan_20"],
            "seeds": [42001, 42002, 42003, 43001],
            "event_types": ["accident"],
            "event_start_seconds": [120],
            "event_duration_seconds": [60],
            "severity": {"accident": {"position_ratio": [0.5, 0.8]}},
        },
    }
    scenarios = generate_scenarios(config, catalog)
    assert scenarios
    for spec in scenarios:
        assert spec.event.event_type == "accident"
        lane_id = str(spec.event.lane_id)
        assert lane_id not in bicycle_only
        meta = permissions[lane_id]
        assert lane_allows_vehicle_class(
            tuple(meta.get("allow") or ()),
            tuple(meta.get("disallow") or ()),
            ACCIDENT_VEHICLE_CLASS,
        )
        assert float(meta.get("length_m") or 0.0) >= 6.0


def test_val_seeds_force_val_and_analysis_seeds_are_excluded() -> None:
    from algorithms.traffic_llm.dataset.split import assign_splits, split_manifest
    from algorithms.traffic_llm.tests.test_dataset import _scenario

    specs = [
        _scenario(scenario_id=f"s{seed}", seed=seed, scenario_group_id=f"g{seed}")
        for seed in (43001, 43004, 42003)
    ]
    assignment = assign_splits(
        specs,
        {
            "seed": 20260909,
            "train": 1.0,
            "val": 0.0,
            "test": 0.0,
            "val_seeds": [43004],
            "analysis_seeds": [42003],
            "holdout_seeds": [],
        },
    )
    assert assignment["g43004"] == "val"
    assert assignment["g43001"] == "train"
    assert assignment["g42003"] == "analysis"
    counts = split_manifest(specs, assignment, {"val_seeds": [43004], "analysis_seeds": [42003]})
    assert counts["n_scenarios"]["val"] == 1
    assert counts["n_scenarios"]["analysis"] == 1
    assert counts["n_scenarios"]["train"] == 1
