"""
CoSLight-P0 Policy Controller — Transformer + 4-component reward.
Multi-intersection coordination via attention-based collaborator selection.

Protocol 2.0: initialize(payload) / step(payload) / finish(payload).
"""
import os, json, sys, time, numpy as np, torch, torch.nn as nn
from collections import defaultdict
from typing import Dict, List, Any
import math, glob

# ============================================================
# 配置
# ============================================================
OBS_DIM = 56              # 状态向量维度：8 相位 + 8 车道 × 6 特征
ACT_DIM = 8               # 最大相位数量
MAX_LANES = 8             # 每个路口最多车道数
LANE_FEAT_KEYS = (        # 车道特征键（Protocol 2.0 字段）
    "vehicle_count", "halting_count", "occupancy",
    "queue_length_m", "mean_speed", "waiting_time",
)
TRANS_HIDDEN = 64         # Transformer 隐藏层维度
TRANS_HEADS = 4           # 多头注意力头数
TRANS_LAYERS = 2          # Transformer 编码器层数
USE_K = 1                 # 协作者 Top-K 数量

# PPO 超参数
LR = 3e-4
CLIP_EPS = 0.2
GAMMA = 0.99
GAE_LAMBDA = 0.95
ENTROPY_COEF = 0.01
PPO_EPOCHS = 4
BATCH_SIZE = 64
ACCUMULATE_EPISODES = 4   # 攒 N 集做一次批量 PPO

# Checkpoint
CHECKPOINT_INTERVAL = 20
MAX_CHECKPOINTS = 5
# 使用最新的 torch.save; 加载模型时使用 checkpoint_load

# 全局模式标记: 设为 True 时跳过 PPO 更新
EVAL_MODE = False

# ============================================================
# 奖励超参数
# ============================================================
ETA = 0.3
LAMBDA_SPILL = 0.5
LAMBDA_SW = 0.05
SPILL_THRESHOLD = 0.7
VEHICLE_LENGTH = 5.0
DEFAULT_GREEN_DURATION = 30.0

# ============================================================
# 全局状态字典 (无锁, 由 Protocol 2.0 单线程调用)
# ============================================================
_prev_pressure = {}
_prev_actions = {}
_actions_for_reward = {}
_lane_capacity = {}
_neighbors = {}
_incoming = {}
_outgoing = {}
_phase_durations = {}

_model: nn.Module = None
_optimizer: torch.optim.Adam = None
_tls_order: List[str] = []
_num_agents: int = 0
_phase_counts: Dict[str, int] = {}
_buffer: dict = None
_batch_buffer: dict = {}
_episode: int = 0
_evaluation_episode_id: str = ""


# ============================================================
# 模型
# ============================================================
class PositionalEncoding(nn.Module):
    """正弦位置编码。"""
    def __init__(self, d_model, max_len=100):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len).unsqueeze(1).float()
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe)

    def forward(self, x):
        return x + self.pe[:x.size(1)].unsqueeze(0)


