# CityPulse V2X Simulation

城市交通与车路云协同仿真可视化项目，包含 Vue 3 仪表盘前端、FastAPI 仿真后端和 SUMO 仿真内核。

## 环境要求

- Node.js 20.19+ 或 22.12+
- Python 3.10+
- SUMO（仿真后端需要，设置 `SUMO_HOME`）

## 目录结构

| 目录 | 说明 |
|------|------|
| `simulation/` | 仿真基础设施：SUMO、CARLA、联合同步、地图工具 |
| `algorithms/` | 算法组协作边界；正式算法由算法组独立维护 |
| `backend/` | FastAPI 仿真后端（包装 SimulationManager） |
| `frontend/` | Vue 3 仪表盘前端 |
| `data/maps/` | 示例地图数据 |
| `configs/` | 全局配置 |
| `scripts/` | 工具脚本 |
| `docs/` | 项目文档 |

**仿真与算法分离**：`simulation/` 独占 SUMO/TraCI；Max Pressure、IPPO 和多路口
强化学习通过 HTTP/JSON 协议 2.0 接收路口、单车及油耗状态，并返回官方目标相位、
单车目标速度和换道请求。

## 启动仿真后端

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

详细说明见 [backend/README.md](backend/README.md)。Swagger：`http://127.0.0.1:8000/docs`。

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址为 `http://127.0.0.1:5173`，开发代理连接 `http://127.0.0.1:8000`。

## 构建检查

```bash
cd frontend
npm run build
```


## SUMO 官方信号仿真

```bash
export SUMO_HOME=/path/to/sumo
cd /home/kemove/devdata1/zrl/citypulse-v2x-sim
python -m simulation.sumo.build_tls
python -m simulation.sumo.run --gui --realtime --mode fixed \
  --period morning_peak
```

构建命令默认将 20 个指定路口联合起来，生成早高峰、平峰和晚高峰三套全局车流。
同一辆车可连续经过多个指定路口，并同时计入这些路口的官方流量约束。构建阶段还会运行
SUMO 复核实际过车总量；数据口径、误差报告和场景切换见
[docs/traffic_demand.md](docs/traffic_demand.md)。

后端可调用的会话、时间窗口、进口筛选、交通倍率和扰动事件接口见
[docs/simulation_core_api.md](docs/simulation_core_api.md)。

### CARLA+SUMO 联合仿真

环境依赖见 [docs/setup.md](docs/setup.md)，官方信号数据结构、派生产物和算法接口见
[docs/signal_control.md](docs/signal_control.md)。

算法组只需阅读 [docs/algorithm_interface.md](docs/algorithm_interface.md)。

仿真后端详细说明见 [backend/README.md](backend/README.md)。
