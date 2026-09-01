# Max Pressure 信号控制

## 定义

Max Pressure 是基于相邻队列差选择信号阶段的网络控制思想。原始理论在特定排队网络假设和可行需求内给出吞吐稳定性结论；这些理论条件不能直接等同于任意有限存储 SUMO 路网的性能保证。

## 核心原理

当前实现以 connection（转向 movement）为单位估计上游排队，并减去对应下游排队形成压力。相位压力是其服务 movements 的加权压力之和；保护通行为权重 1.0，许可通行默认权重 0.5。保留负压力，选择总压力最大相位；压力并列时优先保持当前相位。

## 需要的输入

元数据包括相位顺序、车道、connection、from/to edge、转向与优先级。实时观测包括车道 `halting_count`、信号阶段和车辆路线/速度。车辆路线可识别转向时按 movement 计数；无法识别的停止车辆按观测或均匀比例分配。下游无受控 movement 时，使用出口车道停止车辆数作反压。

## 输出或控制行为

每个路口输出一个 `target_phase`。黄灯、清空或已有待切换时保持当前/待切相位，实际安全过渡由仿真内核执行。

## 适用与谨慎场景

需求较高、上下游排队差异明显、需要抑制把车辆推入拥堵下游时适合比较 Max Pressure。若路线/转向识别不足、车道排队估计误差大、行人和非机动车目标未进入压力函数，或网络接近网格锁死，应谨慎解释。项目实现也没有把原始论文全部假设和改进项原样复现。

## 与 Fixed / SOTL 的区别

Fixed 不看实时状态；SOTL 累计未服务相位的车辆请求；Max Pressure 显式使用上游—下游队列差。对短路段密网，反压信息通常比只看上游请求更关键，但仍须实测评估。

## 当前项目实现

- 实际名称：`max_pressure`；模块：`traffic_control.max_pressure`。
- 支持预设：当前三个预设均允许。
- 当前状态：本地 Protocol 2.0 可运行，无 checkpoint。
- 角色：baseline controller。产品实现含下游反压，适合作为无 AI 时的可解释对照。Qwen 不负责改选该模式。

## 来源

1. Varaiya, Max pressure control of a network of signalized intersections
   - 发布机构：Transportation Research Part C
   - 年份：2013
   - URL：https://doi.org/10.1016/j.trc.2013.08.014
   - 用于支持：Max Pressure 的局部队列、阶段选择和吞吐稳定性理论边界。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/max_pressure.py; traffic_control/registry.py
   - 用于支持：movement 队列估计、权重、平局规则、输入输出和可运行状态。

