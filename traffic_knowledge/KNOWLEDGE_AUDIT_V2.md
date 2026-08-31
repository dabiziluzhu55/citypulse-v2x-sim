# 知识库第二轮审计报告（v0.4）

- 审计日期：2026-08-30
- 当前 HEAD：`1331ba87d6cd77e9052953d894a5dc83e1953009`（`1331ba8`，2026-08-30 18:33 +0800，`fix rag`）
- 知识库版本：`0.3` → `0.4`
- 范围：只修改 `traffic_knowledge/`（含 `traffic_knowledge/tools/`）
- 未修改：frontend / backend / simulation / traffic_control 业务代码
- 静态检查：`python3 traffic_knowledge/tools/audit_knowledge.py` → **RESULT PASS**

本轮 **没有**采信 `KNOWLEDGE_UPDATE_REPORT.md` 中的“已修改”叙述；所有结论来自直接阅读当前文件与代码。

## 本轮发现的旧内容（相对 0.3 实际文件）

0.3 的 README / capabilities 已经不是 0.2，也没有“策略推荐必须限定为 control_mode registry”。仍需修正或补齐的缺口：

| 位置 | 问题 |
| --- | --- |
| `llm_decision_boundaries.md` | 校验链未写成 schema→scope→phase→safety→executor→target_phase；未明确“不可替用户选 baseline / 不可绕过 Worker / 原子 fallback” |
| `scenario_definition.md` | 预设标签已正确，但预测未区分 206 车道底层 vs 路口级聚合 |
| `system_architecture.md` | 仍写“只生成结构化计划”，未写 Orchestrator + Executor 闭环 |
| `ai_control_output_schema.md` | `preferred_phase` 易被读成 30–60 s 锁相；confidence 写了绝对阈值 |
| `ai_control_cases.md` | 未独立覆盖 `speed_limit` 与 `major_event_opening` |
| 缺失文档 | 运行时上下文、Executor、生命周期、评估规范、拓扑/相位目录、事件响应专业原则、RAG 策略、审计脚本 |

过时短语（`recommended_control_mode`、`LLM可推荐`、`STGCN 20节点`、`当前代码没有把东部场景标为校园` 等）在 RAG 正文中已不作为当前定位出现。`KNOWLEDGE_UPDATE_REPORT.md` 作为历史记录仍含旧对照表，未纳入 manifest。

## 实际修改文件

- `README.md`
- `manifest.json`
- `07_project/llm_decision_boundaries.md`
- `07_project/scenario_definition.md`
- `07_project/supported_control_capabilities.md`
- `07_project/system_architecture.md`
- `07_project/ai_control_architecture.md`
- `07_project/ai_control_output_schema.md`
- `04_scenarios/ai_control_cases.md`
- `07_project/intersection_topology_catalog.md`（生成后校正 Protocol 2.0 邻接不对称表述）
- `07_project/signal_phase_movement_catalog.md`

## 新增文件

- `07_project/ai_runtime_context_contract.md`
- `07_project/ai_plan_executor.md`
- `07_project/ai_takeover_lifecycle.md`
- `07_project/rag_retrieval_policy.md`
- `05_metrics/ai_control_evaluation.md`
- `01_fundamentals/event_responsive_signal_control.md`
- `07_project/intersection_topology_catalog.md`
- `07_project/signal_phase_movement_catalog.md`
- `tools/audit_knowledge.py`
- `KNOWLEDGE_AUDIT_V2.md`（本报告，不进入 RAG manifest）

## 当前项目事实

- `control_mode`：`fixed` / `sotl` / `max_pressure` / `ippo` / `mappo`。Qwen 不是其中之一。
- 预设：`xiongan_20` 标签「雄安20路口路网」；`east_dense` 标签「校园周边场景」路口 `demo_3/5/6/9`；`west_dense` 标签「窄路密网片区场景」路口 `demo_14/15/19`。
- 可注入事件：`lane_closure`、`speed_limit`、`accident`、`major_event_opening`、`major_event_closing`。
- 可执行信号动作只有 Protocol 2.0 `target_phase`；`SafePhaseController`：最小绿默认 5 s + yellow + all_red。
- `decision_interval` 默认 5.0 s；Backend `step_length` 默认 0.1 s。
- Snapshot 车道字段含 `vehicle_count`、`halting_count`、`mean_speed`、`waiting_time`、`occupancy`、`current_allowed_speed_mps` 等；没有名为 `waiting` 的独立字段。
- 预测：NarrowNet-TDP，12 帧、四特征、206 车道节点、约 60 s 车道级 `vehicle_count`；IntelligenceHub 聚合为路口级 `PredictionPayload`。失败降级 `moving_average`。
- Protocol 2.0 `direct_neighbors`（`outgoing_edges ∩ incoming_edges`）几乎为空：金标 metadata 仅 `demo_2`→`demo_4`、`demo_14`↔`demo_19`。
- 无 `ai_enabled`、无 Qwen、无 takeover 状态机。

