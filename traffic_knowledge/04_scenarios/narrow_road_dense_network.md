# 窄路密网片区（条件性场景知识）

本文件描述通用交通机理；雄安新区的规划指标、分层路网特征、项目坐标校核和建模要求见 `xiongan_narrow_road_dense_network.md`。

## 与当前预设的关系

**【项目事实】** Backend `west_dense` 标签为“窄路密网片区场景”，路口为 `demo_14`、`demo_15`、`demo_19`。前端可能显示“西部密集区场景”。三路口中心直线距离约 195–413 m；SUMO 进口边长度约 48–291 m，多数 1–2 车道，周期约 75–115 s。这支持“受控路口近、存储有限”，仍不能写成红线宽度实测达标。直线距离不等于沿路距离。**【规划功能】** 该预设仅 3 个路口，AI 可对整个预设协同，仍须按事件方向选择优先走廊。

## 运行机理

路口密集且连接路段短时，一个路口的排队更容易到达上游停止线。下游已无存储空间时，上游继续放行可能占据路口内部或阻挡其他方向，形成回溢和局部网格锁死。单路口降低自身队列的动作可能把成本转移到相邻路口，因此需观察网络外部性。

## 可能的数据表现

下游出口占用和停止数先上升，随后相邻上游进口排队增长；多个路口速度同步下降；绿灯期间队列仍无法释放；排队在路口之间呈方向性传播。必须结合车道长度或路段存储估计，不能仅凭“路口多”断定已经回溢。

## 控制思路

优先保护下游存储与路口清空。无扰动时 Max Pressure / MAPPO / SOTL / Fixed 仍是用户选择的 baseline，西部 MAPPO/IPPO 是 20 路口模型零样本。SOTL 主要累计上游请求，密网中需警惕向已回溢方向继续放行。

## 检索与决策提示

推荐应指出关键下游、传播方向和回溢证据。可以把 Backend 标签和已测进口长度写入依据，但不能把“窄路密网”写成红线宽度达标或规划密度已实现。

## 来源

1. Recurring Traffic Bottlenecks: A Primer
   - 发布机构：FHWA Office of Operations
   - 年份：2018
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop18013/chap2.htm
   - 用于支持：物理限制、瓶颈和排队传播的机理。
2. Varaiya, Max pressure control of a network of signalized intersections
   - 发布机构：Transportation Research Part C
   - 年份：2013
   - URL：https://doi.org/10.1016/j.trc.2013.08.014
   - 用于支持：相邻队列反压和网络控制背景。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/scenario/presets.py; data/maps/sumo/official/map/TotalMap_20.intersections.json; traffic_control/max_pressure.py; traffic_control/mappo/aliases.py
   - 用于支持：西部预设、路口坐标、项目反压实现和零样本边界。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
4. 《河北雄安新区规划纲要》
   - 发布机构：中共中央、国务院批复；中国雄安官网公开
   - 年份：2018
   - URL：https://www.xiongan.gov.cn/2018-04/21/c_129855813_8.htm
   - 用于支持：雄安起步区密路网与分层道路规划背景。
