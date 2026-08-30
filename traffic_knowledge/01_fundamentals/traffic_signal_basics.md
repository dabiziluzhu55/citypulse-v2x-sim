# 交通信号控制基础

## 相位、阶段与信号方案

相位可理解为一组被共同服务的交通流；完整方案还包含相位顺序、绿灯、黄灯、全红或清空阶段以及安全转换关系。`citypulse-v2x-sim` 的算法输出是官方目标相位 `target_phase`，实际切换由 SUMO 信号控制器执行，算法不得跳过黄灯或清空阶段。

## 固定与交通响应控制

固定配时按预先定义的相位顺序和时长循环，优点是稳定、可复现和便于基线比较，缺点是无法直接响应随机波动。交通响应控制根据检测到的车辆、间隙、延误、排队或压力决定延长或切换；它仍需最小绿、最大绿、相位冲突和过渡阶段等约束。

SUMO 官方同时支持 static、actuated、delay_based 等信号类型。本项目的 `fixed` 使用 SUMO 原生固定方案；SOTL、Max Pressure、IPPO、MAPPO 是项目的 Protocol 2.0 本地模块，不能把 SUMO 自带 actuated 误报为当前产品注册算法。

## 协调控制

单路口优化只观察局部服务时，可能把队列推向已拥堵的下游。路口间距短或排队可能跨路段时，应把下游占用、相邻路口队列和传播方向纳入判断。协调不等于盲目同步所有相位，而是让放行与下游可接收能力、走廊到达波和网络目标一致。

## 项目执行边界

**【项目事实】** Backend 接受业务名 `control_mode` 并查注册表，Worker 根据 `algorithm_module` 加载本地控制器。算法只返回 `target_phase`，`SafePhaseController` 负责最小绿、黄灯和全红。黄灯时长和清空来自官方 TLS plan 的 `yellow` / `all_red`。

**【规划功能】** CityPulse-Qwen 不替代上述注册算法，也不直接写灯色。它只在扰动接管窗口内输出结构化计划，再编译为同样的 `target_phase`。详见 `07_project/ai_control_architecture.md`。

## 来源

1. SUMO Traffic Lights Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html
   - 用于支持：固定、感应、延误型控制和相位过渡。
2. GB/T 39900-2021《道路交通信号控制系统通用技术要求》
   - 发布机构：国家市场监督管理总局、国家标准化管理委员会；主管部门公安部
   - 年份：2021
   - URL：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=8ACBD03506C5D9D2727D803850885CFE
   - 用于支持：道路交通信号控制系统的规范背景；截至 2026-08-25 状态为现行。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/registry.py; traffic_control/protocol.py; simulation/sumo/engine/signal.py
   - 用于支持：项目控制链路、target_phase 和安全过渡。
