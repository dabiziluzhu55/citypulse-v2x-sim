# 安全相关指标

## 当前正式指标

`traffic_eval` 当前只正式结算急刹事件数 `hard_braking_events` 和急刹率 `hard_braking_rate`。急刹事件按车辆类型配置的负加速度阈值识别，并只在车辆从“非急刹”进入“急刹”状态时计一次；累计值跨帧取最大，避免重复相加。急刹率为 `事件数 / departed × 100`，单位是次/100 辆。

车辆类型阈值来自 `data/maps/sumo/official/traffic/vehicle_profiles.json`：不同类型可不同。这些是项目仿真配置，不是现实事故判定标准。

## 如何解释

同条件下急刹事件或急刹率上升，提示策略可能产生更多强制减速或不平顺交互，适合作为筛查信号。它不是碰撞数、事故率或人身伤害风险，不能证明某算法不安全或某事件为事故。车辆组成、出发数、跟驰参数和仿真步长变化都会影响可比性。

## 当前缺失

项目正式 `EvalResult` 尚无碰撞数、TTC、PET、DRAC、行人/非机动车冲突或实测事故数据。SUMO 可通过 SSM device 输出 TTC、DRAC、PET 等安全替代指标，但当前 `traffic_eval` 没有接入这些字段，不得把它们说成已实现。

## 使用建议

算法比较时至少联合急刹率、效率指标、车辆组成和 metric source。若急刹率缺失，应保持 `null` 并查看 warning；不得用 0 填充。对校园等混合交通场景，只有机动车急刹远不足以覆盖安全目标。

## 来源

1. SUMO SSM Device Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Output/SSM_Device.html
   - 用于支持：TTC、DRAC、PET、制动率等安全替代指标及其局限。
2. FHWA Surrogate Safety Assessment Model Overview
   - 发布机构：美国联邦公路管理局
   - 年份：持续更新
   - URL：https://highways.dot.gov/turner-fairbank-highway-research-center/software/ssam
   - 用于支持：仿真冲突指标是事故的替代评估，不等于事故记录。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: simulation/sumo/engine/vehicle.py; data/maps/sumo/official/traffic/vehicle_profiles.json; traffic_eval/collector.py; traffic_eval/models.py
   - 用于支持：急刹阈值、事件去重、比率公式和当前缺失项。

