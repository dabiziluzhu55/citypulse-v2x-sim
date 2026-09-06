# CityPulse Traffic Copilot 交接与启动说明

更新时间：2026-09-04

主要分支：`feature/perception`

本文是当前 Traffic Copilot、Qwen 和双路 RAG 的交接入口。目标是让下一位同学能够用同一套模型、索引和启动检查复现问答结果，避免误用旧索引、漏配国家/行业标准索引，或只启动 Backend 而忘记启动 Qwen。

## 1. 当前负责范围与系统链路

本分支负责交通预测、事件识别和 AI/Copilot。当前 Copilot 链路如下：

```text
用户问题
  → Backend /api/v1/simulations/{session_id}/copilot/chat
  → Qwen2.5-7B-Instruct（OpenAI 兼容 HTTP 接口）
  → 只读交通工具 / search_knowledge
  → 当前交通、历史、预测、事件和路网运行时数据
  → traffic knowledge RAG + standards/policy RAG
  → Qwen 组织中文回答
```

Qwen 不直接访问 SUMO/TraCI，也不能修改车辆、事件或信号控制。运行时交通事实以 Backend 返回的数据为准；RAG 只提供交通工程知识、项目静态事实和国家/行业标准依据。

当前代码还保留事件级 AI 信号接管能力：只有仿真启动时的事件配置 `ai_control_enabled=true` 才会进入接管流程，Qwen 只能生成受 Backend 和 SUMO worker 校验的目标相位。前端不属于本次启动脚本交接范围。

## 2. 模型、索引和端口

学校服务器当前使用的专属运行环境如下。路径属于服务器配置，不提交到 Git 的模型权重或 Chroma 二进制索引；若换服务器，只需在 `scripts/copilot/copilot.env` 中替换路径。

| 内容 | 当前位置/配置 |
| --- | --- |
| 学校服务器当前项目副本 | `/home/kemove/citypulse-runtime-20260904-feature-perception-cc983ec` |
| Qwen 权重 | `/home/kemove/devdata1/zyh_v2x_ai/models/Qwen2.5-7B-Instruct` |
| Embedding 权重 | `/home/kemove/devdata1/zyh_v2x_ai/deploy/models/Qwen3-Embedding-0.6B` |
| Traffic RAG 索引 | 当前项目 `outputs/rag/traffic_knowledge_chroma` |
| Traffic RAG 源 manifest | `traffic_knowledge/manifest.json` |
| Traffic RAG 可审阅切片 | `traffic_knowledge/build/chunks.jsonl` |
| Standards RAG 索引 | `/home/kemove/devdata1/zyh_v2x_ai/outputs/rag/standards_policy_chroma` |
| Standards RAG 源 manifest | `/home/kemove/devdata1/zyh_v2x_ai/outputs/rag_build_20260903_main_8efb0c5/source/standards/rag_manifest.json` |
| Standards RAG 可审阅切片 | `/home/kemove/devdata1/zyh_v2x_ai/outputs/rag_build_20260903_main_8efb0c5/source/standards/chunks.jsonl` |
| Qwen 服务 | `127.0.0.1:18000` |
| Backend 服务 | `127.0.0.1:8000` |
| Python | `/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python` |

当前 Traffic RAG 使用 `Qwen3-Embedding-0.6B`、1024 维向量，交通知识索引 344 条切片；Standards RAG 索引 236 条切片。Backend 配置了两个索引后，`search_knowledge` 才能按问题分别检索项目知识和国家/行业标准。

## 3. 推荐启动方式

在服务器上进入项目根目录后，只需要执行一次：

```bash
cp scripts/copilot/copilot.env.example scripts/copilot/copilot.env
# 第一次使用时检查并按实际机器修改 copilot.env 中的模型和索引路径
bash scripts/copilot/start_copilot.sh
```

`start_copilot.sh` 会依次完成：

1. 检查 Python 依赖、CUDA、Qwen 权重、Embedding 权重、traffic/standards 两套 manifest 和 Chroma 索引；
2. 比对索引的知识版本、代码修订号、Embedding 模型、1024 维度、collection 名称和切片数量；
3. 打开两个真实 Chroma collection，确认实际条数和 manifest 一致；
4. 启动并等待 Qwen `/v1/models`；
5. 启动并等待 Backend `/api/v1/health`；
6. 输出最终就绪状态。

