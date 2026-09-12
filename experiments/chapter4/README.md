# 第四章附加实验一键批跑

独立实验目录，**不修改** `frontend` / `backend` / `simulation` / `traffic_control` / `traffic_eval`。

仿真、扰动、算法和指标全部复用现有接口：

- `SimulationManager` / `SimulationConfig`
- `SessionMetricsHub` + `traffic_eval` 指标口径
- `traffic_control.registry`
- `backend.app.scenario.presets`
- `simulation.sumo.engine.events` 五类事件

## 实验矩阵

默认参数：`duration=900s`，`seed=42`，`step_length=0.1s`，`decision_interval=5s`，`gui=False`，`realtime=False`。

同一实验组内不同算法使用完全相同的场景、时段、扰动参数和随机种子。

| 组 | preset | period | 扰动 |
| --- | --- | --- | --- |
| B1 | east_dense | morning_peak | 无 |
| B2 | east_dense | morning_peak | 大型活动开场 180–360s + 散场 540–720s |
| C1 | west_dense | morning_peak | 无 |
| C2 | west_dense | morning_peak | lane_closure 300–600s |
| C3 | west_dense | morning_peak | speed_limit 30km/h 300–600s |
| C4 | west_dense | morning_peak | accident 300–600s，position_ratio=0.6 |

目标车道不硬编码。脚本读取 `SimulationManager.catalog()`，在 preset 路口范围内按「路口顺序 × incoming × lane_id」选择合法进口车道；第一个候选不满足 `events.py` 规则时继续尝试下一个。实际使用的 `intersection_id`、`lane_id`、原始限速和事件参数写入 `experiment_manifest.json`。大型活动的 `source_lane_ids` / `destination_lane_ids` 留空，复用 `events.py` 自动路径选择。

`--modes all` 通过 `traffic_control.registry.list_control_modes()` 读取：`fixed,max_pressure,sotl,ippo,mappo,cov2x`。

6 组 × 6 算法 = **36 轮**。示例命令 6 组 × 4 算法 = **24 轮**。

## 一键命令

在仓库根目录执行。

只看矩阵、不启动 SUMO：

```bash
PYTHONPATH=. python experiments/chapter4/run_chapter4_experiments.py --list-only
```

四算法版本：

```bash
PYTHONPATH=. python experiments/chapter4/run_chapter4_experiments.py \
  --modes fixed,max_pressure,mappo,cov2x
```

完整六算法版本：

```bash
PYTHONPATH=. python experiments/chapter4/run_chapter4_experiments.py \
  --modes all
```

单组检查：

```bash
PYTHONPATH=. python experiments/chapter4/run_chapter4_experiments.py \
  --modes fixed --groups B1 --output-dir outputs/chapter4_experiments/verify

PYTHONPATH=. python experiments/chapter4/run_chapter4_experiments.py \
  --modes fixed --groups C2 --output-dir outputs/chapter4_experiments/verify
```

默认跳过输出目录中已经 `SUCCESS` 的 `experiment_id + algorithm + seed`。加 `--force` 强制重跑。

单个实验失败不会中断整批，错误写入 `run_status.json` 后继续下一组。

## 输出文件

默认目录：`outputs/chapter4_experiments/<timestamp>/`

| 文件 | 用途 |
| --- | --- |
| `experiment_manifest.json` | 每组实际参数、preset、路口、resolved lane、事件、seed、算法 |
| `results_raw.json` | 每轮完整原始结果（指标、决策时延统计、事件生命周期） |
| `results_summary.csv` | 一行 = 实验组 × 算法，列为主要评价指标 |
| `improvement_vs_fixed.csv` | 同组相对 fixed 的改善率 |
| `robustness_vs_normal.csv` | 同算法 B2 vs B1、C2/C3/C4 vs C1 的扰动前后变化 |
| `summary.md` | 可复制到 Word 的 Markdown 表，含 CoV2X 相对 Fixed 改善百分比 |
| `run_status.json` | 每轮 SUCCESS / FAILED |

`fixed` 没有算法推理，决策时延记为 `null` / `—`，不会写成 0 ms。

`SimulationManager` 与 `SessionMetricsHub` 必须使用同一个 `session_root`（`output_dir/sessions`）。否则 SUMO 会把 `tripinfo.xml` 写到默认 `outputs/sessions/`，评估脚本在实验目录找不到文件，TTI / TripInfo DTP / TripInfo 油耗不会回填。

## 已有结果离线后处理

不要重跑 36 次仿真。若旧实验已经把 TripInfo 写到 `outputs/sessions/<session_id>/`，用：

```bash
PYTHONPATH=. python experiments/chapter4/postprocess_existing_results.py \
  --output-dir outputs/chapter4_experiments/20260912_115540
```

该命令只读 JSON/XML 并调用 `traffic_eval.tripinfo.apply_tripinfo_official_metrics`，不启动 SUMO。原 `results_raw.json` / `results_summary.csv` 不会被覆盖。
