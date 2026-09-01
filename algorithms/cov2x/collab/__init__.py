"""CoV2X 协同层：三端联合策略、团队奖励与 CTDE 联合采样。

- joint_policy.py：JointPPOAgent（车端/路端/云端 actor + 共享 critic）
- joint_rewards.py：三端本地奖励与团队奖励
- joint_rollout.py：联合 rollout 与集中式训练数据组装
"""
