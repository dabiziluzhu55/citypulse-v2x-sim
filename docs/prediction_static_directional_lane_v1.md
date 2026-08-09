# 窄路密网拓扑感知方向传播网络（NarrowNet-TDP）

## 目的

雄安新区 20 路口、206 条进口车道是连续串联的窄路密网。车辆沿 SUMO
连接关系向下游传播，信号排队又会沿相反方向向上游回溢。现有静态 Cheb
基线使用对称邻接矩阵，保留了空间邻近关系，但没有区分这两个方向。

本实验保持静态 Cheb 主干和 `[batch, 4, 12, 206] -> [batch, 206]`
契约不变，只增加固定的道路方向残差消息分支：

- `downstream`：把上游车道状态发送到可达的下游车道；
- `upstream`：使用 `downstream.T`，把下游拥堵状态发送回上游车道。

两条分支的残差系数从 0 初始化，并限制在有界范围内。实验开始时模型
等价于静态 Cheb，训练后才会使用方向信息。

## NarrowNet-TDP（D2 实现版本）

NarrowNet-TDP 在 D0 的方向信息基础上进一步区分关系来源：

- `direct_transition` 单独作为直接下游/上游分支；
- `next_target` 单独作为跨路口传播分支；
- `next_target` 的边权按 `0.75 ** (hops - 1)` 衰减，跳数范围为 1--4；
- 每个方向的分支仍使用有界残差，`directional_max_scale=0.25`；
- 静态 Cheb 主干、数据划分、训练参数和 `seed=42` 均保持不变。

NarrowNet-TDP 的方向图由官方拓扑 CSV 提供跳数，不使用未来车辆观测：

```bash
python -m algorithms.prediction.build_directional_lane_graph \
  --candidate-graph <dynamic_candidate_edges.npz> \
  --topology-csv <official20_lane_topology.csv> \
  --hop-decay 0.75 \
  --output <directional_lane_graph_hop075.npz>
```

## 拓扑来源

`build_directional_lane_graph.py` 从现有 SUMO 车道候选图提取
`direct_transition` 和 `next_target` 关系，不使用未来车辆观测，因此没有
数据泄漏。当前官方 206 车道图提取出 238 条下游关系及其转置上游关系；
横向车道关系继续由静态 Cheb 主干处理。

## 运行方式

```bash
python -m algorithms.prediction.build_directional_lane_graph \
  --candidate-graph <dynamic_candidate_edges.npz> \
  --output <directional_lane_graph.npz>

python -m algorithms.prediction.train_narrow_net_tdp \
  --graph <dynamic_candidate_edges.npz> \
  --directional-graph <directional_lane_graph.npz> \
  --dataset-dir <lane206_tensors> \
  --output-dir <independent_experiment_dir>
```

正式 NarrowNet-TDP（D2）训练额外使用 `--directional-max-scale 0.25`，并将方向图传入
`--directional-graph`。输出目录应独立保存 checkpoint、metrics 和 SHA-256 清单。

## NarrowNet-TDP 正式结果

以下结果使用相同的 lane206 数据划分和 `seed=42`，预测 horizon 为 60 秒：

| 集合 | MAE | RMSE | WMAPE |
| --- | ---: | ---: | ---: |
| 验证集 | 0.6765 | 1.1675 | 0.3270 |
| ID 测试 | 0.6741 | 1.1605 | 0.3269 |
| OOD 测试 | 0.7271 | 1.2488 | 0.3558 |

相较静态 Cheb 控制，NarrowNet-TDP 的 MAE、RMSE 和 WMAPE 在三套集合上均下降；OOD RMSE 下降约 3.1%。

正式比较仍使用相同的数据划分、随机种子和 60 秒预测 horizon，并额外按早高峰、平峰、晚高峰以及高停车车道统计结果。

本文件记录 NarrowNet-TDP（D2 实现版本）的方向模型细节；项目最终交接以
`docs/prediction_current_official20_lane206_v1.md` 为准。
