from argparse import Namespace
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from algorithms.coslight import controller as coslight
from algorithms.coslight import lane_state
from algorithms.coslight import train as coslight_train


def _lane_meta(lane_id, role, length=100.0, speed=15.0):
    edge_id, lane_index = lane_id.rsplit("_", 1)
    return {
        "lane_id": lane_id,
        "edge_id": edge_id,
        "lane_index": int(lane_index),
        "role": role,
        "length": length,
        "length_m": length,
        "max_speed": speed,
        "speed_limit_mps": speed,
        "downstream_lane_ids": [],
    }


def _metadata():
    return {
        "protocol_version": "2.0",
        "episode_id": "coslight-test",
        "minimum_green": 5.0,
        "intersections": {
            "a": {
                "phase_order": [10, 20],
                "incoming_lanes": ["a_in_0"],
                "outgoing_lanes": ["a_out_0"],
                "direct_neighbors": ["b"],
                "lanes": {
                    "a_in_0": _lane_meta("a_in_0", "incoming", length=100.0),
                    "a_out_0": _lane_meta("a_out_0", "outgoing", length=80.0),
                },
                "phases": {
                    "10": {"movement": "through", "green_seconds": 25.0},
                    "20": {"movement": "left", "green_seconds": 20.0},
                },
                "connections": [],
            },
            "b": {
                "phase_order": [30],
                "incoming_lanes": ["b_in_0"],
                "outgoing_lanes": ["b_out_0"],
                "direct_neighbors": ["a"],
                "lanes": {
                    "b_in_0": _lane_meta("b_in_0", "incoming", length=120.0),
                    "b_out_0": _lane_meta("b_out_0", "outgoing", length=90.0),
                },
                "phases": {
                    "30": {"movement": "through", "green_seconds": 30.0},
                },
                "connections": [],
            },
        },
        "edge_lanes": {
            "a_in": [{
                "lane_id": "a_in_0", "lane_index": 0, "length_m": 100.0,
                "allowed_vehicle_type_ids": ["passenger"],
            }],
            "a_out": [{
                "lane_id": "a_out_0", "lane_index": 0, "length_m": 80.0,
                "allowed_vehicle_type_ids": ["passenger"],
            }],
            "b_in": [{
                "lane_id": "b_in_0", "lane_index": 0, "length_m": 120.0,
                "allowed_vehicle_type_ids": ["passenger"],
            }],
            "b_out": [{
                "lane_id": "b_out_0", "lane_index": 0, "length_m": 90.0,
                "allowed_vehicle_type_ids": ["passenger"],
            }],
        },
        "vehicle_types": {"passenger": {"length_m": 5.0}},
    }


def _lane_obs(vehicle_count, occupancy=0.0, waiting=0.0):
    return {
        "vehicle_count": vehicle_count,
        "halting_count": min(vehicle_count, 1),
        "occupancy": occupancy,
        "queue_length_m": float(vehicle_count * 5),
        "mean_speed": 5.0,
        "waiting_time": waiting,
    }


def _step_payload(step_id, a_in, a_out, b_in, b_out, occupancy=0.0):
    return {
        "protocol_version": "2.0",
        "episode_id": "coslight-test",
        "step_id": step_id,
        "simulation_time": float(step_id * 5),
        "intersections": {
            "a": {
                "current_phase": 20,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 5.0,
                "lanes": {
                    "a_out_0": _lane_obs(a_out, occupancy=occupancy),
                    "a_in_0": _lane_obs(a_in, waiting=10.0),
                },
            },
            "b": {
                "current_phase": 30,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 5.0,
                "lanes": {
                    "b_in_0": _lane_obs(b_in, waiting=4.0),
                    "b_out_0": _lane_obs(b_out, occupancy=occupancy),
                },
            },
        },
        "vehicles": {},
    }


