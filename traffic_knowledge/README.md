# CityPulse Traffic Knowledge Base

本目录是 `citypulse-v2x-sim` 的第一版智能交通知识原料库，用于后续 RAG 检索。它不是实时交通数据库、控制指令集或微调样本集。知识库版本为 `0.2`，代码基线为 `main@28ea10b0fa11cd7f2a507415430ee88fa01e4809`，外部标准状态核验日期为 2026-08-25。

## 内容范围

- `01_fundamentals/`：交通流、交通状态与信号基础。
- `02_control_algorithms/`：系统已注册的 Fixed、SOTL、Max Pressure、IPPO、MAPPO。
- `03_traffic_events/`：拥堵、事故、异常停车、车道阻塞和突发需求。
- `04_scenarios/`：项目预设、雄安新区窄路密网与典型时段知识，不包含虚构实时数据。
- `05_metrics/`：`traffic_eval` 的真实指标口径及其解释边界。
- `06_standards/`：截至核验日期的现行标准与术语。
- `07_project/`：系统架构、能力清单、场景契约与 LLM 决策边界。
- `08_simulation_cases/`：后续沉淀可复现实验案例的模板。

## 检索使用约定

每个二级或三级标题尽量形成可独立理解的知识块。检索时应同时保留文档路径、标题层级、版本和来源。项目事实以 `07_project/` 和实际代码为准；通用理论不得覆盖项目能力边界。

策略推荐必须限定为 `traffic_control.registry.CONTROL_MODE_REGISTRY` 当前注册项，并在执行前由 Backend 校验场景、算法、模型别名和运行参数。任何外部知识都不能被当作当前仿真实时观测。

## 证据等级

1. **项目事实**：来自当前 `main` 分支代码、配置或部署文档。
2. **规范事实**：来自国家标准全文公开系统或公安标准化信息服务平台，并记录状态。
3. **仿真口径**：来自 SUMO 官方文档。
4. **算法原理**：来自原始论文；项目实现差异单独说明。
5. **情景推断**：由上述事实推出，必须使用“可能”“需验证”等限定语。

## 明确排除

本版不生成 embedding、FAISS 索引或 SFT 数据，不下载或训练 Qwen，不修改 SUMO、Backend、前端和现有控制算法。校园片区和窄路密网没有实测交通量、事故率或时段比例时，不写具体数值。

## 来源

1. citypulse-v2x-sim 当前项目
   - 发布机构：项目仓库
   - 年份：2026
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: README.md; traffic_control/registry.py; backend/app/scenario/presets.py; traffic_eval/
   - 用于支持：知识库范围、项目能力和版本基线。
