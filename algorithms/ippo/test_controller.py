"""Regression tests for IPPO rewards and execution-aligned rollouts."""

from __future__ import annotations

import importlib
import math
import os
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch


def _metadata(intersection_ids=("demo_1", "demo_2"), phase_counts=(2, 1)):
    intersections = {}
    for offset, (intersection_id, phase_count) in enumerate(
        zip(intersection_ids, phase_counts)
    ):
        incoming = f"in_{offset}"
        outgoing = f"out_{offset}"
        intersections[intersection_id] = {
            "phase_order": list(range(phase_count)),
            "incoming_lanes": [incoming],
            "outgoing_lanes": [outgoing],
            "lanes": {
                incoming: {
                    "edge_id": f"in_edge_{offset}",
                    "length_m": 150.0,
                    "speed_limit_mps": 15.0,
                },
                outgoing: {
                    "edge_id": f"out_edge_{offset}",
                    "length_m": 150.0,
                    "speed_limit_mps": 15.0,
                },
            },
            "connections": [
                {
                    "connection_id": f"connection_{offset}",
                    "from_lane": incoming,
                    "to_lane": outgoing,
                }
            ],
            "phases": {
                phase: {
                    "green_seconds": 30.0,
                    "connection_priorities": {
                        f"connection_{offset}": "protected"
                    }
                }
                for phase in range(phase_count)
            },
        }
    return {
        "episode_id": "test-episode",
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": intersections,
    }


def _step_payload(
    *,
    time=5.0,
    waiting=(100.0, 50.0),
    stage="GREEN",
    stage_elapsed=5.0,
    pending_phase=None,
    outgoing_waiting=0.0,
    outgoing_occupancy=25.0,
    incoming_halting=5,
    current_phase=0,
    vehicles=None,
):
    intersections = {}
    for offset, value in enumerate(waiting):
        intersections[f"demo_{offset + 1}"] = {
            "current_phase": current_phase,
            "stage": stage,
            "stage_elapsed": stage_elapsed,
            "pending_phase": pending_phase,
            "lanes": {
                f"in_{offset}": {
                    "vehicle_count": 10,
                    "halting_count": incoming_halting,
                    "waiting_time": value,
                    "mean_speed": 7.5,
                    "occupancy": 25.0,
                },
                f"out_{offset}": {
                    "vehicle_count": 20,
                    "halting_count": 20,
                    "waiting_time": outgoing_waiting,
                    "mean_speed": 0.0,
                    "occupancy": outgoing_occupancy,
                },
            },
        }
    return {
        "episode_id": "test-episode",
        "step_id": int(time / 5),
        "simulation_time": time,
        "intersections": intersections,
        "vehicles": vehicles or {},
    }


@pytest.fixture
def controller(monkeypatch):
    from algorithms.ippo import controller as module

    module = importlib.reload(module)
    monkeypatch.setenv("IPPO_MODE", "train")
    monkeypatch.setenv("IPPO_ACTION_INTERVAL", "15")
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    monkeypatch.delenv("IPPO_EFFECTIVE_DEMAND", raising=False)
    monkeypatch.delenv("IPPO_TRAIN_SEED_START", raising=False)
    monkeypatch.delenv("IPPO_TRAIN_SEED_END", raising=False)
    return module


def test_state_contains_distinct_intersection_identity(controller):
    builder = controller.StateBuilder(_metadata())
    payload = _step_payload(waiting=(100.0, 100.0))
    first = builder.build("demo_1", payload)
    second = builder.build("demo_2", payload)

    identity_start = controller.MAX_PHASES + 1
    assert np.array_equal(first[identity_start : identity_start + 2], [1.0, 0.0])
    assert np.array_equal(second[identity_start : identity_start + 2], [0.0, 1.0])
    assert not np.array_equal(first, second)


def test_state_adds_normalized_outgoing_congestion_summary(controller):
    builder = controller.StateBuilder(_metadata(("demo_1",), (2,)))
    baseline = _step_payload(waiting=(100.0,), outgoing_waiting=0.0)
    congested_outgoing = _step_payload(
        waiting=(100.0,), outgoing_waiting=999999.0, outgoing_occupancy=90.0
    )
    first = builder.build("demo_1", baseline)
    second = builder.build("demo_1", congested_outgoing)

    assert not np.array_equal(first, second)
    lane_start = controller.MAX_PHASES + 1 + len(controller.IDENTITY_SLOT_IDS)
    assert first[lane_start] == pytest.approx(0.5)  # 10 vehicles / 20 capacity
    assert first[lane_start + 1] == pytest.approx(0.25)
    assert first[lane_start + 3] == pytest.approx(0.5)
    assert first[-3:] == pytest.approx([0.25, 0.25, 1.0])
    assert second[-3:] == pytest.approx([0.90, 0.90, 1.0])


@pytest.mark.parametrize("valid_count", [1, 2, 3, 4])
def test_action_mask_never_samples_invalid_phase(controller, valid_count):
    logits = torch.zeros((256, 4))
    distribution = controller._masked_categorical(logits, valid_count)
    samples = distribution.sample()

    assert int(samples.max()) < valid_count
    assert torch.all(distribution.probs[:, valid_count:] == 0)


def test_action_mask_rejects_invalid_counts(controller):
    with pytest.raises(ValueError):
        controller._masked_categorical(torch.zeros((1, 2)), 0)
    with pytest.raises(ValueError):
        controller._masked_categorical(torch.zeros((1, 2)), 3)


def test_boolean_action_mask_can_disable_current_phase(controller):
    logits = torch.tensor([[100.0, 0.0, -100.0]])
    distribution = controller._masked_categorical(
        logits, torch.tensor([[False, True, False]])
    )

    assert torch.equal(distribution.probs, torch.tensor([[0.0, 1.0, 0.0]]))


def test_phase_actor_is_equivariant_to_candidate_order(controller):
    torch.manual_seed(7)
    model = controller.IPPONetwork(12, 3)
    observations = torch.randn(4, 12)
    phase_features = torch.randn(4, 3, controller.PHASE_FEATURES)

    logits = model.actor_forward(observations, phase_features)
    reordered = model.actor_forward(observations, phase_features[:, [2, 0, 1]])

    assert torch.allclose(reordered, logits[:, [2, 0, 1]])


def test_phase_features_follow_served_connections(controller):
    metadata = _metadata(("demo_1",), (2,))
    intersection = metadata["intersections"]["demo_1"]
    intersection["incoming_lanes"] = ["busy_in", "empty_in"]
    intersection["outgoing_lanes"] = ["free_out", "blocked_out"]
    intersection["lanes"] = {
        lane_id: {"length_m": 150.0, "speed_limit_mps": 15.0}
        for lane_id in ("busy_in", "empty_in", "free_out", "blocked_out")
    }
    intersection["connections"] = [
        {"connection_id": "busy", "from_lane": "busy_in", "to_lane": "free_out"},
        {"connection_id": "empty", "from_lane": "empty_in", "to_lane": "blocked_out"},
    ]
    intersection["phases"] = {
        0: {"green_seconds": 30.0, "connection_priorities": {"busy": "protected"}},
        1: {"green_seconds": 30.0, "connection_priorities": {"empty": "protected"}},
    }
    payload = {
        "intersections": {
            "demo_1": {
                "current_phase": 0,
                "stage": "GREEN",
                "stage_elapsed": 10.0,
                "lanes": {
                    "busy_in": {"vehicle_count": 18, "halting_count": 16, "waiting_time": 1200.0, "occupancy": 80.0},
                    "empty_in": {"vehicle_count": 1, "halting_count": 0, "waiting_time": 0.0, "occupancy": 5.0},
                    "free_out": {"vehicle_count": 1, "halting_count": 0, "waiting_time": 0.0, "occupancy": 5.0},
                    "blocked_out": {"vehicle_count": 18, "halting_count": 16, "waiting_time": 1200.0, "occupancy": 90.0},
                },
            }
        }
    }
    builder = controller.StateBuilder(metadata)

    features = builder.build_phase_features(
        "demo_1", payload["intersections"]["demo_1"], simulation_time=10.0,
        last_service_times={0: 10.0, 1: 0.0},
    )

    assert features.shape == (2, controller.PHASE_FEATURES)
    assert features[0, controller.PHASE_PRESSURE_INDEX] > 0.0
    assert features[1, controller.PHASE_PRESSURE_INDEX] < 0.0
    assert features[0, controller.PHASE_CURRENT_INDEX] == pytest.approx(1.0)
    assert features[1, controller.PHASE_SERVICE_AGE_INDEX] > 0.0


