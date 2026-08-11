## 更新记录

> 本板块记录 `algorithms/` 目录的最新变更，新条目放在最上方。

### 2026-08-05
- **MAPPO-v2 优化：M0 审计 + M1 三臂训练基础设施**
  - 新增 M0 审计工具：决策延迟 p95（`evaluation`）、单侧 UCB95、10-seed IPPO 基线冻结入口、vanilla MAPPO 诊断（advantage 分位数、TD target 重复率、per-agent 相关性、梯度余弦）。
  - 新增 `cooperative_m1_v1` 模型版本：`AgentConditionedCritic`、per-agent local reward、mean-of-values 团队价值、共享初始化工件（`mappo/runs/mappo_v2/m0/mappo_v2_shared_init.pt`）。
  - 新增邻域 credit 组件：per-owner local GAE、neighbor/team 组件、`mix_advantages`，以及 M1 配置/checkpoint 元数据校验。
  - 生成 20 路口邻接矩阵：`mappo/runs/mappo_v2/m0/intersection_adjacency_{directed,m1_symmetric}.json`。
  - 训练入口接线中：`mappo/train.py` 支持 `--model-version cooperative_m1_v1`、`--m1-arm`、`--m1-target-mode`、`--m1-adjacency` 等参数；M1-0 走 scalar team GAE，M1-A/B 走 per-agent 组件混合。
  - IPPO v8 10-seed 基线评估后台运行中，完成后将冻结基线并生成正式 pre-registration manifest。

---

# 算法目录

本目录包含 CityPulse V2X Sim 的交通控制算法、训练入口和统一评价模块。所有算法通过项目协议 2.0 获取路口、车道和车辆状态；SUMO/TraCI 始终由 `simulation/` 独占，算法不直接操作 TraCI。

如果你第一次接入算法，先阅读：

- [算法接口协议 2.0](../docs/algorithm_interface.md)：完整请求、响应和字段口径；
- [本地算法与 AI 观察者](../docs/local_transport_ai_observer.md)：同进程调用方式；
- [IPPO v8 算法与实验说明](ippo/说明文档.md)：IPPO 的状态、奖励、并行训练、评价和实验结果；
- [CoSLight-P0 讲解](coslight/coslight讲解.md)：Transformer 协同控制器的结构与训练逻辑。

## 1. 当前可用模块

| 模块 | 类型 | 控制对象 | 是否训练 | 当前定位 |
|---|---|---|---|---|
| fixed | SUMO 官方固定配时 | 信号灯 | 否 | 仿真端基线，不经过 `algorithms/` |
| [`sotl`](sotl/) | 自组织规则控制 | 信号灯 | 否 | 轻量自适应基线，支持本地模块和 HTTP 服务 |
| [`ippo`](ippo/) | 参数共享独立 PPO | 信号灯 | 是 | 当前纯 RL 基线；发布 v8 ep160 模型 |
| [`coslight`](coslight/) | Transformer + 集中式 Critic | 信号灯、车辆速度/车道建议 | 是 | 车路协同研究算法，当前为 P0 实现 |
| [`evaluation`](evaluation/) | 六指标采集与计算 | 不控制 | 否 | 为算法提供统一评价口径 |
| [`local_policy_example.py`](local_policy_example.py) | 最小协议示例 | 信号灯、合法车道示例 | 否 | 新算法模板，不作为性能基线 |
| [`ai_observer_example.py`](ai_observer_example.py) | 高频只读观察器 | 不控制 | 否 | 展示异步消费实时帧和跳帧检测 |

项目文档把 MaxPressure 列为目标基线，但当前 `algorithms/` 目录没有可独立运行的 MaxPressure 模块。提交实验报告前，应先补齐实现并使用与其他算法相同的六指标管线复评，不能混用旧脚本或不同统计口径的数据。

## 2. 运行边界

