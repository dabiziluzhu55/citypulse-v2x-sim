# 预测历史归档

这里保存已经完成、但不属于当前 NarrowNet-TDP 正式主线的代码。需要复现实验时，优先查看对应的归档交接文档和原始结果表。

- `experiments/`：动态图、层级模型和旧的多模式训练入口；
- `aggregation/`：TLS100、20 路口聚合及旧结果表；
- `legacy/`：旧预处理、简单基线和 sMAPE 诊断。

当前正式训练入口是 `algorithms.prediction.train_narrow_net_tdp`。该入口暂时复用
`experiments/train_dynamic_lane_v1.py` 的公共训练和结果封装逻辑，但会固定为
`static_directional`，不会把历史模式暴露给正式命令。
