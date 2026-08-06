# CoSLight 当前可训练版算法讲解

## 1. 概述

本实现是一个基于 Transformer 的多路口协同信号控制 + 车辆引导算法。算法只通过 Protocol 2.0 本地接口与仿真环境交互，不直接调用 TraCI。Protocol 仍每 5 秒回调一次，以便车辆引导保持响应；信号 PPO 使用固定 15 秒联合决策间隔。

### 核心能力

| 功能 | 实现方式 |
|------|---------|
| 信号灯控制 | Transformer + Actor-Critic，按每个路口的真实 `phase_order` 输出相位 ID |
| 多路口协同 | Transformer 表示 + `direct_neighbors` 拓扑内可微 soft attention |
| 车辆速度推荐 | 基于前方信号状态的滑行/减速策略 |
| 车辆车道推荐 | 权限过滤 + 排队贪心 + reject-fix + 静止门控 |
| 在线训练 | 同步并行采样 + 中央 PPO/GAE，保留完整联合时间步，每 4 个 episode 更新一次 |
| Lane State Builder | 41 维状态构建（lane_state.py） |

### 路线图

| 阶段 | 信号控制 | 车辆引导 | 状态 |
|------|---------|---------|:---:|
| V1 | 修正后的 CoSLight PPO 信号策略 | 规则型（权限过滤 + reject-fix + 静止门控） | ✅ |
| V2 | CoSLight 信号策略 | Lane Actor IPPO 参数共享（进口道路层） | 🔨，尚未接入训练/推理 |
| V3 | CoSLight + 意图通信 | Lane Actor + CTDE 集中式 Critic | 📋 |

---

## 2. 输入格式

### 2.1 initialize() —— 路口元数据

仿真启动时调用一次，传入所有路口的静态信息。

新增字段 `edge_lanes`：全网普通 edge 的静态车道权限表，含 `allowed_vehicle_type_ids`。算法端在 `initialize()` 中缓存并构建静态索引。

### 2.2 step() —— 实时观测

**信号控制状态向量：** 201 维 = 8（相位 one-hot）+ 1（阶段持续时间）+ 192（32 车道 × 6 特征）。车道顺序来自初始化元数据，不依赖每步 JSON 字典顺序。32 槽覆盖当前 20 路口拓扑的最大路口 `demo_8`；如果未来拓扑超过该上限，初始化会显式失败，不会静默截断。

Protocol 2.0 的 `occupancy` 单位固定为百分数 `[0, 100]`，算法始终除以 100；不能根据数值是否大于 1 猜测单位，否则真实的 `0.8%` 会被误判为 `80%`。

**车辆观测新增字段（Protocol 2.0）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `leader_gap_m` | float 或 null | 当前车道前车间距 |
| `follower_gap_m` | float 或 null | 当前车道后车间距 |
| `time_since_last_lane_change_s` | float 或 null | 距上次换道秒数 |

**历史动作结果：**

| 字段 | 说明 |
|------|------|
| `previous_action_results.vehicles[vid].lane_change_status` | `"completed"` / `"not_completed"` |
| `previous_action_results.vehicles[vid].requested.target_lane_index` | 上次推荐的目标车道 |

---

## 3. 输出格式

```json
{
  "actions": {
    "signals": { "demo_1": { "target_phase": 3 } },
    "vehicles": {
      "veh_001": {
        "target_speed_mps": 12.0,
        "target_lane_index": 1
      }
    }
  }
}
```

### 3.1 车辆速度推荐

| 前方信号 | 条件 | 推荐速度 |
|---------|------|---------|
| 绿灯 | 可赶上 | min(当前速度, 车辆当前允许速度) |
| 绿灯 | 赶不上 | min(距离/剩余时间, 车辆当前允许速度) |
| 红灯 | — | max(当前速度 − 2 m/s, 2 m/s)，最终再受当前允许速度约束 |

### 3.2 车辆车道推荐（V1 规则型）

**处理链路：** 候选车道 → 权限过滤 → 排队贪心 → reject-fix → 静止门控 → 输出

| 过滤层 | 条件 | 行为 |
|--------|------|------|
| 权限过滤 | `type_id not in allowed_vehicle_type_ids` | 排除该车道 |
| 排队贪心 | 当前排队 > 最优车道 × 1.5 | 推荐换到最优车道 |
| reject-fix | `lane_change_status == "not_completed"` 且目标与上次相同 | 取消推荐 |
| 静止门控 | speed < 0.5 m/s 或内部 edge | 仅输出速度，不推荐换道 |

---

## 4. 奖励函数

默认 `pressure` 模式使用：

$$r_i = -|P_i| + \eta \cdot \frac{1}{|C_i|}\sum_{j \in C_i} -|P_j| - \lambda_{spill} \cdot S_i - \lambda_{sw} \cdot I^{switch}_i$$

**超参数：** η = 0.3, λ_spill = 0.5, λ_sw = 0.05

这里的 `P` 是按车道容量归一化后，进口到出口 movement 的车辆密度差之和。
使用绝对值是为了同时惩罚上游积压和下游拥堵，当前状态持续拥堵会在每个决策
窗口继续产生负奖励。

旧版 `legacy_delta` 模式保留用于消融：

$$r_i^{legacy} = P_i^{t-1} - P_i^t + \eta \cdot \overline{\Delta P}_{C_i} - \lambda_{spill}S_i - \lambda_{sw}I_i^{switch}$$

诊断发现旧奖励存在两个风险：

1. 多步 `ΔP` 在回合内近似首尾相消，不能持续惩罚高压/高排队状态；
2. `P` 保留正负号，出口越来越拥堵会让 `P` 变小，可能产生错误方向的正奖励。

因此训练入口默认改为负绝对压力，并提供 `--reward-mode legacy_delta` 复现实验。
checkpoint 会保存奖励语义；恢复训练时若 checkpoint 与命令行奖励模式不同会
直接报错，避免把两种回报混进同一条训练曲线。

---

## 5. 模型架构（Transformer + CoSLight PPO）

```
CoSLightNetwork [B, N, 201]
  ├── Actor encoder: Linear(201→64) → LayerNorm → ReLU → PositionalEncoding
  │   └── Transformer(2 层, 4 头)
  │       └── 拓扑 mask 内非对称 soft attention → 本地表示 + 邻居上下文
  │       └── shared phase_actor_head([B,N,A,136]) → 每个真实相位的 logit
  └── 独立 centralized critic encoder
      └── Transformer(2 层, 4 头) → 本地表示 + 全局均值 → value head
```

原 201 维状态仍用于编码路口整体交通状态。除此之外，Actor 为每个真实动作
额外接收一组与 `phase_order` 严格对齐的 8 维相位特征：该相位所服务进口车道的
平均车辆密度、停车密度、排队比例、等待比例、对应出口车道密度、
`进口密度 - 出口密度`、是否为当前相位、标称绿灯时长。服务关系直接读取
Protocol 2.0 初始化元数据已有的
`phases[*].connection_priorities` 和 `connections[*].from_lane/to_lane`；
实时数值来自每步 lane observation。算法没有调用 TraCI，也没有修改或生成
SUMO 配置。

v8 曾把共享线性 `phase_scorer` 作为旧动作槽位 head 的小幅 logit 偏置。实验
证明其权重变化远小于旧 head，确定性 argmax 仍主要由“动作 0/1/2”这种跨路口
含义不一致的槽位决定。v9 改为真正的候选相位条件策略：对每个候选动作分别拼接
路口编码、协作者上下文和上述 8 维相位特征，再通过同一组
`phase_actor_head` 参数输出一个 logit。旧 `actor_head` 与
`phase_scorer` 仅为旧 checkpoint 推理兼容而保留并冻结；新 v9 模型把它们
清零，因此所有有效候选动作在初始化时等概率，动作偏好只能从候选相位语义中
学习。

v9 仍把 collaborator 当成一个随机离散动作：从全体路口按 Plackett-Luce
分布抽取 Top-K，并把其 log-prob 与相位 log-prob 一起送入 PPO。固定探针显示
更新主要改变 collaborator，真实相位概率几乎不变。更关键的是，官方 CoLight
并不采样“协作者动作”，而是在固定邻域内对邻居表示做可微的 attention
加权求和。v10 因此使用 Protocol 2.0 的 `direct_neighbors` 建立
“自身 + 直接邻居”mask，以独立的 target/source 投影计算非对称分数，再在
mask 内 softmax 并加权邻居表示。attention 由相位 policy loss 端到端训练，
不产生额外 PPO 动作或 log-prob。

PPO 现在只对真实送入信号控制链路的相位动作计算概率比：

$$
r_t=\exp(\log\pi_{phase}^{new}-\log\pi_{phase}^{old})
$$

这消除了“不可直接执行的内部采样获得策略梯度”带来的高方差。训练 minibatch
的基本单位仍是完整的 `[N, state]` 联合时间步，不能把不同时间、不同路口
打散后重新拼接。

Actor 与 critic 不共享参数和 optimizer：两者分别反向传播、分别计算和裁剪
gradient norm，避免旧实现中 critic 的大梯度通过共享 encoder 和一次全局裁剪
压制 policy gradient。critic 输出的是归一化 value，GAE 和 rollout 中存储的
仍是反归一化后的原尺度 value。每次中央更新先用该批 raw return 更新
running mean/variance，再对 return target 和旧 value 做同一尺度变换；value
loss 使用 clipped value prediction 与 Huber loss。value 统计随 checkpoint
保存，并和模型 generation 一起发给所有 worker。

当前结构还有一个待独立消融的问题：Actor 在拓扑 attention 前已经经过无 mask
的全路口 Transformer，因此拓扑 mask 并没有真正限制 Actor 的信息范围。更符合
CoLight 思想的结构应是“本地编码 → 仅对拓扑候选邻居做注意力/Top-K → Actor”，
全局信息只进入集中式 Critic。v10 先单独验证“随机 collaborator → 可微拓扑
attention”这一变量；若仍不能改善真实相位 KL 与交通指标，再移除 Actor 的
全局 Transformer，避免一次混入两个结构变量。

### 5.1 安全相位时序

SUMO 的 `SafePhaseController` 可能经历 `GREEN → YELLOW → CLEARANCE → GREEN`。算法只有在距上次联合决策已满 15 秒，且全部路口均处于 `GREEN`、`pending_phase is None` 并满足最小绿灯时，才采样并保存新的联合动作；过渡期间返回空的信号动作映射，绝不覆盖尚未生效的相位。固定时距避免了“保持相位 5 秒即获得回报，切换相位却要等待黄灯/全红”造成的动作相关时域偏差。上一动作的奖励在下一次安全决策边界才结算。

算法还会把每个“请求切相”保留到后续观测：只有在
`stage == GREEN && current_phase == target_phase` 时才记为推断生效，并记录
请求到观测生效的延迟。该数据来自 Protocol 2.0 后续状态推断，不是 SUMO
显式回执。20 路口、60 秒冒烟中四种算法策略的切相请求均为 100% 推断生效，
延迟均为 5 秒，因此目前没有发现动作被接口吞掉。

最长绿灯和相位支配率同时保留“全部路口”与“仅多相位路口”两套统计。只有一个
合法相位的 `demo_13` 本来就会整回合保持同一相位，不能用它判断策略发生相位
饿死；训练日志中的 `multi_phase_*` 字段已排除这类路口。

### 5.2 最大绿灯动作掩码

确定性策略早期容易因细小 logit 偏置长期选择同一相位。算法端现对多相位路口
设置相位级上限：

$$T_{max}(p)=\max(60\text{s},\ k_{green}\cdot T_{nominal}(p))$$

默认 `k_green=2`。达到上限后只在当前决策点屏蔽“继续保持当前相位”，其他真实
相位仍可选；单相位路口永不屏蔽。该 mask 会和 observation、action、旧
log-prob 一起存入 rollout，PPO 更新时用同一 mask 重算新 log-prob，因此不是
事后篡改动作。`--max-green-factor 0` 可关闭该约束进行消融。

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
| Batch size | 32 个联合时间步 |
| 奖励模式 | `pressure`（默认）；`legacy_delta` 仅用于旧版消融 |
| 最大绿灯 | `max(60s, 2 × 标称绿灯)`；只约束多相位路口 |
| Optimizer | Actor/Critic 各自 Adam，学习率均为 3e-4，梯度分别裁剪到 0.5 |
| Value 学习 | 独立 centralized critic + running return normalization + clipped Huber value loss |
| Checkpoint | v10；每 4 episode（一次中央更新）原子写入，包含模型、两套 optimizer、奖励/value 统计、策略目标、策略架构、拓扑 attention mask 和相位特征语义、路口顺序、相位语义、策略代际、随机数状态和训练 seed 范围 |
| 累计 episode | 4 集攒一批做 PPO 更新 |
| Top-K | 默认 5；限定每个路口的“自身 + direct neighbors”attention scope 上限 |

---

## 7. 文件结构