class MultiAgentActor(nn.Module):
    """多路口协同 Actor-Critic：Transformer + Top-K 协作者 + 集中式 Critic。"""
    def __init__(self, num_agents, obs_dim=OBS_DIM, hidden=TRANS_HIDDEN):
        super().__init__()
        self.num_agents = num_agents
        self.hidden = hidden
        self.obs_embed = nn.Linear(obs_dim, hidden)
        self.pos_enc = PositionalEncoding(hidden)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden, nhead=TRANS_HEADS, dim_feedforward=hidden * 2,
            dropout=0.1, batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=TRANS_LAYERS)
        self.actor_head = nn.Linear(hidden * 2, ACT_DIM)
        self.critic_head = nn.Sequential(
            nn.Linear(hidden * num_agents, hidden),
            nn.ReLU(),
            nn.Linear(hidden, 1),
        )
        self.score_head = nn.Linear(hidden, hidden)

    def forward(self, obs: torch.Tensor):
        B, N, _ = obs.shape
        x = self.obs_embed(obs)
        x = self.pos_enc(x)
        x = self.transformer(x)

        # 协作者分数, 排除自己 (B_no_self fix)
        scores = torch.matmul(
            self.score_head(x),
            self.score_head(x).transpose(-2, -1)
        )
        self_mask = torch.eye(N, device=obs.device).unsqueeze(0).bool()
        scores = scores.masked_fill(self_mask, float('-inf'))

        if USE_K > 0 and N > 1:
            topk_idx = torch.topk(scores, k=min(USE_K, N - 1), dim=-1)[1]
            collab_feat = torch.gather(
                x.unsqueeze(1).expand(-1, N, -1, -1),
                2,
                topk_idx.unsqueeze(-1).expand(-1, -1, -1, self.hidden)
            ).mean(dim=2)
        else:
            collab_feat = torch.zeros(B, N, self.hidden, device=obs.device)

        actor_in = torch.cat([x, collab_feat], dim=-1)
        actor_logits = self.actor_head(actor_in)

        central_in = x.reshape(B, N * self.hidden)
        values = self.critic_head(central_in)
        values = values.unsqueeze(1).expand(-1, N, -1)

        return actor_logits, values, scores


# ============================================================
# 状态构造
# ============================================================
def build_state(intersections: Dict[str, Any]) -> np.ndarray:
    """从路口数据构造 [N, 56] 状态。每路口: 相位 one-hot(8) + 车道特征(MAX_LANES×6)。"""
    obs = np.zeros((_num_agents, OBS_DIM), dtype=np.float32)
    for idx, tid in enumerate(_tls_order):
        if tid not in intersections:
            continue
        data = intersections[tid]
        phase = data.get("current_phase", 0)
        max_p = _phase_counts.get(tid, ACT_DIM)
        obs[idx, min(phase, max_p - 1)] = 1.0
        lanes = list(data.get("lanes", {}).values())
        for li, lane in enumerate(lanes):
            if li >= MAX_LANES:
                break
            base = ACT_DIM + li * len(LANE_FEAT_KEYS)
            for fi, key in enumerate(LANE_FEAT_KEYS):
                obs[idx, base + fi] = float(lane.get(key, 0))
    return obs


# ============================================================
# 奖励函数
# ============================================================
def compute_pressure(intersections, tls_id):
    """P = Σ(入口 q/C) - Σ(出口 q/C)。"""
    if tls_id not in intersections:
        return 0.0
    data = intersections[tls_id]
    lanes = data.get("lanes", {})
    incoming = sum(
        float(lanes.get(lid, {}).get("vehicle_count", 0)) / max(_lane_capacity.get(lid, 50.0), 1.0)
        for lid in _incoming.get(tls_id, []) if lid in lanes
    )
    outgoing = sum(
        float(lanes.get(lid, {}).get("vehicle_count", 0)) / max(_lane_capacity.get(lid, 50.0), 1.0)
        for lid in _outgoing.get(tls_id, []) if lid in lanes
    )
    return incoming - outgoing


def compute_reward(payload: dict) -> float:
    """四分量奖励: r = ΔP + η·avg(ΔP_neighbor) - λ_spill·S - λ_sw·I_switch。"""
    global _prev_pressure, _prev_actions
    intersections = payload.get("intersections", {})
    total = 0.0
    for tid in _tls_order:
        if tid not in intersections:
            continue
        P_now = compute_pressure(intersections, tid)
        P_before = _prev_pressure.get(tid, P_now)
        delta_P = P_before - P_now

        regional = 0.0
        nids = _neighbors.get(tid, [])
        if nids:
            regional = sum(
                _prev_pressure.get(nid, compute_pressure(intersections, nid))
                - compute_pressure(intersections, nid)
                for nid in nids if nid in intersections
            ) / len(nids)

        spill = sum(
            max(0.0, float(
                intersections.get(tid, {}).get("lanes", {}).get(lid, {}).get("occupancy", 0)
            ) - SPILL_THRESHOLD)
            for lid in _outgoing.get(tid, [])
        )

        switch = 1.0 if (
            _prev_actions.get(tid, -1) >= 0
            and _actions_for_reward.get(tid, -1) >= 0
            and _prev_actions.get(tid) != _actions_for_reward.get(tid)
        ) else 0.0

        r = delta_P + ETA * regional - LAMBDA_SPILL * spill - LAMBDA_SW * switch
        _prev_pressure[tid] = P_now
        total += r

    for tid, a in _actions_for_reward.items():
        _prev_actions[tid] = a
    return total / max(len(_tls_order), 1)


