# 窄路密网片区（条件性场景知识）

本文件描述通用交通机理；雄安新区的规划指标、分层路网特征、项目坐标校核和建模要求见 `xiongan_narrow_road_dense_network.md`。

## 与当前预设的关系

当前 `west_dense` 被代码定义为“西部密集路口场景”，受控路口为 `demo_14`、`demo_15`、`demo_19`。项目坐标映射显示三组路口中心直线距离约为 195.6–413.1 米，能支持“受控路口空间上较近”，但仓库仍没有提供这些道路“狭窄”的红线宽度结论。直线距离也不能代替沿道路距离、直接连接关系或有效排队存储量。本文件在短路段和有限存储经 SUMO 拓扑或测绘确认后适用。

## 运行机理

路口密集且连接路段短时，一个路口的排队更容易到达上游停止线。下游已无存储空间时，上游继续放行可能占据路口内部或阻挡其他方向，形成回溢和局部网格锁死。单路口降低自身队列的动作可能把成本转移到相邻路口，因此需观察网络外部性。

## 可能的数据表现

下游出口占用和停止数先上升，随后相邻上游进口排队增长；多个路口速度同步下降；绿灯期间队列仍无法释放；排队在路口之间呈方向性传播。必须结合车道长度或路段存储估计，不能仅凭“路口多”断定已经回溢。

## 控制思路

优先保护下游存储与路口清空。Max Pressure 的下游反压可作为可解释对照；MAPPO 可纳入协调比较，但其西部预设同样属于 20 路口 cooperative checkpoint 的子集零样本推理。SOTL 主要累计上游请求，若不同时看下游状态，需特别关注过度放行风险。任何协调算法都应与 Fixed 基线做多种子评估。

## LLM 决策提示

推荐应指出关键下游、传播方向、受影响相邻路口和回溢证据。若缺少道路长度、车道容量或邻接拓扑，应请求补证或降低置信度，而不是把“西部密集”直接改写为“窄路实测事实”。

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
   - file: backend/app/scenario/presets.py; data/maps/sumo/TotalMap_20.intersections.json; traffic_control/max_pressure.py; traffic_control/mappo/aliases.py
   - 用于支持：西部预设、路口坐标、项目反压实现和零样本边界。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
4. 《河北雄安新区规划纲要》
   - 发布机构：中共中央、国务院批复；中国雄安官网公开
   - 年份：2018
   - URL：https://www.xiongan.gov.cn/2018-04/21/c_129855813_8.htm
   - 用于支持：雄安起步区密路网与分层道路规划背景。
