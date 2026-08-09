# MAPPO 最终交付：Faithful Cooperative MAPPO

> 本目录是算法端可交付的 MAPPO 实现，**只保留论文忠实版**
> `cooperative_joint_v1`（faithful cooperative MAPPO）。
> v2 / M1 / v1.1-AS（owner-conditioned）等实验线已 hard-stop 并移除，不在交付范围内。

## 1. 算法定义

| 组件 | 定义 |
|---|---|
| model version | `cooperative_joint_v1` |
| 决策粒度 | 同步 joint step：所有受控路口在同一时刻联合决策（`synchronized_all_intersections_v1`） |
| 奖励 | 共享团队奖励：各路口 v5a 局部奖励取均值后 clip[-3, 1]（`v5a_team_mean_raw_then_clip_v1`） |
| Actor | 分散式共享 `CandidateActor`，推理只依赖本地观测与相位特征，不读其他路口 |
| Critic | 集中式 `IsomorphicTeamValueCritic`：全局状态 pooling → 标量团队价值（`critic-scope=global`）或按 owner 的局部价值（`critic-scope=local`） |
| 回报目标 | 团队回报 GAE（gamma=0.99，lambda=0.95），`critic_target_scope=team_return` |
| 关键不变量 | joint 内所有 agent 共享同一团队价值；joint batch 必须完整（不允许丢 pending） |

两个可交付变体（同一套代码，仅 `--critic-scope` 不同）：

- `cooperative_mappo`：`critic-scope=global`，集中 Critic 输出共享标量团队价值；
- `cooperative_ippo`：`critic-scope=local`，每 owner 独立 Critic 价值，但仍用共享团队奖励与团队回报目标。

## 2. 代码结构

| 文件 | 责任 |
|---|---|
| `config.py` | `MAPPOConfig`：模型版本、reward/team/joint schema、超参、`algorithm_label` |
| `models.py` | 本地 `CandidateActor`、集中 `IsomorphicTeamValueCritic`、`MAPPOPolicy`（保留 `residual_*` 形参以兼容外部调用，但已忽略） |
| `features.py` | IPPO-v8 本地特征适配与 centralized-state builder |
| `reward.py` | v5a reward accumulator（无全局副作用） |
| `rollout.py` | requested/applied 对齐状态机、transition、terminal-aware GAE |
| `joint_rollout.py` | 同步 joint 状态机：begin/confirm/complete、团队价值校验 |
| `controller.py` | Protocol 2.0 采样器：joint 决策、团队奖励聚合、`finish` 完成/截断 |
| `parallel_train.py` | worker batch 校验、共享团队 GAE 组批、generation coordinator |
| `trainer.py` | 中心 PPO learner 与训练诊断 |
| `checkpoint.py` | 原子 checkpoint、兼容性校验与完整恢复（可读旧格式元数据） |
| `train.py` | 同步多 SUMO worker / 单 learner 训练入口 |
| `evaluate_checkpoint.py` | checkpoint 确定性评估入口 |
| `__init__.py` | Protocol 2.0 对外 entrypoint（prepare/initialize/step/finish/pop） |

## 3. 训练与评估命令

```bash
# 4 路口 smoke（随机初始化）
python3 -m algorithms.mappo.train \
  --model-version cooperative_joint_v1 --critic-scope global \
  --init random --intersections 4 --episodes 4 --workers 4 \
  --duration 120 --base-seed 93001 --period off_peak \
  --actor-init-seed 42 --critic-init-seed 43 --checkpoint-every 4 \
  --save algorithms/mappo/runs/cooperative_smoke_4tls/cooperative_mappo.pt

# 20 路口正式训练（默认保存 runs/cooperative_mappo_20tls.pt）
python3 -m algorithms.mappo.train \
  --model-version cooperative_joint_v1 --critic-scope global \
  --intersections 20 --episodes 160 --workers 8 --duration 300 \
  --base-seed 95501 --period off_peak --checkpoint-every 20 \
  --save algorithms/mappo/runs/cooperative_mappo_20tls.pt

# 评估
python3 -m algorithms.mappo.evaluate_checkpoint \
  --checkpoint <checkpoint.pt> --seeds 66501 66502 66503 66504 \
  66505 66506 66507 66508 --workers 8 --duration 300 \
  --period off_peak --output <evaluation.json>
```

默认 checkpoint 路径：`algorithms/mappo/runs/{algorithm_label}_{n}tls.pt`。

## 4. 最终结果（faithful cooperative MAPPO ep160，20 路口 off_peak）

- 训练：`runs/cooperative_mappo_v1/full_baseline_20tls_ep160_seed95501/global/cooperative_mappo_ep160.pt`
- SHA-256：`39775b3e3259d27d3b5061e4a167f79fddc21d7085c63322a561e0abb54e83e2`
- 评估（held-out seeds 66501–66508，8 worker，300 s）：
  - 吞吐 831.0 veh/h，完成车辆平均等待 11.04 s，平均排队 0.090 veh，
    平均车速 11.305 m/s，完成率 0.0827，油耗 13.876 L/100km，决策延迟 10.988 ms。
  - 相对 IPPO-v8 ep160：MAPPO 完成行程平均旅行时间更低（96.14 s vs 100.93 s）、
    推理延迟更低，但吞吐/到达数/排队/等待/完成率等其余指标在 8 个 held-out seed 上均落后于 IPPO-v8。
- 完整数字与机器可读报告：`runs/cooperative_mappo_v1/final_five_algorithm_eval_seed66501_66508/`
  （`FINAL_REPORT.md`、`final_five_algorithm_report.json`）。

## 5. 测试与健康检查

```bash
python3 -m compileall -q algorithms/mappo
python3 -m pytest algorithms/mappo -q          # 当前 253 passed
python3 algorithms/mappo/train.py --help
python3 algorithms/mappo/evaluate_checkpoint.py --help
```

## 6. 边界与约束

- 算法端只在 `algorithms/mappo` 内工作；不修改其他组代码、SUMO 路网/routes/tlLogic、
  后端与评价口径。
- 不 push GitHub main；本地提交需经确认。
- 旧版 checkpoint（cc_ippo/local-reward）可被 `checkpoint.py` 读取元数据，但不能恢复为
  cooperative 架构训练状态（架构不兼容，属预期）。
- 已知上游现象：`demo_19` / TLS 891 缺少 green link index 7，只记录不上报修改。
