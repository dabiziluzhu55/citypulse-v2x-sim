# 部署依赖草案说明

> 第一轮审计产出（2026-09-11）。**仅为草案，尚未替换现有 requirements。**

## 文件索引

| 文件 | 用途 |
|---|---|
| `backend.in` | Backend 生产直接依赖 |
| `backend-dev.in` | Backend 开发/测试（含 `-r backend.in`） |
| `sumo-worker.in` | SUMO Celery Worker 直接依赖 |
| `current-v2x-ai-py310-freeze.txt` | 当前 conda 环境完整 pip freeze（**追溯用，非生产安装清单**） |

## 五类环境对照

| 环境 | 依赖来源 | 备注 |
|---|---|---|
| **frontend** | `frontend/package.json` | 构建 Node 20；运行仅 nginx 静态资源 |
| **backend** | `backend.in` | CPU torch；无 SUMO |
| **sumo-worker** | `sumo-worker.in` | SUMO + Celery + traffic_control |
| **citypulse-qwen** | **不新建 .in** | 沿用已验证 vLLM 0.29.0 + CUDA 13 镜像；见下节 |
| **offline-training** | 待第二阶段 | `algorithms/traffic_llm/training/requirements.txt` + peft/trl/bitsandbytes |

## citypulse-qwen 已验证环境（勿重新发明）

| 项 | 固定值 |
|---|---|
| Python | 3.10.x |
| PyTorch | 2.13.0+cu130 |
| CUDA | 13.0 |
| vLLM | **0.29.0**（当前为本地 wheel 安装） |
| AWQ 加载 | `vllm serve <awq_dir> --quantization awq --dtype float16` |
| 模型挂载 | `/models/citypulse-qwen-v2-awq`（volume，禁止 COPY 5.2GB 进镜像） |
| 服务脚本 | `algorithms/traffic_llm/deployment/serve_vllm.py --mode awq` |
| served name | `traffic-qwen-v2` |
| 端口 | 8001 |

**推荐策略**：基于官方/已验证 **vLLM 0.29.0 CUDA 13** 基础镜像，仅添加 manifest 校验脚本；Python 依赖以镜像为准。

## 现有 requirements 迁移判定

### `backend/requirements.txt`

| 条目 | 判定 |
|---|---|
| fastapi, uvicorn, pydantic, pydantic-settings | **保留** → backend.in |
| numpy, torch | **保留** → backend.in（CPU 索引） |
| chromadb, sentence-transformers, transformers | **保留** → backend.in |
| reportlab, matplotlib, pyparsing, pillow | **保留** → backend.in |
| pyproj | **保留** → backend.in |
| shapely | **可删除**（backend/app 未 import；tests/simulation 用） |
| httpx | **移至 dev** |
| pytest | **移至 dev** |
| eclipse-sumo | **移至 sumo-worker** |

### 根目录 `requirements.txt`

全部条目 → **sumo-worker.in**（注释已说明勿放 backend）。

### `algorithms/traffic_llm/training/requirements.txt`

→ **offline-training**（peft, trl, bitsandbytes, datasets）。

## 前端 package.json 分类

### dependencies（`package.json`）

| 包 | 分类 | 生产引用 |
|---|---|---|
| vue, vue-router, element-plus, echarts, jszip | **browser runtime** | ✅ |
| ol | **browser runtime** | ✅ AppBackgroundMap 2D |
| @baidumap/mapv-three, three | **browser runtime** | ✅ BaiduThreeMap 3D（主路径） |
| cesium | **legacy candidate** | ❌ CesiumMap 未挂载到 App.vue |

### devDependencies

| 包 | 分类 |
|---|---|
| vite, @vitejs/plugin-vue, vue-tsc, typescript | **build time** |
| vite-plugin-cesium | **legacy candidate**（仍参与 vite build） |
| @gltf-transform/*, sharp, meshoptimizer, fast-xml-parser, polygon-clipping | **asset generation only**（scripts/*） |
| @types/* | **build time** |

### scripts/* 分类摘要

| 类型 | 示例 |
|---|---|
| asset generation | `generate:*`, `sync:mapvthree-assets` |
| audit | `audit:intersections`, `audit:road-*` |
| test only | `test:*`（Node built-in test runner） |

## 编译建议（第二阶段）

```bash
# 示例：使用 pip-tools
pip-compile deploy/requirements/backend.in -o deploy/requirements/backend.txt
pip-compile deploy/requirements/backend-dev.in -o deploy/requirements/backend-dev.txt
pip-compile deploy/requirements/sumo-worker.in -o deploy/requirements/sumo-worker.txt
```

Backend torch 安装示例（Dockerfile 片段）：

```dockerfile
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install -r deploy/requirements/backend.txt
```

## 相关文档

- 环境快照：`docs/deployment/current_environment_audit.md`
- 边界违规：`docs/deployment/dependency_boundary_audit.md`
