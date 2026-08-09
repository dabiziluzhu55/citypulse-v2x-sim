# [Archived] 静态车道图 + 车道/路口双层模型 v1

本阶段是在官方 20 路口、206 条入口车道 lane206 任务上增加一个最小的层级结构。旧的 persistence、moving average、historical average、XGBoost 和冻结静态 STGCN 仍然是参考结果，不重新训练。

## 冻结契约

- 节点：已有 206 条入口车道，沿用 lane206 metadata 的字典序；
- 路口：官方 TLS manifest 中的 20 个 `demo_N`，按路口 ID 字典序；
- 输入：`[batch, 4, 12, 206]`；
- 输出：`[batch, 206]`，仍然是每条车道未来 60 秒的 `vehicle_count`；
- 训练、验证、ID/OOD 划分、归一化参数和指标保持不变。

## 映射产物

`algorithms.prediction.archive.experiments.build_lane_junction_mapping` 从三个冻结输入生成一个可审计的 NPZ：

```powershell
python -m algorithms.prediction.archive.experiments.build_lane_junction_mapping `
  --metadata <lane206-tensors>/metadata.json `
  --tls-manifest <official20>/tls_manifest.json `
  --lane-graph <official20-lane-graph>/official20_lane_adjacency.npz `
  --output <experiment>/hierarchy/lane_junction_mapping.npz `
  --report <experiment>/hierarchy/lane_junction_mapping.json
```

产物包括：

- `lane_order`、`junction_order`；
- `lane_to_junction_index`；
- 行归一化的均值 `pooling_matrix[20,206]`；
- 一热 `broadcast_matrix[206,20]`；
- 从现有 direct/downstream lane 关系聚合出的 `junction_adjacency[20,20]`；
- lane order、junction order、映射、pooling 和路口图 hash。

生成器会拒绝缺失、重复、额外车道，以及图节点顺序不一致的输入。

## 模型结构

`algorithms.prediction.archive.experiments.static_hierarchical_lane_model` 保留原 `static_cheb` 的两层车道时空块：

```text
车道输入
  → 206 节点 static Chebyshev STGCN
  → pooling 到 20 个路口
  → 20 节点 static Chebyshev 图卷积
  → broadcast 回所属车道
  → 有界残差融合
  → 原车道级输出头
```

路口残差的初始系数为 0，因此新模型初始化时不会破坏 `static_cheb` 控制；训练后才由数据决定是否使用路口上下文。当前不加入动态 gate、不改变后端和实时推理接口。

## 训练入口

归档的 `train_dynamic_lane_v1.py` 保留了 `static_hierarchical` 模式：

```powershell
python -m algorithms.prediction.archive.experiments.train_dynamic_lane_v1 `
  --dataset-dir <lane206-tensors> `
  --graph <dynamic-candidate-edges.npz> `
  --mapping <lane_junction_mapping.npz> `
  --output-dir <new-independent-run>/formal `
  --baseline-reference <baseline_reference.json> `
  --spatial-mode static_hierarchical `
  --epochs 100 --patience 15 --batch-size 32 `
  --learning-rate 0.001 --weight-decay 0.001 --dropout 0.5 --seed 42
```

正式 GPU 运行前仍需先完成 CPU/小样本 dry-run，并使用新目录保存 checkpoint、metrics、mapping 和 SHA-256 清单。
