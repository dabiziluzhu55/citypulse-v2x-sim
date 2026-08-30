# 当前支持的管控能力

## 产品注册表

| control_mode | 实际模块/内核 | 输入重点 | 输出 | 预设 | 运行条件 | LLM 可推荐 |
| --- | --- | --- | --- | --- | --- | --- |
| `fixed` | SUMO `fixed` | 场景内信号方案 | SUMO 原生执行 | 全部当前预设 | 生成产物可用 | 是，基线/回退 |
| `sotl` | `traffic_control.sotl` | 相位请求积分、车辆位置 | `target_phase` | 全部当前预设 | Protocol 2.0 元数据 | 是 |
| `max_pressure` | `traffic_control.max_pressure` | movement 队列、路线、下游反压 | `target_phase` | 全部当前预设 | Protocol 2.0 元数据 | 是 |
| `ippo` | `traffic_control.ippo` | 132 维局部状态、相位特征 | `target_phase` | `xiongan_20/east_dense/west_dense` | torch、checkpoint、契约通过 | 是，注明零样本风险 |
| `mappo` | `traffic_control.mappo` | IPPO-v8 局部状态、cooperative policy | `target_phase` | `xiongan_20/east_dense/west_dense` | torch、checkpoint、训练特征模块、契约通过 | 是，注明零样本风险 |

当前 Backend 默认启用注册表全部模式；部署可用 `enabled_control_modes_csv` 收窄白名单。因此 LLM 推荐前还必须读取运行实例白名单，不能只看静态注册表。

## 模型别名

IPPO 当前默认别名为 `ippo_v8_20tls_ep160`，MAPPO 为 `mappo_cooperative_20tls_ep160`。两个模型均以 20 个身份槽为训练契约；`east_dense` 和 `west_dense` 是训练路口全集的受控子集，代码描述为零样本推理。知识库不宣称其局部场景性能已优于基线。

## 不属于当前产品控制模式

`algorithms/` 中的 CoLight/COSLight、V2X 协同实验、训练器和基准脚本未注册为 Backend `control_mode`。SUMO 自带 actuated/delay_based 也未注册。LLM 不得把它们作为可直接执行策略返回。

## 一致性注意

根 README 的管控模式表尚未列出 MAPPO，且部分示例仍只显示前三种算法；产品注册表、Backend schema 和 simulation service 已支持 MAPPO。未来自动构建知识库时应优先读取注册表并检测文档漂移。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/registry.py; backend/app/core/config.py; backend/app/schemas/simulations.py; backend/app/services/simulation_service.py; traffic_control/ippo/aliases.py; traffic_control/mappo/aliases.py
   - 用于支持：模式、白名单、预设约束、模型别名和执行条件。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim

