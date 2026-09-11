# v2x-ai-py310 当前环境审计（冻结快照）

> 生成时间：2026-09-11  
> 环境路径：`/home/kemove/anaconda3/envs/v2x-ai-py310`  
> **本文件仅记录现状，未对环境做任何修改。**

---

## 1. 基础信息

| 项 | 值 |
|---|---|
| Python | **3.10.20** |
| Python 路径 | `/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python` |
| Conda 环境 | `v2x-ai-py310` |
| NVIDIA Driver | **580.178.04** |
| CUDA（驱动报告） | **13.0** |
| GPU | 2× NVIDIA GeForce RTX 4090（GPU0 约 21GB 被 vLLM 占用） |
| pip 包总数 | **336**（见 `deploy/requirements/current-v2x-ai-py310-freeze.txt`） |
| conda 包总数 | **315**（见 `_conda_list.txt`） |
| pipdeptree | **未安装**（无法生成依赖树） |

### SUMO 状态（存在版本/命名冲突）

| 来源 | 路径/版本 | 状态 |
|---|---|---|
| conda `which sumo` | `/home/kemove/anaconda3/envs/v2x-ai-py310/bin/sumo` | **损坏**：与 PyPI 包 `sumo==2.4.0.post1` 命名冲突，`ImportError: cannot import name 'sumo' from 'sumo'` |
| 系统 SUMO | `SUMO_HOME=/usr/share/sumo`，**Eclipse SUMO 1.12.0** | `sumo --version` 正常 |
| pip eclipse-sumo | **1.27.1** | 已安装，但与系统 1.12.0 / traci 1.12.0 混用 |
| traci | 1.12.0 | `import traci` 成功 |
| libsumo | 1.12.0 | 直接 `import libsumo` **失败**（循环导入，路径混用） |

**结论**：当前 SUMO 工具链处于 **三源混装**（系统 1.12.0 + pip eclipse-sumo 1.27.1 + 错误 PyPI `sumo` 包），是容器化前必须解耦的高风险项。

---

## 2. 命令输出摘要

### `python --version`
```
Python 3.10.20
```

### `which python`
```
/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python
```

### `which sumo`
```
/home/kemove/anaconda3/envs/v2x-ai-py310/bin/sumo   # 损坏，见上表
```

### `nvidia-smi`（摘要）
- Driver 580.178.04，CUDA 13.0
- GPU0：RTX 4090，21713MiB 使用中（`VLLM::EngineCore` + python 进程）
- GPU1：RTX 4090，基本空闲

---

## 3. 关键已安装包版本（运行时探测）

| 包 | 版本 | 安装位置 | 主要归属服务 |
|---|---|---|---|
| torch | **2.13.0+cu130** | conda site-packages | 全环境混用（Backend CPU 推理 + Worker 算法 + 训练） |
| vllm | **0.29.0** | conda（本地 wheel 安装） | citypulse-qwen |
| transformers | **5.17.0** | conda | Backend RAG + offline-training |
| sentence-transformers | **6.0.1** | `~/.local` | Backend RAG |
| chromadb | **1.5.9** | `~/.local` | Backend RAG |
| fastapi | **0.136.3** | conda | backend |
| uvicorn | **0.52.4** | conda | backend |
| pydantic | **2.13.5** | conda | backend |
| celery | **5.6.3** | conda | sumo-worker |
| redis | **6.4.0** | conda | backend（客户端）+ sumo-worker |
| numpy | **2.2.6** | conda | 全栈 |
| scipy | **1.15.3** | conda | sumo-worker（routeSampler）+ offline-training |
| matplotlib | **3.10.9** | conda | backend OD 热力图 + offline |
| reportlab | **5.0.1** | conda | backend 评估报告 PDF |
| pillow | **12.3.0** | conda | backend OD 热力图 fallback |
| pyproj | **3.7.1** | conda | backend map_service（经 sumolib 投影链） |
| eclipse-sumo | **1.27.1** | conda | 声明在 backend/requirements，实际应属 worker |
| peft / trl / bitsandbytes | 0.17.1 / 0.22.2 / 0.48.0 | conda | offline-training |
| autoawq | — | **未安装** | AWQ 量化走 vLLM 内置 `--quantization awq` |
| pytest | **9.1.1** | conda | dev/test（不应进生产 backend 镜像） |