```text
SUMO / TraCI（simulation 独占）
        │
        │ Protocol 2.0 字典或 HTTP/JSON
        ▼
initialize → step → finish
        │
        ├─ actions.signals：目标相位
        └─ actions.vehicles：目标速度或目标车道
        │
        ▼
仿真端校验动作，并执行最小绿灯、黄灯和全红安全过渡
```

算法必须遵守以下边界：

- 不导入 SUMO 或 TraCI；
- 不修改 `.net.xml`、routes、OD、`tlLogic` 或仿真安全控制器；
- `step()` 响应必须同时包含 `actions.signals` 和 `actions.vehicles`；
- 信号灯动作只请求 `target_phase`，实际切换时机由仿真端约束；
- 车辆换道必须遵守 `edge_lanes[].allowed_vehicle_type_ids`；
- 内部道路（`road_id` 以 `:` 开头）不返回换道动作。

## 3. 环境准备

从仓库根目录执行：

```bash
export SUMO_HOME=/usr/share/sumo
python3 -c "import torch, numpy; print(torch.__version__, numpy.__version__)"
sumo --version
```

完整依赖与 SUMO 安装方式见 [环境配置](../docs/setup.md)。SOTL 的 HTTP 服务还需要：

```bash
python3 -m pip install -r algorithms/sotl/requirements.txt
```

## 4. 快速运行

### 4.1 fixed 基线

fixed 由 SUMO 直接执行，不加载算法模块：

```bash
python3 -m simulation.sumo.engine.run \
  --mode fixed \
  --intersection demo_1 demo_2 demo_3 demo_4 \
  --period off_peak \
  --duration 300 \
  --seed 62001
```

### 4.2 SOTL 本地模式

```bash
python3 -m simulation.sumo.engine.run \
  --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.sotl \
  --intersection demo_1 demo_2 demo_3 demo_4 \
  --period off_peak \
  --duration 300 \
  --seed 62001
```

SOTL 根据每个相位服务车道的 `vehicle_count` 选择车辆最多的相位，并使用动态最小保持时间。黄灯、全红和项目级最小绿灯仍由仿真端执行。

### 4.3 SOTL HTTP 模式

先启动服务：

```bash
PYTHONPATH=algorithms python3 -m uvicorn sotl.server:app \
  --host 0.0.0.0 \
  --port 8002
```

再启动仿真：

```bash
python3 -m simulation.sumo.engine.run \
  --mode algorithm \
  --algorithm-transport http \
  --algorithm-endpoint http://127.0.0.1:8002 \
  --intersection demo_1 demo_2 demo_3 demo_4 \
  --period off_peak \
  --duration 300 \
  --seed 62001
```

完成一轮后可访问 `GET /stats` 获取最近一次六指标结果，`GET /health` 查看服务状态。

### 4.4 IPPO ep160 推理

仓库只发布当前选出的 ep160：

```text
algorithms/ippo/models/ippo_v8_20tls_ep160.pt
```

推理时必须显式设置 `IPPO_MODE=model` 和模型路径；不设置时默认是 random 模式：

```bash
IPPO_MODE=model \
IPPO_MODEL_PATH=algorithms/ippo/models/ippo_v8_20tls_ep160.pt \
python3 -m simulation.sumo.engine.run \
  --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.ippo \
  --intersection \
    demo_1 demo_2 demo_3 demo_4 demo_5 \
    demo_6 demo_7 demo_8 demo_9 demo_10 \
    demo_11 demo_12 demo_13 demo_14 demo_15 \
    demo_16 demo_17 demo_18 demo_19 demo_20 \
  --period off_peak \
  --duration 300 \
  --seed 62001
```

发布模型的 checkpoint 固定记录了 `demo_1` 至 `demo_20` 的路口集合和顺序，不能直接用于 4 路口或其他路口组合。正式复现实验应使用上面的 20 路口配置和第 7 节的配对评价入口；4 路口健康检查使用第 6.1 节重新创建的小模型。

模型 SHA-256：

