# CityPulse V2X Sim

基于SUMO的交通管控仿真平台

## 目录

| 目录 | 说明 |
|------|------|
| `frontend/` | Vue前端，通过REST/WebSocket调用后端 |
| `backend/` | FastAPI后端，不直接调用仿真TraCI |
| `simulation/` | SUMO/libsumo仿真内核与分布式Worker |
| `traffic_control/` | 产品管控算法包（fixed/sotl/max_pressure/ippo/mappo） |
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

**仿真与算法分离**：`simulation/` 在生产环境独占进程内libsumo；Max Pressure、IPPO和多路口
强化学习通过HTTP/JSON协议2.0接收路口、单车及油耗状态，并返回官方目标相位、
单车目标速度和换道请求。后端只转发snapshot，前端不直接连接SUMO。TraCI仅保留给
本地`sumo-gui`调试。

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
PYTHONPATH=. uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1
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

### 5. 多会话(redis模式)

```bash
docker compose -f compose.redis.yml up -d
# Backend设 SIMULATION_MANAGER_MODE=redis
celery -A simulation.sumo.engine.distributed.celery_app:app worker \
  --queues citypulse-sumo --pool prefork --concurrency 4
```

Worker使用prefork，一子进程同时只跑一个SUMO会话;与后端Backend共享`generated`与`outputs/sessions`

## 容器化部署

| 容器 | 内容 |
|------|------|
| frontend | 静态资源/Nginx |
| backend | FastAPI+CPU torch（NarrowNet-TDP预测），无SUMO |
| sumo-worker | `simulation`+`traffic_control`+SUMO(+torch) |
| redis | 队列与会话状态 |

`traffic_control`由仿真的Worker进程内加载

## 文档

- 后端接口与配置:[backend/README.md](backend/README.md)
- 仿真核心API:[docs/simulation_core_api.md](docs/simulation_core_api.md)
- 分布式Worker:[docs/distributed_simulation.md](docs/distributed_simulation.md)
- 算法协议2.0:[docs/algorithm_interface.md](docs/algorithm_interface.md)
- 车流与OD:[docs/traffic_demand.md](docs/traffic_demand.md)
- 环境依赖:[docs/setup.md](docs/setup.md)
