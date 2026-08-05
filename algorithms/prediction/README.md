## 更新记录

> 本板块记录 `algorithms/prediction/` 目录的最新变更，新条目放在最上方。

### 2026-07-30
- **官方 20 路口 60 秒流量预测统一评估与交接**
  - 新增 sMAPE 评估，评估统一报告 MAE、RMSE、sMAPE、WMAPE；
  - 训练与基线脚本补充 sMAPE 输出（`train_stgcn_stage1.py`、`train_xgboost_stage1.py`、`evaluate_stage1_baselines.py`）；
  - 结果表统一口径（`build_official20_results_table.py`）；
  - 交接文档统一为 `docs/official20_prediction_handoff.md`。

---

# 官方 20 路口交通流预测

本目录实现官方 20 个目标路口的 60 秒 `vehicle_count` 预测；节点是 `demo_1`--`demo_20`，不是旧方案的全网车道节点。

输入为每 5 秒采集的一帧路口状态，使用过去 12 帧（60 秒）的四项特征：

- `vehicle_count`
- `halting_count`
- `mean_speed`
- `occupancy`

输出为每个路口未来 60 秒的 `vehicle_count`。数据按独立 SUMO episode 划分，窗口不跨 episode；归一化、活跃节点筛选与历史均值仅在训练集拟合。

## 模型与对照

- `persistence`、`moving_average`、`historical_average`：基础对照；
- XGBoost：CPU 多特征基线；
- STGCN-Cheb：当前主模型，输入形状为 `[batch, 4, 12, 20]`，直接输出 `[batch, 20]`。

评估统一报告 MAE、RMSE、sMAPE、WMAPE；MAPE 仅作补充，并在反归一化后按原始计数 `>= 0.5` 排除零流量分母，避免浮点残差造成失真。

## 主要入口

- `aggregate_intersection20_snapshots.py`：将车道快照聚合为 20 个路口；
- `prepare_stgcn_episode_dataset.py`：按 episode 构造无泄漏 NPZ 数据集；
- `evaluate_stage1_baselines.py`：计算三种基础对照；
- `train_xgboost_stage1.py`、`train_stgcn_stage1.py`：训练模型；
- `build_official20_results_table.py`：生成同一 60 秒预测口径的统一结果表。

完整的训练、结果、模型包和推理交接见 `docs/official20_prediction_handoff.md`。
