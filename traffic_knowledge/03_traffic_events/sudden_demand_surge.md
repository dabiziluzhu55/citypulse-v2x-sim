# 突发交通需求增长

## 定义

突发需求增长是在短时窗口内进入某区域或离开某区域的车辆需求显著高于近期基线，可由活动开场/散场、集中接送或临时出行引起。它不同于事故型容量下降，但二者都可能让需求超过有效容量。

## 可能观察到的数据特征

多个入口的车辆数或到达率同步上升，短时预测高于近期移动平均，进口排队和等待逐步增长，而道路允许速度、车道可用性没有对应下降。活动开场可能形成向场馆/学校方向汇聚，散场可能形成由单点向多方向扩散。没有 OD 或事件上下文时，只能判断“需求异常增长”，不能断言具体活动原因。

## 可能造成的影响

增长持续超过服务能力时形成瓶颈，并可能通过短路段传播到相邻路口。活动结束后需求下降，队列通常从尾部逐步消散；若队列不消散，应检查是否叠加阻塞或下游容量限制。

## 可考虑的管控动作

提前预警并监测入口方向；比较 SOTL、Max Pressure、IPPO/MAPPO 与 Fixed；保护下游存储并避免把所有绿时集中到单方向。必要时建议外部系统实施信息发布或需求管理，但 LLM 不得自行执行路网改造、绕行或设备控制。

## 当前项目能力

仿真支持 `major_event_opening` 和 `major_event_closing`，可在时窗内按请求车辆数和来源/目的车道注入活动交通。短时预测用过去 60 秒、每 5 秒一帧的 20 路口四特征预测未来约 60 秒车辆数；STGCN 不可用时降级移动平均。预测不是事件真值。

## LLM 判断指标

使用车辆数/到达趋势、短时预测及 fallback 标志、进口排队、平均速度、通行量、完成率、事件时窗和空间方向。若预测历史不足或模型 fallback，应降低置信度。

## 来源

1. Does Travel Time Reliability Matter?
   - 发布机构：FHWA Office of Operations
   - 年份：2019
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop19062/whatis.htm
   - 用于支持：特殊活动造成短时需求激增和非经常性拥堵。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; simulation/sumo/events.py; backend/app/services/prediction_runtime.py; docs/official20_prediction_handoff.md
   - 用于支持：活动注入、预测契约和 fallback。