def test_phase_features_count_effective_running_demand_by_route(controller):
    metadata = _metadata(("demo_1",), (2,))
    intersection = metadata["intersections"]["demo_1"]
    intersection["incoming_lanes"] = ["busy_in", "empty_in"]
    intersection["outgoing_lanes"] = ["free_out", "blocked_out"]
    intersection["lanes"] = {
        "busy_in": {
            "edge_id": "busy_edge",
            "length_m": 150.0,
            "speed_limit_mps": 15.0,
        },
        "empty_in": {
            "edge_id": "empty_edge",
            "length_m": 150.0,
            "speed_limit_mps": 15.0,
        },
        "free_out": {
            "edge_id": "free_edge",
            "length_m": 150.0,
            "speed_limit_mps": 15.0,
        },
        "blocked_out": {
            "edge_id": "blocked_edge",
            "length_m": 150.0,
            "speed_limit_mps": 15.0,
        },
    }
    intersection["connections"] = [
        {"connection_id": "busy", "from_lane": "busy_in", "to_lane": "free_out"},
        {"connection_id": "empty", "from_lane": "empty_in", "to_lane": "blocked_out"},
    ]
    intersection["phases"] = {
        0: {"green_seconds": 30.0, "connection_priorities": {"busy": "protected"}},
        1: {"green_seconds": 30.0, "connection_priorities": {"empty": "protected"}},
    }

    def vehicle(*, route, speed, distance, signal="demo_1"):
        return {
            "motion": {"speed_mps": speed},
            "location": {"route_edges": route, "route_index": 0},
            "next_signal": {"intersection_id": signal, "distance_m": distance},
        }

    vehicles = {
        "near_busy": vehicle(route=["busy_edge", "free_edge"], speed=8.0, distance=80.0),
        "far_busy": vehicle(route=["busy_edge", "free_edge"], speed=8.0, distance=160.0),
        "near_empty": vehicle(route=["empty_edge", "blocked_edge"], speed=6.0, distance=60.0),
        "beyond_horizon": vehicle(route=["busy_edge", "free_edge"], speed=10.0, distance=310.0),
        "stopped": vehicle(route=["busy_edge", "free_edge"], speed=0.0, distance=50.0),
        "other_signal": vehicle(route=["busy_edge", "free_edge"], speed=8.0, distance=50.0, signal="demo_2"),
    }
    builder = controller.StateBuilder(metadata)

    features = builder.build_phase_features(
        "demo_1",
        _step_payload(waiting=(100.0,))["intersections"]["demo_1"],
        simulation_time=10.0,
        last_service_times={0: 10.0, 1: 0.0},
        vehicles=vehicles,
        demand_horizon_seconds=15.0,
    )

    assert features.shape == (2, controller.PHASE_FEATURES)
    one_vehicle = math.log1p(1.0) / math.log1p(7.5)
    assert features[0, controller.PHASE_NEAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle
    )
    assert features[0, controller.PHASE_FAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle
    )
    assert features[1, controller.PHASE_NEAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle
    )
    assert features[1, controller.PHASE_FAR_DEMAND_INDEX] == 0.0


def test_effective_demand_uses_eta_and_counts_next_local_movement_once(controller):
    metadata = _metadata(("demo_1",), (2,))
    route_with_a_later_repeat = [
        "in_edge_0",
        "out_edge_0",
        "in_edge_0",
        "out_edge_0",
    ]
    vehicles = {
        "near": {
            "motion": {"speed_mps": 10.0},
            "location": {"route_edges": route_with_a_later_repeat, "route_index": 0},
            "next_signal": {"intersection_id": "demo_1", "distance_m": 100.0},
        },
        "far_same_distance": {
            "motion": {"speed_mps": 4.0},
            "location": {"route_edges": ["in_edge_0", "out_edge_0"], "route_index": 0},
            "next_signal": {"intersection_id": "demo_1", "distance_m": 100.0},
        },
    }
    features = controller.StateBuilder(metadata).build_phase_features(
        "demo_1",
        _step_payload(waiting=(100.0,))["intersections"]["demo_1"],
        simulation_time=10.0,
        last_service_times={0: 10.0, 1: 0.0},
        vehicles=vehicles,
        demand_horizon_seconds=15.0,
    )

    one_vehicle = math.log1p(1.0) / math.log1p(7.5)
    assert features[0, controller.PHASE_NEAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle
    )
    assert features[0, controller.PHASE_FAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle
    )


@pytest.mark.parametrize(
    ("distance", "speed", "near_count", "far_count"),
    [
        (0.0, 10.0, 0, 0),
        (150.0, 10.0, 1, 0),
        (150.001, 10.0, 0, 1),
        (300.0, 10.0, 0, 1),
        (300.001, 10.0, 0, 0),
        (1.0, 0.0, 0, 0),
        (1.0, 0.1, 0, 0),
        (1.0, 0.1001, 1, 0),
        (1.0, math.inf, 0, 0),
        (1.0, math.nan, 0, 0),
    ],
)
def test_effective_demand_eta_and_speed_boundaries(
    controller, distance, speed, near_count, far_count
):
    metadata = _metadata(("demo_1",), (2,))
    vehicles = {
        "probe": {
            "motion": {"speed_mps": speed},
            "location": {
                "route_edges": ["in_edge_0", "out_edge_0"],
                "route_index": 0,
            },
            "next_signal": {"intersection_id": "demo_1", "distance_m": distance},
        }
    }
    features = controller.StateBuilder(metadata).build_phase_features(
        "demo_1",
        _step_payload(waiting=(100.0,))["intersections"]["demo_1"],
        simulation_time=10.0,
        last_service_times={0: 10.0, 1: 0.0},
        vehicles=vehicles,
        demand_horizon_seconds=15.0,
    )
    one_vehicle = math.log1p(1.0) / math.log1p(7.5)

    assert features[0, controller.PHASE_NEAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle * near_count
    )
    assert features[0, controller.PHASE_FAR_DEMAND_INDEX] == pytest.approx(
        one_vehicle * far_count
    )


def test_step_stores_effective_demand_in_executed_rollout(controller):
    controller.initialize(_metadata(("demo_1",), (2,)))
    vehicles = {
        "approaching": {
            "motion": {"speed_mps": 8.0},
            "location": {
                "route_edges": ["in_edge_0", "out_edge_0"],
                "route_index": 0,
            },
            "next_signal": {"intersection_id": "demo_1", "distance_m": 100.0},
        }
    }

    controller.step(_step_payload(time=5.0, waiting=(100.0,), vehicles=vehicles))

    transition = controller._pending_transitions["demo_1"]
    assert transition["phase_features"][
        0, controller.PHASE_NEAR_DEMAND_INDEX
    ] == pytest.approx(math.log1p(1.0) / math.log1p(7.5))
    assert transition["phase_features"][0, controller.PHASE_FAR_DEMAND_INDEX] == 0.0


def test_effective_demand_ablation_zeroes_only_the_demand_feature(
    controller, monkeypatch
):
    metadata = _metadata(("demo_1",), (2,))
    vehicles = {
        "approaching": {
            "motion": {"speed_mps": 8.0},
            "location": {
                "route_edges": ["in_edge_0", "out_edge_0"],
                "route_index": 0,
            },
            "next_signal": {"intersection_id": "demo_1", "distance_m": 100.0},
        }
    }

    monkeypatch.setenv("IPPO_EFFECTIVE_DEMAND", "off")
    controller.initialize(metadata)
    controller.step(_step_payload(time=5.0, waiting=(100.0,), vehicles=vehicles))
    disabled = controller._pending_transitions["demo_1"]["phase_features"].copy()

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "train")
    monkeypatch.setenv("IPPO_ACTION_INTERVAL", "15")
    monkeypatch.setenv("IPPO_EFFECTIVE_DEMAND", "on")
    controller.initialize(metadata)
    controller.step(_step_payload(time=5.0, waiting=(100.0,), vehicles=vehicles))
    enabled = controller._pending_transitions["demo_1"]["phase_features"]

    demand_indices = [
        controller.PHASE_NEAR_DEMAND_INDEX,
        controller.PHASE_FAR_DEMAND_INDEX,
    ]
    assert np.all(disabled[:, demand_indices] == 0.0)
    assert enabled[0, controller.PHASE_NEAR_DEMAND_INDEX] == pytest.approx(
        math.log1p(1.0) / math.log1p(7.5)
    )
    assert np.array_equal(
        np.delete(disabled, demand_indices, axis=1),
        np.delete(enabled, demand_indices, axis=1),
    )


def test_max_green_mask_forces_an_alternative_phase(controller):
    builder = controller.StateBuilder(_metadata(("demo_1",), (2,)))
    intersection = _step_payload(
        time=60.0, waiting=(100.0,), stage_elapsed=60.0
    )["intersections"]["demo_1"]

    mask, forced = builder.build_action_mask(
        "demo_1", intersection, max_green_factor=2.0
    )

    assert forced is True
    assert mask.tolist() == [False, True]


def test_max_green_mask_keeps_single_phase_signal_valid(controller):
    builder = controller.StateBuilder(_metadata(("demo_1",), (1,)))
    intersection = _step_payload(
        time=600.0, waiting=(100.0,), stage_elapsed=600.0
    )["intersections"]["demo_1"]

    mask, forced = builder.build_action_mask(
        "demo_1", intersection, max_green_factor=2.0
    )

    assert forced is False
    assert mask.tolist() == [True]


def test_step_enforces_max_green_and_observes_execution(controller):
    controller.initialize(_metadata())

    command = controller.step(
        _step_payload(time=60.0, stage_elapsed=60.0, current_phase=0)
    )
    controller.step(
        _step_payload(time=65.0, stage_elapsed=5.0, current_phase=1)
    )
    diagnostics = controller.signal_execution_diagnostics()

    assert command["actions"]["signals"]["demo_1"]["target_phase"] == 1
    assert diagnostics["max_green_forced_commands"] == 1
    assert diagnostics["change_requests"] == 1
    assert diagnostics["observed_changes"] == 1
    assert diagnostics["mean_change_delay_s"] == pytest.approx(5.0)


def test_v5a_reward_uses_fixed_physical_scale(controller):
    assert controller._normalize_reward(0.1) == pytest.approx(0.1)
    assert controller._normalize_reward(0.1) == pytest.approx(0.1)
    assert controller._normalize_reward(5.0) == pytest.approx(1.0)
    assert controller._normalize_reward(-5.0) == pytest.approx(-3.0)


def test_spillback_penalty_converts_sumo_percent_to_fraction(controller):
    assert controller._spillback_penalty(70.0) == pytest.approx(0.0)
    assert controller._spillback_penalty(80.0) == pytest.approx(0.25)
    assert controller._spillback_penalty(90.0) == pytest.approx(1.0)


def test_actor_and_critic_have_disjoint_gradients(controller):
    model = controller.IPPONetwork(12, 3)
    observations = torch.randn(4, 12)
    actor_parameters = tuple(model.actor_parameters())
    critic_parameters = tuple(model.critic_parameters())

    model.actor_forward(observations).sum().backward()
    assert any(parameter.grad is not None for parameter in actor_parameters)
    assert all(parameter.grad is None for parameter in critic_parameters)

    model.zero_grad(set_to_none=True)
    model.critic_forward(observations).sum().backward()
    assert all(parameter.grad is None for parameter in actor_parameters)
    assert any(parameter.grad is not None for parameter in critic_parameters)


def test_yellow_or_pending_controller_never_receives_a_new_action(controller):
    controller.initialize(_metadata())
    yellow = controller.step(_step_payload(stage="YELLOW", pending_phase=1))
    clearance = controller.step(_step_payload(stage="CLEARANCE", pending_phase=None))

    assert yellow["actions"]["signals"] == {}
    assert clearance["actions"]["signals"] == {}


def test_action_interval_prevents_five_second_phase_thrashing(controller):
    controller.initialize(_metadata())
    first = controller.step(_step_payload(time=5.0))
    early = controller.step(_step_payload(time=10.0))
    next_allowed = controller.step(_step_payload(time=20.0))

    assert set(first["actions"]["signals"]) == {"demo_1", "demo_2"}
    assert early["actions"]["signals"] == {}
    assert set(next_allowed["actions"]["signals"]) == {"demo_1", "demo_2"}


def test_minimum_green_is_respected(controller):
    controller.initialize(_metadata())
    too_early = controller.step(_step_payload(stage_elapsed=4.9))
    allowed = controller.step(_step_payload(time=10.0, stage_elapsed=5.0))

    assert too_early["actions"]["signals"] == {}
    assert set(allowed["actions"]["signals"]) == {"demo_1", "demo_2"}


def test_reward_is_attached_to_previous_executed_action(controller):
    controller.initialize(_metadata())
    controller.step(_step_payload(time=5.0, waiting=(100.0, 50.0)))
    controller.step(_step_payload(time=10.0, waiting=(95.0, 48.0)))
    controller.step(_step_payload(time=15.0, waiting=(90.0, 46.0)))
    controller.step(_step_payload(time=20.0, waiting=(85.0, 44.0)))

    completed = controller._episode_trajectories["demo_1"][0]
    assert completed["observations"] == 3
    assert completed["reward_components"]["H"] > 0.0
    assert completed["reward_components"]["D"] > 0.0
    assert completed["raw_reward"] < 0.0


def test_vehicle_time_loss_and_safe_crossing_feed_v5a_components(controller):
    controller.initialize(_metadata(("demo_1",), (2,)))

    def vehicle(time_loss, next_intersection):
        return {
            "veh": {
                "traffic": {"time_loss_s": time_loss},
                "next_signal": (
                    {"intersection_id": next_intersection}
                    if next_intersection is not None
                    else None
                ),
            }
        }

    controller.step(_step_payload(time=5.0, waiting=(100.0,), vehicles=vehicle(0, "demo_1")))
    controller.step(_step_payload(time=10.0, waiting=(100.0,), vehicles=vehicle(5, "demo_1")))
    controller.step(_step_payload(time=15.0, waiting=(100.0,), vehicles=vehicle(10, None)))
    controller.step(_step_payload(time=20.0, waiting=(100.0,), vehicles=vehicle(15, None)))

    components = controller._episode_trajectories["demo_1"][0]["reward_components"]
    assert components["L"] > 0.0
    assert components["F_safe"] > 0.0
    assert components["B"] == pytest.approx(0.0)


