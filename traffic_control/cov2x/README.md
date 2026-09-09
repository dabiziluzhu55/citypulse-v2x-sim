# CoV2X：frozen IPPO + Cloud / Vehicle

公共入口为 `traffic_control.cov2x`。完整 20 路口场景的默认候选为
`cv_joint_v1`：Road 使用冻结 IPPO，Cloud 产生 movement 速度控制许可，
Vehicle 在许可和合法动作约束下决定限速与变道。部署只进行确定性推理。

当前状态：训练选优结束，已绑定 generation 3；seen DEV 改善 2.207842404%；独立确认因评估误判中止，候选尚无独立结果。
当前文件不能作为已达到 3% 增益或正式验收通过的证明。

## 运行与模型选择

使用已有仿真启动方式选择算法 `cov2x`，场景为完整 `demo_1..demo_20`。
环境需具有项目所需的 PyTorch / NumPy，模式为 `COV2X_MODE=eval`。
checkpoint、特征版本、movement 顺序或依赖哈希不匹配时启动失败，不回退到随机策略。

旧候选 `cov2x_g30_temp_cap_u24` 与 `cov2x_joint_ep12` 继续保留。
`east_dense` / `west_dense` 子集默认保持旧候选；新版只支持完整 20 路口。
训练入口留在研究模块，部署入口不接受训练模式。

## 通信

六个逻辑方向均保留：车→路、车→云、路→车、路→云、云→路、云→车。
消息携带 episode、时间、有效期、movement 和关联请求；执行回执在后续 callback
反馈。云→路为信息与反馈通路，最终相位始终由 frozen IPPO 决定。

消息可通过返回值中的 `v2x`、`traffic_control.cov2x.drain_v2x_events()`
或事件 sink 导出。事件镜像不改变消息消费与控制时序。
现有仿真客户端会丢弃返回值顶层 `v2x`，平台展示尚未接入导出接口；
本次未修改 simulation、backend 或 frontend。

模型来源、特征版本及依赖哈希见
[模型清单](models/cv_joint_v1_manifest.json)。当前仅有两seed的seen DEV改善2.21%；独立确认未完成，不能宣称稳定达到3%提升。
