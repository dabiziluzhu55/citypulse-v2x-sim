# 突发交通需求增长

## 定义

突发需求增长是在短时窗口内进入某区域或离开某区域的车辆需求显著高于近期基线，可由活动开场/散场、集中接送或临时出行引起。它不同于事故型容量下降，但二者都可能让需求超过有效容量。

## 可能观察到的数据特征

多个入口的车辆数或到达率同步上升，短时预测高于近期移动平均，进口排队和等待逐步增长，而道路允许速度、车道可用性没有对应下降。活动开场可能形成向场馆/学校方向汇聚，散场可能形成由单点向多方向扩散。没有 OD 或事件上下文时，只能判断“需求异常增长”，不能断言具体活动原因。

## 可能造成的影响

增长持续超过服务能力时形成瓶颈，并可能通过短路段传播到相邻路口。活动结束后需求下降，队列通常从尾部逐步消散；若队列不消散，应检查是否叠加阻塞或下游容量限制。

## 可考虑的管控动作

**【项目事实】** 可复现的需求冲击注入类型是 `major_event_opening`（向场馆汇聚）和 `major_event_closing`（由场馆扩散）。开场与散场的走廊方向相反，不能共用同一套优先相位叙述。

提前预警并监测主方向；保护下游存储，避免只加长出口/入口单个路口的绿灯。**【规划功能】** 散场 AI scope 应覆盖离场走廊而不只是场馆路口。模型不得执行路网改造或设备控制。

| 对象 | 开场 | 散场 |
| --- | --- | --- |
| 道路容量 | 通常不变 | 通常不变 |
| 交通需求 | 向场馆汇聚 | 由场馆扩散 |
| 上游排队 | 入口走廊 | 出口后的下游走廊 |
| 信号目标 | 入口协同与冲突方向限制 | 疏散走廊协同，禁止单路口加绿 |

## 当前项目能力

仿真支持 `major_event_opening` 和 `major_event_closing`，可在时窗内按请求车辆数和来源/目的车道注入活动交通。短时预测由 NarrowNet-TDP 使用过去 12 帧四特征预测未来约 60 秒路口车辆数；不可用时降级移动平均。预测不是事件真值，Qwen 不得自己编造预测曲线。

## 判断指标

使用车辆数/到达趋势、短时预测及 fallback 标志、进口排队、平均速度、通行量、完成率、事件时窗和空间方向。历史不足或 fallback 时应降低置信度。

## 来源

1. Does Travel Time Reliability Matter?
   - 发布机构：FHWA Office of Operations
   - 年份：2019
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop19062/whatis.htm
   - 用于支持：特殊活动造成短时需求激增和非经常性拥堵。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; simulation/sumo/engine/events.py; backend/app/services/prediction_runtime.py; backend/app/services/intelligence_runtime.py
   - 用于支持：活动注入、预测契约和 fallback。
