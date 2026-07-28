"""
IPPO (Independent PPO) v3 —— 参数共享 + 局部奖励 + 训练稳定性修复。

v3 vs v2 变化：
  - 奖励：每路口独立 diff_waiting_time（不变，本来就是 local）
  - 新增：reward running normalization (RMS)
  - 新增：advantage normalization
  - 新增：explained_variance 日志
  - 新增：Huber loss for critic
  - 新增：critic_lr 独立于 actor_lr
  - 新增：grad_norm / kl / clip_fraction 日志
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


class StateBuilder:

# ═══════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════
    MAX_WAITING = 200.0
MAX_SPEED = 20.0
MAX_OCCUPANCY = 100.0
MAX_STAGE_ELAPSED = 120.0
MAX_PHASES = 8
MAX_LANES = 20

# PPO 超参数
ACTOR_LR = 3e-4
CRITIC_LR = 1e-4
CLIP_EPS = 0.2
GAMMA = 0.99
GAE_LAMBDA = 0.95
ENTROPY_COEF = 0.01
PPO_EPOCHS = 4
BATCH_SIZE = 128
ACCUMULATE_EPISODES = 4
CHECKPOINT_INTERVAL = 20
MAX_GRAD_NORM = 0.5
HUBER_DELTA = 10.0  # Huber loss delta for critic
REWARD_CLIP = 10.0  # clip normalized reward

# ── 全局状态 ──
_model: Optional[nn.Module] = None
_optimizer_actor: Optional[torch.optim.Adam] = None
_optimizer_critic: Optional[torch.optim.Adam] = None
_state_builder: Optional["StateBuilder"] = None
_episode: int = 0

# 训练 buffer
_buffer_obs: List[np.ndarray] = []
_buffer_actions: List[int] = []
_buffer_raw_rewards: List[float] = []  # 原始奖励（用于统计）
_buffer_rewards: List[float] = []      # 归一化后的奖励（用于训练）
_buffer_dones: List[bool] = []
_buffer_log_probs: List[float] = []
_buffer_values: List[float] = []
_buffer_episodes: List[dict] = []

# Reward normalization (running RMS)
_reward_rms_mean: float = 0.0
_reward_rms_var: float = 1.0
_reward_rms_count: int = 0

# 推理用
_phase_orders: Dict[str, List[int]] = {}
_inference_mode: str = "random"
_model_path: Optional[str] = None
_prev_waiting: Dict[str, float] = {}


# ═══════════════════════════════════════════
# 策略网络
# ═══════════════════════════════════════════
class IPPONetwork(nn.Module):
    """共享策略网络：MLP → actor (logits) + critic (value)."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: int = 128):
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.actor = nn.Linear(hidden, act_dim)
        self.critic = nn.Linear(hidden, 1)

    def forward(self, obs: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        h = self.shared(obs)
        logits = self.actor(h)
        value = self.critic(h)
        return logits, value


# ═══════════════════════════════════════════
# 状态构建器（与 v2 相同）
# ═══════════════════════════════════════════
class StateBuilder:
    def __init__(self, metadata: Dict[str, Any]) -> None:
        self._indices: Dict[str, _Idx] = {}
        for iid, i_meta in metadata.get("intersections", {}).items():
            self._indices[iid] = _build_index(i_meta)

    @property
    def max_state_dim(self) -> int:
        return max((ix.state_dim for ix in self._indices.values()), default=0)

    @property
    def max_phases(self) -> int:
        return max((ix.n_phases for ix in self._indices.values()), default=0)

    def build(self, iid: str, obs: Dict[str, Any]) -> np.ndarray:
        ix = self._indices.get(iid)
        i_obs = obs.get("intersections", {}).get(iid, {})
        if ix is None or not i_obs:
            return np.zeros(self.max_state_dim, dtype=np.float32)

        lanes_obs = i_obs.get("lanes", {})
        current_phase = i_obs.get("current_phase", ix.phase_order[0] if ix.phase_order else 0)
        stage_elapsed = float(i_obs.get("stage_elapsed", 0.0))
        parts: List[np.ndarray] = []

        phase_idx = ix.phase_index.get(current_phase, 0)
        oh = np.zeros(MAX_PHASES, dtype=np.float32)
        if 0 <= phase_idx < ix.n_phases:
            oh[phase_idx] = 1.0
        parts.append(oh)

        parts.append(np.array([min(stage_elapsed / max(MAX_STAGE_ELAPSED, 1.0), 1.0)], dtype=np.float32))

        for li in range(MAX_LANES):
            if li < len(ix.lane_order):
                lo = lanes_obs.get(ix.lane_order[li], {})
                feat = np.array([
                    float(lo.get("vehicle_count", 0)),
                    float(lo.get("halting_count", 0)),
                    min(float(lo.get("waiting_time", 0.0)) / max(MAX_WAITING, 1.0), 1.0),
                    min(float(lo.get("mean_speed", 0.0)) / max(MAX_SPEED, 1.0), 1.0),
                    min(float(lo.get("occupancy", 0.0)) / max(MAX_OCCUPANCY, 1.0), 1.0),
                ], dtype=np.float32)
            else:
                feat = np.zeros(5, dtype=np.float32)
            parts.append(feat)

        return np.concatenate(parts)

    def get_all_states(self, obs: Dict[str, Any]) -> Dict[str, np.ndarray]:
        return {iid: self.build(iid, obs) for iid in self._indices}

    def get_phase_order(self, iid: str) -> List[int]:
        ix = self._indices.get(iid)
        return ix.phase_order if ix else []


class _Idx:
    __slots__ = ("phase_order", "phase_index", "n_phases", "lane_order", "state_dim")
    def __init__(self):
        self.phase_order: List[int] = []
        self.phase_index: Dict[int, int] = {}
        self.n_phases: int = 0
        self.lane_order: List[str] = []
        self.state_dim: int = 0


def _build_index(i_meta: Dict[str, Any]) -> _Idx:
    ix = _Idx()
    ix.phase_order = [int(p) for p in i_meta.get("phase_order", [])]
    ix.phase_index = {p: i for i, p in enumerate(ix.phase_order)}
    ix.n_phases = len(ix.phase_order)
    incoming = list(i_meta.get("incoming_lanes", []))
    outgoing = list(i_meta.get("outgoing_lanes", []))
    ix.lane_order = incoming + outgoing
    ix.state_dim = MAX_PHASES + 1 + 5 * MAX_LANES
    return ix


# ═══════════════════════════════════════════
# Protocol 2.0 接口
# ═══════════════════════════════════════════

def initialize(payload: dict) -> dict:
    global _model, _optimizer_actor, _optimizer_critic, _state_builder, _phase_orders
    global _inference_mode, _model_path, _prev_waiting, _episode
    global _buffer_obs, _buffer_actions, _buffer_rewards, _buffer_raw_rewards, _buffer_dones
    global _buffer_log_probs, _buffer_values, _buffer_episodes
    global _reward_rms_mean, _reward_rms_var, _reward_rms_count

    _inference_mode = os.environ.get("IPPO_MODE", "random")
    _model_path = os.environ.get("IPPO_MODEL_PATH", None)

    _state_builder = StateBuilder(payload)
    _prev_waiting = {}

    _phase_orders = {}
    for iid, i_meta in payload.get("intersections", {}).items():
        _phase_orders[iid] = [int(p) for p in i_meta.get("phase_order", [])]

    obs_dim = _state_builder.max_state_dim
    act_dim = _state_builder.max_phases

    if _inference_mode not in ("random", "fixed"):
        if _inference_mode == "model" and _model_path and os.path.exists(_model_path):
            _model = IPPONetwork(obs_dim, act_dim)
            _model.load_state_dict(torch.load(_model_path, map_location="cpu"))
            _model.eval()
            logger.info("IPPO 推理: %s (obs=%d act=%d)", _model_path, obs_dim, act_dim)
        elif _model is None:
            _model = IPPONetwork(obs_dim, act_dim)
            # 分离 actor/critic optimizer
            actor_params = list(_model.shared.parameters()) + list(_model.actor.parameters())
            critic_params = list(_model.critic.parameters())
            _optimizer_actor = torch.optim.Adam(actor_params, lr=ACTOR_LR)
            _optimizer_critic = torch.optim.Adam(critic_params, lr=CRITIC_LR)
            _model.train()
            _reward_rms_mean = 0.0
            _reward_rms_var = 1.0
            _reward_rms_count = 0
            logger.info("IPPO v3 训练: obs=%d act=%d 路口=%d", obs_dim, act_dim, len(_phase_orders))

    # 清空单集 buffer（跨集 buffer 保留）
    _buffer_obs.clear(); _buffer_actions.clear(); _buffer_rewards.clear(); _buffer_raw_rewards.clear()
    _buffer_dones.clear(); _buffer_log_probs.clear(); _buffer_values.clear()

    return {"protocol_version": "2.0", "episode_id": payload["episode_id"], "ready": True}


def _normalize_reward(raw: float) -> float:
    """运行 RMS 标准化奖励（不减去均值，保留零点含义）。"""
    global _reward_rms_mean, _reward_rms_var, _reward_rms_count
    _reward_rms_count += 1
    # Welford online variance
    delta = raw - _reward_rms_mean
    _reward_rms_mean += delta / _reward_rms_count
    delta2 = raw - _reward_rms_mean
    _reward_rms_var = ((_reward_rms_count - 1) * _reward_rms_var + delta * delta2) / max(_reward_rms_count, 1)
    std = math.sqrt(_reward_rms_var) + 1e-8
    return float(np.clip(raw / std, -REWARD_CLIP, REWARD_CLIP))


def step(payload: dict) -> dict:
    global _prev_waiting

    obs = payload.get("intersections", {})
    states = _state_builder.get_all_states(payload)
    actions: Dict[str, int] = {}

    for iid in _phase_orders:
        state = states[iid]
        phase_order = _phase_orders[iid]

        # ── 选动作 ──
        if _inference_mode == "random":
            phase = int(np.random.choice(phase_order))
            log_prob, value = 0.0, 0.0
        elif _inference_mode == "fixed":
            phase = phase_order[0]
            log_prob, value = 0.0, 0.0
        elif _model is not None:
            with torch.no_grad() if _inference_mode == "model" else torch.enable_grad():
                t = torch.from_numpy(state).unsqueeze(0).float()
                logits, val = _model(t)
                probs = F.softmax(logits, dim=-1)
                dist = torch.distributions.Categorical(probs)

                if _inference_mode == "model":
                    action_idx = int(torch.argmax(probs, dim=-1).item())
                    log_prob, value = 0.0, 0.0
                else:
                    act = dist.sample()
                    action_idx = int(act.item())
                    log_prob = float(dist.log_prob(act).item())
                    value = float(val.item())

            idx = action_idx
            phase = phase_order[idx] if 0 <= idx < len(phase_order) else phase_order[0]
        else:
            phase = phase_order[0]
            log_prob, value = 0.0, 0.0

        actions[iid] = phase
        i_obs = obs.get(iid)
        if i_obs is None:
            continue

        # ── 局部奖励：每路口独立 diff_waiting_time ──
        total_wait = 0.0
        for lid, lo in i_obs.get("lanes", {}).items():
            total_wait += float(lo.get("waiting_time", 0.0))
        prev = _prev_waiting.get(iid, total_wait)
        raw_reward = prev - total_wait
        _prev_waiting[iid] = total_wait

        # ── 训练：记录 transition（用归一化后的奖励）──
        if _inference_mode == "train":
            norm_reward = _normalize_reward(raw_reward)
            _buffer_obs.append(state)
            _buffer_actions.append(action_idx)
            _buffer_raw_rewards.append(raw_reward)
            _buffer_rewards.append(norm_reward)
            _buffer_dones.append(False)
            _buffer_log_probs.append(log_prob)
            _buffer_values.append(value)

    signals = {iid: {"target_phase": phase} for iid, phase in actions.items() if phase is not None}
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signals, "vehicles": {}},
    }


