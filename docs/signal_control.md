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

拓扑支持两种写法：相位语义在所有时段一致时使用顶层 `phases`；相位编号在不同时段
代表不同放行方向，或相位数量不同时，使用 `programs -> program_id -> phases`。
`demo_4` 使用后一种写法，因为早高峰相位 1 为“南北向直行”，晚高峰相位 1 为
“东西向直行”，且平峰只有 3 个相位。相位中的 `protected` 可追加受保护放行组，
`permissive` 可追加让行放行组。五岔口还可以使用 `approach_direction_mapping` 覆盖单个进口的
SUMO `dir` 解释；例如 `demo_9` 只把东进口的 `R` 作为第二条右转路线，其他没有官方分流依据的
大角度 `L/R` 连接保持阻断。

`demo_12` 的平峰相位原始数据之和为 `215s`，但截图周期单元格写成 `195s`；晚高峰相位之和为
`180s`，周期单元格写成 `178s`。构建配置以每个相位的绿灯、黄灯和全红原始值为准。其南北
左转轨迹被 junction 182 的 foe 矩阵判定为冲突，因此相位 4 使用让行绿 `g`。`demo_14` 的
“东、北放行”相位把北直行设为受保护绿、东/北左转设为让行绿，并阻断官方表中为空的动作。
`demo_15` 的南北和东西直行使用受保护绿，同相位左转使用让行绿。

当官方要求同相位放行、但 SUMO foe 矩阵判定主放行轨迹彼此冲突时，可把该相位的
`priority` 设置为 `permissive`。相位仍按官方周期同时显示绿灯，但使用 `g` 让车辆
按冲突矩阵避让，而不是生成互相冲突的受保护绿 `G`。

生成的 `tls_manifest.json` 会在每个路口的 `programs` 对象内保存对应的
`phase_order`、`phase_movements` 和 `templates`。runner 在启动场景时按当前
`program_id` 选择这一组数据，因此平峰控制动作不会误用早高峰的相位含义。

灯色由构建器生成：`G` 是受保护绿，`g` 是让行绿，`y` 是黄灯，`r` 是红灯。算法的
信号动作只返回官方 phase ID，不接触灯色字符串、`tls_id` 或 `linkIndex`；协议 2.0
还允许同时返回单车目标速度和当前道路换道请求。

当 `u_turn_policy` 为 `with_left` 且 SUMO 的 `t` 映射为独立的 `uturn` movement 时，
掉头连接自动继承同进口左转的所有灯色阶段。这样左转和掉头共用周期，但车流生成器
仍能区分左转路线与掉头路线，不会把官方左转流量错误分配到掉头出口。

## 构建

```bash
python -m simulation.sumo.build_tls \
  --intersections demo_2 demo_4 demo_5 demo_6 demo_9 demo_12 demo_14 demo_15
```

`data/maps/sumo/generated/` 是可删除、可重建且不提交 Git 的目录，不要手工修改。
完整构建会先清空该目录，防止旧路口和旧版平铺文件残留。

构建器会先检查基础路网中的 junction 类型。已经是 `traffic_light` 的路口会保留原有
`linkIndex` 和冲突矩阵，不再重复传给 `netconvert --tls.set`；只有 `priority` 等尚未
信号化的路口才交给 `netconvert`。传给旧版 SUMO 前还会在临时副本中移除空
`<param>`，基础路网文件本身不会被修改。

| 生成路径 | 用途 |
|---|---|
| `network/TotalMap_20.signals.net.xml` | 加入目标 TLS 的公共派生路网 |
| `signals/official_tls.add.xml` | 所有官方 SUMO signal programs |
| `manifests/tls_manifest.json` | runner 使用的相位、连接、lane 和灯色桥接数据 |
| `manifests/traffic_manifest.json` | 场景路径、官方时间和 PCU 合计 |
| `reports/official_tls_connections.csv` | 人工核对 connection、movement 和 linkIndex |
| `traffic/demo_N/PERIOD/routes.rou.xml` | 该路口、该时段的真实 15 分钟车流 |
| `traffic/demo_N/PERIOD/signals.add.xml` | 只包含该时段 program 的信号文件 |
| `traffic/demo_N/PERIOD/simulation.sumocfg` | 可直接运行的独立场景 |

生成目录根层只保留上述分类目录。旧的转向验证车流、验证用 `sumocfg` 和 debug POI
工具已经删除；路线正确性由配置校验、连接报告、单元测试和真实场景 GUI 检查共同保证。

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

- 在算法动作写入 SUMO 前完整验证路口、phase ID、车辆、速度和目标车道；
- 满足最小绿灯后才开始切换；
- 执行当前相位对应的黄灯和全红；
- 切换期间保留算法提交的最新目标；
- 同步控制一个官方路口对应的多个物理 TLS；
- 以一个决策周期为租约执行单车速度和换道动作；
- 算法超时、失联或返回非法数据时停止仿真。

算法通信字段和响应格式以 [algorithm_interface.md](algorithm_interface.md) 为唯一对外契约。
