"""CPU-only deployment contract tests for the explicit CV Joint V1 candidate."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from traffic_control.cov2x import aliases
from traffic_control.cov2x.communication.bridge import CVJointV1EventBridge
from traffic_control.cov2x.communication.transport import MessageBus
from traffic_control.cov2x import controller as runtime

REPO = Path(__file__).resolve().parents[2]
TRAFFIC_CONTROL_ROOT = REPO / "traffic_control"
PERIODS = ("morning_peak", "off_peak", "evening_peak")


def _actual_map_inputs(period: str) -> tuple[dict, dict]:
    fixture = Path(__file__).parent / "test_fixtures" / f"cv_joint_v1_{period}.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    return data["metadata"], data["frame"]


def _clear_candidate(candidate) -> None:
    if runtime._state.get("active"):
        runtime.finish({"reason": "test_cleanup", "simulation_time": 5.0})
    candidate.set_v2x_event_sink(None)
    candidate.reset()


def test_cv_joint_v1_is_the_only_deployment_model():
    assert set(aliases.MODEL_ALIASES) == {"cv_joint_v1"}
    assert aliases.DEFAULT_MODEL_ALIAS == "cv_joint_v1"
    assert aliases.resolve_model("cv_joint_v1").adapter_module == "traffic_control.cov2x.deployment"
    for old in ("cov2x_g30_temp_cap_u24", "cov2x_joint_ep12"):
        with pytest.raises(ValueError):
            aliases.resolve_model(old)
    for partial in ("east_dense", "west_dense"):
        with pytest.raises(ValueError):
            aliases.default_model_alias_for(partial)


def test_cv_joint_v1_alias_requires_full_twenty_tls_before_loading() -> None:
    with pytest.raises(ValueError, match="exactly demo_1"):
        aliases.validate_alias_combo(["demo_1"], "cv_joint_v1")
    alias, path = aliases.validate_alias_combo(
        [f"demo_{index}" for index in range(1, 21)], "cv_joint_v1"
    )
    assert alias == "cv_joint_v1"
    assert path.name == "cv_joint_v1_generation_003.pt"


def test_cv_joint_v1_manifest_pins_live_sources_and_catalog() -> None:
    model = aliases.resolve_model("cv_joint_v1")
    manifest = json.loads(model.manifest_path.read_text(encoding="utf-8"))
    assert manifest["generation"] == 3
    assert manifest["n_movements"] == 199
    assert len(manifest["movement_catalog"]) == 199
    assert manifest["ippo_checkpoint_sha256"] == (
        "4055ec30bcd03c65572720cea38e51a338f466c351e21124be5fa683e6339449"
    )
    assert manifest["canonical_topology_sha256"] == (
        "026a9d2c884a8722a5e85021a11220cd7b25cc46f5b10118850f0bc1118adddc"
    )
    assert manifest["source_checkpoint_sha256"] == (
        "76762b6917598350cc8448c9f82eafcaacc58a5cc581f19aa5ca3e3c5a26feac"
    )
    assert manifest["live_dirty_source_hashes"] == {
        "algorithms/cov2x/controller.py": (
            "ce4497388a09c707e4781950c7ebe46f598b644e9fc948761097f5cc4aee4e13"
        ),
        "algorithms/cov2x/train.py": (
            "aa2ac3888c6d0b89c80ad301b4a0a49434d9c8033b5cf25460068ba6ec46225c"
        ),
        "algorithms/ippo/controller.py": (
            "e541cc1b0b05b8c29863db47bee7ac5e716790e3ceb9d31777b1c8e9a510db12"
        ),
        "traffic_control/ippo/controller.py": (
            "1ab963872b7c05331b2275e200bdfc8b3f18606a47c6dbfd52ae86e453a7369a"
        ),
    }


def test_cv_joint_v1_rejects_missing_or_tampered_model(tmp_path: Path) -> None:
    from traffic_control.cov2x import deployment as candidate

    model = aliases.resolve_model("cv_joint_v1")
    missing = replace(model, checkpoint_path=tmp_path / "missing.pt")
    with pytest.raises(FileNotFoundError):
        candidate._load_policy(missing)

    tampered = tmp_path / "cv_joint_v1_generation_003.pt"
    tampered.write_bytes(model.checkpoint_path.read_bytes() + b"tamper")
    wrong = replace(model, checkpoint_path=tampered)
    with pytest.raises(ValueError, match="SHA-256"):
        candidate._load_policy(wrong)


@pytest.mark.parametrize("period", PERIODS)
def test_cv_joint_v1_matches_product_frozen_ippo_on_real_static_map(
    period: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traffic_control.cov2x import deployment as candidate
    from traffic_control.ippo import controller as ippo

    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_JOINT_ROAD", "off")
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    metadata, frame = _actual_map_inputs(period)
    environment_before = {
        key: os.environ.get(key)
        for key in (
            "IPPO_MODE",
            "IPPO_JOINT_ROAD",
            "IPPO_MODEL_ALIAS",
            "IPPO_MODEL_PATH",
            "IPPO_ACTION_INTERVAL",
            "IPPO_MAX_GREEN_FACTOR",
            "IPPO_EFFECTIVE_DEMAND",
        )
    }

    ippo.initialize(deepcopy(metadata))
    reference = ippo.step(deepcopy(frame))
    ippo.finish({"reason": "reference", "simulation_time": 5.0})
    monkeypatch.setenv("IPPO_MODEL_ALIAS", "sentinel-must-be-restored")
    environment_before["IPPO_MODEL_ALIAS"] = os.environ["IPPO_MODEL_ALIAS"]
    candidate.configure(aliases.resolve_model("cv_joint_v1"))
    try:
        initialized = candidate.initialize(deepcopy(metadata))
        assert "IPPO_MODEL_ALIAS" not in os.environ
        candidate_state = runtime._state
        hooks = candidate_state["hooks"]
        ippo_builder = hooks.builder
        original_choose = hooks.original_choose
        original_build = hooks.original_build
        response = candidate.step(deepcopy(frame))
        assert initialized["candidate_id"] == candidate.CANDIDATE_ID
        assert initialized["deployment_model_alias"] == "cv_joint_v1"
        assert initialized["checkpoint_generation"] == 3
        assert all(
            not parameter.requires_grad
            for parameter in runtime._state["policy"].parameters()
        )
        assert response["actions"]["signals"] == reference["actions"]["signals"]
        assert not reference["actions"]["vehicles"]
        routes = {
            (event["source_role"], event["destination_role"], event["original_kind"])
            for event in response["v2x"]["events"]
        }
        assert {
            ("vehicle", "cloud", "vehicle_feedback"),
            ("vehicle", "road", "vehicle_state"),
            ("vehicle", "cloud", "vehicle_state"),
            ("road", "vehicle", "road_state"),
            ("road", "cloud", "road_state"),
            ("cloud", "road", "coordination_context"),
            ("cloud", "vehicle", "speed_permission"),
            ("road", "cloud", "road_feedback"),
        } <= routes
        assert all(
            isinstance(event["payload_sha256"], str)
            and len(event["payload_sha256"]) == 64
            for event in response["v2x"]["events"]
        )
        permission = next(
            event for event in response["v2x"]["events"]
            if event["original_kind"] == "speed_permission"
            and event["event"] == "SEND"
        )
        assert {"movement", "permit"} <= set(permission["payload_fields"])
        assert permission["payload_projection"]["movement"]
        candidate.finish({"reason": "completed", "simulation_time": 5.0})
        assert hooks.installed is False
        assert ippo_builder.build_phase_features is original_build
        assert ippo._choose_action is original_choose
    finally:
        if runtime._state.get("active"):
            candidate.finish({"reason": "test_cleanup", "simulation_time": 5.0})
        candidate.reset()
    assert {
        key: os.environ.get(key)
        for key in environment_before
    } == environment_before


def test_cv_joint_v1_bridge_preserves_kind_ttl_and_sink_cursor() -> None:
    from traffic_control.cov2x.communication import V2XEventDrain

    bus = MessageBus("bridge-episode")
    rows = [
        ("vehicle_state", "vehicle", "road", {"vehicles": {}}),
        ("vehicle_state", "vehicle", "cloud", {"vehicles": {}}),
        ("road_state", "road", "vehicle", {"intersections": {}}),
        ("road_state", "road", "cloud", {"intersections": {}}),
        ("coordination_context", "cloud", "road", {"movement_permissions": {}}),
        ("speed_permission", "cloud", "vehicle", {"movement": "m", "permit": True}),
        ("road_feedback", "road", "cloud", {"observed": {}}),
        ("vehicle_feedback", "vehicle", "cloud", {"previous_action_results": {}}),
    ]
    for kind, source, destination, payload in rows:
        message = bus.send(
            kind, source, destination, 0, 10.0, 15.0, payload
        )
        bus.consume(message, destination, 10.0)
    bridge_sink = V2XEventDrain()
    bridge = CVJointV1EventBridge("bridge-episode", event_sink=bridge_sink)
    for message, (_, _, _, payload) in zip(
        tuple(bus._sent), rows
    ):
        bridge.observe_message(message, payload)
    bridge.sync(bus.events)
    batch = bridge.event_batch(snapshot_id="bridge-episode:0")
    assert batch["event_count"] == len(bus.events)
    assert {
        row["original_kind"] for row in batch["events"]
    } == {row[0] for row in rows}
    permission = next(
        row for row in batch["events"]
        if row["original_kind"] == "speed_permission"
        and row["event"] == "SEND"
    )
    assert permission["message_type"] == "CVJointV1"
    assert permission["drop_reason"] is None
    assert permission["payload_fields"] == [
        "movement", "permit"
    ]
    assert permission["payload_sha256"] == next(
        event["payload_sha256"]
        for event in bus.events
        if event["event"] == "SEND"
        and event["kind"] == "speed_permission"
    )
    assert bridge.drain()["event_count"] == len(bus.events)
    assert bridge.drain()["event_count"] == 0
    assert len(bridge_sink.snapshot()) == len(bus.events)


def test_cv_joint_v1_bridge_maps_expiry_without_claiming_receipt() -> None:
    bridge = CVJointV1EventBridge("expiry-episode")
    bridge.sync([{
        "event": "EXPIRE",
        "message_id": "expiry-episode:1",
        "episode_id": "expiry-episode",
        "kind": "speed_permission",
        "source": "cloud",
        "destination": "vehicle",
        "step_id": 2,
        "time": 15.01,
        "generated_at": 0.0,
        "valid_until": 15.0,
        "payload": {"movement": "m", "permit": False},
        "parents": (),
    }])
    row = bridge.event_batch()["events"][0]
    assert row["event"] == "TTL_EXPIRED"
    assert row["original_kind"] == "speed_permission"
    assert row["drop_reason"] == "ttl_expired"


def test_cv_joint_v1_inline_batch_keeps_held_and_expired_events() -> None:
    from traffic_control.cov2x.communication.transport import PermissionBook

    bus = MessageBus("inline-episode")
    bridge = CVJointV1EventBridge("inline-episode")
    first_message = bus.send(
        "speed_permission",
        "cloud",
        "vehicle",
        1,
        5.0,
        15.0,
        {"movement": "m1", "permit": True, "policy_version": "p"},
    )
    bridge.observe_message(first_message, first_message.payload)
    bridge.sync(bus.events)
    first_batch = bridge.inline_batch(
        "inline-episode:1", after_sequence=0
    )
    assert [item["event"] for item in first_batch["events"]] == ["SEND"]

    bus.consume(first_message, "vehicle", 10.0)
    bridge.sync(bus.events)
    held_batch = bridge.inline_batch(
        "inline-episode:2", after_sequence=first_batch["last_sequence"]
    )
    assert [item["event"] for item in held_batch["events"]] == [
        "DELIVER", "CONSUME"
    ]
    assert all(item["snapshot_id"] == "inline-episode:1"
               for item in held_batch["events"])

    permissions = PermissionBook(bus)
    expired = permissions.publish(
        "m2", False, 4, 10.0, "p"
    )
    bridge.observe_message(expired, expired.payload)
    bridge.sync(bus.events)
    before_expiry = bridge.inline_batch(
        "inline-episode:4", after_sequence=held_batch["last_sequence"]
    )
    permissions.current("m2", 15.0)
    bridge.sync(bus.events)
    expiry_batch = bridge.inline_batch(
        "inline-episode:5", after_sequence=before_expiry["last_sequence"]
    )
    assert [item["event"] for item in expiry_batch["events"]] == [
        "TTL_EXPIRED"
    ]
    assert expiry_batch["events"][0]["original_kind"] == "speed_permission"
    assert expiry_batch["snapshot_id"] == "inline-episode:5"


def test_cv_joint_v1_receipt_buffer_preserves_echo_and_release_semantics() -> None:
    from traffic_control.cov2x.vehicle.feedback import EpisodeBuffer

    buffer = EpisodeBuffer("receipt-episode", "policy-v1", "morning_peak", seed=7)
    assert buffer.observe_receipts({
        "step_id": 0,
        "simulation_time": 0.0,
        "previous_action_results": {"step_id": None, "vehicles": {}},
    }) == []
    buffer.record_request(
        1, "veh-1", {"target_lane_index": 2}, cloud_message_id="msg-1"
    )
    receipts = buffer.observe_receipts({
        "step_id": 1,
        "simulation_time": 5.0,
        "previous_action_results": {
            "step_id": 1,
            "vehicles": {
                "veh-1": {
                    "requested": {"target_lane_index": 2},
                    "actual_speed_mps": 5.0,
                    "actual_lane_index": 2,
                    "speed_status": None,
                    "lane_change_status": "applied",
                }
            },
        },
    })
    assert receipts[0]["requested"] == {"target_lane_index": 2}
    assert receipts[0]["lane_change_status"] == "applied"

    buffer.record_request(2, "veh-1", {}, cloud_message_id=None)
    release = buffer.observe_receipts({
        "step_id": 2,
        "simulation_time": 10.0,
        "previous_action_results": {"step_id": 2, "vehicles": {}},
    })
    assert release[0]["implicit_release"] is True
    assert release[0]["actual_speed_mps"] is None


def test_cv_joint_v1_receipt_buffer_rejects_mismatched_requested_echo() -> None:
    from traffic_control.cov2x.vehicle.feedback import EpisodeBuffer

    buffer = EpisodeBuffer("receipt-identity", "policy-v1", "morning_peak")
    buffer.record_request(1, "veh-1", {"target_speed_mps": 4.0})
    with pytest.raises(ValueError, match="requested echo"):
        buffer.observe_receipts({
            "step_id": 1,
            "simulation_time": 5.0,
            "previous_action_results": {
                "step_id": 1,
                "vehicles": {
                    "veh-1": {
                        "requested": {"target_speed_mps": 5.0},
                        "actual_speed_mps": 4.0,
                        "actual_lane_index": 0,
                        "speed_status": "applied",
                        "lane_change_status": None,
                    }
                },
            },
        })
    assert buffer.receipts == []


def test_cv_joint_v1_requires_full_twenty_tls_and_is_eval_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traffic_control.cov2x import deployment as candidate

    monkeypatch.setenv("COV2X_MODE", "train")
    with pytest.raises(ValueError, match="inference-only"):
        candidate.configure(aliases.resolve_model("cv_joint_v1"))
    monkeypatch.setenv("COV2X_MODE", "eval")
    with pytest.raises(ValueError, match="inference-only"):
        runtime.configure(period="morning_peak", training=True)
    candidate.configure(aliases.resolve_model("cv_joint_v1"))
    try:
        with pytest.raises(ValueError, match="exactly demo_1"):
            candidate.initialize({
                "episode_id": "short",
                "period": "morning_peak",
                "intersections": {"demo_1": {}},
            })
    finally:
        _clear_candidate(candidate)


def test_cv_joint_v1_checkpoint_policy_outputs_legal_masks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np
    from traffic_control.cov2x import deployment as candidate

    monkeypatch.setenv("COV2X_MODE", "eval")
    candidate.configure(aliases.resolve_model("cv_joint_v1"))
    try:
        policy = candidate._policy
        context = np.zeros(3300, dtype=np.float32)
        cloud_obs = np.zeros(65, dtype=np.float32)
        vehicle_obs = np.zeros(45, dtype=np.float32)
        cloud = policy.act_cloud(
            context, cloud_obs, 0, rng=None, deterministic=True
        )
        vehicle = policy.act_vehicle(
            context,
            vehicle_obs,
            0,
            np.asarray([True, True, False, False]),
            False,
            rng=None,
            deterministic=True,
        )
        assert cloud["action"] == 0
        assert vehicle["raw_speed"] == 0.0
        assert vehicle["reduction"] == 0.0
        assert vehicle["lane_action"] in (0, 1)
        assert vehicle["actor_mask"] is True
    finally:
        _clear_candidate(candidate)


def test_cv_joint_v1_synthetic_permit_exercises_speed_hold_and_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import math
    import torch
    from traffic_control.cov2x.model import JointPolicy
    from traffic_control.cov2x.communication.transport import (
        MessageBus,
        PermissionBook,
    )
    from traffic_control.cov2x.vehicle.speed_advice import (
        apply_temporary_base_relative_speed_advice,
    )

    torch.set_num_threads(1)
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_JOINT_ROAD", "off")
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    metadata, frame = _actual_map_inputs("morning_peak")
    policy = JointPolicy(199)
    with torch.no_grad():
        policy.cloud_actor.logit.weight.zero_()
        policy.cloud_actor.logit.bias.fill_(10.0)
        policy.vehicle_actor.speed_mean.weight.zero_()
        policy.vehicle_actor.speed_mean.bias.zero_()
        policy.vehicle_actor.lane_logits.weight.zero_()
        policy.vehicle_actor.lane_logits.bias.zero_()

    runtime.configure(policy=policy, period="morning_peak", training=False)
    try:
        first = runtime.initialize(deepcopy(metadata))
        response = runtime.step(deepcopy(frame))
        requests = response["actions"]["vehicles"]
        assert requests
        assert all("target_speed_mps" in request for request in requests.values())
        for request in requests.values():
            assert 0.0 < request["target_speed_mps"] <= 20.0
        data_before = runtime.collected()
        assert data_before["collection_mode"] == "deterministic"

        actual = {}
        for vehicle_id, request in requests.items():
            actual[vehicle_id] = {
                "requested": deepcopy(request),
                "actual_speed_mps": float(request["target_speed_mps"]),
                "actual_lane_index": int(
                    frame["vehicles"][vehicle_id]["location"]["lane_index"]
                ),
                "speed_status": "applied",
                "lane_change_status": None,
            }
        second_frame = deepcopy(frame)
        second_frame.update(
            step_id=2,
            simulation_time=10.0,
            previous_action_results={"step_id": 1, "vehicles": actual},
        )
        second_response = runtime.step(second_frame)
        assert len(runtime.collected()["receipts"]) == len(actual)
        assert second_response["v2x"]["snapshot_id"] == (
            f"{metadata['episode_id']}:2"
        )
        assert any(
            event["original_kind"] == "speed_permission"
            and event["event"] == "CONSUME"
            and event["snapshot_id"] == f"{metadata['episode_id']}:1"
            for event in second_response["v2x"]["events"]
        )

        base = 10.0
        capped = apply_temporary_base_relative_speed_advice(
            previous_advice_mps=None,
            base_speed_mps=base,
            latent_u=-1.0,
            delta_v_max_mps=base * 0.10,
        )
        held = apply_temporary_base_relative_speed_advice(
            previous_advice_mps=capped.target_speed_mps,
            base_speed_mps=base,
            latent_u=-1.0,
            delta_v_max_mps=base * 0.10,
        )
        released = apply_temporary_base_relative_speed_advice(
            previous_advice_mps=held.target_speed_mps,
            base_speed_mps=base,
            latent_u=0.0,
            delta_v_max_mps=base * 0.10,
        )
        assert capped.target_speed_mps == pytest.approx(9.0)
        assert held.target_speed_mps == pytest.approx(9.0)
        assert capped.release_native is False
        assert released.release_native is True
        assert released.target_speed_mps is None
        assert all(
            math.isfinite(value)
            for value in (
                capped.target_speed_mps,
                held.target_speed_mps,
            )
        )

        bus = MessageBus("permission-expiry")
        permissions = PermissionBook(bus)
        message = permissions.publish("movement", True, 0, 0.0, "policy-v1")
        assert permissions.current("movement", 14.999) is message
        assert permissions.current("movement", 15.0) is None
        assert any(event["event"] == "EXPIRE" for event in bus.events)
        runtime.finish({"reason": "completed", "simulation_time": 10.0})
        assert first["ready"] is True
    finally:
        if runtime._state.get("active"):
            runtime.finish({"reason": "test_cleanup", "simulation_time": 10.0})
        runtime._state.clear()
        runtime._config.clear()


def test_cv_joint_v1_package_dispatches_batch_drain_and_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traffic_control.cov2x.communication import V2XEventDrain
    import traffic_control.cov2x as package

    metadata, frame = _actual_map_inputs("off_peak")
    monkeypatch.delenv("COV2X_MODEL_ALIAS", raising=False)
    monkeypatch.setenv("COV2X_MODE", "eval")
    sink = V2XEventDrain()
    package.set_v2x_event_sink(sink)
    try:
        initialized = package.initialize(deepcopy(metadata))
        response = package.step(deepcopy(frame))
        assert initialized["candidate_id"] == "cv_joint_v1_generation_003"
        assert response["v2x"]["event_count"] > 0
        drained = package.drain_v2x_events()
        assert drained["event_count"] == response["v2x"]["event_count"]
        assert package.drain_v2x_events()["event_count"] == 0
        assert len(sink.snapshot()) == drained["event_count"]
    finally:
        try:
            package.finish({"reason": "completed", "simulation_time": 5.0})
        finally:
            package.set_v2x_event_sink(None)


def test_copied_traffic_control_package_runs_cv_without_algorithms(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "traffic_control"
    shutil.copytree(
        TRAFFIC_CONTROL_ROOT,
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    metadata, frame = _actual_map_inputs("morning_peak")
    (tmp_path / "metadata.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    (tmp_path / "frame.json").write_text(
        json.dumps(frame),
        encoding="utf-8",
    )
    script = """
import importlib.util
import json
import os

assert importlib.util.find_spec("algorithms") is None
os.environ["COV2X_MODE"] = "eval"
os.environ["IPPO_MODE"] = "model"
os.environ["IPPO_JOINT_ROAD"] = "off"

from traffic_control.cov2x import aliases
from traffic_control.cov2x import deployment as cv_joint_v1

metadata = json.load(open("metadata.json", encoding="utf-8"))
frame = json.load(open("frame.json", encoding="utf-8"))
cv_joint_v1.configure(aliases.resolve_model("cv_joint_v1"))
response = cv_joint_v1.initialize(metadata)
assert response["ready"] is True
step = cv_joint_v1.step(frame)
assert step["candidate_id"] == "cv_joint_v1_generation_003"
assert step["v2x"]["event_count"] > 0
cv_joint_v1.finish({"reason": "completed", "simulation_time": 5.0})
print("CV_JOINT_SELF_CONTAINED_PASS")
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "CV_JOINT_SELF_CONTAINED_PASS" in completed.stdout
