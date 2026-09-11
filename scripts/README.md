# scripts/ 目录分类

| 目录/文件 | 分类 | 进入部署镜像？ |
|---|---|---|
| `copilot/` | deployment/admin（开发启动、preflight） | 否 |
| `rag/` | offline-build（知识索引构建） | 否 |
| `standards/` | offline-build（标准文档 RAG） | 否 |
| `archive/llm/` | legacy/archive（旧 Transformers Qwen） | 否 |
| `archive/copilot/` | legacy/archive | 否 |
| `archive/prediction/` | legacy/archive（旧 STGCN 训练链） | 否 |
| `build_roadside_media.sh` | offline-build（路边媒体编码） | 否 |
| `export_od_heatmap.py` | offline/admin | 否 |
| `train_official20_lane_v1.sh` | training → 见 `algorithms/` | 否 |

生产推理由 **citypulse-qwen** 容器（vLLM）负责；`start_copilot.sh` 仅 DEV，不再自动拉起旧 Qwen server。
