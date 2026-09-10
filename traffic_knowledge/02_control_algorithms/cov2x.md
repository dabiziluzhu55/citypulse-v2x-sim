# CoV2X（Cooperative Vehicle to Everything）

## 定义

**【项目事实】** CoV2X 是本项目已注册的产品 `control_mode`，业务名为 `cov2x`。它是面向车路协同的 baseline controller，不是规划中的实验代码，也不是 CityPulse-Qwen 的替代名称。

CoV2X 强调路口信号与车辆侧建议的协同：在 Protocol 2.0 下输出信号 `target_phase`，并可通过车辆动作字段给出速度/车道建议。CityPulse-Qwen 的 Signal 路径只吸收信号动作；车辆级动作保留在完整专家数据中，不会被静默改写成“只有信号”的最优标签。

## 当前项目实现

- 实际名称：`cov2x`；模块：`traffic_control.cov2x`。
- 内核：`algorithm` + 本地 `algorithm_transport="local"`。
- 默认模型别名：`cov2x_g30_temp_cap_u24`。版本化候选由 `traffic_control.cov2x.dispatch` 按别名加载，不把旧 EP12 与冻结 update-24 混用同一套 loader。
- 支持预设：`xiongan_20`、`east_dense`、`west_dense`。
- 运行中可产生真实 V2X 通信事件（SEND/DELIVER/CONSUME），写入快照 `v2x_events`，供前端车路云通信面板展示。
- 角色：baseline controller。CityPulse-Qwen **不负责** 选择或改写该模式。

## 适用与谨慎场景

需要观察车路协同信封、车辆建议与信号动作同时存在的对照实验时，应使用 `cov2x`。不能把它理解成已经替代 Max Pressure / IPPO / MAPPO 的默认最优算法；是否更优必须用相同预设、时段、种子和 `traffic_eval` 口径比较。

车辆动作不会由 CityPulse-Qwen 在线接管执行。询问“本项目 CoV2X 做了什么”时，应依据本文和注册表，而不是把通用 V2X 概念写成当前产品行为。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 0847ae894e1456fa43d97c3332b1418399a04194
   - file: traffic_control/registry.py; traffic_control/cov2x/dispatch.py; traffic_control/cov2x/aliases.py
   - 用于支持：产品控制模式、别名分发和 V2X 事件导出。
