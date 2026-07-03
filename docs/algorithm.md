# 管控算法

## 目录

```
algorithms/
├── common/          # 通用状态、动作、奖励、指标定义
├── baseline/        # 基线算法（如 MaxPressure）
├── rl/              # 强化学习
└── evaluation/      # 离线评估
```


- 算法可独立在SUMO中验证，不依赖 CARLA
- RL环境封装在 `rl/envs/`，与仿真循环解耦
- 后端 `algorithm_service` 可统一调度不同算法实现

## 当前基线

- `baseline/max_pressure/` — MaxPressure_Flex 信号控制
- `baseline/run_sumo_control.py` — SUMO 单独运行入口

## 联合仿真集成（后续）

在 `simulation/carla_sumo/` 同步循环中，每步调用 `TrafficController.step()` 即可注入管控逻辑。
