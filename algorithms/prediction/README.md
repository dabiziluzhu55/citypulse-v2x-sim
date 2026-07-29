# 交通流预测

## 当前目标

第一版预测固定信号控制下的车道级 `vehicle_count`：
使用过去 60 秒的车道状态，预测 60 秒后的车流量。

原始 SUMO 数据按 5 秒采样，保留：

- `vehicle_count`
- `halting_count`
- `mean_speed`
- `occupancy`

第一版 STGCN 只以 `vehicle_count` 作为输入和预测目标；
其余指标保留给后续多变量预测、事件识别和数据质量检查。

## 数据与切分

每次 SUMO 仿真为一个独立 episode。训练、验证、测试使用不同 episode，
历史窗口不会跨 episode，归一化统计量只由训练集计算。

## 当前模型

- `persistence`、`moving_average`、`historical_average`：基础对照；
- STGCN-Cheb：当前主模型，输入过去 12 个时间步，直接预测 12 步后的值。

## 产物

数据准备会生成训练、验证、测试 NPZ、邻接矩阵和 `metadata.json`；
训练会生成最佳模型权重和 `metrics.json`。

## 当前限制

当前仅验证固定信号控制下的单变量 `vehicle_count` 预测。
接入动态信号控制、相位特征或多变量预测时，需要重新定义数据集和训练方案。