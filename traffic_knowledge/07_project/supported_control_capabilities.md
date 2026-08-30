# 当前支持的管控能力

## 产品注册表

**【项目事实】** `traffic_control.registry.CONTROL_MODE_REGISTRY` 当前注册：

| control_mode | 内核 | 模块 | 输出 | 预设 | 运行条件 | 角色 |
| --- | --- | --- | --- | --- | --- | --- |
| `fixed` | `fixed` | SUMO 原生方案 | 官方配时 | 全部 | 生成产物可用 | baseline / 公平对比基线 |
| `sotl` | `algorithm` | `traffic_control.sotl` | `target_phase` | 全部 | Protocol 2.0 | baseline |
| `max_pressure` | `algorithm` | `traffic_control.max_pressure` | `target_phase` | 全部 | Protocol 2.0 | baseline |
| `ippo` | `algorithm` | `traffic_control.ippo` | `target_phase` | `xiongan_20` / `east_dense` / `west_dense` | torch、checkpoint、契约 | baseline；局部预设零样本 |
| `mappo` | `algorithm` | `traffic_control.mappo` | `target_phase` | 同上 | torch、checkpoint、特征模块、契约 | baseline；局部预设零样本 |

Backend 默认启用注册表全部模式；部署可用 `enabled_control_modes_csv` 收窄。Catalog 返回运行时白名单。

CityPulse-Qwen **不是** 表中的 `control_mode`。**【规划功能】** 它只在扰动 + 用户启用 AI 时临时接管局部路口；未接管路口和 AI 结束后仍用上表算法。

## 模型别名

**【项目事实】** IPPO 默认 `ippo_v8_20tls_ep160`，MAPPO 默认 `mappo_cooperative_20tls_ep160`。训练身份槽为 `demo_1`–`demo_20`。`east_dense` / `west_dense` 是子集零样本。前端另把 IPPO 限制为 `off_peak`，Backend 未强制该时段限制。

## 启动后不可被 Qwen 改写的实验设计

比较 Fixed / SOTL / Max Pressure / IPPO / MAPPO 时，必须固定 preset、period、时长、种子、需求和扰动。**【规划功能】** 启用 AI 接管的运行应单独标记，不得与“无 AI、纯算法对比”混成同一组结论。

## 不属于当前产品控制模式

`algorithms/` 中的 CoLight/COSLight、V2X 实验、训练器、SUMO actuated/delay_based，以及规划中的 CityPulse-Qwen，都不是可提交的 `control_mode`。不得把它们当作启动参数返回。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 89e1a8173132fc734b4d0c51fb0b71fa36dd4b9d
   - file: traffic_control/registry.py; backend/app/core/config.py; backend/app/schemas/simulations.py; traffic_control/ippo/aliases.py; traffic_control/mappo/aliases.py
   - 用于支持：模式、白名单、预设约束和模型别名。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
