# 跨层依赖边界审计

> 生成时间：2026-09-11  
> 扫描范围：`backend/`、`simulation/`、`traffic_control/`、`traffic_eval/`、`algorithms/`、`scripts/`  
> 方法：AST 静态 import 扫描 + 人工复核

---

## 1. 违规 import 总表

说明：
- **是否允许**：按目标 5 容器架构（frontend / backend / sumo-worker / citypulse-qwen / offline-training）判定
- **建议迁移目标**：第二阶段 refactor 方向（本轮不实施）

### 1.1 backend → algorithms（离线/训练层）

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `backend/app/services/traffic_qwen_observation.py` | 9 | `algorithms.traffic_llm.dataset.feature_builder` | ❌ | 提取 `traffic_llm_observation/` 共享契约包，或迁到 `traffic_eval`/独立 whl |
| 同上 | 10 | `algorithms.traffic_llm.dataset.schema` | ❌ | 同上 |
| 同上 | 11 | `algorithms.traffic_llm.dataset.trace_collector` | ❌ | 同上 |
| 同上 | 109 | `algorithms.traffic_llm.dataset.catalog` | ❌ | 同上（lazy import） |
| 同上 | 125 | `algorithms.traffic_llm.dataset.catalog` | ❌ | 同上 |
| `backend/app/services/takeover_orchestrator.py` | 16 | `algorithms.traffic_llm.dataset.sft_builder.SYSTEM_PROMPT` | ❌ | 常量/Schema 下沉到 `backend/app/copilot/prompts.py` 或共享 `contracts` 包 |
| 同上 | 17 | `algorithms.traffic_llm.deployment.schema.PLAN_JSON_SCHEMA` | ❌ | JSON Schema 文件 + backend 只读加载 |
| 同上 | 18 | `algorithms.traffic_llm.evaluation.policy.POLICY_INSTRUCTION` | ❌ | 同上 |
| `backend/app/services/intelligence_runtime.py` | 13 | `algorithms.event_detection.cards` | ❌ | 提取 `event_detection` 为 backend 可依赖的轻量 runtime 包（无 torch/SUMO） |
| 同上 | 14 | `algorithms.event_detection.rules` | ❌ | 同上 |
| 同上 | 15 | `algorithms.event_detection.state` | ❌ | 同上 |

### 1.2 backend → traffic_control（算法实现/checkpoint 层）

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `backend/app/controllers/registry.py` | 11 | `traffic_control.registry` | ⚠️ | 允许 **协议/registry 常量**；当前会拉取 max_pressure/sotl 实现，需拆 `traffic_control.protocol` |
| `backend/app/controllers/max_pressure.py` | 7-8 | `traffic_control.max_pressure` | ❌ | Worker 侧执行；Backend 仅保留 mode 字符串 + HTTP/Redis 协议 |
| `backend/app/controllers/sotl.py` | 7-8 | `traffic_control.sotl` | ❌ | 同上 |
| `backend/app/scenario/resolver.py` | 58 | `traffic_control.ippo.aliases` | ❌ | checkpoint alias 元数据 JSON 化，backend 只读 manifest |
| 同上 | 68 | `traffic_control.mappo.aliases` | ❌ | 同上 |
| 同上 | 78 | `traffic_control.cov2x.aliases` | ❌ | 同上 |

**测试文件（同类违规，生产镜像不应包含）**：

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `backend/tests/test_traffic_control_package.py` | 27, 342-343, 485-486, 550-551 | `traffic_control.*` | ❌（test） | 保留在 backend-dev 测试；或迁为 worker 集成测试 |
| `backend/tests/test_scenario_aliases.py` | 7, 12, 40 | `traffic_control.ippo.aliases` | ❌（test） | 同上 |
| `backend/tests/test_mappo_scenario_aliases.py` | 7, 13 | `traffic_control.mappo/ippo.*` | ❌（test） | 同上 |

### 1.3 backend → SUMO / libsumo / sumolib

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `backend/app/core/sumo_env.py` | 29, 39 | `sumolib` | ❌（redis 模式） | local 模式可临时保留；redis 模式 backend 不应 configure SUMO_HOME |
| `backend/app/services/map_service.py` | 15, 30+ | `import_sumolib()` / sumolib net | ❌ | GeoJSON 预生成或 worker 提供 map API |
| `backend/app/core/config.py` | 199 | `simulation.sumo.engine.ai_control.AIControlConfig` | ⚠️ | 提取纯 dataclass 到 `contracts/` |
| `backend/app/services/simulation_service.py` | 11+ | `simulation.sumo.*` | ⚠️ | **架构性耦合**：local 模式必须；redis 模式仅需 codec/协议子集 |
| `backend/app/main.py` | 11 | `simulation.sumo.engine.distributed.RedisUnavailableError` | ⚠️ | 异常类下沉到 `citypulse_protocol` 包 |
| `backend/app/services/manager_factory.py` | 8-9 | `simulation.sumo.RedisSimulationManager` | ⚠️ | redis 客户端保留，SUMO session 逻辑留 worker |
| `backend/app/schemas/ai_control.py` | 9 | `simulation.sumo.engine.ai_control.*` | ⚠️ | 共享 schema 包 |
| `backend/app/services/takeover_orchestrator.py` | 11-14 | `simulation.sumo.engine.*` | ⚠️ | AI Control 编排可依赖 snapshot **DTO**，不依赖 engine |
| `backend/tools/eval.py` | 14 | 文档要求 libsumo | ❌（backend 工具） | 移至 sumo-worker 或 offline CLI |

