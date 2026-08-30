# Fixed 固定配时控制

## 定义与核心原理

固定配时控制按预先设定的信号方案循环，不根据当前排队即时改变目标相位。它适合充当稳定、可复现的实验基线，也可在需求规律且方案经过标定时提供可预测服务。

## 输入与输出

本项目 `fixed` 不实例化 Protocol 2.0 控制器，也不消费算法观测。Backend 将业务名 `fixed` 映射到仿真内核模式 `fixed`，SUMO 按场景生成的信号逻辑运行。因此其输出不是算法模块返回的 `target_phase`，而是 SUMO 原生计划的执行结果。

## 适用与谨慎场景

需求稳定、需要对照实验、模型或检测器不可用时可优先使用 Fixed。面对突发需求、局部事故、持续回溢或方向需求明显不均衡时，固定方案可能无法及时重分配绿灯；但不能仅因出现拥堵就断言自适应算法一定更优，必须用相同场景、种子、时长和指标对比。

## 当前项目实现

- 实际名称：`fixed`。
- Backend / Worker 映射：`kernel_mode="fixed"`。
- 支持预设：`xiongan_20`、`east_dense`、`west_dense`，注册表未设额外限制。
- 当前状态：可运行；不需要模型 checkpoint。
- 角色：用户选择的 baseline controller 之一，也是 **【规划功能】** AI 故障或接管结束后的回退算法。CityPulse-Qwen 不负责决定是否改用 Fixed。

## 决策关注指标

比较 Fixed 与其他算法时，重点查看平均行程时间、平均等待、进口车道平均排队、小时通行量、完成率、急刹事件率、燃油强度和决策延迟。Fixed 没有外部算法推理，决策延迟字段可能不可用，不能把缺失值当作零。

## 来源

1. SUMO Traffic Lights Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html
   - 用于支持：static 信号方案的固定顺序和时长语义。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/fixed.py; traffic_control/registry.py; simulation/sumo/session.py
   - 用于支持：项目名称、内核映射、预设支持和执行方式。

