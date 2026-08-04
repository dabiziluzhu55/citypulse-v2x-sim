import numpy as np
import pytest

from algorithms.coslight.cloud import RegionalCloudCoordinator


def _topology():
    return {
        "schema_version": 1,
        "intersections": {"a": {}, "b": {}},
        "regions": [{"region_id": "r", "intersections": ["a", "b"]}],
        "corridors": [{
            "corridor_id": "c",
            "intersections": ["a", "b"],
            "directed_links": [
                {
                    "source": "a", "target": "b", "direction": "east",
                    "distance_m": 100.0,
                },
                {
                    "source": "b", "target": "a", "direction": "west",
                    "distance_m": 100.0,
                },
            ],
        }],
        "outgoing_edge_targets": {
            "a": {"a_out": [{"intersection_id": "b", "distance_m": 100.0}]},
            "b": {"b_out": [{"intersection_id": "a", "distance_m": 100.0}]},
        },
    }


def _coordinator(**kwargs):
    kwargs.setdefault("target_queue_ratio", 0.1)
    action_dim = kwargs.pop("action_dim", 1)
    return RegionalCloudCoordinator(
        _topology(),
        tls_order=("a", "b"),
        incoming_lanes={"a": ("a_in_0",), "b": ("b_in_0",)},
        lane_capacity={"a_in_0": 10, "b_in_0": 10},
        lane_length={"a_in_0": 100, "b_in_0": 100},
        phase_connections={
            "a": ((("a_in_0", "a_out_0"),),),
            "b": ((("b_in_0", "b_out_0"),),),
        },
        lane_edges={"a": {"a_out_0": "a_out"}, "b": {"b_out_0": "b_out"}},
        action_dim=action_dim,
        **kwargs,
    )


def _observations(a_queue=8, b_queue=1, b_occupancy=0.1):
    return {
        "a": {"lanes": {"a_in_0": {
            "vehicle_count": a_queue, "halting_count": a_queue,
            "queue_length_m": a_queue * 10, "occupancy": a_queue / 10,
        }}},
        "b": {"lanes": {"b_in_0": {
            "vehicle_count": b_queue, "halting_count": b_queue,
            "queue_length_m": b_queue * 10, "occupancy": b_occupancy,
        }}},
    }


def _platoon_coordinator(**kwargs):
    coordination_mode = kwargs.pop("coordination_mode", "platoon_shadow")
    kwargs.setdefault("update_interval_s", 30.0)
    kwargs.setdefault("max_weight", 1.2)
    kwargs.setdefault("min_platoon_vehicles", 1)
    kwargs.setdefault("platoon_lead_s", 0.0)
    kwargs.setdefault("platoon_lag_s", 0.0)
    topology = _topology()
    topology["schema_version"] = 2
    topology["corridors"][0]["directed_links"] = [
        {
            "source": "a",
            "target": "b",
            "direction": "east",
            "distance_m": 100.0,
            "free_flow_time_s": 10.0,
            "source_outgoing_edge": "a_out",
            "target_incoming_edge": "b_in",
        }
    ]
    topology["outgoing_edge_targets"] = {
        "a": {
            "a_out": [
                {
                    "intersection_id": "b",
                    "distance_m": 100.0,
                    "free_flow_time_s": 10.0,
                    "target_incoming_edge": "b_in",
                }
            ]
        },
        "b": {},
    }
    return RegionalCloudCoordinator(
        topology,
        tls_order=("a", "b"),
        incoming_lanes={"a": ("a_in_0",), "b": ("b_in_0",)},
        lane_capacity={"a_in_0": 10, "b_in_0": 10},
        lane_length={"a_in_0": 100, "b_in_0": 100},
        lane_speed={"a_in_0": 10, "b_in_0": 10},
        phase_orders={"a": (10,), "b": (20, 21)},
        phase_connections={
            "a": ((('a_in_0', 'a_out_0'),),),
            "b": (
                (("b_in_0", "b_out_0"),),
                (("b_cross_0", "b_cross_out_0"),),
            ),
        },
        lane_edges={
            "a": {"a_in_0": "a_in", "a_out_0": "a_out"},
            "b": {
                "b_in_0": "b_in",
                "b_out_0": "b_out",
                "b_cross_0": "b_cross",
                "b_cross_out_0": "b_cross_out",
            },
        },
        action_dim=2,
        coordination_mode=coordination_mode,
        **kwargs,
    )


def _platoon_observations():
    return {
        "a": {
            "current_phase": 10,
            "lanes": {
                "a_in_0": {
                    "vehicle_count": 3,
                    "halting_count": 0,
                    "mean_speed": 8.0,
                }
            },
        },
        "b": {"current_phase": 20, "lanes": {}},
    }