完整列表：`deploy/requirements/current-v2x-ai-py310-freeze.txt`  
Conda 列表：`docs/deployment/_conda_list.txt`

---

## 4. `pip check` 结果

```
pymatgen 2025.10.7 requires plotly, which is not installed.
matplotlib 3.10.9 requires contourpy, which is not installed.
matplotlib 3.10.9 requires cycler, which is not installed.
matplotlib 3.10.9 requires fonttools, which is not installed.
matplotlib 3.10.9 requires kiwisolver, which is not installed.
```

**解读**：
- `pymatgen` / `spglib` 等 **材料科学包** 与项目业务无关，属于环境噪声。
- `matplotlib` 运行时可能走 fallback（backend OD 导出已有 Pillow 路径），但生产镜像应显式补齐 matplotlib 传递依赖或固定 Agg 路径。

---

## 5. 环境混装分类（336 pip 包 → 5 类映射）

当前 `v2x-ai-py310` 至少混装了以下 **6 类** 本应对立的环境：

| 类别 | 代表包 | 当前状态 | 目标容器 |
|---|---|---|---|
| **A. Backend API** | fastapi, uvicorn, pydantic, redis(client), chromadb, sentence-transformers, transformers, reportlab | 与 SUMO/vLLM 同环境 | `backend` |
| **B. SUMO Worker** | traci, libsumo, sumolib, eclipse-sumo, celery, scipy, traffic_control+simulation | 与 FastAPI 同进程可导入 | `sumo-worker` |
| **C. CityPulse-Qwen** | vllm 0.29.0, torch 2.13+cu130, 大量 nvidia-* / cuda-* | 占 GPU0 ~21GB | `citypulse-qwen` |
| **D. Offline Training** | peft, trl, bitsandbytes, datasets, 完整 algorithms/ | 与线网服务混装 | `offline-training` |
| **E. Frontend Build** | （Node 独立，但 server 上 Python 脚本混用 sumolib） | 部分 asset 脚本依赖 Python+SUMO | 构建机 / CI |
| **F. 环境噪声** | pymatgen, spglib, 错误 `sumo` PyPI 包, 双版本 CUDA 库 (cu12+cu13) | 无业务价值 | **不应进入任何生产镜像** |

### CUDA/Torch 边界现状

| 组件 | 当前 torch | 是否需要 GPU | 目标 |
|---|---|---|---|
| Backend NarrowNet-TDP | GPU 版 torch 可用，代码默认 **CPU** | **否**（`map_location`/默认 cpu） | CPU torch |
| Backend RAG Embedding | GPU 版 torch，`device=auto` 会选 cuda | **可选**（CPU 足够，延迟更高） | 默认 CPU |
| SUMO Worker IPPO/MAPPO/CoV2X | GPU 版 torch，checkpoint `map_location="cpu"`，config `device="cpu"` | **否**（推理默认 CPU） | CPU torch |
| CityPulse-Qwen vLLM AWQ | CUDA 13 + vLLM 0.29.0 | **是** | 独占 GPU |

---

## 6. CityPulse-Qwen 已验证环境（来自源码 + manifest）

| 项 | 值 |
|---|---|
| Python | 3.10.x |
| PyTorch | 2.13.0+cu130 |
| CUDA | 13.0（驱动 580.x） |
| vLLM | **0.29.0**（本地 wheel：`/tmp/vllm_wheels/vllm-0.29.0-...whl`） |
| 服务入口 | `algorithms/traffic_llm/deployment/serve_vllm.py --mode awq` |
| AWQ 加载 | `--quantization awq --dtype float16`（**非** autoawq 包） |
| 模型目录 | `outputs/traffic_llm_deployment/traffic_qwen_v2_awq`（**volume 挂载**，约 5.2GB，禁止 COPY 进镜像） |
| served-model-name | `traffic-qwen-v2` |
| 默认端口 | 8001 |
| attention backend | FLASH_ATTN |
| manifest | `algorithms/traffic_llm/deployment/model_manifest.json`（status=frozen） |

**建议**：citypulse-qwen 镜像优先基于 **已验证 vLLM 0.29.0 官方/自建 CUDA 13 基础镜像**，仅挂载 AWQ 权重与 manifest，不重新发明 requirements。

---

## 7. 当前 requirements 文件问题摘要

