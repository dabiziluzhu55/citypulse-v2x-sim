# CoSLight-P0 算法讲解

## 1. 概述

**CoSLight-P0** 是一个基于 Transformer 的多路口协同信号控制 + 车辆速度推荐算法。通过 Protocol 2.0 接口与仿真环境交互，在每个决策周期（5 秒）输出信号灯相位和车辆推荐速度。

### 核心能力

| 功能 | 实现方式 |
|------|---------|
| 信号灯控制 | Transformer + Actor-Critic，每路口输出 1-8 号相位 |
| 多路口协同 | Transformer self-attention + Top-K 协作者选择（B_no_self fix） |
| 车辆速度推荐 | 基于前方信号状态的滑行/减速策略 |
| 在线训练 | PPO + GAE，每 episode 结束后更新模型 |

---

## 2. 输入格式

### 2.1 initialize() —— 路口元数据

仿真启动时调用一次，传入所有路口的静态信息。

```json
{
  "protocol_version": "2.0",
  "episode_id": "session-uuid",
  "intersections": {
    "demo_1": {
      "phase_order": [1, 2, 3, 4],
      "incoming_lanes": ["lane_a", "lane_b"],
      "outgoing_lanes": ["lane_c", "lane_d"],
      "direct_neighbors": ["demo_2"],
      "lanes": {
        "lane_a": { "length": 150.0, "speed_limit_mps": 13.9 }
      }
    }
  }
}
```

**算法从中提取：** 路口列表、相位数量、车道容量（length/5m）、邻接关系、进出车道映射。

### 2.2 step() —— 实时观测

每个决策周期调用一次。

```json
{
  "step_id": 42,
  "intersections": {
    "demo_1": {
      "current_phase": 2,
      "stage": "GREEN",
      "stage_elapsed": 12.5,
      "lanes": {
        "lane_a": {
          "vehicle_count": 3,          // 车辆数
          "halting_count": 1,          // 停车数
          "occupancy": 0.15,           // 占用率 (0-1)
          "queue_length_m": 24.5,      // 排队长度 (米)
          "mean_speed": 8.2,           // 平均速度 (m/s)
          "waiting_time": 5.0          // 等待时间 (秒)
        }
      }
    }
  },
  "vehicles": {
    "veh_001": {
      "motion": { "speed_mps": 12.0 },
      "next_signal": {
        "tls_id": "demo_1",
        "distance_m": 45.2,
        "state": "GREEN"
      }
    }
  }
}
```

**状态向量：** 56 维 = 8（相位 one-hot）+ 48（8 车道 × 6 特征）

---

## 3. 输出格式

```json
{
  "actions": {
    "signals": {
      "demo_1": { "target_phase": 3 }
    },
    "vehicles": {
      "veh_001": {
        "target_speed_mps": 12.0,
        "target_lane_index": null
      }
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `target_phase` | 1-based 相位编号 |
| `target_speed_mps` | 推荐速度 (m/s) |
| `target_lane_index` | 推荐车道（当前 null = 不干预） |

### 车辆速度推荐策略

| 前方信号 | 条件 | 推荐速度 |
|---------|------|---------|
| 绿灯 | 可赶上 | 维持当前速度 |
| 绿灯 | 赶不上 | min(距离/剩余时间, 13.9) |
| 红灯 | — | max(当前速度×0.5, 2.0) |

### 3.3 车道推荐策略

| 条件 | 行为 |
|------|------|
| 在 internal edge 上（`road_id` 以 `:` 开头） | 不推荐（SUMO 禁止换道） |
| 距路口 < 50m | 不推荐（来不及安全换道） |
| 距路口 ≥ 50m，非内部边 | 找同 edge 排队最短的车道 |
| 当前车道排队 > 最佳车道 × 1.5 | 建议切换到最空车道 |

---

## 4. 奖励函数

使用**四分量容量归一化奖励函数**。

### 公式

$$r_i = \Delta P_i + \eta \cdot \frac{1}{|C_i|}\sum_{j \in C_i} \Delta P_j - \lambda_{spill} \cdot S_i - \lambda_{sw} \cdot I^{switch}_i$$

**超参数：** η = 0.3, λ_spill = 0.5, λ_sw = 0.05

### 分量 1：本路口压力改善 ΔP

$$P_i = \sum_{l \in In} \frac{q_l}{C_l} - \sum_{m \in Out} \frac{q_m}{C_m}, \quad C_l = \frac{\text{lane.length}}{5m}$$

入口堵 + 出口空 → 压力高。动作后压力下降 → ΔP > 0 → 正奖励。

### 分量 2：邻接区域协调

$\eta \cdot$ 直接邻居路口压力改善的均值。防止把拥堵推给下游。

### 分量 3：下游溢出惩罚

$S_i = \sum_{m \in Out} \max(0, \text{occupancy}_m - 0.7)$

当下游车道占用率 > 70% 时惩罚，防止放进堵死的下游。

### 分量 4：相位切换惩罚

$I^{switch} = 1$ 当 $\text{phase}(t) \neq \text{phase}(t-1)$，否则 0。

减少不必要切换，降低黄灯损失。

---

## 5. 模型架构

```
MultiAgentActor [B, N, 56]
  │
  ├── Linear(56→64) → PositionalEncoding
  ├── TransformerEncoder(2 layers, 4 heads, d=64)
  │     └── [B, N, 64] latent features
  ├── score_head: Linear(64→64) → attention[N×N] → Top-K (B_no_self: exclude self)
  │     └── collab_feat = gather(Top-1 hidden)
  ├── actor_head: Linear(128→8)  ← cat(self[64], collab[64])
  ├── critic_head: Linear(N*64→64) → ReLU → Linear(64→1)  ← 集中式：拼接所有 agent 输出全局 V(s)