# ============================================================
# Checkpoint
# ============================================================
def _save_checkpoint():
    """保存模型，自动清理旧文件保留最近 MAX_CHECKPOINTS 个。"""
    d = os.path.join(os.path.dirname(__file__), "checkpoints")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"model_ep{_episode}.pt")
    torch.save(_model.state_dict(), path)
    print(f"[CoSLight] checkpoint 已保存: ep={_episode}")

    files = sorted(glob.glob(os.path.join(d, "model_ep*.pt")), key=os.path.getmtime)
    while len(files) > MAX_CHECKPOINTS:
        old = files.pop(0)
        os.remove(old)
        print(f"[CoSLight] 清理旧 checkpoint: {os.path.basename(old)}")


def checkpoint_load(path: str):
    """加载预训练模型用于评估。"""
    global _model, EVAL_MODE
    EVAL_MODE = True
    if _model is None:
        _model = MultiAgentActor(_num_agents)
    _model.load_state_dict(torch.load(path, map_location='cpu'))
    _model.eval()
    print(f"[CoSLight] 评估模式：已加载 {path}")


# ============================================================
# Protocol 2.0 接口
# ============================================================
def initialize(payload: dict) -> dict:
    """仿真启动时调用。加载路网拓扑、车道容量、相位时长。"""
    global _tls_order, _num_agents, _phase_counts, _model, _optimizer, _buffer, _episode
    global _prev_pressure, _prev_actions, _lane_capacity, _neighbors, _incoming, _outgoing
    global _phase_durations
    global _evaluation_episode_id

    intersections_meta = payload.get("intersections", {})
    _tls_order = sorted(intersections_meta.keys())
    _num_agents = len(_tls_order)

    _prev_pressure.clear(); _prev_actions.clear(); _lane_capacity.clear()
    _neighbors.clear(); _incoming.clear(); _outgoing.clear()
    _phase_durations.clear()

    for tid, meta in intersections_meta.items():
        order = meta.get("phase_order", list(range(ACT_DIM)))
        _phase_counts[tid] = len(order) if order else ACT_DIM
        _neighbors[tid] = list(meta.get("direct_neighbors", []))
        _incoming[tid] = list(meta.get("incoming_lanes", []))
        _outgoing[tid] = list(meta.get("outgoing_lanes", []))

        for lid, lm in meta.get("lanes", {}).items():
            length_m = float(lm.get("length", lm.get("length_m", 50.0)))
            _lane_capacity[lid] = max(length_m / VEHICLE_LENGTH, 1.0)

        durations = meta.get("phase_durations")
        if durations and isinstance(durations, dict):
            _phase_durations[tid] = {int(k): float(v) for k, v in durations.items()}

    if _model is None and not EVAL_MODE:
        _model = MultiAgentActor(_num_agents)
        _optimizer = torch.optim.Adam(_model.parameters(), lr=LR)
    elif EVAL_MODE and _model is not None:
        _model.eval()

    _buffer = defaultdict(list)
    _episode += 1
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_runtime.start("CoSLight", payload)
    _evaluation_episode_id = str(payload.get("episode_id", ""))
    print(f"[CoSLight] 初始化：{_num_agents} 个路口，ep={_episode}"
          + (" [评估模式]" if EVAL_MODE else ""))
    return {"protocol_version": "2.0", "episode_id": payload.get("episode_id", ""), "ready": True}