@pytest.fixture(autouse=True)
def reset_controller(monkeypatch, tmp_path):
    monkeypatch.setenv("COSLIGHT_MODE", "train")
    monkeypatch.setenv("COSLIGHT_TOP_K", "1")
    monkeypatch.setenv("COSLIGHT_MAX_GREEN_FACTOR", "2")
    monkeypatch.setenv("COSLIGHT_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.delenv("COSLIGHT_MODEL_PATH", raising=False)
    monkeypatch.delenv("COSLIGHT_RESUME_PATH", raising=False)
    monkeypatch.delenv("COSLIGHT_EPISODE_DURATION", raising=False)
    monkeypatch.delenv("COSLIGHT_PRESSURE_SHIELD_MARGIN", raising=False)
    monkeypatch.delenv("COSLIGHT_RESIDUAL_MIN_BEST_PRESSURE", raising=False)
    monkeypatch.delenv("COSLIGHT_SWITCH_LOGIT_MARGIN", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_MODE", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_TOPOLOGY", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_UPDATE_INTERVAL", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_MAX_WEIGHT", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_TARGET_QUEUE", raising=False)
    monkeypatch.delenv("COSLIGHT_CLOUD_SPILL_THRESHOLD", raising=False)
    coslight._reset_runtime_state()
    torch.manual_seed(0)
    np.random.seed(0)


def test_state_uses_real_phase_ids_stable_lane_order_and_normalized_features():
    coslight.initialize(_metadata())
    payload = _step_payload(0, a_in=18, a_out=2, b_in=3, b_out=1, occupancy=75.0)

    state = coslight.build_state(payload["intersections"])

    assert state.shape == (2, coslight.OBS_DIM)
    assert state[0, 1] == 1.0  # phase id 20 is the second configured action, not slot 20.
    assert state[0, 0] == 0.0
    assert np.isfinite(state).all()
    assert float(np.max(np.abs(state))) <= 1.0
    # Payload lane insertion order is outgoing then incoming; static metadata order wins.
    incoming_vehicle_ratio = state[0, coslight.MAX_PHASES + 1]
    outgoing_vehicle_ratio = state[0, coslight.MAX_PHASES + 1 + coslight.LANE_FEATURES]
    assert incoming_vehicle_ratio > outgoing_vehicle_ratio


def test_neutral_cloud_weights_are_bit_exact_with_v16_actor():
    mask = torch.ones((2, 2), dtype=torch.bool)
    model = coslight.CoSLightNetwork(
        2, coslight.OBS_DIM, 2, 1, neighbor_mask=mask
    ).eval()
    observations = torch.randn(1, 2, coslight.OBS_DIM)
    phase_features = torch.randn(1, 2, 2, coslight.PHASE_FEATURES)
    valid_counts = torch.tensor([2, 2])

    baseline = model.act(
        observations,
        valid_counts,
        deterministic=True,
        phase_features=phase_features,
    )
    neutral = model.act(
        observations,
        valid_counts,
        deterministic=True,
        phase_features=phase_features,
        pressure_prior_weights=torch.ones((1, 2, 2)),
    )

    assert torch.equal(baseline.actions, neutral.actions)
    assert torch.equal(baseline.action_log_probs, neutral.action_log_probs)


def test_cloud_weight_changes_only_the_explicit_pressure_prior_choice():
    model = coslight.CoSLightNetwork(
        1,
        coslight.OBS_DIM,
        2,
        1,
        neighbor_mask=torch.ones((1, 1), dtype=torch.bool),
    ).eval()
    with torch.no_grad():
        for parameter in model.actor_parameters():
            parameter.zero_()
    observations = torch.zeros((1, 1, coslight.OBS_DIM))
    phase_features = torch.zeros((1, 1, 2, coslight.PHASE_FEATURES))
    phase_features[0, 0, :, 5] = torch.tensor([1.0, 0.95])

    baseline = model.act(
        observations,
        2,
        deterministic=True,
        phase_features=phase_features,
    )
    cloud = model.act(
        observations,
        2,
        deterministic=True,
        phase_features=phase_features,
        pressure_prior_weights=torch.tensor([[[1.0, 1.2]]]),
    )

    assert baseline.actions.item() == 0
    assert cloud.actions.item() == 1


def test_cloud_direction_bonus_does_not_reverse_on_negative_pressure():
    model = coslight.CoSLightNetwork(
        1,
        coslight.OBS_DIM,
        2,
        1,
        neighbor_mask=torch.ones((1, 1), dtype=torch.bool),
    ).eval()
    with torch.no_grad():
        for parameter in model.actor_parameters():
            parameter.zero_()
    observations = torch.zeros((1, 1, coslight.OBS_DIM))
    phase_features = torch.zeros((1, 1, 2, coslight.PHASE_FEATURES))
    phase_features[0, 0, :, 5] = torch.tensor([-0.20, -0.25])

    baseline = model.act(
        observations,
        2,
        deterministic=True,
        phase_features=phase_features,
    )
    cloud = model.act(
        observations,
        2,
        deterministic=True,
        phase_features=phase_features,
        pressure_prior_weights=torch.tensor([[[1.0, 1.2]]]),
    )

    assert baseline.actions.item() == 0
    assert cloud.actions.item() == 1


def test_phase_features_follow_served_connections_in_action_order():
    metadata = _metadata()
    intersection = metadata["intersections"]["a"]
    intersection["incoming_lanes"].append("a_left_in_0")
    intersection["outgoing_lanes"].append("a_left_out_0")
    intersection["lanes"].update(
        {
            "a_left_in_0": _lane_meta(
                "a_left_in_0", "incoming", length=100.0
            ),
            "a_left_out_0": _lane_meta(
                "a_left_out_0", "outgoing", length=100.0
            ),
        }
    )
    intersection["connections"] = [
        {
            "connection_id": "a-through",
            "from_lane": "a_in_0",
            "to_lane": "a_out_0",
        },
        {
            "connection_id": "a-left",
            "from_lane": "a_left_in_0",
            "to_lane": "a_left_out_0",
        },
    ]
    intersection["phases"]["10"]["connection_priorities"] = {
        "a-through": "protected"
    }
    intersection["phases"]["20"]["connection_priorities"] = {
        "a-left": "protected"
    }
    coslight.initialize(metadata)
    payload = _step_payload(0, a_in=12, a_out=2, b_in=3, b_out=1)
    payload["intersections"]["a"]["lanes"].update(
        {
            "a_left_in_0": _lane_obs(2, waiting=2.0),
            "a_left_out_0": _lane_obs(8),
        }
    )

    features = coslight._state_builder.build_phase_features(
        payload["intersections"], coslight._tls_order, coslight._model.act_dim
    )

    assert features.shape == (2, 2, coslight.PHASE_FEATURES)
    # Phase 10 serves the busy through lane; phase 20 serves the quieter left lane.
    assert features[0, 0, 0] == pytest.approx(12 / 20)
    assert features[0, 1, 0] == pytest.approx(2 / 20)
    assert features[0, 0, 5] == pytest.approx(12 / 20 - 2 / 16)
    assert features[0, 1, 5] == pytest.approx(2 / 20 - 8 / 20)
    np.testing.assert_array_equal(features[0, :, 6], [0.0, 1.0])
    np.testing.assert_allclose(
        features[0, :, 7], [25.0 / 120.0, 20.0 / 120.0]
    )
    assert np.count_nonzero(features[1, :, :coslight.PHASE_TRAFFIC_FEATURES]) == 0
    assert features[1, 0, 6] == 1.0


def test_cloud_primary_connections_exclude_permissive_movements():
    metadata = _metadata()
    intersection = metadata["intersections"]["a"]
    intersection["incoming_lanes"].append("a_left_in_0")
    intersection["outgoing_lanes"].append("a_left_out_0")
    intersection["lanes"].update(
        {
            "a_left_in_0": _lane_meta(
                "a_left_in_0", "incoming", length=100.0
            ),
            "a_left_out_0": _lane_meta(
                "a_left_out_0", "outgoing", length=100.0
            ),
        }
    )
    intersection["connections"] = [
        {
            "connection_id": "a-through",
            "from_lane": "a_in_0",
            "to_lane": "a_out_0",
        },
        {
            "connection_id": "a-left",
            "from_lane": "a_left_in_0",
            "to_lane": "a_left_out_0",
        },
    ]
    intersection["phases"]["10"]["connection_priorities"] = {
        "a-through": "protected",
        "a-left": "permissive",
    }
    intersection["phases"]["20"]["connection_priorities"] = {
        "a-through": "permissive",
        "a-left": "protected",
    }

    state_builder = coslight.StateBuilder(metadata, ["a"])
    index = state_builder.indices["a"]

    assert set(index.phase_connections[0]) == {
        ("a_in_0", "a_out_0"),
        ("a_left_in_0", "a_left_out_0"),
    }
    assert index.phase_primary_connections[0] == (("a_in_0", "a_out_0"),)
    assert index.phase_primary_connections[1] == (
        ("a_left_in_0", "a_left_out_0"),
    )


def test_phase_scorer_adds_action_aligned_pressure_bias():
    model = coslight.CoSLightNetwork(
        num_agents=2,
        obs_dim=coslight.OBS_DIM,
        act_dim=2,
        top_k=1,
    )
    with torch.no_grad():
        for parameter in model.actor_head.parameters():
            parameter.zero_()
        model.phase_scorer.weight.zero_()
        model.phase_scorer.weight[0, 0] = 1.0
    encoded = torch.zeros((1, 2, model.hidden))
    collaborators = torch.zeros((1, 2, 1), dtype=torch.long)
    phase_features = torch.zeros((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[:, :, 1, 0] = 0.75

    logits = model._actor_logits(encoded, collaborators, phase_features)

    torch.testing.assert_close(
        logits[:, :, 1] - logits[:, :, 0],
        torch.full((1, 2), 0.75),
    )


def test_movement_pressure_feature_sums_each_served_connection():
    metadata = _metadata()
    intersection = metadata["intersections"]["a"]
    intersection["incoming_lanes"].append("a_left_in_0")
    intersection["outgoing_lanes"].append("a_left_out_0")
    intersection["lanes"].update(
        {
            "a_left_in_0": _lane_meta(
                "a_left_in_0", "incoming", length=100.0
            ),
            "a_left_out_0": _lane_meta(
                "a_left_out_0", "outgoing", length=100.0
            ),
        }
    )
    intersection["connections"] = [
        {
            "connection_id": "a-through",
            "from_lane": "a_in_0",
            "to_lane": "a_out_0",
        },
        {
            "connection_id": "a-left",
            "from_lane": "a_left_in_0",
            "to_lane": "a_left_out_0",
        },
    ]
    intersection["phases"]["10"]["connection_priorities"] = {
        "a-through": "protected",
        "a-left": "protected",
    }
    intersection["phases"]["20"]["connection_priorities"] = {
        "a-left": "protected"
    }
    coslight.initialize(metadata)
    payload = _step_payload(0, a_in=12, a_out=2, b_in=3, b_out=1)
    payload["intersections"]["a"]["lanes"].update(
        {
            "a_left_in_0": _lane_obs(2),
            "a_left_out_0": _lane_obs(8),
        }
    )

    features = coslight._state_builder.build_phase_features(
        payload["intersections"], coslight._tls_order, coslight._model.act_dim
    )

    through_pressure = 12 / 20 - 2 / 16
    left_pressure = 2 / 20 - 8 / 20
    assert features[0, 0, 5] == pytest.approx(
        through_pressure + left_pressure
    )
    assert features[0, 1, 5] == pytest.approx(left_pressure)


def test_phase_scorer_construction_preserves_rng_stream():
    torch.manual_seed(123)
    before = torch.get_rng_state().clone()

    coslight.PhaseFeatureScorer()

    torch.testing.assert_close(torch.get_rng_state(), before)


def test_fresh_phase_conditioned_policy_uses_pressure_and_hold_prior():
    model = coslight.CoSLightNetwork(
        num_agents=2,
        obs_dim=coslight.OBS_DIM,
        act_dim=2,
        top_k=1,
    )
    observations = torch.randn((1, 2, coslight.OBS_DIM))
    phase_features = torch.zeros((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[:, :, 1, 5] = 0.5

    output = model.act(
        observations,
        torch.tensor([2, 2]),
        deterministic=True,
        phase_features=phase_features,
    )
    encoded = model.encode(observations)
    logits = model._actor_logits(
        encoded, output.collaborators, phase_features
    )

    torch.testing.assert_close(
        logits[:, :, 1] - logits[:, :, 0],
        torch.full((1, 2), 0.5 * coslight.PRESSURE_PRIOR_SCALE),
    )

    phase_features.zero_()
    phase_features[:, :, 0, 6] = 1.0
    hold_logits = model._actor_logits(
        encoded, output.collaborators, phase_features
    )
    torch.testing.assert_close(
        hold_logits[:, :, 0] - hold_logits[:, :, 1],
        torch.full((1, 2), coslight.HOLD_PRIOR_BIAS),
    )


def test_shared_phase_head_scores_each_candidate_from_its_features():
    model = coslight.CoSLightNetwork(
        num_agents=2,
        obs_dim=coslight.OBS_DIM,
        act_dim=2,
        top_k=1,
    )
    with torch.no_grad():
        first_layer = model.phase_actor_head[0]
        final_layer = model.phase_actor_head[2]
        first_layer.weight.zero_()
        first_layer.bias.zero_()
        final_layer.weight.zero_()
        final_layer.bias.zero_()
        first_layer.weight[0, model.hidden * 2] = 1.0
        final_layer.weight[0, 0] = 1.0
    encoded = torch.zeros((1, 2, model.hidden))
    collaborators = torch.zeros((1, 2, 1), dtype=torch.long)
    phase_features = torch.zeros((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[:, :, 1, 0] = 0.75

    logits = model._actor_logits(encoded, collaborators, phase_features)

    torch.testing.assert_close(
        logits[:, :, 1] - logits[:, :, 0],
        torch.full((1, 2), 0.75),
    )


def test_state_builder_rejects_topologies_that_exceed_lane_slots():
    metadata = _metadata()
    lanes = [f"a_extra_{index}" for index in range(coslight.MAX_LANES + 1)]
    metadata["intersections"]["a"]["incoming_lanes"] = lanes
    metadata["intersections"]["a"]["outgoing_lanes"] = []
    metadata["intersections"]["a"]["lanes"] = {
        lane_id: _lane_meta(lane_id, "incoming") for lane_id in lanes
    }

    with pytest.raises(ValueError, match="maximum"):
        coslight.initialize(metadata)


def test_masked_phase_distribution_never_samples_nonexistent_phases():
    logits = torch.zeros((2, 2, 4))
    counts = torch.tensor([2, 1])
    dist = coslight._masked_categorical(logits, counts)

    np.testing.assert_allclose(dist.probs[0, 1].numpy(), [1.0, 0.0, 0.0, 0.0])
    samples = torch.stack([dist.sample() for _ in range(100)])
    assert bool(torch.all(samples[:, :, 0] < 2))
    assert bool(torch.all(samples[:, :, 1] == 0))


def test_action_mask_can_forbid_current_phase_without_unmasking_padding():
    logits = torch.zeros((1, 2, 4))
    counts = torch.tensor([2, 1])
    action_masks = torch.tensor(
        [[[True, False, False, False], [True, True, True, True]]]
    )

    dist = coslight._masked_categorical(logits, counts, action_masks)

    np.testing.assert_allclose(dist.probs[0, 0].numpy(), [1.0, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(dist.probs[0, 1].numpy(), [1.0, 0.0, 0.0, 0.0])


def test_phase_ppo_ratio_only_uses_the_executed_signal_action():
    log_ratio = torch.tensor([[np.log(1.25)]], dtype=torch.float32)
    zero = torch.zeros_like(log_ratio)
    advantage = torch.ones_like(log_ratio)

    loss, actual_log_ratio, ratio = coslight._phase_clipped_surrogate(
        log_ratio,
        zero,
        advantage,
    )

    torch.testing.assert_close(ratio, torch.tensor([[1.25]], dtype=torch.float32))
    torch.testing.assert_close(actual_log_ratio.exp(), ratio)
    assert float(loss) == pytest.approx(-1.2)


def test_max_green_mask_only_forces_multi_phase_intersections(monkeypatch):
    monkeypatch.setenv("COSLIGHT_MAX_GREEN_FACTOR", "2")
    coslight.initialize(_metadata())
    payload = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)
    payload["intersections"]["a"]["stage_elapsed"] = 60.0
    payload["intersections"]["b"]["stage_elapsed"] = 300.0

    masks, forced = coslight._build_action_masks(payload["intersections"])

    # ``a`` currently runs phase 20 (slot 1), whose cap is max(60, 2*20).
    np.testing.assert_array_equal(masks[0, :2], [True, False])
    # ``b`` has no alternative phase, so it must never be masked out.
    np.testing.assert_array_equal(masks[1, :1], [True])
    assert forced == {"a"}


def test_pressure_shield_only_keeps_near_optimal_legal_actions():
    base_masks = np.array(
        [[True, True, True], [True, False, False]], dtype=np.bool_
    )
    phase_features = np.zeros((2, 3, coslight.PHASE_FEATURES), dtype=np.float32)
    phase_features[0, :, 5] = [0.80, 0.74, 0.50]
    phase_features[1, :, 5] = [0.10, 9.00, 9.00]

    shielded = coslight._pressure_shield_action_masks(
        base_masks, phase_features, margin=0.10
    )

    np.testing.assert_array_equal(shielded[0], [True, True, False])
    # A padded/forbidden action is never restored even if its pressure is high.
    np.testing.assert_array_equal(shielded[1], [True, False, False])


def test_residual_guard_falls_back_only_for_weak_pressure_agents():
    action_masks = np.array(
        [[True, True, False], [True, True, False]], dtype=np.bool_
    )
    phase_features = np.zeros((2, 3, coslight.PHASE_FEATURES), dtype=np.float32)
    phase_features[0, :, 5] = [-0.01, -0.02, 9.0]
    phase_features[1, :, 5] = [0.20, 0.10, 9.0]

    guarded_masks, guarded = coslight._residual_activation_action_masks(
        action_masks,
        phase_features,
        reference_actions=np.array([0, 0]),
        min_best_pressure=0.0,
    )

    np.testing.assert_array_equal(guarded_masks[0], [True, False, False])
    np.testing.assert_array_equal(guarded_masks[1], [True, True, False])
    np.testing.assert_array_equal(guarded, [True, False])


def test_switch_hysteresis_holds_only_low_confidence_legal_switches():
    selected = np.array([0, 0])
    current = np.array([1, 1])
    selected_log_probs = np.array([-0.1, -0.1])
    current_log_probs = np.array([-0.2, -3.0])
    action_masks = np.array([[True, True], [True, False]])

    final, candidates, held, gaps = coslight._switch_hysteresis_actions(
        selected,
        current,
        selected_log_probs,
        current_log_probs,
        action_masks,
        margin=0.2,
    )

    np.testing.assert_array_equal(final, [1, 0])
    np.testing.assert_array_equal(candidates, [True, False])
    np.testing.assert_array_equal(held, [True, False])
    assert gaps[0] == pytest.approx(0.1)


def test_switch_hysteresis_keeps_high_confidence_switch_and_off_is_identity():
    selected = np.array([0])
    current = np.array([1])
    selected_log_probs = np.array([-0.1])
    current_log_probs = np.array([-1.0])
    action_masks = np.array([[True, True]])

    enabled, _, held, _ = coslight._switch_hysteresis_actions(
        selected,
        current,
        selected_log_probs,
        current_log_probs,
        action_masks,
        margin=0.2,
    )
    disabled, _, disabled_held, _ = coslight._switch_hysteresis_actions(
        selected,
        current,
        selected_log_probs,
        current_log_probs,
        action_masks,
        margin=-np.inf,
    )

    np.testing.assert_array_equal(enabled, selected)
    np.testing.assert_array_equal(disabled, selected)
    np.testing.assert_array_equal(held, [False])
    np.testing.assert_array_equal(disabled_held, [False])


def test_pressure_shield_diagnostics_report_filtering_and_selected_regret():
    coslight.initialize(_metadata())
    base_masks = np.array([[True, True], [True, False]], dtype=np.bool_)
    shielded_masks = np.array([[True, False], [True, False]], dtype=np.bool_)
    phase_features = np.zeros((2, 2, coslight.PHASE_FEATURES), dtype=np.float32)
    phase_features[0, :, 5] = [0.8, 0.5]
    phase_features[1, 0, 5] = 0.2

    coslight._record_pressure_shield_decision(
        base_masks,
        shielded_masks,
        phase_features,
        np.array([0, 0]),
    )
    diagnostics = coslight.signal_execution_diagnostics()

    assert diagnostics["pressure_shield_decisions"] == 1
    assert diagnostics["pressure_shield_candidate_actions"] == 3
    assert diagnostics["pressure_shield_allowed_actions"] == 2
    assert diagnostics["pressure_shield_filtered_actions"] == 1
    assert diagnostics["pressure_shield_filter_rate"] == pytest.approx(1 / 3)
    assert diagnostics["selected_pressure_regret_max"] == pytest.approx(0.0)
    assert diagnostics["maxpressure_reference_disagreement_count"] == 0


def test_pressure_shield_diagnostics_locate_maxpressure_tie_disagreement():
    coslight.initialize(_metadata())
    base_masks = np.array([[True, True], [True, False]], dtype=np.bool_)
    phase_features = np.zeros((2, 2, coslight.PHASE_FEATURES), dtype=np.float32)
    phase_features[0, :, 5] = [0.8, 0.8]
    phase_features[1, 0, 5] = 0.2

    coslight._record_pressure_shield_decision(
        base_masks,
        base_masks,
        phase_features,
        np.array([1, 0]),
        reference_actions=np.array([0, 0]),
        simulation_time=45.0,
        residual_guarded=np.array([False, True]),
    )
    diagnostics = coslight.signal_execution_diagnostics()

    assert diagnostics["selected_pressure_regret_positive_count"] == 0
    assert diagnostics["maxpressure_reference_disagreement_count"] == 1
    assert diagnostics["maxpressure_reference_disagreement_rate"] == pytest.approx(
        0.5
    )
    assert diagnostics["residual_guarded_agent_decisions"] == 1
    assert diagnostics["residual_guard_rate"] == pytest.approx(0.5)
    per_tls = diagnostics["pressure_shield_per_intersection"]["a"]
    assert per_tls["reference_disagreements"] == 1
    assert per_tls["zero_regret_reference_disagreements"] == 1
    assert per_tls["residual_guarded_count"] == 0
    assert diagnostics["pressure_shield_per_intersection"]["b"][
        "residual_guarded_count"
    ] == 1
    assert diagnostics["maxpressure_reference_disagreement_events"] == [
        {
            "time_s": 45.0,
            "tls_id": "a",
            "selected_phase": 20,
            "reference_phase": 10,
            "best_pressure": pytest.approx(0.8),
            "selected_pressure": pytest.approx(0.8),
            "reference_pressure": pytest.approx(0.8),
            "pressure_regret": 0.0,
        }
    ]


def test_max_pressure_uses_phase_connections_masks_and_current_phase_ties(
    monkeypatch,
):
    monkeypatch.setenv("COSLIGHT_MODE", "max_pressure")
    metadata = _metadata()
    intersection = metadata["intersections"]["a"]
    intersection["incoming_lanes"].append("a_left_in_0")
    intersection["outgoing_lanes"].append("a_left_out_0")
    intersection["lanes"].update(
        {
            "a_left_in_0": _lane_meta(
                "a_left_in_0", "incoming", length=100.0
            ),
            "a_left_out_0": _lane_meta(
                "a_left_out_0", "outgoing", length=100.0
            ),
        }
    )
    intersection["connections"] = [
        {
            "connection_id": "a-through",
            "from_lane": "a_in_0",
            "to_lane": "a_out_0",
        },
        {
            "connection_id": "a-left",
            "from_lane": "a_left_in_0",
            "to_lane": "a_left_out_0",
        },
    ]
    intersection["phases"]["10"]["connection_priorities"] = {
        "a-through": "protected"
    }
    intersection["phases"]["20"]["connection_priorities"] = {
        "a-left": "protected"
    }
    coslight.initialize(metadata)
    payload = _step_payload(0, a_in=16, a_out=2, b_in=3, b_out=1)
    payload["intersections"]["a"]["lanes"].update(
        {
            "a_left_in_0": _lane_obs(2),
            "a_left_out_0": _lane_obs(8),
        }
    )

    masks, _ = coslight._build_action_masks(payload["intersections"])
    actions = coslight._max_pressure_action_indices(
        payload["intersections"], masks
    )
    assert actions.tolist() == [0, 0]

    # Equal pressures retain phase 20 instead of generating a needless switch.
    payload["intersections"]["a"]["lanes"].update(
        {
            "a_in_0": _lane_obs(4),
            "a_out_0": _lane_obs(2),
            "a_left_in_0": _lane_obs(4),
            "a_left_out_0": _lane_obs(2),
        }
    )
    actions = coslight._max_pressure_action_indices(
        payload["intersections"], masks
    )
    assert actions[0] == 1

    # The same tie must leave phase 20 when max-green masks it out.
    payload["intersections"]["a"]["stage_elapsed"] = 60.0
    masks, forced = coslight._build_action_masks(payload["intersections"])
    actions = coslight._max_pressure_action_indices(
        payload["intersections"], masks
    )
    assert forced == {"a"}
    assert actions[0] == 0


def test_collaborator_policy_is_symmetric_and_can_select_self():
    model = coslight.CoSLightNetwork(num_agents=2, obs_dim=5, act_dim=3, top_k=1, hidden=16)
    with torch.no_grad():
        model.self_score_bias.fill_(20.0)
    encoded = model.encode(torch.randn(1, 2, 5))
    scores = model.collaboration_logits(encoded)
    selected, log_prob, entropy = model.select_collaborators(scores, deterministic=True)

    torch.testing.assert_close(scores, scores.transpose(-2, -1))
    assert selected.shape == (1, 2, 1)
    assert selected[0, :, 0].tolist() == [0, 1]
    assert log_prob.shape == (1, 2)
    assert entropy.shape == (1, 2)


def test_topology_attention_masks_non_neighbors_and_is_differentiable():
    neighbor_mask = torch.tensor(
        [
            [True, True, False],
            [True, True, True],
            [False, True, True],
        ]
    )
    model = coslight.CoSLightNetwork(
        num_agents=3,
        obs_dim=5,
        act_dim=2,
        top_k=2,
        hidden=16,
        neighbor_mask=neighbor_mask,
    )
    encoded = model.encode(torch.randn(2, 3, 5))

    context, collaborators, entropy, logits = model.topology_attention(encoded)
    probabilities = torch.softmax(logits, dim=-1)

    assert context.shape == (2, 3, 16)
    assert collaborators.shape == (2, 3, 2)
    assert entropy.shape == (2, 3)
    assert torch.all(probabilities[:, 0, 2] == 0)
    assert torch.all(probabilities[:, 2, 0] == 0)
    for target in range(3):
        assert torch.all(neighbor_mask[target, collaborators[:, target]])
    context.square().mean().backward()
    assert model.target_projection.weight.grad is not None
    assert model.source_projection.weight.grad is not None
    assert model.context_projection.weight.grad is not None


def test_learned_topk_can_select_non_neighbor_and_aggregates_only_selected():
    neighbor_mask = torch.eye(3, dtype=torch.bool)
    model = coslight.CoSLightNetwork(
        num_agents=3,
        obs_dim=4,
        act_dim=2,
        top_k=1,
        hidden=4,
        neighbor_mask=neighbor_mask,
    )
    with torch.no_grad():
        model.target_projection.weight.copy_(torch.eye(4))
        model.source_projection.weight.copy_(torch.eye(4))
        model.context_projection.weight.copy_(torch.eye(4))
        model.self_score_bias.fill_(-10.0)
    encoded = torch.tensor(
        [[[1.0, 0.0, 0.0, 0.0],
          [0.0, 1.0, 0.0, 0.0],
          [1.0, 0.1, 0.0, 0.0]]]
    )

    scores = model.learned_collaboration_logits(encoded)
    selected, _, _ = model.select_collaborators(scores, deterministic=True)
    context = model.selected_collaborator_context(encoded, selected)

    assert selected[0, 0, 0].item() == 2
    assert not neighbor_mask[0, selected[0, 0, 0]]
    torch.testing.assert_close(context[0, 0], encoded[0, 2])

    changed_unselected = encoded.clone()
    changed_unselected[0, 1] += 100.0
    changed_context = model.selected_collaborator_context(
        changed_unselected, selected
    )
    torch.testing.assert_close(context[0, 0], changed_context[0, 0])


def test_fresh_policy_executes_learned_topk_path():
    model = coslight.CoSLightNetwork(
        num_agents=3,
        obs_dim=4,
        act_dim=2,
        top_k=1,
        hidden=4,
        neighbor_mask=torch.eye(3, dtype=torch.bool),
    )
    with torch.no_grad():
        model.self_score_bias.fill_(-100.0)
        output = model.act(
            torch.randn(2, 3, 4),
            valid_action_counts=torch.full((2, 3), 2),
            deterministic=True,
        )

    target = torch.arange(3).reshape(1, 3, 1)
    assert model.use_learned_topk_collaboration
    assert torch.all(output.collaborators != target)
    assert torch.isfinite(output.collaborator_log_probs).all()
    assert torch.all(output.collaborator_log_probs < 0.0)


def test_training_rejects_topk_that_selects_every_intersection(monkeypatch):
    monkeypatch.setenv("COSLIGHT_TOP_K", "2")

    with pytest.raises(ValueError, match="leave at least one"):
        coslight.initialize(_metadata())


def test_topology_counterfactual_probe_returns_valid_phase_probabilities():
    coslight.initialize(_metadata())
    observations = torch.zeros((3, 2, coslight.OBS_DIM))
    valid_counts = torch.full((3, 2), 2)
    action_masks = torch.ones((3, 2, 2), dtype=torch.bool)
    phase_features = torch.zeros((3, 2, 2, coslight.PHASE_FEATURES))

    probabilities, argmax_actions = coslight._probe_topology_policy(
        observations,
        valid_counts,
        action_masks,
        phase_features,
    )

    assert probabilities.shape == (3, 2, 2)
    assert argmax_actions.shape == (3, 2)
    torch.testing.assert_close(
        probabilities.sum(dim=-1), torch.ones((3, 2))
    )


def test_collaborator_ppo_ratio_updates_selection_policy():
    torch.manual_seed(23)
    model = coslight.CoSLightNetwork(
        num_agents=2,
        obs_dim=4,
        act_dim=2,
        top_k=1,
        hidden=4,
        neighbor_mask=torch.eye(2, dtype=torch.bool),
    )
    optimizer = torch.optim.Adam(model.actor_parameters(), lr=3e-3)
    encoded = torch.tensor(
        [[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]]
    ).repeat(16, 1, 1)
    selections = torch.tensor([[[0], [1]]]).repeat(16, 1, 1)

    with torch.no_grad():
        old_scores = model.learned_collaboration_logits(encoded)
        _, old_log_probs, _ = model.select_collaborators(
            old_scores, selections=selections
        )
        before_diagonal = torch.diagonal(
            torch.softmax(old_scores, dim=-1), dim1=-2, dim2=-1
        ).mean()

    scores = model.learned_collaboration_logits(encoded)
    _, new_log_probs, _ = model.select_collaborators(
        scores, selections=selections
    )
    loss, _, _ = coslight._collaborator_clipped_surrogate(
        new_log_probs,
        old_log_probs,
        torch.ones_like(new_log_probs),
    )
    optimizer.zero_grad()
    loss.backward()
    assert model.self_score_bias.grad is not None
    assert float(model.self_score_bias.grad.abs()) > 0.0
    optimizer.step()

    with torch.no_grad():
        after_scores = model.learned_collaboration_logits(encoded)
        after_diagonal = torch.diagonal(
            torch.softmax(after_scores, dim=-1), dim1=-2, dim2=-1
        ).mean()
    assert after_diagonal > before_diagonal


def test_learned_collaboration_logits_are_not_cosine_bounded():
    model = coslight.CoSLightNetwork(
        num_agents=2,
        obs_dim=4,
        act_dim=2,
        top_k=1,
        hidden=4,
    )
    with torch.no_grad():
        model.target_projection.weight.copy_(torch.eye(4) * 4.0)
        model.source_projection.weight.copy_(torch.eye(4) * 4.0)
        model.self_score_bias.zero_()
    encoded = torch.tensor(
        [[[2.0, 0.0, 0.0, 0.0], [2.0, 0.0, 0.0, 0.0]]]
    )

    logits = model.learned_collaboration_logits(encoded)

    assert torch.all(logits > 1.0)


def test_actor_has_no_non_neighbor_information_before_topology_attention():
    neighbor_mask = torch.tensor(
        [
            [True, True, False],
            [True, True, False],
            [False, False, True],
        ]
    )
    model = coslight.CoSLightNetwork(
        num_agents=3,
        obs_dim=5,
        act_dim=2,
        top_k=2,
        hidden=16,
        neighbor_mask=neighbor_mask,
    )
    observations = torch.randn(1, 3, 5)
    changed = observations.clone()
    changed[:, 2] += 100.0

    encoded = model.encode(observations)
    changed_encoded = model.encode(changed)
    context, collaborators, _, _ = model.topology_attention(encoded)
    changed_context, changed_collaborators, _, _ = model.topology_attention(
        changed_encoded
    )
    phase_features = torch.zeros(1, 3, 2, coslight.PHASE_FEATURES)
    logits = model._actor_logits(
        encoded,
        collaborators,
        phase_features,
        collaborator_context=context,
    )
    changed_logits = model._actor_logits(
        changed_encoded,
        changed_collaborators,
        phase_features,
        collaborator_context=changed_context,
    )

    torch.testing.assert_close(encoded[:, 0], changed_encoded[:, 0])
    torch.testing.assert_close(context[:, 0], changed_context[:, 0])
    torch.testing.assert_close(logits[:, 0], changed_logits[:, 0])
    assert not torch.allclose(encoded[:, 2], changed_encoded[:, 2])


def test_neighbor_scope_contains_self_and_caps_direct_neighbors():
    mask = coslight._build_neighbor_mask(
        ["a", "b", "c", "d"],
        {
            "a": ["b", "c", "d"],
            "b": ["a"],
            "c": [],
            "d": ["missing"],
        },
        top_k=2,
    )

    assert mask.tolist() == [
        [True, True, False, False],
        [True, True, False, False],
        [False, False, True, False],
        [False, False, False, True],
    ]


def test_reward_is_attached_to_previous_joint_action_and_uses_percent_occupancy(
    monkeypatch,
):
    monkeypatch.setenv("COSLIGHT_REWARD_MODE", "legacy_delta")
    coslight.initialize(_metadata())
    first = _step_payload(0, a_in=10, a_out=1, b_in=6, b_out=1, occupancy=50.0)
    first_state = coslight.build_state(first["intersections"])
    first_result = coslight.step(first)

    assert coslight._episode_trajectory["obs"] == []
    assert first_result["actions"]["signals"]["a"]["target_phase"] in (10, 20)
    assert first_result["actions"]["signals"]["b"]["target_phase"] == 30

    second = _step_payload(3, a_in=7, a_out=2, b_in=4, b_out=2, occupancy=50.0)
    coslight.step(second)
    trajectory = coslight._episode_trajectory

    assert len(trajectory["obs"]) == 1
    np.testing.assert_array_equal(trajectory["obs"][0], first_state)
    # 50 percent occupancy is below the 70 percent spillback threshold.
    assert np.all(np.asarray(trajectory["raw_rewards"]) > 0.0)


def test_pressure_reward_penalizes_current_absolute_imbalance(monkeypatch):
    monkeypatch.setenv("COSLIGHT_REWARD_MODE", "pressure")
    coslight.initialize(_metadata())
    moderate = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)
    congested = _step_payload(1, a_in=15, a_out=1, b_in=15, b_out=1)

    moderate_reward = coslight.compute_reward(moderate)
    congested_reward = coslight.compute_reward(congested)

    assert np.all(moderate_reward < 0.0)
    assert np.all(congested_reward < moderate_reward)


def test_stage1_pressure_reward_does_not_include_spillback_penalty(monkeypatch):
    monkeypatch.setenv("COSLIGHT_REWARD_MODE", "pressure")
    coslight.initialize(_metadata())
    low_occupancy = _step_payload(
        0, a_in=8, a_out=2, b_in=8, b_out=2, occupancy=10.0
    )
    high_occupancy = _step_payload(
        1, a_in=8, a_out=2, b_in=8, b_out=2, occupancy=95.0
    )

    low_reward = coslight.compute_reward(low_occupancy)
    high_reward = coslight.compute_reward(high_occupancy)

    np.testing.assert_allclose(high_reward, low_reward)


@pytest.mark.parametrize(
    ("percentage", "expected"),
    [(0.8, 0.008), (1.0, 0.01), (1.01, 0.0101)],
)
def test_occupancy_unit_is_always_protocol_percentage(percentage, expected):
    assert coslight._occupancy_ratio(percentage) == pytest.approx(expected)


def test_signal_decision_waits_for_safe_controller_boundary():
    coslight.initialize(_metadata())
    too_early = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)
    for intersection in too_early["intersections"].values():
        intersection["stage_elapsed"] = 0.0

    early_result = coslight.step(too_early)
    assert early_result["actions"]["signals"] == {}
    assert coslight._pending_transition is None

    eligible = _step_payload(1, a_in=5, a_out=1, b_in=5, b_out=1)
    decision = coslight.step(eligible)
    stored = coslight._pending_transition
    assert set(decision["actions"]["signals"]) == {"a", "b"}
    assert stored is not None

    transitioning = _step_payload(2, a_in=4, a_out=1, b_in=4, b_out=1)
    transitioning["intersections"]["a"].update(
        {"stage": "YELLOW", "stage_elapsed": 1.0, "pending_phase": 10}
    )
    held = coslight.step(transitioning)
    assert held["actions"]["signals"] == {}
    assert coslight._pending_transition is stored
    assert coslight._episode_trajectory["obs"] == []

    settled_too_soon = _step_payload(3, a_in=3, a_out=1, b_in=3, b_out=1)
    held_for_fixed_horizon = coslight.step(settled_too_soon)
    assert held_for_fixed_horizon["actions"]["signals"] == {}
    assert coslight._pending_transition is stored
    assert coslight._episode_trajectory["obs"] == []

    settled = _step_payload(4, a_in=3, a_out=1, b_in=3, b_out=1)
    next_decision = coslight.step(settled)
    assert set(next_decision["actions"]["signals"]) == {"a", "b"}
    assert len(coslight._episode_trajectory["obs"]) == 1


def test_step_builds_vehicle_actions_once(monkeypatch):
    monkeypatch.setenv("COSLIGHT_VEHICLE_GUIDANCE", "rule")
    coslight.initialize(_metadata())
    calls = 0

    def fake_vehicle_actions(_payload):
        nonlocal calls
        calls += 1
        return {}

    monkeypatch.setattr(coslight, "_build_vehicle_actions", fake_vehicle_actions)
    coslight.step(_step_payload(0, a_in=1, a_out=0, b_in=1, b_out=0))
    assert calls == 1


def test_default_mode_preserves_training_behavior(monkeypatch):
    monkeypatch.delenv("COSLIGHT_MODE")
    coslight.initialize(_metadata())

    assert coslight._mode == "train"
    assert coslight._model is not None
    assert coslight._actor_optimizer is not None
    assert coslight._critic_optimizer is not None


def test_vehicle_speed_guidance_respects_protocol_allowed_speed():
    coslight.initialize(_metadata())
    payload = _step_payload(0, a_in=1, a_out=0, b_in=0, b_out=0)
    payload["vehicles"] = {
        "slow-zone": {
            "type_id": "passenger",
            "motion": {"speed_mps": 3.0, "allowed_speed_mps": 1.5},
            "location": {
                "road_id": "a_in",
                "lane_id": "a_in_0",
                "lane_index": 0,
            },
            "next_signal": {
                "intersection_id": "a",
                "distance_m": 100.0,
                "state": "RED",
            },
        }
    }

    actions = coslight._build_vehicle_actions(payload)

    assert actions["slow-zone"]["target_speed_mps"] == 1.5


@pytest.mark.parametrize("speed", [0.0, 1.0, 2.0])
def test_red_signal_guidance_never_accelerates_slow_vehicle(speed):
    coslight.initialize(_metadata())
    payload = _step_payload(0, a_in=1, a_out=0, b_in=0, b_out=0)
    payload["vehicles"] = {
        "slow": {
            "type_id": "passenger",
            "motion": {"speed_mps": speed, "allowed_speed_mps": 15.0},
            "location": {
                "road_id": "a_in",
                "lane_id": "a_in_0",
                "lane_index": 0,
            },
            "next_signal": {
                "intersection_id": "a",
                "distance_m": 100.0,
                "state": "RED",
            },
        }
    }

    actions = coslight._build_vehicle_actions(payload)

    assert "slow" not in actions or actions["slow"]["target_speed_mps"] <= speed


def test_joint_batch_keeps_agents_from_the_same_timestep_together():
    n, d, k = 2, 4, 1
    trajectory = {
        "obs": [np.full((n, d), fill_value=t, dtype=np.float32) for t in (1, 2, 3)],
        "actions": [np.array([0, 0])] * 3,
        "action_log_probs": [np.zeros(n, dtype=np.float32)] * 3,
        "collaborators": [np.zeros((n, k), dtype=np.int64)] * 3,
        "collaborator_log_probs": [np.zeros(n, dtype=np.float32)] * 3,
        "values": [np.zeros(n, dtype=np.float32)] * 3,
        "normalized_values": [
            np.full(n, 0.25, dtype=np.float32)
        ] * 3,
        "rewards": [np.array([1.0, 2.0], dtype=np.float32)] * 3,
        "raw_rewards": [np.array([1.0, 2.0], dtype=np.float32)] * 3,
        "valid_action_counts": [np.array([2, 1], dtype=np.int64)] * 3,
        "action_masks": [
            np.array([[True, True], [True, False]], dtype=np.bool_)
        ] * 3,
        "last_values": np.zeros(n, dtype=np.float32),
    }

    batch = coslight._build_training_batch([trajectory])

    assert batch["obs"].shape == (3, n, d)
    for timestep in batch["obs"]:
        assert np.unique(timestep).size == 1
    assert batch["advantages"].shape == (3, n)
    np.testing.assert_allclose(batch["normalized_values"], 0.25)


def test_ppo_update_processes_joint_timestep_minibatches(monkeypatch):
    coslight.initialize(_metadata())
    n, d, k = 2, coslight.OBS_DIM, 1
    obs = np.random.default_rng(0).normal(size=(3, n, d)).astype(np.float32)
    with torch.no_grad():
        rollout = coslight._model.act(torch.from_numpy(obs), torch.tensor([2, 1]))
    trajectory = {
        "obs": list(obs),
        "actions": list(rollout.actions.numpy()),
        "action_log_probs": list(rollout.action_log_probs.numpy()),
        "collaborators": list(rollout.collaborators.numpy()),
        "collaborator_log_probs": list(rollout.collaborator_log_probs.numpy()),
        "values": list(rollout.values.numpy()),
        "rewards": [np.array([1.0, 0.5], dtype=np.float32)] * 3,
        "raw_rewards": [np.array([1.0, 0.5], dtype=np.float32)] * 3,
        "valid_action_counts": [np.array([2, 1], dtype=np.int64)] * 3,
        "action_masks": [
            np.array([[True, True], [True, False]], dtype=np.bool_)
        ] * 3,
        "last_values": np.zeros(n, dtype=np.float32),
    }
    coslight._buffer_episodes.append(trajectory)
    monkeypatch.setattr(coslight, "ACCUMULATE_EPISODES", 1)
    monkeypatch.setattr(coslight, "PPO_EPOCHS", 1)
    monkeypatch.setattr(coslight, "BATCH_SIZE", 2)

    seen_shapes = []
    original_evaluate = coslight._model.evaluate_actions

    def checked_evaluate(obs_t, *args, **kwargs):
        seen_shapes.append(tuple(obs_t.shape))
        assert obs_t.ndim == 3 and obs_t.shape[1] == n
        return original_evaluate(obs_t, *args, **kwargs)

    monkeypatch.setattr(coslight._model, "evaluate_actions", checked_evaluate)
    coslight._ppo_update()

    assert seen_shapes == [(2, n, d), (1, n, d)]
    assert coslight.policy_generation() == 1
    assert all(torch.isfinite(parameter).all() for parameter in coslight._model.parameters())


def test_value_normalizer_round_trips_raw_returns():
    normalizer = coslight.RunningValueNorm()
    normalizer.update(np.array([-4.0, -2.0, 0.0, 2.0], dtype=np.float32))
    raw = torch.tensor([[-5.0, -1.0, 3.0]], dtype=torch.float32)

    normalized = normalizer.normalize(raw)
    restored = normalizer.denormalize(normalized)

    torch.testing.assert_close(restored, raw)
    assert normalizer.count == 4
    assert normalizer.std > 0.0


def test_actor_and_critic_optimizers_have_disjoint_parameters():
    coslight.initialize(_metadata())

    actor_ids = {
        id(parameter)
        for group in coslight._actor_optimizer.param_groups
        for parameter in group["params"]
    }
    critic_ids = {
        id(parameter)
        for group in coslight._critic_optimizer.param_groups
        for parameter in group["params"]
    }

    assert actor_ids
    assert critic_ids
    assert actor_ids.isdisjoint(critic_ids)
    assert actor_ids | critic_ids == {
        id(parameter) for parameter in coslight._model.parameters()
    }


def test_phase_scorer_uses_dedicated_learning_rate_group():
    coslight.initialize(_metadata())

    groups = coslight._actor_optimizer.param_groups
    assert [group["lr"] for group in groups] == pytest.approx(
        [
            coslight.ACTOR_LR,
            coslight.ACTOR_LR * coslight.PHASE_SCORER_LR_MULTIPLIER,
        ]
    )
    scorer_ids = {id(parameter) for parameter in coslight._model.phase_scorer.parameters()}
    assert {
        id(parameter) for parameter in groups[1]["params"]
    } == scorer_ids
    assert scorer_ids.isdisjoint(
        {id(parameter) for parameter in groups[0]["params"]}
    )


def test_synthetic_advantages_move_phase_probabilities_in_correct_direction():
    torch.manual_seed(17)
    batch = 16
    model = coslight.CoSLightNetwork(
        num_agents=1,
        obs_dim=coslight.OBS_DIM,
        act_dim=2,
        top_k=1,
        neighbor_mask=torch.ones((1, 1), dtype=torch.bool),
    )
    optimizer = torch.optim.Adam(model.actor_parameters(), lr=3e-3)
    observations = torch.zeros((batch, 1, coslight.OBS_DIM))
    valid_counts = torch.full((batch, 1), 2, dtype=torch.long)
    action_masks = torch.ones((batch, 1, 2), dtype=torch.bool)
    phase_features = torch.zeros(
        (batch, 1, 2, coslight.PHASE_FEATURES)
    )
    phase_features[:, :, 0, 0] = 1.0
    phase_features[:, :, 1, 0] = -1.0
    actions = torch.zeros((batch, 1), dtype=torch.long)
    actions[batch // 2 :] = 1
    advantages = torch.ones((batch, 1))
    advantages[batch // 2 :] = -1.0
    collaborators = torch.zeros((batch, 1, 1), dtype=torch.long)

    with torch.no_grad():
        before = model.act(
            observations,
            valid_counts,
            deterministic=True,
            action_masks=action_masks,
            phase_features=phase_features,
        )
        old_log_probabilities = model.evaluate_actions(
            observations,
            actions,
            valid_counts,
            collaborators,
            action_masks,
            phase_features,
        ).action_log_probs.detach()
        before_probabilities = torch.exp(
            model.evaluate_actions(
                observations,
                actions,
                valid_counts,
                collaborators,
                action_masks,
                phase_features,
            ).action_log_probs
        )

    output = model.evaluate_actions(
        observations,
        actions,
        valid_counts,
        collaborators,
        action_masks,
        phase_features,
    )
    loss, _, _ = coslight._phase_clipped_surrogate(
        output.action_log_probs, old_log_probabilities, advantages
    )
    optimizer.zero_grad()
    loss.backward()
    assert model.phase_scorer.weight.requires_grad
    assert model.phase_scorer.weight.grad is not None
    assert float(model.phase_scorer.weight.grad.norm()) > 0.0
    optimizer.step()

    with torch.no_grad():
        after_probabilities = torch.exp(
            model.evaluate_actions(
                observations,
                actions,
                valid_counts,
                collaborators,
                action_masks,
                phase_features,
            ).action_log_probs
        )

    assert before.actions.shape == (batch, 1)
    assert torch.all(
        after_probabilities[: batch // 2]
        > before_probabilities[: batch // 2]
    )
    assert torch.all(
        after_probabilities[batch // 2 :]
        < before_probabilities[batch // 2 :]
    )


def test_rollout_stores_denormalized_critic_values():
    coslight.initialize(_metadata())
    coslight._value_normalizer.load_state_dict(
        {"mean": 10.0, "m2": 8.0, "count": 2}
    )
    payload = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)
    observations = coslight.build_state(payload["intersections"])
    with torch.no_grad():
        normalized_values = coslight._model.values(
            torch.from_numpy(observations).unsqueeze(0)
        )
        expected = coslight._value_normalizer.denormalize(
            normalized_values
        ).squeeze(0)

    coslight.step(payload)

    np.testing.assert_allclose(
        coslight._pending_transition["values"],
        expected.numpy(),
        rtol=1e-6,
        atol=1e-6,
    )
    np.testing.assert_allclose(
        coslight._pending_transition["normalized_values"],
        normalized_values.squeeze(0).numpy(),
        rtol=1e-6,
        atol=1e-6,
    )


def test_error_finish_discards_rollout_and_completed_finish_keeps_it():
    coslight.initialize(_metadata())
    coslight.step(_step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1))
    coslight.step(_step_payload(3, a_in=4, a_out=1, b_in=4, b_out=1))
    coslight.finish({"reason": "error", "departed_vehicles": 1, "arrived_vehicles": 0})
    assert coslight._buffer_episodes == []

    coslight.initialize(_metadata())
    coslight.step(_step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1))
    coslight.step(_step_payload(3, a_in=4, a_out=1, b_in=4, b_out=1))
    coslight.finish({"reason": "completed", "departed_vehicles": 1, "arrived_vehicles": 0})
    assert len(coslight._buffer_episodes) == 1


def test_lane_state_builder_emits_downstream_green_without_name_error():
    metadata = _metadata()
    metadata["intersections"] = {"a": metadata["intersections"]["a"]}
    metadata["intersections"]["a"]["connections"] = [{
        "from_lane": "a_in_0", "to_lane": "a_out_0", "movement": "through",
    }]
    metadata["intersections"]["a"]["lanes"]["a_in_0"]["downstream_lane_ids"] = ["a_out_0"]
    lane_state.build_static_indices(metadata, metadata["edge_lanes"])
    payload = {
        "simulation_time": 10.0,
        "intersections": {
            "a": {
                "current_phase": 10,
                "stage": "GREEN",
                "stage_elapsed": 6.0,
                "lanes": {
                    "a_in_0": {
                        **_lane_obs(2, occupancy=0.8),
                        "signal_state": "GREEN",
                    },
                    "a_out_0": {**_lane_obs(1), "signal_state": "GREEN"},
                },
            }
        },
        "vehicles": {
            "v": {
                "type_id": "passenger",
                "motion": {"speed_mps": 5.0, "allowed_speed_mps": 15.0, "acceleration_mps2": 0.0},
                "location": {
                    "road_id": "a_in", "lane_id": "a_in_0", "lane_index": 0,
                    "lane_position_m": 40.0, "route_edges": ["a_in", "a_out"],
                },
                "time_since_last_lane_change_s": 20.0,
            }
        },
    }

    batch = lane_state.build_lane_actor_batch(payload, {"a": 10})

    assert batch.states.shape[1] == lane_state.LANE_STATE_DIM
    active = np.flatnonzero(batch.slot_mask)
    assert active.size == 1
    assert batch.states[active[0], 15] == pytest.approx(0.008)
    assert batch.states[active[0], 32] == 1.0


def test_lane_state_uses_sumo_left_and_right_index_direction():
    metadata = _metadata()
    metadata["edge_lanes"]["road"] = [
        {
            "lane_id": f"road_{index}",
            "lane_index": index,
            "length_m": 100.0,
            "allowed_vehicle_type_ids": ["passenger"],
        }
        for index in range(3)
    ]
    lane_state.build_static_indices(metadata, metadata["edge_lanes"])
    vehicle = {
        "type_id": "passenger",
        "motion": {"speed_mps": 5.0},
        "location": {
            "road_id": "road",
            "lane_id": "road_1",
            "lane_index": 1,
            "route_edges": ["road"],
        },
    }

    assert lane_state._lane_neighbors["road_1"] == {
        "left": "road_2",
        "right": "road_0",
    }
    np.testing.assert_array_equal(
        lane_state._build_action_mask(vehicle), [True, True, True]
    )


def test_checkpoint_payload_can_resume_training(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._value_normalizer.update(
        np.array([-5.0, -3.0, 2.0], dtype=np.float32)
    )
    checkpoint = tmp_path / "resume.pt"
    coslight._save_checkpoint(checkpoint)
    payload = coslight.load_checkpoint_metadata(checkpoint)
    assert payload["format_version"] == coslight.CHECKPOINT_FORMAT_VERSION
    assert (
        payload["training_config"]["policy_objective"]
        == coslight.POLICY_OBJECTIVE
    )
    assert (
        payload["training_config"]["phase_feature_schema"]
        == coslight.PHASE_FEATURE_SCHEMA
    )
    assert (
        payload["training_config"]["policy_architecture"]
        == coslight.POLICY_ARCHITECTURE
    )
    assert (
        payload["training_config"]["collaboration_schema"]
        == coslight.COLLABORATION_SCHEMA
    )
    assert (
        payload["training_config"]["pressure_prior_scale"]
        == coslight.PRESSURE_PRIOR_SCALE
    )
    assert (
        payload["training_config"]["hold_prior_bias"]
        == coslight.HOLD_PRIOR_BIAS
    )
    assert (
        payload["training_config"]["actor_encoder_scope"]
        == coslight.ACTOR_ENCODER_SCOPE
    )
    assert payload["training_config"]["reward_schema"] == coslight.REWARD_SCHEMA
    assert (
        payload["training_config"]["terminal_transition_schema"]
        == coslight.TERMINAL_TRANSITION_SCHEMA
    )
    assert payload["training_config"]["spillback_coef"] == 0.0
    assert (
        payload["training_config"]["phase_scorer_lr_multiplier"]
        == coslight.PHASE_SCORER_LR_MULTIPLIER
    )
    torch.testing.assert_close(
        payload["neighbor_mask"], coslight._model.neighbor_mask
    )
    old_state = {name: value.clone() for name, value in coslight._model.state_dict().items()}
    old_value_stats = coslight.export_value_stats()

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    coslight.initialize(_metadata())

    for name, value in coslight._model.state_dict().items():
        torch.testing.assert_close(value, old_state[name])
    assert coslight.export_value_stats() == old_value_stats
    assert coslight._actor_optimizer is not None
    assert coslight._critic_optimizer is not None


def test_training_resume_rejects_pre_value_norm_checkpoint(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "legacy-critic.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 5
    payload["training_config"].pop("value_normalization")
    payload.pop("value_normalization")
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="value normalization"):
        coslight.initialize(_metadata())


def test_training_resume_rejects_pre_horizon_guard_checkpoint(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "pre-horizon-guard.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 14
    payload["training_config"].pop("terminal_transition_schema")
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="terminal-transition schema"):
        coslight.initialize(_metadata())


def test_training_resume_rejects_separate_policy_objective(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "separate-objective.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 6
    payload["training_config"].pop("policy_objective")
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="policy objective"):
        coslight.initialize(_metadata())


def test_training_resume_rejects_pre_phase_feature_checkpoint(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "pre-phase-features.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 7
    payload["training_config"].pop("phase_feature_schema")
    payload["model_state_dict"].pop("phase_scorer.weight")
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="phase-feature schema"):
        coslight.initialize(_metadata())


def test_v7_checkpoint_remains_inference_compatible(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._model.use_pressure_prior = False
    coslight._model.use_local_actor_encoder = False
    observations = torch.zeros((1, 2, coslight.OBS_DIM))
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        collaborators, _, _ = coslight._model.select_collaborators(
            coslight._model.collaboration_logits(encoded),
            deterministic=True,
        )
        expected_logits = coslight._model._actor_logits(
            encoded, collaborators
        )
    checkpoint = tmp_path / "v7-inference.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 7
    payload["training_config"].pop("phase_feature_schema")
    payload["training_config"].pop("policy_architecture")
    payload["model_state_dict"].pop("phase_scorer.weight")
    for name in tuple(payload["model_state_dict"]):
        if name.startswith("phase_actor_head."):
            payload["model_state_dict"].pop(name)
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    coslight.initialize(_metadata())

    torch.testing.assert_close(
        coslight._model.phase_scorer.weight,
        torch.zeros_like(coslight._model.phase_scorer.weight),
    )
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        collaborators, _, _ = coslight._model.select_collaborators(
            coslight._model.collaboration_logits(encoded),
            deterministic=True,
        )
        actual_logits = coslight._model._actor_logits(
            encoded,
            collaborators,
            torch.ones((1, 2, 2, coslight.PHASE_FEATURES)),
        )
    torch.testing.assert_close(actual_logits, expected_logits)


def test_v8_checkpoint_keeps_legacy_actor_logits(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._model.use_pressure_prior = False
    coslight._model.use_local_actor_encoder = False
    with torch.no_grad():
        coslight._model.actor_head[-1].bias.copy_(
            torch.tensor([0.2, -0.1])
        )
        coslight._model.phase_scorer.weight.copy_(
            torch.tensor([[0.3, 0.0, 0.0, 0.0, 0.0, -0.2]])
        )
    observations = torch.zeros((1, 2, coslight.OBS_DIM))
    phase_features = torch.ones((1, 2, 2, coslight.PHASE_FEATURES))
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        collaborators, _, _ = coslight._model.select_collaborators(
            coslight._model.collaboration_logits(encoded),
            deterministic=True,
        )
        expected_logits = coslight._model._actor_logits(
            encoded, collaborators, phase_features
        )
    checkpoint = tmp_path / "v8-inference.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 8
    payload["training_config"]["phase_feature_schema"] = "connection_pressure_v1"
    payload["training_config"].pop("policy_architecture")
    for name in tuple(payload["model_state_dict"]):
        if name.startswith("phase_actor_head."):
            payload["model_state_dict"].pop(name)
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    coslight.initialize(_metadata())
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        collaborators, _, _ = coslight._model.select_collaborators(
            coslight._model.collaboration_logits(encoded),
            deterministic=True,
        )
        actual_logits = coslight._model._actor_logits(
            encoded, collaborators, phase_features
        )

    torch.testing.assert_close(actual_logits, expected_logits)


def test_v10_checkpoint_keeps_pre_prior_topology_logits(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._model.use_pressure_prior = False
    coslight._model.use_local_actor_encoder = False
    observations = torch.zeros((1, 2, coslight.OBS_DIM))
    phase_features = torch.zeros((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[:, :, 1, 5] = 0.75
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        context, collaborators, _, _ = coslight._model.topology_attention(
            encoded
        )
        expected_logits = coslight._model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=context,
        )
    checkpoint = tmp_path / "v10-inference.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 10
    payload["training_config"]["phase_feature_schema"] = (
        "connection_pressure_v2"
    )
    payload["training_config"]["policy_architecture"] = (
        "topology_soft_attention_phase_head_v1"
    )
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    coslight.initialize(_metadata())

    assert not coslight._model.use_legacy_collaboration
    assert not coslight._model.use_learned_topk_collaboration
    assert not coslight._model.use_pressure_prior
    assert (
        coslight._state_builder.phase_feature_schema
        == "connection_pressure_v2"
    )
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        context, collaborators, _, _ = coslight._model.topology_attention(
            encoded
        )
        actual_logits = coslight._model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=context,
        )
    torch.testing.assert_close(actual_logits, expected_logits)


def test_v11_checkpoint_restores_mean_pressure_prior_semantics(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    coslight._model.use_pressure_prior = True
    coslight._model.use_local_actor_encoder = False
    coslight._model.pressure_prior_scale = 0.5
    coslight._model.hold_prior_bias = 0.05
    observations = torch.zeros((1, 2, coslight.OBS_DIM))
    phase_features = torch.zeros((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[:, :, 1, 5] = 0.75
    phase_features[:, :, 0, 6] = 1.0
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        context, collaborators, _, _ = coslight._model.topology_attention(
            encoded
        )
        expected_logits = coslight._model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=context,
        )
    checkpoint = tmp_path / "v11-inference.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 11
    payload["training_config"].update(
        {
            "phase_feature_schema": "connection_pressure_v2",
            "policy_architecture": "pressure_anchored_topology_residual_v2",
            "pressure_prior_scale": 0.5,
            "hold_prior_bias": 0.05,
        }
    )
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    coslight.initialize(_metadata())

    assert coslight._model.use_pressure_prior
    assert coslight._model.pressure_prior_scale == pytest.approx(0.5)
    assert coslight._model.hold_prior_bias == pytest.approx(0.05)
    assert (
        coslight._state_builder.phase_feature_schema
        == "connection_pressure_v2"
    )
    with torch.no_grad():
        encoded = coslight._model.encode(observations)
        context, collaborators, _, _ = coslight._model.topology_attention(
            encoded
        )
        actual_logits = coslight._model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=context,
        )
    torch.testing.assert_close(actual_logits, expected_logits)


def test_v16_checkpoint_keeps_direct_neighbor_inference(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._model.use_learned_topk_collaboration = False
    observations = torch.randn((1, 2, coslight.OBS_DIM))
    phase_features = torch.randn((1, 2, 2, coslight.PHASE_FEATURES))
    phase_features[..., 6:] = 0.0
    with torch.no_grad():
        expected = coslight._model.act(
            observations,
            valid_action_counts=torch.full((1, 2), 2),
            deterministic=True,
            phase_features=phase_features,
        )
    checkpoint = tmp_path / "v16-inference.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = 16
    payload["training_config"].update(
        {
            "policy_objective": "phase_action_ratio",
            "policy_architecture": "movement_pressure_local_topology_residual_v4",
            "collaboration_schema": "direct_neighbors_softmax_v1",
            "actor_encoder_scope": "local_then_topology_neighbors_v1",
        }
    )
    # A real V16 file predates the V17 collab-gate parameters.
    payload["model_state_dict"] = {
        name: value
        for name, value in payload["model_state_dict"].items()
        if not name.startswith("collab_bias.")
    }
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    coslight.initialize(_metadata())

    assert not coslight._model.use_legacy_collaboration
    assert not coslight._model.use_learned_topk_collaboration
    with torch.no_grad():
        output = coslight._model.act(
            observations,
            valid_action_counts=torch.full((1, 2), 2),
            deterministic=True,
            phase_features=phase_features,
        )
    torch.testing.assert_close(output.action_logits, expected.action_logits)
    torch.testing.assert_close(output.actions, expected.actions)
    torch.testing.assert_close(output.collaborators, expected.collaborators)
    torch.testing.assert_close(
        output.collaborator_log_probs,
        torch.zeros_like(output.collaborator_log_probs),
    )


def test_training_resume_rejects_missing_value_stats(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "missing-value-stats.pt"
    payload = coslight._checkpoint_payload()
    payload.pop("value_normalization")
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="value-normalization statistics"):
        coslight.initialize(_metadata())


def test_checkpoint_rejects_phase_semantic_mismatch(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "resume.pt"
    coslight._save_checkpoint(checkpoint)
    mismatched = _metadata()
    mismatched["intersections"]["a"]["phase_order"] = [11, 20]

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="checkpoint.*runtime"):
        coslight.initialize(mismatched)


def test_resume_rejects_reward_semantic_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("COSLIGHT_REWARD_MODE", "pressure")
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "pressure.pt"
    coslight._save_checkpoint(checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_REWARD_MODE", "legacy_delta")
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="reward mode"):
        coslight.initialize(_metadata())


def test_resume_rejects_max_green_semantic_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("COSLIGHT_MAX_GREEN_FACTOR", "2")
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "max-green.pt"
    coslight._save_checkpoint(checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MAX_GREEN_FACTOR", "1.5")
    monkeypatch.setenv("COSLIGHT_RESUME_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="max-green factor"):
        coslight.initialize(_metadata())


def test_checkpoint_rejects_pressure_prior_semantic_mismatch(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "prior-mismatch.pt"
    payload = coslight._checkpoint_payload()
    payload["training_config"]["pressure_prior_scale"] = 99.0
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="pressure_prior_scale mismatch"):
        coslight.initialize(_metadata())


def test_checkpoint_rejects_current_format_phase_schema_mismatch(
    monkeypatch, tmp_path
):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "schema-mismatch.pt"
    payload = coslight._checkpoint_payload()
    payload["training_config"]["phase_feature_schema"] = (
        "connection_pressure_v2"
    )
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="phase_feature_schema mismatch"):
        coslight.initialize(_metadata())


def test_checkpoint_rejects_future_format(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    checkpoint = tmp_path / "future.pt"
    payload = coslight._checkpoint_payload()
    payload["format_version"] = coslight.CHECKPOINT_FORMAT_VERSION + 1
    torch.save(payload, checkpoint)

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "model")
    monkeypatch.setenv("COSLIGHT_MODEL_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="newer than this runtime"):
        coslight.initialize(_metadata())


def test_finalize_training_flushes_tail_rollouts(monkeypatch, tmp_path):
    coslight.initialize(_metadata())
    coslight._buffer_episodes.append({"sentinel": True})
    processed = []

    def fake_update(episode_count=None):
        processed.append(episode_count)
        coslight._buffer_episodes.clear()

    monkeypatch.setattr(coslight, "_ppo_update", fake_update)
    checkpoint = tmp_path / "final.pt"

    result = coslight.finalize_training(checkpoint)

    assert processed == [1]
    assert result == checkpoint
    assert checkpoint.is_file()


def test_training_timeout_stops_active_session(monkeypatch, tmp_path):
    class TimeoutManager:
        instance = None

        def __init__(self):
            self.wait_calls = 0
            self.stopped = False
            TimeoutManager.instance = self

        def start(self, _config):
            return "timeout-session"

        def wait(self, _session_id, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise TimeoutError("synthetic timeout")
            return SimpleNamespace(state="STOPPED")

        def snapshot(self, _session_id):
            return SimpleNamespace(state="RUNNING")

        def stop(self, _session_id):
            self.stopped = True

    monkeypatch.setattr(coslight_train, "SimulationManager", TimeoutManager)
    monkeypatch.setattr(
        coslight_train,
        "_parse_args",
        lambda: Namespace(
            episodes=1,
            duration=1,
            seed=42,
            top_k=1,
            reward_mode="pressure",
            max_green_factor=2.0,
            save=tmp_path / "final.pt",
            checkpoint_dir=tmp_path / "checkpoints",
            resume=None,
            vehicle_guidance="off",
        ),
    )

    assert coslight_train.main() == 1
    assert TimeoutManager.instance.stopped is True
    assert TimeoutManager.instance.wait_calls == 2


def test_collector_exports_raw_joint_rollout_without_local_update(monkeypatch):
    monkeypatch.setenv("COSLIGHT_MODE", "collect")
    coslight.prepare_collector(
        policy_state=None,
        policy_seed=42,
        rollout_seed=701,
        policy_generation=3,
    )
    coslight.initialize(_metadata())

    coslight.step(_step_payload(0, a_in=10, a_out=1, b_in=6, b_out=1))
    coslight.step(_step_payload(3, a_in=7, a_out=2, b_in=4, b_out=2))
    coslight.finish(
        {"reason": "completed", "departed_vehicles": 10, "arrived_vehicles": 2}
    )

    collected = coslight.take_collected_rollout()
    trajectory = collected["trajectory"]
    assert collected["sample_count"] == 1
    assert collected["policy_generation"] == 3
    assert collected["reward_mode"] == "pressure"
    assert collected["max_green_factor"] == pytest.approx(2.0)
    assert collected["phase_feature_schema"] == coslight.PHASE_FEATURE_SCHEMA
    assert collected["policy_architecture"] == coslight.POLICY_ARCHITECTURE
    assert collected["collaboration_schema"] == coslight.COLLABORATION_SCHEMA
    assert collected["pending_dropped"] == 0
    assert collected["terminal_unexecuted_action_agents"] == 2
    assert collected["terminal_pending_age_s"] == pytest.approx(0.0)
    assert trajectory["obs"].shape[0] == 1
    np.testing.assert_allclose(trajectory["transition_durations_s"], [15.0])
    assert trajectory["phase_features"].shape == (
        1,
        2,
        2,
        coslight.PHASE_FEATURES,
    )
    assert np.count_nonzero(trajectory["rewards"]) == 0
    assert coslight._reward_count == 0
    assert coslight._episode == 0
    assert coslight._buffer_episodes == []
    with pytest.raises(RuntimeError, match="No completed collector rollout"):
        coslight.take_collected_rollout()


def test_finish_uses_latest_observation_time_to_detect_executed_pending_action(
    monkeypatch,
):
    monkeypatch.setenv("COSLIGHT_MODE", "collect")
    coslight.prepare_collector(
        policy_state=None,
        policy_seed=42,
        rollout_seed=703,
        policy_generation=0,
    )
    coslight.initialize(_metadata())

    coslight.step(_step_payload(0, a_in=10, a_out=1, b_in=6, b_out=1))
    coslight.step(_step_payload(3, a_in=7, a_out=2, b_in=4, b_out=2))
    # The t=15 decision has already controlled the environment for five seconds,
    # but is not yet a complete 15-second PPO transition.
    coslight.step(_step_payload(4, a_in=6, a_out=2, b_in=3, b_out=2))
    coslight.finish(
        {"reason": "completed", "departed_vehicles": 10, "arrived_vehicles": 2}
    )

    collected = coslight.take_collected_rollout()
    assert collected["pending_dropped"] == 2
    assert collected["terminal_unexecuted_action_agents"] == 0
    assert collected["terminal_pending_age_s"] == pytest.approx(5.0)


def test_training_horizon_guard_keeps_only_full_transitions(monkeypatch):
    monkeypatch.setenv("COSLIGHT_MODE", "collect")
    monkeypatch.setenv("COSLIGHT_EPISODE_DURATION", "20")
    coslight.prepare_collector(
        policy_state=None,
        policy_seed=42,
        rollout_seed=704,
        policy_generation=0,
    )
    coslight.initialize(_metadata())

    first = coslight.step(
        _step_payload(0, a_in=10, a_out=1, b_in=6, b_out=1)
    )
    guarded = coslight.step(
        _step_payload(3, a_in=7, a_out=2, b_in=4, b_out=2)
    )
    held = coslight.step(
        _step_payload(4, a_in=6, a_out=2, b_in=3, b_out=2)
    )
    coslight.finish(
        {
            "reason": "completed",
            "simulation_time": 20.0,
            "departed_vehicles": 10,
            "arrived_vehicles": 2,
        }
    )

    collected = coslight.take_collected_rollout()
    assert set(first["actions"]["signals"]) == {"a", "b"}
    assert guarded["actions"]["signals"] == {}
    assert held["actions"]["signals"] == {}
    assert collected["sample_count"] == 1
    assert collected["pending_dropped"] == 0
    assert collected["terminal_unexecuted_action_agents"] == 0
    assert collected["terminal_horizon_guarded_agents"] == 2
    np.testing.assert_allclose(
        collected["trajectory"]["transition_durations_s"], [15.0]
    )
    assert np.isfinite(collected["trajectory"]["last_values"]).all()


def test_parallel_ingest_normalizes_joint_rollouts_centrally(monkeypatch):
    coslight.initialize(_metadata())
    update_sizes = []
    monkeypatch.setattr(
        coslight,
        "_ppo_update",
        lambda episode_count=None: update_sizes.append(episode_count),
    )
    num_agents = 2
    observations = np.zeros((1, num_agents, coslight.OBS_DIM), dtype=np.float32)
    episode = {
        "obs": observations,
        "actions": np.zeros((1, num_agents), dtype=np.int64),
        "action_log_probs": np.zeros((1, num_agents), dtype=np.float32),
        "collaborators": np.zeros((1, num_agents, 1), dtype=np.int64),
        "collaborator_log_probs": np.zeros((1, num_agents), dtype=np.float32),
        "values": np.zeros((1, num_agents), dtype=np.float32),
        "rewards": np.zeros((1, num_agents), dtype=np.float32),
        "raw_rewards": np.array([[1.0, 2.0]], dtype=np.float32),
        "transition_durations_s": np.array([15.0], dtype=np.float32),
        "valid_action_counts": np.array([[2, 1]], dtype=np.int64),
        "action_masks": np.array([[[True, True], [True, False]]]),
        "phase_features": np.zeros(
            (1, num_agents, 2, coslight.PHASE_FEATURES), dtype=np.float32
        ),
        "last_values": np.zeros(num_agents, dtype=np.float32),
    }
    rollouts = [
        {
            "reward_mode": "pressure",
            "max_green_factor": 2.0,
            "phase_feature_schema": coslight.PHASE_FEATURE_SCHEMA,
            "policy_architecture": coslight.POLICY_ARCHITECTURE,
            "collaboration_schema": coslight.COLLABORATION_SCHEMA,
            "trajectory": {key: value.copy() for key, value in episode.items()},
        },
        {
            "reward_mode": "pressure",
            "max_green_factor": 2.0,
            "phase_feature_schema": coslight.PHASE_FEATURE_SCHEMA,
            "policy_architecture": coslight.POLICY_ARCHITECTURE,
            "collaboration_schema": coslight.COLLABORATION_SCHEMA,
            "trajectory": {key: value.copy() for key, value in episode.items()},
        },
    ]

    summary = coslight.ingest_parallel_rollouts(rollouts, update=True)

    assert summary == {"episodes": 2, "samples": 2}
    assert coslight._episode == 2
    assert coslight._reward_count == 4
    assert update_sizes == [2]
    assert len(coslight._buffer_episodes) == 2
    assert all(np.isfinite(item["rewards"]).all() for item in coslight._buffer_episodes)


def test_parallel_ingest_rejects_mixed_reward_semantics():
    coslight.initialize(_metadata())

    with pytest.raises(ValueError, match="reward mode"):
        coslight.ingest_parallel_rollouts(
            [{
                "phase_feature_schema": coslight.PHASE_FEATURE_SCHEMA,
                "policy_architecture": coslight.POLICY_ARCHITECTURE,
                "collaboration_schema": coslight.COLLABORATION_SCHEMA,
                "reward_mode": "legacy_delta",
                "max_green_factor": 2.0,
                "trajectory": {},
            }],
            update=False,
        )


def test_parallel_initial_policy_has_a_generation_guard():
    coslight.initialize(_metadata())
    replacement = {
        name: torch.full_like(value, 0.125)
        for name, value in coslight.export_policy_state().items()
    }

    value_stats = {"mean": -2.5, "m2": 12.0, "count": 7}
    coslight.install_parallel_initial_policy(replacement, value_stats)

    assert all(
        torch.equal(value, replacement[name])
        for name, value in coslight.export_policy_state().items()
    )
    assert coslight.export_value_stats() == value_stats
    coslight._episode = 1
    with pytest.raises(RuntimeError, match="before the first rollout"):
        coslight.install_parallel_initial_policy(replacement)


def test_signal_execution_diagnostics_follow_requested_phase_until_observed(
    monkeypatch,
):
    monkeypatch.setenv("COSLIGHT_MODE", "collect")
    coslight.prepare_collector(
        policy_state=None,
        policy_seed=42,
        rollout_seed=702,
        policy_generation=0,
    )
    coslight.initialize(_metadata())

    def choose_first_phase(
        observations,
        valid_counts,
        deterministic=False,
        action_masks=None,
        phase_features=None,
    ):
        del observations, valid_counts, deterministic, action_masks, phase_features
        return SimpleNamespace(
            actions=torch.tensor([[0, 0]]),
            action_log_probs=torch.zeros((1, 2)),
            collaborators=torch.zeros((1, 2, 1), dtype=torch.long),
            collaborator_log_probs=torch.zeros((1, 2)),
            values=torch.zeros((1, 2)),
        )

    monkeypatch.setattr(coslight._model, "act", choose_first_phase)
    first = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)
    # ``b`` has only one legal phase.  Its long green must not make the
    # multi-phase starvation diagnostic look unhealthy.
    first["intersections"]["b"]["stage_elapsed"] = 120.0
    coslight.step(first)

    requested = coslight.signal_execution_diagnostics()
    assert requested["commands"] == 2
    assert requested["change_requests"] == 1
    assert requested["observed_changes"] == 0
    assert requested["unresolved_changes"] == 1
    assert requested["max_observed_green_s"] == pytest.approx(120.0)
    assert requested["mean_phase_dominance"] == pytest.approx(1.0)
    assert requested["multi_phase_max_observed_green_s"] == pytest.approx(5.0)
    assert requested["multi_phase_mean_phase_dominance"] == pytest.approx(1.0)
    assert requested["multi_phase_max_phase_dominance"] == pytest.approx(1.0)
    assert requested["per_intersection"]["a"]["valid_phase_count"] == 2
    assert requested["per_intersection"]["b"]["valid_phase_count"] == 1
    assert requested["per_intersection"]["a"]["phase_commands"] == {"10": 1}
    assert requested["per_intersection"]["b"]["phase_commands"] == {"30": 1}

    applied = _step_payload(2, a_in=4, a_out=1, b_in=4, b_out=1)
    applied["intersections"]["a"].update(
        {"current_phase": 10, "stage": "GREEN", "stage_elapsed": 0.0}
    )
    coslight.step(applied)

    observed = coslight.signal_execution_diagnostics()
    assert observed["observed_changes"] == 1
    assert observed["unresolved_changes"] == 0
    assert observed["mean_change_delay_s"] == pytest.approx(10.0)
    assert observed["change_execution_rate"] == pytest.approx(1.0)


def test_vehicle_braking_is_attributed_to_observed_phase_change_windows():
    coslight.initialize(_metadata())
    initial = _step_payload(0, a_in=4, a_out=1, b_in=4, b_out=1)
    coslight._observe_signal_execution(initial["intersections"], 0.0)
    assert coslight._last_observed_phase_changes == {}

    changed = _step_payload(2, a_in=4, a_out=1, b_in=4, b_out=1)
    changed["intersections"]["a"].update(
        {"current_phase": 10, "stage": "GREEN", "stage_elapsed": 0.0}
    )
    coslight._observe_signal_execution(changed["intersections"], 10.0)

    payload = {
        # One cumulative event belongs to a vehicle that left between two
        # observations; coverage must expose it instead of inventing an owner.
        "traffic": {"hard_braking_events": 5},
        "vehicles": {
            "near": {
                "motion": {"acceleration_mps2": -9.0},
                "location": {"lane_id": "a_in_0"},
                "next_signal": {"intersection_id": "a", "distance_m": 80.0},
                "driving_events": {"hard_braking_since_last_decision": 1},
            },
            "far": {
                "motion": {"acceleration_mps2": -7.0},
                "location": {"lane_id": "a_in_0"},
                "next_signal": {"tls_id": "a", "distance_m": 150.0},
                "driving_events": {"hard_braking_since_last_decision": 2},
            },
            "unknown": {
                "motion": {"acceleration_mps2": -8.0},
                "location": {"lane_id": "outside_0"},
                "next_signal": {"intersection_id": "unknown", "distance_m": 20.0},
                "driving_events": {"hard_braking_since_last_decision": 1},
            },
            "calm": {
                "motion": {"acceleration_mps2": 0.0},
                "location": {"lane_id": "b_in_0"},
                "next_signal": {"intersection_id": "b", "distance_m": 40.0},
                "driving_events": {"hard_braking_since_last_decision": 0},
            },
        },
    }
    coslight._observe_vehicle_braking(payload, 15.0)

    diagnostics = coslight.signal_execution_diagnostics()
    assert diagnostics["vehicle_observations"] == 4
    assert diagnostics["vehicle_hard_braking_events_observed"] == 4
    assert diagnostics["traffic_hard_braking_total"] == 5
    assert diagnostics["vehicle_hard_braking_event_coverage"] == pytest.approx(0.8)
    assert diagnostics["hard_braking_attributed_events"] == 3
    assert diagnostics["hard_braking_unattributed_events"] == 1
    assert diagnostics["hard_braking_post_switch"]["5"] == {
        "vehicle_observations": 2,
        "events": 3,
        "events_per_1000_vehicle_observations": 1500.0,
    }
    assert diagnostics["hard_braking_by_next_signal_distance"]["100"][
        "events"
    ] == 1
    assert diagnostics["hard_braking_recent_10s_near_100m"]["events"] == 1
    assert diagnostics["hard_braking_per_intersection"]["a"]["events"] == 3
    assert diagnostics["hard_braking_per_intersection"]["b"]["events"] == 0
    event = diagnostics["hard_braking_attribution_events"][0]
    assert event["vehicle_id"] == "near"
    assert event["time_since_phase_change_s"] == pytest.approx(5.0)
    assert event["last_change_from_phase"] == 20
    assert event["last_change_to_phase"] == 10

    payload["vehicles"] = {}
    coslight._observe_vehicle_braking(payload, 20.0)
    assert coslight.signal_execution_diagnostics()[
        "vehicle_hard_braking_events_observed"
    ] == 4


def test_untrained_evaluation_policy_is_seeded_and_deterministic(monkeypatch):
    monkeypatch.setenv("COSLIGHT_MODE", "untrained")
    monkeypatch.setenv("COSLIGHT_POLICY_SEED", "123")
    payload = _step_payload(0, a_in=5, a_out=1, b_in=5, b_out=1)

    coslight.initialize(_metadata())
    first = coslight.step(payload)["actions"]["signals"]

    coslight._reset_runtime_state()
    monkeypatch.setenv("COSLIGHT_MODE", "untrained")
    monkeypatch.setenv("COSLIGHT_POLICY_SEED", "123")
    coslight.initialize(_metadata())
    second = coslight.step(payload)["actions"]["signals"]

    assert first == second
    assert coslight._model is not None
    assert coslight._actor_optimizer is None
    assert coslight._critic_optimizer is None
    diagnostics = coslight.signal_execution_diagnostics()
    assert diagnostics["switch_logit_margin"] is None
    assert diagnostics["switch_hysteresis_agent_decisions"] == 2



def test_v17_collab_bias_is_action_dependent_and_starts_zero(monkeypatch):
    """The bias must be zero at warm start and give messages an
    action-relative lever once learned (zeroing context must not change logits
    before, and flipping the bias must change argmax after)."""
    coslight.initialize(_metadata())
    model = coslight._model
    observations = torch.randn((8, 2, coslight.OBS_DIM))
    phase_features = torch.randn((8, 2, 2, coslight.PHASE_FEATURES))
    phase_features[..., 6:] = 0.0
    with torch.no_grad():
        encoded = model.encode(observations)
        collaborators = (
            torch.arange(model.top_k)
            .view(1, 1, model.top_k)
            .expand(8, 2, model.top_k)
        )
        context = model.selected_collaborator_context(encoded, collaborators)
        ctx_ones = torch.ones_like(context)
        ctx_zero = torch.zeros_like(context)
        logits_pos = model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=ctx_ones,
        )
        logits_neg = model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=ctx_zero,
        )
        # Zero-initialized bias => the two contexts cannot change logits.
        torch.testing.assert_close(logits_pos, logits_neg)
        # Enable the bias deterministically: identity first layer, +1/-1
        # second layer => action 0 gets +sum(ctx), action 1 gets -sum(ctx).
        model.collab_bias[0].weight.copy_(torch.eye(model.hidden))
        model.collab_bias[0].bias.zero_()
        bias_weight = torch.zeros(2, model.hidden)
        bias_weight[0, :] = 1.0
        bias_weight[1, :] = -1.0
        model.collab_bias[-1].weight.copy_(bias_weight)
        model.collab_bias[-1].bias.zero_()
        logits_ones_enabled = model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=ctx_ones,
        )
        logits_zero_enabled = model._actor_logits(
            encoded,
            collaborators,
            phase_features,
            collaborator_context=ctx_zero,
        )
    assert not torch.equal(logits_ones_enabled, logits_zero_enabled)
    change = (
        torch.argmax(logits_ones_enabled, dim=-1)
        != torch.argmax(logits_zero_enabled, dim=-1)
    ).float().mean().item()
    assert change > 0.0


def test_install_parallel_initial_policy_tolerates_missing_collab_bias(
    monkeypatch,
):
    """Warm-starting from a V16 checkpoint (no collab-bias parameters) must
    install the policy and keep the new bias at its zero initialization."""
    coslight.initialize(_metadata())
    state = {
        name: value.clone()
        for name, value in coslight.export_policy_state().items()
        if not name.startswith("collab_bias.")
    }
    value_stats = {"mean": -1.0, "m2": 4.0, "count": 8}
    coslight.install_parallel_initial_policy(state, value_stats)
    assert (
        int(torch.count_nonzero(coslight._model.collab_bias[-1].weight)) == 0
    )
    # A non-bias missing key is still a real mismatch and must be rejected.
    with pytest.raises(ValueError, match="incompatible"):
        coslight.install_parallel_initial_policy(
            {
                name: value
                for name, value in state.items()
                if name != "obs_embed.0.weight"
            }
        )
