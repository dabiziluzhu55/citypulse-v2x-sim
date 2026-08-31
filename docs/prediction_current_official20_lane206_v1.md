# NarrowNet-TDP：官方 20 路口 206 入口车道预测交接

更新：2026-08-09（Asia/Shanghai）

> 状态：最终推荐版本。NarrowNet-TDP 作为项目当前默认预测方法；原始 STGCN、静态 Cheb 控制和 D0 作为对照；TLS100 路口级方案已归档。

## 方法名称

- 中文名：**窄路密网拓扑感知方向传播网络**
- 英文名：**Topology-aware Directional Propagation Network for Narrow-Dense Road Networks**
- 简称：**NarrowNet-TDP**

NarrowNet-TDP 是面向雄安新区窄路密网路网的车道级预测方法。它保留稳定的静态 Cheb
时空主干，同时增加基于 SUMO 拓扑的有向下游传播、上游回溢和跨路口 hop 衰减分支。
STGCN 在本文中仅表示原始基线或内部时空主干，不再作为最终方法名称。

## 范围

这是一个独立于“20 个路口聚合预测”的车道级实验，不能将两者的指标直接混用。

- 节点：官方 20 个路口 TLS manifest 中定义的 **206 条入口车道**，不是全网 2,714 条车道。
- 数据：30 个独立 SUMO episode；18 个训练、3 个验证、3 个 ID 测试、6 个 OOD 测试。每个 episode 为 721 个 5 秒快照。
- 输入：过去 12 帧（60 秒）的 `vehicle_count`、`halting_count`、`mean_speed`、`occupancy`。
- 输出：每条车道未来第 12 帧（60 秒）的 `vehicle_count`。
- 归一化：仅用训练 episode 拟合。
- 正式报告指标：MAE、RMSE、WMAPE。

## 车道图

由 `algorithms/prediction/build_official20_lane_adjacency.py` 从 SUMO 网络和 TLS manifest 自动生成。

- 132 条同一进口道的相邻车道关系，权重 0.25；
- 238 条至下一个官方入口车道的下游关系（最大 4 hop），权重 1.0；
- 生成给 STGCN 使用的对称图共 348 条非自环边，并含自环。

训练前必须用 `algorithms/prediction/filter_official20_lane_snapshots.py` 固定到该图的 `nodes` 顺序；数据准备阶段会校验图节点顺序与快照车道顺序一致。

## 针对雄安新区窄路密网的路网适配

### 路网特征

这个任务不是把 20 个路口当成互相独立的节点，而是预测一张由 206 条入口车道组成的连续路网。雄安新区这张路网具有几个直接影响预测的特点：

- 多个官方路口沿道路连续串联，车辆的影响会跨越当前路口继续传播；
- 路口形态并不完全相同，包含三岔、四岔、五岔和两臂路口，车道之间不是均匀的规则网格；
- 同一进口道内存在横向相邻车道，但真正的车辆流动还受到车道进入方向、转向关系和下游目标车道约束；
- 车辆沿 SUMO 连接关系向下游行驶，信号排队和通行受阻又会沿相反方向向上游回溢；
- 官方拓扑中的 `next_target` 关系最多跨 4 hop，远端车道与当前车道存在关联，但不应与紧邻车道具有相同影响强度。

因此，本版本针对的“窄路密网”问题不是简单增加模型规模，而是让模型区分“车辆向前传播”和“拥堵向后回溢”，同时控制跨多个路口的远距离影响。

### 具体适配方式

1. **保留静态 Cheb 主干。** 对横向相邻车道和稳定的局部空间关系继续使用原有静态图，保证模型稳定并保留原始 STGCN 的可比性。
2. **增加有向下游传播。** 使用 SUMO 的 `direct_transition` 和 `next_target` 关系，把上游车道状态传递到车辆可能进入的下游车道。
3. **增加上游回溢传播。** 使用下游关系的转置，把下游拥堵和排队信息传回可能受影响的上游车道。
4. **区分近距离和远距离关系。** `direct_transition` 单独建分支；`next_target` 按 `0.75 ** (hops - 1)` 衰减，1 hop 保持完整权重，跨得越远影响越弱。
5. **避免数据泄漏。** 方向图只由 SUMO 路网和官方拓扑生成，不使用未来车辆观测；模型输入输出、数据划分和归一化方式均保持不变。

整体结构可以概括为：

```text
过去 60 秒车道状态
        ↓
静态 Cheb 车道主干
        + 直接下游/上游残差
        + hop 衰减的跨路口下游/上游残差
        ↓
206 条车道未来 60 秒 vehicle_count
```

详细的方向图构建和 NarrowNet-TDP 模型说明见 `docs/prediction_static_directional_lane_v1.md`。

## 已完成训练

原始 STGCN 基线和 NarrowNet-TDP 均在 AutoDL RTX 4090 上完成。NarrowNet-TDP 使用 Chebyshev 图卷积、4 个输入特征、batch size 32、最多 100 epoch、`seed=42`；最佳 checkpoint 为 **epoch 96**。XGBoost 使用 250,000 条确定性抽样训练行。

历史基础模型的 60 秒结果见 [CSV](results/prediction_current_official20_lane206_results_60s.csv)。下表只保留 MAE、RMSE 和 WMAPE，WMAPE 已转换为百分比。