```text
5656e351dc66aa7ffebd50d6a01109aff6a71bca393976eb45e9dca70c7ef107
```

### 4.5 CoSLight-P0 本地模式

```bash
python3 -m simulation.sumo.engine.run \
  --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.coslight \
  --intersection demo_1 demo_2 demo_3 demo_4 \
  --period off_peak \
  --duration 300 \
  --seed 62001
```

当前模块默认进入在线采样/训练模式，不会自动加载目录中的参考权重。`actor_207.pt`、`critic_207.pt` 和 `vnorm_207.pt` 是历史参考产物；不要把上面的命令误当作冻结模型评估。冻结推理需要在路口元数据初始化后显式调用 `checkpoint_load(path)`，并先确认模型结构与路口数量兼容。

## 5. 新算法接入

### 5.1 本地模块

复制 [`local_policy_example.py`](local_policy_example.py)，实现：

```python
def initialize(payload: dict) -> dict: ...
def step(payload: dict) -> dict: ...
def finish(payload: dict) -> object: ...
```

然后运行：

```bash
python3 -m simulation.sumo.engine.run \
  --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.your_algorithm \
  --intersection demo_1 \
  --period off_peak \
  --duration 120 \
  --seed 42
```

本地模式与 HTTP 使用完全相同的字典结构，但省去网络与 JSON 编解码，适合训练和可信模块部署。

### 5.2 HTTP 服务

HTTP 算法必须提供：

| 接口 | 调用次数 | 作用 |
|---|---:|---|
| `POST /initialize` | 每轮一次 | 接收静态路网、相位、车道、车型和控制能力 |
| `POST /step` | 每个决策周期一次 | 接收实时状态并返回动作 |
| `POST /finish` | 每轮一次 | 接收结束原因和汇总指标 |

协议错误、超时、episode/step 回显不一致或非法动作会使本轮仿真失败。完整 JSON 示例见 [算法接口协议](../docs/algorithm_interface.md)。

### 5.3 只读 AI 观察者

只需要状态、不需要控制权时，实现：

```python
def initialize(metadata: dict) -> None: ...
def on_frame(frame: dict) -> None: ...
def finish(summary: dict) -> None: ...
```

示例：

```bash
python3 -m simulation.sumo.engine.run \
  --mode fixed \
  --ai-observer-module algorithms.ai_observer_example \
  --ai-frame-interval 1.0 \
  --intersection demo_1 \
  --period off_peak \
  --duration 120
```

观察者异步消费容量为 1 的最新帧队列。推理慢时旧帧会被覆盖，`frame_id` 缺口可用于检测跳帧，SUMO 不等待观察者完成每一帧。

## 6. IPPO 训练

IPPO v8 使用多个独立 SUMO 进程同步采样、中心 learner 统一更新。所有 worker 必须使用同一 policy generation；任何 worker 失败时整批数据作废，保证 PPO 的 on-policy 条件。

### 6.1 4 路口 smoke

```bash
python3 -m algorithms.ippo.parallel_train \
  --episodes 4 \
  --duration 120 \
  --workers 4 \
  --intersections 4 \
  --period off_peak \
  --seed 90000 \
  --checkpoint-every 4 \
  --save /tmp/ippo_v8_4tls_smoke.pt
```

### 6.2 20 路口训练

```bash
python3 -m algorithms.ippo.parallel_train \
  --episodes 160 \
  --duration 300 \
  --workers 8 \
  --intersections 20 \
  --period off_peak \
  --seed 88300 \
  --effective-demand on \
  --checkpoint-every 8 \
  --save algorithms/ippo/runs/my_v8_run/ippo_v8_20tls_ep160.pt
```

`--episodes` 表示本次新增 episode 数。续训应同时传入 `--resume`；程序会恢复 Actor、Critic、optimizer、episode 计数和训练 seed 范围，并拒绝不兼容的 checkpoint。

