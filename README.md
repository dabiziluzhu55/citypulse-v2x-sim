# CityPulse V2X Sim

基于SUMO的交通管控仿真平台

## 目录

| 目录 | 说明 |
|------|------|
| `frontend/` | Vue前端，通过REST/WebSocket调用后端 |
| `backend/` | FastAPI后端，不直接调用仿真TraCI |
| `simulation/` | SUMO/libsumo仿真内核与分布式Worker |
| `traffic_control/` | 产品管控算法包（fixed/sotl/max_pressure/ippo/mappo/cov2x） |
| `traffic_intelligence/` | 运行时事件识别与交通状态（Backend部署包） |
| `traffic_eval/` | 部署侧公共交通评估口径（Backend与命令行共用） |
| `algorithms/` | 算法组训练与实验代码，不参与项目的部署 |
| `data/maps/` | 地图与SUMO生成产物 |
| `docs/` | 详细文档 |

## 项目架构

```text
[Frontend] ──HTTP──► [Backend API] ──Redis──► [SUMO Worker ×N]
                              │                    │
                    traffic_eval（结算指标）    libsumo + simulation
                         会话元数据              + traffic_control（决策）
                              │                    │
                              └──── 共享session目录 / tripinfo.xml ────┘
```

- 前端只传业务名`control_mode`，不直接调用算法、SUMO
- 后端根据`traffic_control.registry`写成`SimulationConfig`(如`algorithm_module=traffic_control.sotl`)
- 仿真端只认`fixed`或`algorithm`，本地算法由`algorithm_module`动态加载
- 评估脚本在`traffic_eval/`（Backend封装；算法可直接import，无需启动后端）
- `traffic_eval`：**Backend 容器封装**；sumo容器也需封装以便本地命令工具复用同一包，但Worker的仿真进程不负责指标计算

- IPPO等含torch的管控推理只在SUMO Worker进程内运行；Backend可带CPU torch用于NarrowNet-TDP短时交通预测

### 管控模式

| control_mode | 说明 |
|--------------|------|
| `fixed` | SUMO固定配时 |
| `sotl` | SOTL，本地Protocol 2.0 |
| `max_pressure` | Max Pressure，本地Protocol 2.0 |
| `ippo` | 部署版IPPO，仅`xiongan_20`，默认加载包内checkpoint |
| `mappo` | 部署版MAPPO，多路口协同 |
| `cov2x` | 部署版CoV2X，车路协同 |

**仿真与算法分离**：`simulation/` 在生产环境独占进程内 libsumo；所有管控算法由 SUMO Worker 按 `traffic_control.registry` 动态加载，经本地 Protocol 2.0（`initialize` / `step` / `finish`）在 Worker 进程内执行。Backend 只管理会话与指标，不实例化 Controller、不加载 checkpoint。TraCI 仅保留给本地 `sumo-gui` 调试。

## 快速开始

克隆后请先拉取 Git LFS 大文件（官方路网 `TotalMap_20.net.xml`）：

```bash
git lfs install
git lfs pull
```

仿真启动依赖官方源数据目录（已入库，勿再忽略）：

```text
data/maps/sumo/official/
├── map/TotalMap_20.intersections.json
├── map/TotalMap_20.net.xml
├── tls/official_tls_plans.json
├── tls/official_tls_topology.json
└── traffic/...
```

若缺少上述文件，前端会在「算法初始化」阶段失败并报 `Configuration file not found`。

### 1. 构建路网与车流

仓库已包含可用的 `data/maps/sumo/generated/` 时，本地联调可跳过本步。需要重建时：

```bash
export SUMO_HOME=/usr/share/sumo
cd /path/to/citypulse-v2x-sim
python -m simulation.sumo.building.build_tls
python -m simulation.sumo.building.build_traffic
```

### 2. 安装依赖

```bash
pip install -r backend/requirements.txt
pip install -r requirements.txt          # SUMO Worker(含torch等)
```

### 3. 启动Backend(local调试)

```bash
PYTHONPATH=. uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

前端:

```bash
cd frontend && npm install && npm run dev
```

### 3b. 无 Backend 本机评估（算法团队）

```bash
PYTHONPATH=. python -m traffic_eval \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_local.json
```

浏览器打开前端后选择场景与`control_mode`即可启动仿真

### 4. 仅CLI跑SUMO

```bash
python -m simulation.sumo.engine.run --mode fixed --intersection demo_2 --period morning_peak
python -m simulation.sumo.engine.run --mode algorithm \
  --algorithm-transport local \
  --algorithm-module traffic_control.sotl \
  --intersection demo_2 --period morning_peak

