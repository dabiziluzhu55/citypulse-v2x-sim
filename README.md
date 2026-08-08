# CityPulse V2X Sim

SUMO+CARLA联合仿真与交通协同管控平台。

## 目录结构

| 目录 | 说明 |
|------|------|
| `simulation/` | 仿真基础设施：SUMO、CARLA、联合同步、地图工具 |
| `algorithms/` | 算法组协作边界；正式算法由算法组独立维护 |
| `backend/` | FastAPI后端（待实现） |
| `frontend/` | Vue 前端（待实现） |
| `data/maps/` | 示例地图数据 |
| `configs/` | 全局配置 |
| `scripts/` | 一键运行脚本（待补充） |
| `docs/` | 项目文档 |

## 架构说明

**仿真与算法分离**：`simulation/` 在生产环境独占进程内 libsumo；Max Pressure、IPPO 和多路口
强化学习通过 HTTP/JSON 协议 2.0 接收路口、单车及油耗状态，并返回官方目标相位、
单车目标速度和换道请求。后端只转发 snapshot，前端不直接连接 SUMO。TraCI 仅保留给
本地 `sumo-gui` 调试。

## 快速开始

### SUMO 官方信号仿真

```bash
export SUMO_HOME=/path/to/sumo
cd /home/kemove/devdata1/zrl/citypulse-v2x-sim
python -m simulation.sumo.build_tls
python -m simulation.sumo.run --mode fixed \
  --intersection demo_2 --period morning_peak

# 需要观察 SUMO 原生窗口时使用本地 GUI 调试旁路
python -m simulation.sumo.run --gui --realtime --mode fixed \
  --intersection demo_2 --period morning_peak
```

构建命令还会使用 SUMO routeSampler 联合拟合 20 个路口，按
`data/maps/sumo/traffic_generation_policy.json` 为不同车型筛选长短途候选路线，生成早高峰、
平峰和晚高峰 3 个全局真实车流场景，并按最终车流的起止区域生成对应的九区域 OD PCU 矩阵。
数据口径、总量校验和场景切换见 [docs/traffic_demand.md](docs/traffic_demand.md)。

后端可调用的会话、时间窗口、局部管控范围、交通倍率和扰动事件接口见
[docs/simulation_core_api.md](docs/simulation_core_api.md)。

### CARLA+SUMO联合仿真

```bash
export SUMO_HOME=/path/to/sumo
export CARLA_ROOT=/path/to/CARLA_0.9.16
# 先启动 CARLA 服务端
python simulation/carla_sumo/run_synchronization.py --sumo-gui
```

环境依赖见 [docs/setup.md](docs/setup.md)，官方信号数据结构、派生产物和算法接口见
[docs/signal_control.md](docs/signal_control.md)。

算法组只需阅读 [docs/algorithm_interface.md](docs/algorithm_interface.md)。
