import numpy as np
import pytest
import torch

from algorithms.cov2x.collab.mvp_policy import CloudTanhNormalActor, MVPPolicyConfig
from algorithms.cov2x.contract import VehicleProfile, calibrate_delta_R, normalized_mp_scores, override_reachable, project_acceleration, road_logits
from algorithms.cov2x.reward_ledger import VehicleLifecycleLedger
from algorithms.cov2x.road.mp_prior import (
    RoadResidualConfig,
    RoadResidualNetwork,
    StrongMPPressureOracle,
    normalized_phase_prior,
)
from algorithms.cov2x.road import screen_controller
from algorithms.cov2x.smdp import SMDPTransition, smdp_gae
from algorithms.cov2x.transport import IdealPhasedTransport, TypedEnvelope
from algorithms.cov2x.vehicle.pooling import masked_movement_pool
from algorithms.cov2x.vehicle.sticky_leader import StickyLeadCAV


def test_normalized_prior_and_exact_reachability():
    scores = normalized_mp_scores({0: 10, 1: 8, 2: 2}, legal_phases=(0, 1))
    assert scores[0] > scores[1] >= 0
    assert normalized_mp_scores({0: 2, 1: 2}) == {0: 0.0, 1: 0.0}
    assert override_reachable({0: 0.5, 1: 0.0}, 1.0)
    assert not override_reachable({0: 1.0, 1: 0.0}, 0.5)


def test_calibration_uses_smallest_in_band():
    result = calibrate_delta_R([{0: 0.0, 1: 0.0}, {0: 1.0, 1: 0.0}, {0: 0.0, 1: 0.0}, {0: 1.0, 1: 0.0}])
    assert result.delta_R == 0.5 and result.reachable_rate == 0.5


def test_road_logits_and_cloud_conditioned_residual():
    assert road_logits({0: 1.0, 1: 0.5}, {0: -0.5, 1: 0.5}, delta_R=0.5) == {0: 3.5, 1: 2.5}
    network = RoadResidualNetwork(RoadResidualConfig(delta_R=0.5))
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.fill_(0.05)
    phase = np.zeros((2, 8), dtype=np.float32); movement = np.zeros((2, 8), dtype=np.float32)
    low = network.residuals(np.zeros(32), [-1.0], phase, movement)
    high = network.residuals(np.zeros(32), [1.0], phase, movement)
    assert not torch.allclose(low, high)
    assert float(high.detach().abs().max()) <= 0.5


def test_mp_prior_reuses_frozen_strong_mp_pressure_kernel():
    metadata = {
        "intersections": {
            "i": {
                "intersection_id": "i",
                "phase_order": [0, 1],
                "incoming_lanes": ["in_0", "in_1"],
                "outgoing_lanes": ["out_0"],
                "lanes": {
                    "in_0": {"edge_id": "in"},
                    "in_1": {"edge_id": "in"},
                    "out_0": {"edge_id": "out"},
                },
                "connections": [
                    {
                        "connection_id": "c0",
                        "from_lane": "in_0",
                        "to_lane": "out_0",
                        "movement": "through",
                    },
                    {
                        "connection_id": "c1",
                        "from_lane": "in_1",
                        "to_lane": "out_0",
                        "movement": "left",
                    },
                ],
                "phases": {
                    "0": {"connection_priorities": {"c0": "protected"}},
                    "1": {"connection_priorities": {"c1": "permissive"}},
                },
            }
        }
    }
    payload = {
        "intersections": {
            "i": {
                "lanes": {
                    "in_0": {"halting_count": 4},
                    "in_1": {"halting_count": 2},
                    "out_0": {"halting_count": 0},
                }
            }
        },
        "vehicles": {},
    }
    prior = normalized_phase_prior(
        payload,
        "i",
        (0, 1),
        oracle=StrongMPPressureOracle(metadata),
    )
    # Exact Strong-MP kernel: protected 4.0 vs permissive 1.0.
    assert prior[0] == pytest.approx(1.0)
    assert prior[1] == pytest.approx(0.0)


