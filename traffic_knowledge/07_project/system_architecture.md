# citypulse-v2x-sim 系统架构

## 运行链路

**【项目事实】** 前端通过 HTTP/WebSocket 访问 FastAPI Backend，统一前缀 `/api/v1`。Backend 不直接持有 TraCI 循环，而是校验请求、创建会话，再通过本地线程或 Redis/Celery 管理器调度 SUMO Worker。Worker 独占 libsumo，加载 `simulation/` 与产品包 `traffic_control/`，把快照和终态写回会话。

`SIMULATION_MANAGER_MODE` 默认 `local`；`redis` 模式使用 `RedisSessionStore` + Celery 队列 `citypulse-sumo`，连接失败不降级 local。Uvicorn 要求单 worker，因为算法状态在进程内。

## 控制链路

**【项目事实】** 前端只提交业务名 `control_mode`。Backend 查 `traffic_control.registry`：`fixed` 映射内核 `fixed`；其余映射 `algorithm` + 本地 `algorithm_module`。本地模块遵循 Protocol 2.0：`initialize` 收路口元数据，`step` 收观测并返回 `{signals, vehicles}`，`finish` 清理。信号动作只有 `target_phase`。`SafePhaseController` 保证最小绿、黄灯和全红清空后，Worker 才把官方相位模板灯色写入 SUMO。

内部 HTTP `/api/v1/internal/algorithm/{name}/initialize|step|finish` 仅服务 `max_pressure` 和 `sotl`。IPPO/MAPPO 走 Worker 内 `LocalAlgorithmClient`。

**【规划功能】** CityPulse-Qwen 不进入上述注册表。它生成结构化 AI Control Plan；Backend AI Control Orchestrator 做安全校验，AI Plan Executor 在现有决策周期内把它转换成 Protocol 2.0 可执行 `target_phase`。CityPulse-Qwen 本身不直接操作 SUMO。详见 `ai_control_architecture.md`、`ai_plan_executor.md`。

## 评估链路

**【项目事实】** `traffic_eval` 是 Backend 与 CLI 共用口径。运行中从 `SimulationSnapshot` 采集排队、到达、急刹；终态用 TripInfo 回填行程、等待和燃油强度。`backend/app/metrics` 只封装，不另建公式。

## 智能分析链路

**【项目事实】** Backend `IntelligenceHub` 保存短时历史。预测模块是 **NarrowNet-TDP**（`backend/models/prediction/narrow_net_tdp`），不是在线依赖外部 STGCN 仓库；`stgcn_root` 仅兼容旧配置。历史 12 帧、特征为车道 `vehicle_count` / `halting_count` / `mean_speed` / `occupancy`、206 个训练车道节点，底层在 206 个车道节点上预测未来约 60 秒 `vehicle_count`；Backend 再聚合为路口级 `PredictionPayload.intersections`。不可用时降级 `moving_average`，返回 `fallback` 与 `fallback_reason`。不得写成“20 节点 STGCN”。

事件检测是规则/CUSUM 候选，写入 `event_detection.cards`，不修改 SUMO。检测语义包括 `normal`、`localized_blockage`、`spillback`、`capacity_drop`、`unknown_abnormal`。

**预测模型回答接下来可能怎样；CityPulse-Qwen 回答应该怎样管。** Qwen 不得自己做数值预测。

## 实时快照字段

**【项目事实】** `SimulationSnapshot` 主要字段：`session_id`、`state`、`elapsed_seconds`、`duration_seconds`、`intersections`、`vehicles`、`events`、`metrics`。路口含 `current_phase`、`pending_phase`、`stage`、`stage_elapsed`、`lanes`。车道含 `vehicle_count`、`halting_count`、`mean_speed`、`waiting_time`、`occupancy`、`role`、`approach_id`、`downstream_lane_ids`、`lane_has_green`、`signal_state`、`current_allowed_speed_mps`。状态 API 还附带 `evaluation`、`event_detection`、`prediction`、`traffic_style`。

## 主要 API

**【项目事实】**

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/simulations` | 启动；体为 `StartSimulationRequest` |
| GET | `/api/v1/simulations/{id}` | 状态快照 |
| WS | `/api/v1/simulations/{id}/stream` | 实时推送 |
| POST | `/api/v1/simulations/{id}/events` | 运行时注入扰动 |
| DELETE | `/api/v1/simulations/{id}/events/{event_id}` | 取消扰动 |
| GET | `/api/v1/simulations/{id}/metrics` | 正式评估 |
| GET | `/api/v1/simulations/{id}/intelligence` | 检测 + 预测 + 路况样式 |
| GET | `/api/v1/simulations/{id}/prediction` | 仅预测 |
| GET | `/api/v1/catalog` | 路口、预设、事件类型、控制模式 |
| POST | `/api/v1/scenarios/export` | 导出 SUMO 包 |

启动字段包括 `scenario_preset_id`、`period`、`duration_seconds`、`control_mode`、`model_alias`、`disturbance_targets`、`seed`、`step_length`（默认 0.1）、`snapshot_interval_seconds`（默认 0.5）。没有 `ai_enabled` 或 Qwen 字段。

## 代码归属

`traffic_control/` 是产品部署算法；`algorithms/` 主要是训练、实验和事件检测研究代码，不自动等于产品 `control_mode`。**【规划功能】** 未来 Qwen 应由 Backend 托管，生成结构化 AI Control Plan，经 Orchestrator / 安全校验 / Executor 变成 `target_phase`。Qwen 不得直接导入训练脚本或连接 libsumo。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: backend/app/api/router.py; backend/app/services/simulation_service.py; backend/app/services/prediction_runtime.py; backend/app/services/intelligence_runtime.py; simulation/sumo/engine/session.py; traffic_control/protocol.py
   - 用于支持：架构、API、预测和快照字段。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