def _green_remaining(intersections, tls_id):
    """估算剩余绿灯时间。优先使用 phase_durations 元数据，否则默认 30s。"""
    if tls_id not in intersections:
        return -1
    d = intersections[tls_id]
    if d.get("stage", "") != "GREEN":
        return -1

    current_phase = d.get("current_phase", 0)
    elapsed = float(d.get("stage_elapsed", 0))

    if tls_id in _phase_durations and current_phase in _phase_durations[tls_id]:
        total_green = _phase_durations[tls_id][current_phase]
    else:
        total_green = DEFAULT_GREEN_DURATION

    return max(0.0, total_green - elapsed)


def _build_vehicle_actions(payload: dict) -> dict:
    """构造车辆推荐动作：速度 + 车道。

    速度: 绿灯滑行 / 红灯减速。
    车道: 同 edge 排队最短的车道 (距路口 ≥ 50m, 非内部边)。
    """
    vehicles = payload.get("vehicles", {})
    intersections = payload.get("intersections", {})
    if not vehicles:
        return {}

    # 预构建 edge → 车道排队信息映射
    edge_lanes = {}
    for tid, data in intersections.items():
        for lid, lane in data.get("lanes", {}).items():
            if not isinstance(lane, dict):
                continue
            edge_id = lane.get("edge_id", "")
            if not edge_id:
                edge_id = "_".join(lid.split("_")[:-1]) if "_" in lid else lid
            ql = float(lane.get("queue_length_m", 0))
            li_str = lid.split("_")[-1] if "_" in lid else "0"
            edge_lanes.setdefault(edge_id, []).append((li_str, ql))

    actions = {}
    for vid, veh in vehicles.items():
        ns = veh.get("next_signal")
        loc = veh.get("location", {})

        # 速度
        if ns:
            dist = float(ns.get("distance_m", 999))
            spd = float(veh.get("motion", {}).get("speed_mps", 13.9))
            green_left = _green_remaining(intersections, ns.get("tls_id", ""))
            state = ns.get("state", "RED")
            if state == "GREEN" and green_left > 0 and dist > 5:
                eta = dist / max(spd, 0.1)
                ts = round(min(spd, 13.9), 1) if eta <= green_left else min(round(dist / green_left, 1), 13.9)
            elif state == "RED":
                ts = round(max(spd * 0.5, 2.0), 1)
            else:
                ts = None
        else:
            ts = None

        # 车道
        tl = None
        road_id = loc.get("road_id", "")
        current_lane_idx = loc.get("lane_index")
        if road_id and not road_id.startswith(":") and ns and float(ns.get("distance_m", 999)) > 50:
            edge_lanes_list = (
                edge_lanes.get(road_id)
                or edge_lanes.get("_".join(road_id.split("_")[:-1]) if "_" in road_id else road_id)
            )
            if edge_lanes_list and current_lane_idx is not None:
                current_q = None
                best_q = float('inf')
                best_li = current_lane_idx
                for li_str, ql in edge_lanes_list:
                    try:
                        li = int(li_str)
                    except ValueError:
                        continue
                    if li == current_lane_idx:
                        current_q = ql
                    if ql < best_q:
                        best_q = ql
                        best_li = li
                if current_q is not None and current_q > best_q * 1.5 and best_li != current_lane_idx:
                    tl = best_li

        if ts is not None or tl is not None:
            actions[vid] = {"target_speed_mps": ts, "target_lane_index": tl}

    return actions


# ============================================================
# 核心协议
# ============================================================
_step_count = 0
_sample_logged = 0

