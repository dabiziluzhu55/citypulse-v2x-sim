"""Runtime subset decoupling tests for the deployable controller."""

from __future__ import annotations

import json

import numpy as np
import pytest

from traffic_control.ippo.controller import StateBuilder
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


def _subset_metadata(intersection_ids):
    return {
        "intersections": {
            iid: {
                "phase_order": [1, 2, 3, 4],
                "incoming_lanes": [f"{iid}_in_a", f"{iid}_in_b"],
                "outgoing_lanes": [f"{iid}_out_a"],
                "lanes": {
                    f"{iid}_in_a": {"role": "incoming", "length_m": 150.0, "speed_limit_mps": 13.9},
                    f"{iid}_in_b": {"role": "incoming", "length_m": 150.0, "speed_limit_mps": 13.9},
                    f"{iid}_out_a": {"role": "outgoing", "length_m": 150.0, "speed_limit_mps": 13.9},
                },
                "phases": {
                    "1": {"connection_priorities": {"c0": "protected"}},
                    "2": {"connection_priorities": {"c1": "protected"}},
                    "3": {"connection_priorities": {"c2": "protected"}},
                    "4": {"connection_priorities": {"c3": "protected"}},
                },
                "connections": [
                    {"connection_id": f"c{i}", "from_lane": f"{iid}_in_a", "to_lane": f"{iid}_out_a"}
                    for i in range(4)
                ],
            }
            for iid in intersection_ids
        }
    }


def test_state_dim_is_132_for_subset():
    builder = StateBuilder(_subset_metadata(("demo_3", "demo_5", "demo_6", "demo_9")))
    assert builder.max_state_dim == 132


def test_identity_one_hot_uses_canonical_slots():
    builder = StateBuilder(_subset_metadata(("demo_3", "demo_5")))
    frame = {
        "intersections": {
            "demo_3": {"current_phase": 1, "stage_elapsed": 5.0, "lanes": {}},
            "demo_5": {"current_phase": 1, "stage_elapsed": 5.0, "lanes": {}},
        }
    }
    states = builder.get_all_states(frame)
    assert set(states) == {"demo_3", "demo_5"}  # batch = active subset only
    identity3 = states["demo_3"][8 + 1 : 8 + 1 + 20]
    identity5 = states["demo_5"][8 + 1 : 8 + 1 + 20]
    assert np.argmax(identity3) == IDENTITY_SLOT_IDS.index("demo_3")
    assert np.argmax(identity5) == IDENTITY_SLOT_IDS.index("demo_5")
    assert identity3.sum() == 1.0 and identity5.sum() == 1.0


def test_unknown_intersection_rejected_at_construction():
    with pytest.raises(ValueError, match="not in the canonical"):
        StateBuilder(_subset_metadata(("demo_3", "demo_99")))

from traffic_control.ippo.contract import (
    OBSERVATION_SCHEMA,
    build_sidecar,
    sidecar_path,
)
from traffic_control.ippo.controller import initialize, load_checkpoint_metadata


def _write_checkpoint(path, checkpoint: dict) -> None:
    import torch

    torch.save(checkpoint, path)


def _sidecar_for(path, checkpoint, intersection_ids):
    metadata = _subset_metadata(intersection_ids)
    from traffic_control.ippo.controller import StateBuilder

    builder = StateBuilder(metadata)
    fingerprints = {
        iid: builder.get_fingerprint(iid) for iid in intersection_ids
    }
    return build_sidecar(path, checkpoint=checkpoint, fingerprints=fingerprints)


def _minimal_checkpoint(intersection_ids):
    from traffic_control.ippo.model import IPPONetwork

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
        "intersection_ids": list(intersection_ids),
        "model_state_dict": IPPONetwork(132, 4).state_dict(),
    }


def test_subset_checkpoint_load_with_sidecar(tmp_path, monkeypatch):
    """20-tl checkpoint (sidecar) must load for the east-4 subset."""
    from traffic_control.ippo import controller as mod

    checkpoint = _minimal_checkpoint(IDENTITY_SLOT_IDS)
    ckpt_path = tmp_path / "model.pt"
    _write_checkpoint(ckpt_path, checkpoint)
    sidecar = _sidecar_for(ckpt_path, checkpoint, IDENTITY_SLOT_IDS)
    sidecar_path(ckpt_path).write_text(json.dumps(sidecar), encoding="utf-8")

    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(ckpt_path))
    monkeypatch.setenv("IPPO_ACTION_INTERVAL", "15")
    metadata = _subset_metadata(("demo_3", "demo_5", "demo_6", "demo_9"))
    metadata["episode_id"] = "ep-subset"
    metadata["decision_interval"] = 5.0
    metadata["minimum_green"] = 5.0
    response = initialize(metadata)
    assert response["ready"] is True
    mod.finish({})
