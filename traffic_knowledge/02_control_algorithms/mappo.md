# MAPPO 多智能体近端策略优化控制

## 定义

MAPPO 是面向协作多智能体任务的 PPO 变体，常用集中训练、分散执行：训练价值函数可利用更广信息，部署 actor 使用各主体可获得的观测。它不意味着部署时各路口直接交换任意控制指令。

## 当前项目输入与输出

部署控制器复用 IPPO-v8 的 132 维、20 身份槽局部观测和 11 维候选相位特征。策略对每个受控路口输出合法相位索引，再映射为 `target_phase`。动作 mask、最大绿保护、动作间隔和安全过渡边界与部署契约共同约束执行。

## 与 IPPO 的区别

两者部署输入形式相近，关键差别来自训练：IPPO 以独立 PPO 路径训练；当前 MAPPO checkpoint 使用 cooperative policy 及其元数据契约。不能只看文件名断言 MAPPO 在所有密网场景必然优于 IPPO，协同收益必须通过同条件多种子评估证明。

## 适用与谨慎场景

路口间距较短、相邻队列相互影响、局部动作外部性明显时，可优先把 MAPPO 纳入比较。若当前场景是训练全集的局部子集、需求明显分布外、模型元数据不匹配，或需要可审计的规则解释，则应谨慎，保留 Fixed/Max Pressure 对照和回退路径。

## 当前项目实现与可运行性

- 实际名称：`mappo`；模块：`traffic_control.mappo`；模型别名 `mappo_cooperative_20tls_ep160`。
- 支持 `xiongan_20`、`east_dense`、`west_dense`；东/西局部预设为 20 路口模型子集零样本推理。
- checkpoint 格式、132 维观测、相位特征 schema、动作间隔、最大绿和受控路口子集会在初始化校验。
- 当前状态：注册表和 checkpoint 均存在，可运行但依赖 torch、训练代码中的特征构建模块和契约通过。
- 根 README 的管控模式表尚未列 MAPPO；产品注册表和 Backend 校验代码才是本知识库采用的事实源。
- LLM 推荐：允许；必须经过 Backend 校验，并明确模型适配风险。

## 来源

1. Yu et al., The Surprising Effectiveness of PPO in Cooperative, Multi-Agent Games
   - 发布机构：NeurIPS 2022 / arXiv 原始论文
   - 年份：2021–2022
   - URL：https://arxiv.org/abs/2103.01955
   - 用于支持：MAPPO 的协作多智能体 PPO 背景与经验边界。
2. MAPPO 官方实现
   - 发布机构：marlbenchmark
   - 年份：持续维护
   - URL：https://github.com/marlbenchmark/on-policy
   - 用于支持：原算法官方代码来源。
3. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/mappo/controller.py; traffic_control/mappo/contract.py; traffic_control/mappo/aliases.py; traffic_control/registry.py
   - 用于支持：项目部署特征、模型、场景限制和 README 差异。