def finish(payload: dict) -> None:
    global _episode

    if _inference_mode != "train" or _model is None:
        return

    if _buffer_dones:
        _buffer_dones[-1] = True

    ep_data = {
        "obs": list(_buffer_obs),
        "acts": list(_buffer_actions),
        "rews": list(_buffer_rewards),
        "raw_rews": list(_buffer_raw_rewards),
        "dones": list(_buffer_dones),
        "log_probs": list(_buffer_log_probs),
        "values": list(_buffer_values),
    }
    _buffer_episodes.append(ep_data)
    _episode += 1

    arrived = int(payload.get("arrived_vehicles", 0))
    departed = int(payload.get("departed_vehicles", 0))
    raw_mean = np.mean(ep_data["raw_rews"]) if ep_data["raw_rews"] else 0.0
    logger.info(
        "EP%d 完成: departed=%d arrived=%d steps=%d raw_reward_mean=%.2f rms_std=%.2f",
        _episode, departed, arrived, len(ep_data["obs"]), raw_mean, math.sqrt(_reward_rms_var),
    )

    if len(_buffer_episodes) >= ACCUMULATE_EPISODES:
        _ppo_update()

    if _episode > 0 and _episode % CHECKPOINT_INTERVAL == 0:
        save_dir = os.path.join(os.path.dirname(__file__), "checkpoints")
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"ippo_v3_ep{_episode}.pt")
        torch.save(_model.state_dict(), path)
        logger.info("Checkpoint: %s", path)

    _buffer_obs.clear(); _buffer_actions.clear(); _buffer_rewards.clear(); _buffer_raw_rewards.clear()
    _buffer_dones.clear(); _buffer_log_probs.clear(); _buffer_values.clear()