训练瓶颈主要是 SUMO CPU 进程。增加 worker 前先检查物理 CPU 核、内存和并发 SUMO 稳定性；当前小型 MLP 通常不会因换用更强 GPU 获得同等比例加速。

## 7. 配对评价

正式对比使用相同场景、时长和 seed 的 paired evaluation：

```bash
python3 -m algorithms.ippo.evaluate_paired \
  --methods fixed random model \
  --episodes 4 \
  --workers 8 \
  --duration 300 \
  --intersections 20 \
  --period off_peak \
  --seed 62000 \
  --checkpoint algorithms/ippo/models/ippo_v8_20tls_ep160.pt \
  --output /tmp/ippo_v8_ep160_paired.json
```

`--seed 62000 --episodes 4` 生成 `62001..62004`。评价器会拒绝与 checkpoint 训练 seed 范围重叠的评估配置。

### 7.1 六项正式指标

| 指标 | 趋势 | 数据来源 |
|---|---|---|
| 平均行程时间 | 越低越好 | 已完成车辆 TripInfo `duration` |
| 平均等待时间 | 越低越好 | 同一批已完成车辆 TripInfo `waitingTime` |
| 平均排队长度 | 越低越好 | 1 s 实时进口车道 `halting_count` 时间平均 |
| 路网吞吐量 | 越高越好 | `arrived / simulation_time × 3600` |
| 平均决策延迟 | 越低越好 | `step()` 内纯算法计算耗时 |
| 燃油强度 | 越低越好 | 燃油车辆累计燃油/同一车辆集合累计里程 |

燃油强度只统计 gasoline、diesel 和 hybrid。纯电动车、自行车、电动自行车等不进入燃油分子或里程分母。数据缺失时返回 `N/A`，不会以 0 参与平均。

300 s episode 结束时仍可能有车辆留在路网。除六项正式指标外，应同时检查 completion rate、未完成车辆 waiting/timeLoss、末端 active/halting 和残余队列，避免 completed-trip 截尾偏差。

### 7.2 当前 IPPO ep160 结果

统一配置：20 路口、off_peak、300 s、deterministic 推理、seeds `62001..62004`。

| 方法 | 行程时间↓ | 等待时间↓ | 队列↓ | 路网吞吐量↑ | 决策延迟↓ | 燃油强度↓ |
|---|---:|---:|---:|---:|---:|---:|
| IPPO v8 ep160 | 100.53 s | 8.43 s | 0.08 | 906 veh/h | 12.53 ms | 13.82 L/100km |
| fixed | 103.04 s | 16.22 s | 0.22 | 744 veh/h | N/A | 14.70 L/100km |

ep160 相对 fixed：行程时间 `-2.4%`、等待时间 `-48.0%`、队列 `-63.6%`、路网吞吐量 `+21.8%`、燃油强度 `-5.9%`。这些数字是当前 off_peak 验证集结果，不代表多时段、扰动或 MaxPressure 对比结论。完整训练谱系和 ep160/168/176/184/192 选模过程见 [IPPO 说明文档](ippo/说明文档.md)。

## 8. 文件职责

```text
algorithms/
├── README.md                  # 本文档：算法入口和统一使用方式
├── local_policy_example.py    # Protocol 2.0 本地算法模板
├── ai_observer_example.py     # 高频只读观察器模板
├── ippo/                      # IPPO v8、并行训练、配对评价、测试、发布模型
├── coslight/                  # CoSLight-P0、参考权重和算法说明
├── sotl/                      # SOTL 本地控制器与 FastAPI 服务
└── evaluation/                # 六指标采集、TripInfo 回填和计算
```