| 集合 | 模型 | MAE | RMSE | WMAPE |
| --- | --- | ---: | ---: | ---: |
| 验证 | persistence | 1.105 | 1.933 | 53.41% |
| 验证 | moving_average | 1.041 | 1.791 | 50.32% |
| 验证 | historical_average | 1.487 | 2.958 | 71.89% |
| 验证 | XGBoost | 0.899 | 1.449 | 43.44% |
| 验证 | 原始 STGCN | **0.716** | **1.178** | **34.62%** |
| ID 测试 | persistence | 1.114 | 1.941 | 54.04% |
| ID 测试 | moving_average | 1.049 | 1.801 | 50.87% |
| ID 测试 | historical_average | 1.485 | 2.942 | 72.02% |
| ID 测试 | XGBoost | 0.897 | 1.453 | 43.49% |
| ID 测试 | 原始 STGCN | **0.714** | **1.173** | **34.64%** |
| OOD 测试 | persistence | 1.093 | 1.951 | 53.49% |
| OOD 测试 | moving_average | 1.031 | 1.812 | 50.46% |
| OOD 测试 | historical_average | 1.696 | 3.816 | 83.00% |
| OOD 测试 | XGBoost | 0.915 | 1.509 | 44.77% |
| OOD 测试 | 原始 STGCN | **0.768** | **1.277** | **37.57%** |

### 当前主线模型对照

以下是保持同一数据划分和 `seed=42` 的本地对齐实验。D0 是只区分下游/上游的中间版本；NarrowNet-TDP 是当前最终版本（实现版本 D2）。

| 集合 | 模型 | MAE | RMSE | WMAPE |
| --- | --- | ---: | ---: | ---: |
| 验证 | 静态 Cheb 控制 | 0.6989 | 1.1867 | 33.78% |
| 验证 | D0 有向残差 | 0.6944 | 1.1725 | 33.56% |
| 验证 | **NarrowNet-TDP** | **0.6765** | **1.1675** | **32.70%** |
| ID 测试 | 静态 Cheb 控制 | 0.6987 | 1.1834 | 33.89% |
| ID 测试 | D0 有向残差 | 0.6962 | 1.1712 | 33.77% |
| ID 测试 | **NarrowNet-TDP** | **0.6741** | **1.1605** | **32.69%** |
| OOD 测试 | 静态 Cheb 控制 | 0.7485 | 1.2891 | 36.63% |
| OOD 测试 | D0 有向残差 | 0.7523 | 1.2776 | 36.82% |
| OOD 测试 | **NarrowNet-TDP** | **0.7271** | **1.2488** | **35.58%** |

NarrowNet-TDP 相较静态 Cheb 控制在三套集合上的 MAE、RMSE 和 WMAPE 均下降；OOD RMSE
下降约 3.1%。因此最终版本采用 NarrowNet-TDP，静态 Cheb 保留为稳定对照，D0 保留为方向信息的中间实验。

## 复现与交接

基础数据准备脚本为 `scripts/train_official20_lane_v1.sh`。NarrowNet-TDP 的方向图由
`algorithms/prediction/build_directional_lane_graph.py` 生成，正式训练使用
`algorithms/prediction/train_narrow_net_tdp.py`。
训练参数固定为 `batch_size=32`、最多 100 epoch、`patience=15`、
`learning_rate=0.001`、`weight_decay=0.001`、`dropout=0.5`、`seed=42`、
`directional_max_scale=0.25`。遇到 `last.pt` 时可恢复训练。

NarrowNet-TDP 的最小复现命令为：

```bash
python -m algorithms.prediction.build_directional_lane_graph \
  --candidate-graph <dynamic_candidate_edges.npz> \
  --topology-csv <official20_lane_topology.csv> \
  --hop-decay 0.75 \
  --output <directional_lane_graph_hop075.npz>

python -m algorithms.prediction.train_narrow_net_tdp \
  --dataset-dir <lane206_tensors> \
  --graph <dynamic_candidate_edges.npz> \
  --directional-graph <directional_lane_graph_hop075.npz> \
  --output-dir <static_directional_lane_d2_hop075/formal> \
  --baseline-reference <baseline_reference.json> \
  --epochs 100 --patience 15 --batch-size 32 \
  --learning-rate 0.001 --weight-decay 0.001 --dropout 0.5 \
  --seed 42 --directional-max-scale 0.25
```

正式运行前应遵守 `docs/AUTODL_RUNBOOK.md`，先执行独立 smoke，再启动正式 GPU 训练。

模型二进制不进入 Git。最终 NarrowNet-TDP 交接包应作为 Release 附件交付，至少包含：

```text
official20_lane206_d2_hop075_handoff.tar.gz
└── lane206_v1/
    ├── graph/official20_lane_adjacency.npz
    ├── directional/directional_lane_graph_hop075.npz
    └── formal/
        ├── d2/{best.pt,last.pt,metrics.json,config.json}
        ├── xgb/{model.json,metrics.json}
        ├── baseline metrics, tensor metadata, results table, SHA256SUMS.txt
        └── manifest and filtered metadata
```

原始 lane206 基线交接包 SHA-256：`89fe16f0a05a6bef48d845fadb4e4d82fd82fb3fdcbd30034d82a5e7591278db`。

部署方需要的最小推理契约为：206 个图节点的既定顺序、4 个输入特征顺序、12 帧历史窗口、训练集拟合的归一化参数、静态车道图、NarrowNet-TDP 方向图以及 `best.pt`。本交接不包含后端部署改动。
