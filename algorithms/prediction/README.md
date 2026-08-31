# 交通流预测模块

当前正式预测主线是 **NarrowNet-TDP（窄路密网拓扑感知方向传播网络）**，任务为官方 20 路口的 206 条入口车道预测。正式方法、数据契约、结果和复现说明见：

- `docs/prediction_current_official20_lane206_v1.md`
- `docs/prediction_static_directional_lane_v1.md`
- `docs/results/prediction_current_official20_lane206_results_60s.csv`

## 当前正式主线

NarrowNet-TDP 以 TLS manifest 中固定顺序的 206 条入口车道为节点，输入过去 12 帧、4 个车道特征，预测未来 60 秒的 `vehicle_count`。模型保留静态 Chebyshev 时空主干，并加入从 SUMO 拓扑提取的下游传播和上游回溢方向残差。

核心程序：

- `build_official20_lane_adjacency.py`：生成 206 车道静态图；
- `filter_official20_lane_snapshots.py`：固定车道顺序并过滤快照；
- `prepare_stgcn_episode_dataset.py`：构造不跨 episode 的时序数据；
- `build_dynamic_lane_graph.py`：提供可审计的候选图和公共图工具；
- `build_directional_lane_graph.py`：生成下游、上游及关系分支图；
- `static_cheb_lane_model.py`：静态 Cheb 对照主干；
- `static_directional_lane_model.py`：NarrowNet-TDP 方向残差模型；
- `train_narrow_net_tdp.py`：固定使用 `static_directional` 的正式训练入口；
- `preflight_official20_lane206.py`：检查数据、图和基线契约。

基线训练仍保留在 `train_stgcn_stage1.py`、`train_xgboost_stage1.py` 和 `evaluate_stage1_baselines.py` 中，用于结果对照。完整数据准备入口是 `scripts/train_official20_lane_v1.sh`。

正式入口目前通过一个很薄的兼容层复用归档的多模式训练实现，以避免复制约 1,000 行训练与结果封装逻辑；它对外只接受 NarrowNet-TDP 的 `static_directional` 模式。历史动态、Cheb gate 和层级模式不会从正式入口启动。

## 目录约定

当前目录只放正式主线、必要的基线和公共工具。历史实验统一放在：

- `algorithms/prediction/archive/experiments/`：动态图、层级模型和多模式旧训练入口；
- `algorithms/prediction/archive/aggregation/`：TLS100、20 路口聚合和旧结果表；
- `algorithms/prediction/archive/legacy/`：旧基线、活动车道过滤和 sMAPE 分析工具；
- `docs/archive/prediction/`、`docs/results/archive/prediction/`：历史交接文档和结果；
- `scripts/archive/prediction/`：历史数据聚合、冒烟和训练脚本。

归档文件保留用于复现和审计，不属于当前默认预测入口。当前正式结果只报告 MAE、RMSE 和 WMAPE，不再把 sMAPE 作为主结果指标。