def step(payload: dict) -> dict:
    """每个决策周期调用。返回 {signals: {tls_id: {target_phase}}, vehicles: {veh_id: ...}}。"""
    global _step_count, _sample_logged
    decision_started = time.perf_counter()
    obs_np = build_state(payload.get("intersections", {}))
    obs_t = torch.FloatTensor(obs_np).unsqueeze(0)

    logits, values, scores = _model(obs_t)
    probs = torch.softmax(logits.squeeze(0), dim=-1)
    if EVAL_MODE:
        actions = torch.argmax(probs, dim=-1)
        dist = torch.distributions.Categorical(probs)
        lp = dist.log_prob(actions)
    else:
        dist = torch.distributions.Categorical(probs)
        actions = dist.sample()
        lp = dist.log_prob(actions)

    acts = actions.numpy().astype(int)
    for idx, tid in enumerate(_tls_order):
        _actions_for_reward[tid] = int(acts[idx])

    reward = compute_reward(payload)

    _buffer["obs"].append(obs_np)
    _buffer["actions"].append(actions.numpy())
    _buffer["logprobs"].append(lp.detach().numpy())
    _buffer["values"].append(values.squeeze(-1).detach().numpy().flatten())
    _buffer["rewards"].append(reward)

    signals = {}
    for idx, tid in enumerate(_tls_order):
        p = int(acts[idx]) % _phase_counts.get(tid, ACT_DIM)
        signals[tid] = {"target_phase": p + 1}

    veh_actions = _build_vehicle_actions(payload)
    _step_count += 1
    if _sample_logged < 3 and veh_actions:
        for vid, va in list(veh_actions.items())[:1]:
            lane_s = f" lane={va['target_lane_index']}" if va.get("target_lane_index") is not None else ""
            print(f"[CoSLight] 车辆消息: {vid} speed={va['target_speed_mps']}{lane_s}")
        _sample_logged += 1

    response = {
        "protocol_version": "2.0",
        "episode_id": payload.get("episode_id", ""),
        "step_id": payload.get("step_id", 0),
        "actions": {"signals": signals, "vehicles": veh_actions},
    }
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_runtime.record_latency(
        (time.perf_counter() - decision_started) * 1000.0,
        episode_id=str(payload.get("episode_id", "")),
    )
    evaluation_runtime.observe_decision(payload)
    return response


def finish(payload: dict) -> None:
    """Episode 结束。攒够 N 集后做批量 PPO 更新。"""
    global _episode, _buffer, _step_count, _batch_buffer
    from algorithms.evaluation import runtime as evaluation_runtime

    evaluation_payload = dict(payload)
    evaluation_payload.setdefault("episode_id", _evaluation_episode_id)
    evaluation_runtime.finish(evaluation_payload)
    _step_count = 0
    traffic = payload.get("traffic", {})

    rews = np.array(_buffer["rewards"])
    T = len(rews)
    if T < 4:
        print(f"[CoSLight] ep={_episode}: T={T} 集太短跳过, arrived={traffic.get('arrived_vehicles', 0)}")
        return

    avg_r = rews.mean()
    print(f"[CoSLight] ep={_episode}: T={T}步, avg_r={avg_r:.3f}, arrived={traffic.get('arrived_vehicles', 0)}")

    episode_data = {
        "obs": np.array(_buffer["obs"]),
        "actions": np.array(_buffer["actions"]),
        "logprobs": np.array(_buffer["logprobs"]),
        "values": np.array(_buffer["values"]),
        "rewards": np.array(_buffer["rewards"]),
    }
    _batch_buffer.setdefault("episodes", []).append(episode_data)

    if len(_batch_buffer["episodes"]) >= ACCUMULATE_EPISODES and not EVAL_MODE:
        _batch_ppo_update()
        _batch_buffer.clear()

    if _episode % CHECKPOINT_INTERVAL == 0 and not EVAL_MODE:
        _save_checkpoint()

    _buffer.clear()


def evaluation_result():
    """Return the latest six-metric result after ``finish``."""

    from algorithms.evaluation import runtime as evaluation_runtime

    result = evaluation_runtime.last_result()
    return None if result is None else result.to_dict()


