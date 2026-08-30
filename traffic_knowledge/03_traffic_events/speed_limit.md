# 临时道路限速

## 项目定义

**【项目事实】** `speed_limit` 是可注入扰动。启动时按路口提交，缺省车道为该路口第一条 incoming 车道；运行时按 `lane_ids` 提交。`max_speed` 单位 m/s，`speed_kmh` 为单位 km/h，至少提供一个。DisturbanceTarget 未提供时默认 **5.0 m/s**，且必须 **严格低于** 车道原始 `max_speed`。SUMO 对活跃限速取最小值写入 `lane.setMaxSpeed`。快照中可观察 `current_allowed_speed_mps`。

前端标签为“道路限速”。它不是独立的产品检测标签；规则检测可将限速类异常归到 `capacity_drop` / `speed_restriction` 语义。

## 对交通的影响

**【交通专业知识】** 临时限速降低该车道的饱和流率和有效容量，车辆减速、车头时距增大。与完全封道不同，车道仍可通行，但上游到达若不变，排队仍会增长。同向未限速车道可能被更多车辆挤入，产生换道和急刹。

| 对象 | 可能变化 |
| --- | --- |
| 道路容量 | 下降，幅度取决于限速与原速度差、车道数 |
| 交通需求 | 不自动减少；只是更慢地通过 |
| 上游排队 | 到达超过新的服务率时增长 |
| 下游通行 | 到达率下降，占用可能降低 |
| 拥堵传播 | 持续限速可向上游回溢 |
| 信号目标 | 避免向已减速路段过度送车；保护横向清空 |

## 管控原则

**【规划功能】** AI scope 重点为事件路口及直接上下游。不应把限速理解成“需要更长绿灯把车推过去”。事件结束后允许速度恢复，但仍需恢复控制消化剩余排队。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; backend/app/schemas/disturbance_targets.py; simulation/sumo/engine/events.py
   - 用于支持：限速字段、默认值和 SUMO 写入方式。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