def test_outgoing_spillback_is_penalized_without_crossings(controller):
    controller.initialize(_metadata(("demo_1",), (2,)))
    controller.step(_step_payload(time=5.0, waiting=(100.0,), outgoing_occupancy=90.0))
    controller.step(_step_payload(time=10.0, waiting=(100.0,), outgoing_occupancy=90.0))
    controller.step(_step_payload(time=15.0, waiting=(100.0,), outgoing_occupancy=90.0))
    controller.step(_step_payload(time=20.0, waiting=(100.0,), outgoing_occupancy=90.0))

    transition = controller._episode_trajectories["demo_1"][0]
    assert transition["reward_components"]["B"] == pytest.approx(0.7)
    assert transition["raw_reward"] < -0.1


def test_final_action_without_a_followup_observation_is_not_trained(controller):
    controller.initialize(_metadata())
    controller.step(_step_payload(time=5.0))
    controller.finish({"reason": "completed"})

    assert controller._episode == 0
    assert controller._buffer_episodes == []


def test_observed_partial_transition_is_bootstrapped_at_time_limit(controller):
    controller.initialize(_metadata())
    controller.step(_step_payload(time=5.0, waiting=(100.0, 50.0)))
    controller.step(_step_payload(time=10.0, waiting=(90.0, 45.0)))
    controller.finish({"reason": "completed"})

    assert controller._episode == 1
    assert len(controller._buffer_episodes[0]["demo_1"]) == 1
    assert controller._buffer_episodes[0]["demo_1"][0]["done"] is False


def test_failed_rollout_is_discarded(controller):
    controller.initialize(_metadata())
    controller.step(_step_payload())
    controller.finish({"reason": "error"})

    assert controller._episode == 0
    assert controller._buffer_episodes == []
    assert controller._pending_transitions == {}


def test_stopped_rollout_is_discarded(controller):
    controller.initialize(_metadata())
    controller.step(_step_payload())
    controller.finish({"reason": "stopped"})

    assert controller._episode == 0
    assert controller._buffer_episodes == []


def test_incompatible_training_metadata_resets_model_and_buffer(controller):
    controller.initialize(_metadata(("demo_1",), (2,)))
    first_model = controller._model
    controller._episode = 17
    controller._buffer_episodes.append({"demo_1": [{"sentinel": True}]})
    controller.initialize(_metadata(("demo_1", "demo_2"), (2, 1)))

    assert controller._model is not first_model
    assert controller._buffer_episodes == []
    assert controller._model_intersection_ids == ("demo_1", "demo_2")
    assert controller._episode == 0


def test_model_mode_requires_checkpoint(controller, monkeypatch):
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    with pytest.raises(ValueError, match="IPPO_MODEL_PATH"):
        controller.initialize(_metadata())


def test_legacy_raw_state_dict_checkpoint_is_rejected(controller, tmp_path):
    legacy = tmp_path / "legacy.pt"
    torch.save(controller.IPPONetwork(10, 2).state_dict(), legacy)

    with pytest.raises(ValueError, match="legacy raw state_dict"):
        controller.load_checkpoint_metadata(legacy)


def test_v5a_checkpoint_is_rejected_by_phase_aware_controller(
    controller, monkeypatch, tmp_path
):
    controller.initialize(_metadata(("demo_1",), (2,)))
    checkpoint_path = controller.save_checkpoint(tmp_path / "old.pt")
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    checkpoint["model_version"] = "v5a"
    torch.save(checkpoint, checkpoint_path)

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint_path))
    with pytest.raises(ValueError, match="version"):
        controller.initialize(_metadata(("demo_1",), (2,)))


def test_checkpoint_records_compatibility_and_seed_metadata(
    controller, monkeypatch, tmp_path
):
    monkeypatch.setenv("IPPO_TRAIN_SEED_START", "43")
    monkeypatch.setenv("IPPO_TRAIN_SEED_END", "242")
    controller.initialize(_metadata())
    checkpoint_path = controller.save_checkpoint(tmp_path / "model")
    checkpoint = torch.load(checkpoint_path, map_location="cpu")

    assert checkpoint["model_version"] == "v8"
    assert checkpoint["reward_definition"]["normalization"] == "physical_fixed"
    assert checkpoint["phase_feature_schema"] == controller.PHASE_FEATURE_SCHEMA
    assert checkpoint["effective_demand_enabled"] is True
    assert checkpoint["max_green_factor"] == pytest.approx(2.0)
    assert checkpoint["intersection_ids"] == ["demo_1", "demo_2"]
    assert checkpoint["obs_dim"] == controller._model.obs_dim
    assert checkpoint["act_dim"] == 2
    assert checkpoint["training_seed_range"] == {"start": 43, "end": 242}
    assert not checkpoint_path.with_suffix(".pt.tmp").exists()


def test_checkpoint_restores_disabled_effective_demand(
    controller, monkeypatch, tmp_path
):
    monkeypatch.setenv("IPPO_EFFECTIVE_DEMAND", "off")
    controller.initialize(_metadata(("demo_1",), (2,)))
    checkpoint_path = controller.save_checkpoint(tmp_path / "no-demand.pt")
    checkpoint = torch.load(checkpoint_path, map_location="cpu")
    assert checkpoint["effective_demand_enabled"] is False

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint_path))
    monkeypatch.delenv("IPPO_EFFECTIVE_DEMAND", raising=False)
    controller.initialize(_metadata(("demo_1",), (2,)))

    assert controller._effective_demand_enabled is False


def test_resume_restores_episode_and_optimizer_state(controller, monkeypatch, tmp_path):
    controller.initialize(_metadata(("demo_1",), (2,)))
    loss = controller._model.actor_forward(torch.zeros(1, controller._obs_dim)).sum()
    controller._optimizer_actor.zero_grad()
    loss.backward()
    controller._optimizer_actor.step()
    controller._episode = 17
    checkpoint = controller.save_checkpoint(tmp_path / "resume.pt")

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "train")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint))
    controller.initialize(_metadata(("demo_1",), (2,)))

    assert controller._episode == 17
    assert len(controller._optimizer_actor.state) > 0


def test_checkpoint_with_different_intersections_is_rejected(
    controller, monkeypatch, tmp_path
):
    controller.initialize(_metadata(("demo_1",), (2,)))
    checkpoint = controller.save_checkpoint(tmp_path / "model.pt")

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint))
    with pytest.raises(ValueError, match="not trained on this intersection subset"):
        controller.initialize(_metadata(("demo_1", "demo_2"), (2, 1)))


def test_checkpoint_with_different_action_interval_is_rejected(
    controller, monkeypatch, tmp_path
):
    controller.initialize(_metadata(("demo_1",), (2,)))
    checkpoint = controller.save_checkpoint(tmp_path / "model.pt")

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "model")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint))
    monkeypatch.setenv("IPPO_ACTION_INTERVAL", "20")
    with pytest.raises(ValueError, match="action interval"):
        controller.initialize(_metadata(("demo_1",), (2,)))


def test_time_limited_transition_bootstraps_value(controller):
    trajectory = [
        {
            "reward": 1.0,
            "value": 0.0,
            "next_value": 2.0,
            "done": False,
        }
    ]
    advantages, returns = controller._compute_gae(trajectory)
    expected = 1.0 + controller.GAMMA * 2.0

    assert advantages[0] == pytest.approx(expected)
    assert returns[0] == pytest.approx(expected)


def test_gae_is_computed_per_agent_trajectory(controller):
    first = [
        {"reward": 1.0, "value": 0.0, "next_value": 0.0, "done": True},
    ]
    second = [
        {"reward": 100.0, "value": 0.0, "next_value": 0.0, "done": True},
    ]

    first_advantage, _ = controller._compute_gae(first)
    second_advantage, _ = controller._compute_gae(second)
    assert first_advantage.tolist() == pytest.approx([1.0])
    assert second_advantage.tolist() == pytest.approx([100.0])


