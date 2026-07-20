# 管控算法

算法组独立维护，全部通过 HTTP/JSON 协议（v2.0）与仿真端对接，

算法代码不需要导入 SUMO 或 TraCI。跨进程部署时实现 docs/algorithm_interface.md 中的三个 HTTP 接口；同机训练时可复制 local_policy_example.py，实现同名的 initialize/step/finish Python 函数。 两种方式收发完全相同的字典结构，本地方式不经过网络和 JSON 编解码。 固定配时由仿真端直接执行，不经过算法服务。当前接口为不兼容旧版的协议 2.0，算法响应 必须同时提供 actions.signals 和 actions.vehicles 两个对象。

只消费状态、不返回控制动作的 AI 训练入口见 ai_observer_example.py 和 docs/local_transport_ai_observer.md。

## 目录结构

```
algorithms/
├── README.md                  # 本文件
├── __init__.py
├── evaluation/                # 指标计算（与仿真端解耦）
│   ├── __init__.py
│   ├── metrics.py             # 离线计算：基于 SUMO tripinfo.xml
│   ├── collector.py           # 在线采集：每步采样，/finish 汇总
│   └── compare.py             # 一键对比脚本
├── max_pressure/              # 8001 — 排队理论自适应
│   ├── server.py              # FastAPI 服务入口 + 指标集成
│   ├── controller.py          # MaxPressure 算法主体
│   ├── requirements.txt
│   └── __init__.py
├── sotl/                      # 8002 — 规则驱动自适应
│   ├── server.py
│   ├── controller.py
│   ├── requirements.txt
│   └── __init__.py
├── ippo/                      # 8003 — 独立多智能体强化学习
│   ├── server.py
│   ├── controller.py
│   ├── train.py               # 训练入口
│   ├── requirements.txt
│   └── __init__.py
└── models/                    # RL 模型权重
    └── ippo_demo2.zip
```

## 算法层级

| 层级 | 算法 | 端口 | 类型 | 状态 |
|------|------|------|------|------|
| L1 | Fixed-Time（固定配时） | 仿真端内置 | 静态基线 | ✅ 仿真端直接执行 |
| L2 | SOTL | 8002 | 规则驱动自适应 | ✅ |
| L3 | MaxPressure | 8001 | 排队理论自适应 | ✅ |
| L4 | SCATS-like | — | 传统多路口协调 | ⬜ 等待多路口车流 |
| L5 | IPPO | 8003 | 独立多智能体 RL | ✅ 单路口训练完成 |
| L6 | 自研 RL | — | MARL 多路口协同 | ⬜ 等待多路口车流 |

### 各算法原理

**Fixed-Time（固定配时）**：按预设相位顺序和固定时长循环切换。
仿真端直接执行，不经过算法服务。作为下界（lower bound）基线。

**SOTL（Self-Organizing Traffic Light）**：规则驱动的自适应控制。
如果当前相位绿灯期间有新来车且排队超过阈值，延长绿灯；否则切换相位。
简单、可解释、无训练成本。

**MaxPressure**：基于排队压力的自适应控制。
每步选择"压力最大"的相位——压力 = 进口道排队长度 − 出口道排队长度。
理论上能最大化路网吞吐量，是强 baseline。

**IPPO（Independent PPO）**：每个路口独立的 PPO 智能体。
State：各进口道排队长度 + 等待时间 + 当前相位。
Action：离散相位选择。
Reward：−等待时间（差分式 `diff_waiting_time`）。
训练用 SB3 PPO，推理时一次前向传播 < 1ms。

**自研 RL**：计划采用 QMIX / MAPPO 做多路口协同值分解，
待多路口车流数据（demo_3～demo_20）到位后启动。

---

## 6 大评估指标

指标全部通过 HTTP 协议实时采集，由 `evaluation/collector.py` 的
`HttpMetricsCollector` 在每个 `/step` 和 `/finish` 中计算。

### 指标定义与计算

| # | 指标 | 英文 | 计算公式 | 数据来源 |
|---|------|------|---------|---------|
| 1 | 平均行程时间 | avg_travel_time_s | Σ(车辆消失时间 − 首次出现时间) ÷ 到达车辆数 | `/step` vehicles 跟踪：首次出现 → 从 vehicles 消失 |
| 2 | 平均等待时间 | avg_waiting_time_s | Σ(到达车辆累计等待) ÷ 到达车辆数 | `/step` vehicles[vid].traffic.accumulated_waiting_time_s |
| 3 | 平均排队长度 | avg_queue_length_veh | Σ(各步 halting_count) ÷ 采样步数 | `/step` intersections[].lanes[].halting_count |
| 4 | 有效吞吐量 | throughput_veh_per_h | arrived_vehicles ÷ simulation_time × 3600 | `/finish` arrived_vehicles + simulation_time |
| 5 | 平均决策耗时 | avg_decision_latency_ms | Σ(compute_actions 耗时) ÷ 决策步数 | server.py 内 `time.perf_counter()` 打点 |
| 6 | 单位车公里油耗 | fuel_intensity_L_per_100km | (fuel_ml ÷ 1000) ÷ (total_distance_m ÷ 100000) | `/finish` fuel_consumed_ml + 车辆里程累计 |