def test_cloud_amplifies_only_the_selected_safe_corridor_direction():
    coordinator = _coordinator(max_weight=1.2)
    features = np.zeros((2, 1, 8), dtype=np.float32)
    features[..., 5] = 0.1

    weights = coordinator.phase_weights(_observations(), features, 0.0)

    assert 1.0 < weights[0, 0] <= 1.2
    assert weights[1, 0] == 1.0
    assert coordinator.diagnostics()["preferred_directions"] == {"c": "east"}
    assert coordinator.diagnostics()["mapped_phase_actions"] == 2


def test_local_outgoing_spill_gate_neutralizes_stale_cloud_boost():
    coordinator = _coordinator(max_weight=1.2, update_interval_s=60.0)
    features = np.zeros((2, 1, 8), dtype=np.float32)
    features[..., 5] = 0.1
    assert coordinator.phase_weights(_observations(), features, 0.0)[0, 0] > 1.0

    features[0, 0, 4] = 0.8
    weights = coordinator.phase_weights(_observations(), features, 15.0)

    assert weights[0, 0] == 1.0
    assert coordinator.diagnostics()["spill_suppressed_phase_decisions"] == 1


def test_nonpositive_local_pressure_neutralizes_cloud_boost():
    coordinator = _coordinator(max_weight=1.2, update_interval_s=60.0)
    features = np.zeros((2, 1, 8), dtype=np.float32)
    features[..., 5] = 0.1
    assert coordinator.phase_weights(_observations(), features, 0.0)[0, 0] > 1.0

    features[0, 0, 5] = 0.0
    weights = coordinator.phase_weights(_observations(), features, 15.0)

    assert weights[0, 0] == 1.0
    assert (
        coordinator.diagnostics()[
            "local_pressure_suppressed_phase_decisions"
        ]
        == 1
    )


def test_cloud_maps_only_the_shortest_path_outgoing_edge_to_a_corridor():
    topology = _topology()
    topology["generation_config"] = {"max_corridor_distance_m": 1500.0}
    topology["outgoing_edge_targets"]["a"]["a_detour"] = [
        # Still below the global 1500 m corridor cap, but not the first edge of
        # the 100 m source-target shortest path.
        {"intersection_id": "b", "distance_m": 1000.0}
    ]
    coordinator = RegionalCloudCoordinator(
        topology,
        tls_order=("a", "b"),
        incoming_lanes={"a": ("a_in_0",), "b": ("b_in_0",)},
        lane_capacity={"a_in_0": 10, "b_in_0": 10},
        lane_length={"a_in_0": 100, "b_in_0": 100},
        phase_connections={
            "a": (
                (("a_in_0", "a_out_0"),),
                (("a_in_0", "a_detour_0"),),
            ),
            "b": ((("b_in_0", "b_out_0"),),),
        },
        lane_edges={
            "a": {"a_out_0": "a_out", "a_detour_0": "a_detour"},
            "b": {"b_out_0": "b_out"},
        },
        action_dim=2,
        target_queue_ratio=0.1,
    )
    features = np.zeros((2, 2, 8), dtype=np.float32)
    features[..., 5] = 0.1

    weights = coordinator.phase_weights(_observations(), features, 0.0)

    assert weights[0, 0] > 1.0
    assert weights[0, 1] == 1.0
    assert coordinator.diagnostics()["mapped_phase_actions"] == 2


def test_cloud_plan_updates_only_at_low_frequency_boundary():
    coordinator = _coordinator(update_interval_s=60.0)
    features = np.zeros((2, 1, 8), dtype=np.float32)
    features[..., 5] = 0.1
    coordinator.phase_weights(_observations(), features, 0.0)
    coordinator.phase_weights(_observations(a_queue=1, b_queue=8), features, 59.0)
    assert coordinator.diagnostics()["preferred_directions"] == {"c": "east"}

    coordinator.phase_weights(_observations(a_queue=1, b_queue=8), features, 60.0)
    assert coordinator.diagnostics()["preferred_directions"] == {"c": "west"}
    assert coordinator.diagnostics()["updates"] == 2


def test_cloud_records_same_state_counterfactual_action_changes():
    coordinator = _coordinator()

    coordinator.record_counterfactual_actions(
        np.array([0, 0]), np.array([1, 0]), simulation_time=15.0
    )

    diagnostics = coordinator.diagnostics()
    assert diagnostics["counterfactual_agent_decisions"] == 2
    assert diagnostics["counterfactual_action_changes"] == 1
    assert diagnostics["counterfactual_action_change_rate"] == pytest.approx(0.5)
    assert diagnostics["counterfactual_events"][0]["intersection_id"] == "a"


