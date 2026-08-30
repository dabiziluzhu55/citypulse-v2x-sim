# 正常平峰场景

## 项目定义

`off_peak` 是项目支持的第三个 period，与 `morning_peak`、`evening_peak` 并列。它表示平峰需求与相应信号方案，不意味着所有路口始终自由流或不存在事件。

## 预期状态

在无扰动、需求低于有效容量且方案适配时，排队通常能在若干信号周期内消散，速度、等待和通行保持相对稳定。平峰基线非常适合识别局部异常：如果某一车道突然长期低速，而同向其他车道正常，容量损失的解释可能比全网需求增长更合理。

## 基线用途

平峰无扰动运行可用于建立各预设和算法的正常范围，校验事件检测误报、指标缺失和模型推理稳定性。基线必须由多次同条件仿真获得；单次运行不能形成可靠阈值。

## 控制思路

Fixed 可作为优先基线。自适应算法若频繁切相位但等待和通行没有改善，可能产生不必要扰动。选择策略时需同时关注急刹率、燃油强度和决策延迟，防止以微小效率收益换取安全替代指标恶化。

## 正常交通与异常区分

红灯期间短时停车属于正常信号控制现象。只有在放绿后仍不释放、等待持续增长、下游异常或相邻车道差异明显时，才应提升异常置信度。知识库内容和 period 名称都不能替代实时观测。

## 来源

1. SUMO Traffic Lights Documentation
   - 发布机构：Eclipse SUMO / DLR
   - 年份：持续更新
   - URL：https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html
   - 用于支持：信号周期、阶段和正常蓄车解释。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: simulation/sumo/scenario.py; data/maps/sumo/official_traffic_demands.json; algorithms/event_detection/rules.py
   - 用于支持：off_peak 场景、平峰方案和信号感知检测。
