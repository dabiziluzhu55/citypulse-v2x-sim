import numpy as np
import pytest
import torch

from algorithms.cov2x.contract import DELTA_V_MAX_LANE_SPEED_FRACTION
from algorithms.cov2x.collab.mvp_policy import (
    MVPPolicyConfig,
    VehicleSpeedAdviceActor,
    vehicle_feature_vector,
)
from algorithms.cov2x.vehicle.speed_advice import (
    apply_incremental_speed_advice,
    apply_temporary_base_relative_speed_advice,
    reference_base_speed,
)
from algorithms.cov2x.vehicle.sticky_leader import StickyLeadCAV


def _apply(
    *,
    previous: float | None,
    base: float,
    latent: float,
    gain: float = 1.0,
):
    return apply_incremental_speed_advice(
        previous_advice_mps=previous,
        base_speed_mps=base,
        latent_u=latent,
        delta_v_max_mps=2.0,
        authority_gain=gain,
        native_release_tolerance_mps=0.05,
    )


def test_reference_base_speed_has_one_frozen_definition():
    assert reference_base_speed(13.9, 12.0) == 12.0
    assert reference_base_speed(8.0, 12.0) == 8.0
    assert reference_base_speed(-1.0, 12.0) == 0.0
    with pytest.raises(ValueError, match="finite"):
        reference_base_speed(float("nan"), 12.0)


def test_base_drop_projection_is_not_attributed_to_policy_delta():
    decision = _apply(previous=12.0, base=8.0, latent=0.0)

    assert decision.projected_advice_mps == 8.0
    assert decision.base_projection_delta_mps == -4.0
    assert decision.requested_delta_mps == 0.0
    assert decision.effective_delta_mps == 0.0
    assert decision.release_native
    assert decision.target_speed_mps is None


def test_neutral_action_holds_an_active_cap_but_releases_at_base():
    active = _apply(previous=8.0, base=10.0, latent=0.0)
    native = _apply(previous=None, base=10.0, latent=0.0)

    assert active.next_advice_mps == 8.0
    assert active.target_speed_mps == 8.0
    assert active.transition_kind == "active_cap"
    assert native.next_advice_mps is None
    assert native.target_speed_mps is None
    assert native.transition_kind == "native_release"


def test_latent_boundaries_saturate_cap_without_changing_action_identity():
    lower = _apply(previous=1.0, base=10.0, latent=-1.0)
    upper = _apply(previous=9.0, base=10.0, latent=1.0)
    staggered = _apply(previous=8.0, base=10.0, latent=-1.0, gain=0.25)

    assert lower.latent_u == -1.0
    assert lower.target_speed_mps == 0.0
    assert lower.effective_delta_mps == -1.0
    assert upper.latent_u == 1.0
    assert upper.release_native
    assert upper.effective_delta_mps == 1.0
    assert staggered.requested_delta_mps == -0.5
    assert staggered.target_speed_mps == 7.5


def test_tanh_normal_logprob_round_trip_uses_latent_action_only():
    torch.manual_seed(17)
    actor = VehicleSpeedAdviceActor(MVPPolicyConfig())
    features = np.zeros((5, MVPPolicyConfig.vehicle_feature_dim), dtype=np.float32)

    action, sampled_logprob, sampled_entropy = actor.sample(features)
    recomputed_logprob, recomputed_entropy = actor.log_prob(features, action)

    assert torch.all(action.abs() < 1.0)
    assert torch.allclose(sampled_logprob, recomputed_logprob, atol=1e-6)
    assert torch.allclose(sampled_entropy, recomputed_entropy, atol=1e-6)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {
                "previous_advice_mps": None,
                "base_speed_mps": 10.0,
                "latent_u": 1.1,
                "delta_v_max_mps": 2.0,
                "authority_gain": 1.0,
                "native_release_tolerance_mps": 0.05,
            },
            "latent_u",
        ),
        (
            {
                "previous_advice_mps": None,
                "base_speed_mps": 10.0,
                "latent_u": 0.0,
                "delta_v_max_mps": 2.0,
                "authority_gain": 0.0,
                "native_release_tolerance_mps": 0.05,
            },
            "authority_gain",
        ),
    ],
)
def test_invalid_speed_advice_contract_fails_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        apply_incremental_speed_advice(**kwargs)


def test_delta_v_limit_is_ten_percent_of_lane_speed_limit():
    lane_speed_limit = 13.9
    decision = apply_incremental_speed_advice(
        previous_advice_mps=10.0,
        base_speed_mps=lane_speed_limit,
        latent_u=-1.0,
        delta_v_max_mps=lane_speed_limit * DELTA_V_MAX_LANE_SPEED_FRACTION,
        authority_gain=1.0,
        native_release_tolerance_mps=0.05,
    )
    assert decision.requested_delta_mps == pytest.approx(-1.39)
