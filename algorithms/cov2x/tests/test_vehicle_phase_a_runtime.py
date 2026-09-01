from __future__ import annotations

import copy

import pytest
import torch

from algorithms.cov2x import mvp_runtime
from algorithms.cov2x.tests.test_mvp_runtime import _payload as _mvp_payload
from algorithms.cov2x.vehicle import phase_a_runtime


def _payload(step_id: int, *, episode_id: str = "episode-a", speed: float = 10.0):
    return {
        "episode_id": episode_id,
        "step_id": step_id,
        "simulation_time": float(step_id * 5),
        "intersections": {
            "tls": {
                "current_phase": 0,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 5.0,
                "lanes": {"terminal_0": {"vehicle_count": 1}},
            }
        },
        "vehicles": {
            "veh": {
                "type_id": "passenger",
                "control_authority": True,
                "motion": {
                    "speed_mps": speed,
                    "allowed_speed_mps": 12.0,
                },
                "location": {
                    "road_id": "terminal",
                    "lane_id": "terminal_0",
                    "route_edges": ["terminal", "out"],
                    "route_index": 0,
                },
                "next_signal": {
                    "intersection_id": "tls",
                    "state": "G",
                    "distance_m": 30.0,
                },
            }
        },
    }


def _response(step_id: int, *, opportunity: bool = False):
    diagnostics = {
        "phase_a_evidence": {
            "cloud_priority": {"tls": 0.25},
            "transport_trace": [
                {
                    "event": "CONSUME",
                    "message_id": f"{step_id}:spat:tls",
                    "snapshot_id": str(step_id),
                    "logical_phase": "road",
                    "sim_time": float(step_id * 5),
                    "causal_parents": [],
                }
            ],
            "ledger": {"active": {"veh": 1.0}, "total": 1.0},
            "safety": {"red_crossing_proxy": 0, "dangerous_gap": 0},
        }
    }
    if opportunity:
        diagnostics["phase_a_opportunities"] = [
            {
                "vehicle_id": "veh",
                "intersection_id": "tls",
                "movement_id": "through",
                "assignment_epoch": 1,
                "previous_advice_mps": None,
                "base_speed_mps": 12.0,
                "delta_v_max_mps": 1.2,
                "transition_kind": "native_release",
            }
        ]
    return {
        "protocol_version": "2.0",
        "candidate_id": "g30",
        "episode_id": "ignored",
        "step_id": step_id,
        "actions": {
            "signals": {"tls": {"target_phase": 0}},
            "vehicles": {},
        },
        "diagnostics": diagnostics,
    }


def test_canonical_pre_action_hash_ignores_only_episode_identity():
    first = phase_a_runtime.decision_record(_payload(1, episode_id="a"), _response(1))
    second = phase_a_runtime.decision_record(_payload(1, episode_id="b"), _response(1))
    assert first["pre_action_hashes"] == second["pre_action_hashes"]

    changed = _payload(1, episode_id="b", speed=9.9)
    third = phase_a_runtime.decision_record(changed, _response(1))
    assert first["pre_action_hashes"]["observation"] != third["pre_action_hashes"]["observation"]


def test_treatment_selection_is_spaced_unique_and_horizon_complete():
    candidates = []
    for step_id in range(1, 60):
        candidates.append(
            {
                "step_id": step_id,
                "simulation_time": float(step_id * 5),
                "opportunity": {
                    "vehicle_id": f"veh-{step_id // 2}",
                    "movement_id": "through",
                    "intersection_id": "tls",
                    "previous_advice_mps": None,
                    "transition_kind": "native_release",
                },
            }
        )

    selected = phase_a_runtime.select_treatments(
        candidates,
        target_count=10,
        minimum_spacing_s=20.0,
        episode_duration_s=300.0,
        horizon_s=20.0,
    )

    assert len(selected) == 10
    assert all(
        right["simulation_time"] - left["simulation_time"] >= 20.0
        for left, right in zip(selected, selected[1:])
    )
    assert len(
        {
            (
                row["opportunity"]["vehicle_id"],
                row["opportunity"]["movement_id"],
            )
            for row in selected
        }
    ) == len(selected)
    assert selected[-1]["simulation_time"] + 20.0 <= 300.0


