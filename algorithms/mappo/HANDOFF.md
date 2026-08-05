# IPPO → MAPPO 开发交接

> **状态警告（2026-08-03）：本文档中的旧“MAPPO-v1/v2”使用每路口局部奖励和局部回报目标，因此已重新归类为 `cc_ippo_local_reward`，不是 cooperative MAPPO。** 当前 cooperative MAPPO-v1 的权威定义见 [`../../docs/superpowers/specs/2026-08-03-cooperative-mappo-v1-design.md`](../../docs/superpowers/specs/2026-08-03-cooperative-mappo-v1-design.md)：同步 joint step、共享团队奖励、分散 Actor、集中 Critic 和团队回报目标。旧 checkpoint 与报告保留原路径和原 SHA，索引见 [`runs/LEGACY_CC_IPPO_INDEX.md`](runs/LEGACY_CC_IPPO_INDEX.md)。

> 用途：在新对话、新工作区中启动 MAPPO 开发时，保留本轮 IPPO 排障、训练和评测积累。  
> 生成时状态：IPPO v8 已形成可运行的 20 路口纯 RL 基线；当时 MAPPO 目录尚为空。  
> 生成日期：2026-08-01。  
> 本文件只记录历史上下文和建议，不代表当前 cooperative MAPPO 实现状态。

## 0. 新对话先做什么

新对话中的助手应按顺序读取：

1. 本文件：`algorithms/mappo/HANDOFF.md`；
2. 算法目录入口：[`../README.md`](../README.md)；
3. IPPO 完整说明：[`../ippo/说明文档.md`](../ippo/说明文档.md)；
4. IPPO 核心与测试：[`../ippo/controller.py`](../ippo/controller.py)、[`../ippo/test_controller.py`](../ippo/test_controller.py)；
5. 并行训练：[`../ippo/parallel_train.py`](../ippo/parallel_train.py)；
6. 统一评价：[`../evaluation/`](../evaluation/)；
7. 协议定义：[`../../docs/algorithm_interface.md`](../../docs/algorithm_interface.md)。

推荐给新对话的第一条消息：

```text
请连接 4090 服务器，先完整阅读
/home/kemove/devdata1/gsb/citypulse-v2x-sim-ippo-next/algorithms/mappo/HANDOFF.md，
再阅读其中列出的 IPPO、评价和协议文件。请先设计 MAPPO-v1：保持 IPPO v8
的 Actor、状态、动作、奖励、并行采样和评价不变，只把局部 Critic 改为训练期可见
全局状态的集中式 Critic。先做配对 smoke 和消融，不要直接启动长训练，不要修改 SUMO
网络、routes、tlLogic 或 TraCI 控制代码。
```

## 1. 当前仓库状态

| 项目 | 值 |
|---|---|
| 4090 SSH 别名 | `346-4090` |
| 当前工作区 | `/home/kemove/devdata1/gsb/citypulse-v2x-sim-ippo-next` |
| 当前本地分支 | `codex/ippo-next-v7` |
| GitHub 目标分支 | `feature/rl` |
| 最新远端提交 | `e5d6f7c`，算法 README |
| IPPO 功能提交 | `a6777e2` |
| 当前测试 | `84 passed` |
| MAPPO 目录 | `algorithms/mappo/`，当前无实现 |

当前工作区在生成本文件前是干净的。本文件不会自动提交或上传 GitHub。

建议为 MAPPO 建立独立 worktree，避免污染已经验证的 IPPO 工作区：

```bash
cd /home/kemove/devdata1/gsb
git -C citypulse-v2x-sim-ippo-next fetch origin feature/rl
git -C citypulse-v2x-sim-ippo-next worktree add \
  -b codex/mappo-v1 \
  /home/kemove/devdata1/gsb/citypulse-v2x-sim-mappo \
  origin/feature/rl
```

这只是推荐命令。新对话执行前应重新检查目标目录、分支和 worktree，不能覆盖已有目录。

## 2. IPPO 最终交付物

### 2.1 算法定义

当前 IPPO 是参数共享的独立 PPO：

- 20 个路口共享 Actor/Critic 参数；
- 每个路口只用本地状态、候选相位语义和本路口 ETA 来车需求；
- 每个路口独立保存轨迹并计算 GAE；
- Critic 不读取其他路口状态，因此不是 MAPPO；
- 训练使用多 SUMO 同步采样，中心 learner 统一更新；
- 不使用 MaxPressure 行为克隆、教师损失、动作先验或 residual policy。

