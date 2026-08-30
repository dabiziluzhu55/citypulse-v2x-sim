# 通行效率指标

## 当前正式指标

`traffic_eval.EvalResult` 发布：平均行程时间 `avg_travel_time_s`、平均等待时间 `avg_waiting_time_s`、进口车道平均排队 `avg_queue_length_veh`、小时通行量 `throughput_veh_per_h`、出发/到达数、完成率 `completion_rate` 和平均算法决策延迟。它没有发布名为“平均速度”的正式结算字段；车道 `mean_speed` 存在于实时快照和预测输入中，只能用于状态研判，不能冒充结算指标。

## 计算口径

- 行程/等待：终态从 TripInfo 读取全部已出发车辆的 `duration` / `waitingTime` 总和，再除以 `departed`。记录数不一致时指标置空并告警。
- 排队：仅 `role == incoming` 的进口车道；每帧先求车道 `halting_count` 均值，再对时间采样求均值，单位为 veh/lane。
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