def test_screen_controller_records_same_prior_and_keeps_strong_mp_action():
    metadata = {
        "episode_id": "screen",
        "minimum_green": 5.0,
        "intersections": {
            "i": {
                "intersection_id": "i",
                "phase_order": [0, 1],
                "incoming_lanes": ["in_0", "in_1"],
                "outgoing_lanes": ["out_0"],
                "lanes": {},
                "connections": [
                    {"connection_id": "c0", "from_lane": "in_0", "to_lane": "out_0"},
                    {"connection_id": "c1", "from_lane": "in_1", "to_lane": "out_0"},
                ],
                "phases": {
                    "0": {"connection_priorities": {"c0": "protected"}},
                    "1": {"connection_priorities": {"c1": "protected"}},
                },
            }
        },
    }
    payload = {
        "episode_id": "screen",
        "step_id": 1,
        "intersections": {
            "i": {
                "current_phase": 1,
                "stage": "GREEN",
                "stage_elapsed": 10.0,
                "lanes": {
                    "in_0": {"halting_count": 4},
                    "in_1": {"halting_count": 1},
                    "out_0": {"halting_count": 0},
                },
            }
        },
        "vehicles": {},
    }
    screen_controller.initialize(metadata)
    response = screen_controller.step(payload)
    assert response["actions"]["signals"]["i"]["target_phase"] == 0
    screen_controller.finish({"episode_id": "screen"})
    scores = screen_controller.take_score_sets()
    assert scores == [{0: pytest.approx(1.0), 1: pytest.approx(0.0)}]


def test_ledger_retains_exit_value():
    ledger = VehicleLifecycleLedger()
    assert ledger.observe({"v1": 3.0, "v2": 2.0}) == 5.0
    assert ledger.observe({"v1": 4.0}) == 6.0
    assert ledger.retired_last_observed_total == 2.0
    assert ledger.observe({}) == 6.0
    with pytest.raises(ValueError, match="reappeared"):
        ledger.observe({"v1": 5.0})


def test_smdp_gae_uses_gamma_and_lambda_physical_time():
    gamma, lam = 0.99, 0.95
    steps = [
        SMDPTransition("road", "s0", None, None, 0.0, 0.0, 1.0, 15.0, next_value=0.0),
        SMDPTransition("road", "s1", None, None, 0.0, 0.0, 2.0, 5.0, done=True),
    ]
    advantages, returns = smdp_gae(steps, gamma=gamma, lam=lam)
    assert advantages[0] == pytest.approx(1.0 + (gamma ** 3) * (lam ** 3) * 2.0)
    assert np.isfinite(advantages).all() and np.isfinite(returns).all()


def test_sticky_leader_only_changes_after_explicit_completion():
    leader = StickyLeadCAV(); leader.assign("i", "left", "v1", now=0.0, lease_s=15.0)
    assert leader.assign("i", "left", "v2", now=5.0, lease_s=15.0).vehicle_id == "v1"
    leader.refresh_signal("i", "left", now=5.0, green_window=2.0)
    with pytest.raises(ValueError):
        leader.release("i", "left", "spat_update")
    assert leader.get("i", "left").vehicle_id == "v1"
    assert leader.release("i", "left", "lease_completion")


def test_transport_trace_ttl_and_causal_fail_closed():
    transport = IdealPhasedTransport()
    with pytest.raises(ValueError, match="unknown causal parent"):
        transport.send(TypedEnvelope("RegionalPriorityV1", "bad", "s1", "cloud", "i", 0.0, 15.0, "cloud", {"intersection_id": "i", "priority": 0.2}, causal_parents=("missing",)))
    message = TypedEnvelope("RegionalPriorityV1", "m1", "s1", "cloud", "i", 0.0, 15.0, "cloud", {"intersection_id": "i", "priority": 0.2})
    transport.send(message); assert transport.deliver("s1", "cloud", 1.0) == (message,)
    assert transport.consume(message, sim_time=1.0)["priority"] == 0.2
    assert [event.event for event in transport.trace()] == ["SEND", "DELIVER", "CONSUME"]


def test_vehicle_projection_and_masked_pooling():
    value, reason = project_acceleration(-4.0, profile=VehicleProfile(2.6, 4.5, -3.0), safe_lower_mps2=-2.0, safe_upper_mps2=2.0)
    assert value == -2.0 and reason == "projected"
    pooled = masked_movement_pool(np.asarray([[1, 2], [9, 9]], dtype=np.float32), [True, False])
    assert pooled.shape == (6,) and pooled[0] == 1.0 and pooled[-2] == 1.0


def test_cloud_actor_is_continuous_and_bounded():
    actor = CloudTanhNormalActor(MVPPolicyConfig())
    action, logprob, _ = actor.sample(np.zeros((2, 8), dtype=np.float32), ((0, 0), (1, 1), (0, 1)))
    assert action.shape == (2, 1) and logprob.shape == (2,)
    assert np.all(np.abs(action.detach().numpy()) <= 1.0)
