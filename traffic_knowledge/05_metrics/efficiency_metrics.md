# 通行效率指标

## 当前正式指标

`traffic_eval.EvalResult` 发布标准核心指标：路径平均速度 `path_avg_speed_kmh`、行程时间比 `travel_time_index`、延误时间比 `delay_time_proportion`（0~1）、城市交通运行指数 `traffic_performance_index`、运行状态 `traffic_state`、路径平均停车次数 `avg_stops_per_vehicle`、区域最大排队长度 `regional_max_queue_length_m`、溢流率 `spillback_rate`（%）。辅助诊断仍发布平均行程时间 `avg_travel_time_s`、平均停车等待时间 `avg_waiting_time_s`、进口车道平均排队车辆数 `avg_queue_length_veh`（veh/lane）、网络实际吞吐流率 `throughput_veh_per_h`、完成率 `completion_rate` 和平均算法决策延迟。吞吐流率不是道路理论容量；平均停车等待不是完整延误（延误用 DTP）。

路径速度/TTI/DTP 来自 TripInfo 已完成车辆；排队车辆数与溢流率按仿真时间加权，不用帧数当时间权重。

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

