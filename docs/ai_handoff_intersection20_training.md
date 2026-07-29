# CityPulse 官方 20 路口预测：AI 交接文档

更新时间：2026-07-29（Asia/Shanghai）

## 当前目标

训练第一版**路口级**交通流预测模型：

- 节点：官方定义的 20 个目标路口 `demo_1`–`demo_20`；不是全路网全部 junction。
- 输入：过去 60 秒（12 个、每 5 秒一个）四项历史特征：
  `vehicle_count`、`halting_count`、`mean_speed`、`occupancy`。
- 标签：未来 60 秒的路口 `vehicle_count`。
- 模型：多特征 XGBoost（CPU 基线）与四特征 STGCN（GPU 1）。
- 切分：18 train、3 validation、3 ID test、6 OOD test，共 30 个独立 SUMO episode。

不要恢复此前的 lane-level STGCN：它将 2,714 条活跃车道当作节点，训练极慢且不符合当前“一个路口一个节点”的目标。该 STGCN 已暂停。

## 数据与映射

学校服务器路径：

```text
实验根目录：/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1
项目：/home/kemove/devdata1/zyh_v2x_ai/repos/citypulse-v2x-sim
源快照：/home/kemove/devdata1/zyh_v2x_ai/experiments/official20-stage1/source-simulation-sumo-1686970
```

- 30 个原始 lane CSV 位于 `raw/`，均已完整核验。
- 17 个从 AutoDL 迁移的原始副本仍保留在 `incoming_autodl_remaining_20260729/raw/`。
- 20 路口聚合数据：`intersection20_v1/raw/`，已完成 30/30。
- 官方权威映射：
  `source-simulation-sumo-1686970/data/maps/sumo/generated/manifests/tls_manifest.json`。
  它含 20 个目标路口和共 206 条入口车道。
- 聚合口径：`vehicle_count`、`halting_count` 对入口车道求和；`mean_speed` 按车辆数加权平均；`occupancy` 对入口车道取均值。
- 图：20 节点空间 3-nearest-neighbour 图，`intersection20_v1/adjacency.npz`，40 条无向边。

注意：原始 SUMO 网络中有更多普通 junction 和 TLS；它们不是本实验节点。先前提到的“97”仅是全图 TLS 数量，不属于当前研究范围。

## 当前训练状态

已完成：

- 30 个 SUMO episode 采集与迁移；无需重新跑 SUMO。
- 20 路口聚合冒烟：`721 × 20 = 14,420` 行/episode，成功。
- 20 节点、四特征 XGBoost 与 STGCN 冒烟均成功。
- 已启动完整 30 episode 的路口级正式训练，输出目录：
  `intersection20_v1/formal/`。

正式训练将输出：

```text
intersection20_v1/formal/xgb/metrics.json
intersection20_v1/formal/stgcn/metrics.json
intersection20_v1/formal/xgb.log
intersection20_v1/formal.log
```

训练时 XGBoost 使用 CPU，STGCN 使用学校服务器 GPU 1。不要使用 GPU 0。

## 关键代码

- `algorithms/prediction/aggregate_intersection20_snapshots.py`：车道 CSV 聚合为官方 20 路口。
- `algorithms/prediction/build_intersection20_adjacency.py`：构造可复现的 20 节点 3-NN 空间图。
- `algorithms/prediction/prepare_stgcn_episode_dataset.py`：已支持 `--feature` 多特征和 `--adjacency` 外部图。
- `algorithms/prediction/train_xgboost_stage1.py`：四特征 XGBoost 基线。
- `algorithms/prediction/train_stgcn_stage1.py`：四特征 STGCN。
- `scripts/aggregate_official20_intersection_v1.sh`：可恢复的 30 episode 路口聚合。

## 建议检查命令

本机通过 SSH alias `346-4090` 连接学校服务器。勿输出或复制 SSH 私钥。

```bash
ssh 346-4090 '
base=/home/kemove/devdata1/zyh_v2x_ai/data/experiments/official20-prediction-v1/intersection20_v1
test -s "$base/formal/xgb/metrics.json" && echo XGB_DONE || echo XGB_RUNNING
test -s "$base/formal/stgcn/metrics.json" && echo STGCN_DONE || echo STGCN_RUNNING
tail -n 30 "$base/formal.log"
nvidia-smi -i 1
'
```

完成后，读取两个 `metrics.json`，并与同一 20 路口数据上的 persistence 基线比较 MAE、RMSE、MAPE、WMAPE。不要拿此前 lane-level XGBoost 或 lane-level STGCN 结果与本结果直接比较；它们的节点和标签粒度不同。

## 运行环境

- 学校服务器：2× RTX 4090；本任务固定使用 GPU 1。
- Python：`/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python`。
- 已安装：PyTorch CUDA、XGBoost 3.2.0。
- AutoDL CPU 实例的数据已完整迁移；可保持关机。