def test_negative_cap_uses_frozen_incremental_mapper_and_zero_holds():
    opportunity = {
        "vehicle_id": "veh",
        "base_speed_mps": 12.0,
        "delta_v_max_mps": 1.2,
    }
    command, active_cap = phase_a_runtime.vehicle_command(
        arm="u=-0.50",
        opportunity=opportunity,
        vehicle=_payload(1)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=None,
        first_decision=True,
    )
    assert command == {"veh": {"target_speed_mps": pytest.approx(11.4)}}
    assert active_cap == pytest.approx(11.4)

    held, held_cap = phase_a_runtime.vehicle_command(
        arm="u=-0.50",
        opportunity=opportunity,
        vehicle=_payload(2)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=active_cap,
        first_decision=False,
    )
    assert held == {"veh": {"target_speed_mps": pytest.approx(11.4)}}
    assert held_cap == pytest.approx(11.4)


def test_context_glosa_reference_uses_exact_initial_cap_then_frozen_zero_hold():
    opportunity = {
        "vehicle_id": "veh",
        "base_speed_mps": 12.0,
        "delta_v_max_mps": 1.2,
        "gate_decision": {"reference_speed_cap_mps": 9.75},
    }
    command, active_cap = phase_a_runtime.vehicle_command(
        arm="QUEUE_AWARE_GLOSA",
        opportunity=opportunity,
        vehicle=_payload(1)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=None,
        first_decision=True,
    )
    assert command == {"veh": {"target_speed_mps": pytest.approx(9.75)}}
    assert active_cap == pytest.approx(9.75)

    held, held_cap = phase_a_runtime.vehicle_command(
        arm="QUEUE_AWARE_GLOSA",
        opportunity=opportunity,
        vehicle=_payload(2)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=active_cap,
        first_decision=False,
    )
    assert held == {"veh": {"target_speed_mps": pytest.approx(9.75)}}
    assert held_cap == pytest.approx(9.75)


def test_zero_hold_releases_when_native_ceiling_falls_below_active_cap():
    opportunity = {
        "vehicle_id": "veh",
        "base_speed_mps": 12.0,
        "delta_v_max_mps": 1.2,
    }
    command, active_cap = phase_a_runtime.vehicle_command(
        arm="u=-0.50",
        opportunity=opportunity,
        vehicle=_payload(1)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=None,
        first_decision=True,
    )
    assert command == {"veh": {"target_speed_mps": pytest.approx(11.4)}}

    lower_ceiling_vehicle = copy.deepcopy(_payload(2)["vehicles"]["veh"])
    lower_ceiling_vehicle["motion"]["allowed_speed_mps"] = 10.0
    released, next_cap = phase_a_runtime.vehicle_command(
        arm="u=-0.50",
        opportunity=opportunity,
        vehicle=lower_ceiling_vehicle,
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=active_cap,
        first_decision=False,
    )

    assert released == {}
    assert next_cap is None


@pytest.mark.parametrize("arm", ["NATIVE_RELEASE", "NATIVE_RELEASE_PLACEBO"])
def test_release_arms_never_issue_a_vehicle_command(arm):
    command, cap = phase_a_runtime.vehicle_command(
        arm=arm,
        opportunity={"vehicle_id": "veh"},
        vehicle=_payload(1)["vehicles"]["veh"],
        vehicle_types={"passenger": {"max_speed_mps": 13.9, "min_gap_m": 2.5}},
        previous_cap_mps=None,
        first_decision=True,
    )
    assert command == {}
    assert cap is None


def test_prefix_comparison_fails_closed_on_any_hash_or_joint_action_drift():
    expected = phase_a_runtime.decision_record(_payload(1), _response(1))
    observed = copy.deepcopy(expected)
    phase_a_runtime.assert_prefix_equal(expected, observed)

    observed["pre_action_hashes"]["ledger"] = "drift"
    with pytest.raises(RuntimeError, match="ledger"):
        phase_a_runtime.assert_prefix_equal(expected, observed)


