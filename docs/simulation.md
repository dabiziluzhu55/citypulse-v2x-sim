# SUMO + CARLA 联合仿真

## 地图数据

- OSM: `data/maps/osm/`
- SUMO: `data/maps/sumo/`
- CARLA OpenDRIVE: `data/maps/carla/`

## 生成全局地图

```bash
python simulation/utils/netcovert_sumo_20.py --keep-xodr
```

该工具从 `TotalMap.osm` 生成 SUMO 路网，并可保留中间 OpenDRIVE 文件。

## 官方信号

`simulation.sumo.build_tls` 根据官方配时、路口拓扑和基础路网生成可运行的派生
路网。源配置、`generated/` 产物和算法接口见
[signal_control.md](signal_control.md)。

## 联合仿真入口

```bash
python simulation/carla_sumo/run_synchronization.py [--sumo-gui]
```

默认配置：
`data/maps/sumo/generated/traffic/global/morning_peak/simulation.sumocfg`，运行前必须先
构建 20 路口官方信号和全局车流。

## 纯 SUMO 管控

`simulation.sumo.run` 提供固定配时和外部 HTTP 算法两种模式。Max Pressure、IPPO 和
多路口强化学习通过 HTTP 协议 2.0 提交官方目标相位、单车目标速度和当前道路换道请求；
runner 独占 TraCI，负责信号安全切换、动作校验和车辆控制租约。
真实车流构建见 [traffic_demand.md](traffic_demand.md)，算法契约见
[algorithm_interface.md](algorithm_interface.md)。

供后端调用的单会话 Python API、实时快照和施工/事故事件见
[simulation_core_api.md](simulation_core_api.md)。
