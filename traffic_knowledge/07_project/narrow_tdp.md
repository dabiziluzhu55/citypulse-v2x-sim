# Narrow-TDP 短时交通流预测

## 定义

**【项目事实】** 系统在线短时预测模块是 **NarrowNet-TDP**（文档和 Copilot 中常称 Narrow-TDP），代码位于 `backend/app/services/prediction_runtime.py` 与 `backend/app/services/narrow_net_tdp/`。它不是外部在线 STGCN 仓库，也不是 CityPulse-Qwen 自己做的数值预测。

## 做什么

- 历史窗口 12 帧，特征为车道 `vehicle_count` / `halting_count` / `mean_speed` / `occupancy`。
- 在 206 个训练车道节点上预测未来约 60 秒的 `vehicle_count`。
- Backend 再聚合为路口级 `PredictionPayload.intersections`（当前量、预测量、增量）。
- Copilot 的 `get_prediction` 读取该运行时结果；用户要求超过当前 `horizon_seconds`（通常约 60 秒）时不能外推。

模型包不可用或推理失败时降级 `moving_average`，并返回 `fallback` 与 `fallback_reason`。不得把 fallback 结果写成 Narrow-TDP 已成功预测。

## 与 AI 管控的关系

**【项目事实】** Narrow-TDP 可用于触发动态再规划：Backend 仅在 `ready==true` 且 `fallback==false` 时，把粗粒度预测桶变化作为 AI 接管重新规划的信号之一。AI Control **不会**把完整 Narrow-TDP JSON 写入 CityPulse-Qwen 的 Observation V2 prompt。

**预测模型回答接下来可能怎样；CityPulse-Qwen 回答应该怎样管。**

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 0847ae894e1456fa43d97c3332b1418399a04194
   - file: backend/app/services/prediction_runtime.py; backend/app/services/narrow_net_tdp/model.py; backend/app/services/takeover_orchestrator.py
   - 用于支持：预测窗口、车道节点数、fallback 和预测触发再规划。
