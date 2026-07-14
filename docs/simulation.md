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
`data/maps/sumo/generated/official_traffic_demo_2_morning_peak.sumocfg`，运行前必须先
构建官方信号。`official_tls.sumocfg` 仅用于验证转向。

## 纯 SUMO 管控

`simulation.sumo.run` 提供固定配时、本地 Python 策略和远程 HTTP 策略三种使用方式。
策略只提交官方相位及受约束的车辆建议，由 runner 独占 TraCI 并处理安全切换。
真实车流构建见 [traffic_demand.md](traffic_demand.md)，算法契约见
[signal_control.md](signal_control.md)。
