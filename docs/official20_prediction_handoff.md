# 官方 20 路口预测：训练、结果与模型交接

更新时间：2026-07-30（Asia/Shanghai）

## 目标与数据口径

主模型 `official20-stgcn-v1` 是 STGCN-Cheb 直接预测器：基于官方 20 个路口过去 60 秒的状态，预测每个路口未来 60 秒的 `vehicle_count`。

| 项目 | 约定 |
| --- | --- |
| 节点 | 官方 `demo_1`--`demo_20`，不是全路网 junction |
| 输入 | `vehicle_count`、`halting_count`、`mean_speed`、`occupancy` |
| 频率 / 窗口 | 每 5 秒一帧；12 帧历史（60 秒） |
| 输出 | `[batch, 20]`，未来 60 秒的 `vehicle_count` |
| 数据划分 | 30 个独立 SUMO episode：18 train、3 validation、3 ID test、6 OOD test |
| 图 | 20 节点空间 3-nearest-neighbour 图，70 条无向边 |
| 归一化 | 只用训练集拟合 |
| 降级策略 | 每个路口最近 12 帧的 `moving_average` |

节点和特征的确切顺序、归一化参数以及邻接图均以模型包内 `normalization_and_nodes.json`、`model_manifest.json`、`adjacency.npz` 为准，接入方不得按界面显示顺序猜测。

当前节点顺序：

```text
demo_1, demo_10, demo_11, demo_12, demo_13, demo_14, demo_15, demo_16,
demo_17, demo_18, demo_19, demo_2, demo_20, demo_3, demo_4, demo_5,
demo_6, demo_7, demo_8, demo_9
```

## 已完成训练与选型

代码版本：`f0adbf3`（`feat(prediction): add official20 forecasting pipeline`）。

- 数据：30/30 个 episode 已完成 20 路口聚合；每个 episode 为 `721 × 20 = 14,420` 行。
- 模型：四特征 XGBoost（CPU）和四特征 STGCN（GPU 1）。
- 对照：`persistence`、`moving_average`、`historical_average`。
- 数据窗口不跨 episode；活跃节点掩码、归一化和历史均值均只使用训练集，避免泄漏。

60 秒结果如下；详细 CSV 位于 `docs/results/official20_prediction_results_60s.csv`。

| 测试集 | 模型 | MAE | RMSE | sMAPE | WMAPE |
| --- | --- | ---: | ---: | ---: | ---: |
| ID | moving_average | 4.438 | 5.989 | 33.36% | 20.90% |
| ID | XGBoost | 3.545 | 4.737 | 27.04% | 16.69% |
| ID | STGCN | **3.169** | **4.351** | **24.63%** | **14.92%** |
| OOD | moving_average | 4.328 | 5.922 | 36.05% | 20.56% |
| OOD | XGBoost | 3.792 | **5.099** | 31.41% | 18.02% |
| OOD | STGCN | **3.722** | 5.108 | **30.00%** | **17.68%** |

旧结果表中的原始 MAPE 曾受零流量数值残差影响。2026-07-30 已直接加载服务器上的既有权重和数据完成纯评估：评估代码统一加入 sMAPE，并将 MAPE 的“非零”判定设在原始计数尺度（`>= 0.5`）。主比较使用 MAE、RMSE、sMAPE、WMAPE。

## 可复现实验

学校服务器：

```text
项目：/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim
实验：/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1
Python：/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python
STGCN：/home/kemove/devdata1/zyh_v2x_ai/repos/STGCN
```

使用 `scripts/train_official20_intersection_v1.sh` 训练。它会复用完整产物，对未完成的 STGCN 从 `last.pt` 恢复；如果既有 `metrics.json` 缺少 sMAPE，则只加载现有模型重新评估，不重训。不要恢复旧的 2,714 车道级 STGCN，因为它与本任务的节点和标签粒度不同。

主要代码：

- 聚合：`algorithms/prediction/aggregate_intersection20_snapshots.py`
- 数据集：`algorithms/prediction/prepare_stgcn_episode_dataset.py`
- 基线：`algorithms/prediction/evaluate_stage1_baselines.py`
- 训练：`algorithms/prediction/train_xgboost_stage1.py`、`algorithms/prediction/train_stgcn_stage1.py`
- 统一结果表：`algorithms/prediction/build_official20_results_table.py`

## 模型包与推理交接

模型二进制不进入源码分支，而由 GitHub Release 附件交付：

- Release：`Official-20 prediction v1`
- 标签：`official20-prediction-v1`
- 当前状态：Draft；交接给队长前必须在 GitHub 页面执行 **Publish release**。

附件 `official20-prediction-v1.zip` 包含：

```text
stgcn_best.pt                 xgboost_model.json
model_manifest.json           adjacency.npz
normalization_and_nodes.json  stgcn_metrics.json
xgb_metrics.json              results_summary_60s.csv
sha256sums.txt                README.md
```

已记录的服务器源文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `stgcn/best.pt` | `de8eea0345dac842106a74ca6ef0db898fb6fae145ec595a8411fcfeb2ef4179` |
| `xgb/model.json` | `526f228852dc2a535cbab6557d76c9ef889a5623346ea8ae91383dcacc9fa88c` |
| `tensors/adjacency.npz` | `f89b3de246cea8e5b028b50000fd2ebb57d33178adb7833d6df805e8845448f9` |

验证环境：Python `v2x-ai-py310`、PyTorch `2.13.0+cu130`、XGBoost `3.2.0`、物理 GPU 1（RTX 4090）。本交接只提供模型和推理契约，不包含 FastAPI、前端或后端部署改动。
