# Traffic-Qwen 训练工作区

本目录是 **离线数据集生成与未来微调基础设施**，属于 `algorithms/` 实验侧，不进入正式部署镜像。

它不依赖正在运行的 FastAPI Backend，也不修改 CoV2X / IPPO / MAPPO / Max Pressure / SOTL / Fixed 的算法逻辑或 `traffic_eval` 正式口径。

## 阶段规划

1. **Stage 1** 数据集生成与 expert selection
2. **Stage 1.5** 评分校准 + 300s 中规模 pilot
3. **Stage 2** 正式数据集 V1 + Observation V2 + Qwen2.5-7B-Instruct QLoRA（当前）
4. **Stage 3** 模型评测与量化
5. **Stage 4** vLLM 部署
6. **Stage 5** RAG 更新
7. **Stage 6** Backend 异步 AI takeover 适配
8. **Stage 7** Frontend AI 决策全过程展示
9. **Stage 8** Docker 容器化

本阶段明确不做：AWQ / INT4 合并、vLLM 正式服务、Chroma/RAG、前端 AI 面板、TakeoverOrchestrator 在线控制、算法训练代码或 `traffic_eval` 指标改写。

Observation V2 schema 见 `dataset/OBSERVATION_V2.md`。正式训练使用 TRL prompt-completion 且 `completion_only_loss=true`；assistant JSON 被 `max_length` 截断则直接 FAIL。

## 运行环境

数据生成直接调用 in-process `SimulationManager` + libsumo，不启动 FastAPI。需要：

```bash
export SUMO_HOME=/usr/share/sumo
export PYTHONPATH=.
# 必须使用带 pip/libsumo 原生绑定的 Python；仅有 /usr/share/sumo/tools 桩文件会失败
```

当前 4090 服务器上可用：`/home/kemove/anaconda3/envs/v2x-ai-py310/bin/python`

## 已确认的现有动作空间

| control_mode | Protocol 2.0 `actions.signals` | `actions.vehicles` |
|---|---|---|
| `fixed` | 无 Protocol 2.0，SUMO 官方配时 | 无 |
| `max_pressure` | `target_phase` | 空 |
| `sotl` | `target_phase`（可省略表示保持） | 空 |
| `ippo` | `target_phase` | 空 |
| `mappo` | `target_phase` | 空 |
| `cov2x` | `target_phase` | `target_speed_mps` / `target_lane_index` |

V1 Signal SFT **只吸收 `teacher_action_space=signal_only` 的高置信 expert**。CoV2X 等车辆级动作完整保留在 Full Expert Dataset，并标记 `signal_sft_eligible=false`，禁止静默丢掉车辆动作后伪装成最优信号标签。

同场景、同 seed、同扰动下的多算法对比是 **episode-level expert selection**，不是严格反事实（各算法从 t=0 起策略不同，扰动发生时路网状态已分叉）。

## CLI

所有命令在仓库根目录执行：

```bash
# 只统计将生成多少场景，不跑 SUMO
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli plan \
  --config algorithms/traffic_llm/configs/pilot_v2.yaml

# 最小闭环 smoke test
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli generate \
  --config algorithms/traffic_llm/configs/dataset_v1.yaml \
  --smoke \
  --limit-scenarios 2 \
  --modes fixed,max_pressure,sotl

# 用 scoring_v2 对已有 runs 重选 expert（不重跑 SUMO）
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli score \
  --dataset outputs/traffic_llm_dataset/smoke_v1 \
  --scoring algorithms/traffic_llm/configs/scoring_v2.yaml

# 300s pilot（45 scenarios × 6 algorithms = 270）。断点续跑：
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli generate \
  --config algorithms/traffic_llm/configs/pilot_v2.yaml \
  --resume \
  --output outputs/traffic_llm_dataset/pilot_v2

# 正式 810 episode（先 plan，再 generate；split 在生成前冻结）
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli plan \
  --config algorithms/traffic_llm/configs/formal_v1.yaml

PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli generate \
  --config algorithms/traffic_llm/configs/formal_v1.yaml \
  --resume \
  --workers 1 \
  --output outputs/traffic_llm_dataset/formal_v1

# 用现有 Pilot raw runs 重建 Observation V2 SFT（不重跑 SUMO）
PYTHONPATH=. python -m algorithms.traffic_llm.dataset.cli build-sft-v2 \
  --dataset outputs/traffic_llm_dataset/pilot_v2 \
  --config algorithms/traffic_llm/configs/pilot_v2.yaml


`scoring_v1.yaml` 保留以便复现旧结果。`scoring_v2.yaml` 修正 TPI 方向、取消 winner-vs-fixed 二次归一化、只在最佳 Pareto front 内比较 ambiguity，并把扰动响应切到 `local_event_window`。

`generate` 默认不覆盖 `COMPLETED` 的 `<scenario, algorithm>`；`FAILED` 只有 `--retry-failed` 才会重跑。

`retention.mode=audit` 保留完整 1s snapshot；`compact` 只保留 SFT anchor snapshot，但 **不删除** 各算法 decision/action trace。

## 输出

生成结果默认写入 `outputs/traffic_llm_dataset/v1/`（已被根目录 `.gitignore` 忽略，不要提交大规模数据）。

并发默认 `workers=1`。大于 1 时使用独立 process，每个 process 一个 libsumo session。

## 评价分层

- **Event window**：全网 Snapshot 排队/溢流/速度/吞吐增量，不调用短窗 TripInfo。
- **Local event window**：事件目标路口 + 1-hop（preset ≤6 路口则用全部受控路口），供 scoring_v2 扰动响应使用。
- **Episode**：完整结束后调用现有 `traffic_eval`，同名指标不以另一套公式重算。
- **Recovery**：数据集专用，阈值全部来自 YAML；无法判定时 `recovery_time_s=null`。场景网格可配置 `required_post_event_horizon_s`，拒绝没有恢复窗口的 episode。
- **TripInfo reliability gate**：`completion_rate` 低于配置阈值时不删除 episode，只忽略 TPI/速度/燃油等截尾敏感指标。