| 文件 | 作用 |
|---|---|
| [`ippo/controller.py`](ippo/controller.py) | IPPO 状态、相位语义、网络、动作约束、奖励、GAE/PPO 和 checkpoint |
| [`ippo/parallel_train.py`](ippo/parallel_train.py) | 多 SUMO 同步采样和中心更新 |
| [`ippo/evaluate_paired.py`](ippo/evaluate_paired.py) | fixed/random/model 的配对评价 |
| [`ippo/test_controller.py`](ippo/test_controller.py) | IPPO 与并行训练回归测试 |
| [`coslight/controller.py`](coslight/controller.py) | Transformer 控制器、PPO 和车辆速度/车道建议 |
| [`sotl/controller.py`](sotl/controller.py) | SOTL 规则控制器 |
| [`sotl/server.py`](sotl/server.py) | SOTL FastAPI/HTTP 服务 |
| [`evaluation/collector.py`](evaluation/collector.py) | 实时帧指标采集 |
| [`evaluation/metrics.py`](evaluation/metrics.py) | TripInfo 回填、六指标计算和燃油单位处理 |
| [`evaluation/runtime.py`](evaluation/runtime.py) | 单 episode 评价生命周期和 episode_id 保护 |

## 9. 测试与提交检查

```bash
python3 -m pytest -q \
  algorithms/ippo \
  algorithms/evaluation \
  tests/test_session_scenario.py

python3 -m compileall -q algorithms/ippo algorithms/evaluation algorithms/sotl
git diff --check
```

IPPO 训练产物默认不进入 Git：

```text
algorithms/ippo/runs/
algorithms/ippo/checkpoints/
algorithms/ippo/*.pt
```

仓库只保留经过统一评价选出的 `algorithms/ippo/models/ippo_v8_20tls_ep160.pt`。更换发布模型时，必须同步更新 SHA-256、验证 seed、实验表和说明文档。

## 10. 常见问题

### IPPO 为什么像随机策略？

`IPPO_MODE` 默认值是 `random`。冻结模型评估必须设置：

```bash
export IPPO_MODE=model
export IPPO_MODEL_PATH=algorithms/ippo/models/ippo_v8_20tls_ep160.pt
```

### checkpoint 为什么加载失败？

IPPO 会检查模型版本、路口顺序、状态/动作维度、动作周期、相位特征 schema、最大绿灯、ETA 开关和训练时段。旧版 raw `state_dict` 或配置不一致的模型会在启动 worker 前被拒绝。

### `Retrying in 1 seconds` 是训练错误吗？

通常是算法客户端等待新的 SUMO/协议服务就绪。只要随后 session 正常进入运行并以 `COMPLETED` 结束，就不是 PPO 失败；若持续重试，应检查服务进程、端口和 session 状态。

### 路口 891 的 warning 是什么？

```text
Warning: Missing green phase in tlLogic '891', program 'demo_19_off_peak' for tl-index 7
```

它表示该信号方案中受控 link index 7 没有任何绿灯相位，属于 SUMO 信号数据问题，不是 IPPO 代码异常。对照算法使用同一网络时仍可配对比较，但正式数据应由 SUMO/数据负责人修复或确认。

### emergency braking warning 是 PPO 崩溃吗？

不是。它是 SUMO 跟驰/车流警告。算法评价可以把急刹车作为安全诊断，但单条 warning 不代表训练更新失败。

### 为什么不直接访问 TripInfo 做训练？

TripInfo 在 episode 结束后用于精确评价已完成和未完成行程；实时控制、奖励和 ETA 需求仍读取 Protocol 2.0 状态。这样训练不依赖未来信息，也不要求算法接管 SUMO。

## 11. 贡献约定

新增或修改算法时：

1. 保持 `initialize/step/finish` 的 Protocol 2.0 响应完整；
2. 不绕过仿真端信号安全约束；
3. 为动作 mask、checkpoint 兼容、异常 episode 和评价缺失数据补测试；
4. 使用固定留出 seed 做 paired evaluation；
5. 同时报告六项正式指标和未完成车辆诊断；
6. 不提交 `runs/`、临时 checkpoint、日志或服务器绝对路径；
7. 文档中的命令必须从仓库根目录可直接执行。
