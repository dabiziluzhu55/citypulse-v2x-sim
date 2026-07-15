# 官方车流需求与 SUMO 场景

## 数据口径

赛方表格按 15 分钟给出进口转向交通量，单位为 PCU。源数据保存在
`data/maps/sumo/official_traffic_demands.json`，生成文件保存在
`data/maps/sumo/generated/`。当前已配置的 `demo_2`、`demo_4` 使用小客车，
`1 vehicle = 1 PCU`，
因此生成车辆数与表格 PCU 数严格相等。

`demo_2` 三个时段的校验总量为：

| 时段 | 官方时间 | 西进口 | 北进口 | 南进口 | 合计 |
|---|---:|---:|---:|---:|---:|
| 早高峰 | 07:00-09:00 | 816 | 903 | 1042 | 2761 |
| 平峰 | 14:30-16:30 | 474 | 545 | 483 | 1502 |
| 晚高峰 | 17:30-19:30 | 693 | 847 | 759 | 2299 |

`demo_4` 三个时段的校验总量为：

| 时段 | 官方时间 | 东进口 | 西进口 | 北进口 | 南进口 | 合计 |
|---|---:|---:|---:|---:|---:|---:|
| 早高峰 | 07:00-09:00 | 894 | 741 | 865 | 913 | 3413 |
| 平峰 | 14:30-16:30 | 474 | 512 | 490 | 523 | 1999 |
| 晚高峰 | 17:30-19:30 | 1054 | 916 | 983 | 1120 | 4073 |

配置中的 `expected_totals` 不是重复数据，而是防止人工录入错误的校验值。构建器会
检查每个区间恰好 900 秒、区间连续覆盖完整时段、每个值为非负整数，并重新计算
各进口及全路口合计。

## 进口与转向映射

官方表格名称和 SUMO 根据道路几何计算的 `l/r` 不完全一致，必须通过配置显式映射：

| 官方进口/转向 | SUMO approach/movement | 路网 incoming edge |
|---|---|---|
| 西进口左转 | `southeast_branch/left` | `-51425` |
| 西进口右转 | `southeast_branch/right` | `-51425` |
| 北进口直行 | `northeast_main/through` | `-56734` |
| 北进口右转 | `northeast_main/left` | `-56734` |
| 南进口左转 | `southwest_main/right` | `-57228` |
| 南进口直行 | `southwest_main/through` | `-57228` |

这里的反向 `left/right` 是 SUMO 路网几何与官方进口命名口径不同导致的，不能将
官方字符串直接当作 SUMO direction。构建器通过 `tls_manifest.json` 中实际连接反查
`from_edge -> to_edge`；找不到或找到多条不同路线时立即终止。

`demo_4` 的官方方位与 SUMO 转向一致，四个 incoming edge 为：

| 官方进口 | SUMO approach | incoming edge |
|---|---|---|
| 东进口 | `east` | `-50333` |
| 西进口 | `west` | `-57186` |
| 北进口 | `north` | `-56732` |
| 南进口 | `south` | `-57229` |

## 构建与运行

服务器上设置 `SUMO_HOME` 后执行：

```bash
python -m simulation.sumo.build_tls --intersections demo_2 demo_4
```

该命令一次生成公共信号路网以及三个真实交通场景：

```text
generated/
  network/TotalMap_20.signals.net.xml
  signals/official_tls.add.xml
  manifests/tls_manifest.json
  manifests/traffic_manifest.json
  reports/official_tls_connections.csv
  traffic/demo_2/morning_peak/
    routes.rou.xml
    signals.add.xml
    simulation.sumocfg
  traffic/demo_2/off_peak/
    routes.rou.xml
    signals.add.xml
    simulation.sumocfg
  traffic/demo_2/evening_peak/
    routes.rou.xml
    signals.add.xml
    simulation.sumocfg
```

每个场景使用独立的 `signals.add.xml`，其中只保留与车流时段对应的
program，保证直接用 `sumo-gui` 打开时不会误选其他时段配时。

每个场景把本时段起点归一化为仿真 `t=0`，保留 `traffic_manifest.json` 中的官方
起止时间。这样早高峰只运行 7200 秒需求期，不需要从午夜空跑到 07:00。配置另留
300 秒用于最后一批车辆驶离路口。

直接检查 GUI：

```bash
sumo-gui -c data/maps/sumo/generated/traffic/demo_2/morning_peak/simulation.sumocfg
```

固定配时 runner（默认就是 `demo_2` 早高峰）：

```bash
python -m simulation.sumo.run --gui --realtime --mode fixed
```

平峰或晚高峰通过 `period` 选择；runner 会自动编译对应会话场景：

```bash
python -m simulation.sumo.run --gui --mode fixed \
  --intersection demo_4 --period off_peak
```

## 扩展其他路口

每增加一个路口，按以下顺序维护：

1. 在 `official_tls_plans.json` 录入官方配时。
2. 在 `official_tls_topology.json` 录入 SUMO incoming edge 与相位运动。如果三个
   program 的相位编号、数量或含义不同，使用 `programs -> program_id -> phases`；
   三个 program 完全共用相位语义时才使用顶层 `phases`。
3. 在 `official_traffic_demands.json` 录入官方进口、转向映射和每个 15 分钟值。
4. 构建后检查 `generated/reports/official_tls_connections.csv`，再用真实场景 GUI 查看每种转向。
5. 运行单元测试，确认区间、合计、路线和接口校验均通过。

如果后续加入公交车或货车，不能继续直接把 PCU 当车辆数。应在需求配置中增加车型
占比与 PCU 系数，再把 PCU 确定性换算为各车型车辆数，同时保留舍入误差报告。