def _batch_ppo_update():
    """批量 PPO 更新。所有累计 episode 拼接后做多 epoch 梯度更新。"""
    eps = _batch_buffer["episodes"]
    total_samples = sum(len(e['rewards']) for e in eps)
    print(f"[CoSLight] 批量更新：{len(eps)} 集 × {total_samples} 样本")

    all_obs, all_act, all_lp, all_adv, all_ret = [], [], [], [], []

    for ed in eps:
        rews = ed["rewards"]
        T = len(rews)
        vals = ed["values"]
        vals_mean = vals.mean(axis=1)
        ret = np.zeros(T)
        adv = np.zeros(T)
        gae = 0.0
        for t in reversed(range(T)):
            nv = 0.0 if t == T - 1 else vals_mean[t + 1]
            delta = rews[t] + GAMMA * nv - vals_mean[t]
            gae = delta + GAMMA * GAE_LAMBDA * gae
            adv[t] = gae
            ret[t] = adv[t] + vals_mean[t]
        adv = (adv - adv.mean()) / (adv.std() + 1e-8)

        obs_np = ed["obs"]; act_np = ed["actions"]; old_lp = ed["logprobs"]
        all_obs.append(obs_np.reshape(-1, OBS_DIM))
        all_act.append(act_np.reshape(-1))
        all_lp.append(old_lp.reshape(-1))
        all_adv.append(np.repeat(adv, _num_agents))
        all_ret.append(np.repeat(ret, _num_agents))

    flat_obs = np.concatenate(all_obs)
    flat_act = np.concatenate(all_act)
    flat_lp = np.concatenate(all_lp)
    flat_adv = np.concatenate(all_adv)
    flat_ret = np.concatenate(all_ret)
    total = len(flat_obs)

    for epoch in range(PPO_EPOCHS):
        idxs = np.random.permutation(total)
        for s in range(0, total, BATCH_SIZE):
            b = idxs[s:s + BATCH_SIZE]
            o_t = torch.FloatTensor(flat_obs[b])
            a_t = torch.LongTensor(flat_act[b])
            adv_t = torch.FloatTensor(flat_adv[b])
            ret_t = torch.FloatTensor(flat_ret[b])
            old_t = torch.FloatTensor(flat_lp[b])

            o_t_batch = o_t.view(-1, _num_agents, OBS_DIM) if len(b) % _num_agents == 0 else o_t.unsqueeze(1)
            if o_t_batch.shape[1] == _num_agents:
                logits, values, _ = _model(o_t_batch)
                logits = logits.reshape(-1, ACT_DIM)
                values = values.reshape(-1, 1)
            else:
                logits_list, values_list = [], []
                for i in range(len(b)):
                    single = o_t[i:i + 1].unsqueeze(0)
                    N_eff = min(_num_agents, single.shape[1])
                    pad = torch.zeros(1, _num_agents - N_eff, OBS_DIM) if N_eff < _num_agents else None
                    inp = torch.cat([single, pad], dim=1) if pad is not None else single.expand(1, _num_agents, -1)
                    l, v, _ = _model(inp)
                    logits_list.append(l[:, :N_eff, :])
                    values_list.append(v[:, :N_eff, :])
                logits = torch.cat(logits_list)
                values = torch.cat(values_list)

            probs = torch.softmax(logits, dim=-1)
            dist = torch.distributions.Categorical(probs)
            new_lp = dist.log_prob(a_t)
            ratio = (new_lp - old_t).exp()
            surr1 = ratio * adv_t.unsqueeze(-1)
            surr2 = torch.clamp(ratio, 1 - CLIP_EPS, 1 + CLIP_EPS) * adv_t.unsqueeze(-1)
            actor_loss = -torch.min(surr1, surr2).mean()
            critic_loss = (ret_t.unsqueeze(-1) - values).pow(2).mean()
            entropy = dist.entropy().mean()
            loss = actor_loss + 0.5 * critic_loss - ENTROPY_COEF * entropy
            _optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(_model.parameters(), 0.5)
            _optimizer.step()

    print(f"[CoSLight] 批量 PPO 完成：{len(eps)} 集 × {total} 样本")
