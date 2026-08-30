# IPPO 独立近端策略优化控制

## 定义

IPPO 将多路口中的每个路口作为独立决策主体，使用 PPO 类策略从局部观测选择相位。PPO 通过限制策略更新幅度的代理目标改善训练稳定性；部署时本项目只做确定性 checkpoint 推理，不在线训练。

## 项目输入与动作

每个路口状态为固定长度归一化向量：最多 8 个相位的 one-hot、阶段持续时间、20 路口身份槽、最多 20 条进口车道的车辆数/停止数/等待/速度/占用率，以及出口占用和排队摘要。每个候选相位另有 11 维特征，涵盖进口/出口密度、停止、等待、压力差、当前相位、服务年龄、相位时长和近/远到达需求。

动作是相位索引，再映射为官方 `target_phase`。无效填充动作被 mask；绿灯持续过长时可屏蔽当前相位以强制候选切换。只有绿色稳定阶段、无待切换且达到动作间隔时才决策。

## 适用与谨慎场景

IPPO 适合在训练拓扑、观测契约和需求分布具有代表性时比较。它可能利用规则控制难以表达的非线性关系，但对分布外需求、拓扑指纹变化、checkpoint 不匹配和观测缺失敏感。模型输出不能天然解释为全网协调最优。

## 当前项目实现与可运行性

- 实际名称：`ippo`；模块：`traffic_control.ippo`；模型别名 `ippo_v8_20tls_ep160`。
- checkpoint、sidecar、观测维度、相位特征、动作间隔和拓扑指纹在初始化时校验；不匹配会拒绝运行。
- 支持 `xiongan_20`、`east_dense`、`west_dense`。东/西局部预设使用 20 路口通用模型的子集零样本推理，不能声称已针对该局部场景微调。
- 默认动作间隔 15 s，并取动作间隔、决策间隔和最小绿的最大值。
- 当前状态：仓库含部署 checkpoint，可运行但依赖 torch 和模型契约通过。
- LLM 推荐：允许；必须注明模型别名、预设适配方式和分布外风险。

## 决策评估

不能仅依据训练回报推荐。应与 Fixed、SOTL、Max Pressure 使用同种子和场景比较平均等待、行程、排队、通行量、完成率、燃油强度、急刹率和推理延迟，并检查 warnings 与 metric_sources。

## 来源

1. Schulman et al., Proximal Policy Optimization Algorithms
   - 发布机构：arXiv 原始论文
   - 年份：2017
   - URL：https://arxiv.org/abs/1707.06347
   - 用于支持：PPO 的代理目标和训练背景。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/ippo/controller.py; traffic_control/ippo/contract.py; traffic_control/ippo/aliases.py; traffic_control/registry.py
   - 用于支持：项目观测、动作、模型契约、场景别名和运行状态。

