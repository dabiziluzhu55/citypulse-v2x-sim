from __future__ import annotations

import numpy as np
import torch

from algorithms.ippo.controller import (
    IPPONetwork,
    PHASE_FEATURES,
    StateBuilder as IPPOStateBuilder,
    _masked_categorical,
)
from algorithms.mappo.features import IPPOV8FeatureBuilder
from algorithms.mappo.models import CandidateActor, LocalCritic


def _metadata() -> dict:
    intersections = {}
    for offset, phase_count in enumerate((2, 1)):
        intersection_id = f"demo_{offset + 1}"
        incoming = f"in_{offset}"
        outgoing = f"out_{offset}"
        connection = f"connection_{offset}"
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
                    "connection_id": connection,
                    "from_lane": incoming,
                    "to_lane": outgoing,
                }
            ],
            "phases": {
                phase: {
                    "green_seconds": 30.0,
                    "connection_priorities": {connection: "protected"},
                }
                for phase in range(phase_count)
            },
        }
    return {
        "episode_id": "parity",
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": intersections,
    }


def _payload() -> dict:
    intersections = {}
    for offset in range(2):
        intersections[f"demo_{offset + 1}"] = {
            "current_phase": 0,
            "stage": "GREEN",
            "stage_elapsed": 20.0,
            "pending_phase": None,
            "lanes": {
                f"in_{offset}": {
                    "vehicle_count": 10 + offset,
                    "halting_count": 4 + offset,
                    "waiting_time": 100.0 - 10.0 * offset,
                    "mean_speed": 7.5,
                    "occupancy": 25.0,
                },
                f"out_{offset}": {
                    "vehicle_count": 2,
                    "halting_count": 1,
                    "waiting_time": 5.0,
                    "mean_speed": 12.0,
                    "occupancy": 10.0,
                },
            },
        }
    vehicles = {
        "near": {
            "motion": {"speed_mps": 10.0},
            "location": {
                "route_edges": ["in_edge_0", "out_edge_0"],
                "route_index": 0,
            },
            "next_signal": {
                "intersection_id": "demo_1",
                "distance_m": 100.0,
            },
        },
        "far": {
            "motion": {"speed_mps": 4.0},
            "location": {
                "route_edges": ["in_edge_0", "out_edge_0"],
                "route_index": 0,
            },
            "next_signal": {
                "intersection_id": "demo_1",
                "distance_m": 100.0,
            },
        },
    }
    return {
        "episode_id": "parity",
        "step_id": 4,
        "simulation_time": 20.0,
        "intersections": intersections,
        "vehicles": vehicles,
    }


def test_feature_adapter_matches_ippo_state_phase_eta_and_mask() -> None:
    metadata = _metadata()
    payload = _payload()
    expected = IPPOStateBuilder(metadata)
    actual = IPPOV8FeatureBuilder(metadata)

    for intersection_id in expected.intersection_ids:
        np.testing.assert_array_equal(
            actual.build(intersection_id, payload),
            expected.build(intersection_id, payload),
        )
        np.testing.assert_array_equal(
            actual.build_phase_features(
                intersection_id,
                payload["intersections"][intersection_id],
                simulation_time=20.0,
                last_service_times={phase: 0.0 for phase in expected.get_phase_order(intersection_id)},
                vehicles=payload["vehicles"],
                demand_horizon_seconds=15.0,
            ),
            expected.build_phase_features(
                intersection_id,
                payload["intersections"][intersection_id],
                simulation_time=20.0,
                last_service_times={phase: 0.0 for phase in expected.get_phase_order(intersection_id)},
                vehicles=payload["vehicles"],
                demand_horizon_seconds=15.0,
            ),
        )
        actual_mask, actual_forced = actual.build_action_mask(
            intersection_id,
            payload["intersections"][intersection_id],
            max_green_factor=2.0,
        )
        expected_mask, expected_forced = expected.build_action_mask(
            intersection_id,
            payload["intersections"][intersection_id],
            max_green_factor=2.0,
        )
        np.testing.assert_array_equal(actual_mask, expected_mask)
        assert actual_forced == expected_forced


def test_candidate_actor_loaded_from_ippo_is_bitwise_equivalent() -> None:
    torch.manual_seed(123)
    ippo = IPPONetwork(obs_dim=6, act_dim=4, hidden=8)
    actor = CandidateActor(obs_dim=6, phase_feature_dim=PHASE_FEATURES, hidden_dim=8)
    actor.load_state_dict(
        {
            name: tensor
            for name, tensor in ippo.state_dict().items()
            if name.startswith("actor_body.") or name.startswith("phase_actor.")
        }
    )
    obs = torch.arange(18, dtype=torch.float32).reshape(3, 6) / 10.0
    phase_features = torch.arange(
        3 * 4 * PHASE_FEATURES, dtype=torch.float32
    ).reshape(3, 4, PHASE_FEATURES) / 100.0
    action_mask = torch.tensor(
        [
            [True, True, True, True],
            [True, False, True, False],
            [False, True, False, False],
        ]
    )

    expected = _masked_categorical(
        ippo.actor_forward(obs, phase_features), action_mask
    )
    actual = actor(obs, phase_features, action_mask)

    torch.testing.assert_close(actual.probs, expected.probs, rtol=0, atol=0)
    actions = torch.tensor([3, 2, 1])
    torch.testing.assert_close(
        actual.log_prob(actions), expected.log_prob(actions), rtol=0, atol=0
    )


def test_local_critic_loaded_from_ippo_is_bitwise_equivalent() -> None:
    torch.manual_seed(456)
    ippo = IPPONetwork(obs_dim=6, act_dim=4, hidden=8)
    critic = LocalCritic(obs_dim=6, num_agents=2, hidden_dim=8)
    critic.load_state_dict(
        {
            name: tensor
            for name, tensor in ippo.state_dict().items()
            if name.startswith("critic_body.") or name.startswith("critic.")
        }
    )
    local_obs = torch.arange(18, dtype=torch.float32).reshape(3, 6) / 10.0
    global_obs = torch.zeros((3, 2, 6), dtype=torch.float32)
    owner = torch.tensor([0, 1, 0])
    global_obs[torch.arange(3), owner] = local_obs
    mask = torch.ones((3, 2), dtype=torch.bool)

    torch.testing.assert_close(
        critic(global_obs, mask, owner),
        ippo.critic_forward(local_obs),
        rtol=0,
        atol=0,
    )
