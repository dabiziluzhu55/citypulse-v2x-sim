from __future__ import annotations

import json
import stat

import pytest

from algorithms.cov2x import phase_history_audit as audit_module
from algorithms.cov2x.phase_history_audit import (
    AMBIGUOUS_PHASE_TRANSITION,
    LEGAL_PERMISSIVE_GREEN,
    LEGAL_PROTECTED_GREEN,
    RIGHT_ON_RED_SPECIAL,
    TRUE_RED_ENTRY,
    TRUE_RED_YELLOW_ENTRY,
    SUMO_TIMEBASE_CONTRACT,
    UNCONTROLLED_OR_OFF,
    YELLOW_ENTRY,
    PhaseHistoryCrossingObserver,
    classify_crossing_phase,
    classify_signal_state,
    load_controlled_via_map,
    observer_integrity_failures,
    sumo_time_to_ticks,
)
from algorithms.cov2x.vehicle.sticky_leader import StickyLeadCAV


def _metadata() -> dict:
    return {
        "episode_id": "audit",
        "edge_lanes": {
            "in": [{"lane_id": "in_0", "edge_id": "in"}],
            "out": [{"lane_id": "out_0", "edge_id": "out"}],
        },
        "intersections": {
            "i": {
                "lanes": {
                    "in_0": {
                        "lane_id": "in_0",
                        "edge_id": "in",
                        "length_m": 100.0,
                    },
                    "out_0": {
                        "lane_id": "out_0",
                        "edge_id": "out",
                        "length_m": 100.0,
                    },
                },
                "connections": [
                    {
                        "connection_id": "c",
                        "from_lane": "in_0",
                        "to_lane": "out_0",
                        "movement": "straight",
                        "tls_id": "tls",
                        "link_index": 3,
                    }
                ],
            }
        },
    }


def _vehicle(lane_id: str, lane_position: float, distance: float) -> dict:
    return {
        "location": {
            "lane_id": lane_id,
            "lane_position_m": lane_position,
            "route_index": 0,
            "route_edges": ["in", "out"],
        },
        "traffic": {"distance_m": distance},
        "motion": {"speed_mps": 10.0},
        "next_signal": {
            "intersection_id": "i",
            "tls_id": "i",
            "state": "r",
        },
    }


def _frame(frame_id: int, time_s: float, lane_id: str, state: str) -> dict:
    position = 99.5 if lane_id == "in_0" else 0.5
    distance = 10.0 if lane_id == "in_0" else 11.0
    return {
        "episode_id": "audit",
        "frame_id": frame_id,
        "simulation_time": time_s,
        "vehicles": {"v": _vehicle(lane_id, position, distance)},
        "intersections": {
            "i": {
                "current_phase": 0,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": time_s,
                "lanes": {
                    "in_0": {
                        "connection_signal_states": [
                            {
                                "connection_id": "c",
                                "movement": "straight",
                                "downstream_lane_id": "out_0",
                                "signal_state": state,
                            }
                        ]
                    }
                },
            }
        },
    }


def test_signal_state_contract_is_exact() -> None:
    assert classify_signal_state("G") == LEGAL_PROTECTED_GREEN
    assert classify_signal_state("g") == LEGAL_PERMISSIVE_GREEN
    assert classify_signal_state("y") == YELLOW_ENTRY
    assert classify_signal_state("r") == TRUE_RED_ENTRY
    assert classify_signal_state("u") == TRUE_RED_YELLOW_ENTRY
    assert classify_signal_state("s") == RIGHT_ON_RED_SPECIAL
    assert classify_signal_state("o") == UNCONTROLLED_OR_OFF
    assert classify_signal_state("O") == UNCONTROLLED_OR_OFF
    assert classify_signal_state("") == AMBIGUOUS_PHASE_TRANSITION


def _controlled_via() -> dict:
    return {
        ":demo_3_2_1": {
            "via_lane_id": ":demo_3_2_1",
            "tls_id": "tls",
            "link_index": 3,
            "from_lane": "in_0",
            "to_lane": "out_0",
        }
    }