```
algorithms/coslight/
├── __init__.py           # 模块入口
├── controller.py         # 核心：Transformer + 奖励 + PPO + 车辆推荐
├── lane_state.py         # Lane Actor 状态构建器（41 维 + 槽位 + mask）
├── train.py              # 串行训练入口，用于回归和并行基准
├── parallel_train.py     # 多 SUMO 同步采样、中央 PPO 更新（正式训练入口）
├── evaluate.py           # 同 seed 的 official fixed/random/模型公平评估
├── test_controller.py    # 算法与协议回归测试
├── test_parallel_train.py# 并行批次、seed 和策略代际回归测试
├── test_evaluate.py      # tripinfo、fixed 配置和配对统计测试
├── test_train.py         # 串行入口默认配置回归测试
├── coslight讲解.md        # 本文档
├── actor_207.pt          # [参考] 冻结权重
├── checkpoints/          # 训练产出
└── patches/              # 历史修复记录
```

---

## 8. Lane State Builder（lane_state.py）

为 V2/V3 分层 CTDE 车道引导准备的数据底座。

### 8.1 静态索引

| 索引 | 用途 |
|------|------|
| `_approach_slots` | [(tls_id, incoming_edge_id), ...] 固定决策槽位 |
| `_edge_to_lanes` | edge_id → [lane_id_0, ...] |
| `_lane_neighbors` | lane_id → {left, right} |
| `_lane_permissions` | edge_id → {lane_idx: set of allowed type_ids} |
| `_phase_metadata` | intersection_id → {phase_id: set of movements} |

### 8.2 41 维状态

| 索引 | 组 | 维度 | 说明 |
|------|-----|:---:|------|
| 0-7 | A. 车辆自身 | 8 | speed_ratio, accel_norm, dist_to_stop, lane_idx, time_since_lc, movement one-hot(3) |
| 8-13 | B. 左车道 | 6 | veh_count, occupancy, mean_speed, leader_gap, follower_gap, downstream_storage |
| 14-19 | B. 当前车道 | 6 | 同上 |
| 20-25 | B. 右车道 | 6 | 同上 |
| 26-32 | C. 信号意图 | 7 | cur_serves_movement, target_serves, same_phase, elapsed_ratio, met_min_green, is_transition, downstream_green |
| 33-35 | D. 区域 | 3 | flow_rate, downstream_occ, intersection_queue |
| 36-40 | E. 历史 | 5 | last_action one-hot(3), last_ok, cooldown |

### 8.3 动作 mask（3 维）

```
[KEEP_valid, LEFT_valid, RIGHT_valid]
```

过滤原因链：物理邻居存在 → 权限允许 → 路线兼容 → 速度达标

### 8.4 间距计算

左右车道 gap 通过投影计算：候选车辆在当前纵向位置，在目标车道上的前后最近车辆 bumper-to-bumper 间距。无邻居时归一化为 1.0。

---

## 9. 运行

正式训练 20 路口、200 episode（推荐 4 worker）：

```bash
cd /home/kemove/devdata1/gsb/citypulse-v2x-sim
python3 -u -m algorithms.coslight.parallel_train \
  --episodes 200 --workers 4 --intersections 20 \
  --duration 300 --seed 42 --top-k 5 --reward-mode pressure \
  --max-green-factor 2 \
  --vehicle-guidance rule \
  --checkpoint-dir algorithms/coslight/runs/coslight_parallel/checkpoints \
  --save algorithms/coslight/runs/coslight_parallel/final.pt
```

每个 policy batch 的流程为：中央进程冻结一份策略快照 → 4 个 worker
用不同 SUMO seed 各采一条原始轨迹 → 校验元数据、策略权重、value 统计和
`policy_generation` → 中央按 seed 顺序更新奖励统计 → 合并 4 条联合轨迹
做一次 PPO 更新。worker 不创建 optimizer、不更新奖励归一化；任一 worker
失败时整批样本作废，避免 PPO 混入旧策略或半批数据。

4090 服务器实测的 20 路口、4 episode、60 秒场景中，串行入口耗时
81.31 秒，并行入口耗时 22.2 秒，墙钟加速约 3.66 倍。该数字是短场景
工程基准，不代表策略效果提升。

训练建议放在 screen 中，并同时启用 screen 日志：

```bash
screen -DmS coslight_train -L \
  -Logfile algorithms/coslight/runs/coslight_parallel/screen.log \
  python3 -u -m algorithms.coslight.parallel_train \
  --episodes 200 --workers 4 --intersections 20 --duration 300 \
  --seed 42 --top-k 5 --reward-mode pressure --max-green-factor 2 \
  --vehicle-guidance rule \
  --checkpoint-dir algorithms/coslight/runs/coslight_parallel/checkpoints \
  --save algorithms/coslight/runs/coslight_parallel/final.pt
```

恢复训练使用 `--resume checkpoint.pt`。v10 checkpoint 会记录已经消耗的
seed 范围，续训自动从下一个 seed 开始；旧 checkpoint 没有 seed 范围时，
以 `base_seed + 已完成 episode + 1` 作为兼容起点。v7/v8/v9 checkpoint
仍可加载 Actor 做推理评估：旧动作 head 和旧相位打分器按原权重恢复，新
模块缺失时保持兼容初始化，并走旧随机 collaborator 推理路径；但 v10 改变了
PPO 动作语义和通信结构，v7/v8/v9 均不允许作为 v10 连续训练恢复点。

直接作为 Protocol 2.0 模块运行时，`COSLIGHT_MODE` 支持 `train`、
`collect`、`untrained`、`model`、`stochastic_model`、`random`、`fixed`；
`collect` 只由并行 worker 使用。
为兼容旧入口，未设置时默认为 `train`。模型推理还必须设置
`COSLIGHT_MODEL_PATH`，串行断点续训使用 `COSLIGHT_RESUME_PATH`。

每次 PPO 更新会输出两行诊断日志。第一行包含 loss、entropy、KL、
clip fraction 和 rollout explained variance；第二行包含 advantage 强度、
Actor/Critic 各自梯度、更新后 value 方差与 EV、参数变化量，以及固定探针
状态上的策略 KL、attention KL 和 argmax 动作变化率。v10 还记录有效相位的特征覆盖率
`phase_cov`、共享候选相位 head 最终输出层变化量 `dphase_out` 和输出层
权重范数 `phase_out`。只统计最终层可避免隐藏层的随机初始化范数掩盖实际
策略输出变化。判断训练是否有效时要串联检查：

`advantage → gradient → parameter delta → probe policy change → command/execution → traffic metrics`

### 9.1 公平评估

`evaluate.py` 对每种方法使用完全相同的 SUMO seed。`fixed` 直接使用
`SimulationConfig(control_mode="fixed")`，不是 CoSLight 的动作 0；`random`
只从当前路口的有效相位中采样；`untrained` 是固定初始化 seed 的确定性网络；
`model` 是 checkpoint 的 argmax 动作；`stochastic_model` 按 checkpoint
分布采样。

```bash
python3 -u -m algorithms.coslight.evaluate \
  --methods fixed random untrained model stochastic_model \
  --episodes 4 --workers 4 --intersections 20 --duration 300 \
  --seed 9600 --top-k 5 --reward-mode legacy_delta --max-green-factor 0 \
  --vehicle-guidance off \
  --checkpoint algorithms/coslight/runs/diagnostic_8ep/final.pt \
  --output algorithms/coslight/runs/eval_4seed/report.json
```

报告同时保存实时快照指标和 SUMO 已有的 `tripinfo.xml`：

- `arrived/remaining/waiting/halting/mean_speed`；
- 已完成车辆的平均、中位数和 P95 旅行时间；
- 已完成车辆的等待与 time loss；
- 未完成车辆数量及截至截断时已累计的 duration/waiting/time loss；
- 同 seed 相对 official fixed 的配对差值。

完成车辆旅行时间不能单独使用：300 秒结束时仍有大量车辆在路网中，若只统计
到达车辆会产生截尾偏差，所以必须同时报告吞吐量、残余车辆和未完成车辆成本。

### 9.2 2026-07-31 诊断实验

20 路口、300 秒、4 worker、车辆引导关闭、8 episode 共完成两次 PPO 更新：

| 更新 | joint steps | agent samples | EV | KL | clip fraction | 相对参数变化 | 探针 argmax 变化 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 76 | 1520 | 0.145 | 0.0103 | 0.176 | 1.74% | 61.4% |
| 2 | 76 | 1520 | 0.106 | 0.0047 | 0.056 | 1.36% | 43.9% |

两次 advantage 标准差约 0.27、梯度范数约 0.55–0.63，说明采样、优势、
反向传播、参数更新和策略变化的完整链路都有效；当前问题不是“没有梯度”。
首轮策略变化较大，但仅凭两个不同 seed 批次不能判定性能退化，必须看同 seed
评估结果后再决定是否降低学习率或增加 target-KL 提前停止。

旧 `legacy_delta` checkpoint 在 4 个固定留出 seed（9601–9604）上的结果：

| 方法 | 到达车辆 | 终点快照 waiting | 平均速度 | 已完成车辆旅行时间 |
|---|---:|---:|---:|---:|
| official fixed | 62.0 | 2668.6 | 10.67 m/s | 103.03 s |
| random valid | 60.0 | 4271.0 | 10.50 m/s | 103.35 s |
| untrained argmax | 44.5 | 42066.0 | 6.85 m/s | 98.39 s |
| legacy 8ep argmax | 54.0 | 34456.5 | 7.08 m/s | 92.74 s |
| legacy 8ep stochastic | 63.0 | 4427.8 | 10.35 m/s | 102.25 s |

确定性旧模型相对未训练模型多到达 9.5 辆、waiting 下降约 7600，说明参数
更新确实改变了策略；但它仍比 fixed 少到达 8 辆、waiting 高约 31788。
其每回合约 380 个联合路口指令中仅请求切相 29–32 次，而随机/随机采样模型
约为 204–234 次，且请求均在后续观测中生效。这把首个主要断点定位到
**策略长期保持少数相位**，不是接口吞掉动作。

表中确定性模型的“已完成旅行时间”看似更低，不能解释为更优：它让更多车辆
留在路网中，只剩较容易完成的车辆进入该均值，属于截尾/幸存者偏差。部署判断
必须优先结合 arrived、remaining、全体累计 waiting/time loss 和残余队列。

只替换为 `pressure` 奖励、保持相同训练 seed 和模型结构后的两次更新：

| 更新 | EV | KL | clip fraction | 相对参数变化 | 探针 argmax 变化 |
|---:|---:|---:|---:|---:|---:|
| 1 | 0.007 | 0.0077 | 0.111 | 2.78% | 56.6% |
| 2 | 0.002 | 0.0055 | 0.073 | 2.22% | 65.5% |

同一留出 seed 下，pressure 8ep 的确定性策略平均到达 52.0 辆、waiting
30299.6，较 legacy 8ep 的 54.0 辆/34456.5 waiting 只部分改善，仍不可部署。
其多相位路口平均相位支配率约 0.90，多个路口 300 秒不切相。相同 checkpoint
随机采样时平均到达 62.75 辆、waiting 4136.2、相位支配率约 0.50–0.53，
证明主要剩余问题是高熵早期策略转成 argmax 后的相位饥饿，而不是动作接口失效。

加入最大绿灯动作掩码后，以相同 seed 9301–9308 从头训练 8 episode：

| 更新 | EV | KL | clip fraction | 相对参数变化 | 探针 argmax 变化 | 强制切相 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.007 | 0.0074 | 0.110 | 2.78% | 56.4% | 10 |
| 2 | 0.002 | 0.0053 | 0.069 | 2.22% | 60.9% | 14 |

两批中的切相指令分别为 868/868、907/907 生效，平均生效延迟均为 5 秒；
多相位路口的平均相位支配率约为 0.51、0.53。与无掩码的同 seed 第二批相比，
终点快照 waiting 从 4017.8 降至 3765.1，但这是随机采样训练轨迹，不能代替
确定性部署评估。

在固定留出 seed 9601–9604 上使用确定性 argmax：

| 模型/约束 | 到达车辆 | 终点快照 waiting | 平均速度 | 全车辆累计 waiting |
|---|---:|---:|---:|---:|
| official fixed | 62.0 | 2668.6 | 10.67 m/s | — |
| pressure 8ep，无最大绿灯约束 | 52.0 | 30299.6 | 7.23 m/s | 43581.4 |
| pressure 8ep，仅部署时加最大绿灯约束 | 62.5 | 13325.1 | 9.25 m/s | 32089.5 |
| pressure + 最大绿灯共同训练 8ep | 61.5 | 13282.0 | 9.25 m/s | 31534.8 |

最大绿灯约束把确定性策略的吞吐量恢复到 fixed 附近，并将终点 waiting
降低约 56%，证明它修复了严重的相位饥饿；但 waiting 仍约为 fixed 的 5 倍。
共同训练 8 episode 相对“只在部署时加约束”尚无实质收益，原因是总共只有
2 次中央 PPO 更新，critic EV 仍约为 0，不能据此判断奖励或模型已经收敛。

随后从完整 ep8 checkpoint 继续训练，并按预定规则观察连续 5 次中央更新。
旧共享 critic 在累计 ep12–ep28 的结果为：

| 累计 episode | rollout EV | return std | value std | probe argmax 变化 |
|---:|---:|---:|---:|---:|
| 12 | 0.003 | 3.852 | 0.031 | 37.2% |
| 16 | 0.002 | 4.547 | 0.027 | 11.4% |
| 20 | 0.004 | 3.379 | 0.022 | 0.0% |
| 24 | 0.004 | 3.404 | 0.018 | 0.0% |
| 28 | 0.004 | 4.417 | 0.019 | 3.1% |

