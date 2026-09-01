from __future__ import annotations

import copy

import numpy as np
import pytest
import torch

from algorithms.cov2x.collab.mvp_policy import MVPPolicyConfig, VehicleSpeedAdviceActor
from algorithms.cov2x.vehicle.movement_corridor import MovementApproachCorridor
from algorithms.cov2x.vehicle.speed_advice import (
    apply_incremental_speed_advice,
    reference_base_speed,
)
from algorithms.cov2x.vehicle.sticky_leader import StickyLeadCAV


def _metadata(*, conflicting: bool = False) -> dict:
    connections = [
        {
            "connection_id": "c0",
            "movement": "through",
            "from_lane": "terminal_0",
            "to_lane": "out_0",
        },
        {
            "connection_id": "c1",
            "movement": "left" if conflicting else "through",
            "from_lane": "terminal_1",
            "to_lane": "out_1",
        },
    ]
    return {
        "edge_lanes": {
            "upstream": [{"lane_id": "upstream_0", "edge_id": "upstream"}],
            "terminal": [
                {"lane_id": "terminal_0", "edge_id": "terminal"},
                {"lane_id": "terminal_1", "edge_id": "terminal"},
            ],
            "out": [
                {"lane_id": "out_0", "edge_id": "out"},
                {"lane_id": "out_1", "edge_id": "out"},
            ],
        },
        "intersections": {"tls": {"connections": connections}},
    }


def _vehicle(route_edges=("upstream", "terminal", "out"), route_index=0) -> dict:
    return {
        "location": {
            "lane_id": "upstream_0",
            "route_edges": list(route_edges),
            "route_index": route_index,
        },
        "motion": {"allowed_speed_mps": 11.0},
    }


def test_remaining_route_selects_earliest_controlled_transition() -> None:
    resolver = MovementApproachCorridor(_metadata())
    resolved = resolver.resolve("tls", _vehicle())

    assert resolved.predecessor_depth == 1
    assert resolved.route_transition_index == 1
    assert resolved.resolved_movement_id == "through"
    assert resolved.candidate_connection_ids == ("c0", "c1")
    assert resolved.controlled_terminal_lane_ids == ("terminal_0", "terminal_1")
    assert resolved.failure_reason is None
    assert resolved.route_intent_source == "simulation_side_route_intent"


def test_parallel_links_are_accepted_only_when_they_agree_on_movement() -> None:
    same = MovementApproachCorridor(_metadata()).resolve("tls", _vehicle())
    conflict = MovementApproachCorridor(_metadata(conflicting=True)).resolve(
        "tls", _vehicle()
    )

    assert same.resolved
    assert not conflict.resolved
    assert conflict.candidate_movement_ids == ("left", "through")
    assert conflict.failure_reason == "ambiguous_movement"


def test_no_route_match_has_no_lane_or_next_signal_fallback() -> None:
    resolver = MovementApproachCorridor(_metadata())
    resolved = resolver.resolve(
        "tls", _vehicle(route_edges=("upstream", "elsewhere"))
    )

    assert not resolved.resolved
    assert resolved.resolved_movement_id is None
    assert resolved.failure_reason == "no_route_consistent_controlled_transition"


def test_speed_ceiling_and_delta_use_vehicle_motion_allowed_speed() -> None:
    ceiling = reference_base_speed(11.0, 13.9)
    decision = apply_incremental_speed_advice(
        previous_advice_mps=None,
        base_speed_mps=ceiling,
        latent_u=-1.0,
        delta_v_max_mps=0.10 * ceiling,
        authority_gain=1.0,
        native_release_tolerance_mps=0.05,
    )

    assert ceiling == 11.0
    assert decision.requested_delta_mps == pytest.approx(-1.1)
    assert decision.target_speed_mps == pytest.approx(9.9)


def test_sticky_identity_is_exact_intersection_movement_pair() -> None:
    leaders = StickyLeadCAV()
    through = leaders.assign("tls", "through", "v0", now=0.0, lease_s=15.0)
    left = leaders.assign("tls", "left", "v1", now=0.0, lease_s=15.0)

    assert leaders.get("tls", "through") is through
    assert leaders.get("tls", "left") is left
    assert leaders.get("other", "through") is None
    assert {lease.identity_key for lease in leaders.active()} == {
        ("tls", "through"),
        ("tls", "left"),
    }


def test_fresh_vehicle_actor_has_exact_zero_mean_release_invariant() -> None:
    torch.manual_seed(14101)
    actor = VehicleSpeedAdviceActor(MVPPolicyConfig())
    trunk_before = copy.deepcopy(actor.trunk.state_dict())
    actor.initialize_corridor_generation_zero()
    features = np.zeros((64, MVPPolicyConfig.vehicle_feature_dim), dtype=np.float32)

    deterministic, _, _ = actor.sample(features, deterministic=True)
    stochastic, sampled_logprob, _ = actor.sample(features, deterministic=False)
    recomputed_logprob, _ = actor.log_prob(features, stochastic)

    assert all(
        torch.equal(trunk_before[name], value)
        for name, value in actor.trunk.state_dict().items()
    )
    assert torch.count_nonzero(actor.mean.weight) == 0
    assert torch.count_nonzero(actor.mean.bias) == 0
    assert actor.log_std.item() == pytest.approx(-1.0)
    assert torch.count_nonzero(deterministic) == 0
    assert torch.allclose(sampled_logprob, recomputed_logprob, atol=1e-6)
    assert 0.30 <= float((stochastic < -0.005).float().mean()) <= 0.70