def test_controlled_via_map_uses_explicit_tls_link_index(tmp_path) -> None:
    network = tmp_path / "network.net.xml"
    network.write_text(
        '<net><connection from="in" to="out" fromLane="0" toLane="0" '
        'via=":demo_3_2_1" tl="tls" linkIndex="9" dir="s"/></net>',
        encoding="utf-8",
    )
    mapping = load_controlled_via_map(network)
    assert mapping[":demo_3_2_1"] == {
        "via_lane_id": ":demo_3_2_1",
        "tls_id": "tls",
        "link_index": 9,
        "from_lane": "in_0",
        "to_lane": "out_0",
    }


def test_authoritative_link_index_recovers_coarsened_lane_tuple() -> None:
    controlled_via = _controlled_via()
    controlled_via[":demo_3_2_1"] = {
        **controlled_via[":demo_3_2_1"],
        "from_lane": "net_exact_in_7",
        "to_lane": "net_exact_out_2",
    }
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=controlled_via
    )
    observer.on_frame(_frame(0, 1.00, "in_0", "G"))
    observer.on_frame(_frame(1, 1.05, ":demo_3_2_1", "G"))
    event = observer.finish({})["crossing_events"][0]

    assert event["actual_tls_id"] == "tls"
    assert event["actual_tls_link_index"] == 3
    assert event["actual_connection_id"] == "c"
    assert event["connection_resolution_method"] == "authoritative_tls_link_index"
    assert event["movement_link_mapping_available"] is True
    assert event["phase_history_available"] is True
    assert event["crossing_class"] == LEGAL_PROTECTED_GREEN


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, 0),
        (0.00049, 0),
        (0.0005, 1),
        (1.2344, 1234),
        (1.2345, 1235),
        (219.99999999999997, 220000),
        (-0.00049, 0),
        (-0.0005, -1),
    ],
)
def test_sumo_time_to_ticks_matches_time2steps(
    seconds: float, expected: int
) -> None:
    assert sumo_time_to_ticks(seconds) == expected


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -float("inf")])
def test_sumo_time_to_ticks_rejects_nonfinite(seconds: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        sumo_time_to_ticks(seconds)


@pytest.mark.parametrize(
    ("state_before", "state_after", "class_before", "class_after"),
    [
        ("G", "y", LEGAL_PROTECTED_GREEN, YELLOW_ENTRY),
        ("y", "r", YELLOW_ENTRY, TRUE_RED_ENTRY),
        ("r", "G", TRUE_RED_ENTRY, LEGAL_PROTECTED_GREEN),
    ],
)
def test_exact_phase_boundaries_use_integer_tick_ownership(
    state_before: str,
    state_after: str,
    class_before: str,
    class_after: str,
) -> None:
    transition_tick = 220000
    cases = (
        (transition_tick - 1, class_before, state_before),
        (transition_tick, class_before, state_before),
        (transition_tick + 1, class_after, state_after),
    )
    for _repeat in range(50):
        for crossing_tick, expected_class, expected_state in cases:
            assert classify_crossing_phase(
                state_before=state_before,
                state_after=state_after,
                crossing_time_tick=crossing_tick,
                transition_time_tick=transition_tick,
                history_available=True,
            ) == (expected_class, expected_state)


@pytest.mark.parametrize("invalid_tick", [220000.0, True, "220000"])
def test_crossing_classifier_rejects_noninteger_hard_time(invalid_tick) -> None:
    with pytest.raises(TypeError, match="canonical integer tick"):
        classify_crossing_phase(
            state_before="G",
            state_after="y",
            crossing_time_tick=invalid_tick,
            transition_time_tick=220000,
            history_available=True,
        )
    with pytest.raises(TypeError, match="canonical integer tick"):
        classify_crossing_phase(
            state_before="G",
            state_after="y",
            crossing_time_tick=220000,
            transition_time_tick=invalid_tick,
            history_available=True,
        )


def test_observer_records_movement_specific_terminal_to_via_crossing() -> None:
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    observer.on_frame(_frame(0, 1.00, "in_0", "r"))
    observer.on_frame(_frame(1, 1.05, ":demo_3_2_1", "G"))
    result = observer.finish({"observer_frames": {"dropped": 0}})
    assert len(result["crossing_events"]) == 1
    event = result["crossing_events"][0]
    assert event["movement_id"] == "straight"
    assert event["terminal_lane_id"] == "in_0"
    assert event["via_lane_id"] == ":demo_3_2_1"
    assert event["actual_tls_link_index"] == 3
    assert event["actual_tls_id"] == "tls"
    assert event["candidate_tls_link_indexes"] == [3]
    assert event["movement_link_mapping_available"] is True
    assert event["phase_history_available"] is True
    assert event["phase_transition_timestamp_s"] == 1.05
    assert event["crossing_class"] == TRUE_RED_ENTRY


def test_float_interpolation_tie_is_diagnostic_not_hard_time() -> None:
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    before = _frame(0, 219.95, "in_0", "G")
    after = _frame(1, 220.0, ":demo_3_2_1", "y")
    before["vehicles"]["v"]["location"]["lane_position_m"] = 99.0
    observer.on_frame(before)
    observer.on_frame(after)
    event = observer.finish({})["crossing_events"][0]
    assert event["timebase_contract"] == SUMO_TIMEBASE_CONTRACT
    assert event["simulation_time_before_ms"] == 219950
    assert event["simulation_time_after_ms"] == 220000
    assert event["crossing_event_time_ms"] == 220000
    assert event["phase_transition_time_ms"] == 220000
    assert event["crossing_time_s"] == pytest.approx(220.0)
    assert event["crossing_time_diagnostic_only"] is True
    assert event["phase_transition_timestamp_s_diagnostic_only"] is True
    assert event["exact_boundary_owner"] == "state_before"
    assert event["phase_state_at_crossing"] == "G"
    assert event["crossing_class"] == LEGAL_PROTECTED_GREEN


def test_fail_fast_raises_on_true_red_entry(monkeypatch) -> None:
    monkeypatch.setenv("COV2X_PHASE_HISTORY_FAIL_FAST", "1")
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    observer.on_frame(_frame(0, 1.00, "in_0", "r"))
    with pytest.raises(RuntimeError, match="phase-history fail-fast"):
        observer.on_frame(_frame(1, 1.05, ":demo_3_2_1", "r"))
    assert observer.events[-1]["crossing_class"] == TRUE_RED_ENTRY


def test_nonconsecutive_crossing_with_controller_tick_continuity_is_classified(
) -> None:
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    observer.on_frame(_frame(0, 138.70, "in_0", "G"))
    observer.on_frame(_frame(2, 138.80, ":demo_3_2_1", "G"))
    event = observer.finish({})["crossing_events"][0]
    assert event["phase_history_available"] is True
    assert event["phase_history_method"] == "controller_stage_tick_continuity"
    assert event["phase_history_span_proven"] is True
    assert event["frame_pair_consecutive"] is False
    assert event["frame_span_canonical"] is True
    assert event["frame_id_delta"] == 2
    assert event["missing_frame_count"] == 1
    assert event["crossing_class"] == LEGAL_PROTECTED_GREEN


def test_nonconsecutive_stable_red_remains_true_red() -> None:
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    observer.on_frame(_frame(0, 138.70, "in_0", "r"))
    observer.on_frame(_frame(2, 138.80, ":demo_3_2_1", "r"))
    event = observer.finish({})["crossing_events"][0]
    assert event["phase_history_available"] is True
    assert event["phase_history_method"] == "controller_stage_tick_continuity"
    assert event["crossing_class"] == TRUE_RED_ENTRY


def test_nonconsecutive_crossing_without_proven_continuity_is_ambiguous() -> None:
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    before = _frame(0, 1.00, "in_0", "r")
    after = _frame(2, 1.10, ":demo_3_2_1", "r")
    after["intersections"]["i"]["current_phase"] = 1
    after["intersections"]["i"]["stage_elapsed"] = 0.05
    observer.on_frame(before)
    observer.on_frame(after)
    event = observer.finish({})["crossing_events"][0]
    assert event["phase_history_available"] is False
    assert event["phase_history_method"] == "unavailable"
    assert event["phase_history_span_proven"] is False
    assert event["crossing_class"] == AMBIGUOUS_PHASE_TRANSITION


def test_runtime_audit_snapshot_is_observer_only_and_deep_copied(
    monkeypatch,
) -> None:
    from algorithms.cov2x import mvp_runtime

    leaders = StickyLeadCAV()
    lease = leaders.assign("i", "straight", "v", now=0.0, lease_s=15.0)
    monkeypatch.setenv("COV2X_PHASE_HISTORY_AUDIT", "1")
    monkeypatch.setattr(mvp_runtime, "_leaders", leaders)
    monkeypatch.setattr(
        mvp_runtime,
        "_speed_advice",
        {("i", "straight", "v", lease.assignment_epoch): {"cap_mps": 9.0}},
    )
    monkeypatch.setattr(
        mvp_runtime,
        "_phase_history_audit",
        {"policy_snapshots": [], "proxy_events": []},
    )
    mvp_runtime._record_phase_history_policy_snapshot(
        snapshot_id="episode:0",
        step_id=0,
        sim_time=0.05,
        signal_actions={"i": {"target_phase": 1}},
    )
    snapshot = mvp_runtime.phase_history_audit_snapshot()
    assert snapshot["policy_snapshots"][0]["active_leases"][0] == {
        "vehicle_id": "v",
        "intersection_id": "i",
        "movement_id": "straight",
        "assignment_epoch": lease.assignment_epoch,
        "issued_at_s": 0.0,
        "expires_at_s": 15.0,
        "active_cap": True,
    }
    snapshot["policy_snapshots"].clear()
    assert len(mvp_runtime.phase_history_audit_snapshot()["policy_snapshots"]) == 1


def test_synchronous_exact_ledger_is_atomically_persisted(
    monkeypatch, tmp_path
) -> None:
    audit_module.reset_completed()
    observer = PhaseHistoryCrossingObserver(
        _metadata(), step_length_s=0.05, controlled_via=_controlled_via()
    )
    observer.on_frame(_frame(0, 1.00, "in_0", "G"))
    observer.on_frame(_frame(1, 1.05, ":demo_3_2_1", "G"))
    monkeypatch.setattr(audit_module, "_active", observer)
    summary_path = tmp_path / "observer_summary.json"
    monkeypatch.setenv("COV2X_PHASE_HISTORY_SUMMARY_PATH", str(summary_path))
    monkeypatch.setenv("COV2X_PHASE_HISTORY_REQUIRE_COMPLETE", "1")

    audit_module.finish(
        {
            "observer_frames": {"generated": 2, "consumed": 2, "dropped": 0},
            "observer_ledger": {
                "delivery_mode": "synchronous",
                "executed_tick_ids": [0, 1],
                "observer_committed_tick_ids": [0, 1],
                "missing_ticks": [],
                "duplicate_ticks": [],
                "out_of_order_ticks": [],
                "unexpected_committed_ticks": [],
                "finalized": True,
            },
        }
    )

    persisted = json.loads(summary_path.read_text())
    assert persisted["executed_tick_ids"] == [0, 1]
    assert persisted["observer_committed_tick_ids"] == [0, 1]
    assert persisted["tick_coverage"] == 1.0
    assert persisted["phase_coverage"] == 1.0
    assert persisted["crossing_classification_coverage"] == 1.0
    assert persisted["summary_persisted"] is True
    assert observer_integrity_failures(
        persisted, require_persisted=True
    ) == []
    assert stat.S_IMODE(summary_path.stat().st_mode) == 0o444
    audit_module.reset_completed()
