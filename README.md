# CityPulse V2X Simulation

城市交通与车路云协同仿真可视化项目，包含 Vue 3 仪表盘前端、FastAPI Mock 后端和 SUMO 仿真内核。

## 环境要求

- Node.js 20.19+ 或 22.12+
- Python 3.10+
- SUMO（仿真后端需要，设置 `SUMO_HOME`）

## 目录结构

| 目录 | 说明 |
|------|------|
| `simulation/` | 仿真基础设施：SUMO、CARLA、联合同步、地图工具 |
| `algorithms/` | 算法组协作边界；正式算法由算法组独立维护 |
| `backend/` | FastAPI 后端（仿真后端 + Mock 后端） |
| `frontend/` | Vue 3 仪表盘前端 |
| `data/maps/` | 示例地图数据 |
| `configs/` | 全局配置 |
| `scripts/` | 工具脚本 |
| `docs/` | 项目文档 |

**仿真与算法分离**：`simulation/` 独占 SUMO/TraCI；Max Pressure、IPPO 和多路口
强化学习通过 HTTP/JSON 协议 2.0 接收路口、单车及油耗状态，并返回官方目标相位、
单车目标速度和换道请求。

## 启动 Mock 后端（前端联调）

```bash
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认地址为 `http://127.0.0.1:5173`，开发代理连接 `http://127.0.0.1:8000`。Swagger 位于 `http://127.0.0.1:8000/docs`。

## 构建检查

```bash
cd frontend
npm run build
```

雄安新区本地 3D Tiles 可通过环境变量 `XIONGAN_3DTILES_DIR` 指定；未配置时后端仍可正常提供其他 Mock API。

## SUMO 官方信号仿真

```bash
export SUMO_HOME=/path/to/sumo
cd /home/kemove/devdata1/zrl/citypulse-v2x-sim
python -m simulation.sumo.build_tls --intersections demo_2
python -m simulation.sumo.run --gui --realtime --mode fixed \
  --intersection demo_2 --period morning_peak
```

构建命令还会按赛方 15 分钟数据生成 `demo_2` 的早高峰、平峰和晚高峰真实车流。
数据口径、总量校验和场景切换见 [docs/traffic_demand.md](docs/traffic_demand.md)。

后端可调用的会话、时间窗口、进口筛选、交通倍率和扰动事件接口见
[docs/simulation_core_api.md](docs/simulation_core_api.md)。

### CARLA+SUMO 联合仿真

环境依赖见 [docs/setup.md](docs/setup.md)，官方信号数据结构、派生产物和算法接口见
[docs/signal_control.md](docs/signal_control.md)。

算法组只需阅读 [docs/algorithm_interface.md](docs/algorithm_interface.md)。

仿真后端详细说明见 [backend/README.md](backend/README.md)。