def test_cloud_diagnoses_logit_shift_and_remaining_challenger_gap():
    coordinator = _coordinator(action_dim=2)
    features = np.zeros((2, 2, 8), dtype=np.float32)
    features[0, 1, 0] = 0.6
    features[0, 1, 6] = 1.0

    coordinator.record_counterfactual_actions(
        np.array([0, 0]),
        np.array([1, 0]),
        simulation_time=15.0,
        baseline_logits=np.array([[1.0, 0.9], [0.5, 0.2]]),
        cloud_logits=np.array([[1.0, 1.1], [0.5, 0.2]]),
        action_masks=np.ones((2, 2), dtype=np.bool_),
        pressure_prior_weights=np.array([[1.0, 1.2], [1.0, 1.0]]),
        phase_features=features,
    )

    diagnostics = coordinator.diagnostics()
    assert diagnostics["counterfactual_abs_logit_shift_mean"] == pytest.approx(0.05)
    assert diagnostics["counterfactual_abs_logit_shift_max"] == pytest.approx(0.2)
    assert diagnostics["counterfactual_base_top_margin_p50"] == pytest.approx(0.2)
    assert diagnostics["counterfactual_weighted_agent_decisions"] == 1
    assert diagnostics["counterfactual_selected_action_amplified"] == 0
    assert diagnostics["counterfactual_challenger_decisions"] == 1
    assert diagnostics["counterfactual_challenger_base_gap_p50"] == pytest.approx(0.1)
    assert diagnostics["counterfactual_challenger_post_gap_min"] == pytest.approx(-0.1)
    assert diagnostics["counterfactual_positive_logit_shifts"] == 1
    assert diagnostics["counterfactual_negative_logit_shifts"] == 0
    assert diagnostics["incoming_priority_shadow_agent_decisions"] == 2
    assert diagnostics["incoming_priority_shadow_action_changes"] == 1
    assert diagnostics["incoming_priority_shadow_action_change_rate"] == pytest.approx(
        0.5
    )
    assert diagnostics["incoming_priority_shadow_events"][0][
        "intersection_id"
    ] == "a"
    assert diagnostics["direction_priority_shadow_agent_decisions"] == 2
    assert diagnostics["direction_priority_shadow_action_changes"] == 1
    assert diagnostics["direction_priority_shadow_action_change_rate"] == pytest.approx(
        0.5
    )
    assert diagnostics["direction_priority_shadow_events"][0][
        "intersection_id"
    ] == "a"
    assert diagnostics["weighted_probe_events"][0]["intersection_id"] == "a"
    assert diagnostics["weighted_probe_events"][0]["weights"] == [1.0, 1.2]
    assert diagnostics["counterfactual_events"][0]["current_action_index"] == 1
    assert diagnostics["counterfactual_events"][0]["effect"] == (
        "hold_current_receiving"
    )


def test_cloud_rejects_partial_counterfactual_logit_inputs():
    coordinator = _coordinator()

    with pytest.raises(ValueError, match="all-or-none"):
        coordinator.record_counterfactual_actions(
            np.array([0, 0]),
            np.array([0, 0]),
            simulation_time=15.0,
            baseline_logits=np.zeros((2, 1)),
        )


def test_platoon_shadow_targets_downstream_receiving_phase_at_arrival_time():
    coordinator = _platoon_coordinator()
    features = np.zeros((2, 2, 8), dtype=np.float32)

    before = coordinator.platoon_shadow_weights(
        _platoon_observations(), features, 0.0
    )
    at_arrival = coordinator.platoon_shadow_weights(
        _platoon_observations(), features, 10.0
    )

    assert np.array_equal(before, np.ones((2, 2), dtype=np.float32))
    assert at_arrival[0].tolist() == [1.0, 1.0]
    assert at_arrival[1, 0] == pytest.approx(1.16)
    assert at_arrival[1, 1] == 1.0
    diagnostics = coordinator.diagnostics()
    assert diagnostics["mode"] == "platoon_shadow"
    assert diagnostics["platoon_topology_links"] == 1
    assert diagnostics["platoon_predictions_created"] == 1
    assert diagnostics["platoon_boosted_phase_decisions"] == 1
    assert diagnostics["platoon_prediction_history"][0]["source"] == "a"
    assert diagnostics["platoon_prediction_history"][0]["target"] == "b"


def test_platoon_shadow_keeps_downstream_spillback_gate():
    coordinator = _platoon_coordinator()
    features = np.zeros((2, 2, 8), dtype=np.float32)
    coordinator.platoon_shadow_weights(_platoon_observations(), features, 0.0)
    features[1, 0, 4] = 0.8

    weights = coordinator.platoon_shadow_weights(
        _platoon_observations(), features, 10.0
    )

    assert np.array_equal(weights, np.ones((2, 2), dtype=np.float32))
    assert coordinator.diagnostics()[
        "platoon_spill_suppressed_phase_decisions"
    ] == 1