```

**数据流：**

1. N 路口 56 维 → Linear → 64 维 embedding
2. PositionalEncoding 注入路口位置
3. 2 层 Transformer self-attention 全路口交互
4. score_head 计算路口间关联度 → 排除自己 → 选 Top-1 协作者
5. 拼接 self_hidden(64) + collab_hidden(64) = 128 → actor_head → 8 维 logits

### 关键设计

- **B_no_self fix：** score 矩阵对角线设为 -∞，避免自循环退化
- **Markov 性质：** 56 维状态含所有决策所需信息，不依赖历史
- **动态协作：** Top-K 通过 score 自适应选择，随训练演化

---

## 6. 训练配置

| 参数 | 值 |
|------|-----|
| 算法 | PPO + GAE |
| 学习率 | 3e-4 |
| GAE λ | 0.95 |
| 折扣因子 γ | 0.99 |
| Clip ε | 0.2 |
| PPO epochs | 4 |
| Batch size | 64 |
| Checkpoint | 每 20 episode |

### 训练流程

```
Episode 开始
→ initialize: 创建模型、加载路网元数据
→ 每个决策周期 (5s):
    step: 状态编码 → Transformer 推理 → Top-K 协作 → 动作 → 计算奖励
→ Episode 结束:
    finish: GAE 优势计算 → 4 epoch PPO 更新 → 保存 checkpoint
```

---

## 7. 与原始 CoSLight 的差异

| | 原始 CoSLight | 当前 P0 |
|---|---|---|
| 状态接口 | Direct TraCI | Protocol 2.0 |
| 模型 | Transformer + MAPPO | Transformer + MAPPO（集中式 critic） |
| 奖励 | 5 分量简单求和 | 4 分量容量归一化 |
| 协作者 | K=1 (有自循环 bug) | K=1 (B_no_self fix) |
| 训练 | 离线批量更新 | 在线批量更新（攒 4 集做一次 PPO） |

---

## 8. 运行

```bash
cd /home/kemove/devdata1/gsb/citypulse-v2x-sim
export SUMO_HOME=/usr/share/sumo

# 单集
python3.10 -m simulation.sumo.run \
  --mode algorithm --algorithm-transport local \
  --algorithm-module algorithms.coslight \
  --intersection demo_1 demo_2 --period off_peak \
  --duration 120 --seed 2781
```

---

## 9. 已完成的优化

1. **绿灯时长估算**：`initialize()` 已支持从元数据 `phase_durations` 读取每相位精确时长。未提供时默认 30s。
2. **训练/评估分离**：全局变量 `EVAL_MODE` 设为 `True` 时跳过 PPO 更新（仅推理）。调用 `_load_checkpoint(path)` 自动进入评估模式。
3. **Checkpoint 自动清理**：`MAX_CHECKPOINTS = 5`，每次保存后自动删除最旧文件。
4. **车道推荐**：同 edge 排队最短车道，距路口 ≥ 50m 时推荐，内部边跳过。


---

## 10. 文件结构说明

```
algorithms/coslight/
├── __init__.py           # 模块入口：导出 initialize / step / finish
├── controller.py         # 核心文件：Transformer 模型 + 状态构造 + 奖励 + PPO 训练 + 车辆推荐
├── coslight讲解.md        # 本文档
├── actor_207.pt          # [参考] 冻结的 CoSLight-P0 Actor 权重（Grid4x4 最佳模型）
├── critic_207.pt         # [参考] 冻结的 CoSLight-P0 Critic 权重
├── vnorm_207.pt          # [参考] 冻结的 Value Normalizer
├── sim_env_timer_fix.py  # [参考] 历史修复记录，当前已不需要
└── patches/
    ├── distributions.py  # [参考] B_no_self：协作者排除自己的分布修复
    └── r_mappo.py        # [参考] B_no_self：MAPPO 训练循环修复
```

| 文件 | 作用 |
|------|------|
| `controller.py` | **唯一运行时文件**。含模型、状态构造、奖励、训练全部逻辑 |
| `__init__.py` | 暴露 controller 的 initialize/step/finish 给仿真框架 |
| `patches/` | 历史 bug 修复记录，当前代码已内置 B_no_self，不依赖 |
| `*.pt` | 参考权重，非当前训练产出（训练 checkpoint 在 checkpoints/ 下） |

---

## 11. 已知问题：自行车道限制导致仿真退避

### 现象
SUMO 路网中存在大量 `allow="bicycle"` 的自行车道（如 `-56732_0`、`-57802_0`、`-50930_0`、`-52216_0`），载客车和公交车被路由到这些车道时 SUMO 会抛出错误：
```
Lane -56732_0 does not allow passenger vehicles.
Lane -50930_0 does not allow bus vehicles.
```

### 根因
`simulation.sumo.build_traffic` 生成的全局路由文件（`routes.rou.xml`）指定车辆走 edge，但 SUMO 的多路口内部连接（internal junction）有时只连接了自行车道。载客车在 edge 内部走投无路时触发 lane permission 错误。

### 临时绕过（算法端）
在 `simulation/sumo/session.py` 和 `simulation.sumocfg` 中增加 `--ignore-route-errors` 参数，告知 SUMO 将 lane permission 视为警告而非致命错误。
```
# session.py 第 670 行附近
"--ignore-route-errors",
"true",
```

### 根本修复（SUMO 端）
需要修改 `TotalMap_20.net.xml`，为涉及载客车的内部路口添加机动车道连接线（如缺失 `-56732_1`、`-56732_2` 的 `incLanes`），或放开对应自行车道的 `allow` 限制。
