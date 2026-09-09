# CoV2X — CV Joint generation 3

唯一部署策略：frozen IPPO Road + 已训练 Cloud/Vehicle。入口为 `traffic_control.cov2x`，仅支持完整 `demo_1..demo_20`；不再提供旧 EP12、u24 或局部地图候选。

- `controller.py`：固定 callback 观测、六向消息调度、最终请求与回执。
- `deployment.py`：generation 3 模型、SHA、特征版本、movement 顺序及依赖校验；仅推理，加载失败报错。
- `cloud/policy.py`：movement 速度许可网络，每 15 秒决策，有效许可保持。
- `vehicle/policy.py`：许可下速度幅度与合法变道网络。
- `vehicle/speed_advice.py`：非累积限速执行器；`vehicle/feedback.py`：执行回执。
- `road/hooks.py`：纯 frozen IPPO 接口与生命周期恢复，不允许 Cloud 修改最终相位。
- `observations.py` / `contracts.py`：版本化观测、movement、消息与动作定义。
- `communication/transport.py`：实际消息总线、TTL 与许可；`bridge.py`：事件导出；JSON schema 声明 CVJointV1 消息。
- `model.py`：组合车云网络与 checkpoint 兼容状态价值网络。
- `models/`：唯一 gen3 权重、manifest、规范拓扑。冻结 IPPO 依赖在 `traffic_control/ippo/`。

六向逻辑通路：Vehicle→Cloud/Road、Road→Cloud/Vehicle、Cloud→Road/Vehicle。云→路仅上下文与反馈。消息可通过 response、drain、sink 导出；平台 UI 是否消费这些事件不由本模块保证。

只复制整个 `traffic_control` 即可运行，不依赖研究目录 `algorithms`。配置 `COV2X_MODEL_ALIAS=cv_joint_v1`、`COV2X_MODE=eval`；运行时使用三时段之一和完整 900 秒协议。旧模型与旧代码仅保留于 Git 历史。

验证：`python -m pytest traffic_control/cov2x -q`。严格 schema 测试需要测试依赖 `jsonschema`，推理无需此依赖。

历史 DEV 筛选有正增益，但独立确认未完成，不能宣称已验证稳定 3% 提升。本次仅整理部署目录，不更改参数、不训练、不运行 SUMO。