python -m simulation.sumo.engine.run --mode fixed \
  --intersection demo_2 --period morning_peak

# 需要观察 SUMO 原生窗口时使用本地 GUI 调试旁路
python -m simulation.sumo.engine.run --gui --realtime --mode fixed \
  --intersection demo_2 --period morning_peak
```

构建命令还会使用 SUMO routeSampler 联合拟合 20 个路口，按
`data/maps/sumo/traffic/traffic_generation_policy.json` 为不同车型筛选长短途候选路线，生成早高峰、
平峰和晚高峰 3 个全局真实车流场景，并按最终车流的起止区域生成对应的九区域 OD PCU 矩阵。
数据口径、总量校验和场景切换见 [docs/sumo.md](docs/sumo.md) 的构建部分。

后端可调用的会话、时间窗口、局部管控范围、交通倍率和扰动事件接口见
[docs/sumo.md](docs/sumo.md)。

### CARLA+SUMO联合仿真

```bash
docker compose -f compose.redis.yml up -d
# Backend设 SIMULATION_MANAGER_MODE=redis
celery -A simulation.sumo.engine.distributed.celery_app:app worker \
  --queues citypulse-sumo --pool prefork --concurrency 4
```

Worker使用prefork，一子进程同时只跑一个SUMO会话;与后端Backend共享`generated`与`outputs/sessions`

## 容器化部署（5 容器）

生产部署先阅读 [边界、静态地图与运行时配置](docs/deployment/production_boundaries.md)。
Redis Backend 不加载 SUMO 内核；道路 GeoJSON 在 SUMO 环境离线生成。
浏览器地图 Key 由容器启动时的 `runtime-config.js` 提供，服务端 secrets 单独注入。

| 容器 | 内容 |
|------|------|
| frontend | Node build + Nginx（同源 `/api/` 反代） |
| backend | FastAPI + Redis/Celery client + traffic_eval + Narrow-TDP + RAG；**无 SUMO、无 checkpoint** |
| sumo-worker | SUMO/libsumo + Celery worker + simulation + traffic_control + 算法 checkpoint |
| redis | 官方 Redis（会话状态 + Celery broker） |
| citypulse-qwen | vLLM + CityPulse-Qwen V2 AWQ（GPU） |

`traffic_control`由仿真的Worker进程内加载

当前仓库提供 `deploy/compose.acceptance.yml`，用于 Frontend、Backend、SUMO Worker 和 Redis 的独立 SSH 验收部署；`compose.redis.yml` 保留用于原分布式运行方式。frontend 镜像由 `frontend/Dockerfile` 构建静态资源，**不要把 `roadside_media/` 打进镜像**。路侧 MP4 以只读 volume 挂载：

```text
./roadside_media/encoded  →  /usr/share/nginx/html/roadside-media:ro
```

浏览器通过 `/roadside-media/demo_14.mp4` 等相对路径访问。转码与挂载说明见 [roadside_media/README.md](roadside_media/README.md)。

## 文档

- 后端接口与配置:[backend/README.md](backend/README.md)
- 仿真核心API:[docs/simulation_core_api.md](docs/simulation_core_api.md)
- 分布式Worker:[docs/distributed_simulation.md](docs/distributed_simulation.md)
- 算法协议2.0:[docs/algorithm_interface.md](docs/algorithm_interface.md)
- 车流与OD:[docs/traffic_demand.md](docs/traffic_demand.md)
- 环境依赖:[docs/setup.md](docs/setup.md)

### 部署交付（develop / fix3）

- [详细部署运行说明](docs/deployment/deployment-runbook.md)
- [部署版改动与优化说明](docs/deployment/deployed-vs-original-change-summary.md)
- [正确性与交通流优化验证](docs/deployment/fix3-correctness-and-traffic-optimization.md)
- 算法执行源码位于 `traffic_control/`、`algorithms/`，本次交通流生成优化位于 `simulation/sumo/building/build_traffic.py`。既有算法源码随分支保留，未改动的算法不重复复制。
- 真实地图 Key、认证文件、模型和大体积运行数据不随本次提交新增；恢复运行所需交接项见部署说明第 8 节。