发布 checkpoint：

```text
algorithms/ippo/models/ippo_v8_20tls_ep160.pt
```

SHA-256：

```text
5656e351dc66aa7ffebd50d6a01109aff6a71bca393976eb45e9dca70c7ef107
```

checkpoint 关键元数据：

| 字段 | 值 |
|---|---|
| `model_version` | `v8` |
| `episode` | `160` |
| `intersection_ids` | `demo_1..demo_20` |
| `obs_dim` | `132` |
| `act_dim` | `4` |
| `action_interval` | `15.0` s |
| `max_green_factor` | `2.0` |
| `phase_feature_schema` | `connection_pressure_service_age_eta_demand_v2` |
| `effective_demand_enabled` | `true` |
| `training_seed_range` | `88301..88460` |
| `training_periods` | `off_peak` |

### 2.2 当前结果

统一评测：20 路口、off_peak、300 s、deterministic、seeds `62001..62004`。

| 方法 | 行程时间↓ | 等待时间↓ | 队列↓ | 吞吐量↑ | 决策延迟↓ | 燃油强度↓ |
|---|---:|---:|---:|---:|---:|---:|
| IPPO v8 ep160 | 100.53 s | 8.43 s | 0.08 | 906 veh/h | 12.53 ms | 13.82 L/100km |
| fixed | 103.04 s | 16.22 s | 0.22 | 744 veh/h | N/A | 14.70 L/100km |

ep160 相对 fixed：

- 行程时间 `-2.4%`；
- 等待时间 `-48.0%`；
- 队列 `-63.6%`；
- 吞吐量 `+21.8%`；
- 燃油强度 `-5.9%`。

这只证明 ep160 在当前 off_peak 四个验证 seed 上明显超过 fixed。它没有证明：

- 已在 morning/evening、扰动和需求倍率场景泛化；
- 已超过 MaxPressure；
- ep160 是 MAPPO 的公平参照训练量；
- 四个验证 seed 足以形成最终论文结论。

## 3. IPPO 版本演进与真实教训

### 3.1 v5a：奖励修好后仍会锁相

v5a 使用物理尺度归一化的局部压力奖励，解决了早期 `W(t-1)-W(t)` 在高拥堵稳定时接近 0、reward RMS 漂移和跨路口尺度不一致的问题。

但 v5a Actor 直接按动作下标输出 logits。异构路口中“动作 0”服务的 movement 不相同，共享 Actor 会把无语义的下标相关性当成规律。20 路口策略出现明显动作偏置和长时间锁相。

教训：**参数共享不是简单共享动作编号。异构路口必须给候选动作提供交通语义。**

### 3.2 v6：候选相位评分和最大绿灯约束救回策略

v6 改为对每个候选相位使用同一个 scorer，根据 incoming、outgoing、pressure、当前相位和 service age 等特征独立打分；同时加入最大绿灯 action mask。

这两项比继续调整 PPO 学习率更重要：

- candidate scorer 让共享参数理解“这个相位服务什么”；
- max-green mask 防止随机初始化或偏置策略无限保持当前相位；
- 单相位路口必须特殊处理，不能屏蔽成零合法动作。

教训：**动作表达和合法性通常比网络加深更先决定交通 RL 是否能学。**

### 3.3 v6b：奖励项变多不等于更好

曾加入区域队列和过早切相奖励。4 个留出 seed 上稳定退化，因此回退。

失败原因不是简单的“权重没调好”：区域项、切相项和原局部奖励同时改变后，无法定位是哪一项破坏了优势估计；部分项还依赖不稳定或量级偏小的实时信号。

教训：

- 一次只改变一个奖励因素；
- 奖励项必须有非零方差和足够动态范围；
- 先记录分量分布，再决定是否纳入总奖励；
- reward 改善必须同时对应 waiting、queue 和 throughput 改善。

### 3.4 v7：单标量来车需求信息太少

v7 加入运行车辆的未来需求，但用单标量表示。数据链确认有效，增益很小且部分 timeLoss 略退化。

教训：**“有预测信息”不等于表达足够。不同时间尺度的到达需求不能全部压成一个数。**

### 3.5 v8：ETA 双桶形成稳定增益

v8 把运行车辆按 ETA 分为：

