# 官方信号系统

本文面向仿真组，说明官方信号配置和 SUMO 派生产物。算法组无需阅读本文，只需阅读
[algorithm_interface.md](algorithm_interface.md)。

## 配置分层

| 文件 | 维护内容 |
|---|---|
| `TotalMap_20.net.xml` | 基础路网、edge、lane、connection 和 junction |
| `TotalMap_20.intersections.json` | `demo_N` 到 SUMO junction 的权威映射 |
| `official_tls_plans.json` | 官方周期、相位、绿灯、黄灯、全红和时段 |
| `official_tls_topology.json` | 官方进口和相位到 SUMO edge/movement 的适配 |
| `official_traffic_demands.json` | 官方 15 分钟转向交通量及进口映射 |

配时与地图拓扑分开维护：修改官方秒数时不接触 SUMO edge；路网重建导致 edge 变化时
只修正拓扑映射。

## 配时校验

每个 program 定义 `program_id`、官方时段、完整周期和相位。构建器要求：

- 每个相位 `green + yellow + all_red == total`；
- 所有相位 `total` 之和等于 `cycle_duration`；
- 每个官方相位在拓扑中有且只有一个定义；
- 所有正常转向至少被一个相位放行；
- 受保护绿之间不能被 SUMO foe 矩阵判定为冲突。

灯色由构建器生成：`G` 是受保护绿，`g` 是让行绿，`y` 是黄灯，`r` 是红灯。算法只
返回官方 phase ID，不接触灯色字符串、`tls_id` 或 `linkIndex`。

## 构建

```bash
python -m simulation.sumo.build_tls --intersections demo_2
```

`data/maps/sumo/generated/` 是可删除、可重建且不提交 Git 的目录，不要手工修改。

| 生成文件 | 用途 |
|---|---|
| `TotalMap_20.signals.net.xml` | 加入目标 TLS 的派生路网 |
| `official_tls.add.xml` | 所有官方 SUMO signal programs |
| `tls_manifest.json` | runner 使用的相位、连接、lane 和灯色桥接数据 |
| `official_tls_validation.rou.xml` | 检查每个正常转向能否通过，不代表真实车流 |
| `official_tls_connections.csv` | 人工核对 connection、movement 和 linkIndex |
| `official_traffic_demo_N_PERIOD.rou.xml` | 真实 15 分钟车流 |
| `official_traffic_demo_N_PERIOD.sumocfg` | 对应时段的可运行场景 |
| `official_tls_demo_N_PERIOD.add.xml` | 只包含该时段 program 的信号文件 |
| `traffic_manifest.json` | 场景时长、官方时间和 PCU 合计 |

真实车流的数据口径和场景命令见 [traffic_demand.md](traffic_demand.md)。

## 运行模式

固定配时不调用任何算法服务：

```bash
python -m simulation.sumo.run --mode fixed --gui \
  --intersection demo_2 --period morning_peak
```

外部算法模式：

```bash
python -m simulation.sumo.run --mode algorithm --gui \
  --algorithm-endpoint http://127.0.0.1:8001 \
  --intersection demo_2 --period morning_peak
```

多个路口可以一次传给 `--intersection`。runner 会分别选择
`demo_N_morning_peak`、`demo_N_off_peak` 或 `demo_N_evening_peak`，不要求不同路口使用
同一个 program ID。

## 控制安全边界

runner 独占 TraCI 信号写权限，并负责：

- 在算法动作写入 SUMO 前验证路口和 phase ID；
- 满足最小绿灯后才开始切换；
- 执行当前相位对应的黄灯和全红；
- 切换期间保留算法提交的最新目标；
- 同步控制一个官方路口对应的多个物理 TLS；
- 算法超时、失联或返回非法数据时停止仿真。

算法通信字段和响应格式以 [algorithm_interface.md](algorithm_interface.md) 为唯一对外契约。