value 预测方差连续收缩到真实 return 方差的约 1/240，EV 始终约为 0，说明
critic 实际退化成了随训练平移的近常数基线。ep20 在同一留出 seed
9601–9604 上的确定性结果也从 ep8 的 61.5 辆/13282 waiting 退化到
57.25 辆/17272.8 waiting。因此在 ep28 触发连续窗口停止条件，未继续盲跑
到 ep64；完整恢复点保存在：

```text
algorithms/coslight/runs/reward_v2_maxgreen_64ep/screen.log
algorithms/coslight/runs/reward_v2_maxgreen_64ep/final.recovery.pt
```

据此只做 critic-v1 修复：独立 Actor/Critic encoder 和 optimizer、running
return normalization、clipped Huber value loss；奖励、动作约束、Actor 的
全局 Transformer 和分层 PPO 概率目标暂时保持不变。修正 old-value clipping
尺度后的 4 worker × 60 秒冒烟中，第一次更新的 rollout EV 为 -0.036，
更新后同批 post-EV 为 0.096；续训一批后 rollout EV 为 0.059、post-EV
为 0.102。v6 checkpoint 成功连续恢复 episode
4→8、generation 1→2、value 统计 count 240→480，并使用连续 seed
9401–9408。

正式 A/B 使用与旧版相同的 9301–9308、20 路口、300 秒、8 episode；结果需
同时检查 critic EV 和留出 seed 的确定性交通指标。两次中央更新结果为：

| 更新 | rollout EV | post-EV | rollout value std | post value std |
|---:|---:|---:|---:|---:|
| 1 | 0.003 | 0.120 | 0.074 | 0.543 |
| 2 | 0.096 | 0.220 | 0.539 | 1.198 |

第二批新轨迹上的 EV 已明显高于旧版的 0.002，说明 critic 修复有效。在固定
留出 seed 9601–9604 上，critic-v1 的确定性策略平均到达 52.5 辆、终点
waiting 10483.6、平均速度 9.37 m/s、全车辆累计 waiting 29065.8。相对旧
critic 的共同训练 ep8（61.5 辆、13282.0 waiting、9.25 m/s、全车辆累计
waiting 31534.8），拥堵成本有所下降但少到达 9 辆；说明 critic 已开始学习，
交通策略却仍不可部署。

因此下一轮只修正分层动作的联合 PPO ratio：phase 与 collaborator 的 log-prob
相加后形成一个联合 ratio、一次裁剪和一个 policy loss，并把该目标语义写入 v7
checkpoint。训练 seed 仍使用 9301–9308，评估 seed 仍使用 9601–9604；
Actor 通信结构保持不变，避免把两个结构变量混进同一 A/B。

联合目标版本随后继续到 ep20。critic 已稳定学习，但 Actor 没有形成优于随机
策略的交通偏好：

| checkpoint / 策略 | arrived | 终点 waiting | 全车辆累计 waiting | 全车辆累计 time loss |
|---|---:|---:|---:|---:|
| fixed（当前统计口径） | 62.00 | 2668.6 | 23140.0 | 32364.5 |
| v7 ep8 deterministic | 63.75 | 8263.6 | 26873.3 | 35879.5 |
| v7 ep8 stochastic | 65.25 | 2217.9 | 21946.3 | 31465.2 |
| v7 ep20 deterministic | 61.25 | 15007.8 | 32066.5 | 39949.3 |
| v7 ep20 stochastic | 65.50 | 2526.8 | 22340.9 | 31695.7 |
| valid-random | 65.50 | 2913.6 | 21960.9 | 31265.6 |

训练阶段 ep12、ep16、ep20 的 rollout/post-update EV 分别约为
`0.242/0.364`、`0.307/0.429`、`0.520/0.636`，证明 critic 断点已经修好；
但 deterministic 随训练持续恶化，ep20 stochastic 与只在有效动作 mask 内
采样、并经过相同安全约束的 random 基本相当。因此不能把 stochastic 相对
fixed 的部分指标改善归因于学习，它主要来自随机相位覆盖和最大绿灯约束。

对 Protocol 2.0 元数据的只读检查表明，SUMO 已提供每个相位服务的
connection 及其 `from_lane/to_lane`，而 v7 Actor 完全没有消费这层动作语义；
共享 Actor 只能看扁平 lane slot 后输出各路口含义不同的动作序号。这是
“critic 学会而 Actor 近似 random”的首要候选病因。v8 因此只加入上述
action-aligned phase scorer，保持 reward、GAE、联合 PPO、Transformer、
Top-K 和 SUMO 不变。

v8 使用相同训练 seed 9301–9308 和评估 seed 9601–9604。两次更新的
post-EV 分别为 0.114 和 0.254，证明 critic 仍在学习；但相位打分器最终权重
范数只有约 0.0032，旧动作槽位 head 的 argmax 变化率约 42%，动作偏好仍被
旧 head 主导。留出评估也验证了这一点：

| v8 策略 | arrived | 终点 waiting | 全车辆累计 waiting | 全车辆累计 time loss |
|---|---:|---:|---:|---:|
| deterministic | 37.00 | 19243.5 | 38806.6 | 48823.5 |
| stochastic | 52.00 | 8341.0 | 27176.9 | 39539.9 |

两者均明显差于 valid-random。由此否定“给旧动作槽位 logit 加一个很小的语义
偏置就足够”的假设，而不是继续增加训练量。v9 只修这一个结构断点：用共享的
候选相位条件 head 直接产生全部动作 logit，并冻结清零旧 head；reward、GAE、
PPO 目标、Transformer、Top-K、训练/评估 seed 和 SUMO 均保持不变。60 秒
冒烟与续训验证已确认 episode 4→8、generation 1→2、seed 9801→9808 和
value 统计连续，全部 390 次被观察到的切相请求最终生效。

v9 正式训练的两次 post-EV 为 0.147 和 0.238，第一批固定探针 argmax 变化率
为 52.5%，说明“旧动作槽位 head 压住新相位语义”的断点已被修通；但两批
phase probe KL 都低到日志显示为 0，动作 entropy 保持约 0.89，变化主要发生
在 collaborator KL。留出评估没有转化为交通收益：

| v9 策略 | arrived | 终点 waiting | 全车辆累计 waiting | 全车辆累计 time loss |
|---|---:|---:|---:|---:|
| deterministic | 31.50 | 18517.0 | 36463.7 | 48168.6 |
| stochastic | 49.00 | 8181.7 | 27228.3 | 39088.4 |

因此 v9 不继续盲目增加 episode。对照官方 CoLight 后确认，原论文使用邻域内
soft attention 加权表示，并没有把邻居选择定义为环境动作。v10 据此移除
collaborator log-prob、collaborator entropy bonus 和对角概率正则，PPO 只优化
真实相位动作；通信改为 `direct_neighbors` mask 内的可微非对称 attention。
当前 20 路口子集的只读元数据显示，仅 3 个路口在该子集中有一个直接邻居，
其余 17 个只有自身可用；旧版全网随机采样协作者大部分没有物理邻接关系。
v10 的 4 worker × 60 秒冒烟与断点恢复已通过，episode 4→8、generation
1→2、seed 9901→9908、value count 240→480 连续，354/354 次切相请求
均被观察到生效。

正式 v10 仍使用训练 seed 9301–9308 和留出 seed 9601–9604。两次中央更新的
post-EV 分别为 0.119、0.273；固定探针的相位输出层相对参数变化约为
1.65%、1.68%，第一批相位 argmax 变化率为 52.5%。这说明 PPO 梯度终于主要
作用到真实相位策略，而不是不可执行的协作者采样。留出评估为：

| v10 策略 | arrived | 终点 waiting | 全车辆累计 waiting | 全车辆累计 time loss |
|---|---:|---:|---:|---:|
| deterministic | 48.25 | 8281.9 | 24454.0 | 36127.4 |
| stochastic | 53.50 | 6195.9 | 24631.0 | 36885.2 |

相对 v9，deterministic 的终点 waiting 从 18517.0 降至 8281.9、全车辆累计
waiting 从 36463.7 降至 24454.0，证明移除随机 collaborator 是有效的结构
修复；但它仍落后 valid-random（65.5 辆、2913.6 waiting）和 official fixed
（62.0 辆、2668.6 waiting）。由于 v10 此时只有两次独立新数据更新，不能仅凭
episode 数断言已经收敛，也不能直接继续长跑。下一步先在完全相同的动作安全
约束和场景上加入 MaxPressure 诊断基线：若显式压力控制也无法接近 fixed，
优先检查相位—movement 语义及目标/指标对齐；若 MaxPressure 明显有效，则继续
增加 v10 的有效 PPO 更新数，并监控相位 KL、探针变化和交通收益。

MaxPressure 使用现有 `connection_priorities` 和实时 lane observation，为每个
有效相位计算其服务 movement 的容量归一化 `进口密度 - 出口密度` 之和；平分时
保持当前相位，并经过与 CoSLight 完全相同的最小绿灯、最大绿灯和安全过渡约束。
它只在算法模块内读取状态，不调用 TraCI、不修改 SUMO。4 个留出 seed 的结果为：

| 方法 | arrived | 终点 waiting | 全车辆累计 waiting | 全车辆累计 time loss |
|---|---:|---:|---:|---:|
| official fixed | 62.00 | 2668.6 | 23140.0 | 32364.5 |
| valid-random | 65.50 | 2913.6 | 21960.9 | 31265.6 |
| MaxPressure | 58.00 | 3562.6 | 20003.9 | 31224.4 |
| v10 deterministic ep8 | 48.25 | 8281.9 | 24454.0 | 36127.4 |

MaxPressure 的 838/838 次切相请求全部在后续观测中生效，平均延迟 5 秒。
它的吞吐量和终点快照不及 fixed，但全车辆累计 waiting 比 fixed 低约 13.6%，
time loss 也更低。这说明相位—movement 映射、实时压力和执行链路均具有有效
控制信号；同时说明 300 秒截断下“即时压力/累计拥堵”和“已到达车辆数/终点
waiting”并不完全同向。v10 不能只优化或汇报某一个指标，接下来先从 ep8
增加 5 次中央更新到 ep28；若相位 KL 和交通收益仍不增长，再调整 Actor
信息路径或奖励，而不是继续盲跑。

v10 随后续训到 ep28（共 7 次中央更新）。留出 seed 9601–9604 的
deterministic 结果为：平均到达 59.0 辆、终点 waiting 9181.0、全车辆
累计 waiting 22862.7、全车辆累计 time loss 34546.9。它比 ep8 的全
车辆 waiting 24454.0 有所改善，但仍未超过 MaxPressure；且每个 300
秒 episode 平均发出 380 次切相请求，几乎每个 15 秒决策点都切相。
所有请求均在 5 秒后生效，因此断点不在 SUMO 接口，而在策略把大量
控制时间消耗在黄灯/全红过渡上。

为区分“样本少”与“Actor 更新弱”，v10 ep28 又使用同一组新数据续训
4 episode 并记录完整因果链。advantage 原始标准差为 6.18，94.4%
样本有不少于两个合法动作，Actor 纯 policy-gradient norm 为 0.00457，
entropy-gradient norm 仅为 0.000001，说明 reward、mask 和 entropy 都没有把
学习链路归零；但 PPO ratio 仅为 `0.999990±0.000451`，KL 约为
`1e-7`，clip fraction 为 0。同时合成正/负 advantage 方向测试能正确
提高/降低被选动作概率。结论是 PPO 公式和 optimizer 链路可用，但参数
更新几乎没有转化成相对 action logit 变化；不能再把问题单独归因于
episode 数量。

v11 因此解冻零初始化的直接相位打分器，并尝试以“候选相位平均进口
密度 - 平均出口密度”作为固定压力先验。这个假设被同 seed 实验明确否定：
未训练 deterministic 虽把切相从 380 次降到 56 次，全车辆 waiting 却升到
32450.9；训练 8 episode 后仍为 31868.1，并且两次更新的 KL 只有
`2e-7` 和 `4e-7`。原因是候选相位服务的 movement 数量不同，对唯一进/
出车道取平均会改变真实 MaxPressure 的相位排序。v11 不作为后续训练
起点。

v12 只修正这一个已证实的误差：候选相位的第 6 个特征改为其所服务
每一条 `(from_lane, to_lane)` 的容量归一化密度差之和，与诊断
MaxPressure 使用完全相同的 movement 映射；其上叠加可学习的直接相位
打分器和 CoLight 拓扑 attention 残差。60 秒、同 seed 的算法内 A/B 中，
未训练 v12 与 MaxPressure 的到达、剩余车辆、全车辆 waiting、time loss、
22 次切相及每路口相位命令完全一致，证明强基线起点和动作语义
已对齐。完整 300 秒、seed 9601–9604 复验也精确复现 MaxPressure：平均
到达 58.0 辆、终点 waiting 3562.6、全车辆 waiting 20003.9、time loss
31224.4、完成车辆平均行程 96.7 秒、平均切相 209.5 次。v12 不修改
SUMO；checkpoint format 升为 12，加载 v10/v11
时会恢复各自的旧 phase-feature 语义和先验系数，避免静默改变旧模型推理。