- `0 < ETA <= 15 s`；
- `15 s < ETA <= 30 s`。

三组配对消融中，`demand=on` 相对 `off` 平均：

- arrived `+4.45%`；
- 全过程 waiting `-4.06%`；
- timeLoss `-3.89%`。

教训：

- 交通状态表达经常比“换一种 PPO 变体”更先产生收益；
- 消融开关必须保持网络形状、初始化、奖励和 seed 不变；
- 三次重复比单次最好结果更可信，但仍要检查是否由单个 pair 主导。

## 4. 必须复用的工程原则

### 4.1 requested action 不等于 executed action

策略请求新相位后，仿真端还要执行最小绿灯、黄灯和全红。若请求动作立刻记 transition，会把过渡期间产生的奖励错误归给尚未生效的动作。

IPPO 的做法：

1. 策略请求动作后建立 pending transition；
2. 后续实时观测持续累计 delay、停车车辆秒、队列、过线量和出口状态；
3. 到下一次合法决策或正常 episode 结束时才完成 transition；
4. 无任何后续观测的 pending 动作丢弃；
5. 仿真失败或被停止时整轮 rollout 丢弃。

MAPPO 必须保留相同执行对齐，否则集中式 Critic 学到的只是错误 credit assignment。

### 4.2 同步并行必须保证同一代策略

多 SUMO worker 的正确结构是：

```text
中心 learner 导出 policy generation G
  → N 个 worker 独立 SUMO、seed、session 采样
  → worker 只返回原始轨迹，不更新参数
  → 中心验证 metadata 和 generation
  → 任一 worker 失败则整批作废
  → 全部成功后统一 PPO 更新到 G+1
```

不能让 worker 各自更新，也不能把 G 与 G+1 样本混为一批。MAPPO 的 centralized critic 同样是 on-policy，不会因为有全局状态就允许旧策略数据混入。

### 4.3 seed 是实验数据的一部分

必须记录：

- 训练 seed 起止范围；
- 每个 worker 的 seed；
- 训练 period；
- 评估 seed；
- checkpoint 到底消耗到哪个 seed；
- 评估 seed 与训练 seed 是否重叠。

IPPO 会在评价前拒绝 seed 重叠。MAPPO checkpoint 也必须保存并验证同类元数据。

### 4.4 checkpoint 不只是网络权重

至少保存：

- Actor/Critic 权重；
- 两个 optimizer state；
- episode 和 policy generation；
- observation/action schema；
- 路口集合与顺序；
- action interval、最大绿灯和 demand 开关；
- reward definition；
- 训练 seed 范围与 periods；
- MAPPO centralized-state schema。

写入采用临时文件后原子替换。版本或 schema 不匹配时，在启动 SUMO worker 前失败。

### 4.5 缺失数据不能当 0

实时帧丢失、TripInfo 数量不一致、燃油车型缺少 powertrain 或 telemetry 单位无法确认时，评价结果应为 `N/A`。把缺失值写成 0 会让失败运行看起来异常优秀。

## 5. 防御性编程与冗余的平衡

IPPO 后期加入了很多防御性检查。以下必须保留：

- checkpoint schema 和配置兼容；
- 训练/评估 seed 防重叠；
- action mask 至少一个合法动作；
- worker metadata 和 policy generation 一致；
- 失败 worker 导致整批 on-policy 数据作废；
- 截断与 terminal 分开处理；
- 缺失评价数据返回 `N/A`；
- checkpoint 原子写入。

但当前 IPPO 也留下了真实维护成本：

- `controller.py` 约 1800 行，同时承担特征、奖励、执行链、PPO 和 checkpoint；
- `train.py`、`parallel_train.py`、`evaluate_ckpt.py`、`evaluate_paired.py` 重复 SUMO 环境初始化；
- 超时 session 清理和 seed 校验存在重复；
- 多处环境变量配置增加了隐式状态。

MAPPO 不要复制整个 `controller.py` 后继续堆功能。建议从第一天分文件：

```text
algorithms/mappo/
├── __init__.py          # Protocol 2.0 initialize/step/finish
├── config.py            # 显式配置与 schema/version
├── features.py          # 本地状态、候选相位、全局 critic 状态
├── models.py            # decentralized Actor + centralized Critic
├── rollout.py           # 执行对齐 transition、buffer、GAE
├── trainer.py           # PPO update 与诊断指标
├── checkpoint.py        # 保存、加载和兼容检查
├── parallel_train.py    # 同步多 SUMO 采样
├── evaluate_paired.py   # 复用统一评价口径
├── test_*.py            # 单元与回归测试
└── 说明文档.md
```

