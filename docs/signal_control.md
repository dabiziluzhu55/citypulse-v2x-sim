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

`right_turn_policy: permissive_always` 表示右转在所有相位使用让行绿；
`right_turn_policy: phase_controlled` 表示右转必须显式写入对应相位的 `protected` 或
`permissive` 组，其他相位保持红灯。后者用于官方配时明确把右转划入特定相位的路口。

`demo_4` 的官方需求不包含掉头。其 `u_turn_policy` 设置为 `blocked`，SUMO `t` 方向不继承
左转灯色，并在派生路网构建时删除；左转流量只使用普通 `l/left` 连接。

`demo_12` 的平峰相位原始数据之和为 `215s`，但截图周期单元格写成 `195s`；晚高峰相位之和为
`180s`，周期单元格写成 `178s`。构建配置以每个相位的绿灯、黄灯和全红原始值为准。其南北
左转轨迹被 junction 182 的 foe 矩阵判定为冲突，因此相位 4 使用让行绿 `g`。`demo_14` 的
“东、北放行”相位把北直行和北左转设为受保护绿、东左转设为让行绿，并阻断官方表中为空的动作。
junction 892 的冲突矩阵要求东左转 `linkIndex 5` 让行于北直行和北左转 `linkIndex 2/3`；
如果两者都使用 `g`，SUMO 会因双向互让形成无优先级闭环而拒绝该 program。
`demo_14` 使用 `phase_controlled`：东右转只在相位 1 放行，南右转只在相位 2 放行。
`demo_15` 的南北和东西直行使用受保护绿，同相位左转使用让行绿。

`demo_10` 的东西直行使用受保护绿；东、南左转被 junction 4162 的 foe 矩阵判定为冲突，
因此相位 2 使用让行绿。`demo_13` 按赛方最新要求只保留东、北两个进口，删除西进口及北进口
左转。早晚高峰原相位 1 和相位 4 的东直行时间合并为一个 75 秒相位，平峰保留一个 70 秒
东直行相位；东、北右转始终让行放行，北进口在 junction 1204 上的 `t/uturn` 连接显式阻断。

`demo_1` 按现场方位使用东 `-56907`、西 `-manual_demo1_missing_arm`、北 `-57217`、
南 `-56384`。junction 4427 的南北左转轨迹经 NetEdit 调整后不再互为 foe，四个官方相位
均使用受保护绿；官方需求不包含掉头，因此 `t` 连接在派生路网构建时删除。

`demo_3` 按现场方位使用东 `-57582`、西 `-50816`、北 `-46791`、南 `-52565`。
三个时段均为 `108s` 周期：东西左转直行 `55s` 绿灯加 `3s` 黄灯，南北左转直行
`47s` 绿灯加 `3s` 黄灯。junction `citypulse_demo_3` 的对向直行与左转存在 foe 冲突，
因此直行使用受保护绿、同相位左转使用让行绿，右转始终让行放行。该 junction 只有
12 条 `r/s/l` 连接，没有 `t` 掉头连接；拓扑仍显式使用 `u_turn_policy: blocked`。

`demo_7` 对应 junction `610`，使用东 `-51953`、西 `-46217`、南 `-51871` 三个进口。
早高峰两个相位为 `47+3s` 和 `42+3s`，平峰为 `36+3s` 和 `41+3s`，晚高峰为
`45+3s` 和 `39+3s`。相位 1 放行西直行、西左转、南直行和南右转；由于西左转与
南进口轨迹互为 foe，西左转使用让行绿，其余使用受保护绿。相位 2 仅放行东左转和东右转。
该路口使用 `phase_controlled`，两个右转不会跨相位常绿。

`demo_8` 对应 junction `4393`，复用基础路网中现有的 TLS `J1`。早高峰、平峰、晚高峰
周期分别为 `110s`、`90s`、`120s`；四个相位依次为东西直行、东西左转、南北直行、
南北左转。东进口额外存在的 `t` 掉头连接不属于官方方案，始终保持红灯；四个右转使用
全周期让行绿。

`demo_11` 对应 junction `4306`，构建时由 `netconvert` 从 priority junction 转为交通灯。
三个时段周期分别为 `170s`、`130s`、`176s`，相位顺序同样为东西直行、东西左转、
南北直行、南北左转。东西进口包含多条直行车道，构建器会让同进口的所有直行
`linkIndex` 使用相同灯色；四个右转使用全周期让行绿。

