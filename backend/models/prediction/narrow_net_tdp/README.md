# NarrowNet-TDP后端运行时推理权重包（206车道/60秒）

本目录是Backend部署用的最小推理包，不依赖 `algorithms/`。

必需文件：

- `best.pt`
- `config.json`
- `model_manifest.json`
- `dynamic_candidate_edges.npz`
- `directional_lane_graph_hop075.npz`

默认通过环境变量：

```text
PREDICTION_MODEL_DIR=backend/models/prediction/narrow_net_tdp
```

相对路径相对仓库根目录解析。留空则短时预测降级为 `moving_average`。
