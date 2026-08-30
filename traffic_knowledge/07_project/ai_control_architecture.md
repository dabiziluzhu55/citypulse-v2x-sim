# CityPulse-Qwen 管控架构

本文说明 CityPulse-Qwen 的任务、接管机制和与现有控制链路的关系。**【规划功能】** 与 **【项目事实】** 必须分开阅读。

## 任务定义

**【规划功能】** CityPulse-Qwen 面向用户在前端表单中提交的扰动事件（施工占道、道路限速、大型活动开场/散场、交通事故），融合实时交通状态、事件信息、短时预测、道路拓扑、当前信号状态、相邻路口状态、RAG 交通知识和系统实际控制能力，对扰动影响区域做态势理解，并生成局部多路口协同信号管控方案。

它是 **事件触发的 AI 管控模块**，不是普通情况下全程工作的默认控制器。

核心问题：扰动发生后，局部通行能力或交通需求会变化，并可能沿拓扑向相邻路口传播。CityPulse-Qwen 应：

1. 判断扰动对当前交通状态的影响；
2. 判断拥堵可能向哪些相邻路口传播；
3. 读取短时预测，判断未来数十秒至数分钟趋势；
4. 确定需要协同控制的局部路口范围；
5. 生成区域协同信号管控方案；
6. 在后续决策周期根据执行反馈调整；
7. 事件影响消退后退出 AI 接管。

## 与现有算法的关系

**【项目事实】** 产品 `control_mode` 仍只有 `fixed`、`sotl`、`max_pressure`、`ippo`、`mappo`。用户在前端选择其中之一后，该算法独立运行，用于相同场景下的公平对比。CityPulse-Qwen **不是** 新的 `control_mode`，也 **不负责** 选择哪个算法。

**【规划功能】** 仅当用户创建或注入扰动，并主动选择“启用 AI 管控”时：

| 术语 | 含义 |
| --- | --- |
| baseline controller | 用户原先选择的 `control_mode` |
| AI takeover | AI 在规定时间和规定路口临时接管信号决策 |
| AI controlled intersections | 当前被 AI 接管的路口集合，必须是当前 preset 的子集 |
| AI control window | AI 实际接管的仿真时间区间 |

逻辑：

```
正常状态：baseline controller → SUMO
扰动 + AI 启用：
  baseline controller
  → 事件生效
  → AI 接管局部路口
  → CityPulse-Qwen 生成高层协同方案
  → schema / scope / phase / safety 校验
  → AI Plan Executor 在现有 decision_interval 内编译 Protocol 2.0 target_phase
  → SUMO Worker 经 SafePhaseController 执行
  → AI 周期反馈
  → AI 结束
  → 恢复 baseline controller
```

未被 AI 接管的路口继续运行原 `control_mode`。AI 故障、超时、解析失败或校验失败时，必须立即回退到 baseline controller，不能让信号停止工作。

## 事件触发

**【项目事实】** 当前可注入事件类型为 `lane_closure`、`speed_limit`、`accident`、`major_event_opening`、`major_event_closing`。启动时通过 `disturbance_targets` 提交，运行中通过 `POST /api/v1/simulations/{session_id}/events` 注入。规则检测卡片 `event_detection` 是只读候选，不是注入真值，不能单独触发接管。

**【规划功能】** AI takeover 的触发条件建议为：存在处于 `ACTIVE` 的注入事件，且用户显式启用 AI 管控。检测卡片可用于提示，不得自动接管。

## 空间范围

**【规划功能】** AI 管控范围必须是当前 `scenario_preset_id` 的路口子集；事件目标路口必须属于该预设。

| 预设 | 设计原则 |
| --- | --- |
| `east_dense`（4 路口） | 可对整个局部预设协同，因路口数已接近推荐邻域 |
| `west_dense`（3 路口） | 同上 |
| `xiongan_20`（20 路口） | 不默认接管全部 20 个路口；优先“事件路口 + 一跳或有限局部邻域” |

推荐同时控制约 3–6 个有直接交通关联的路口。该数字是设计原则，不是当前代码限制。

不同事件的推荐范围：

- `lane_closure` / `accident` / `speed_limit`：事件路口及其直接上下游。
- `major_event_opening`：活动区域主要流入方向和入口走廊。
- `major_event_closing`：活动区域主要离场方向和疏散走廊。

后续可根据流向和预测动态扩展 scope。

## 时间范围

必须区分三个时间概念，不得混用：

| 概念 | 含义 | 当前代码 |
| --- | --- | --- |
| simulation duration | 整个实验持续时间 | `duration_seconds` |
| event time window | 扰动 `start_seconds`–`end_seconds` | 事件 schema |
| AI control window | AI 实际接管时段 | **【规划功能】** 尚未实现 |

