# Copilot + RAG + Tools 架构

## 当前实现

**【项目事实】** CityPulse-Qwen Copilot 已接入 Backend，不要求开启 AI 管控，也不要求先启动仿真才能提问。

| 模式 | 接口 | 模型 | 工具 |
| --- | --- | --- | --- |
| 无仿真 | `POST /api/v1/copilot/chat`，`session_id` 缺省 | CityPulse-Qwen + RAG | 仅 `search_knowledge`、`calculator` |
| 有仿真 | 同上并携带 `session_id`；兼容 `POST /api/v1/simulations/{session_id}/copilot/chat` | CityPulse-Qwen + RAG + 实时 Tools | 上述工具，加上当前交通、路网摘要、预测、事件、AI 接管状态、历史、道路上下文 |

不要伪造 simulation session。无 session 时如果用户问当前车辆数、最堵路口或未来约 60 秒拥堵，应明确说明暂无实时仿真数据，同时仍可回答算法与交通知识问题。

## RAG

- 知识原料：`traffic_knowledge/` + `manifest.json`。
- 索引：`scripts/rag/build_knowledge_index.py` 离线构建 Chroma；Backend 只读加载，启动时不重新 Embedding。
- 用户明确点名算法时，后端 `route_knowledge_query()` 先按 alias 锁定 `document_id`，再在该文档内检索，避免 control profile 或全库 Top-K 把算法文档挤掉。
- 索引带 `knowledge_content_hash`。Markdown 已变但 version 忘记修改时，也会判定 index stale。

AI 事件接管 **不依赖 RAG**。Copilot 的知识问答不能替代 `get_ai_takeover_status` 的运行时事实。

## 证据优先级

运行时实时状态 > 当前代码/项目事实型 RAG > 交通专业知识 > 模型常识。项目实现、评估口径和标准条款必须有检索证据；一般交通知识在检索为空时可以降级用通用知识，但不得编造本项目实现。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 0847ae894e1456fa43d97c3332b1418399a04194
   - file: backend/app/api/v1/copilot.py; backend/app/copilot/orchestrator.py; backend/app/copilot/rag.py; backend/app/copilot/traffic_tools.py
   - 用于支持：无仿真 Copilot、工具白名单和算法别名检索。
