"""Regression tests for the opt-in IPPO period compatibility profile."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from traffic_control.ippo.contract import (
    CANDIDATE_SCORING_PERIOD_PROFILE,
    build_sidecar,
    intersection_fingerprint_from_index,
    load_contract,
    sidecar_path,
    validate_contract,
)


class _Index:
    def __init__(self) -> None:
        self.phase_order = [0, 1, 2]
        self.n_phases = 3
        self.lane_order = ["in_a", "in_b"]
        self.outgoing_order = ["out_a", "out_b"]
        self.phase_connections = [
            [("in_a", "out_a")],
            [("in_b", "out_a")],
            [("in_a", "out_b"), ("in_b", "out_b")],
        ]
        self.phase_movements = [
            [("edge_a", "edge_out_a")],
            [("edge_b", "edge_out_a")],
            [("edge_a", "edge_out_b"), ("edge_b", "edge_out_b")],
        ]
        self.phase_durations = [30.0, 30.0, 30.0]
        self.lane_capacities = {"in_a": 20.0, "in_b": 20.0}
        self.lane_speed_limits = {"in_a": 13.9, "in_b": 13.9}
        self.outgoing_capacities = {"out_a": 20.0, "out_b": 20.0}
        self.flow_reference_rate = 0.5


def _contract(tmp_path, *, profile: str | None):
    checkpoint_path = tmp_path / "model.pt"
    checkpoint_path.write_bytes(b"checkpoint")
    fingerprint = intersection_fingerprint_from_index(_Index())
    checkpoint = {
        "model_version": "v8",
        "obs_dim": 132,
        "act_dim": 4,
        "action_interval": 15.0,
        "max_green_factor": 2.0,
        "phase_feature_schema": "connection_pressure_service_age_eta_demand_v2",
        "effective_demand_enabled": True,
        "intersection_ids": ["demo_4"],
    }
    return build_sidecar(
        checkpoint_path,
        checkpoint=checkpoint,
        fingerprints={"demo_4": fingerprint},
        topology_compatibility_profile=profile,
    ), fingerprint


def _validate(contract, live_fingerprint) -> None:
    validate_contract(
        contract,
        intersection_ids=("demo_4",),
        fingerprints={"demo_4": live_fingerprint},
        obs_dim=132,
        act_dim=4,
        action_interval=15.0,
        max_green_factor=2.0,
        phase_feature_schema="connection_pressure_service_age_eta_demand_v2",
        effective_demand_enabled=True,
        model_version="v8",
    )


def test_strict_contract_still_rejects_period_changes(tmp_path):
    contract, live = _contract(tmp_path, profile=None)
    live = deepcopy(live)
    live["phase_durations"] = [38.0, 32.0, 30.0]
    with pytest.raises(ValueError, match="topology fingerprint mismatch"):
        _validate(contract, live)


def test_inline_contract_loads_only_hash_bound_sidecar_profile(tmp_path):
    contract, _live = _contract(tmp_path, profile=None)
    checkpoint_path = tmp_path / "model.pt"
    extension = dict(contract)
    extension["obs_dim"] = 999
    extension["topology_compatibility_profile"] = CANDIDATE_SCORING_PERIOD_PROFILE
    sidecar_path(checkpoint_path).write_text(
        json.dumps(extension), encoding="utf-8"
    )
    version, loaded = load_contract(checkpoint_path, contract)
    assert version == 2
    assert (
        loaded["topology_compatibility_profile"]
        == CANDIDATE_SCORING_PERIOD_PROFILE
    )
    assert loaded["obs_dim"] == 132


def test_inline_contract_rejects_unbound_sidecar_profile(tmp_path):
    contract, _live = _contract(tmp_path, profile=None)
    checkpoint_path = tmp_path / "model.pt"
    extension = dict(contract)
    extension["sha256"] = "0" * 64
    extension["topology_compatibility_profile"] = CANDIDATE_SCORING_PERIOD_PROFILE
    sidecar_path(checkpoint_path).write_text(
        json.dumps(extension), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Sidecar SHA-256 mismatch"):
        load_contract(checkpoint_path, contract)


def test_profile_accepts_duration_only_period_change(tmp_path):
    contract, live = _contract(
        tmp_path, profile=CANDIDATE_SCORING_PERIOD_PROFILE
    )
    live = deepcopy(live)
    live["phase_durations"] = [38.0, 32.0, 30.0]
    live["flow_reference_rate"] = 0.9
    _validate(contract, live)


def test_profile_accepts_phase_regrouping_with_same_movement_universe(tmp_path):
    contract, live = _contract(
        tmp_path, profile=CANDIDATE_SCORING_PERIOD_PROFILE
    )
    live = deepcopy(live)
    live["phase_order"] = [0, 1, 2, 3]
    live["n_phases"] = 4
    live["phase_connections"] = [
        [["in_a", "out_a"]],
        [["in_b", "out_a"]],
        [["in_a", "out_b"]],
        [["in_b", "out_b"]],
    ]
    live["phase_movements"] = [
        [["edge_a", "edge_out_a"]],
        [["edge_b", "edge_out_a"]],
        [["edge_a", "edge_out_b"]],
        [["edge_b", "edge_out_b"]],
    ]
    live["phase_durations"] = [38.0, 32.0, 32.0, 38.0]
    _validate(contract, live)


@pytest.mark.parametrize(
    "field,replacement",
    (
        ("lane_order", ["in_a", "in_changed"]),
        (
            "phase_movements",
            [
                [["edge_a", "edge_out_a"]],
                [["edge_b", "edge_out_a"]],
                [["edge_new", "edge_out_b"]],
            ],
        ),
    ),
)
def test_profile_rejects_roadway_or_movement_change(tmp_path, field, replacement):
    contract, live = _contract(
        tmp_path, profile=CANDIDATE_SCORING_PERIOD_PROFILE
    )
    live = deepcopy(live)
    live[field] = replacement
    with pytest.raises(ValueError, match="outside the candidate-scoring"):
        _validate(contract, live)


def test_profile_rejects_more_phases_than_checkpoint_action_dimension(tmp_path):
    contract, live = _contract(
        tmp_path, profile=CANDIDATE_SCORING_PERIOD_PROFILE
    )
    live = deepcopy(live)
    live["phase_order"] = [0, 1, 2, 3, 4]
    live["n_phases"] = 5
    live["phase_connections"] += [live["phase_connections"][0]] * 2
    live["phase_movements"] += [live["phase_movements"][0]] * 2
    live["phase_durations"] += [30.0, 30.0]
    with pytest.raises(ValueError, match="exceeds checkpoint act_dim"):
        _validate(contract, live)
