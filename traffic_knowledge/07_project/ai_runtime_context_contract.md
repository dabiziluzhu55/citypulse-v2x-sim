---
information_type: mixed
status: current
code_revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
applicable_events:
  - accident
  - lane_closure
  - speed_limit
  - major_event_opening
  - major_event_closing
applicable_presets:
  - xiongan_20
  - east_dense
  - west_dense
priority: high
---

# CityPulse-Qwen 运行时上下文协议

本文定义每一次 CityPulse-Qwen 决策 **应当输入哪些信息、这些数据从哪里来**。**【项目事实】** 字段以当前 Snapshot / IntelligenceHub / Catalog 为准。**【规划功能】** 指尚未实现的编排载荷，不得写成已有 API。

## 证据优先级

冲突时按下表取高优先级，**规划设计知识不得覆盖实时事实**：

1. 运行时实时状态（当前 `SimulationSnapshot`、注入事件、runtime phase metadata）
2. 当前代码事实（registry、protocol、SafePhaseController）
3. 当前场景配置（preset、period、duration、seed、disturbance）
4. 项目事实型 RAG（本知识库中标注为项目事实的文档）
5. 交通专业知识（机理与原则）
6. 规划设计知识（待实现架构）

runtime payload 与 RAG 冲突时，**runtime payload 优先**。RAG 中的 phase / 邻接信息不能替代当前会话 metadata。

## 1. session context

**【规划功能】** 每次推理应附带：

| 字段 | 来源 | 状态 |
| --- | --- | --- |
| `session_id` | `SimulationSnapshot.session_id` | 项目事实 |
| `simulation_time` / `elapsed_seconds` | `SimulationSnapshot.elapsed_seconds` | 项目事实 |
| `official_time` | `SimulationSnapshot.official_time` | 项目事实 |
| `duration_seconds` | `SimulationSnapshot.duration_seconds` | 项目事实 |
| `scenario_preset` | 启动请求 `scenario_preset_id` | 项目事实；当前快照对象本身不带该字段，需编排器注入 |
| `period` | 启动请求 | 项目事实；需编排器注入 |
| `baseline_controller` | 启动请求 `control_mode` | 项目事实；需编排器注入 |
| `decision_interval` | 会话配置，Backend 默认 `5.0` s | 项目事实 |
| `step_length` | 启动请求，Backend 默认 `0.1` s | 项目事实 |
| `seed` | 启动请求 | 项目事实；需编排器注入 |

不得把 `control_mode` 说成 Qwen 的输出。它是用户选择的 baseline controller。

## 2. event context

**【项目事实】** `EventSnapshot` 字段：`event_id`、`event_type`、`state`、`start_seconds`、`end_seconds`、`error`、`details`。`state` 为 `SCHEDULED` / `ACTIVE` / `COMPLETED` / `CANCELLED` / `FAILED`。可注入类型：`lane_closure`、`speed_limit`、`accident`、`major_event_opening`、`major_event_closing`。

**【规划功能】** 决策载荷应再显式给出：

- 事件目标路口 / 车道（从 `details` 或启动 `disturbance_targets` 解析）
- 当前是否仍在事件时窗内
- 规则检测卡片 `event_detection.cards` 仅为候选，**不能**当作注入真值

## 3. AI scope

**【规划功能】** 编排器计算并写入：

| 字段 | 含义 |
| --- | --- |
| `event_intersection` | 事件直接作用的路口 |
| `upstream_intersections` | 沿当前事件流向能向该路口送车的邻接路口 |
| `downstream_intersections` | 沿当前事件流向接收放行交通的邻接路口 |
| `controlled_intersections` | 最终允许 AI takeover 的路口集合 |

约束：必须是当前 preset 子集；不得默认 20 路口。拓扑定义见 `intersection_topology_catalog.md`。上游/下游取决于流向，不能硬编码为东/西。

## 4. live traffic state

**【项目事实】** 只能使用 Snapshot 实际字段，不得编造密度、饱和度、行程时间等未发布字段。

路口 `IntersectionRuntimeSnapshot`：

- `current_phase`
- `pending_phase`
- `stage`
- `stage_elapsed`
- `lanes`

车道 `LaneRuntimeSnapshot`：

- `vehicle_count`
- `halting_count`
- `mean_speed`
- `waiting_time`（不是名为 `waiting` 的独立字段）
- `occupancy`
- `role`
- `approach_id`
- `downstream_lane_ids`
- `lane_has_green`
- `signal_state`
- `current_allowed_speed_mps`

会话级 `SessionMetrics`：`active_vehicles`、`departed_vehicles`、`arrived_vehicles`、`remaining_vehicles`、`halting_vehicles`、`total_waiting_time`、`mean_speed`、燃油与急刹累计。这些是会话汇总，不是路口级结算指标。

## 5. prediction

必须区分两层粒度。

**【项目事实】底层模型（NarrowNet-TDP）**

- 历史 12 帧
- 车道特征：`vehicle_count`、`halting_count`、`mean_speed`、`occupancy`
- 206 个训练车道节点
- 输出未来约 60 s 的车道级 `vehicle_count`
- 不可用、历史不足或推理失败时，IntelligenceHub 降级 `moving_average`，并返回 `fallback` / `fallback_reason`

**【项目事实】提供给 Frontend / 规划中 LLM 的聚合结果**

`PredictionPayload`：

- `horizon_seconds`（默认 60）
- `as_of_seconds`
- `model` / `model_version`
- `ready` / `fallback` / `fallback_reason`
- `inference_latency_ms`
- `intersections[intersection_id]`：`current_vehicle_count`、`predicted_vehicle_count`、`delta`、`delta_ratio`

聚合方式：按 `tls_manifest` 进口车道映射到官方路口节点后求和。这是 **路口级聚合预测**，不是“20 个节点 STGCN”。

局部预设只采集受控路口车道，其余训练节点为 0，精度不得外推为官方 20 路口精度。

## 6. RAG knowledge

**【规划功能】** 一次 AI control retrieval 应覆盖：

- 事件响应专业知识
- 多路口协同知识
- 信号约束与安全规则
- 系统能力事实（registry、preset、protocol）
- 当前 preset 拓扑与相位语义（catalog，仅理解用）

检索策略见 `rag_retrieval_policy.md`。规划设计文档不得当作已实现 API。

## 7. previous AI plan

**【规划功能】** 若上一周期存在计划，应输入：

- `previous_objective`
- `previous_scope` / `controlled_intersections`
- `previous_actions`（高层意图，不是灯色串）
- `execution_outcome`：是否通过校验、实际下发的 `target_phase`、是否 fallback、受控路口排队/停止数变化

第一版没有该对象时，应显式标记 `previous_plan: null`，不得编造执行效果。

## 8. 不应输入的内容

- SUMO 灯色字符串
- 未实现的邻接 API 伪字段
- 把检测卡片写成已确认事故
- 把 RAG catalog 的 phase 表写成当前 runtime 唯一真相

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: simulation/sumo/engine/session.py; simulation/sumo/engine/events.py; backend/app/schemas/intelligence.py; backend/app/services/intelligence_runtime.py; backend/app/services/prediction_runtime.py; backend/app/core/config.py
   - 用于支持：快照字段、事件字段、预测两级粒度。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