## 当前规划功能

- AI takeover 生命周期：INACTIVE → ARMED → ACTIVE → RECOVERY → FINISHED；异常 FALLBACK → BASELINE。
- 双时间尺度：Qwen 30–60 s 高层 plan；Executor 在 5 s 决策周期编译 `target_phase`。
- 原子计划：关键字段非法则整单 fallback，不做半执行。
- `confidence` 可保留，不设绝对阈值。
- `max_green_extension`、显式 gating/spillback Adapter 需未来支持。
- pause/resume、多事件并行、scope 重叠、recovery 中插入新事件：待实现策略。
- AI 工程指标与局部回溢/恢复时间：规划指标，不是当前 `EvalResult`。

## topology catalog 是否成功生成

**成功，且标明定义边界。** 来源均为代码/制品，不是地图目测：

1. Protocol 2.0 `direct_neighbors`（几乎为空，且不一定对称）
2. CosLight `cloud_topology_v2.json` 有向走廊（客车最短路，走廊阈值 1500 m）
3. `topology-routes.json` 无向路径

可回答：

- `demo_5`：协议邻接无；CosLight 直接邻接 `demo_6`；路径邻接还有 `demo_9`、`demo_1`、`demo_3`
- `east_dense`：路径上 3–5、3–6、5–6、5–9、6–9 相连；CosLight 走廊不含 `demo_3`（距离超过 1500 m）
- `west_dense`：14–15–19 三角；协议层仅 14↔19

**上游/下游不能硬编码为东/西**，必须是拓扑邻接 + 流向相关。仓库没有按 origin 标注的单一权威上下游表。

## phase catalog 是否成功生成

**成功。** 来源：`official_tls_topology.json`、`official_tls_plans.json`、`tls_manifest.json` 的 `phase_order` / `phase_movements`。

关键限制：`demo_4` 的官方 topology 按 period 给出不同相位组合（平峰 3 相位 vs 早晚 4 相位）。**runtime phase metadata 优先于 RAG catalog。** 不得用灯色字符串作为 LLM 控制目标。

## 仍无法从代码确定的信息

- 每个 origin 的“主要上游/下游”单一权威表（只能运行时按流向推断）
- 未来 Adapter 何时实现 gating / max green extension
- 多事件、暂停仿真、用户取消事件的最终产品策略
- 局部回溢持续时间等规划指标的计算实现
- 前端展示文案是否仍写“东部/西部密集区”（Backend label 已是校园/窄路密网）

## 是否已经适合 chunking / embedding

结构上已可进入 embedding 准备：文档有三类信息标签、manifest 可按 `information_type` / event / preset / status 过滤、核心闭环问题可由「运行时 + 项目事实 + 专业知识」共同回答。

仍建议 **不要立刻全库 embedding**，先做下一节的切片与排除。

## 建议的 embedding 前最后步骤

1. 按路口切分 `intersection_topology_catalog.md` 与 `signal_phase_movement_catalog.md`，避免 20 路口大表进同一个 chunk。
2. 排除 `KNOWLEDGE_*.md`、`tools/`、规划段若与项目事实同 chunk 必须保留【规划功能】前缀。
3. chunk 元数据写入 manifest 同名字段：`information_type`、`applicable_events`、`applicable_presets`、`code_revision`。
4. 检索时强制注入 runtime payload；与 RAG 冲突时 runtime 优先。
5. 再跑一遍 `tools/audit_knowledge.py`，并人工抽检 13 个验收问题是否都能落到具体文档标题。

## 验收问题对照

| # | 问题 | 主要依据 |
| --- | --- | --- |
| 1 | 当前发生了什么扰动 | runtime `EventSnapshot` + `ai_runtime_context_contract.md` |
| 2 | 哪些路口直接受影响 | 事件目标 + preset |
| 3 | 上下游传播 | `intersection_topology_catalog.md` + 流向 |
| 4 | 短时预测 | 底层 206 车道 vs 路口聚合，`scenario_definition.md` |
| 5 | AI 允许控制哪些路口 | preset 子集 + lifecycle/architecture |
| 6 | 合法 phase | runtime metadata > `signal_phase_movement_catalog.md` |
| 7 | 协同目标 | `event_responsive_signal_control.md` + 五类案例 |
| 8 | 高层 AI plan | `ai_control_output_schema.md` / executor |
| 9 | 如何变成 target_phase | Executor + Protocol 2.0 |
| 10 | 安全约束 | SafePhaseController + boundaries |
| 11 | 失败 fallback | 原子计划 → baseline |
| 12 | 何时退出 AI | `ai_takeover_lifecycle.md` |
| 13 | 如何公平验证 | `ai_control_evaluation.md`：同一 baseline + 同一扰动，AI ON vs OFF |