def test_ppo_update_keeps_remainder_minibatch_and_action_masks(
    controller, monkeypatch
):
    controller.initialize(_metadata(("demo_1",), (2,)))
    monkeypatch.setattr(controller, "PPO_EPOCHS", 1)
    monkeypatch.setattr(controller, "BATCH_SIZE", 3)
    monkeypatch.setattr(controller, "ACCUMULATE_EPISODES", 1)
    batch_sizes = []
    original_mask = controller._masked_categorical

    def recording_mask(logits, counts):
        batch_sizes.append(logits.shape[0])
        return original_mask(logits, counts)

    monkeypatch.setattr(controller, "_masked_categorical", recording_mask)
    observation = np.zeros(controller._obs_dim, dtype=np.float32)
    phase_features = np.zeros(
        (controller._act_dim, controller.PHASE_FEATURES), dtype=np.float32
    )
    action_mask = np.ones(controller._act_dim, dtype=np.bool_)
    trajectory = [
        {
            "obs": observation.copy(),
            "phase_features": phase_features.copy(),
            "action_mask": action_mask.copy(),
            "action": index % 2,
            "reward": float(index + 1),
            "raw_reward": float(index + 1),
            "log_prob": -0.69,
            "value": 0.0,
            "next_value": 0.0,
            "done": True,
            "valid_action_count": 2,
        }
        for index in range(5)
    ]
    controller._buffer_episodes.append({"demo_1": trajectory})

    controller._ppo_update()

    assert sorted(batch_sizes) == [2, 3]
    assert controller._buffer_episodes == []


def test_evaluation_seeds_must_not_overlap_training():
    from algorithms.ippo.evaluate_ckpt import _validate_evaluation_seeds

    metadata = {"training_seed_range": {"start": 43, "end": 242}}
    _validate_evaluation_seeds(metadata, [1042, 1142])
    with pytest.raises(ValueError, match="重叠"):
        _validate_evaluation_seeds(metadata, [142, 1042])


def test_resume_training_advances_beyond_previous_seed_range(tmp_path):
    from algorithms.ippo import train

    checkpoint = tmp_path / "resume.pt"
    torch.save(
        {
            "model_state_dict": {},
            "model_version": "v8",
            "intersection_ids": [],
            "obs_dim": 1,
            "act_dim": 1,
            "action_interval": 15.0,
            "training_seed_range": {"start": 43, "end": 242},
        },
        checkpoint,
    )

    assert train._training_seed_range(42, 10, checkpoint) == (243, 252, 43)


def test_parallel_resume_rejects_previous_model_version_before_workers(
    monkeypatch, tmp_path
):
    from algorithms.ippo import parallel_train

    checkpoint = tmp_path / "v7.pt"
    torch.save(
        {
            "model_state_dict": {},
            "model_version": "v7",
            "phase_feature_schema": "connection_pressure_service_age_effective_demand_v1",
            "intersection_ids": [parallel_train.DEFAULT_INTERSECTION_IDS[0]],
            "obs_dim": 1,
            "act_dim": 1,
            "action_interval": 15.0,
            "training_seed_range": {"start": 43, "end": 46},
            "effective_demand_enabled": True,
        },
        checkpoint,
    )
    monkeypatch.setattr(
        parallel_train,
        "_run_policy_batch",
        lambda **_kwargs: pytest.fail("worker batch must not start"),
    )

    with pytest.raises(SystemExit) as error:
        parallel_train.main(
            [
                "--episodes",
                "1",
                "--workers",
                "1",
                "--intersections",
                "1",
                "--duration",
                "1",
                "--resume",
                str(checkpoint),
            ]
        )

    assert error.value.code == 2


def test_joint_resume_rejects_v2_checkpoint_with_attached_v3_contract(
    monkeypatch,
    tmp_path,
):
    from algorithms.ippo import parallel_train
    from traffic_control.common.environment_contract import (
        JOINT_PERIODS,
        build_environment_contract,
    )
    from traffic_control.ippo.contract import build_ippo_policy_spec

    policy_spec = build_ippo_policy_spec(
        obs_dim=132,
        act_dim=4,
        action_interval=15.0,
        max_green_factor=2.0,
        phase_feature_schema=parallel_train.PHASE_FEATURE_SCHEMA,
        effective_demand_enabled=True,
        model_version=parallel_train.MODEL_VERSION,
    )
    environment_contract = build_environment_contract(
        {
            period: _joint_metadata(period)
            for period in JOINT_PERIODS
        },
        policy_spec=policy_spec,
    )
    checkpoint = {
        "model_state_dict": {},
        "model_version": parallel_train.MODEL_VERSION,
        "phase_feature_schema": parallel_train.PHASE_FEATURE_SCHEMA,
        "intersection_ids": ["demo_1"],
        "obs_dim": 132,
        "act_dim": 4,
        "action_interval": 15.0,
        "max_green_factor": 2.0,
        "training_periods": list(JOINT_PERIODS),
        "training_seed_range": {"start": 101, "end": 103},
        "effective_demand_enabled": True,
        "checkpoint_contract_version": 2,
        "environment_contract": environment_contract,
    }
    checkpoint_path = tmp_path / "v2-with-v3-envelope.pt"
    checkpoint_path.touch()
    monkeypatch.setattr(
        parallel_train,
        "load_checkpoint_metadata",
        lambda _path: checkpoint,
    )
    worker_calls = []

    def should_not_start_workers(**_kwargs):
        worker_calls.append(True)
        raise RuntimeError("worker batch must not start")

    monkeypatch.setattr(
        parallel_train,
        "_run_policy_batch",
        should_not_start_workers,
    )

    with pytest.raises(SystemExit) as error:
        parallel_train.main(
            [
                "--episodes",
                "6",
                "--workers",
                "6",
                "--intersection-ids",
                "demo_1",
                "--duration",
                "1",
                "--periods",
                *JOINT_PERIODS,
                "--resume",
                str(checkpoint_path),
            ]
        )

    assert error.value.code == 2
    assert worker_calls == []


def test_failed_evaluation_is_not_aggregated_as_zero(monkeypatch, tmp_path):
    from algorithms.ippo import evaluate_ckpt

    metadata = {
        "model_version": "v8",
        "intersection_ids": list(evaluate_ckpt.DEFAULT_INTERSECTION_IDS),
        "action_interval": 15.0,
        "training_seed_range": {"start": 43, "end": 242},
    }
    monkeypatch.setattr(evaluate_ckpt, "load_checkpoint_metadata", lambda _: metadata)

    class FailedManager:
        def start(self, _config):
            return "session"

        def wait(self, _session_id, timeout):
            return SimpleNamespace(
                state="FAILED",
                error="synthetic failure",
                metrics=SimpleNamespace(
                    departed_vehicles=819,
                    arrived_vehicles=0,
                    total_waiting_time=0.0,
                ),
            )

    monkeypatch.setattr(evaluate_ckpt, "SimulationManager", FailedManager)
    summary = evaluate_ckpt.evaluate(str(tmp_path / "fake.pt"), seeds=[1042])

    assert summary["status"] == "failed"
    assert summary["successful_runs"] == 0
    assert summary["failed_runs"] == 1
    assert summary["mean_arrived"] is None


def test_training_aborts_without_saving_failed_episode(monkeypatch, tmp_path):
    from algorithms.ippo import train

    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)

    class FailedManager:
        def start(self, _config):
            return "session"

        def wait(self, _session_id, timeout):
            return SimpleNamespace(state="FAILED", error="synthetic", metrics=None)

    saved = []
    monkeypatch.setattr(train, "SimulationManager", FailedManager)
    monkeypatch.setattr(train, "save_checkpoint", lambda path: saved.append(path))
    monkeypatch.setattr(train.time, "sleep", lambda _seconds: None)
    result = train.main(
        ["--episodes", "1", "--duration", "1", "--save", str(tmp_path / "model")]
    )

    assert result == 1
    assert saved == []