v12 第 1 批 4 episode 更新中，合法动作的 movement-pressure 特征为
`0.201±0.519`，1%/99% 分位为 `-0.327/2.642`，因此信号没有被归一化
压平。直接相位评分器参数范数从 0 变为 0.00586，固定探针 argmax
变化率为 10.3%，但 KL 仍只有约 `2e-7`。留出评估表明这些小幅残差
已转化为可复现的交通收益：

| v12 ep4 deterministic 对比 | arrived | 终点 waiting | 全车辆 waiting | time loss | 完成行程 | 切相 |
|---|---:|---:|---:|---:|---:|---:|
| MaxPressure / v12 未训练 | 58.0 | 3562.6 | 20003.9 | 31224.4 | 96.71s | 209.5 |
| v12 ep4 | 59.5 | 3437.3 | 19319.6 | 30600.9 | 96.75s | 269.5 |

即 ep4 相对强起点将全车辆 waiting 再降低 3.4%、time loss 降低 2.0%、
平均多到达 1.5 辆；但切相增加约 28.6%、急刹车增加约 1.0%。这证明
“精确压力强先验 + 可学习 CoLight 残差”的方向有效，但尚未全面超过
fixed/random，后续 checkpoint 必须继续按多指标选择，不能只看 reward。

第 2 批更新后，相位评分器参数范数从 0.00586 增至 0.00954，
但固定探针的 argmax 变化率只有 0.6%，KL 仍约为 `2e-7`。ep8
在同一留出集上为：到达 59.25 辆、终点 waiting 3419.3、全车辆
waiting 19311.7、time loss 30626.6、完成行程 96.19 秒、急刹车
5123.3 次、切相 275.0 次。与 ep4 相比，ep8 的 waiting 仅降低
7.95 秒（0.04%），time loss 反而增加 25.78 秒（0.08%），到达数减少
0.25 辆并多切相 5.5 次，属于实验噪声范围内的平台，不支持盲目
继续长训。因此 v12 ep4 和 ep8 都保留为 Pareto 候选：ep4 优先吞吐、
time loss 和较少切相，ep8 优先终点 waiting、全车辆 waiting、
完成行程和急刹车。下一轮只放大直接作用于候选相位语义的评分器
学习率，不同时改 reward、拓扑或 SUMO，用于判断平台是否源于
Actor 有效更新幅度不足。

v13 是该单变量 A/B：基础 Actor 和 critic 仍使用 `3e-4`，只将
6 维直接相位评分器放入独立 Adam 参数组，使用 `3e-3`（10 倍）。
第一批 rollout 与 v12 使用完全相同的 seed 9301–9304，更新前的
reward、arrived、waiting 和 949/949 次实际生效的切相轨迹一致。
更新后相位评分器参数变化范数从 v12 的 0.00586 放大到
0.05857，平均 KL 从约 `2e-7` 放大到 `1.5e-6`，ratio 标准差
从 0.000604 增至 0.001633，clip fraction 仍为 0，固定探针 argmax
变化率为 10.5%。这说明更新强度已可控放大且没有破坏 PPO 信赖域；
是否保留还必须由同留出集的交通指标决定。实验分支将 checkpoint
format 升为 13，以防止旧单参数组 optimizer 被静默恢复到 v13 训练；
完整算法测试为 74 passed。

v13 的留出评估没有形成新的全面优势：到达量与 v12 ep4 相同，
终点 waiting 改善 45.0 秒（1.3%），切相减少 1.5 次；但全车辆
waiting 增加 12.1 秒、time loss 增加 24.8 秒、急刹车增加 8.8 次。
这些差异很小且方向不一致，因此 v13 本身不能被选为更优交通模型。
后续 v14/v15 又把 10 倍相位评分器学习率恢复为显式、可审计的 optimizer
参数组，但理由不是“v13 赢了”，而是它已经证明能在 clip fraction 仍为 0
时放大直接物理动作通路的有效更新。该设置要和本地 Actor、第一阶段奖励、
终点 horizon guard 一起重新做因果诊断；v12 ep4/ep8 继续作为历史 Pareto
对照，不能把旧结论直接移植到新架构。

| 同留出集 deterministic | arrived | 终点 waiting | 全车辆 waiting | time loss | 完成行程 | 急刹车 | 切相 |
|---|---:|---:|---:|---:|---:|---:|---:|
| official fixed | 62.00 | 2668.6 | 23140.0 | 32364.5 | 103.03s | 8707.3 | 未记录 |
| valid-random | 65.50 | 2913.6 | 21960.9 | 31265.6 | 108.91s | 8761.8 | 228.8 |
| MaxPressure / v12 未训练 | 58.00 | 3562.6 | 20003.9 | 31224.4 | 96.71s | 5095.3 | 209.5 |
| **v12 ep4** | **59.50** | 3437.3 | 19319.6 | **30600.9** | 96.75s | 5147.3 | **269.5** |
| **v12 ep8** | 59.25 | 3419.3 | **19311.7** | 30626.6 | **96.19s** | **5123.3** | 275.0 |
| v13 ep4（相位 LR×10） | **59.50** | **3392.3** | 19331.7 | 30625.7 | 96.70s | 5156.0 | **268.0** |

上表也说明当前还不能声称“全面、大幅领先”：CoSLight 已在全车辆
waiting、time loss、完成行程和急刹车上明显优于 fixed/random，但
300 秒窗口内的 arrived 和终点 waiting 仍落后。且每回合约 831 辆车
离开出发地，只有约 59 辆完成行程，截断时尚有约 772 辆未完成；
后续必须加入更长窗口/清空评估，避免只对 7% 已完成车辆过拟合。

v8 首次正式运行前还检查过“新增 `nn.Linear` 是否提前消耗 PyTorch RNG，
导致与 v7 初始策略不再可比”的可能性。将兼容线性 scorer 改为不消耗 RNG 的
零参数实现后，复现实验结果完全一致，因此该假设被排除；实现仍保留这一处理，
以减少未来结构消融中的随机初始化干扰。

### v14/v15：第一阶段语义收口与终点因果链修复

v14 首先完成四项第一阶段收口：Actor 不再通过无 mask Transformer 预先读取
全网路口，而是“逐路口本地编码 → direct-neighbor 拓扑 attention”；训练与
评估默认关闭车辆规则引导；回溢惩罚系数在第一阶段固定为 0；V13 已验证的
相位评分器 10 倍学习率改为独立 optimizer 参数组并写入 checkpoint。旧
v10–v13 checkpoint 评估时仍恢复旧全网编码语义，但不能续训到新架构。

第一次 20 路口 × 4 worker × 4 episode × 300 秒烟测给出了反直觉但关键的
证据：每回合有 19 条完整的 15 秒 transition，末尾 pending action 不是零时长，
而是在约 290 秒下发后实际执行了 10 秒。因此不能把 `pending_dropped=20`
简单改写为 0，也不能把 10 秒 reward 当成 15 秒 transition 混入固定时距 GAE。
最终的算法侧修法是设置训练回合上限：到 290 秒先完成上一条合法 transition，
保存 `V(s290)` 作为 bootstrap；若剩余时间不足 15 秒，则不再下发新信号动作，
最后 10 秒保持当前相位。该实现不修改 SUMO，也不需要直接 TraCI。

修正后的第二次真实烟测四个 worker 全部满足：`joint_steps=19`、
`transition_dt=15.0±0.0s`、`pending_dropped=0`、`horizon_guarded=20`。
中心更新使用 76 个联合时刻、1520 个 agent-sample；Actor 梯度范数为
0.0101，Actor 参数变化范数为 0.1319，固定探针 argmax 改变率 12.7%，
实际信号切换 929/929 生效、平均观测延迟 5 秒。由此确认
“advantage → 梯度 → 参数 → 策略 → 信号执行”链路已贯通；但 4 episode
仍只是工程烟测，`arrived=69.0`、终点 waiting=1712.2 不能跨 seed 直接拿来
宣称优于历史基线。

终点训练语义最终固化为 `fixed_horizon_guard_bootstrap_v1`，checkpoint format
升为 15。中心 learner 还会拒绝任何不等于 15 秒的 worker transition，防止
环境延迟或接口变化把可变时距样本静默混入 PPO。烟测阶段产生的 v14 checkpoint
只允许历史推理，不允许继续训练。下一准入门槛是从零运行 20 episode 因果诊断，
再用完全相同的留出场景比较 fixed、合法 random、MaxPressure、未训练 V15、
deterministic V15 与 stochastic V15；在此之前不进入云端和车辆 Actor 阶段。

上述 20 episode 因果诊断已经完成：4 个并行 worker 共采集 7600 条 agent
sample，中央执行 5 次 on-policy PPO 更新，总耗时约 23.5 分钟。五批的 Actor
梯度范数为 `0.0082–0.0167`，Actor 参数变化范数为 `0.1289–0.1513`，固定
探针 argmax 变化率为 `1.1%–2.3%`；critic 更新后的 explained variance 从
0.208 逐批升到 0.510。全部 rollout 都满足 `transition_dt=15±0s`、
`pending_dropped=0`、`terminal_unexecuted=0`，切相请求生效率为 100%、平均
观测延迟 5 秒。由此可以排除“Actor 没有梯度”或“动作被 SUMO 吞掉”这两个
旧假设；当前断点已经转移到策略目标与交通指标的对齐。

随后在固定留出 seed 9901–9904 上做了成对评估（20 路口、300 秒、车辆引导
关闭）。主要均值如下：

| 方法 | arrived | remaining | 终点 waiting | 完成行程 | 未完成车辆 waiting 总量 | 全车辆 waiting 总量 | 全车辆 time loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| official fixed | 62.00 | 775.00 | 2659.6 | 102.95s | 22080.4 | 23090.3 | 32328.9 |
| valid-random | 59.75 | 777.25 | 2318.1 | 108.03s | 22138.6 | 23350.1 | 未单列 |
| MaxPressure | 73.75 | 763.25 | 572.0 | 101.07s | 15157.4 | 15925.1 | 24724.5 |
| V15 untrained | 73.75 | 763.25 | 572.0 | 101.07s | 15157.4 | 15925.1 | 24724.5 |
| V15 deterministic ep20 | 68.75 | 768.25 | **432.5** | **92.71s** | 15164.7 | **15727.3** | **24545.4** |
| V15 stochastic ep20 | 66.75 | 770.25 | 1877.0 | 99.84s | 未单列 | 19962.9 | 未单列 |

未训练 V15 与 MaxPressure 在四个 seed 上逐项一致，证明初始化强先验仍然成立。
deterministic ep20 相对 MaxPressure 将终点 waiting 降低 24.4%、完成车辆平均
行程降低 8.3%，全车辆累计 waiting 仅降低 1.24%；同时少到达 5 辆（-6.8%），
未完成车辆累计 waiting 反而略高。收益主要集中在已经完成的少量车辆，尚未形成
吞吐、公平性和效率的全面优势。stochastic 明显更差，因此部署主指标使用
deterministic，随机策略只作为方差诊断。

为防止 RL 残差破坏强压力起点，算法端增加了默认关闭的压力安全层实验开关：
只允许选择与当前最优合法 movement-pressure 相差不超过 margin 的动作，且
只能缩小既有 action mask，不能恢复最大绿灯或安全状态机已经禁止的相位。该
约束位于算法 controller，不调用 TraCI、不修改 SUMO；PPO 轨迹保存约束后的
mask，保证未来启用训练时新旧 log-prob 语义一致。粗扫描 `0/0.05/0.10/0.20`
表明 margin=0 精确复现 MaxPressure，而后三者都精确复现无约束 V15。原因是
V15 实际选中动作的最大压力 regret 只有 0.00355，粗粒度 margin 对它没有约束
作用。进一步细扫 `0.0005/0.001/0.002/0.003` 后，四档的平均 arrived 都是
68.5；其中最严格的 0.0005 已把最大实际 regret 压到 0.000408，仍没有恢复
MaxPressure 的 74.0 辆。虽然各档完成行程约为 92.6–93.3 秒、全车辆 waiting
约为 15690–15770，仍优于 MaxPressure 的 100.9 秒和 15907，但不存在同时补回
吞吐的简单 margin “甜点”。这说明少量近零压力差动作即可改变 300 秒截止时刻
的完成量，安全层不是一个可以靠静态阈值解决的补丁。它继续保持默认关闭，不
固化为 V16。下一单变量实验改为 600 秒同 seed 评估，先判断 5 辆差距是有限
时域截断效应，还是长期吞吐退化；在结论出来前不改 reward。

600 秒持续需求评估使用新留出 seed 9951–9952；该场景在 599.3 秒仍生成车辆，
不是“300 秒需求后清空”。结果如下：

| 方法 | arrived | remaining | 终点 waiting | 完成行程 | 未完成车辆 waiting 总量 | 全车辆 waiting 总量 | 全车辆 time loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| official fixed | 276.5 | 1545.5 | 4129.2 | 223.87s | 79682.7 | 92269.6 | 129864.9 |
| valid-random | 299.5 | 1522.5 | 2794.1 | 210.54s | 72255.2 | 82957.0 | 120260.3 |
| MaxPressure | **332.0** | **1490.0** | 1590.9 | 204.63s | **59459.3** | **68601.5** | **103739.1** |
| V15 deterministic ep20 | 327.0 | 1495.0 | **1409.5** | **203.16s** | 59481.4 | 68742.0 | 104261.0 |

