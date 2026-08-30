# 术语与项目字段映射

## 标准术语原则

交通信号控制术语以现行 GB/T 31418-2025 为规范入口。项目代码名是软件契约，不应擅自改写为标准中的同义词；需要对外合规文件时，应阅读全文后建立逐项映射。

## 项目常用术语

| 术语 | 本知识库含义 | 项目字段/边界 |
| --- | --- | --- |
| 路口 / intersection | 一个受控交通信号节点 | `intersection_id`，如 `demo_3`；不是地理实名 |
| 车道 / lane | SUMO 路网中的车道对象 | `lane_id`；进口/出口由 metadata role 区分 |
| movement / connection | 从进口车道到出口车道的一次转向连接 | Max Pressure 的最小排队与服务单元 |
| 相位 / phase | 同时获得服务的一组 connections | 算法输出官方相位 ID，不直接输出灯色串 |
| 阶段 / stage | 当前 GREEN、YELLOW、CLEARANCE 等执行状态 | 过渡阶段算法不可任意切换 |
| 目标相位 | 算法请求仿真最终进入的相位 | Protocol 2.0 `target_phase`；不是立即强制灯色 |
| 固定配时 | 按预设顺序和时长运行的 SUMO 方案 | 产品名 `fixed` |
| 停止车辆数 | 上一仿真步速度低于 0.1 m/s 的车辆数 | SUMO `halting_count`，不是拥堵等级 |
| 等待时间 | SUMO/TripInfo 定义的近停止时间累计 | 注意实时车道值与终态 TripInfo 聚合不同 |
| 排队回溢 | 队列延伸影响上游路段/路口 | 项目候选事件 `spillback` |
| 通行量 | 有限评估窗口到达数的小时化 | `throughput_veh_per_h`，不等于理论容量 |
| 完成率 | 到达车辆数除以出发车辆数 | 短窗口受在途车辆影响 |
| 急刹率 | 急刹事件数/出发车辆数×100 | 次/100 辆；安全替代指标，非事故率 |
| 预设 / preset | 一组受控路口和地图模板 | `xiongan_20`、`east_dense`、`west_dense` |
| period | 需求与信号方案的场景切片 | `morning_peak`、`off_peak`、`evening_peak` |

## 易混淆项

`accident` 注入事件是仿真实验输入；规则检测的事故候选不是外部确认事实。`east_dense` 不是代码层面的“校园”同义词，`west_dense` 也不是“窄路”实测结论。`fuel_consumption` 前端兼容名实际映射 L/100 km 燃油强度，不是总燃油量。

## 来源

1. GB/T 31418-2025《道路交通信号控制系统术语》
   - 发布机构：国家市场监督管理总局、国家标准化管理委员会；公安部主管
   - 年份：2025
   - URL：https://openstd.samr.gov.cn/bzgk/std/std_list?p.p1=0&p.p2=3141&p.p90=circulation_date&p.p91=desc
   - 用于支持：现行道路交通信号控制术语入口；2026-07-01 实施并替代 2015 版。
2. SUMO TraCI Lane API
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/pydoc/traci/_lane.html
   - 用于支持：halting、车道车辆数等仿真术语。
3. SUMO TripInfo Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html
   - 用于支持：行程、等待、到达和燃油字段语义。
4. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/protocol.py; backend/app/scenario/presets.py; traffic_eval/models.py
   - 用于支持：项目字段映射和易混淆项。