def test_training_stops_a_timed_out_sumo_session(monkeypatch, tmp_path):
    from algorithms.ippo import train

    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    instances = []

    class TimeoutManager:
        def __init__(self):
            self.wait_calls = 0
            self.stopped = False
            instances.append(self)

        def start(self, _config):
            return "session"

        def wait(self, _session_id, timeout):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise TimeoutError("synthetic timeout")
            return SimpleNamespace(state="STOPPED", error=None, metrics=None)

        def stop(self, _session_id):
            self.stopped = True

    monkeypatch.setattr(train, "SimulationManager", TimeoutManager)
    monkeypatch.setattr(train.time, "sleep", lambda _seconds: None)
    result = train.main(
        ["--episodes", "1", "--duration", "1", "--save", str(tmp_path / "model")]
    )

    assert result == 1
    assert instances[0].stopped is True
    assert instances[0].wait_calls == 2


def test_checkpoint_watcher_records_failures_once(monkeypatch, tmp_path):
    from algorithms.ippo import watch_ckpts

    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    checkpoint = checkpoint_dir / "ippo_v8_ep20.pt"
    checkpoint.write_bytes(b"complete")
    parallel_checkpoint = checkpoint_dir / "ippo_v8_parallel_ep20.pt"
    parallel_checkpoint.write_bytes(b"complete")
    manifest_path = checkpoint_dir / "evaluated.json"
    calls = []

    monkeypatch.setattr(watch_ckpts, "CKPT_DIR", checkpoint_dir)
    monkeypatch.setattr(watch_ckpts, "MANIFEST", manifest_path)
    monkeypatch.setattr(watch_ckpts, "is_stable", lambda *_args, **_kwargs: True)

    def failed_evaluation(path):
        calls.append(path)
        return {"status": "failed", "returncode": 1}

    monkeypatch.setattr(watch_ckpts, "evaluate_checkpoint", failed_evaluation)
    manifest = {}

    assert watch_ckpts.scan_once(manifest, stability_wait=0) is True
    assert watch_ckpts.scan_once(manifest, stability_wait=0) is False
    assert calls == [checkpoint, parallel_checkpoint]
    assert manifest[checkpoint.name]["status"] == "failed"
    assert manifest[parallel_checkpoint.name]["status"] == "failed"


def test_collector_exports_raw_rollout_without_local_update(controller, monkeypatch):
    monkeypatch.setenv("IPPO_MODE", "collect")
    controller.prepare_collector(policy_state=None, policy_seed=42, rollout_seed=701)
    controller.initialize(_metadata(("demo_1",), (2,)))

    controller.step(_step_payload(time=5.0, waiting=(100.0,)))
    controller.step(_step_payload(time=10.0, waiting=(90.0,)))
    controller.finish({"reason": "completed", "departed_vehicles": 10, "arrived_vehicles": 2})

    collected = controller.take_collected_rollout()
    transition = collected["trajectories"]["demo_1"][0]
    assert transition["raw_reward_parts"] == pytest.approx([transition["raw_reward"]])
    assert transition["reward"] == pytest.approx(transition["raw_reward"])
    assert set(transition["reward_components"]) == {
        "B", "D", "F_safe", "H", "L", "MP_alpha", "MP_regret", "Qmax", "S"
    }
    assert collected["sample_count"] == 1
    assert collected["metadata"]["intersections"]["demo_1"]["phase_order"] == [0, 1]
    assert controller._episode == 0
    assert controller._buffer_episodes == []
    with pytest.raises(RuntimeError, match="No completed collector rollout"):
        controller.take_collected_rollout()


def test_parallel_ingest_preserves_fixed_reward_scale_and_updates_one_policy_batch(
    controller, monkeypatch
):
    controller.initialize(_metadata(("demo_1",), (2,)))
    update_sizes = []
    monkeypatch.setattr(
        controller,
        "_ppo_update",
        lambda episode_count=None: update_sizes.append(episode_count),
    )
    observation = np.zeros(controller._obs_dim, dtype=np.float32)
    phase_features = np.zeros(
        (controller._act_dim, controller.PHASE_FEATURES), dtype=np.float32
    )
    action_mask = np.ones(controller._act_dim, dtype=np.bool_)

    rollouts = []
    for reward in (-0.1, -0.2, -0.3, -0.4):
        transition = {
            "obs": observation.copy(),
            "phase_features": phase_features.copy(),
            "action_mask": action_mask.copy(),
            "action": 0,
            "reward": 0.0,
            "raw_reward": reward,
            "raw_reward_parts": [reward],
            "log_prob": 0.0,
            "value": 0.0,
            "next_value": 0.0,
            "done": True,
            "valid_action_count": 2,
        }
        rollouts.append({"trajectories": {"demo_1": [transition]}})

    summary = controller.ingest_parallel_rollouts(rollouts, update=True)

    assert summary == {"episodes": 4, "samples": 4}
    assert controller._episode == 4
    assert update_sizes == [4]
    assert len(controller._buffer_episodes) == 4
    assert [episode["demo_1"][0]["reward"] for episode in controller._buffer_episodes] == pytest.approx(
        [-0.1, -0.2, -0.3, -0.4]
    )


def test_parallel_initial_policy_can_be_adopted_before_first_update(controller):
    controller.initialize(_metadata(("demo_1",), (2,)))
    replacement = {
        name: torch.full_like(value, 0.125)
        for name, value in controller.export_policy_state().items()
    }

    controller.install_parallel_initial_policy(replacement)

    assert all(
        torch.equal(value, replacement[name])
        for name, value in controller.export_policy_state().items()
    )
    controller._episode = 1
    with pytest.raises(RuntimeError, match="before the first rollout"):
        controller.install_parallel_initial_policy(replacement)


def test_parallel_seed_batches_are_unique_and_cover_remainder():
    from algorithms.ippo.parallel_train import _seed_batches

    assert list(_seed_batches(first_seed=101, episodes=5, workers=2)) == [
        (101, 102),
        (103, 104),
        (105,),
    ]


def test_parallel_periods_are_balanced_across_batches_and_resume_offsets():
    from algorithms.ippo.parallel_train import _period_batch

    periods = ("morning_peak", "off_peak", "evening_peak")
    assert _period_batch((101, 102, 103, 104), periods=periods, training_seed_start=101) == (
        "morning_peak",
        "off_peak",
        "evening_peak",
        "morning_peak",
    )
    assert _period_batch((105, 106), periods=periods, training_seed_start=101) == (
        "off_peak",
        "evening_peak",
    )


def test_parallel_metadata_allows_only_trailing_phase_omissions():
    from algorithms.ippo.parallel_train import (
        _metadata_signature,
        _metadata_signatures_compatible,
    )

    def metadata(phases, incoming=("in",), outgoing=("out",)):
        return {
            "intersections": {
                "demo_1": {
                    "phase_order": phases,
                    "incoming_lanes": incoming,
                    "outgoing_lanes": outgoing,
                }
            }
        }

    full = _metadata_signature(metadata((1, 2, 3, 4)))
    trailing_omission = _metadata_signature(metadata((1, 2, 3)))
    reordered = _metadata_signature(metadata((1, 3, 2)))
    lane_drift = _metadata_signature(metadata((1, 2, 3), incoming=("other",)))

    assert _metadata_signatures_compatible((full, trailing_omission)) is True
    assert _metadata_signatures_compatible((full, reordered)) is False
    assert _metadata_signatures_compatible((full, lane_drift)) is False

    def movement_metadata(swapped=False):
        phase_connections = ("second", "first") if swapped else ("first", "second")
        return {
            "intersections": {
                "demo_1": {
                    "phase_order": (1, 2),
                    "incoming_lanes": ("in_a", "in_b"),
                    "outgoing_lanes": ("out_a", "out_b"),
                    "connections": (
                        {"connection_id": "first", "from_lane": "in_a", "to_lane": "out_a"},
                        {"connection_id": "second", "from_lane": "in_b", "to_lane": "out_b"},
                    ),
                    "phases": {
                        1: {"green_seconds": 30.0, "connection_priorities": {phase_connections[0]: "protected"}},
                        2: {"green_seconds": 30.0, "connection_priorities": {phase_connections[1]: "protected"}},
                    },
                }
            }
        }

    mapped = _metadata_signature(movement_metadata())
    remapped = _metadata_signature(movement_metadata(swapped=True))
    assert _metadata_signatures_compatible((mapped, remapped)) is False