延长时域后，V15 仍比 MaxPressure 少到达 5 辆；全车辆 waiting 高 0.20%、
time loss 高 0.50%，未完成车辆 waiting 也略高。它仅保留终点快照 waiting
低 11.4%、完成行程低 0.7% 和平均速度略高的局部优势。因此 300 秒下“总
waiting 略优”不是稳定的长期收益，不能继续用相同配置盲目训练 ep20 之后。
下一步先在相同 300 秒留出集评估 ep4/8/12/16/20 五个 checkpoint，判断是否
存在早期 Pareto 最优模型；若各 checkpoint 都不能超过 MaxPressure，才改
Stage-1 目标或约束，而不是继续调整静态 pressure margin。

五个 checkpoint 的 300 秒同 seed 扫描也已经完成：

| checkpoint | arrived | 终点 waiting | 全车辆 waiting | time loss | 与 MaxPressure 分歧率 |
|---|---:|---:|---:|---:|---:|
| ep4 | **69.5** | 438.0 | **15657.1** | 24483.3 | 3.13% |
| ep8 | **69.5** | 502.2 | 15983.1 | 24820.0 | 5.13% |
| ep12 | 69.0 | **375.3** | 15929.8 | 24760.0 | 2.50% |
| ep16 | 68.5 | 401.9 | 15703.7 | 24511.2 | **2.25%** |
| ep20 | 69.0 | 440.9 | 15658.1 | **24449.6** | 3.50% |
| MaxPressure（同两 seed） | 74.0 | 563.5 | 15907.3 | 24708.9 | 0% |

ep4 是较平衡的早期模型，但仍少到达 4.5 辆；五个 checkpoint 都没有同时补回
吞吐。因此问题不是简单的“ep20 过训练”，而是当前 reward 下残差稳定地沿
“降低部分等待、牺牲有限时域完成量”的方向更新。逐路口反事实诊断显示 ep20
每回合 400 个 agent-decision 里只有约 14 个偏离同状态 MaxPressure，最早在
t=20 秒的 `demo_17` 和 `demo_6` 出现，随后整条交通轨迹分叉。

60 秒复现实验进一步记录到这些早期分歧的最佳相位压力分别为 -0.0791、
-0.000408 和 0.0107，即集中在“所有相位都受下游阻塞”或“物理压力信号很弱”
的状态。基于该证据新增默认关闭的低信号残差门控：当当前最佳合法相位压力不
高于设定 floor 时，只允许同状态 MaxPressure 动作；高于 floor 时仍允许 RL
残差。它复用现有 movement pressure，不增加回溢 reward，也不能绕过既有
安全 action mask。当前仅作为 `0/0.02/0.05/0.10` 单变量评估开关，在成对结果
出来前不写入 checkpoint 语义、不用于训练。

低信号 floor 扫描同样没有形成甜点：`0/0.02/0.05/0.10` 四档 arrived 均为
68.5；floor=0.10 已让 72.1% 的 agent-decision 回退 MaxPressure、每回合仅剩
约 2 次分歧，但全车辆 waiting 已回到 15908.1，仍没有补回 74.0 的到达量。
继续提高 floor 只会退化为纯 MaxPressure。因此 pressure margin 和 residual
floor 都保留为默认关闭的诊断开关，不固化为训练或部署语义。

### v16：撤销诊断性 10× scorer 学习率

V13/V15 的相位 scorer 10× 学习率用途是放大直接动作通路，证明 Actor 梯度
确实能改变相位；它从未在留出交通指标上证明优于 1×。当前 300/600 秒、五个
checkpoint 和两类门控实验已经证明：10× 残差只需极少动作分歧就会稳定牺牲
完成量。相反，历史 V12 的基础学习率 ep4 曾同时提高 arrived 和全车辆 waiting。
因此 v16 只把 `PHASE_SCORER_LR_MULTIPLIER` 从 10 恢复为 1，reward、Actor
本地/邻域结构、critic、GAE、固定 15 秒时距和 SUMO 全部不变；checkpoint format
升为 16，避免静默恢复 V15 的 optimizer 语义。训练入口还会显式清除两个运行时
门控环境变量，保证本轮是纯学习率单变量实验。

60 秒、4 路口、1 episode 真 SUMO 烟测已通过：format=16、scorer LR=1×，
`transition_dt=15±0s`、`pending_dropped=0`、`terminal_unexecuted=0`、8/8
切相生效。下一步从零训练 8 episode，只评估 ep4/ep8；若仍不能超过
MaxPressure，就停止在 pressure reward 上继续调优化器，进入互补的局部效率
目标设计。

正式 8 episode 训练已经完成：4 个并行 worker、20 路口、300 秒，共两批
on-policy 更新、3040 个 agent-sample，耗时 651.9 秒。两批都满足固定 15 秒
transition、无丢样本、信号请求 100% 生效。相位 scorer 每批参数变化范数为
0.00496 和 0.00519，与历史 V12 基础学习率的量级一致；固定探针 argmax 变化率
分别为 12.0% 和 1.4%。因此恢复 1× 后 Actor 仍能改变策略，并未退化成“只靠
MaxPressure、不发生学习”。

在校准 seed 9901–9902 上，ep4/ep8 的 deterministic 成对结果如下。这里的
MaxPressure 数值来自完全相同的场景、时域和 seed；两个 checkpoint 均未启用
pressure margin 或低信号 floor。

| 方法 | arrived | remaining | 终点 waiting | 完成行程 | 未完成车辆 waiting 总量 | 全车辆 waiting 总量 | 全车辆 time loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| MaxPressure | **74.0** | **763.0** | 563.5 | 100.93s | 15154.2 | 15907.3 | 24708.9 |
| V16 ep4 | 73.5 | 763.5 | **481.5** | 100.30s | 14521.3 | 15305.7 | 24196.4 |
| **V16 ep8** | 73.5 | 763.5 | 493.4 | **99.65s** | **14459.2** | **15206.4** | **24121.4** |

V16 ep8 相对 MaxPressure 仅少到达 0.5 辆（-0.68%），同时将全车辆累计
waiting 降低 4.41%、time loss 降低 2.38%、完成行程降低 1.27%。这已经基本
消除 V15 每回合少 4.5–5 辆的吞吐断崖，并形成更有希望的近 Pareto 改善；但
两个 seed 参与了 checkpoint 选择，不能作为最终泛化结论。下一门槛是在未参与
选择的 seed 9951–9954 上直接比较 V16 ep8 与 MaxPressure：若等待/时间损失收益
保持且吞吐不出现实质退化，就保留 V16 并扩大完整基线；否则再进入奖励设计。

四个新留出 seed 的复验已经通过：

| 方法（seed 9951–9954） | arrived | remaining | 终点 waiting | halting | 平均速度 | 完成行程 | 完成 waiting | 未完成 waiting | 全车辆 waiting | 全车辆 time loss | 急刹车 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MaxPressure | 73.0 | 764.0 | 558.0 | 32.75 | 11.236 | 101.42s | 10.86s | 15279.7 | 16072.3 | 24891.5 | **7979.3** |
| **V16 ep8** | **73.5** | **763.5** | **489.4** | **29.50** | **11.344** | **100.57s** | **10.55s** | **14534.7** | **15310.0** | **24266.9** | 8027.5 |

相对 MaxPressure，V16 ep8 的 arrived 提高 0.68%，终点 waiting 降低
12.30%，全车辆累计 waiting 降低 4.74%，time loss 降低 2.51%，完成行程
降低 0.83%，未完成车辆 waiting 降低 4.88%，平均速度提高 0.96%。四个 seed
中的到达量分别为 `73/74/73/74`，MaxPressure 为 `73/72/73/74`，不是由单一
异常 seed 造成。代价是急刹车增加约 0.60%，且 V16 每回合约 291 次切相，
MaxPressure 约 222 次；所有切相仍 100% 生效、延迟 5 秒。由此 V16 已通过
“新 seed 上不牺牲吞吐且改善全车辆效率”的 Stage-1 初步门槛，但舒适性与切相
频率尚未全面领先。当前决策是冻结 V16 reward 和优化器，不进入新 reward 版本；
先在同一留出集补齐 fixed、合法 random、未训练和 stochastic 基线，再决定是否
做更长时域与切相/舒适性约束实验。

同一四个 seed 的完整 300 秒基线随后补齐：

| 方法 | arrived | 终点 waiting | 全车辆 waiting | 全车辆 time loss | 完成行程 | 急刹车 |
|---|---:|---:|---:|---:|---:|---:|
| official fixed | 61.75 | 2685.4 | 23153.8 | 32386.1 | 102.91s | 8614.0 |
| valid-random | 65.50 | 2189.4 | 21945.3 | 31611.4 | 104.96s | 8758.8 |
| V16 untrained / MaxPressure | 73.00 | 558.0 | 16072.3 | 24891.5 | 101.42s | **7979.3** |
| V16 stochastic ep8 | 66.25 | 2353.2 | 19989.2 | 29059.7 | 106.58s | 8494.8 |
| **V16 deterministic ep8** | **73.50** | **489.4** | **15310.0** | **24266.9** | **100.57s** | 8027.5 |

未训练网络再次与 MaxPressure 逐项一致，说明改善来自学习后的残差，而不是
初始化或评估路径差异。deterministic V16 在 arrived、终点 waiting、全车辆
waiting、time loss 和完成行程上都优于其余四个 Stage-1 基线；stochastic
仍显著退化，因此后者只作为分布诊断，不能用于部署结果。下一项最小实验是新的
600 秒持续需求留出集，因为 V15 的 300 秒收益曾在长时域消失。V16 通过前不再
改训练配置，也不提前进入 Stage 2。

新的 600 秒持续需求留出 seed 9961–9964 已经通过：

| 方法 | arrived | remaining | 终点 waiting | halting | 平均速度 | 完成行程 | 完成 waiting | 未完成 waiting | 全车辆 waiting | 全车辆 time loss | 急刹车 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MaxPressure | 335.25 | 1486.75 | 1017.2 | 47.00 | 10.511 | **205.97s** | 27.43s | 59037.8 | 68235.9 | 103371.7 | **29382.8** |
| **V16 deterministic ep8** | **341.50** | **1480.50** | **471.3** | **35.25** | **10.667** | 208.32s | **26.61s** | **56318.5** | **65406.3** | **100830.7** | 29558.5 |

V16 在四个 seed 上分别比 MaxPressure 多到达 `4/5/14/2` 辆，每个 seed 的
全车辆 waiting 和 time loss 也都更低；均值上 arrived 提高 1.86%、终点
waiting 降低 53.67%、全车辆 waiting 降低 4.15%、time loss 降低 2.46%、
未完成车辆 waiting 降低 4.61%、平均速度提高 1.48%。这与 V15 在 600 秒时
少到达 5 辆且总 waiting/time loss 反向的失败模式相反，说明基础 scorer
学习率是已被因果对照验证的关键修正，不是校准 seed 偶然性。

仍有两个明确代价：完成车辆平均行程增加 1.14%，急刹车增加约 0.60%；V16
每回合约 568 次切相，MaxPressure 约 492 次。因而当前可以把 V16 ep8 冻结为
Stage-1 效率基座，但还不能声称所有指标全面领先。下一实验应单独研究切相频率
与舒适性，保持 V16 checkpoint、reward 和 scorer LR 不变，避免再次把多个变量
揉进 reward。600 秒并行日志还出现过 SUMO 路口碰撞告警；算法侧未修改 SUMO，
该环境事件应在最终实验中按 method/seed 单独统计或由 SUMO 侧提供碰撞指标。

### V16 训练量审计与严格续训

检查 checkpoint 后发现，V16 ep8 由 4 个并行 worker 每轮各产生 1 个
episode，每 4 个 episode 才进行一次 PPO 大更新，所以 ep8 实际只经历了
2 次新数据更新，而不是 8 次。为区分“训练不足”和“继续训练会损害长时域
稳定性”，从 ep8 完整恢复 actor、critic、两个 optimizer、reward/value
归一化统计、RNG、policy generation 和 seed 进度，使用原配置续训至
ep12 和 ep16。这是同一训练轨迹的严格续训，不是只加载模型权重后重启优化。

ep12 和 ep16 的更新没有出现梯度断链：explained variance 在大更新后
分别为 0.106 和 0.236，固定探针观测上的 argmax 动作变化率分别为
2.3% 和 1.3%，说明参数更新确实传导到了策略输出。KL 仅为
`1.2e-6/0.6e-6`、clip fraction 为 0，表明每次更新幅度很小，但不等于
Actor 没有学习。

同一组 300 秒留出 seed 9951–9954 的确定性评估为：

| checkpoint | arrived | 全车 waiting | 全车 time loss | 完成行程 | 急刹车 | 平均切相请求 |
|---|---:|---:|---:|---:|---:|---:|
| V16 ep8 | **73.50** | 15310.0 | 24266.9 | 100.57s | 8027.5 | 291.5 |
| V16 ep12 | 73.25 | 15247.2 | 24188.8 | 99.46s | **7955.3** | 300.5 |
| V16 ep16 | 73.25 | **15079.8** | **23827.5** | **97.15s** | 7994.8 | 309.3 |