# ═══════════════════════════════════════════
# PPO 更新 (v3: huber critic + explained variance)
# ═══════════════════════════════════════════

def _ppo_update() -> None:
    episodes = _buffer_episodes[:ACCUMULATE_EPISODES]

    all_obs = np.concatenate([np.array(ep["obs"]) for ep in episodes], axis=0)
    all_acts = np.concatenate([np.array(ep["acts"]) for ep in episodes], axis=0)
    all_rews = np.concatenate([np.array(ep["rews"]) for ep in episodes], axis=0)
    all_raw_rews = np.concatenate([np.array(ep["raw_rews"]) for ep in episodes], axis=0)
    all_dones = np.concatenate([np.array(ep["dones"]) for ep in episodes], axis=0)
    all_log_probs = np.concatenate([np.array(ep["log_probs"]) for ep in episodes], axis=0)
    all_values = np.concatenate([np.array(ep["values"]) for ep in episodes], axis=0)

    # ── GAE ──
    advantages = np.zeros_like(all_rews)
    returns = np.zeros_like(all_rews)
    gae = 0.0
    next_value = 0.0
    for t in reversed(range(len(all_rews))):
        if all_dones[t]:
            gae = 0.0
            next_value = 0.0
        delta = all_rews[t] + GAMMA * next_value - all_values[t]
        gae = delta + GAMMA * GAE_LAMBDA * gae
        advantages[t] = gae
        returns[t] = gae + all_values[t]
        next_value = all_values[t]

    # ── Advantage normalization ──
    adv_mean = float(np.mean(advantages))
    adv_std = float(np.std(advantages)) + 1e-8
    advantages = (advantages - adv_mean) / adv_std

    # ── Stats ──
    raw_reward_mean = float(np.mean(all_raw_rews))
    raw_reward_std = float(np.std(all_raw_rews))
    return_mean = float(np.mean(returns))
    return_std = float(np.std(returns))
    value_mean = float(np.mean(all_values))
    value_std = float(np.std(all_values))

    # Explained variance
    var_residual = float(np.var(returns - all_values))
    var_total = float(np.var(returns)) + 1e-8
    explained_var = 1.0 - var_residual / var_total

    # ── Tensor 化 ──
    obs_t = torch.from_numpy(all_obs).float()
    acts_t = torch.from_numpy(all_acts).long()
    adv_t = torch.from_numpy(advantages).float()
    ret_t = torch.from_numpy(returns).float()
    old_logp_t = torch.from_numpy(all_log_probs).float()

    total_samples = len(all_rews)
    n_batches = max(1, total_samples // BATCH_SIZE)
    total_actor_loss = 0.0
    total_critic_loss = 0.0
    total_entropy = 0.0
    total_approx_kl = 0.0
    total_clip_frac = 0.0
    n_updates = 0

    for epoch in range(PPO_EPOCHS):
        perm = torch.randperm(total_samples)
        for bi in range(n_batches):
            idx = perm[bi * BATCH_SIZE : (bi + 1) * BATCH_SIZE]
            if len(idx) == 0:
                continue
            n_updates += 1

            logits, values = _model(obs_t[idx])
            probs = F.softmax(logits, dim=-1)
            dist = torch.distributions.Categorical(probs)
            new_logp = dist.log_prob(acts_t[idx])
            entropy = dist.entropy().mean()

            # KL
            with torch.no_grad():
                old_probs = F.softmax(logits.detach(), dim=-1)
                old_dist = torch.distributions.Categorical(old_probs)
                kl = torch.distributions.kl_divergence(old_dist, dist).mean()
                ratio = torch.exp(new_logp - old_logp_t[idx])
                clip_frac = ((ratio - 1.0).abs() > CLIP_EPS).float().mean()

            # Actor loss
            ratio = torch.exp(new_logp - old_logp_t[idx])
            surr1 = ratio * adv_t[idx]
            surr2 = torch.clamp(ratio, 1 - CLIP_EPS, 1 + CLIP_EPS) * adv_t[idx]
            actor_loss = -torch.min(surr1, surr2).mean()

            # Critic loss (Huber)
            value_pred = values.squeeze(-1)
            value_target = ret_t[idx]
            critic_loss = F.huber_loss(value_pred, value_target, delta=HUBER_DELTA)

            loss = actor_loss + 0.5 * critic_loss - ENTROPY_COEF * entropy

            _optimizer_actor.zero_grad()
            _optimizer_critic.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(_model.parameters(), MAX_GRAD_NORM)
            _optimizer_actor.step()
            _optimizer_critic.step()

            total_actor_loss += float(actor_loss)
            total_critic_loss += float(critic_loss)
            total_entropy += float(entropy)
            total_approx_kl += float(kl)
            total_clip_frac += float(clip_frac)

    _buffer_episodes[:ACCUMULATE_EPISODES] = []

    logger.info(
        "PPO 更新: samples=%d | "
        "rew_raw=%.1f±%.1f ret=%.1f±%.1f val=%.1f±%.1f ev=%.3f | "
        "actor=%.4f critic=%.4f ent=%.3f kl=%.4f clip=%.3f",
        total_samples,
        raw_reward_mean, raw_reward_std,
        return_mean, return_std,
        value_mean, value_std,
        explained_var,
        total_actor_loss / max(n_updates, 1),
        total_critic_loss / max(n_updates, 1),
        total_entropy / max(n_updates, 1),
        total_approx_kl / max(n_updates, 1),
        total_clip_frac / max(n_updates, 1),
    )