def test_parallel_periodic_checkpoint_records_only_consumed_seeds(
    monkeypatch, tmp_path
):
    from algorithms.ippo import parallel_train

    state = {"weight": torch.tensor([1.0])}
    metadata = {
        "intersections": {"demo_1": {"phase_order": [0, 1]}},
    }
    completed_episodes = 0
    saved_seed_ends = []
    saved_paths = []
    monkeypatch.setenv("IPPO_TRAIN_SEED_START", "test-original")
    monkeypatch.setenv("IPPO_TRAIN_SEED_END", "test-original")
    monkeypatch.delenv("IPPO_CHECKPOINT_DIR", raising=False)

    def collect_batch(**kwargs):
        assert kwargs["effective_demand_enabled"] is False
        return [
            {
                "status": "complete",
                "seed": seed,
                "metrics": {"arrived": 1, "waiting": 2.0},
                "rollout": {
                    "sample_count": 1,
                    "metadata": metadata,
                    "policy_state": state,
                    "trajectories": {"demo_1": [{"raw_reward": 0.0}]},
                },
            }
            for seed in kwargs["seeds"]
        ]

    def ingest(rollouts, *, update):
        nonlocal completed_episodes
        assert update is True
        completed_episodes += len(rollouts)
        return {"episodes": len(rollouts), "samples": len(rollouts)}

    def save(path):
        path = Path(path)
        saved_paths.append(path)
        saved_seed_ends.append((path.name, os.environ["IPPO_TRAIN_SEED_END"]))
        return str(path)

    monkeypatch.setattr(parallel_train, "_run_policy_batch", collect_batch)
    monkeypatch.setattr(parallel_train.controller, "initialize", lambda _metadata: None)
    monkeypatch.setattr(
        parallel_train.controller, "install_parallel_initial_policy", lambda _state: None
    )
    monkeypatch.setattr(parallel_train.controller, "export_policy_state", lambda: state)
    monkeypatch.setattr(
        parallel_train.controller,
        "training_episode_count",
        lambda: completed_episodes,
    )
    monkeypatch.setattr(parallel_train.controller, "ingest_parallel_rollouts", ingest)
    monkeypatch.setattr(parallel_train.controller, "save_checkpoint", save)

    result = parallel_train.main(
        [
            "--episodes",
            "4",
            "--workers",
            "2",
            "--intersections",
            "1",
            "--duration",
            "1",
            "--checkpoint-every",
            "2",
            "--effective-demand",
            "off",
            "--save",
            str(tmp_path / "model.pt"),
        ]
    )

    assert result == 0
    assert saved_seed_ends == [
        ("ippo_v8_parallel_ep2.pt", "44"),
        ("ippo_v8_parallel_ep4.pt", "46"),
        ("model.pt", "46"),
    ]
    assert saved_paths == [
        tmp_path / "checkpoints" / "ippo_v8_parallel_ep2.pt",
        tmp_path / "checkpoints" / "ippo_v8_parallel_ep4.pt",
        tmp_path / "model.pt",
    ]


def test_parallel_worker_failure_invalidates_entire_policy_batch():
    from algorithms.ippo.parallel_train import _validated_worker_results

    completed = [
        {"status": "complete", "seed": 102, "rollout": {"sample_count": 1}},
        {"status": "complete", "seed": 101, "rollout": {"sample_count": 1}},
    ]
    assert [result["seed"] for result in _validated_worker_results(completed)] == [
        101,
        102,
    ]

    with pytest.raises(RuntimeError, match="seed=102"):
        _validated_worker_results(
            [
                completed[0],
                {"status": "failed", "seed": 102, "error": "synthetic"},
            ]
        )


def test_four_tls_checkpoint_uses_its_own_intersection_set(monkeypatch, tmp_path):
    from algorithms.ippo import evaluate_ckpt
    from algorithms.evaluation import runtime as evaluation_runtime
    from algorithms.evaluation.collector import EvalResult

    intersection_ids = tuple(f"demo_{index}" for index in range(1, 5))
    metadata = {
        "model_version": "v8",
        "intersection_ids": list(intersection_ids),
        "action_interval": 15.0,
        "training_seed_range": {"start": 43, "end": 58},
        "effective_demand_enabled": False,
    }
    seen_configs = []
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    session_dir.joinpath("tripinfo.xml").write_text(
        "<tripinfos>"
        + "".join(
            f"<tripinfo id='v{index}' depart='0' arrival='10' duration='10' "
            "waitingTime='2'/>"
            for index in range(20)
        )
        + "</tripinfos>",
        encoding="utf-8",
    )

    class SuccessfulManager:
        session_root = tmp_path

        def start(self, config):
            assert os.environ["IPPO_EFFECTIVE_DEMAND"] == "off"
            seen_configs.append(config)
            return "session"

        def wait(self, _session_id, timeout):
            return SimpleNamespace(
                state="COMPLETED",
                error=None,
                metrics=SimpleNamespace(
                    departed_vehicles=100,
                    arrived_vehicles=20,
                    remaining_vehicles=0,
                    total_waiting_time=123.0,
                ),
            )

    monkeypatch.setattr(evaluate_ckpt, "load_checkpoint_metadata", lambda _: metadata)
    monkeypatch.setattr(evaluate_ckpt, "SimulationManager", SuccessfulManager)
    monkeypatch.setattr(
        evaluation_runtime,
        "last_result",
        lambda _episode_id=None: EvalResult(
            avg_queue_length_veh=1.0,
            throughput_veh_per_h=1200.0,
            avg_decision_latency_ms=2.0,
            fuel_intensity_L_per_100km=8.0,
            emergency_braking_exposure_per_1000=None,
            controlled_intersection_passages=100,
            departed=100,
            arrived=20,
        ),
    )
    summary = evaluate_ckpt.evaluate(
        str(tmp_path / "four.pt"),
        seeds=[1042],
        duration=60,
        period="evening_peak",
    )

    assert summary["status"] == "complete"
    assert summary["period"] == "evening_peak"
    assert summary["details"][0]["period"] == "evening_peak"
    assert summary["details"][0]["missing_official_metrics"] == [
        "emergency_braking_exposure_per_1000",
    ]
    assert tuple(seen_configs[0].intersection_ids) == intersection_ids
    assert seen_configs[0].period == "evening_peak"
    assert summary["details"][0]["all_waiting_total_s"] == pytest.approx(40.0)
    assert summary["details"][0]["all_time_loss_total_s"] == pytest.approx(0.0)
    assert summary["details"][0]["departed"] == 20
    assert summary["details"][0]["trip_records"] == 20
    assert summary["details"][0]["completed_trips"] == 20
    assert summary["details"][0]["unfinished_trips"] == 0
    assert summary["details"][0]["snapshot_remaining"] == 0
    assert summary["details"][0]["residual_mismatch"] == 0

