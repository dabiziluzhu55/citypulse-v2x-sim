# 交通流预测模块

当前正式预测主线是 **官方 20 路口 206 入口车道预测（v1）**。TLS100 路口级方案保留为归档，官方 20 路口聚合方案也保留为历史对照；三套方案的节点粒度和指标不能直接混用。

## 当前主线：官方 20 路口 206 入口车道预测（v1）

这套方案以官方 20 个路口 TLS manifest 中的 **206 条入口车道**为节点，不是全网 2,714 条车道，也不是把车道聚合成路口。模型使用过去 60 秒的 `vehicle_count`、`halting_count`、`mean_speed` 和 `occupancy` 四个特征，预测每条入口车道未来 60 秒的 `vehicle_count`。

STGCN 输入形状为 `[batch, 4, 12, 206]`，输出每个车道一个未来 60 秒预测值。归一化只使用训练集拟合，窗口不跨 episode；评估统一报告 MAE、RMSE、sMAPE、WMAPE，MAPE 仅作补充。

主要入口：

- `build_official20_lane_adjacency.py`：从 SUMO 网络和 TLS manifest 生成 206 车道拓扑；
- `filter_official20_lane_snapshots.py`：将全车道快照固定到模型所需的 206 条车道顺序；
- `prepare_stgcn_episode_dataset.py`：按 episode 构造无泄漏的时序张量；
- `evaluate_stage1_baselines.py`、`train_xgboost_stage1.py`、`train_stgcn_stage1.py`：基线、XGBoost 和 STGCN 训练评估；
- `scripts/train_official20_lane_v1.sh`：完整训练入口；
- `tests/test_prediction_official20_lane_adjacency.py`、`tests/test_prediction_filter_official20_lane_snapshots.py`：车道图和快照顺序约束测试。

当前交接文档见 `docs/prediction_current_official20_lane206_v1.md`，正式结果表见 `docs/results/prediction_current_official20_lane206_results_60s.csv`。

## 归档：TLS100 路口级预测（v1）

TLS100 方案以 SUMO `traffic_light` junction 为节点，将全车道 5 秒快照聚合为 100 个路口状态，预测未来 60 秒的路口 `vehicle_count`。这是路口级任务，不是道路级或车道级任务；目前不作为默认预测入口。

- `build_tls100_junction_manifest.py`：生成稳定的 100 路口节点清单；
- `aggregate_tls100_junction_snapshots.py`：校验并聚合全车道快照；
- `build_tls100_junction_adjacency.py`：构建路口拓扑；
- `build_tls100_results_table.py`：生成统一结果表；
- `scripts/aggregate_tls100_intersection_v1.sh`、`scripts/smoke_tls100_intersection_v1.sh`、`scripts/train_tls100_intersection_v1.sh`：准备、冒烟和训练入口；
- `tests/test_prediction_tls100_*.py`：TLS100 节点、聚合和拓扑约束测试。

归档交接文档见 `docs/prediction_archive_tls100_intersection_v1.md`，结果表见 `docs/results/prediction_archive_tls100_intersection_results_60s.csv` 和同名 Markdown 文件。

## 归档：官方 20 路口聚合预测

这套历史方案将车道快照聚合为 `demo_1`--`demo_20` 的 20 个路口，保留用于旧实验结果对照和兼容，不作为当前正式预测主线。

- `aggregate_intersection20_snapshots.py`：将车道快照聚合为 20 个路口；
- `prepare_stgcn_episode_dataset.py`：按 episode 构造无泄漏 NPZ 数据集；
- `evaluate_stage1_baselines.py`：计算基础对照；
- `train_xgboost_stage1.py`、`train_stgcn_stage1.py`：训练模型；
- `build_official20_results_table.py`：生成 20 路口结果表。

归档交接文档见 `docs/prediction_archive_official20_intersection20_v1.md`，结果表见 `docs/results/prediction_archive_official20_intersection20_results_60s.csv`。