def test_runtime_stops_joint_action_comparison_after_counterfactual_horizon(
    monkeypatch,
):
    treatment = {
        "simulation_time": 0.0,
        "horizon_s": 20.0,
        "opportunity": {
            "vehicle_id": "veh",
            "base_speed_mps": 12.0,
            "delta_v_max_mps": 1.2,
        },
    }
    expected = phase_a_runtime.decision_record(_payload(5), _response(5))
    drifted = _response(5)
    drifted["actions"]["signals"]["tls"]["target_phase"] = 1
    phase_a_runtime.configure_run(
        mode="arm",
        tape={"steps": [expected]},
        treatment=treatment,
        arm="NATIVE_RELEASE",
    )
    monkeypatch.setattr(mvp_runtime, "initialize", lambda payload: {"ready": True})
    monkeypatch.setattr(mvp_runtime, "step", lambda payload: drifted)
    phase_a_runtime.initialize({"vehicle_types": {}})

    result = phase_a_runtime.step(_payload(5))

    assert result["actions"]["signals"]["tls"]["target_phase"] == 1


def test_runtime_discovers_then_replays_joint_tape_with_only_target_cap(monkeypatch):
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("COV2X_ROAD_GAIN", "1")
    monkeypatch.setenv("COV2X_CLOUD_GAIN", "1")
    monkeypatch.setenv("COV2X_VEHICLE_GAIN", "1")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    monkeypatch.delenv("COV2X_PARENT_MODEL_PATH", raising=False)
    mvp_runtime.reset_untrained_state()
    phase_a_runtime.reset_completed()
    discovery_payload = {**_mvp_payload(), "episode_id": "discovery"}
    phase_a_runtime.configure_run(mode="discovery")
    phase_a_runtime.initialize(discovery_payload)
    with torch.no_grad():
        mvp_runtime._vehicle_actor.mean.weight.zero_()
        mvp_runtime._vehicle_actor.mean.bias.zero_()
    phase_a_runtime.step(
        {**discovery_payload, "step_id": 0, "simulation_time": 0.0}
    )
    phase_a_runtime.step(
        {**discovery_payload, "step_id": 1, "simulation_time": 5.0}
    )
    phase_a_runtime.finish(
        {
            "simulation_time": 10.0,
            "vehicles": {},
            "intersections": discovery_payload["intersections"],
        }
    )
    tape = phase_a_runtime.take_completed()
    assert tape is not None and tape["candidates"]
    assert mvp_runtime.take_collected_rollout() is not None
    treatment = {**tape["candidates"][0], "horizon_s": 20.0}

    arm_payload = {**_mvp_payload(), "episode_id": "arm"}
    phase_a_runtime.configure_run(
        mode="arm", tape=tape, treatment=treatment, arm="u=-0.50"
    )
    phase_a_runtime.initialize(arm_payload)
    first = phase_a_runtime.step(
        {**arm_payload, "step_id": 0, "simulation_time": 0.0}
    )
    assert set(first["actions"]["vehicles"]) == {"v1"}
    first_cap = first["actions"]["vehicles"]["v1"]["target_speed_mps"]
    assert first_cap < 13.9

    held = phase_a_runtime.step(
        {**arm_payload, "step_id": 1, "simulation_time": 5.0}
    )
    assert held["actions"]["vehicles"]["v1"]["target_speed_mps"] == pytest.approx(
        first_cap
    )
    assert held["actions"]["signals"] == tape["steps"][1]["joint_action"]["signals"]
    phase_a_runtime.finish(
        {
            "simulation_time": 10.0,
            "vehicles": {},
            "intersections": arm_payload["intersections"],
        }
    )
    assert phase_a_runtime.take_completed() is not None
    assert mvp_runtime.take_collected_rollout() is not None
