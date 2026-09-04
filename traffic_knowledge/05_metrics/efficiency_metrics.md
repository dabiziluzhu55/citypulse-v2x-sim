---
information_type: project_fact
status: current
document_role: canonical_metric_definition
priority: high
---

# 通行效率指标

## 当前正式指标

`traffic_eval.EvalResult` 发布标准核心指标：路径平均速度 `path_avg_speed_kmh`、行程时间比 `travel_time_index`、延误时间比 `delay_time_proportion`（0~1）、城市交通运行指数 `traffic_performance_index`、运行状态 `traffic_state`、路径平均停车次数 `avg_stops_per_vehicle`、区域最大排队长度 `regional_max_queue_length_m`、溢流率 `spillback_rate`（%）。辅助诊断仍发布平均行程时间 `avg_travel_time_s`、平均停车等待时间 `avg_waiting_time_s`、进口车道平均排队车辆数 `avg_queue_length_veh`（veh/lane）、网络实际吞吐流率 `throughput_veh_per_h`、完成率 `completion_rate` 和平均算法决策延迟。吞吐流率不是道路理论容量；平均停车等待不是完整延误（延误用 DTP）。

路径速度/TTI/DTP 来自 TripInfo 已完成车辆；排队车辆数与溢流率按仿真时间加权，不用帧数当时间权重。

## 完整公式、单位与边界

以下内容是当前 `traffic_eval` 实现的正式项目口径。问答时应优先复述
字段、单位、样本范围和空值规则，不要用通用交通教材中的近似公式替代。

| 指标 | 单位 | 数据范围 | 计算方法 |
| --- | --- | --- | --- |
| `path_avg_speed_kmh` | km/h | 已完成且未 vaporize 的 TripInfo | `ΣrouteLength / Σduration × 3.6`；是总距离除以总时间，不是单车速度的算术平均 |
| `travel_time_index` | 无量纲 | 有效 TripInfo；`duration - timeLoss > 0` | `Σduration / Σ(duration - timeLoss)`；自由流参考是 SUMO 仿真等效时间，不是实测自由流 |
| `delay_time_proportion` | 0~1 | 有效 TripInfo；`duration > 0` | `ΣtimeLoss / Σduration`；前端若显示百分数再乘以 100 |
| `traffic_performance_index` | 0~10 | 由有效 DTP 计算 | 按 GB/T 33171-2016 附录 C 的 DTP 区间进行项目约定的档内线性插值 |
| `traffic_state` | 分级文本 | 由 TPI 计算 | `[0,2)` 畅通；`[2,4)` 基本畅通；`[4,6)` 轻度拥堵；`[6,8)` 中度拥堵；`[8,10]` 严重拥堵 |
| `avg_stops_per_vehicle` | 次/车 | 已完成且未 vaporize 的 TripInfo | `ΣwaitingCount / N`；不是 Snapshot 中 `halting_count` 的均值 |
| `regional_max_queue_length_m` | m | 评价期内进口车道 Snapshot | 所有 `role == incoming` 车道的 `queue_length_m` 最大值 |
| `spillback_rate` | % | 评价期内进口车道-时间样本 | `溢流 lane·s / 有效 lane·s × 100`；溢流判定为 `queue_length_m >= lane_length_m`，只使用数值 epsilon，不另设交通阈值 |

辅助指标的口径也必须区分：平均行程时间和平均停车等待时间使用全部已出发
车辆；网络实际吞吐流率为 `arrived / evaluation_duration_seconds × 3600`，
不是道路理论容量；完成率为 `arrived / departed`；平均算法决策延迟没有样本时
为 `null`，不能用 0 代替。

指标不可计算时返回 `null` 并保留 warning，不用 0 或模型推测补齐。国家/行业
标准依据需要另查标准索引；本节的项目公式不能被表述成国家标准原文。

## 计算口径

- 行程/等待：终态从 TripInfo 读取全部已出发车辆的 `duration` / `waitingTime` 总和，再除以 `departed`。记录数不一致时指标置空并告警。
- 路径速度 / TTI / DTP / 停车次数：仅已完成且未 vaporize 的 TripInfo；TTI 使用 `duration-timeLoss` 作为仿真等效自由流时间。
- 排队车辆数：仅 `role == incoming`；每帧先求车道 `halting_count` 均值，再按仿真时间 Δt 加权，单位为 veh/lane。
- 区域最大排队 / 溢流率：进口车道 Snapshot `queue_length_m`（与算法 payload 同一估算函数）；溢流按 lane·s 暴露率，不用帧比例。
- 通行量：`arrived / evaluation_duration_seconds × 3600`，是全网到达率的小时化值，不是道路理论容量。
- 完成率：`arrived / departed`。短时仿真结束时仍在途车辆会降低该值。
- 决策延迟：由算法 perf counter 注入；无样本为 `null`，不是 0。

## 指标方向

同条件下，行程、等待、排队和决策延迟通常越低越好；通行量和完成率通常越高越好。但高通行量若伴随急刹率或燃油强度明显恶化，不应单独判为更优。不同需求、时长、路网或车辆组成的数值不能直接横比。

## 来源

1. SUMO TripInfo Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html
   - 用于支持：duration、waitingTime、routeLength 与到达记录语义。
2. FHWA Traffic Analysis Measures of Effectiveness
   - 发布机构：美国联邦公路管理局
   - 年份：2011
   - URL：https://www.fhwa.dot.gov/publications/research/operations/11064/004.cfm
   - 用于支持：时间、延误、速度、排队和通行类指标需组合解释。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_eval/models.py; traffic_eval/collector.py; traffic_eval/tripinfo.py
   - 用于支持：项目精确字段、公式、空值和来源告警。