为了不让重构本身破坏 IPPO，MAPPO v1 可以复制少量已经验证的纯特征/奖励公式，但不要复制训练脚本和全局状态框架。等 MAPPO smoke 通过后，再考虑把真正相同的纯函数抽到 `algorithms/rl_common/`，并用 golden fixture 验证 IPPO 提取前后输出逐元素一致。

## 6. MAPPO-v1 最小设计

### 6.1 第一轮只验证一个变量

MAPPO-v1 应保持以下内容与 IPPO v8 一致：

- 本地 Actor observation；
- 11 维候选相位语义；
- ETA `0–15 s` / `15–30 s` 双桶；
- action mask、最小绿灯和最大绿灯；
- 15 s 动作周期；
- v5a 固定物理尺度奖励；
- 执行对齐 transition；
- 同步多 SUMO on-policy 采样；
- PPO 超参数和更新批量；
- 训练/评估场景与 seed；
- 六项评价和未完成车辆诊断。

唯一核心变化：

```text
IPPO Critic:  V_i(o_i)
MAPPO Critic: V_i(s_global, agent_i)
Actor:        π_i(a_i | o_i) 仍然只看本地信息
```

这符合 centralized training, decentralized execution（CTDE）：训练时 Critic 可见全局状态，部署时 Actor 不依赖全局通信。

### 6.2 集中式 Critic 推荐起点

不要第一版就加入图注意力、RNN、通信消息和复杂邻接编码。推荐：

1. 用共享 encoder 把 20 个路口的本地 observation 编成固定维向量；
2. 对全部 agent embedding 做 masked mean/global pooling；
3. 将 `local_embedding_i + global_pool + agent_identity_i` 输入共享 value head；
4. 输出每个 agent 对应的 `V_i`；
5. 每个 agent 仍使用自己的局部 reward 和 GAE。

这种设计比把 `20 × 132` 直接拼成大向量更容易处理归一化，也比 Transformer Critic 更适合作为“MAPPO 是否有效”的第一条基线。

第二阶段再消融：

- global mean pool vs 一跳邻居 pool；
- MLP centralized critic vs attention critic；
- 局部 reward vs 区域 reward；
- feed-forward vs recurrent critic。

### 6.3 不要从 ep160 warm start 冒充 vanilla MAPPO

可以用 IPPO ep160 Actor 权重做接口 smoke 或迁移学习实验，但必须单独命名，例如 `MAPPO-IPPO-init`。正式 vanilla MAPPO 与 IPPO 对比应：

- 都从随机初始化开始；
- 使用相同训练 seed、训练 episode 和 worker 数；
- 使用相同 Actor 结构与初始化；
- 只让 Critic 信息范围不同。

否则无法判断收益来自 centralized critic，还是来自 IPPO 已训练好的 Actor。

## 7. 建议实验顺序

### E0：静态与单元测试

目标：不启动长训练就证明数据链正确。

- 全局 critic state 形状固定；
- 路口顺序与 checkpoint 一致；
- 缺失路口/补齐 mask 不泄漏非法值；
- Actor 在相同输入、权重和 RNG 下与 IPPO 输出一致；
- centralized Critic 对其他路口状态变化有响应；
- Actor 对其他路口状态变化无响应；
- terminal 与 time-limit truncated bootstrap 正确；
- 单/多相位路口 action mask 始终合法。

### E1：4 路口 smoke

- 4 workers；
- 4–16 episodes；
- 120 s；
- 检查 samples、pending_dropped、policy generation；
- 记录 Actor/Critic loss、entropy、KL、clip fraction、explained variance、grad norm；
- 确认参数变化带来动作分布变化。

这一阶段只判断训练链是否通，不根据交通均值宣称 MAPPO 优于 IPPO。

### E2：20 路口短训配对消融

建议至少三组独立训练 seed：

```text
IPPO-v8-from-scratch
MAPPO-v1-from-scratch
```

要求两者共享：

- 相同 episode 数；
- 相同每批 episode 数；
- 相同 worker 数；
- 相同 Actor、reward、action interval；
- 相同评估 seed；
- deterministic 评估。