### 辅助统计

| 指标 | 说明 |
|------|------|
| departed | 已出发车辆总数 |
| arrived | 已到达车辆总数 |
| completion_rate_pct | 到达率 = arrived ÷ planned × 100% |

### 采集流程

```
/initialize → 重置 collector 状态
    ↓
/step（循环）→ 每步：
  1. 采样 halting_count → 排队长度
  2. 跟踪 vehicles 增删 → 检测新车/到达车
  3. 更新 active vehicles 的最新等待/油耗/里程
  4. 打点计算耗时 → 决策延迟
    ↓
/finish → 汇总 6 大指标，输出到日志 + /stats 接口
```

### 离线计算（备用）

`evaluation/metrics.py` 提供 `compute_from_tripinfo()`，
从 SUMO 的 `tripinfo.xml` + `emission.xml` 独立计算相同 6 大指标，
不依赖 HTTP 协议，用于事后分析。

### 查看指标

```bash
# 查询最近一次仿真的 6 大指标（任一算法服务）
curl http://localhost:8001/stats

# 返回示例：
# {
#   "avg_travel_time_s": 183.6,
#   "avg_waiting_time_s": 1.31,
#   "avg_queue_length_veh": 0.05,
#   "throughput_veh_per_h": 1346.5,
#   "avg_decision_latency_ms": 0.023,
#   "fuel_intensity_L_per_100km": 0.01,
#   "departed": 2761,
#   "arrived": 2693
# }
```

### 一键对比

```bash
# 对比多个算法的 tripinfo 输出
python3 -m evaluation.compare \
  --tripinfo-fixed     tripinfo_fixed.xml \
  --tripinfo-sotl      tripinfo_sotl.xml \
  --tripinfo-maxpressure tripinfo_mp.xml \
  --tripinfo-ippo      tripinfo_ippo.xml \
  --eval-duration 3600 \
  --markdown
```

---

## HTTP 接口

每个算法服务实现 5 个接口，协议见 `../docs/algorithm_interface.md`：

| 接口 | 方法 | 调用频次 | 说明 |
|------|------|---------|------|
| `/initialize` | POST | 每轮 1 次 | 接收路口拓扑、相位、车道、车型 |
| `/step` | POST | 每决策周期 1 次 | 接收实时状态，返回信号+车辆动作 |
| `/finish` | POST | 每轮 1 次 | 接收汇总数据，输出指标 |
| `/stats` | GET | 任意 | 查询最近仿真的 6 大指标 |
| `/health` | GET | 任意 | 健康检查 |

请求/响应格式详见 `../docs/algorithm_interface.md`。

---

## 启动命令

```bash
# 确保在 algorithms/ 目录
cd /home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms

# MaxPressure（8001）
python3 -m uvicorn max_pressure.server:app --host 0.0.0.0 --port 8001

# SOTL（8002）
python3 -m uvicorn sotl.server:app --host 0.0.0.0 --port 8002

# IPPO — 随机探索模式（8003）
IPPO_MODE=random ~/anaconda3/envs/v2x-ai-py310/bin/python3 \
  -m uvicorn ippo.server:app --host 0.0.0.0 --port 8003

# IPPO — 训练模式
export SUMO_HOME=/usr/share/sumo
~/anaconda3/envs/v2x-ai-py310/bin/python3 -m ippo.train \
  --episodes 50 --save models/ippo_demo2

# IPPO — 推理模式
IPPO_MODE=model IPPO_MODEL_PATH=models/ippo_demo2.zip \
  ~/anaconda3/envs/v2x-ai-py310/bin/python3 \
  -m uvicorn ippo.server:app --port 8003
```

IPPO 使用独立的 conda 环境 `v2x-ai-py310`（含 torch + stable-baselines3）。
MaxPressure / SOTL 用系统 Python 即可。

---

## 当前阻塞

多路口车流数据（`demo_3` ~ `demo_20`）。以下工作依赖此数据：

- SCATS 绿波协调
- IPPO 多路口参数共享验证
- 自研 MARL（QMIX/MAPPO）多路口协同训练

---

## 对接关系

```
仿真端（SUMO + TraCI）
    │  HTTP/JSON v2.0
    ├──→ :8001 MaxPressure
    ├──→ :8002 SOTL
    ├──→ :8003 IPPO
    └──→ 内置 Fixed-Time（不经过 HTTP）
```
