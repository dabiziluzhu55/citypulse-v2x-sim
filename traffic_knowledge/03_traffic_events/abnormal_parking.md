# 异常停车

## 定义

异常停车指车辆在非计划停车位置长时间占用行车空间并影响通行。它与事故、合法临停、公交站停靠、红灯停车和拥堵停车不同。当前项目没有名为 `abnormal_parking` 的独立注入类型或产品级检测标签。

## 可能观察到的数据特征

候选证据包括：同一车辆在非计划停车位置持续近零速度；其所在车道停止数、等待或占用增加；相邻可用车道仍流动；信号放绿后该车道仍无法释放；影响从停车位置向上游扩展。仅车道聚合数据不足以确认停车原因，应结合车辆 ID、位置、计划 stop、事件注入记录和持续时间。

## 可能造成的影响

异常停车相当于局部容量下降，可能引发换道、急刹和排队。如果位于短路段、学校出入口或交叉口附近，其影响可能快速传到相邻路口。但在缺少真实路侧数据时，不应虚构影响比例或持续时间。

## 可考虑的管控动作

可将其标为“疑似停止车辆型阻塞”，建议核验车辆轨迹。控制上可减少向受阻方向放行。实体处置和违法认定超出本系统范围。当前没有独立注入类型，不能把检测卡片当成 `accident` 真值来触发接管。

## 判断指标

车辆速度/位置持续性、是否计划停车、车道速度、停止数、等待增量、占用率、相邻车道对照、信号阶段与下游状态。若无法获得车辆级持续轨迹，应降级为局部阻塞，不要强判异常停车。

## 当前项目能力边界

事件检测语义能把 `stopped_vehicle` 等外部标签归一到 `lane_blocked`，但这不等于产品已实现异常停车识别。仿真扰动 API 也没有独立异常停车请求。可以解释或提出待核验假设，不可声称系统已直接检测并确认异常停车。

## 来源

1. Freeway Management and Operations Handbook
   - 发布机构：FHWA Office of Operations
   - 年份：2003
   - URL：https://ops.fhwa.dot.gov/freewaymgmt/publications/frwy_mgmt_handbook/chapter1_02.htm
   - 用于支持：停止车辆造成临时车道容量下降的机制。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; algorithms/event_detection/semantics.py; simulation/sumo/vehicle.py
   - 用于支持：不存在独立异常停车类型、车辆轨迹和归一语义。

