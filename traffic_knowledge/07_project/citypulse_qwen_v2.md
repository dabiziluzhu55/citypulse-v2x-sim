# CityPulse-Qwen V2

## 产品名称

**【项目事实】** 前端与 Copilot 对用户展示的名称是 **CityPulse-Qwen**。仓库内部训练/部署目录和 vLLM served name 仍可能写作 `traffic-qwen-v2`，那是推理服务标识，不是另一套产品模型。

CityPulse-Qwen V2 是当前系统唯一在线大模型。Copilot 问答与 AI 事件接管共用同一 `base_url` + `model`，但 timeout、max_tokens、tools 和 RAG 相互独立。

## 能力边界

它是 **面向突发交通扰动的局部多路口协同信号管控模型**，同时也作为只读交通 Copilot。

它不是：

1. 普通闲聊模型的产品名称；
2. 在 Fixed、SOTL、Max Pressure、IPPO、MAPPO、CoV2X 之间自动选择算法的模型；
3. 全路网逐秒直接控制全部信号灯的模型；
4. 新增的普通 `control_mode`。

## 部署形态

**【项目事实】** 当前在线推理使用 Traffic-Qwen V2 的 QLoRA 合并后再 AWQ 量化的 vLLM OpenAI 兼容服务，默认端口 8001，served name `traffic-qwen-v2`。需要开启 tool calling（`--enable-auto-tool-choice --tool-call-parser hermes`）后，Copilot 才能稳定调用 RAG 与实时工具。

QLoRA 训练与 AWQ 转换属于离线 `algorithms/traffic_llm/deployment/` 流程，不在 Backend 启动时重新训练。

## 两条在线路径

| 路径 | 是否使用 RAG | 是否使用实时 Tools | 触发条件 |
| --- | --- | --- | --- |
| Copilot 问答 | 是，`search_knowledge` | 有仿真会话时使用实时交通工具；无仿真时仅 RAG + 计算器 | 用户在助手面板提问，不要求开启 AI 管控 |
| AI 事件接管 | 否。AI Control 不把 RAG 文本写入 Observation V2 | 不走 Copilot Tools | 启动时至多一个扰动 `ai_control_enabled=true` |

闭环控制路径：扰动事件 + 用户启用 AI → Backend 用 Observation V2 请求 CityPulse-Qwen → 校验 `AIControlPlan` → Worker 经 `SafePhaseController` 执行 `target_phase`。模型本身不直接操作 SUMO。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 0847ae894e1456fa43d97c3332b1418399a04194
   - file: backend/app/core/config.py; backend/app/api/v1/copilot.py; backend/app/services/takeover_orchestrator.py; algorithms/traffic_llm/deployment/serve_vllm.py
   - 用于支持：产品名称、V2 部署形态和 Copilot / AI Control 分工。
