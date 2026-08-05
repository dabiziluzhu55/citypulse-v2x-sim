from __future__ import annotations

from pathlib import Path

import pytest
import torch

from algorithms.mappo.checkpoint import CheckpointMetadata, save_checkpoint
from algorithms.mappo.config import (
    MAPPOConfig,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.evaluate_checkpoint import (
    _summarize,
    load_evaluation_checkpoint,
    validate_evaluation_seeds,
)
from algorithms.mappo.features import IPPO_V8_LOCAL_OBSERVATION_SCHEMA
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.train import REWARD_DEFINITION
from algorithms.mappo.trainer import MAPPOTrainer


def _checkpoint(
    tmp_path: Path,
) -> tuple[Path, MAPPOConfig, MAPPOPolicy]:
    config = MAPPOConfig(
        ("demo_1", "demo_2"),
        hidden_dim=8,
        model_version="cooperative_joint_v1",
        actor_variant="shared",
        reward_scope=REWARD_SCOPE_SHARED_TEAM,
        critic_target_scope="team_return",
    )
    policy = MAPPOPolicy(
        obs_dim=config.obs_dim,
        num_agents=20,
        critic_scope="global",
        actor_init_seed=12,
        critic_init_seed=34,
        hidden_dim=config.hidden_dim,
        phase_feature_dim=config.phase_feature_dim,
        model_version=config.model_version,
        actor_variant=config.actor_variant,
        identity_offset=config.identity_offset,
    )
    trainer = MAPPOTrainer(policy, config)
    metadata = CheckpointMetadata.from_config(
        config,
        episode=16,
        policy_generation=4,
        actor_init_seed=12,
        critic_init_seed=34,
        training_seed_start=93101,
        training_seed_end=93116,
        training_periods=("off_peak",),
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        reward_definition=REWARD_DEFINITION,
    )
    path = tmp_path / "mappo.pt"
    save_checkpoint(path, policy, trainer, metadata)
    return path, config, policy


def test_evaluation_checkpoint_loads_validated_cpu_policy_snapshot(
    tmp_path: Path,
) -> None:
    path, config, expected_policy = _checkpoint(tmp_path)

    snapshot = load_evaluation_checkpoint(path)

    assert snapshot.config == config
    assert snapshot.metadata.episode == 16
    assert snapshot.metadata.policy_generation == 4
    assert snapshot.metadata.training_seed_start == 93101
    for name, value in expected_policy.state_dict().items():
        torch.testing.assert_close(
            snapshot.policy_state[name], value, rtol=0, atol=0
        )


def test_evaluation_checkpoint_reconstructs_cooperative_objective(
    tmp_path: Path,
) -> None:
    path, config, expected_policy = _checkpoint(tmp_path)

    snapshot = load_evaluation_checkpoint(path)

    assert snapshot.config == config
    assert snapshot.config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    assert snapshot.config.critic_target_scope == "team_return"
    assert snapshot.config.team_reward_schema == config.team_reward_schema
    assert snapshot.config.joint_step_schema == config.joint_step_schema
    for name, value in expected_policy.state_dict().items():
        torch.testing.assert_close(
            snapshot.policy_state[name], value, rtol=0, atol=0
        )


def test_evaluation_seeds_must_not_overlap_checkpoint_training_range(
    tmp_path: Path,
) -> None:
    path, _, _ = _checkpoint(tmp_path)
    snapshot = load_evaluation_checkpoint(path)

    validate_evaluation_seeds(snapshot.metadata, (63101, 63102))
    with pytest.raises(ValueError, match="overlap.*93110"):
        validate_evaluation_seeds(snapshot.metadata, (63101, 93110))


def test_failed_evaluation_seed_is_counted_as_missing_metric() -> None:
    summary = _summarize(
        (
            {
                "status": "complete",
                "arrived": 10,
                "official_metrics": {"avg_waiting_time_s": 2.0},
            },
            {"status": "failed", "seed": 63102},
        )
    )

    assert summary["arrived"]["available_runs"] == 1
    assert summary["arrived"]["missing_runs"] == 1
    waiting = summary["official_metrics"]["avg_waiting_time_s"]
    assert waiting["available_runs"] == 1
    assert waiting["missing_runs"] == 1


def test_evaluation_summary_merges_action_diagnostics() -> None:
    action_diagnostics = {
        "intersections": {
            "demo_1": {
                "decision_count": 1,
                "candidates": [
                    {
                        "candidate_index": 0,
                        "phase": 0,
                        "available_count": 1,
                        "selected_count": 1,
                        "selection_rate_when_available": 1.0,
                        "never_selected_while_available": False,
                        "max_available_opportunities_without_selection": 0,
                        "green_observation_count": 1,
                        "green_entry_count": 1,
                        "max_observed_service_gap_s": 0.0,
                    }
                ],
            }
        }
    }

    summary = _summarize(
        (
            {
                "status": "complete",
                "arrived": 10,
                "official_metrics": {},
                "action_diagnostics": action_diagnostics,
            },
        )
    )

    merged = summary["action_diagnostics"]
    assert merged["episodes_available"] == 1
    candidate = merged["intersections"]["demo_1"]["candidates"][0]
    assert candidate["selected_count"] == 1
    assert candidate["selection_rate_when_available"] == 1.0


def test_load_evaluation_checkpoint_accepts_zero_shot_subset(
    tmp_path: Path,
) -> None:
    path, config, _ = _checkpoint(tmp_path)
    assert config.intersection_ids == ("demo_1", "demo_2")

    snapshot = load_evaluation_checkpoint(
        path, intersection_ids=("demo_1",)
    )

    assert snapshot.config.intersection_ids == ("demo_1",)
    assert snapshot.metadata.intersection_ids == ("demo_1", "demo_2")
    assert snapshot.policy_state

    with pytest.raises(ValueError):
        load_evaluation_checkpoint(path, intersection_ids=("demo_9",))
