# CityPulse V2X Sim

基于SUMO的交通管控仿真平台

## 目录

| 目录 | 说明 |
|------|------|
| `frontend/` | Vue前端，通过REST/WebSocket调用后端 |
| `backend/` | FastAPI后端，不直接调用仿真TraCI |
| `simulation/` | SUMO/TraCI仿真内核与分布式Worker |
| `traffic_control/` | 产品管控算法包，目前包括(fixed/sotl/max_pressure/ippo) |
| `algorithms/` | 算法组训练与实验代码，不参与项目的部署 |
| `data/maps/` | 地图与SUMO生成产物 |
| `docs/` | 详细文档 |

## 项目架构

```text
前端 --HTTP/WS--> 后端 --配置/队列--> SUMO Worker
                                      ├─ simulation(TraCI)
                                      └─ importlib加载 traffic_control.*
```

- 前端只传业务名`control_mode`，不直接调用算法、SUMO
- 后端根据`traffic_control.registry`写成`SimulationConfig`(如`algorithm_module=traffic_control.sotl`)
- 仿真端只认`fixed`或`algorithm`，本地算法由`algorithm_module`动态加载
- IPPO等含torch的推理只在SUMO Worker进程内运行，Backend启动不导入torch

### 管控模式

| control_mode | 说明 |
|--------------|------|
| `fixed` | SUMO固定配时 |
| `sotl` | SOTL，本地Protocol 2.0 |
| `max_pressure` | Max Pressure，本地Protocol 2.0 |
| `ippo` | 部署版IPPO，仅`xiongan_20`，默认加载包内checkpoint |

## 快速开始

### 1. 构建路网与车流

```bash
export SUMO_HOME=/usr/share/sumo
cd /path/to/citypulse-v2x-sim
python -m simulation.sumo.build_tls
python -m simulation.sumo.build_traffic
```

### 2. 安装依赖

```bash
pip install -r backend/requirements.txt
pip install -r requirements.txt          # SUMO Worker(含torch等)
```

### 3. 启动Backend(local调试)

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

前端:

```bash
cd frontend && npm install && npm run dev
```

浏览器打开前端后选择场景与`control_mode`即可启动仿真

### 4. 仅CLI跑SUMO

```bash
python -m simulation.sumo.run --mode fixed --intersection demo_2 --period morning_peak
python -m simulation.sumo.run --mode algorithm \
  --algorithm-transport local \
  --algorithm-module traffic_control.sotl \
  --intersection demo_2 --period morning_peak
```

### 5. 多会话(redis模式)

```bash
docker compose -f compose.redis.yml up -d
# Backend设 SIMULATION_MANAGER_MODE=redis
celery -A simulation.sumo.distributed.celery_app:app worker \
  --queues citypulse-sumo --pool prefork --concurrency 4
```

Worker使用prefork，一子进程同时只跑一个SUMO会话;与后端Backend共享`generated`与`outputs/sessions`

## 容器化部署

| 容器 | 内容 |
|------|------|
| frontend | 静态资源/Nginx |
| backend | FastAPI，无SUMO/无torch |
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
