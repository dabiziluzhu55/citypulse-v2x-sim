# 交通事故与疑似事故

## 定义与系统语义

现实事故需要来自警情、视频、V2X 或人工确认等外部证据。仅凭仿真低速和排队只能标记“疑似事故/容量下降”。本项目既支持注入 `accident` 扰动，也有可选事故规则检测；注入事件是实验真值，检测结果则是候选研判，两者不得混淆。

## 可能观察到的数据特征

事故阻塞可能表现为：某车道允许速度或实际速度显著下降，停止车辆和等待持续增加，同方向相邻车道仍有流动，局部通行能力下降，队列向上游传播。仅出现这些特征仍可能是车道关闭、故障车、施工或下游回溢，需要检查事件上下文和跨车道差异。

## 可能造成的影响

事故可能占用一个或多个车道，降低有效容量并产生非经常性拥堵。若发生在已拥堵时段，恢复可能更慢。信号放行无法消除物理障碍；错误增加事故方向绿灯还可能把更多车辆送入受阻区域。

## 可考虑的管控动作

优先输出告警、受影响车道、证据和需确认事项。策略层可减少向受阻下游放行、保护替代路径、关注上游路口，并在事件解除后做恢复控制。**【规划功能】** 注入事故且用户启用 AI 后，可对事件路口及上下游做 takeover。任何模型都不得自动清障、改路网或直接操作 SUMO。详见 `04_scenarios/ai_control_cases.md`。

## 判断指标

使用车道速度、停止数、等待增长、占用率、允许速度、相邻车道对照、下游状态、事件持续时间和急刹事件。急刹率是安全替代指标，不是事故计数。

| 对象 | 可能影响 |
| --- | --- |
| 道路容量 | 被占车道有效容量接近 0 |
| 交通需求 | 不自动减少 |
| 上游排队 | 向事件进口传播 |
| 下游通行 | 该方向到达减少 |
| 信号目标 | 限制进入受阻方向，保护横向清空 |

## 当前项目能力

Backend 可将 `accident` 注入指定车道与位置比例；仿真会维护事件时窗。规则检测中的事故开关默认关闭，且语义层将 `accident` 归入局部阻塞交通状态，同时保留“停止或碰撞车辆”的候选原因。因此必须区分 `injected_ground_truth`、`detected_suspected` 和 `externally_confirmed`。检测卡片不能单独触发 AI 接管。

## 来源

1. Reducing Non-Recurring Congestion
   - 发布机构：FHWA Office of Operations
   - 年份：持续更新
   - URL：https://ops.fhwa.dot.gov/program_areas/reduce-non-cong.htm
   - 用于支持：事故导致临时容量损失和非经常性拥堵。
2. SUMO Safety Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Safety.html
   - 用于支持：碰撞、急刹和安全替代输出的区别。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: backend/app/schemas/events.py; simulation/sumo/events.py; algorithms/event_detection/rules.py; algorithms/event_detection/semantics.py
   - 用于支持：事故注入、可选检测和语义边界。