`demo_16` 三个时段均使用 `77s` 周期：东西直行受保护放行、东西左转让行放行后，切换为
南北直行受保护放行、南北左转让行放行；四个右转始终使用让行绿。`demo_17` 三个时段均使用
`96s` 周期：相位 1 放行东进口左转，东进口右转始终让行放行；相位 2 放行南北直行，并让行
放行北进口左转。两个路口中不属于官方方案的 `t` 连接均保持红灯。

`demo_18` 三个时段均使用 `80s` 周期，每个相位为 `37s` 绿灯加 `3s` 黄灯；`demo_19`
三个时段均使用 `76s` 周期，每个相位为 `35s` 绿灯加 `3s` 黄灯。两处路口都先放行东北、
西南进口，再放行西北、东南进口；同向直行使用受保护绿、左转使用让行绿，四个右转始终
让行放行，基础路网中不属于官方方案的 `t` 连接保持红灯。

`demo_20` 对应现有交通灯 junction/TLS `3637`。配时表的东北、西南、西北、东南依次映射为
项目中的东、西、北、南进口；三个时段周期分别为 `120s`、`90s`、`110s`。相位 1/3
分别以受保护绿放行东西/南北直行，并以让行绿同时放行同方向左转；相位 2/4 是左转延长相位。
由于 junction foe 矩阵判定两组对向左转均存在冲突，左转延长相位也使用让行绿。四个右转
全周期让行放行，东、西、南进口的三条 `t` 掉头连接始终保持红灯。

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
  --intersections demo_1 demo_2 demo_3 demo_4 demo_5 demo_6 demo_7 demo_8 demo_9 demo_10 demo_11 demo_12 demo_13 demo_14 demo_15 demo_16 demo_17 demo_18 demo_19 demo_20
```

`data/maps/sumo/generated/` 是可删除、可重建且不提交 Git 的目录，不要手工修改。
完整构建会先清空该目录，防止旧路口和旧版平铺文件残留。

构建器会先检查基础路网中的 junction 类型。已经是 `traffic_light` 的路口会保留原有
`linkIndex` 和冲突矩阵，不再重复传给 `netconvert --tls.set`；只有 `priority` 等尚未
信号化的路口才交给 `netconvert`。传给旧版 SUMO 前还会在临时副本中移除空
`<param>`，基础路网文件本身不会被修改。

其中 `demo_16` 对应 junction `3279`，基础类型为 `priority`，构建时需要由
`netconvert --tls.set 3279` 信号化；`demo_17` 对应 junction `3702`，基础类型已经是
`traffic_light`，构建器会直接保留其现有 `linkIndex` 和 foe 矩阵，不把它再次传给
`netconvert`。这也避免了旧版 SUMO 对已信号化路口重复转换时的不稳定行为。

`demo_18` 的 junction `4409` 和 `demo_19` 的 junction `891` 在基础路网中也已经是
`traffic_light`，构建器直接复用其现有 `linkIndex` 与 foe 矩阵，不会把它们加入
`netconvert --tls.set` 参数。

| 生成路径 | 用途 |
|---|---|
| `network/TotalMap_20.signals.net.xml` | 加入目标 TLS 的公共派生路网 |
| `signals/official_tls.add.xml` | 所有官方 SUMO signal programs |
| `manifests/tls_manifest.json` | runner 使用的相位、连接、lane 和灯色桥接数据 |
| `manifests/traffic_manifest.json` | schema v3 全局场景、路线覆盖、官方时间与审核摘要 |
| `reports/official_tls_connections.csv` | 人工核对 connection、movement 和 linkIndex |
| `traffic/global/candidates.rou.xml` | 单转向兜底与跨路口候选路线池 |
| `traffic/global/PERIOD/routes.rou.xml` | 联合满足已构建路口约束的真实 15 分钟车流 |
| `traffic/global/PERIOD/signals.add.xml` | 所有已构建路口在该时段的 program |
| `traffic/global/PERIOD/simulation.sumocfg` | 可直接运行的全局独立场景 |
| `reports/traffic/PERIOD.*.json` | 路线分配零误差报告与 SUMO 实际过车审核 |

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
