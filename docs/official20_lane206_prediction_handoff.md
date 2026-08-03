# 官方 20 路口入口车道预测：训练结果与模型交接

更新：2026-07-30（Asia/Shanghai）

## 范围

这是一个独立于“20 个路口聚合预测”的车道级实验，不能将两者的指标直接混用。

- 节点：官方 20 个路口 TLS manifest 中定义的 **206 条入口车道**，不是全网 2,714 条车道。
- 数据：30 个独立 SUMO episode；18 个训练、3 个验证、3 个 ID 测试、6 个 OOD 测试。每个 episode 为 721 个 5 秒快照。
- 输入：过去 12 帧（60 秒）的 `vehicle_count`、`halting_count`、`mean_speed`、`occupancy`。
- 输出：每条车道未来第 12 帧（60 秒）的 `vehicle_count`。
- 归一化：仅用训练 episode 拟合。

## 车道图

由 `algorithms/prediction/build_official20_lane_adjacency.py` 从 SUMO 网络和 TLS manifest 自动生成。

- 132 条同一进口道的相邻车道关系，权重 0.25；
- 238 条至下一个官方入口车道的下游关系（最大 4 hop），权重 1.0；
- 生成给 STGCN 使用的对称图共 348 条非自环边，并含自环。

训练前必须用 `algorithms/prediction/filter_official20_lane_snapshots.py` 固定到该图的 `nodes` 顺序；数据准备阶段会校验图节点顺序与快照车道顺序一致。

## 已完成训练

训练在 AutoDL RTX 4090 上完成。STGCN 使用 Chebyshev 图卷积、4 个输入特征、batch size 32、最多 100 epoch；最佳 checkpoint 为 **epoch 95**。XGBoost 使用 250,000 条确定性抽样训练行。

60 秒统一结果见 [CSV](results/official20_lane206_prediction_results_60s.csv)。sMAPE 和 WMAPE 在下表中均已转换为百分比。

| 集合 | 模型 | MAE | RMSE | sMAPE | WMAPE |
| --- | --- | ---: | ---: | ---: | ---: |
| 验证 | persistence | 1.105 | 1.933 | 149.14% | 53.41% |
| 验证 | moving_average | 1.041 | 1.791 | 141.15% | 50.32% |
| 验证 | historical_average | 1.487 | 2.958 | 134.78% | 71.89% |
| 验证 | XGBoost | 0.899 | 1.449 | 130.57% | 43.44% |
| 验证 | STGCN | **0.716** | **1.178** | **126.62%** | **34.62%** |
| ID 测试 | persistence | 1.114 | 1.941 | 148.83% | 54.04% |
| ID 测试 | moving_average | 1.049 | 1.801 | 140.79% | 50.87% |
| ID 测试 | historical_average | 1.485 | 2.942 | 134.63% | 72.02% |
| ID 测试 | XGBoost | 0.897 | 1.453 | 130.06% | 43.49% |
| ID 测试 | STGCN | **0.714** | **1.173** | **126.09%** | **34.64%** |
| OOD 测试 | persistence | 1.093 | 1.951 | 152.32% | 53.49% |
| OOD 测试 | moving_average | 1.031 | 1.812 | 144.66% | 50.46% |
| OOD 测试 | historical_average | 1.696 | 3.816 | 139.56% | 83.00% |
| OOD 测试 | XGBoost | 0.915 | 1.509 | 134.45% | 44.77% |
| OOD 测试 | STGCN | **0.768** | **1.277** | **131.86%** | **37.57%** |

## sMAPE 修正说明

真实 `vehicle_count` 是整数。早期基线评估在 float64 反归一化后把部分真实 0 恢复成极小负数，导致 sMAPE 将这些零流量样本误作非零，低估了基线的 sMAPE。

现在所有评估路径都会在计算 MAPE/sMAPE 前将**真实标签**恢复为整数计数；预测仍保留连续值。该修正不需要重训，也不改变 MAE、RMSE 或 WMAPE 的结论。

ID/OOD 的真实零流量占比为 52.57% / 54.83%。因此 sMAPE 的绝对值很高：只要真实为 0 而预测非 0，该单元的 sMAPE 就是 200%。分段诊断显示，STGCN 在 ID/OOD 的零流量预测均值为 0.381 / 0.397，仍优于 moving_average 的 0.459 / 0.454；而在其余流量区间也保持更低 sMAPE。

## 复现与交接

训练脚本：`scripts/train_official20_lane_v1.sh`。它会生成图、验证固定车道集合、准备 episode-bounded tensor、计算三条基础基线、训练 XGBoost 和 STGCN；遇到 `last.pt` 时可恢复 STGCN。

模型二进制不进入 Git。已保存的交接包应作为 Release 附件交付，包含：

```text
official20_lane206_v1_handoff_20260803.tar.gz
└── lane206_v1/
    ├── graph/official20_lane_adjacency.npz
    └── formal/
        ├── stgcn/{best.pt,last.pt,metrics.json}
        ├── xgb/{model.json,metrics.json}
        ├── baseline metrics, tensor metadata, results table, SHA256SUMS.txt
        └── manifest and filtered metadata
```

本地保存的交接包 SHA-256：`89fe16f0a05a6bef48d845fadb4e4d82fd82fb3fdcbd30034d82a5e7591278db`。

部署方需要的最小推理契约为：206 个图节点的既定顺序、4 个输入特征顺序、12 帧历史窗口、训练集拟合的归一化参数、图邻接矩阵以及 `best.pt`。本交接不包含后端部署改动。
