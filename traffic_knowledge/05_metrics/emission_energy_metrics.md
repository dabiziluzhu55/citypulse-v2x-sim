# 排放与能源指标

## 当前正式指标

当前 `traffic_eval` 只发布燃油强度 `fuel_intensity_L_per_100km`，没有正式发布 CO2、CO、NOx、PM、噪声或电耗指标。字段在前端兼容映射中也可能叫 `fuel_consumption`，但实际口径仍是 L/100 km 的燃油强度，不是本次仿真总耗油量。

## 终态计算口径

终态从 TripInfo 的 `emissions.fuel_abs` 读取燃油质量，并用车辆类型燃油密度换算为体积；只统计已完成、未 vaporized 且动力类型为 gasoline、diesel、hybrid 的车辆。分子和里程分母必须来自同一车辆集合：`(总燃油 mL / 1000) / (总里程 m / 100000)`。

若缺 TripInfo、emissions、powertrain、密度或有效里程，指标置为 `null` 并写 warning。电动车不进入燃油强度分子/分母；因此不同燃油/电动车组成的实验不能只凭该指标直接比较总体能源效率。

## SUMO 单位边界

SUMO 1.14.0 起燃油相关输出由升改为毫克；项目正式终态代码按 `fuel_abs` 毫克与配置密度换算。解释历史实验产物时必须查看 SUMO 版本和 `metric_sources`，不能混用旧版体积单位结果。

## 指标方向与限制

同车辆组成、路网和需求下，燃油强度通常越低越好。它可能受启停、速度、车型和完成车辆选择影响，不能单独代表全部排放或全生命周期能源。需要 CO2/污染物/电耗时，应在第二阶段新增明确字段和一致车辆集合。

## 来源

1. SUMO Emissions Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Models/Emissions.html
   - 用于支持：排放模型覆盖范围与 SUMO 1.14 燃油单位变化。
2. SUMO TripInfo Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html
   - 用于支持：`fuel_abs`、`routeLength` 和排放设备输出。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_eval/tripinfo.py; traffic_eval/powertrain.py; traffic_eval/models.py
   - 用于支持：项目筛选集合、换算公式、空值与现有字段。