**补充（backend → simulation，扫描规则外但同等重要）**：共 **20+** 处 runtime import `simulation.sumo`，是 Backend 无法完全无 SUMO 的**主因**（见 §3）。

### 1.4 traffic_eval → backend

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `traffic_eval/runner.py` | 272 | `backend.app.scenario.presets` | ❌ | 预设表下沉到 `scenario_presets/` 或 JSON catalog |

### 1.5 traffic_control → backend

| 结果 | 说明 |
|---|---|
| **未发现** | `traffic_control/` 未 import `backend.*` ✅ |

### 1.6 simulation → backend

| 文件 | 行号 | import | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `simulation/utils/convert_sumo_road_network.py` | 152 | `backend.app.core.config` | ❌ | 使用 `simulation` 本地 config 或 env vars |
| 同上 | 153 | `backend.app.core.sumo_env` | ❌ | 使用 `simulation/sumo` 自有 sumo_env |

### 1.7 frontend → 旧 Cesium

| 文件 | 行号 | 引用 | 是否允许 | 建议迁移目标 |
|---|---:|---|---|---|
| `frontend/vite.config.ts` | 8, 79 | `vite-plugin-cesium` | ⚠️ legacy | 生产已切 Baidu mapv-three；可移 devDependencies |
| `frontend/src/composables/useAppMapView.ts` | 3, 41 | `import type { Viewer } from 'cesium'` | ⚠️ legacy | 删除 cesiumRef 或改 three 类型 |
| `frontend/src/components/visualization/CesiumMap.vue` | 3-4 | `import * as Cesium from 'cesium'` | ❌ legacy | **未挂载到 App.vue**；candidate 归档 |
| `frontend/src/components/visualization/AppBackgroundMap.vue` | 26 | `cesium/traffic/TrafficModelRegistry` | ⚠️ | 共享常量文件，非 Cesium runtime |
| `frontend/src/cesium/traffic/*` | — | Cesium 渲染器 | ❌ legacy | 3D 生产路径已用 `BaiduThreeMap.vue` |
| `frontend/package.json` | 72-73, 94 | `cesium`, `vite-plugin-cesium` dependencies | ⚠️ legacy | 标记 legacy candidate；build 仍打包 |

**生产路径确认**：`App.vue` 仅挂载 `AppBackgroundMap`（OpenLayers 2D）+ `AppThreeMapLoader` → `BaiduThreeMap`（mapv-three/three）。**CesiumMap 无 router/组件引用**。

---

## 2. 按服务归类的第三方 import 摘要

### backend（`backend/app` runtime，不含 tests）

| 包 | import 文件数 | 在 backend/requirements？ | 备注 |
|---|---:|---|---|
| fastapi | 19 | ✅ | 核心 |
| pydantic / pydantic_settings | 11 / 1 | ✅ | 核心 |
| reportlab | 8 | ✅ | PDF 报告 |
| torch | 5 | ✅ | NarrowNet + RAG（应 CPU 版） |
| matplotlib | 4 | ✅ | OD 热力图主路径 |
| numpy | 4 | ✅ | 指标/预测 |
| chromadb | 2 | ✅ | RAG |
| redis | 2 | ❌（在根 requirements） | 应写入 backend.in |
| sumolib | 2 | ⚠️ eclipse-sumo | **违规**：redis 模式不应需要 |
| PIL | 2 | ❌ | OD fallback，应补 pillow |
| sentence_transformers / transformers | 1 / 1 | ✅ | RAG embedding |
| httpx | 1 | ✅ | 仅 copilot/llm 测试路径？实际 llm 用 urllib |

### sumo-worker（simulation + traffic_control，非 test）

| 包 | 用途 |
|---|---|
| traci / libsumo / sumolib | SUMO 仿真引擎 |
| celery / redis | 分布式 worker |
| numpy / scipy | 特征构建、routeSampler |
| torch | IPPO/MAPPO/CoV2X 推理（默认 CPU） |
| pyproj / shapely | 路网转换工具 |

### citypulse-qwen

| 包 | 用途 |
|---|---|
| vllm 0.29.0 | OpenAI-compatible API |
| torch 2.13+cu130 | vLLM 运行时 |
| transformers | tokenizer/processor（随 vLLM 镜像） |

---

## 3. Backend 能否理论上完全无 SUMO？

| 模式 | 现状 | 理论可行？ |
|---|---|---|
| **redis 模式（目标生产）** | API 仍 import simulation/session codec、map_service 用 sumolib、local 模式同进程跑 SimulationManager | **可以，但需第二阶段 refactor** |
| **local 模式（开发）** | Backend 进程内直接跑 SUMO | 故意耦合，容器化后应废弃 |