def test_unavailable_relative_speed_is_explicitly_masked():
    common = {
        "speed_mps": 5.0,
        "accel_mps2": 0.0,
        "allowed_speed_mps": 13.9,
        "base_speed_mps": 13.9,
        "advice_speed_mps": 10.0,
        "advice_active": True,
        "distance_m": 40.0,
        "green": True,
        "signal_remaining_s": 0.0,
        "cloud_priority": 0.0,
    }
    unavailable = vehicle_feature_vector(**common)
    available = vehicle_feature_vector(**common, relative_speed_mps=3.0)

    assert unavailable[12] == 0.0
    assert unavailable[13] == 0.0
    assert available[12] == pytest.approx(0.2)
    assert available[13] == 1.0


def test_assignment_epoch_prevents_old_leader_advice_inheritance():
    leaders = StickyLeadCAV()
    first = leaders.assign("i", "left", "A", now=0.0, lease_s=15.0)
    first_key = ("i", "left", "A", first.assignment_epoch)
    advice_state = {first_key: 5.0}

    assert leaders.release("i", "left", "lease_completion")
    advice_state.pop(first_key)
    second = leaders.assign("i", "left", "B", now=15.0, lease_s=15.0)
    second_key = ("i", "left", "B", second.assignment_epoch)

    assert second.assignment_epoch > first.assignment_epoch
    assert second_key not in advice_state
    fresh = _apply(previous=None, base=12.0, latent=0.0)
    assert fresh.release_native
    assert fresh.next_advice_mps is None
    assert fresh.projected_advice_mps == 12.0


def test_scripted_advice_changes_only_the_next_snapshot():
    frozen_snapshot = {"speed_mps": 12.0, "base_speed_mps": 12.0}
    original = dict(frozen_snapshot)

    neutral = _apply(previous=None, base=12.0, latent=0.0)
    slower = _apply(previous=None, base=12.0, latent=-0.5)

    assert frozen_snapshot == original
    assert neutral.release_native
    assert slower.target_speed_mps == 11.0
    neutral_next_speed = frozen_snapshot["speed_mps"]
    slower_next_speed = min(
        frozen_snapshot["speed_mps"], float(slower.target_speed_mps)
    )
    assert slower_next_speed < neutral_next_speed


def _apply_temporary(
    *, previous: float | None, base: float, latent: float, delta: float = 2.0
):
    return apply_temporary_base_relative_speed_advice(
        previous_advice_mps=previous,
        base_speed_mps=base,
        latent_u=latent,
        delta_v_max_mps=delta,
    )


def test_temporary_negative_action_caps_current_base_for_one_decision():
    decision = _apply_temporary(previous=None, base=12.0, latent=-0.25)

    assert decision.requested_delta_mps == pytest.approx(-0.5)
    assert decision.projected_advice_mps == 12.0
    assert decision.target_speed_mps == pytest.approx(11.5)
    assert decision.effective_delta_mps == pytest.approx(-0.5)
    assert not decision.release_native


def test_temporary_zero_action_restores_current_native_base():
    decision = _apply_temporary(previous=11.5, base=13.0, latent=0.0)

    assert decision.projected_advice_mps == 13.0
    assert decision.requested_delta_mps == 0.0
    assert decision.effective_delta_mps == 0.0
    assert decision.release_native
    assert decision.target_speed_mps is None
    assert decision.next_advice_mps is None


def test_temporary_second_cap_is_recomputed_from_current_base():
    first = _apply_temporary(previous=None, base=12.0, latent=-0.5)
    second = _apply_temporary(
        previous=first.target_speed_mps, base=10.0, latent=-0.1
    )

    assert first.target_speed_mps == pytest.approx(11.0)
    assert second.projected_advice_mps == 10.0
    assert second.requested_delta_mps == pytest.approx(-0.2)
    assert second.target_speed_mps == pytest.approx(9.8)
    assert second.target_speed_mps != pytest.approx(10.8)


def test_temporary_positive_action_is_native_release():
    decision = _apply_temporary(previous=8.0, base=10.0, latent=0.75)

    assert decision.requested_delta_mps == pytest.approx(1.5)
    assert decision.effective_delta_mps == 0.0
    assert decision.release_native
    assert decision.target_speed_mps is None


def test_zero_mean_vehicle_initialization_and_action_identity_are_exact():
    torch.manual_seed(25500)
    actor = VehicleSpeedAdviceActor(MVPPolicyConfig())
    actor.initialize_local_credit_final(0.0)
    features = np.zeros((7, MVPPolicyConfig.vehicle_feature_dim), dtype=np.float32)

    deterministic, _, _ = actor.sample(features, deterministic=True)
    sampled, sampled_logprob, _ = actor.sample(features)
    recomputed_logprob, _ = actor.log_prob(features, sampled)

    assert torch.equal(deterministic, torch.zeros_like(deterministic))
    assert torch.allclose(sampled_logprob, recomputed_logprob, atol=1e-6)