def test_platoon_hold_shadow_only_boosts_current_receiving_phase():
    features = np.zeros((2, 2, 8), dtype=np.float32)
    receiving = _platoon_coordinator()
    receiving.platoon_shadow_weights(
        _platoon_observations(), features, 0.0, hold_current_only=True
    )

    held = receiving.platoon_shadow_weights(
        _platoon_observations(), features, 10.0, hold_current_only=True
    )

    assert held[1, 0] == pytest.approx(1.16)
    assert held[1, 1] == 1.0
    assert receiving.diagnostics()["platoon_hold_candidate_phase_decisions"] == 1

    nonreceiving = _platoon_coordinator()
    observations = _platoon_observations()
    observations["b"]["current_phase"] = 21
    nonreceiving.platoon_shadow_weights(
        observations, features, 0.0, hold_current_only=True
    )
    neutral = nonreceiving.platoon_shadow_weights(
        observations, features, 10.0, hold_current_only=True
    )

    assert np.array_equal(neutral, np.ones((2, 2), dtype=np.float32))
    assert nonreceiving.diagnostics()[
        "platoon_hold_noncurrent_suppressed_phase_decisions"
    ] == 1


def test_platoon_hold_control_mode_uses_the_same_bounded_hold_prior():
    coordinator = _platoon_coordinator(coordination_mode="platoon_hold_control")
    features = np.zeros((2, 2, 8), dtype=np.float32)
    coordinator.platoon_shadow_weights(
        _platoon_observations(), features, 0.0, hold_current_only=True
    )

    weights = coordinator.platoon_shadow_weights(
        _platoon_observations(), features, 10.0, hold_current_only=True
    )

    assert coordinator.diagnostics()["mode"] == "platoon_hold_control"
    assert weights[1, 0] == pytest.approx(1.16)
    assert weights[1, 1] == 1.0


def test_platoon_hold_safe_mode_rejects_nonpositive_movement_pressure():
    coordinator = _platoon_coordinator(
        coordination_mode="platoon_hold_safe_shadow"
    )
    features = np.zeros((2, 2, 8), dtype=np.float32)
    coordinator.platoon_shadow_weights(
        _platoon_observations(),
        features,
        0.0,
        hold_current_only=True,
        require_positive_pressure=True,
    )

    neutral = coordinator.platoon_shadow_weights(
        _platoon_observations(),
        features,
        10.0,
        hold_current_only=True,
        require_positive_pressure=True,
    )

    assert np.array_equal(neutral, np.ones((2, 2), dtype=np.float32))
    assert coordinator.diagnostics()[
        "platoon_hold_nonpositive_pressure_suppressed_phase_decisions"
    ] == 1


def test_platoon_hold_cooldown_suppresses_a_repeated_cloud_hold():
    coordinator = _platoon_coordinator(
        coordination_mode="platoon_hold_safe_control",
        hold_cooldown_s=30.0,
        platoon_lag_s=15.0,
    )
    observations = _platoon_observations()
    features = np.zeros((2, 2, 8), dtype=np.float32)
    features[1, 0, 5] = 0.1
    features[1, 0, 6] = 1.0
    coordinator.platoon_shadow_weights(
        observations,
        features,
        0.0,
        hold_current_only=True,
        require_positive_pressure=True,
    )
    first = coordinator.platoon_shadow_weights(
        observations,
        features,
        10.0,
        hold_current_only=True,
        require_positive_pressure=True,
    )
    coordinator.record_counterfactual_actions(
        np.array([0, 1]),
        np.array([0, 0]),
        simulation_time=10.0,
        baseline_logits=np.array([[1.0, 0.0], [0.8, 1.0]]),
        cloud_logits=np.array([[1.0, 0.0], [1.1, 1.0]]),
        action_masks=np.ones((2, 2), dtype=np.bool_),
        pressure_prior_weights=first,
        phase_features=features,
    )

    repeated = coordinator.platoon_shadow_weights(
        observations,
        features,
        15.0,
        hold_current_only=True,
        require_positive_pressure=True,
    )

    assert first[1, 0] == pytest.approx(1.16)
    assert np.array_equal(repeated, np.ones((2, 2), dtype=np.float32))
    assert coordinator.diagnostics()[
        "platoon_hold_cooldown_suppressed_phase_decisions"
    ] == 1


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("update_interval_s", 0.0),
        ("max_weight", 0.9),
        ("target_queue_ratio", 1.0),
        ("spill_threshold", 0.0),
    ],
)
def test_cloud_rejects_unsafe_configuration(argument, value):
    with pytest.raises(ValueError):
        _coordinator(**{argument: value})
