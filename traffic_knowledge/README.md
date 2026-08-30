# CityPulse Traffic Knowledge Base

本目录是 `citypulse-v2x-sim` 的 RAG 知识原料库。知识库版本为 `0.3`，代码基线为 `main@89e1a8173132fc734b4d0c51fb0b71fa36dd4b9d`，外部标准状态核验日期为 2026-08-25，项目事实复核日期为 2026-08-30。

它不是实时交通数据库、控制指令集、微调样本集或已上线的 AI 控制器。本版不生成 embedding、不实现 Qwen API、不修改 Backend / Frontend / SUMO Worker。

## CityPulse-Qwen 定位

CityPulse-Qwen 是 **面向突发交通扰动的知识增强局部多路口协同信号管控模型**。

它不是：

1. 普通交通智能问答模型；
2. 在 Fixed、SOTL、Max Pressure、IPPO、MAPPO 之间自动选择算法的模型；
3. 全路网逐秒直接控制全部信号灯的模型；
4. 新增的普通 `control_mode`。

它只在用户注入扰动事件并主动启用 AI 管控后，对扰动影响区域做态势理解，并生成局部多路口协同信号管控方案。普通情况下，用户选择的基线算法独立运行，用于公平对比。详见 `07_project/ai_control_architecture.md`。

**【规划功能】** AI takeover、决策周期、安全校验适配器和独立 Qwen 服务当前代码尚未实现。**【项目事实】** 可执行信号动作仍只有 Protocol 2.0 的 `target_phase`。

## 三类信息

1. **【项目事实】**：以当前仓库代码、配置和生成清单为准。
2. **【交通专业知识】**：交通工程机理、规范或可靠资料。
3. **【规划功能】**：准备接入但尚未实现的 AI 管控能力。不得写成“系统已经具备”。

通用理论不得覆盖项目能力边界。任何外部知识都不能当作当前仿真实时观测。

## 内容范围

- `01_fundamentals/`：交通流、状态、信号与多路口协同基础。
- `02_control_algorithms/`：已注册的 baseline controller：Fixed、SOTL、Max Pressure、IPPO、MAPPO。
- `03_traffic_events/`：拥堵、事故、施工占道、限速、异常停车和突发需求。
- `04_scenarios/`：预设、时段、雄安密网、仿真需求目录与 AI 管控案例。
- `05_metrics/`：`traffic_eval` 正式指标口径。
- `06_standards/`：现行标准与术语映射。
- `07_project/`：架构、能力、场景契约、AI 管控规划与决策边界。
- `08_simulation_cases/`：可复现实验案例模板。

## 检索约定

每个二级或三级标题尽量形成可独立理解的知识块。检索时应保留文档路径、标题层级、版本、信息类别和来源。

CityPulse-Qwen 检索应优先服务这些问题：扰动影响哪些相邻路口、下游饱和时上游是否继续放行、散场为何不能只控单路口、施工后看哪些指标、事件结束后为何还要恢复控制、系统允许控制哪些路口和哪些动作、某建议是否超出当前能力、方案如何变成 SUMO 可执行动作。

## 证据等级

1. **项目事实**：当前 `main` 代码、配置或生成制品。
2. **规范事实**：国家标准全文公开系统或公安标准化信息服务平台，并记录状态。
3. **仿真口径**：SUMO 官方文档或项目生成报告。
4. **算法原理**：原始论文；项目实现差异单独说明。
5. **规划设计**：明确标注待实现，不得覆盖项目事实。
6. **情景推断**：必须使用“可能”“需验证”等限定语。

## 来源

1. citypulse-v2x-sim 当前项目
   - 发布机构：项目仓库
   - 年份：2026
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 89e1a8173132fc734b4d0c51fb0b71fa36dd4b9d
   - file: traffic_control/registry.py; backend/app/scenario/presets.py; traffic_control/protocol.py; traffic_eval/
   - 用于支持：知识库范围、项目能力和版本基线。
