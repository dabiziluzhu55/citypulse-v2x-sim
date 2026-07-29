# 官方 20 路口预测模型交接

## 交接范围

本文仅用于模型打包与推理接入，不包含、也不授权 FastAPI、前端或后端部署改动。

主模型为 `official20-stgcn-v1`：STGCN-Cheb 直接预测官方 20 个路口在未来 60 秒的 `vehicle_count`。
对应代码版本为 `f0adbf3`（`feat(prediction): add official20 forecasting pipeline`）。

## 推理契约

| 字段 | 约定 |
| --- | --- |
| 节点 | 20 个官方路口，顺序以 `metadata.json` 为准 |
| 特征 | `vehicle_count`、`halting_count`、`mean_speed`、`occupancy` |
| 输入频率 | 每 5 秒一个快照 |
| 历史窗口 | 12 个快照（60 秒） |
| 输入形状 | 训练集 `normalization` 后的 `[batch, 4, 12, 20]` |
| 输出 | `[batch, 20]`，每个路口未来 60 秒的 `vehicle_count` |
| 降级策略 | 按路口计算最近 12 个快照的 `moving_average` |

当前模型的节点顺序如下：

```text
demo_1, demo_10, demo_11, demo_12, demo_13, demo_14, demo_15, demo_16,
demo_17, demo_18, demo_19, demo_2, demo_20, demo_3, demo_4, demo_5,
demo_6, demo_7, demo_8, demo_9
```

`normalization` 统计量仅由训练集拟合。特征顺序、节点顺序与邻接图均以模型包中的元数据为准，接入方不能按界面显示顺序自行推断。

## Release 与模型包位置

模型二进制不提交到源码分支，而是作为 GitHub Release 附件交付：

- Release 名称：`Official-20 prediction v1`
- 标签：`official20-prediction-v1`
- 当前 Draft Release：[GitHub Draft Release](https://github.com/dabiziluzhu55/citypulse-v2x-sim/releases/tag/untagged-e351d9fa95231c700d49)
- 发布后可从仓库 [Releases 页面](https://github.com/dabiziluzhu55/citypulse-v2x-sim/releases) 获取附件

当前 Release 仍是 Draft，只有仓库维护者可见；在交付给队长或其他成员前，需要在 GitHub 页面点击 **Publish release**。

附件 `official20-prediction-v1.zip` 包含：

```text
official20-prediction-v1/
  stgcn_best.pt
  xgboost_model.json
  model_manifest.json
  adjacency.npz
  normalization_and_nodes.json
  stgcn_metrics.json
  xgb_metrics.json
  results_summary_60s.csv
  sha256sums.txt
  README.md
```

其中，`model_manifest.json` 记录代码版本、STGCN 架构参数、输入输出形状与包内文件校验值；`normalization_and_nodes.json` 是训练元数据的脱敏摘录，不包含服务器路径、原始 episode 路径或凭据。

## 已验证文件

下列文件保留在学校服务器上，打包前后均应验证 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `stgcn/best.pt` | `de8eea0345dac842106a74ca6ef0db898fb6fae145ec595a8411fcfeb2ef4179` |
| `xgb/model.json` | `526f228852dc2a535cbab6557d76c9ef889a5623346ea8ae91383dcacc9fa88c` |
| `tensors/adjacency.npz` | `f89b3de246cea8e5b028b50000fd2ebb57d33178adb7833d6df805e8845448f9` |
| `stgcn/metrics.json` | `2996f50e8e66e5bccb1b61342c764040d0288ff9a32eacd91f3aa312d2a8dfb4` |
| `xgb/metrics.json` | `ea04234967cae2d37f5b7eaf1ee42085fdae9fd0e0f70079423cc870215920c2` |

本次验证环境：

```text
Python: v2x-ai-py310
PyTorch: 2.13.0+cu130
XGBoost: 3.2.0
训练 GPU: 物理 GPU 1（RTX 4090）
```

`docs/results/` 下的统一结果表也会随 Release 附件提供，其 SHA-256 记录在模型包的 `sha256sums.txt` 中。
