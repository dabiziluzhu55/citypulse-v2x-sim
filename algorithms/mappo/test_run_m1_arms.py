# M1 三 arm 编排：计划生成与 shared-init → checkpoint 转换单测。
from __future__ import annotations

from pathlib import Path

import pytest
import torch

from algorithms.mappo.checkpoint import read_checkpoint_metadata
from algorithms.mappo.config import (
    COOPERATIVE_M1_MODEL_VERSION,
    REWARD_SCOPE_SHARED_TEAM,
)
from algorithms.mappo.run_m1_arms import (
    arm_config,
    build_arm_plan,
    convert_shared_init_to_checkpoint,
    load_diagnostics,
)
from algorithms.mappo.shared_init import create_shared_init, load_shared_init


def test_plan_has_three_arms_and_smoke_first() -> None:
    plan = build_arm_plan(
        shared_init="runs/mappo_v2/m0/mappo_v2_shared_init.pt",
        adjacency="runs/mappo_v2/m0/intersection_adjacency_m1_symmetric.json",
        manifest="runs/mappo_v2/m0/mappo_v2_preregistration.yaml",
    )
    assert [step["arm"] for step in plan["arms"]] == ["m1_0", "m1_a", "m1_b"]
    assert plan["smoke_before_train"] is True
    assert plan["m1_0_alone_first"] is True
    assert plan["invalid_run_policy"]["training_worker_drop"] == "whole arm invalid"


def test_plan_arms_match_arm_specs() -> None:
    plan = build_arm_plan(
        shared_init="s.pt", adjacency="a.json", manifest="m.yaml"
    )
    by_arm = {step["arm"]: step for step in plan["arms"]}
    assert by_arm["m1_0"]["target_mode"] == "m1_0_scalar"
    assert by_arm["m1_0"]["weights"] == (0.0, 0.0, 1.0)
    assert "adjacency" not in by_arm["m1_0"]
    assert by_arm["m1_a"]["target_mode"] == "per_agent"
    assert by_arm["m1_a"]["weights"] == (0.95, 0.0, 0.05)
    assert by_arm["m1_a"]["adjacency"] == "a.json"
    assert by_arm["m1_b"]["target_mode"] == "per_agent"
    assert by_arm["m1_b"]["weights"] == (0.70, 0.25, 0.05)
    assert by_arm["m1_b"]["adjacency"] == "a.json"


def test_arm_config_m1_0_scalar() -> None:
    config = arm_config("m1_0", intersections=20)
    assert config.model_version == COOPERATIVE_M1_MODEL_VERSION
    assert config.reward_scope == REWARD_SCOPE_SHARED_TEAM
    assert config.m1_target_mode == "m1_0_scalar"
    assert (
        config.m1_local_weight,
        config.m1_neighbor_weight,
        config.m1_team_weight,
    ) == (0.0, 0.0, 1.0)
    assert config.m1_adjacency_path is None


def test_arm_config_per_agent_requires_adjacency() -> None:
    with pytest.raises(ValueError):
        arm_config("m1_a", intersections=20, adjacency=None)
    config = arm_config("m1_a", intersections=20, adjacency="a.json")
    assert config.m1_target_mode == "per_agent"
    assert config.m1_adjacency_path == "a.json"


def test_convert_shared_init_to_checkpoint_roundtrip(tmp_path: Path) -> None:
    shared_path = tmp_path / "shared.pt"
    create_shared_init(
        str(shared_path), model_version=COOPERATIVE_M1_MODEL_VERSION
    )
    ckpt_path = tmp_path / "m1_0_init.pt"
    convert_shared_init_to_checkpoint(
        str(shared_path),
        str(ckpt_path),
        arm="m1_0",
        intersections=20,
        training_workers=8,
        episode_duration_s=300.0,
    )
    assert ckpt_path.is_file()
    meta = read_checkpoint_metadata(ckpt_path)
    assert meta.episode == 0
    assert meta.policy_generation == 0
    assert meta.model_version == COOPERATIVE_M1_MODEL_VERSION
    assert meta.m1_arm == "m1_0"
    assert meta.m1_target_mode == "m1_0_scalar"
    assert meta.training_seed_start == 0
    assert meta.training_seed_end == 0
    assert meta.training_workers == 8
    assert meta.episode_duration_s == 300.0

    shared = load_shared_init(str(shared_path))
    payload = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    state = payload["policy_state_dict"]
    for module in ("actor", "critic"):
        expected = shared["policy"][module]
        for key, value in expected.items():
            assert torch.equal(state[f"{module}.{key}"], value), (
                module,
                key,
            )
    assert "actor_optimizer_state_dict" in payload
    assert "critic_optimizer_state_dict" in payload


def test_load_diagnostics_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_diagnostics(tmp_path / "missing.json") == {}


def test_load_diagnostics_parses_json(tmp_path: Path) -> None:
    payload = {"status": "complete", "batches": []}
    path = tmp_path / "diag.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    assert load_diagnostics(path) == payload
