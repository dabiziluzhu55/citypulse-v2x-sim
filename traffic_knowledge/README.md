# CityPulse Traffic Knowledge Base

本目录是 `citypulse-v2x-sim` 的 RAG 知识原料库。知识库版本为 `0.4`，代码基线为 `main@1331ba87d6cd77e9052953d894a5dc83e1953009`，外部标准状态核验日期为 2026-08-25，项目事实复核日期为 2026-08-30。

它不是实时交通数据库、控制指令集、微调样本集或已上线的 AI 控制器。本版不生成 embedding、不实现 Qwen API、不修改 Backend / Frontend / SUMO Worker。

## CityPulse-Qwen 定位

CityPulse-Qwen 是 **面向突发交通扰动的知识增强局部多路口协同信号管控模型**。

用户选择的 `control_mode` 是 **baseline controller**。只有同时满足：

1. 扰动事件存在；
2. 用户主动启用 AI 管控；

CityPulse-Qwen 才对允许范围内的部分路口进行临时 AI takeover。AI 结束后恢复 baseline controller。

它不是：

1. 普通交通智能问答模型；
2. 在 Fixed、SOTL、Max Pressure、IPPO、MAPPO 之间自动选择算法的模型；
3. 全路网逐秒直接控制全部信号灯的模型；
4. 新增的普通 `control_mode`。

闭环路径（规划）：高层 AI Control Plan → Backend Orchestrator 校验 → AI Plan Executor → Protocol 2.0 `target_phase` → `SafePhaseController` → SUMO。Qwen 本身不直接操作 SUMO。

**【规划功能】** AI takeover、Orchestrator、Executor 和独立 Qwen 服务当前代码尚未实现。**【项目事实】** 可执行信号动作仍只有 Protocol 2.0 的 `target_phase`。

## 三类信息

1. **【项目事实】**：以当前仓库代码、配置和生成清单为准。
2. **【交通专业知识】**：交通工程机理、规范或可靠资料。
3. **【规划功能】**：准备接入但尚未实现的 AI 管控能力。不得写成“系统已经具备”。

机器可过滤元数据写在 `manifest.json` 的 `documents[]`（`information_type`、`status`、`applicable_events`、`applicable_presets`）。部分 AI 文档另有 Markdown frontmatter。通用理论不得覆盖项目能力边界。任何外部知识都不能当作当前仿真实时观测。

## 内容范围

- `01_fundamentals/`：交通流、状态、信号、多路口协同与事件响应信号控制。
- `02_control_algorithms/`：已注册的 baseline controller：Fixed、SOTL、Max Pressure、IPPO、MAPPO。
- `03_traffic_events/`：拥堵、事故、施工占道、限速、异常停车和突发需求。
- `04_scenarios/`：预设、时段、雄安密网、仿真需求目录与 AI 管控案例。
- `05_metrics/`：`traffic_eval` 正式指标口径与 AI 管控评估规范。
- `06_standards/`：现行标准与术语映射。
- `07_project/`：架构、能力、场景契约、运行时上下文、Executor、生命周期、拓扑/相位目录、RAG 策略与决策边界。
- `08_simulation_cases/`：可复现实验案例模板。
- `tools/audit_knowledge.py`：知识库静态一致性检查，不修改业务代码。

## 检索约定

每个二级或三级标题尽量形成可独立理解的知识块。检索时应保留文档路径、标题层级、版本、信息类别和来源。策略见 `07_project/rag_retrieval_policy.md`。

CityPulse-Qwen 检索应能与运行时数据一起回答：当前扰动是什么、影响哪些路口、上下游如何传播、短时预测是什么、允许控制哪些路口、合法 phase 是哪些、协同目标是什么、高层 plan 应长什么样、如何变成 `target_phase`、安全约束、失败如何 fallback、何时退出 AI、如何公平验证效果。

## 证据等级

1. **项目事实**：当前 `main` 代码、配置或生成制品。
2. **规范事实**：国家标准全文公开系统或公安标准化信息服务平台，并记录状态。
3. **仿真口径**：SUMO 官方文档或项目生成报告。
4. **算法原理**：原始论文；项目实现差异单独说明。
5. **规划设计**：明确标注待实现，不得覆盖项目事实。
6. **情景推断**：必须使用“可能”“需验证”等限定语。

证据优先级：运行时实时状态 > 当前代码事实 > 当前场景配置 > 项目事实型 RAG > 交通专业知识 > 规划设计知识。

## 来源

1. citypulse-v2x-sim 当前项目
   - 发布机构：项目仓库
   - 年份：2026
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_control/registry.py; backend/app/scenario/presets.py; traffic_control/protocol.py; traffic_eval/
   - 用于支持：知识库范围、项目能力和版本基线。
