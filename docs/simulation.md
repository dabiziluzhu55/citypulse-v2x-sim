# SUMO + CARLA 联合仿真

## 地图数据

- OSM: `data/maps/osm/`
- SUMO: `data/maps/sumo/`
- CARLA OpenDRIVE: `data/maps/carla/`

## OSM转OpenDRIVE

```bash
python simulation/utils/osm_to_xodr.py
```

## 联合仿真入口

```bash
python simulation/carla_sumo/run_synchronization.py [--sumo-gui]
```

默认配置：`data/maps/sumo/xiongan_test.sumocfg`

## 纯 SUMO 管控

```bash
python algorithms/baseline/run_sumo_control.py [--gui]
```
