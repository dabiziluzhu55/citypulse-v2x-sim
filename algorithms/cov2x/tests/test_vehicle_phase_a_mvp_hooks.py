from __future__ import annotations

import torch

from algorithms.cov2x import mvp_runtime
from algorithms.cov2x.tests.test_mvp_runtime import _payload


def _initialize_release_runtime(monkeypatch, *, episode_id="phase-a-hook"):
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("COV2X_ROAD_GAIN", "1")
    monkeypatch.setenv("COV2X_CLOUD_GAIN", "1")
    monkeypatch.setenv("COV2X_VEHICLE_GAIN", "1")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    monkeypatch.delenv("COV2X_PARENT_MODEL_PATH", raising=False)
    mvp_runtime.reset_untrained_state()
    payload = {**_payload(), "episode_id": episode_id}
    mvp_runtime.initialize(payload)
    with torch.no_grad():
        mvp_runtime._vehicle_actor.mean.weight.zero_()
        mvp_runtime._vehicle_actor.mean.bias.zero_()
    return payload


def _finish(payload):
    mvp_runtime.finish(
        {
            "simulation_time": 5.0,
            "vehicles": {},
            "intersections": payload["intersections"],
        }
    )
    assert mvp_runtime.take_collected_rollout() is not None


def test_phase_a_diagnostics_are_strictly_opt_in(monkeypatch):
    payload = _initialize_release_runtime(monkeypatch)
    monkeypatch.delenv("COV2X_PHASE_A_DIAGNOSTICS", raising=False)
    ordinary = mvp_runtime.step({**payload, "step_id": 0, "simulation_time": 0.0})
    assert "phase_a_evidence" not in ordinary["diagnostics"]
    assert "phase_a_opportunities" not in ordinary["diagnostics"]
    _finish(payload)

    payload = _initialize_release_runtime(monkeypatch, episode_id="phase-a-diagnostic")
    monkeypatch.setenv("COV2X_PHASE_A_DIAGNOSTICS", "1")
    diagnostic = mvp_runtime.step(
        {**payload, "step_id": 0, "simulation_time": 0.0}
    )
    assert diagnostic["actions"]["vehicles"] == {}
    opportunity = diagnostic["diagnostics"]["phase_a_opportunities"][0]
    assert opportunity["vehicle_id"] == "v1"
    assert opportunity["intersection_id"] == "i"
    assert opportunity["movement_id"] == "through"
    assert opportunity["previous_advice_mps"] is None
    assert opportunity["transition_kind"] == "native_release"
    evidence = diagnostic["diagnostics"]["phase_a_evidence"]
    assert set(evidence) == {"cloud_priority", "transport_trace", "ledger", "safety"}
    assert evidence["transport_trace"]
    assert all("phase-a-diagnostic" not in row["message_id"] for row in evidence["transport_trace"])
    _finish(payload)


def test_phase_a_joint_override_replays_cloud_and_road_with_valid_trace(monkeypatch):
    payload = _initialize_release_runtime(monkeypatch, episode_id="phase-a-override")
    monkeypatch.setenv("COV2X_PHASE_A_DIAGNOSTICS", "1")
    mvp_runtime.set_phase_a_joint_action_override(
        cloud_priority={"i": 0.25},
        signal_actions={"i": {"target_phase": 1}},
    )
    try:
        response = mvp_runtime.step(
            {**payload, "step_id": 0, "simulation_time": 0.0}
        )
    finally:
        mvp_runtime.clear_phase_a_joint_action_override()

    assert response["actions"]["signals"] == {"i": {"target_phase": 1}}
    assert response["actions"]["vehicles"] == {}
    evidence = response["diagnostics"]["phase_a_evidence"]
    assert evidence["cloud_priority"] == {"i": 0.25}
    assert any(
        row["logical_phase"] == "cloud" and row["event"] == "CONSUME"
        for row in evidence["transport_trace"]
    )
    assert any(
        row["logical_phase"] == "road" and row["event"] == "CONSUME"
        for row in evidence["transport_trace"]
    )
    _finish(payload)