任何索引缺失或过期都会在启动前失败；不会静默退回关键词检索，也不会让 Qwen 自行编造缺失的标准依据。
如果 8000/18000 已经被没有 PID 记录的旧进程占用，脚本默认拒绝接管，避免误用旧 Backend 或旧 Qwen；确认外部进程配置无误后，才可在 `copilot.env` 中显式设置 `CITYPULSE_ALLOW_UNMANAGED_SERVICES=1`。

常用命令：

```bash
# 查看模型、两套 RAG 和两个服务的状态
bash scripts/copilot/check_copilot.sh

# 单独启动 Qwen 或 Backend（Backend 要求 Qwen 已经就绪）
bash scripts/copilot/start_qwen.sh
bash scripts/copilot/start_backend.sh

# 停止本启动脚本管理的 Backend 和 Qwen；不会误杀其他用户进程
bash scripts/copilot/stop_copilot.sh
```

日志和 PID 文件位于 `outputs/.copilot_runtime/`，已加入 `.gitignore`。如果服务是人工启动的而没有 PID 文件，停止脚本只会提示，不会根据端口盲目杀进程。

## 4. 索引更新规则

Traffic RAG 的源文件是仓库中的 `traffic_knowledge/`。修改源 Markdown 或 manifest 后，在目标服务器上用服务器已有的 Embedding 权重重建：

```bash
"$CITYPULSE_PYTHON" scripts/rag/build_knowledge_index.py \
  --embedding-model-path "$RAG_EMBEDDING_MODEL_PATH" \
  --index-dir "$RAG_INDEX_DIR" \
  --device cpu
```

国家/行业标准使用独立的源准备和索引脚本，不要把标准 Chroma 目录复制到仓库或和 traffic index 混用。标准源发生变化时，先重新生成标准 `rag_manifest.json`/`chunks.jsonl`，再运行 `scripts/standards/build_standards_rag_index.py`。完成后必须重新运行：

```bash
bash scripts/copilot/check_copilot.sh
```

检查失败意味着索引与源 manifest 不一致，应先重建或修正配置，再进行问答测试。

## 5. Git 中应该提交什么

应该提交：

- `traffic_knowledge/` 下的 Markdown、manifest 和可审阅的 `build/chunks.jsonl`；
- `scripts/rag/`、`scripts/standards/` 和 `scripts/copilot/` 中的构建、校验、启动脚本；
- 本交接文档和问答测试记录。

不要提交：

- Qwen 或 Embedding 模型权重；
- Chroma 持久化目录、`outputs/` 下的运行产物；
- `scripts/copilot/copilot.env`、PID、日志和服务器私有路径配置。

本项目的提交目标是 `feature/perception`，不要直接推送到 `main`。提交前需要先抓取并检查最新 `origin/main`，合并主分支后再解决冲突和复测。

## 6. 当前测试证据

学校服务器上已经用真实 Qwen、真实 Embedding 和两套 Chroma 索引完成中文问答复测：

- 项目正式指标公式；
- 项目指标与国家/行业标准依据；
- 全网当前车流；
- 预测覆盖范围；
- 事故影响范围与事件上下文；
- 直接相连的上游/下游路口。

6 个问题均完成回答，完整问题和完整回答见[中文问答测试记录](copilot_chinese_qa_test_20260904.md)。后端单元测试 `test_copilot_llm.py`、`test_traffic_tools.py`、`test_rag.py` 共 `50 passed`。

当前已知的非阻塞问题：`free` 枚举的中文展示仍不够自然；项目指标与标准的逐项对应结论仍需要队长结合标准原文人工确认。两者都不应通过更换模型或复制旧索引来解决。

## 7. 本次上传前检查结果

2026-09-04 已执行 `git fetch origin main`。最新 `origin/main` 为：

```text
a82f716 fix backend od
```

该提交涉及 Backend Copilot 文档字符串、OD 导出、指标/场景导入兼容和新增 `scripts/export_od_heatmap.py` 等 9 个文件；当前 `feature/perception` 工作树尚未合并它。本次启动脚本和交接文档完成后，应先让负责人检查改动清单，再单独执行合并、冲突检查、测试、commit 和 push。
