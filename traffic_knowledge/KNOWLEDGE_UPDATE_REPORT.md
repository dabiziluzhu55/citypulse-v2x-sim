# traffic_knowledge 0.3 更新报告

更新日期：2026-08-30。代码基线：`main@89e1a8173132fc734b4d0c51fb0b71fa36dd4b9d`。本轮只修改 `traffic_knowledge/`。

## 1. 阅读的关键代码目录

- `backend/app/scenario/`：`presets.py`、`resolver.py`
- `backend/app/schemas/`：`events.py`、`disturbance_targets.py`、`simulations.py`、`catalog.py`、`intelligence.py`
- `backend/app/api/`：`router.py` 及 simulations / catalog 路由
- `backend/app/services/`：`simulation_service.py`、`prediction_runtime.py`、`intelligence_runtime.py`
- `backend/app/core/config.py`、`backend/app/controllers/registry.py`
- `traffic_control/protocol.py`、`registry.py`、各算法与 IPPO/MAPPO aliases
- `simulation/sumo/engine/`：`session.py`、`signal.py`、`events.py`、`run.py`、`scenario.py`
- `simulation/sumo/algorithm/policy.py`
- `traffic_eval/models.py`
- `algorithms/event_detection/semantics.py`
- `frontend/src/constants/scenarioOptions.ts` 等场景与扰动文案
- `data/maps/sumo/official/` 与 `generated/manifests/traffic_manifest.json`

结论：仓库中 **不存在** CityPulse-Qwen、AI takeover、独立 Qwen 服务或 `ai_enabled` 启动字段。可执行信号动作仍只有 Protocol 2.0 的 `target_phase`。在线预测已是 NarrowNet-TDP，不再依赖外部 STGCN 仓库。

## 2. 修改的知识库文件

- `README.md`
- `manifest.json`
- `01_fundamentals/traffic_flow_basics.md`
- `01_fundamentals/traffic_state_and_congestion.md`
- `01_fundamentals/traffic_signal_basics.md`
- `02_control_algorithms/fixed_time.md`
- `02_control_algorithms/sotl.md`
- `02_control_algorithms/max_pressure.md`
- `02_control_algorithms/ippo.md`
- `02_control_algorithms/mappo.md`
- `03_traffic_events/congestion.md`
- `03_traffic_events/traffic_accident.md`
- `03_traffic_events/lane_blockage.md`
- `03_traffic_events/abnormal_parking.md`
- `03_traffic_events/sudden_demand_surge.md`
- `04_scenarios/school_commuting_area.md`
- `04_scenarios/narrow_road_dense_network.md`
- `04_scenarios/xiongan_narrow_road_dense_network.md`
- `04_scenarios/morning_evening_peak.md`
- `04_scenarios/normal_off_peak.md`
- `05_metrics/safety_metrics.md`
- `05_metrics/metric_interpretation.md`
- `06_standards/terminology.md`
- `07_project/system_architecture.md`
- `07_project/supported_control_capabilities.md`
- `07_project/scenario_definition.md`
- `07_project/llm_decision_boundaries.md`
- `08_simulation_cases/README.md`

## 3. 新增的文件

- `01_fundamentals/multi_intersection_coordination.md`
- `03_traffic_events/speed_limit.md`
- `04_scenarios/simulation_demand_catalog.md`
- `04_scenarios/ai_control_cases.md`
- `07_project/ai_control_architecture.md`
- `07_project/ai_control_output_schema.md`
- `KNOWLEDGE_UPDATE_REPORT.md`（本报告，不作为 RAG 主文档）

未再拆 `ai_control_scope.md` / `ai_control_lifecycle.md`，范围与时间已写入架构文档，避免重复。

## 4. 删除或修正的过时描述

