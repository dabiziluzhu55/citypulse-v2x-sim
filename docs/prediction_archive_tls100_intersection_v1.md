# 归档：TLS100 路口级预测：训练与交接

更新时间：2026-08-04（Asia/Shanghai）

> 状态：归档。当前预测主线是官方 20 路口 206 入口车道级预测；本文件保留 TLS100 路口级实验的复现信息、结果和交接约定。

## 任务边界

本任务预测 100 个 SUMO `traffic_light` junction 的路口级状态，不是道路级或车道级预测。节点清单由 `TotalMap_20.signals.net.xml` 生成，节点顺序固定写入 `tls100_junction_manifest.json`；聚合结果沿用现有数据集接口中的 `lane_id` 字段承载路口 ID，不能把它误解为道路节点。

每个 5 秒快照从全车道输入聚合为四个路口特征：

- `vehicle_count`：路口入车道车辆数之和；
- `halting_count`：路口入车道停车车辆数之和；
- `mean_speed`：按车辆数加权的入车道平均速度；
- `occupancy`：入车道占有率的算术平均。

不做车道级 active filter。每个场景必须包含 721 个时间点和完整的 100 个路口，因此每个聚合 CSV 为 `721 * 100 = 72,100` 行。

## 数据、图和模型约定

| 项目 | 约定 |
| --- | --- |
| 场景数 | 30 个独立 SUMO episode |
| 实际划分 | train 18、validation 3、test ID 3、test OOD 6 |
| 采样与窗口 | 5 秒采样；历史 12 帧（60 秒）；预测 12 帧（60 秒） |
| 目标 | 未来 60 秒 `vehicle_count` |
| 输入特征 | `vehicle_count`、`halting_count`、`mean_speed`、`occupancy` |
| 归一化 | 只用 train split 拟合 |
| 路口节点 | 100 个，来自 `type="traffic_light"` junction |
| 入车道 | 763 条，固定写入节点清单 |
| 拓扑 | 沿 SUMO 车道下游追踪到首个下游信号路口，再构建 STGCN 对称邻接 |
| 图校验 | 100 节点、1 个连通分量、0 个孤立点、0 条截断路径 |

正式训练时发现数据清单的实际划分是 `18/3/3/6`，与早期 handoff 中的 `18/6/3/3` 不同。训练脚本默认拒绝这种差异，只有在确认实际清单后显式设置 `ALLOW_ACTUAL_SPLITS=1` 才接受，避免静默改变评估口径。

## 代码入口

- `algorithms/prediction/build_tls100_junction_manifest.py`：生成并校验 100 路口 manifest；
- `algorithms/prediction/aggregate_tls100_junction_snapshots.py`：单 episode 预检和聚合，拒绝重复、缺失、时间轴不连续及不完整输出；
- `algorithms/prediction/build_tls100_junction_adjacency.py`：生成路口拓扑和报告，不使用 kNN fallback；
- `algorithms/prediction/build_tls100_results_table.py`：生成统一的 60 秒结果表；
- `scripts/aggregate_tls100_intersection_v1.sh`：CPU 侧 manifest、拓扑和 30 个 episode 聚合；
- `scripts/smoke_tls100_intersection_v1.sh`：小规模 XGBoost/STGCN 冒烟；
- `scripts/train_tls100_intersection_v1.sh`：正式 baseline、XGBoost 和 STGCN 训练；
- `tests/test_prediction_tls100_manifest.py`、`test_prediction_tls100_aggregate.py`、`test_prediction_tls100_adjacency.py`：核心约束测试。

## 复现顺序

在具有项目依赖和 SUMO 网络的环境中，先执行 CPU 准备：

```bash
PROJECT_DIR=/path/to/citypulse-v2x-sim \
SNAPSHOT_DIR=/path/to/official20-stage1/source-simulation-sumo-1686970 \
EXPERIMENT_DIR=/path/to/official20-prediction-v1 \
PYTHON=/path/to/python \
bash scripts/aggregate_tls100_intersection_v1.sh
```

准备完成后执行冒烟和正式训练：

```bash
PROJECT_DIR=/path/to/citypulse-v2x-sim \
EXPERIMENT_DIR=/path/to/official20-prediction-v1 \
STGCN_DIR=/path/to/STGCN \
PYTHON=/path/to/python \
GPU_ID=0 \
bash scripts/smoke_tls100_intersection_v1.sh

PROJECT_DIR=/path/to/citypulse-v2x-sim \
EXPERIMENT_DIR=/path/to/official20-prediction-v1 \
STGCN_DIR=/path/to/STGCN \
PYTHON=/path/to/python \
GPU_ID=0 ALLOW_ACTUAL_SPLITS=1 EPOCHS=100 PATIENCE=15 \
bash scripts/train_tls100_intersection_v1.sh
```

脚本会复用已完成产物，并在发现 partial 或不完整产物时停止而不是覆盖。正式模型权重、NPZ 张量、聚合 CSV 和拓扑二进制文件不进入 Git；它们通过独立的训练交接包交付。Git 中保留可复现的源码、脚本、测试和轻量级结果表。

## 正式结果摘要

STGCN 最优 epoch 为 58。三个评估划分上的结果如下；完整方法对比见 `docs/results/prediction_archive_tls100_intersection_results_60s.md`。

| 划分 | MAE | RMSE | sMAPE | WMAPE |
| --- | ---: | ---: | ---: | ---: |
| Validation | 1.847 | 2.924 | 82.27% | 21.34% |
| Test (ID) | 1.862 | 2.977 | 83.06% | 21.79% |
| Test (OOD) | 2.030 | 3.297 | 86.75% | 23.81% |

sMAPE 使用零安全计算；MAPE 仅作补充指标，主要比较使用 MAE、RMSE、sMAPE 和 WMAPE。