**解除 SUMO 的最小路径**：
1. 提取 `simulation_protocol`（Snapshot/Config/Event DTO + Redis codec），backend 只依赖 DTO
2. `MapService` 改为读取预生成 GeoJSON（frontend 已有静态资产管线）
3. redis 模式删除 `configure_sumo_home()` 启动路径
4. `backend/tools/eval.py` 迁到 worker CLI

---

## 4. torch/CUDA 边界（源码证据）

### Backend

| 模块 | 设备逻辑 | GPU 必须？ |
|---|---|---|
| `prediction_runtime.py` | `torch.device("cpu")` 默认；`map_location` CPU | **否** |
| `copilot/rag.py` | `device=auto` → 有 CUDA 则用 GPU | **否**（CPU 足够） |

### SUMO Worker（IPPO / MAPPO / CoV2X）

| 模块 | 设备逻辑 | GPU 必须？ |
|---|---|---|
| `traffic_control/ippo/controller.py` | `torch.load(..., map_location="cpu")` | **否** |
| `traffic_control/mappo/controller.py` | 同上 | **否** |
| `traffic_control/cov2x/*/policy.py` | `device: str = "cpu"` 默认 | **否** |
| `traffic_control/cov2x/runtime/mvp_runtime.py` | `map_location="cpu"` | **否** |

### citypulse-qwen

| 项 | 值 |
|---|---|
| vLLM + AWQ | 必须 GPU |
| 当前验证组合 | Python 3.10 + torch 2.13+cu130 + CUDA 13 + vLLM 0.29.0 |

**推荐默认值**：Backend **CPU torch**；SUMO Worker **CPU torch**；仅 citypulse-qwen 占 GPU。

---

## 5. 容器依赖矩阵

| 服务 | Python/Node | 系统依赖 | Python/npm 直接依赖（草案） | 模型/数据 Volume | GPU | 端口 | 与其他服务通信 |
|---|---|---|---|---|---|---|---|
| **frontend** | Node 20 | nginx | vue, vue-router, element-plus, echarts, ol, @baidumap/mapv-three, three, jszip | 静态 tileset/GLB（`frontend/public`） | 否 | 80/443 | → backend HTTPS/WS |
| **backend** | Python 3.10 | 字体（PDF/OD 中文） | fastapi, uvicorn, pydantic, redis, numpy, torch(CPU), chromadb, sentence-transformers, transformers, reportlab, matplotlib, pillow, pyproj | RAG index volume, NarrowNet checkpoint volume | 否 | 8000 | ↔ redis；→ citypulse-qwen HTTP；↔ frontend |
| **sumo-worker** | Python 3.10 | SUMO 1.20+（建议统一 1.20/1.27 单版本） | celery[redis], redis, numpy, scipy, torch(CPU), eclipse-sumo, traffic_control, simulation | SUMO network/session 输出目录, RL checkpoints | 否（默认） | —（Celery worker） | ↔ redis；← backend 命令；→ backend algorithm HTTP（可选） |
| **redis** | — | — | redis:7-alpine | AOF/RDB persist volume | 否 | 6379/6380 | ↔ backend, sumo-worker |
| **citypulse-qwen** | Python 3.10 | NVIDIA driver + CUDA 13 runtime | **vLLM 0.29.0 镜像**（含 torch cu130） | `/models/citypulse-qwen-v2-awq` | **是** | 8001 | ← backend HTTP OpenAI API |

---

## 6. 冗余 / 隐藏 / 跨层依赖清单

| 类型 | 项 | 说明 |
|---|---|---|
| **冗余** | PyPI `sumo==2.4.0.post1` | 与 Eclipse SUMO 冲突，破坏 conda `sumo` 二进制 |
| **冗余** | pymatgen, spglib | 与业务无关 |
| **冗余** | torchvision, torchaudio | backend/worker 推理不需要 |
| **冗余** | 双 CUDA 栈（cu12+cu13 nvidia 包） | 来自多次 torch/vllm 安装 |
| **隐藏** | matplotlib → contourpy/cycler/fonttools | pip check 已报缺失 |
| **隐藏** | map_service → sumolib → pyproj | backend 声明 pyproj 但错误信息指向 SUMO 链 |
| **隐藏** | sentence-transformers/chromadb 在 `~/.local` | 非 conda 管理，容器化易丢失 |
| **跨层** | backend controllers → traffic_control 实现 | 违反 worker 专属算法层 |
| **跨层** | backend → algorithms.traffic_llm.dataset | 训练数据管线渗入 API |
| **跨层** | traffic_eval → backend.app.scenario.presets | 共享库反向依赖 API 层 |

---

## 7. 第二阶段建议优先级

1. **P0**：提取 `simulation_protocol` DTO，backend redis 模式去除 sumolib
2. **P0**：requirements 拆分 + 去除 backend 中 pytest/eclipse-sumo
3. **P1**：AI Control 观测/Schema 从 `algorithms/` 提取 contracts 包
4. **P1**：traffic_control registry 协议与实现分离
5. **P2**：frontend 移除 Cesium dependencies（确认无回归后）
6. **P2**：统一 SUMO 单版本（消除 1.12/1.27 混用）
