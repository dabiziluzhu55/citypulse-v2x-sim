"""Identity slot and checkpoint contract v2 tests."""

from __future__ import annotations

import pytest

from traffic_control.ippo.identity import (
    IDENTITY_SLOT_IDS,
    identity_slots_for,
)


def test_identity_slots_are_explicit_20_slots():
    assert len(IDENTITY_SLOT_IDS) == 20
    assert IDENTITY_SLOT_IDS == tuple(f"demo_{i}" for i in range(1, 21))


def test_slots_for_subset_maps_to_canonical_positions():
    assert identity_slots_for(("demo_3", "demo_5", "demo_6", "demo_9")) == (2, 4, 5, 8)
    assert identity_slots_for(("demo_14", "demo_15", "demo_19")) == (13, 14, 18)


def test_slots_are_not_string_sorted():
    # "demo_10" must be slot 9, not slot 1 (string sort would put it second).
    assert identity_slots_for(("demo_10", "demo_2")) == (9, 1)


def test_unknown_intersection_rejected():
    with pytest.raises(ValueError, match="not in the canonical"):
        identity_slots_for(("demo_3", "demo_99"))

import hashlib
import json

from traffic_control.ippo.contract import (
    CHECKPOINT_CONTRACT_VERSION,
    OBSERVATION_SCHEMA,
    build_sidecar,
    fingerprint_sha256,
    intersection_fingerprint_from_index,
    load_contract,
    sidecar_path,
    validate_contract,
)


class _FakeIndex:
    """Duck-typed stand-in for controller._Idx with stable small values."""

    def __init__(self) -> None:
        self.phase_order = [1, 2]
        self.n_phases = 2
        self.lane_order = ["in_a", "in_b"]
        self.outgoing_order = ["out_a"]
        self.phase_connections = [[("in_a", "out_a")], [("in_b", "out_a")]]
        self.phase_movements = [[("e_in_a", "e_out_a")], [("e_in_b", "e_out_a")]]
        self.phase_durations = [30.0, 30.0]
        self.lane_capacities = {"in_a": 20.0, "in_b": 20.0}
        self.lane_speed_limits = {"in_a": 13.9, "in_b": 13.9}
        self.outgoing_capacities = {"out_a": 20.0}
        self.flow_reference_rate = 0.5


def _fake_checkpoint() -> dict:
    return {
        "model_version": "v8",
        "obs_dim": 132,
        "act_dim": 4,
        "action_interval": 15.0,
        "max_green_factor": 2.0,
        "phase_feature_schema": "connection_pressure_service_age_eta_demand_v2",
        "effective_demand_enabled": True,
        "reward_definition": "v5a_local_pressure",
        "training_periods": ["off_peak"],
        "training_seed_range": {"start": 88301, "end": 88460},
        "intersection_ids": list(IDENTITY_SLOT_IDS),
    }


def test_fingerprint_is_deterministic_and_compact():
    first = intersection_fingerprint_from_index(_FakeIndex())
    second = intersection_fingerprint_from_index(_FakeIndex())
    assert first == second
    assert fingerprint_sha256(first) == fingerprint_sha256(second)
    assert len(fingerprint_sha256(first)) == 64


def test_observation_schema_total_is_132():
    assert OBSERVATION_SCHEMA["total"] == 132
    assert OBSERVATION_SCHEMA["identity_slots"] == 20


def test_v1_checkpoint_without_sidecar_is_hard_error(tmp_path):
    checkpoint = _fake_checkpoint()
    path = tmp_path / "model.pt"
    path.write_bytes(b"not-a-real-torch-file")
    with pytest.raises(ValueError, match="no sidecar"):
        load_contract(path, checkpoint)


def test_sidecar_sha_mismatch_is_hard_error(tmp_path):
    checkpoint = _fake_checkpoint()
    path = tmp_path / "model.pt"
    path.write_bytes(b"bytes-v1")
    sidecar = build_sidecar(
        path,
        checkpoint=checkpoint,
        fingerprints={
            "demo_1": intersection_fingerprint_from_index(_FakeIndex())
        },
    )
    sidecar_path(path).write_text(json.dumps(sidecar), encoding="utf-8")
    path.write_bytes(b"bytes-v2-tampered")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_contract(path, checkpoint)


def test_validate_contract_subset_passes(tmp_path):
    checkpoint = _fake_checkpoint()
    fingerprints = {
        "demo_3": intersection_fingerprint_from_index(_FakeIndex()),
        "demo_5": intersection_fingerprint_from_index(_FakeIndex()),
    }
    path = tmp_path / "model.pt"
    path.write_bytes(b"dummy")
    sidecar = build_sidecar(
        path, checkpoint=checkpoint, fingerprints=fingerprints
    )
    validate_contract(
        sidecar,
        intersection_ids=("demo_3", "demo_5"),
        fingerprints=fingerprints,
        obs_dim=132,
        act_dim=4,
        action_interval=15.0,
        max_green_factor=2.0,
        phase_feature_schema="connection_pressure_service_age_eta_demand_v2",
        effective_demand_enabled=True,
        model_version="v8",
    )


def test_validate_contract_rejects_unknown_intersection(tmp_path):
    checkpoint = _fake_checkpoint()
    path = tmp_path / "model.pt"
    path.write_bytes(b"dummy")
    sidecar = build_sidecar(
        path,
        checkpoint=checkpoint,
        fingerprints={"demo_1": intersection_fingerprint_from_index(_FakeIndex())},
    )
    with pytest.raises(ValueError, match="not trained on this intersection subset"):
        validate_contract(
            sidecar,
            intersection_ids=("demo_99",),
            fingerprints={"demo_99": intersection_fingerprint_from_index(_FakeIndex())},
            obs_dim=132,
            act_dim=4,
            action_interval=15.0,
            max_green_factor=2.0,
            phase_feature_schema="connection_pressure_service_age_eta_demand_v2",
            effective_demand_enabled=True,
            model_version="v8",
        )


def test_validate_contract_rejects_fingerprint_mismatch(tmp_path):
    checkpoint = _fake_checkpoint()
    path = tmp_path / "model.pt"
    path.write_bytes(b"dummy")
    sidecar = build_sidecar(
        path,
        checkpoint=checkpoint,
        fingerprints={"demo_3": intersection_fingerprint_from_index(_FakeIndex())},
    )
    other = _FakeIndex()
    other.lane_order = ["in_a", "in_b", "in_c"]
    with pytest.raises(ValueError, match="topology fingerprint mismatch"):
        validate_contract(
            sidecar,
            intersection_ids=("demo_3",),
            fingerprints={"demo_3": intersection_fingerprint_from_index(other)},
            obs_dim=132,
            act_dim=4,
            action_interval=15.0,
            max_green_factor=2.0,
            phase_feature_schema="connection_pressure_service_age_eta_demand_v2",
            effective_demand_enabled=True,
            model_version="v8",
        )
