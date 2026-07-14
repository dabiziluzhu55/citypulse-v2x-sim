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
- `baseline/longest_queue_first/policy.py` — 新信号接口的示例策略
- `simulation.sumo.run` — 固定配时与算法策略的统一运行入口

算法实现 `SignalPolicy.reset/act/close`，并在 `act()` 中返回
`{official_intersection_id: target_phase_no}`。runner 独占 TraCI，并负责黄灯、
清空阶段和非法动作校验。

## 联合仿真集成（后续）

联合仿真后续复用 `simulation.sumo.policy.SignalPolicy`，不再由算法直接写入
SUMO 灯色字符串。
