# 交通状态与拥堵

## 状态分层

本知识库将交通状态作为运行研判，而非法定分级：正常状态通常表现为速度和放行稳定、等待与排队不持续增长；一般拥堵表现为局部速度下降、停止车辆和等待上升但仍能在若干周期内消散；严重拥堵表现为排队持续增长、下游存储接近耗尽、队列跨越路段并影响上游路口。等级必须相对场景基线判断。

## 经常性与非经常性拥堵

经常性拥堵通常来自重复出现的需求—容量失衡，例如通勤高峰。非经常性拥堵由事故、故障车、施工、天气、特殊活动或临时需求冲击引起。两者可能叠加：高峰期发生车道阻塞时，同样的容量损失会更难恢复。

## 可观测证据链

拥堵证据不应是单一阈值。较可信的组合是：平均速度相对基线下降，`halting_count` 和等待持续增加，进口车道占用率上升，通行量或完成率未同步改善。若下游车道先出现高占用/高排队，上游随后恶化，符合排队回溢传播；若某车道突然低速且与同向相邻车道差异显著，则应进一步排查局部阻塞。

`citypulse-v2x-sim` 的规则检测语义包括 `normal`、`lane_blocked`、`spillback` 和 `speed_restriction`。这些是基于仿真观测的候选事件语义，事故检测默认并非无条件启用，不能把候选卡片写成已确认事故。

## 控制含义

**【项目事实】** 无扰动时由用户选择的 baseline controller（Fixed / SOTL / Max Pressure / IPPO / MAPPO）独立运行，用于公平对比。CityPulse-Qwen 不负责挑选这些算法。

**【规划功能】** 当注入扰动并启用 AI 管控后，才对局部邻域做协同计划。研判仍应联合等待、排队、占用、下游存储、预测和安全替代指标，不能只优化一个数字。协同原则见 `01_fundamentals/multi_intersection_coordination.md`。

## 来源

1. Recurring Traffic Bottlenecks: A Primer, Chapter 2
   - 发布机构：FHWA Office of Operations
   - 年份：2018
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop18013/chap2.htm
   - 用于支持：经常性与非经常性拥堵、瓶颈和需求—容量失衡。
2. Does Travel Time Reliability Matter?
   - 发布机构：FHWA Office of Operations
   - 年份：2019
   - URL：https://ops.fhwa.dot.gov/publications/fhwahop19062/whatis.htm
   - 用于支持：速度、旅行时间、排队与突发容量/需求变化的关系。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: algorithms/event_detection/semantics.py; algorithms/event_detection/rules.py
   - 用于支持：项目事件语义和规则检测边界。

