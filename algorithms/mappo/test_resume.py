"""Resume equivalence: save/load must reproduce a second update exactly."""

from __future__ import annotations

import torch

from algorithms.mappo.trainer import MAPPOTrainer, PPOBatch
from algorithms.mappo.config import MAPPOConfig
from algorithms.mappo.models import MAPPOPolicy
from algorithms.mappo.checkpoint import (
    CheckpointMetadata,
    load_checkpoint,
    save_checkpoint,
)
from algorithms.mappo.features import IPPO_V8_LOCAL_OBSERVATION_SCHEMA

REWARD_DEFINITION = "v5a:-0.60D+0.20F_safe-0.15B+0.05H;clip[-3,1]"


def _make_config(**kw):
    base = dict(
        intersection_ids=("demo_1", "demo_2"),
        model_version="cooperative_joint_v1",
        reward_scope="shared_team",
        critic_scope="global",
        critic_target_scope="team_return",
    )
    base.update(kw)
    return MAPPOConfig(**base)


def _make_policy(config: MAPPOConfig) -> MAPPOPolicy:
    return MAPPOPolicy(
        obs_dim=8, num_agents=2, critic_scope="global",
        actor_init_seed=1, critic_init_seed=2,
        model_version=config.model_version,
    )


def _make_batch(policy: MAPPOPolicy) -> PPOBatch:
    """2 agents x 2 joints 的共享团队目标 batch，on-policy 计算。"""
    obs_dim = policy.actor.obs_dim
    local_obs = torch.arange(4 * obs_dim, dtype=torch.float32).reshape(4, obs_dim) / 100.0
    phase_features = torch.zeros((4, 2, 11), dtype=torch.float32)
    phase_features[:, 0, 0] = 1.0
    phase_features[:, 1, 1] = 1.0
    action_mask = torch.ones((4, 2), dtype=torch.bool)
    actions = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_log_probs = policy.actor(
            local_obs, phase_features, action_mask
        ).log_prob(actions)
    first = torch.arange(2 * obs_dim, dtype=torch.float32).reshape(2, obs_dim) / 50.0
    global_obs = torch.stack((first, first, first + 7.0, first + 7.0))
    agent_mask = torch.ones((4, 2), dtype=torch.bool)
    owners = torch.tensor([0, 1, 0, 1])
    with torch.no_grad():
        old_values = policy.value(global_obs, agent_mask, owners).squeeze(-1)
    return PPOBatch(
        local_obs=local_obs,
        phase_features=phase_features,
        action_mask=action_mask,
        global_obs=global_obs,
        agent_mask=agent_mask,
        agent_index=owners,
        actions=actions,
        old_log_probs=old_log_probs,
        old_values=old_values,
        advantages=torch.tensor([1.0, 1.0, 3.0, 3.0]),
        returns=old_values + torch.tensor([0.5, 0.5, -0.25, -0.25]),
        joint_step_index=torch.tensor([0, 0, 1, 1]),
    )


def _metadata(config: MAPPOConfig) -> CheckpointMetadata:
    return CheckpointMetadata.from_config(
        config,
        episode=1,
        policy_generation=1,
        actor_init_seed=1,
        critic_init_seed=2,
        training_seed_start=95501,
        training_seed_end=95502,
        training_periods=("off_peak", "off_peak"),
        local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        reward_definition=REWARD_DEFINITION,
        training_workers=1,
        episode_duration_s=300.0,
    )


def test_resume_equivalence_synthetic(tmp_path):
    """一次 update -> 保存 -> 加载 -> 再 update 与连续两次 update 参数一致。"""
    config = _make_config()
    policy = _make_policy(config)
    trainer = MAPPOTrainer(policy, config)
    batch = _make_batch(policy)
    trainer.update(batch)
    d1 = {k: v.clone() for k, v in policy.actor.state_dict().items()}
    trainer.update(batch)
    d2 = {k: v.clone() for k, v in policy.actor.state_dict().items()}

    policy2 = _make_policy(config)
    trainer2 = MAPPOTrainer(policy2, config)
    batch2 = _make_batch(policy2)
    trainer2.update(batch2)
    ckpt_path = str(tmp_path / "mid.pt")
    save_checkpoint(ckpt_path, policy2, trainer2, metadata=_metadata(config))

    policy3 = _make_policy(config)
    trainer3 = MAPPOTrainer(policy3, config)
    load_checkpoint(
        ckpt_path, policy3, trainer3,
        expected_config=config,
        expected_local_observation_schema=IPPO_V8_LOCAL_OBSERVATION_SCHEMA,
        expected_reward_definition=REWARD_DEFINITION,
    )
    trainer3.update(batch2)
    e1 = {k: v.clone() for k, v in policy3.actor.state_dict().items()}
    for k in d2:
        assert torch.allclose(d2[k], e1[k], atol=1e-4, rtol=1e-4)