ep16 相对 ep8 在四个 seed 上都降低了 waiting 和 time loss，均值分别
改善 1.50% 和 1.81%，但 arrived 少 0.25 辆，切相持续增加。因此 300 秒
结果只说明续训在短时域上仍有收益，不能据此替换已通过长时域门槛的 ep8。

ep16 在 600 秒、seed 9961–9964 上没有通过部署门槛：相对 ep8，
arrived `341.5→338.0`，全车 waiting 上升 0.18%，time loss 仅下降
0.46%，急刹车下降 1.01%。其中 seed 9964 的 arrived `342→329`、
全车 waiting `65787.7→67871.7`，构成明确的长时域吞吐崩塌。该 seed 上
多个路口的相位分布同时偏移，平均切相从 ep8 的 568 次增加到
ep16 的 602 次左右，不是某一个路口或 SUMO 接口故障。结论是：
**ep16 不替换 ep8；V16 ep8 继续作为 Stage-1 默认基座。**

ep12 作为中间 checkpoint，先只评估 ep16 崩塌的 seed 9964。这个
最小早停实验也未通过：相对 ep8，arrived `342→324`、全车 waiting
`65787.65→67955.50`、time loss `101492.24→103007.62`，切相请求
`565→589`。虽然急刹车 `29861→29369` 和完成车辆的平均行程时间下降，
但完成车辆少 18 辆，后者存在明显的完成样本选择偏差，不能解读为
改善。因此不再补跑 ep12 的其余三个 seed，ep12 和 ep16 均不替换
ep8。现有证据表明继续 PPO 更新在 300 秒上改善了局部效率，却使
长时域吞吐对个别流量实现变得更脆弱。下一步不是盲目扩大 episode，
而是在 ep8 基座上设计长时域稳定性约束或按 600 秒留出门槛早停。

### V16 切相置信度诊断与静态迟滞否决实验

V16 在 300 秒和 600 秒留出集上均显著降低全车辆 waiting 与 time loss，但
切相次数高于 MaxPressure，急刹车也略高。逐 seed 对照显示，切相增量与急刹
增量并不稳定同向，因此不能直接把 `LAMBDA_SWITCH` 调大并重新训练。算法端先
增加了一个只用于确定性推理的单变量实验：对策略选中的新相位和当前相位计算
同一 masked categorical 下的 log-prob 差。这个差等于两动作的 logit 差；若
当前相位仍在既有 action mask 内且差值不高于
`COSLIGHT_SWITCH_LOGIT_MARGIN`，则暂时保持当前相位。当前相位已被最大绿灯
或安全约束禁止时绝不恢复。开关默认 `-inf`，此时只记录置信度而不改变动作；
`train.py` 和 `parallel_train.py` 会显式清除该环境变量，训练、collect、随机
采样和 checkpoint 语义均不改变。

默认关闭的等价性验证使用旧留出 seed 9951、20 路口、300 秒。新旧报告的
arrived、remaining、hard braking、tripinfo、全车辆 waiting、time loss 和
`400/290/290` 条命令/切相请求/实际切相全部一致，仅有约 `5.7e-14` 的浮点显示
误差。289 个合法切相候选的 logit gap 中位数为 0.0634、P95 为 0.4157；另有
一次当前相位已不合法，迟滞逻辑没有恢复它。由此确认诊断代码没有改变 V16
轨迹，也没有绕过 action mask。

校准 seed 9901–9902 上，margin=0.02 保持约 28% 的切相候选，但两个 seed 均
少到达 4 辆，全车辆 waiting 平均增加约 1040 秒、time loss 增加约 934 秒，
急刹还增加 16.5 次，明确失败。更保守的 margin=0.005 只保持约 4.2% 的候选，
校准均值 arrived 增加 0.5、急刹降低 0.66%，因此进入全新 seed 的最终复验。

最终复验使用未参与阈值选择的 seed 9971–9974：

| 方法 | arrived | remaining | 终点 waiting | 完成行程 | 未完成 waiting | 全车辆 waiting | 全车辆 time loss | 急刹车 | 实际切相 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MaxPressure | **74.00** | **763.00** | 576.1 | 101.29s | 15271.8 | 16066.4 | 24831.5 | **7980.5** | **220.8** |
| 原始 V16 ep8 | 73.75 | 763.25 | **537.1** | 99.94s | **14548.4** | **15329.6** | **24211.6** | 8219.3 | 292.0 |
| V16 + margin=0.005 | 72.75 | 764.25 | 554.2 | **97.85s** | 14860.9 | 15578.5 | 24437.9 | 8113.8 | 279.8 |

margin=0.005 相对原始 V16 虽把急刹降低 1.28%、切相降低 4.2%，却平均少到达
1 辆，全车辆 waiting 增加 1.62%、time loss 增加 0.93%、未完成车辆 waiting
增加 2.15%；seed 9973 单独少到达 4 辆。完成行程缩短 2.1% 主要受少完成车辆
的截尾效应影响，不能视为真实 Pareto 收益。因此静态全局 logit 迟滞被否决，
`DEFAULT_SWITCH_LOGIT_MARGIN` 保持 `-inf`，不进入部署配置。诊断字段和显式
实验参数保留，用于复现实验与选择后续有条件保护，但不继续扫描更多全局阈值。

这组实验还在新 seed 上确认了原始 V16 的稳定取舍：相对 MaxPressure，全车辆
waiting 降低 4.59%、time loss 降低 2.50%、完成行程降低 1.34%，但 arrived
少 0.25 辆、急刹增加 2.99%、切相增加约 32%。下一步应利用 Protocol 2.0 已有
车辆 `hard_braking_since_last_decision`、`next_signal.intersection_id` 和车辆
位置，把急刹归因到具体路口及最近一次实际切相的时间窗口；在定位高风险切相
状态前，不增加全局切相 reward，也不启用静态迟滞。

为此，算法端新增了纯观测归因，不需要修改 SUMO。它从后续的
`current_phase/stage/stage_elapsed` 估计真实绿灯切换时刻，再将 Protocol 2.0
已有的车辆急刹事件按下一个信号灯归属到路口。报告同时输出切相后
`5/10/15/30s`、距下一信号灯 `50/100/200m`、`10s 且 100m`
联合窗口和路口级事件率；每个事件率使用同窗口的车辆观测暴露量归一化，
避免把“V16 切相更多”本身误当成因果。该路径在 `step()` 的每次 5 秒观测
都执行，但只更新诊断计数器，不改变动作、reward、PPO 样本或 checkpoint。
由于急刹字段只表示上个 Protocol 决策间隔内的事件，归因时间分辨率为
5 秒；它用于筛查因果候选，不伪装成车辆级精确时间戳。

首次归因对照使用 20 路口、300 秒和全新配对 seed 9982–9983，报告位于
`runs/stage1_v16_brake_attr_9981_9982/evaluation.json`（目录名保留了启动时的
base seed，JSON 中的实际 seed 为准）。车辆间隔事件与 SUMO 累计急刹在四个
回合中全部一致，覆盖率为 100%，因而这次没有“车辆在观测前离网”造成的
归因缺口。

| 方法 | arrived | 终点 waiting | 全车 waiting | 全车 time loss | 急刹 | 实际切相 |
|---|---:|---:|---:|---:|---:|---:|
| MaxPressure | 73.50 | 560.1 | 15903.6 | 24676.3 | 8110 | 225 |
| **V16 ep8** | **74.00** | **417.8** | **15211.9** | **24155.1** | 8130 | 292 |

V16 在两个 seed 上的全车 waiting 和 time loss 都更低，均值分别降低
4.35% 和 2.11%，arrived 增加 0.5 辆。急刹只增加 20 次（0.25%），而且两个
seed 的差值分别为 `+53/-13`，方向不稳定。更重要的是，暴露量归一化后：

- 切相后 5/10/15/30 秒内，V16 急刹率相对 MaxPressure 分别变化
  `-0.46%/-2.06%/-4.51%/-3.36%`；
- “切相后 10 秒且距下一信号灯 100 米内”的急刹率降低 1.29%；
- V16 的切相后 30 秒风险相对 30 秒外仅为 1.13 倍，MaxPressure 为
  1.35 倍。

因此，“V16 因全局切相更多而造成急刹增量”被这组实验否决。急刹差异
更像是特定路口和交通状态的局部问题：`demo_5` 和 `demo_9` 在两个 seed
上的千次车辆观测急刹率都比 MaxPressure 高，平均差值为 `+27.4/+32.7`；
`demo_14/15/16/18` 则稳定更低。这些 ID 只用于定位和查找共同状态，不会
写入策略特判。原始 V16、`LAMBDA_SWITCH=0.05` 和默认关闭的迟滞配置继续冻结。
下一个候选必须先用路口的相位数、近灯车流、速度和排队特征找到可泛化条件，
再做默认关闭的单变量实验；不扫描更多全局 margin，也不调大全局切相
reward。

### Stage 2：低频云端区域先验（已实现，因果链诊断中）

Stage 2 不把云信息拼进 Actor 观测，也不训练新的云端网络。原因是拼接观测会
改变输入维度、废弃 V16 checkpoint，并把“云是否有效”和“重新训练的随机性”
混在一起。当前实现保持 V16 的网络、reward、optimizer 和 checkpoint 格式
全部不变，云端只调制 Actor 中已有的显式物理压力先验：

`pressure_prior = pressure_prior_scale × [movement_pressure + (cloud_weight - 1)]`

`cloud_weight` 的中性值为 `1.0`，上限默认 `1.2`，因此方向 bonus 的范围是
`[0, 0.2]`。它只作用于上述显式压力项，
不会乘到 learned phase scorer、conditioned actor head 或 observation 上。因此
`COSLIGHT_CLOUD_MODE=off` 时不仅动作不变，连旧调用链也不会收到新增参数；单测
已经验证中性权重与原 V16 的 action 和 action log-prob 位级一致。云模式只允许
用于 `model/stochastic_model` 推理，训练和并行 collect 若误开会直接失败，避免
无意改变 on-policy 数据语义。

云运行期仍只读取 Protocol 2.0：每个路口的来向车道排队、车辆密度、占有率和
仿真时间。云以默认 60 秒周期聚合区域状态，给出区域目标排队水平、区域优先级
和每条短走廊的主导方向；路侧每 15 秒决策时把对应方向相位的权重限制在
`[1.0, 1.2]`。区域排队是“各来向车道容量占比的平均值”，实测尺度约为
`0.00–0.02`，因此默认目标值修正为 `0.01`，优先级按超出这一目标的相对比例截断到
`[0, 1]`。若该相位当前直接下游车道密度达到默认 `0.7`，路侧立即把权重
恢复为 `1.0`，不等待下一次云更新。第一版只做“允许安全方向获得正向先验”，
不加入负权重、不改变 reward，也不加入新的回溢惩罚，保证本轮实验只有一个
行为变量。

区域和走廊不是按 `demo_N` 编号猜测。`build_cloud_topology.py` 离线读取只读的
`TotalMap_20.signals.net.xml` 与 `official_tls_topology.json`，沿 passenger 可行
道路搜索下一个受控路口，并把结果写到算法侧
`configs/cloud_topology_v1.json`。运行期不导入 sumolib、不读取 SUMO net 文件。
当前真实网络生成结果为 20 个官方路口、2 个强连通区域、5 个不超过 1500 米的
短走廊和 136 条有向可达联系；重复生成的文件 SHA256 均为
`aca59767e210ef10810eba23c29995f6709beeb654752f585eab5afb9f77dd68`。
再用同一份 `tls_manifest.json` 的真实相位绿灯模板做静态交叉检查，53 个可控
相位中有 36 个能映射到上述短走廊目标，覆盖 12 个路口；未处于短走廊的路口
保持权重 1.0。这说明云端不是因映射为空而必然成为装饰，同时也不会为长距离
可达关系伪造“绿波”。
这也解决了 Protocol 元数据 `direct_neighbors` 过稀、无法表示隔着无信号节点的
相邻受控路口这一问题，但只服务于云端规则，不悄悄扩大 V16 Actor 的 attention
范围。

新增文件及职责：

- `build_cloud_topology.py`：离线、确定性生成区域/走廊/相位出口目标映射；
- `configs/cloud_topology_v1.json`：第一版区域/走廊静态拓扑产物；
- `configs/cloud_topology_v2.json`：增加最短路径首出口、目标入口和自由流旅行时间的
  车团预测拓扑，仍只由算法侧离线生成；
- `cloud.py`：无训练参数的低频区域规则、方向选择、权重与回溢门控；
- `test_build_cloud_topology.py`：强连通分区、走廊分组、方向和结构校验；
- `test_cloud.py`：安全方向调制、低频更新、回溢降级和非法配置测试；
- `controller.py`：仅在显式启用时把云权重传给 pressure prior，并输出诊断；
- `evaluate.py`：增加云模式、拓扑、周期、权重上限、目标排队和回溢阈值参数。

截至本段记录，完整 CoSLight 算法测试为 `123 passed`。SUMO 场景代码未修改，
`simulation/sumo/scenario.py` 校验值仍为
`676e05e31376e29671ddeecfb37aee8b279953149f73892763e5f3173524ea3e`。