| 旧说法 | 修正 |
| --- | --- |
| LLM 只推荐 / 只选择 `control_mode` | CityPulse-Qwen 不负责选算法；扰动 + 启用后才做局部接管（规划） |
| LLM 不参与管控、只做问答 | 定位改为事件触发的局部协同管控模型 |
| 输出必须含 `recommended_control_mode` | 改为结构化计划，最终编译 `target_phase` |
| `east_dense` 未标校园、`west_dense` 只是西部密集 | Backend 标签已是校园周边 / 窄路密网片区；前端文案仍可能不同 |
| 在线预测是 STGCN | NarrowNet-TDP，`stgcn_root` 仅兼容旧配置 |
| 官方数据在 `data/maps/sumo/*.json` 根目录 | 已迁到 `official/map`、`official/traffic`、`official/tls` |
| 未建模公交/自行车 | 需求已含公交与电动自行车；行人仍未生成 |
| 高峰时钟不明 | 写入 07:00–09:00 / 14:30–16:30 / 17:30–19:30 |
| 局部 preset 等于独立需求包 | API 启动路径当前默认 `scenario_scope=global` |

全局检索已确认不再残留：“LLM只进行算法选择”“LLM只推荐control_mode”“LLM不参与交通管控”“LLM仅用于智能问答”。

## 5. 属于当前代码事实的知识

- 三个 preset、路口列表、Backend 与前端标签差异
- 五个注入事件类型及 SUMO 效果
- 五个 `control_mode`、Protocol 2.0、`SafePhaseController`
- 快照字段、Catalog / Simulations API
- NarrowNet-TDP：12 帧、四特征、60 秒、206 车道、moving_average fallback
- 规则检测语义与注入真值的区别
- `EvalResult` 正式指标
- 生成需求 9 套场景的 PCU、车型混合、路径策略
- 官方信号 yellow / all_red、许可右转、禁止掉头

## 6. 属于未来 AI 管控规划的内容

必须按 **【规划功能】** 阅读，不得写成已上线：

- AI takeover / baseline 临时替换
- AI controlled intersections 与 3–6 路口推荐邻域
- AI control window、`recovery_seconds`、动态退出
- 30–60 仿真秒决策周期
- 上层计划字段（`control_scope`、`preferred_phase` 等）
- schema + 安全校验 + 执行适配器
- 故障回退到原 baseline
- 前端“启用 AI 管控”与方案展示
- 独立 CityPulse-Qwen 服务

## 7. 仍缺少的交通专业知识

- 雄安各 demo 路口的官方地理名称、道路等级和实测红线宽度
- 行人、校车、接送比例等校园实测
- 一跳邻域的权威拓扑表（现只能从路网/下游车道推断）
- 公交优先、应急车、可变信息板等管控手段
- 正式 TTC/PET/碰撞与行人冲突指标
- 已复现的 AI takeover 对照实验（尚无代码，故无结果）
- 官方配时方案到“优先方向 ↔ phase_no”的逐路口检索表（避免模型猜相位号）

## 8. 是否适合进入 chunking / embedding

**可以进入试验性 chunking**，前提是：

1. chunk 元数据必须带 `信息类别`（项目事实 / 交通专业知识 / 规划功能）和文档路径；
2. 规划段落不得与项目事实混在同一最小 chunk；
3. 生成需求数字必须标注“制品，非实时”；
4. 本报告文件可不入库。

**还不适合直接当 SFT 语料**：没有真实 takeover 运行轨迹和校验失败/回退样本。

## 9. 构建 RAG 前建议补充的资料

1. 由代码导出的 20 路口相位—转向映射表（只含确认过的 `official_phase_no`）。
2. 每个 preset 的邻接/下游车道摘要，供“哪些相邻路口可能受影响”检索。
3. 实现 AI 编排后，把真实 schema 回写知识库，删除与代码不一致的建议字段。
4. 若干 Fixed 对照 + 注入扰动的评估运行，再写入 `08_simulation_cases/`。
5. 统一前端与 Backend 的 preset 中文标签，避免 RAG 与 UI 各说各话。
