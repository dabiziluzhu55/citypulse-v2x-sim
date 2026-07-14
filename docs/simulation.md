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

## 构建官方信号

```bash
python -m simulation.sumo.build_tls --intersections demo_2
```

## 联合仿真入口

```bash
python simulation/carla_sumo/run_synchronization.py [--sumo-gui]
```

默认配置：`data/maps/sumo/generated/official_tls.sumocfg`，运行前必须先构建官方信号。

## 纯 SUMO 管控

```bash
python -m simulation.sumo.run --gui --mode fixed \
  --intersection demo_2 --program demo_2_morning_peak
```
