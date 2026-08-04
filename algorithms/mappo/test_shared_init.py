import torch

from algorithms.mappo.shared_init import create_shared_init, load_shared_init


def test_shared_init_roundtrip(tmp_path):
    path = str(tmp_path / "mappo_v2_shared_init.pt")
    meta = create_shared_init(out_path=path, obs_dim=132, num_agents=20)
    loaded = load_shared_init(path)
    assert loaded["meta"]["obs_dim"] == 132
    assert loaded["meta"]["num_agents"] == 20
    assert "sha256" in loaded["meta"]
    # 两次创建在同一 seed 下 actor 参数一致（确定性）
    path2 = str(tmp_path / "second.pt")
    create_shared_init(out_path=path2, obs_dim=132, num_agents=20)
    a = torch.load(path, map_location="cpu", weights_only=False)
    b = torch.load(path2, map_location="cpu", weights_only=False)
    for k in a["policy"]["actor"]:
        assert torch.equal(a["policy"]["actor"][k], b["policy"]["actor"][k])


def test_shared_init_m1_uses_agent_conditioned_critic(tmp_path):
    from algorithms.mappo.config import COOPERATIVE_M1_MODEL_VERSION
    from algorithms.mappo.models import AgentConditionedCritic, MAPPOPolicy

    path = str(tmp_path / "mappo_v2_shared_init.pt")
    meta = create_shared_init(
        out_path=path,
        obs_dim=132,
        num_agents=20,
        model_version=COOPERATIVE_M1_MODEL_VERSION,
    )
    assert meta["model_version"] == COOPERATIVE_M1_MODEL_VERSION
    payload = torch.load(path, map_location="cpu", weights_only=False)
    policy = MAPPOPolicy(
        obs_dim=132,
        num_agents=20,
        critic_scope="global",
        actor_init_seed=42,
        critic_init_seed=43,
        model_version=COOPERATIVE_M1_MODEL_VERSION,
    )
    policy.actor.load_state_dict(payload["policy"]["actor"])
    policy.critic.load_state_dict(payload["policy"]["critic"])
    assert isinstance(policy.critic, AgentConditionedCritic)