第一个同 checkpoint、同 seed（`9982`）的 300 秒真实对照已完成。`cloud off`
与旧 V16 报告的 arrived=74、remaining=763、endpoint waiting=420.3、
hard_braking=8114、完成行程时间=100.1068s、指令数=400、切相请求/执行=294/294
全部一致，证明默认关闭不改变 V16 轨迹。最初的目标排队 `0.25` 远大于真实区域
尺度，虽然云更新 5 次，但优先级、计划增益和相位放大全为 0，这一配置已否决。
改为与实测统计口径匹配的 `0.01` 后，同 seed 出现 5 条计划增益、112 次非中性相位权重，
平均权重 1.00924、最大权重 1.17175；但交通指标、指令计数和每路口相位序列仍与 off
完全一致。因此当前不能宣称云有效；因果断点已从“云没有输出”缩小为“权重产生了
数值变化，但尚未改变 Actor 决策”。
在第二个 seed `9983` 上加入同状态反事实 logit 诊断后，结论再次复现：400 个
路口决策中有 36 个的合法动作 logit 发生了变化，最大变化为 `0.28693`，但动作改变
仍为 0；而且 36 个决策中有 35 个连原本的最优动作也被加权。候选动作的中位跨越间隙
从加权前的 `0.16325` 增到加权后的 `0.18997`，离真正翻转最近也仍差 `0.00508`。
交通轨迹与旧 V16 seed 9983 报告再次只有 `5.7e-14` 的浮点显示误差，每路口相位直方图
也完全相同。这证明不能把上限从 1.2 盲目放大：当前乘法大部分是在强化 Actor 已有选择，
上限越大反而可能让候选动作更难翻转。
同轨迹的两个影子候选也已被否决：`(weight-1) × mean_incoming`
的上游需求残差和最大为 `0.2` 的纯方向 logit 先验都是 400 次决策中 0 次动作
改变。因此本问题不是换一个增益强度就能解决，当前诊断转向“一个路口的多个相位是否被
同时映射到同一走廊方向”以及“云规划是否只复述了 Actor 已有的局部压力判断”。
加权决策探针最终把第一个原因定位为真：36 个有效时刻中，16 次是该路口四个合法相位
全部加权，20 次是两个合法相位全部加权，并且同一时刻所有权重完全相同。抽查算法侧
拓扑产物后发现，`outgoing_edge_targets` 的语义是 10km 内一般可达；某个目标虽然最短路径只有
数百米，但从反方向出口绕行数公里也能最终到达。原运行期只匹配目标 ID，没有再验证该出口
的实际路径距离，于是把绕行相位也误当成短走廊相位。这是算法侧拓扑消费错误，不是 SUMO
问题。现在相位映射会同时校验该出口到目标的路径不超过拓扑中的 `max_corridor_distance_m`
（当前 1500m）；单测专门验证 5000m 绕行出口不会被加权。

距离过滤是必要的防御性修复，但同 seed 复验后，运行期仍映射 35 个动作、放大 104 次，
36 个有效时刻仍然给该路口全部合法动作相同权重，动作和交通轨迹均未变化。进一步只读检查
SUMO 的 Protocol 2.0 元数据构造发现，`connection_priorities` 明确把信号状态大写 `G`
编码为 `protected`、小写 `g` 编码为 `permissive`；旧 StateBuilder 只遍历字典键而丢弃
优先级值，云端于是把相位中的许可转向也当成主放行方向。只要每个相位都带一个通往目标
走廊的许可转向，所有相位就会被同权加权。算法侧现已拆分两套连接：Stage 1 的观测、
压力和冻结 V16 仍使用全部可放行连接，Stage 2 云端方向映射只使用 `protected` 主放行连接。
这一改动不修改 SUMO，也不改变云关闭时的基线调用链；新增单测同时验证“Stage 1 保留
permissive、Stage 2 排除 permissive”。

保护/许可拆分后，映射动作从 35 降到 19、放大次数从 104 降到 48，并首次出现 4 个
“同一路口不同合法动作获得不同权重”的时刻，说明相位方向映射已开始提供相对信息；但旧的
`movement_pressure × cloud_weight` 仍是 400 次决策中 0 次动作改变。原因是 movement
pressure 是有符号量：目标方向压力为负时，大于 1 的乘数反而让 logit 更负；即使为正，
乘法也常只强化既有最优动作。同状态影子计算表明，直接加入有界正向方向 bonus 可在
`demo_20` 改变 3 次动作。于是第二个单变量候选采用上述加法公式：中性权重仍严格贡献 0，
最大只贡献 0.2，不训练新网络、不改变观测、不碰 learned residual；新增负压力单测确保
云端优先方向不会再次受到反向惩罚。

同 seed `9983` 的真实加法先验实验与影子结果完全一致：400 个路口决策中有 3 个动作
发生改变，均位于 `demo_20` 的 140/155/170 秒；该路口的相位指令直方图也随之改变，
证明“云规划 → 相对先验 → Actor 指令 → 实际相位 → 交通状态”的链路已经打通。单 seed
交通结果是混合的：arrived/remaining 不变，全车 waiting `+0.139%`、time loss
`+0.067%`，急刹 `-1.203%`，平均速度 `+0.063%`。这些变化幅度不足以支持接受或否决，
因此保持公式和参数不动，进入与现有 V16 完全配对的 4 个留出 seed（9951–9954）门槛。

4-seed 门槛否决了无局部门控的加法候选。四个 seed 都在 `demo_20` 产生 3 次相同的
动作改变，但只有两个 seed 改变交通轨迹；另两个 seed 虽然最终相位指令直方图已改变，
交通指标仍位级一致，说明干预时该相位没有作用到有效车辆，而不是指令被约束层吞掉。
相对 V16 无云基线，云候选的均值为 arrived `73.50→73.25`、全车 waiting
`15309.95→15327.23`（`+0.112%`）、time loss `24266.95→24288.65`
（`+0.089%`）、完成车辆 waiting `+1.831%`、急刹 `+0.085%`；全车累计
waiting/time loss 是 `0/4` 获胜、`2/4` 完全持平，因此不能保留为 Stage 2 结果。

探针进一步显示，被删除价值最高的干预发生在 155 秒：云端选择的相位
`mean_incoming=0`、movement pressure 为 `-0.03`，即没有当前进口需求仍强制改变
方向。下一候选不扫描经验阈值，而加入物理边界 `movement_pressure > 0`：云 bonus 只
能给“进口需求高于对应出口负载”的相位，压力为 0 或负数立即恢复中性权重。离线同状态
计算预测保留 140/170 秒两个有正压力的干预、删除 155 秒空需求干预；诊断新增
`local_pressure_suppressed_phase_decisions`，并有专门单测覆盖。该候选仍需真实
单 seed 和 4-seed 门槛，未通过前不能宣称云端带来收益。

正压力候选在 seed 9983 中按预期把动作改变从 3 次降为 2 次，并记录 24 次
`local_pressure_suppressed_phase_decisions`；但全车 waiting/time loss、完成车辆指标
和急刹与无门控加法候选完全相同。这说明被删除的空需求干预原本就没有交通作用，剩余
140/170 秒两次干预才是退化来源，因此本候选被单 seed 门槛否决，不进入 4-seed。

回查 125 秒云计划与真实拓扑发现，云选择 corridor_5 的 north：真实最短首跳是
`demo_20→demo_17=900.96m`，应映射南北直行相位；旧消费逻辑却也接受东西向出口先绕行
1374.92m 后到达 demo_17 的路径，并把它错误标成 north，导致实际加权并选择了东西直行
相位。生产拓扑 18 条 corridor link 中，每条都有且只有一个出口距离与 source-target
最短路相等，但 10 条还存在一个或多个低于 1500m 的错误绕行出口，证明这是系统性问题。
运行期现要求出口路径距离与 corridor link 最短距离在浮点误差内相等，1500m 只继续作为
外层安全上限；新增诊断 `indirect_corridor_routes_rejected`，回归测试使用 1000m 绕行
（仍低于 1500m）验证其不会再映射为 100m 直达走廊。该修复仍需同 seed 真实验证。

首跳修复的 seed 9983 真实结果与因果预测一致：运行期映射动作 `19→18`，拒绝 37 个
“能到目标但不是 corridor 最短首跳”的相位关联，正压力门控后放大 21 次、反事实动作
改变 0 次；arrived、remaining、累计 waiting/time loss、完成行程、急刹、相位指令均
恢复到 V16 无云基线（除 endpoint waiting 的 `5.7e-14` 显示误差外位级一致）。在这条
正确轨迹上，把方向 bonus 离线放大到配置上限 2.0 仍是 0 次动作改变：15 个有效探针中，
云要么强化 Actor 已选动作，要么给多个动作相同 bonus。当前源路口规则因此被否决为
“安全但信息冗余”，不能用更大权重继续碰运气。

下一版 Stage 2 的信息方向必须反转为真正的跨路口协调：云从源路口的 corridor 出口车道
检测自由流车团/预计到达，把低频、带时间戳的先验下发给目标路口中接收该上游来车的
protected 相位，同时保留目标出口回溢降级。这样提供的是下游本地 Actor 尚未观测到的
未来到达，而不是重复源路口当前队列。实现前先扩充算法侧离线拓扑的最短路径首出口、
目标入口与自由流旅行时间，并用同轨迹 shadow 验证“预测车团 → 下游相位 bonus →
动作变化”后才允许真实控制。该方向与协调 MaxPressure 中利用上游自由流车团提高走廊
movement 权重、以及层级控制中“区域聚合信息向下游 worker 下发目标”的成熟做法一致；
仍不训练新云网络，也不修改 SUMO。

### 9.8 Stage 2 车团到达云先验：影子成功，主动抢绿候选否决

`cloud_topology_v2.json` 由只读网络
`data/maps/sumo/generated/network/TotalMap_20.signals.net.xml` 和官方拓扑离线生成，
schema v2 对每条最短路径补充：

- `source_outgoing_edge`：源路口放行后进入走廊的第一条边；
- `target_incoming_edge`：到达目标路口前的最后一条边；
- `free_flow_time_s`：按各边长度/限速求和的自由流旅行时间。

产物 SHA256 为
`2797a268c7241a6f8692cb03cd29360ccc699bbc44374d69e5964a7137df6c6e`；
20 个路口、5 条走廊组中的 18 条短走廊连接均有完整路径字段，自由流旅行时间范围
`11.236–58.323s`。运行期不读取 SUMO net，只消费该算法 JSON 与 Protocol 2.0 实时观测。

车团影子规则不拼接 Actor 观测、不训练新网络。云每 30 秒检查源路口当前相位所服务的
进口车道，用 `vehicle_count-halting_count` 判断移动车辆、用
`mean_speed/speed_limit` 计算自由流强度；事件按离线旅行时间投影到目标路口，在预测
到达前后各 15 秒的窗口内，只给接收 `target_incoming_edge` 的目标 protected 相位
有界加法先验，并继续用目标相位出口密度执行回溢降级。`platoon_shadow` 只计算同状态
反事实，实际动作仍为 V16；`platoon_control` 才把同一权重送入 Actor，而且默认配置
仍是 `cloud off`。

只读核查官方信号模板后发现 18 条连接中有 4 条缺少完整的 protected→protected
相位链。其中 `demo_2→demo_4`、`demo_11→demo_12` 的目标运动始终只有小写 `g`
让行态，不能保证云端加权后真正获得通行权，控制与影子都排除；另两条只在源端为
permissive。源端 permissive 车辆若已经有正速度，可以作为影子预测证据，但真实控制
保守地只接受源端大写 `G` 保护放行。因此影子最多映射 16/18，真实控制映射 14/18；
这不是算法漏配，也不应通过修改 SUMO 或把 `g` 冒充 `G` 来绕过。

第一轮 seed 9983、20 路口、300 秒、V16 ep8 的 protected-only 影子实验产生 27 次
上游车团预测、52 次活动连接决策、48 次目标相位加权；400 个路口决策中 42 个的
合法 logit 被改变，12 个反事实动作翻转，集中于 `demo_6/19/20`。arrived、remaining、
endpoint waiting、全车累计 waiting/time loss、完成行程与急刹均与同 seed V16 基线
逐项完全一致，证明影子隔离正确。旧源路口排队反馈规则在任意权重下为 0 次动作变化，
而新规则达到 12 次，证明“上游预测→下游接车相位→Actor”确实提供了非局部有效信号。

随后按单 seed 门槛测试了三个真实控制候选，均未进入 4-seed：

1. 强先验 `max_weight=1.20`、最小车团 1、源端含已实际移动的 permissive 证据：
   49 次预测、35 次动作改变；相对 V16，arrived `74→72`、全车 waiting
   `+1.175%`、time loss `+0.124%`，虽完成车辆行程时间 `-2.220%`、急刹
   `-0.565%`，但属于把服务从全网转移给少数完成车辆，否决。
2. 只允许源端 protected、弱先验 `1.02`、最小车团 1：27 次预测、4 次动作改变；
   arrived 不变，全车 waiting `+0.283%`、time loss `+0.088%`、急刹
   `-1.964%`，主延误指标仍退化，否决。
3. 在候选 2 上只改 `min_platoon_vehicles: 1→2`：9 次预测，最终只在 125 秒
   `demo_14→demo_19` 产生 1 次动作改变；全车 waiting 仍 `+0.168%`、time loss
   `+0.004%`，急刹 `-0.982%`，完成车辆行程时间基本不变，仍按延误门槛否决。

动作—预测关联表明，弱先验的 4 次改变中 3 次由单车触发；把单车排除后，唯一剩余
干预仍是“在预计到达前主动切到接车相位”，没有产生旅行时间收益。这说明当前失败点
不是云信息无法影响 Actor，而是控制语义过于激进：提前抢绿会牺牲目标路口其它方向。
下一候选必须先在影子模式验证“仅当接车 protected 相位已经是当前绿灯时，云端阻止
Actor 过早切走/适度延长绿灯”，不允许云主动切入新相位；同时在反事实事件中记录当前
相位，区分 `hold_receiving` 与 `switch_to_receiving`。在该影子链路出现有效 hold 事件
之前，不继续扫权重、不跑 4 seed，也不能宣称 Stage 2 已带来交通收益。

为避免盲目放大 `max_weight`，评估器对同一时刻、同一观测同时计算 V16 中性策略和云调制
策略，记录合法动作 logit 变化、原始 top-2 margin、当前执行动作，以及候选动作距离跨过
argmax 边界剩余多少 logit。新增的 `platoon_hold_shadow` 只允许云给“目标路口当前已经
执行、且正接收预测车团”的 protected 相位加权；其它目标相位即使有预测车团也保持中性，
因此云只能阻止过早切走，不能主动抢绿。

seed 9983 的上界影子实验使用 `max_weight=1.20`、最小车团 1：产生 27 次预测、24 个
实际加权决策，并压制 25 个“接车相位不是当前相位”的候选；400 个路口决策中出现 5 次
反事实动作改变，全部被诊断为 `hold_current_receiving`，没有任何提前切入新相位。由于是
影子模式，交通轨迹与 V16 基线逐项一致。固定这些观测与 logits 的离线强度扫描显示，
权重 1.075/1.15/1.20 分别只会产生 1/3/5 次保持事件，因此真实控制选择中等强度 1.15，
而不是直接使用影子上界 1.20。

`platoon_hold_control` 的 seed 9983 单 seed 真实控制产生 6 次保持事件；arrived/remaining
维持 74/763，全车累计 waiting `15240.40→15235.65`（`-0.031%`）、time loss
`24209.65→23996.37`（`-0.881%`）、完成车辆平均行程 `100.553→100.036s`
（`-0.515%`）、急刹 `8146→8004`（`-1.743%`）。仿真终点的瞬时 waiting
`415.35→519.85` 虽然恶化，但它只描述最后一个采样时刻，不能覆盖整段累计改善，因此
按预先约定进入 4-seed 配对门槛，而不是据此接受或误杀候选。

在固定留出 seed 9951–9954 上，权重 1.15 的 hold control 分别改变 7/3/5/5 次动作，所有
事件仍是 `hold_current_receiving`。相对完全相同的 V16 无云基线：

- arrived 均值 `73.5→74.0`，2/4 增加、2/4 持平；
- 全车累计 waiting `15309.95→15154.91`（`-1.013%`），4/4 改善；
- 全车累计 time loss `24266.95→23979.11`（`-1.186%`），4/4 改善；
- 完成车辆 waiting `-4.335%`、time loss `-5.574%`、平均行程时间 `-0.888%`，均为
  4/4 改善；
- 急刹均值 `8027.5→8098.75`（`+0.888%`），仅 1/4 改善，是当前明确的代价。

因此该候选是第一个通过 Stage 2 的 **300 秒多 seed 门槛** 的云端方案：云的非局部信息、
Actor 动作、真实相位和短时交通收益之间已有完整因果链。它只能暂记为 Stage 2 V1 候选，
默认配置仍保持 `cloud off`，只有显式选择 `platoon_hold_control` 才启用；是否冻结必须再过
600 秒长时门槛，不能把短窗口结果外推成最终收益。

600 秒、seed 9961–9964 的配对结果否决了 V1 最终控制版。相对 V16 无云基线，arrived
均值 `341.5→335.5`（`-1.757%`），仅 1/4 提升；全车累计 waiting 只从
`65406.29→65391.53`（`-0.023%`），time loss `-0.212%`，两者都只有 2/4 改善；
未完成车辆 waiting 反而 `+0.288%`，急刹 `+0.119%`。完成车辆平均行程虽降低
`1.526%`，但同时少完成 6 辆，存在明显完成样本选择偏差，不能当成系统收益。seed 9964
终点瞬时 waiting 从 319.95 升到 2299.8，更说明后半程可能出现局部服务失衡。

长时运行的动作关联给出下一条可验证病因：四个 seed 分别保持 11/14/9/10 次，而最差的
9962 有 14 次保持，其中 9 次只由 1 辆移动汽车触发；当前 `min_platoon_vehicles=1`
实际上把单车也当成车团，云端为了很小的非局部收益多次延长绿灯，长期累计后牺牲其它方向。
下一候选只把最小车团从 1 改为 2，保持权重、Actor、预测时窗和安全门控不变；先过 300 秒
4-seed 门槛，若通过再跑 600 秒。若仍失败，才实现“每个绿灯周期最多额外保持一个决策步”
的公平预算，避免同时调整两个变量。

后续实验已否决“最小车团 2”：300 秒、seed 9951–9954 中每个 seed 均有
9 条预测，但云端权重对 Actor 为 0 次动作改变，所有交通指标与 V16 逐项完全相同。
它是安全的，但在当前流量下惯性为零，因此不进入 600 秒。

为直接处理长时实验中的负压力保持，新增 `platoon_hold_safe_shadow/control`：
只有当前接车运动的归一化压力严格为正时，云端才能延长当前绿灯。seed 9983 的
影子上界把原有 5 次反事实改变压缩为仅 1 次（125 秒、`demo_6`），并记录
14 次非正压力压制。但权重 1.15 的真实控制在首次保持改变轨迹后，140 秒又连续
保持一次；相对 V16，全车 waiting `+1.671%`、time loss `+0.905%`、完成车辆平均行程
`+0.487%`，仅急刹 `-1.191%`，故不进入多 seed。这证明“当前正压力”能排除明显错误方向，
但不能防止云端在反馈轨迹上连续抢占本地决策。

下一个单变量是显式 `cloud_hold_cooldown`，默认为 0（不改变历史模式）。实验值 30 秒
表示同一路口的云保持真正改变 Actor 动作后，一个云更新周期内不允许再次保持。
该保护不改 Actor、reward 或 SUMO，且独立诊断被冷却拦截的候选数。
同 seed 9983 真实控制中，冷却成功将 140 秒的连续保持压制，只保留 125 秒
`demo_6` 的首次保持；但全车 waiting 仍 `+0.755%`、time loss `+0.428%`、完成车辆平均
行程 `+0.194%`，仅急刹 `-0.503%`。因此连续抢占是加剧因素，但不是唯一病因；
首次保持本身也无益。关联预测显示，它只由 `demo_9→demo_6` 的 1 辆高速车触发，而旧公式
几乎只按速度给权，导致单车也获得 `1.149` 的近上限权重。

与车队控制的“显著车团到达与排放”语义对齐后，当前先不新造连续权重公式，而是复用已有
最小车团门控：300 秒低需求时 `min_platoon_vehicles=2` 零干预视为正确的本地降级，
再在 600 秒后半程真实出现 2–8 辆车团时，检验“正压力 + 至少 2 辆”是否才产生收益。

600 秒、seed 9961–9964 的该组合仍未通过：每个 seed 只有 1/2/2/2 次动作改变，
但 arrived 均值 `341.5→339.25`（`-0.659%`，3/4 下降），全车 waiting `+0.114%`
（0/4 改善），未完成车辆 waiting `+0.248%`（0/4 改善），急刹 `+0.211%`。
完成车辆平均行程看似 `-0.376%`，但同时平均少完成 2.25 辆，属于完成样本选择偏差。

这 7 次干预的逐动作检查显示，除 1 次外，被云端强制保持的当前相位虽然压力为正，
但它的 movement pressure 和 Actor logit 都低于本地即将切换的相位；云实际上在阻止一个更优的
本地切换。唯一当前压力略高的事件也未产生交通收益。此外，当前预测仅用离线自由流 ETA，
未建模车团离散、转向损失、残余队列和真实到达—排放过程，因此时间对齐也不足以支持真实控制。

据此，Stage 2 当前结论是：保留云端拓扑、低频预测、本地降级、影子反事实与完整诊断链；
真实控制默认仍为 `cloud off`，不冻结任何 hold control 版本，也不继续叠加启发式门控。
若重启真实云控制，必须首先在影子层引入可验证的到达—排放预测，并证明其不会压过更优的本地相位。

---

## 10. 已知限制

1. **自行车道权限**：`allowed_vehicle_type_ids = []` 表示无匹配车型（非无限制），算法端保守拒绝。
2. **静止换道**：速度 < 0.5 m/s 的车辆不推荐换道，避免 SUMO 校验异常。
3. **下游信号（C06）**：v1 可能为 0，后续版本实现跨路口信号查询。
4. **区域流量**：前 30 秒为 0 属正常（滑动窗口未填充），暖机后应出现非零。
5. **Lane Actor 尚未完成**：`lane_state.py` 只提供 41 维状态、槽位和动作 mask；当前车辆引导仍是 V1 规则策略，不能在论文中宣称已训练 Lane Actor。
6. **路口 891 配时告警**：当前 SUMO 场景会报告 `tlLogic '891'` 的 tl-index 7 缺少绿灯相位。这是场景/配时问题，算法侧未修改；实验报告应保留该环境缺陷，并确保所有对比方法使用相同场景。
7. **公平对比**：第一阶段训练和评估默认 `--vehicle-guidance off`，隔离信号控制收益；若显式使用 `rule`，所有对比方法必须统一开启，并另做车辆引导消融。
8. **异常恢复**：训练保持 fail-fast，不自动重试。失败或超时会先停止活动会话，并把此前正常完成的 rollout 刷新到 `*.recovery.pt`；使用日志中的 `--resume` 路径人工续训。
9. **同步 on-policy 约束**：同一批 worker 必须使用相同 `policy_generation` 和完全相同的模型权重；批次中任一 worker 失败，整批不进入 PPO。
10. **信号执行信息边界**：Protocol 2.0 当前只返回车辆动作结果，没有逐信号指令的接受/执行结果。算法已经通过后续 `current_phase/stage` 记录推断生效率与延迟，但无法直接得到 SUMO 内部拒绝原因。
11. **旧奖励兼容**：legacy `ΔP` 有首尾相消和有符号方向问题，只保留用于复现实验；新训练使用 `pressure`。
12. **通信图过稀**：Protocol 2.0 的 `direct_neighbors` 只包含“本路口出口边直接与另一受控路口入口边重合”的关系；当前 20 路口中 17 个只有自身。若路口之间隔着无信号节点，算法元数据无法追踪到“沿路网最近的受控路口”。v14 起 Actor 已改为逐路口本地编码后再按该图融合，消除了全网 Transformer 的信息泄漏；但图本身仍需数据侧补充多跳受控邻居或路口坐标，算法侧不会根据 `demo_N` 编号猜测拓扑。
13. **碰撞指标缺口**：600 秒并行评估日志出现过 SUMO `junction collision` 告警；当前评估报告记录急刹车但不单列碰撞数，且并行 stdout 无法可靠归属 method/seed。算法侧不改 SUMO；正式对比需由会话级输出补齐碰撞计数或让 SUMO 侧提供结构化指标。
14. **跨时段 checkpoint 语义**：V16 在 `off_peak` 训练，其 `demo_4` 合法相位为 `[1,2,3]`；`evening_peak` 为 `[1,2,3,4]`，而且同一序号的放行方向并不一致。当前加载器正确地拒绝直接复用，并输出精确 mismatch，不会为了跑通而放宽安全校验。要支持跨时段，需要训练包含多时段的相位语义条件策略，或把最后的动作头改为对相位排列等变的共享打分器并重训；这不属于云先验小改。

---

## 11. 参考依据

- CoLight 论文与官方仓库：图注意力用于动态建模邻近路口影响，论文建议邻域规模
  约为自身加四个邻近路口，而不是无条件聚合全网。
- PressLight：奖励定义为当前路口的负压力，并以平均旅行时间进行最终评价。
- SUMO-RL：常用奖励包括等待时间变化、负队列和压力，系统指标应同时包含总队列、
  总等待与平均速度。
- MAPPO 官方实现：Actor/Critic 使用独立 optimizer，value target 支持
  ValueNorm/PopArt；本项目采用较小的 running scalar normalization，不直接
  引入其训练框架。

参考链接：

- https://arxiv.org/abs/1905.05717
- https://github.com/wingsweihua/colight
- https://faculty.ist.psu.edu/jessieli/Publications/2019-KDD-presslight.pdf
- https://lucasalegre.github.io/sumo-rl/mdp/reward/
- https://github.com/marlbenchmark/on-policy
- https://arxiv.org/abs/2103.01955