### `backend/requirements.txt`

| 包 | 判定 | 说明 |
|---|---|---|
| fastapi, uvicorn, pydantic, pydantic-settings | **必须保留** | Backend 核心 |
| httpx | **移至 backend-dev** | 仅测试/工具使用，runtime llm 用 urllib |
| pytest | **移至 backend-dev** | 不应在生产 requirements |
| reportlab, pillow, matplotlib | **必须保留** | 评估 PDF + OD 热力图 |
| pyproj | **必须保留** | map_service 坐标投影链 |
| shapely | **保留或下沉** | backend/app 未直接 import；tests 使用，simulation 工具使用 |
| eclipse-sumo | **移至 sumo-worker** | Backend redis 模式不应依赖 SUMO；但当前代码仍 import sumolib |
| numpy, torch | **保留（CPU 索引）** | NarrowNet + RAG |
| chromadb, sentence-transformers, transformers | **必须保留** | Copilot RAG |

### 根目录 `requirements.txt`

| 包 | 判定 |
|---|---|
| numpy, scipy, celery[redis], redis, torch | **全部移至 sumo-worker.in**（注释已说明勿放 backend） |

### `frontend/package.json`

见 `deploy/requirements/README.md` 前端章节；Cesium 仍在 dependencies 但 **生产 App.vue 未挂载 CesiumMap**。

---

## 8. 隐藏依赖核查

| 包 | backend runtime | sumo-worker | citypulse-qwen | offline | 在 requirements？ |
|---|---|---|---|---|---|
| redis | ✅ 客户端（history/metadata） | ✅ store | — | — | 根 requirements |
| celery | ❌ | ✅ worker | — | — | 根 requirements |
| torch | ✅ NarrowNet+RAG | ✅ IPPO/MAPPO/CoV2X | ✅ vLLM 依赖 | ✅ 训练 | backend+根 |
| numpy | ✅ | ✅ | — | ✅ | 多处 |
| scipy | ❌ | ✅ build_traffic | — | ✅ prediction 训练 | 根 requirements |
| sumolib/traci/libsumo | ⚠️ sumo_env/map_service | ✅ session/runtime | — | 部分测试 | eclipse-sumo 在 backend req |
| chromadb | ✅ rag.py | ❌ | — | ✅ index 构建脚本 | backend req |
| sentence-transformers | ✅ rag.py | ❌ | — | ✅ scripts | backend req |
| transformers | ✅ rag fallback | ❌ | ✅ | ✅ QLoRA | backend req |
| vllm | ❌（HTTP 客户端） | ❌ | ✅ | 部署脚本 | **无 requirements 文件** |
| autoawq | ❌ | ❌ | ❌（vLLM 内置） | 量化脚本可能间接 | 未安装 |
| matplotlib/reportlab/Pillow | ✅ od_export/eval_report | ❌ | — | scripts | backend req |
| httpx | ❌ runtime | ❌ | — | — | backend req（应移 dev） |
| pytest | tests only | 部分 tests | — | 大量 tests | backend req（应移 dev） |

---

## 9. 跨层耦合（高层摘要）

详见 `docs/deployment/dependency_boundary_audit.md`。

**最严重**：
1. Backend 深度 import `simulation.sumo.*`（local + redis 模式共用数据模型）
2. Backend import `algorithms.traffic_llm.dataset.*`（AI Control 观测构建）
3. Backend import `traffic_control.*`（控制器注册表 + 场景 alias 解析）
4. Backend `sumo_env.py` import `sumolib`（redis 模式 API 容器仍可能触发）
5. `traffic_eval` import `backend.app.scenario.presets`（反向耦合）

---

## 10. 附件

| 文件 | 说明 |
|---|---|
| `deploy/requirements/current-v2x-ai-py310-freeze.txt` | 完整 pip freeze（追溯用） |
| `docs/deployment/_conda_list.txt` | conda list 原始输出 |
| `docs/deployment/_pip_check.txt` | pip check 原始输出 |
| `docs/deployment/_scan_result.json` | 自动 import 扫描 JSON |

---

## 11. 本轮未执行项（按用户要求）

- ❌ conda remove / pip uninstall
- ❌ 重建 v2x-ai-py310
- ❌ 修改业务逻辑 / 删代码
- ❌ 创建正式 Docker 镜像 / docker-compose
