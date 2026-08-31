# 已生成仿真需求目录

**【项目事实】** 下列数字来自 `data/maps/sumo/generated/manifests/traffic_manifest.json` 与质量/OD 报告，表示 **官方需求生成制品**，不是一次运行会话的实时观测，也不是实地检测器数据。

## 生成参数

- 官方时段：早高峰 07:00–09:00，平峰 14:30–16:30，晚高峰 17:30–19:30。
- 需求时长 7200 s，仿真结束 7500 s，步长 0.1 s，`time-to-teleport=-1`。
- 目标车型份额（按车辆数）：客车 0.80、公交 0.10、电动自行车 0.05、货车 0.05。
- PCU：客车 1.0、公交 2.0、电动自行车 0.5、货车 2.5。
- 配置路径：`data/maps/sumo/official/traffic/vehicle_profiles.json`、`official_traffic_demands.json`、`traffic_generation_policy.json`。

## 九套场景观测 PCU

目标观测 PCU 是进口转向计数口径，车辆经过多个官方路口会被重复计入。质量报告 GEH&lt;5 均为 100%。

| scenario_id | 观测 PCU | 采样车辆数 | 跨路口车辆占比 | 路径策略 |
| --- | ---: | ---: | ---: | --- |
| `global_morning_peak` | 76238 | 31933 | 72.6% | 未通过（货车长途份额 59.6% / 阈值 60%） |
| `global_off_peak` | 53412 | 23699 | 67.8% | 未通过 |
| `global_evening_peak` | 85147 | 35715 | 72.6% | 通过 |
| `east_dense_morning_peak` | 13752 | 6686 | 60.2% | 未通过（局部裁剪无 10 km 长途） |
| `east_dense_off_peak` | 7950 | 3633 | 67.2% | 未通过 |
| `east_dense_evening_peak` | 15118 | 7053 | 67.9% | 未通过 |
| `west_dense_morning_peak` | 12597 | 6429 | 59.0% | 未通过 |
| `west_dense_off_peak` | 10349 | 5483 | 55.0% | 未通过 |
| `west_dense_evening_peak` | 14922 | 7244 | 67.2% | 未通过 |

`route_policy_passed=false` 表示生成策略护栏未全部满足，**不是** “场景不可用”。east/west 局部包空间不足以产生公交/货车 ≥10 km 长途，长途份额为 0。

## 混合交通边界

生成需求包含公交和电动自行车，**不包含行人出行**。路网 XML 中存在人行道车道，但不能据此声称已仿真过街行人。SOTL / IPPO 按 `vehicle_count` 计辆，不按 PCU，一辆公交与一辆电动自行车在请求积分中权重相同。

## 与 API 会话的关系

通过 Backend 启动 `east_dense` / `west_dense` 时，当前启动路径默认 `scenario_scope=global`。上表局部场景数字描述的是独立生成包，不能自动等于该 API 会话里的车辆数。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: data/maps/sumo/generated/manifests/traffic_manifest.json; data/maps/sumo/official/traffic/vehicle_profiles.json; data/maps/sumo/official/traffic/traffic_generation_policy.json
   - 用于支持：生成场景规模、车型和路径策略口径。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