先跑 16/32 episodes。若 MAPPO Critic explained variance 更好，但交通指标没变，继续检查 Actor advantage 是否因此改变；不要直接把训练拉到 ep200。

### E3：完整训练

只有满足以下条件才进入完整训练：

- 无相位锁死或单一动作偏置；
- requested/applied 生效率正常；
- MAPPO 相比随机初始化有稳定改善；
- 至少三组短训不是单 pair 偶然；
- Critic explained variance 不长期为负；
- 多数配对场景 waiting/timeLoss 方向一致。

完整训练保存 32/64/96/128/160 等更新后的独立策略，不要评估没有发生 PPO 更新的相邻 episode。

### E4：泛化与强基线

最终覆盖：

- morning_peak / off_peak / evening_peak；
- 需求倍率或已有扰动场景；
- 至少 10 个配对留出 seed；
- fixed、SOTL、IPPO、MAPPO；
- MaxPressure 在实现并通过同一评价管线后加入。

报告均值、标准差、中位数、最差值和配对置信区间。不要只报最好 checkpoint 的单个平均数。

## 8. 诊断因果链

每次大 PPO 更新都应能回答：

```text
advantage 有信号
  → grad norm 非零
  → Actor/Critic 参数发生变化
  → 固定探针状态上的策略概率变化
  → requested action 变化
  → applied phase 实际变化
  → 队列、等待和吞吐变化
```

推荐保留固定 probe observations，在每个 checkpoint 上记录：

- Actor 参数相对变化；
- Critic 参数相对变化；
- probe KL、entropy、argmax 变化率；
- advantage 原始均值/标准差/绝对值；
- explained variance；
- requested action 变化率；
- 被最小绿灯/黄灯/全红约束比例；
- 最终生效比例与平均延迟；
- 每路口有效切相率和相位饥饿时间。

MAPPO 额外记录：

- centralized Critic 对全局状态的敏感度；
- global pooling 的数值范围；
- 各 agent value/return 尺度；
- Critic 是否只学会拥堵总量而忽略 agent identity；
- centralized value 改善是否真正降低 Actor advantage 方差。

## 9. 奖励函数不要一开始重写

IPPO v8 当前奖励：

```text
r = clip(-0.60D + 0.20F_safe - 0.15B + 0.05H, -3, 1)
D = 0.45L + 0.40S + 0.15Qmax
```

- `L`：动作窗口内 timeLoss 增量/时间/进口容量；
- `S`：停车车辆秒/时间/进口容量；
- `Qmax`：最大进口车道停车密度的时间均值；
- `F_safe`：按出口回溢风险折减的安全过线量；
- `B`：出口高占有率和 blocked crossings；
- `H`：窗口首尾 waiting 改善。

已知 `B` 和 `H` 经常接近 0，主要学习信号来自 `D` 和 `F_safe`。这值得后续做单因素诊断，但不应与 centralized critic 同时修改。

MAPPO-v1 先完全复用该奖励。只有证明 MAPPO 数据链健康后，才单独做 `B/H` 有效性消融。

## 10. 评价口径必须固定

六项正式指标：

1. 已完成车辆平均行程时间；
2. 同一批已完成车辆平均等待时间；
3. 1 s 进口车道平均排队长度；
4. 吞吐量；
5. 纯算法 `step()` 平均延迟；
6. 燃油车辆 L/100 km。

燃油只统计 gasoline、diesel、hybrid。电动车、自行车和电动自行车不进入燃油分子或里程分母。

TripInfo 用于 episode 后处理，不用于实时策略输入。还要同时报告：

- completed/unfinished 数量与 completion rate；
- unfinished waiting/timeLoss；
- 全部已出发车辆累计 waiting/timeLoss；
- 末端 active、remaining、halting、waiting 和 mean speed。

否则可能出现“已完成车辆很好看，但大量车辆被留在路网”的伪改善。

## 11. SUMO 边界和已知警告

MAPPO 不需要修改 SUMO 上游源码、路网、routes、OD、`tlLogic` 或 TraCI 控制权。

现有唯一相关仿真改动是 session 配置生成 TripInfo，并启用：

```xml
<tripinfo-output.write-unfinished value="true"/>
```

常见日志：

```text
Retrying in 1 seconds
```

通常是算法客户端等待新 SUMO/协议 session 就绪；持续重试才需要检查服务、端口和 session 状态。

