# CityPulse V2X Sim

SUMO+CARLA联合仿真与交通协同管控平台。

## 目录结构

| 目录 | 说明 |
|------|------|
| `simulation/` | 仿真基础设施：SUMO、CARLA、联合同步、地图工具 |
| `algorithms/` | 管控算法：基线、强化学习、评估指标 |
| `backend/` | FastAPI后端（待实现） |
| `frontend/` | Vue 前端（待实现） |
| `data/maps/` | 示例地图数据 |
| `configs/` | 全局配置 |
| `scripts/` | 一键运行脚本（待补充） |
| `docs/` | 项目文档 |

## 架构说明

**仿真与算法分离**：`simulation/` 负责世界模型与 TraCI/CARLA 交互；`algorithms/` 负责信号控制决策。算法可在纯SUMO模式运行，也可在联合仿真循环中通过hook注入

## 快速开始

### SUMO单独仿真+MaxPressure管控

```bash
export SUMO_HOME=/path/to/sumo
cd /home/kemove/devdata1/zrl/citypulse-v2x-sim
python algorithms/baseline/run_sumo_control.py --gui
```

### CARLA+SUMO联合仿真

```bash
export SUMO_HOME=/path/to/sumo
export CARLA_ROOT=/path/to/CARLA_0.9.16
# 先启动 CARLA 服务端
python simulation/carla_sumo/run_synchronization.py --sumo-gui
```

详见 [docs/setup.md](docs/setup.md)