def _joint_metadata(period: str) -> dict:
    phase_count = 1 if period == "off_peak" else 2
    metadata = _metadata(("demo_1",), (phase_count,))
    metadata.update(
        {
            "protocol_version": "2.0",
            "period": period,
            "episode_id": f"episode-{period}",
            "seed": 100,
        }
    )
    intersection = metadata["intersections"]["demo_1"]
    intersection["intersection_id"] = "demo_1"
    intersection["direct_neighbors"] = []
    for phase_id, phase in intersection["phases"].items():
        phase.update(
            {
                "phase_id": phase_id,
                "name": f"{period}-{phase_id}",
                "movement": "through" if phase_id == 0 else "left",
                "approaches": ["west"],
                "yellow_seconds": 3.0,
                "clearance_seconds": 2.0,
            }
        )
    return metadata


def _joint_rollout(controller, metadata: dict, reward: float = -0.1) -> dict:
    phase_count = len(
        metadata["intersections"]["demo_1"]["phase_order"]
    )
    action_mask = np.zeros(controller._act_dim, dtype=np.bool_)
    action_mask[:phase_count] = True
    return {
        "metadata": metadata,
        "sample_count": 1,
        "trajectories": {
            "demo_1": [
                {
                    "obs": np.zeros(controller._obs_dim, dtype=np.float32),
                    "phase_features": np.zeros(
                        (controller._act_dim, controller.PHASE_FEATURES),
                        dtype=np.float32,
                    ),
                    "action_mask": action_mask,
                    "action": 0,
                    "reward": 0.0,
                    "raw_reward": reward,
                    "raw_reward_parts": [reward],
                    "log_prob": 0.0,
                    "value": 0.0,
                    "next_value": 0.0,
                    "done": True,
                    "valid_action_count": phase_count,
                }
            ]
        },
    }


def test_joint_batch_validation_happens_before_learner_mutation(
    controller,
    monkeypatch,
):
    monkeypatch.setenv(
        "IPPO_TRAIN_PERIODS",
        "morning_peak,off_peak,evening_peak",
    )
    controller.initialize(_joint_metadata("morning_peak"))
    updates = []
    monkeypatch.setattr(
        controller,
        "_ppo_update",
        lambda episode_count=None: updates.append(episode_count),
    )
    rollouts = [
        _joint_rollout(controller, _joint_metadata("morning_peak"))
        for _ in range(3)
    ]

    with pytest.raises(ValueError, match="balanced"):
        controller.ingest_parallel_rollouts(rollouts, update=True)

    assert controller._episode == 0
    assert controller._buffer_episodes == []
    assert updates == []


def test_balanced_joint_batch_saves_complete_v3_contract(
    controller,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "IPPO_TRAIN_PERIODS",
        "morning_peak,off_peak,evening_peak",
    )
    monkeypatch.setenv("IPPO_EPISODE_DURATION_S", "3600")
    controller.initialize(_joint_metadata("morning_peak"))
    assert controller._act_dim == 4
    monkeypatch.setattr(controller, "_ppo_update", lambda episode_count=None: None)
    rollouts = [
        _joint_rollout(controller, _joint_metadata(period))
        for period in ("morning_peak", "off_peak", "evening_peak")
    ]

    assert controller.ingest_parallel_rollouts(rollouts, update=True) == {
        "episodes": 3,
        "samples": 3,
        "period_counts": {
            "morning_peak": 1,
            "off_peak": 1,
            "evening_peak": 1,
        },
    }
    assert set(controller._collector_metadata_by_period) == {
        "morning_peak",
        "off_peak",
        "evening_peak",
    }

    path = controller.save_checkpoint(tmp_path / "joint.pt")
    checkpoint = torch.load(path, map_location="cpu", weights_only=False)

    assert checkpoint["checkpoint_contract_version"] == 3
    assert checkpoint["episode_duration_s"] == 3600.0
    assert checkpoint["training_periods"] == [
        "morning_peak",
        "off_peak",
        "evening_peak",
    ]
    assert checkpoint["environment_contract"]["supported_periods"] == [
        "morning_peak",
        "off_peak",
        "evening_peak",
    ]


def test_joint_resume_rejects_noninitial_period_program_drift_before_update(
    controller,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "IPPO_TRAIN_PERIODS",
        "morning_peak,off_peak,evening_peak",
    )
    controller.initialize(_joint_metadata("morning_peak"))
    monkeypatch.setattr(controller, "_ppo_update", lambda episode_count=None: None)
    controller.ingest_parallel_rollouts(
        [
            _joint_rollout(controller, _joint_metadata(period))
            for period in ("morning_peak", "off_peak", "evening_peak")
        ],
        update=True,
    )
    checkpoint = controller.save_checkpoint(tmp_path / "joint-resume.pt")

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "train")
    monkeypatch.setenv("IPPO_MODEL_PATH", str(checkpoint))
    controller.initialize(_joint_metadata("morning_peak"))
    updates = []
    monkeypatch.setattr(
        controller,
        "_ppo_update",
        lambda episode_count=None: updates.append(episode_count),
    )
    saved_episode = controller._episode
    saved_contract = deepcopy(controller._joint_environment_contract)
    drifted_off_peak = _joint_metadata("off_peak")
    drifted_off_peak["intersections"]["demo_1"]["phases"][0][
        "green_seconds"
    ] += 1.0

    with pytest.raises(ValueError, match="program mismatch for off_peak"):
        controller.ingest_parallel_rollouts(
            [
                _joint_rollout(controller, _joint_metadata("morning_peak")),
                _joint_rollout(controller, drifted_off_peak),
                _joint_rollout(controller, _joint_metadata("evening_peak")),
            ],
            update=True,
        )

    assert controller._episode == saved_episode
    assert controller._buffer_episodes == []
    assert controller._joint_environment_contract == saved_contract
    assert updates == []


def test_joint_collector_pads_off_peak_to_global_action_dim(
    controller,
    monkeypatch,
):
    monkeypatch.setenv("IPPO_MODE", "collect")
    controller.prepare_collector(
        policy_state=None,
        policy_seed=7,
        rollout_seed=8,
        action_dim=4,
    )

    controller.initialize(_joint_metadata("off_peak"))

    assert controller._state_builder.max_phases == 1
    assert controller._act_dim == 4
    assert controller._model.act_dim == 4


def test_joint_collector_rejects_saved_program_drift_before_model_creation(
    controller,
    monkeypatch,
):
    monkeypatch.setenv(
        "IPPO_TRAIN_PERIODS",
        "morning_peak,off_peak,evening_peak",
    )
    controller.initialize(_joint_metadata("morning_peak"))
    monkeypatch.setattr(controller, "_ppo_update", lambda episode_count=None: None)
    controller.ingest_parallel_rollouts(
        [
            _joint_rollout(controller, _joint_metadata(period))
            for period in ("morning_peak", "off_peak", "evening_peak")
        ],
        update=True,
    )
    environment_contract = deepcopy(controller._joint_environment_contract)

    controller = importlib.reload(controller)
    monkeypatch.setenv("IPPO_MODE", "collect")
    controller.prepare_collector(
        policy_state=None,
        policy_seed=7,
        rollout_seed=8,
        action_dim=4,
        environment_contract=environment_contract,
    )
    drifted = _joint_metadata("off_peak")
    drifted["intersections"]["demo_1"]["phases"][0][
        "green_seconds"
    ] += 1.0

    with pytest.raises(
        ValueError,
        match="program mismatch for off_peak",
    ):
        controller.initialize(drifted)

    assert controller._model is None

def test_import_pressure_shaping_available() -> None:
    """PressureShaper and density_gate are importable in IPPO context."""
    from algorithms.common.pressure_shaping import (
        PressureShaper, density_gate, PressureRegretResult,
    )
    assert PressureShaper is not None
    assert density_gate is not None