```text
Warning: Missing green phase in tlLogic '891', program 'demo_19_off_peak' for tl-index 7
```

这是路口 891 的信号数据问题。所有方法使用同一网络时仍可做配对比较，但 link index 7 的能力可能受限，应由 SUMO/数据负责人确认。

`emergency braking` 是 SUMO 跟驰警告，不等于 PPO 崩溃；应作为安全诊断统计。

## 12. 训练资源经验

- 当前小型 IPPO MLP 的主要瓶颈是 SUMO CPU，不是 GPU；
- 8 workers 在 4090 服务器上比 4 workers 更快，但收益取决于物理 CPU 核和 SUMO 稳定性；
- 不要让 worker 数超过可用 CPU 后仍假设线性加速；
- MAPPO centralized Critic 比 IPPO 稍重，但在 20 agents 下通常仍不会让 GPU 成为主瓶颈；
- 优先测每批墙钟、CPU 利用率、worker 失败率和 learner 占比，再决定是否增加并行数。

## 13. 不要重复的错误

1. 不要从随机策略直接跑 200 episodes 后才第一次评估；
2. 不要只看 entropy/KL 就断言“训练量不够”；
3. 不要把 requested action 当成 applied phase；
4. 不要让共享 Actor 只理解动作下标；
5. 不要让 random 基线从非法动作空间采样；
6. 不要用不同 seed 或不同 route 比较算法；
7. 不要只载入模型权重却把 optimizer/normalization 重置后称为连续续训；
8. 不要用 completed-trip 均值掩盖未完成车辆；
9. 不要把 missing metric 写成 0；
10. 不要同时改 Actor、Critic、reward、observation 和训练量；
11. 不要把 IPPO ep160 warm start 的 MAPPO 写成 vanilla MAPPO；
12. 不要为了少几行代码删除能防止无效实验的检查；
13. 不要复制 IPPO 1800 行单体控制器后继续堆功能；
14. 不要修改 SUMO 代码来迁就算法，除非用户明确授权且仿真负责人确认。

## 14. MAPPO 首轮验收标准

代码健康：

- 新测试全部通过，原 84 项测试不退化；
- Actor decentralized，推理不依赖其他路口状态；
- Critic centralized，训练时确实使用全局状态；
- action mask、pending transition、truncation 和 checkpoint 兼容有测试；
- 并行 worker 使用同一 policy generation；
- 无 `pending_dropped` 异常增长。

实验健康：

- 4 路口 smoke 能完成至少一次 PPO 更新；
- 20 路口短训相对初始化策略方向改善；
- 三组独立短训中多数 waiting/timeLoss 同向；
- Critic explained variance 不长期为负；
- requested action 能实际转化为相位变化；
- 无全网锁相、单一动作偏置或相位饥饿。

强基线目标：

- 先稳定超过 fixed；
- 再与相同训练量的 IPPO v8-from-scratch 配对比较；
- MaxPressure 实现并经相同六指标管线复评后，才判断 MAPPO 是否达到强基线；
- 最终覆盖多时段、扰动和至少 10 个留出 seed。

## 15. 开始 MAPPO 前的检查清单

- [ ] 新建独立 MAPPO worktree/branch；
- [ ] 确认基线提交包含 `a6777e2` 和 `e5d6f7c`；
- [ ] 运行现有 84 项测试；
- [ ] 保存 IPPO ep160 SHA-256；
- [ ] 设计 MAPPO-v1 centralized-state schema；
- [ ] 冻结 Actor、reward、action 和 evaluation 定义；
- [ ] 先写 Actor 隔离与 Critic 全局敏感度测试；
- [ ] 实现执行对齐 rollout 与 time-limit bootstrap；
- [ ] 实现同步 worker generation 校验；
- [ ] 先跑 4 路口 smoke；
- [ ] 再跑 20 路口三组短训配对；
- [ ] 短训健康后才启动完整训练；
- [ ] 未经用户明确要求，不 commit/push GitHub；
- [ ] 不修改 SUMO 路网、routes、tlLogic 或上游源码。

## 16. 最终一句话

IPPO 这轮最重要的经验不是“多训练就会变好”，而是：**先保证状态和动作有交通语义、动作与执行对齐、on-policy 数据真实一致、评价口径不作弊，再讨论 centralized critic 是否比 independent critic 更强。MAPPO-v1 应只改变 Critic 的信息范围，用配对消融证明收益。**