**【规划功能】** 推荐：

- AI start = event start
- AI end = min(event end + recovery_seconds, simulation end)
- `recovery_seconds` 第一版可配置
- 后续可在受控区域排队、速度或占用连续多个决策周期恢复后提前退出

事件结束不等于排队立即消失，因此需要恢复控制。`event duration ≠ AI control duration`。

## 决策周期

**【项目事实】** SUMO 默认步长 `step_length=0.1` s。已注册算法的 Backend 默认 `decision_interval=5.0` s。算法只输出 `target_phase`，黄灯和全红由 `SafePhaseController` 执行。

**【规划功能】** CityPulse-Qwen 不应每个 SUMO step 推理，也不应每 0.1 s 或 1 s 调用大模型。建议每 30–60 个仿真秒生成一次高层 AI plan。Executor 仍按现有约 5 s 决策周期选择 `target_phase`。不得把一次 plan 理解成锁死某个相位 30–60 s。`AI decision interval ≠ signal decision interval ≠ SUMO simulation step`。详见 `ai_plan_executor.md`、`ai_takeover_lifecycle.md`。

## 输入数据

**【项目事实】** 当前会话可提供：

- 实时快照：`SimulationSnapshot` 中的路口相位、阶段、车道 `vehicle_count` / `halting_count` / `mean_speed` / `waiting_time` / `occupancy` / `lane_has_green` / `current_allowed_speed_mps`、车辆轨迹和 `events`
- 预测：底层 NarrowNet-TDP 在 206 车道节点上预测约 60 秒 `vehicle_count`；Backend 聚合为路口级 `current_vehicle_count` / `predicted_vehicle_count` / `delta`。不可用时 `moving_average` fallback。不得写成 20 节点 STGCN。
- 规则检测：`event_detection.cards`
- 场景契约：preset、period、路口、车道、origin
- Catalog：控制模式白名单、事件类型

**【规划功能】** RAG 检索块、AI scope、上一周期执行效果。邻接关系没有独立一跳邻域 API；静态目录见 `intersection_topology_catalog.md`，运行时仍以 Snapshot `downstream_lane_ids`、Protocol 2.0 `direct_neighbors` 和当前 preset 为准。完整输入清单见 `ai_runtime_context_contract.md`。

## 预测与大模型职责

统一表述：**预测模型回答接下来可能怎样，CityPulse-Qwen 回答应该怎样管。**

**【项目事实】** 数值短时预测由 Backend `IntelligenceHub` + NarrowNet-TDP 完成，不是 Qwen。必须区分：模型底层是 12 帧 × 四特征 × 206 车道节点的约 60 秒 `vehicle_count`；LLM/Frontend 收到的是路口级聚合预测。模型不可用、历史不足或推理失败时降级移动平均，并返回 `fallback` 与 `fallback_reason`。局部预设下未选路口特征为 0，精度不能外推为官方 20 路口精度。

CityPulse-Qwen 不得自己编造数值型短时预测。

## 输出、校验与执行

**【规划功能】** Qwen 输出结构化 AI Control Plan，不得直接输出 SUMO 灯色字符串，不得连接 libsumo / TraCI。计划必须经过 schema → scope → phase → safety validation，再由 AI Plan Executor 编译为现有 Protocol 2.0：

```json
{"signals": {"demo_5": {"target_phase": 2}}, "vehicles": {}}
```

Worker 已有 `SafePhaseController` 强制最小绿、黄灯和全红清空。建议字段与编译规则见 `ai_control_output_schema.md`。

## Fallback 与恢复

**【规划功能】** 下列任一情况必须保持或恢复 baseline controller：

- 用户未启用 AI；
- 输出无法解析或 schema 失败；
- 路口、相位或时间窗非法；
- 安全规则失败；
- 模型超时、输出不可解析或校验失败；
- AI control window 结束。

`confidence` 可保留但第一版不设绝对阈值。回退不得中断仿真，也不得改写用户原来选择的 `control_mode`。状态机见 `ai_takeover_lifecycle.md`。

## 前端展示原则

**【项目事实】** 当前前端没有“启用 AI 管控”开关，也没有 Qwen 方案可视化。

**【规划功能】** 前端若展示 AI 状态，应区分 baseline controller、AI takeover 是否激活、受控路口、当前计划有效期、置信度和 fallback 原因。不得把规划方案画成已经在跑的 `control_mode`。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_control/registry.py; traffic_control/protocol.py; backend/app/scenario/presets.py; backend/app/schemas/events.py; backend/app/schemas/simulations.py; simulation/sumo/engine/signal.py; backend/app/services/prediction_runtime.py; backend/app/services/intelligence_runtime.py
   - 用于支持：现有控制模式、协议动作、事件类型、安全状态机和预测职责。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
