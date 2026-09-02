# traffic_eval

部署侧公共交通评估

- Backend：`backend/app/metrics` 仅做封装，转发到本包
- 算法团队 / 二次开发：可直接 `import traffic_eval`，或用本机 CLI（**无需启动 FastAPI**）
- 与 `algorithms/evaluation`分离

指标按标准国家标准和交通行业标准制定，在微观交通仿真环境中构造等效计算；

## 无Backend本机评估

```bash
# 仓库根目录；需 SUMO_HOME + libsumo + 已生成 SUMO 产物
python -m traffic_eval \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_local.json
```

等价入口：`python -m traffic_eval.eval_cli ...`

## 经Backend HTTP评估（联调用）

需先启动 uvicorn，再跑：

```bash
python backend/tools/eval.py \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_backend.json
```

两边指标公式相同（均走 `traffic_eval`）；HTTP版多一层 API/会话编排。

## 模块

| 模块 | 作用 |
|------|------|
| `collector` | 从 `SimulationSnapshot` 采集排队/吞吐/临时油耗/急刹/溢流 |
| `tripinfo` | 终态 TripInfo 回填行程、等待、路径类标准指标、正式百公里油耗 |
| `tpi` | DTP→TPI 区间映射（GB/T 33171 附录 C + 项目内线性插值约定） |
| `powertrain` | 从 session/traffic manifest 读 powertrain 与燃油密度 |
| `session_hub` | 按 session 生命周期管理采集器 |
| `runner` | 无 Backend：直连 `SimulationManager` + `traffic_control` |
| `eval_cli` / `__main__` | 命令行入口（`python -m traffic_eval`） |
| `models` | `EvalResult` 与前后端字段映射 |

## 评测指标口径

数据由 `simulation` 提供，标准指标统一在本包计算
终态优先 TripInfo；时间型运行指标按仿真时间 `elapsed_seconds` 右端点加权，
不使用帧数当时间权重，也不受暂停wall-clock影响

样本集合：

- **完整路径类指标**（速度 / TTI / DTP / 停车次数）：`parse_completed_tripinfo`，
  即 `arrival>=0` 且未 vaporize 的车辆。未完成车辆不混入。
- **平均行程 / 平均停车等待**：`parse_departed_tripinfo`（含未完成），分母为 departed。
- **完成率**：`arrived/departed`，单独反映未完成车辆。
- **燃油强度**：已完成、未 vaporize、且 powertrain∈{gasoline,diesel,hybrid}。

`None` 表示不可计算，不用 0 冒充无数据。

比例与百分数严格区分：`delay_time_proportion` / `completion_rate` 为 0~1；
`spillback_rate` / `hard_braking_rate` 为百分数口径。

### 1. 标准核心指标

| 指标 | 字段 | 单位 | 数据源 | 公式 | 依据 | 说明 |
|------|------|------|--------|------|------|------|
| 路径平均速度 | `path_avg_speed_kmh` | km/h | TripInfo | ΣrouteLength / Σduration × 3.6 | GB/T 33171、GA/T 527.2 指标思想 | 总距离/总时间，不是单车速度算术平均 |
| 行程时间比 TTI | `travel_time_index` | - | TripInfo | Σduration / Σ(duration−timeLoss) | GB/T 33171 | 仿真等效：用 SUMO timeLoss 对应的无延误时间作自由流参考，不是实测自由流 |
| 延误时间比 DTP | `delay_time_proportion` | 0~1 | TripInfo | ΣtimeLoss / Σduration | GB/T 33171 | 模型层是比例；前端若显示百分数再 ×100 |
| 城市交通运行指数 TPI | `traffic_performance_index` | 0~10 | DTP 二次转换 | 附录 C 区间 + 档内线性插值 | GB/T 33171 附录 C | 插值是本项目连续显示约定，不是国标额外公式 |
| 运行状态 | `traffic_state` | - | TPI | [0,2)畅通 … [8,10]严重拥堵 | GB/T 33171 附录 C | |
| 路径平均停车次数 | `avg_stops_per_vehicle` | 次/车 | TripInfo `waitingCount` | ΣwaitingCount / N | GA/T 527.2 指标思想 | 不用 Snapshot 跳帧数停车 |
| 区域最大排队长度 | `regional_max_queue_length_m` | m | Snapshot 进口车道 `queue_length_m` | 评价期内 max | GA/T 527.2 指标思想 | 只统计 `role=incoming` |
| 溢流率 | `spillback_rate` | % | Snapshot 进口车道 queue vs 车道长度 | 溢流 lane·s / 有效 lane·s ×100 | GA/T 527.2 指标思想 | 进口车道-时间暴露率；车道长度是 SUMO lane length 近似，不是独立储车长度 |

TPI 方法字段：`tpi_method = "GB/T33171-2016 Annex C / DTP / piecewise-linear"`。

溢流判定：`queue_length_m >= lane_length_m`（仅保留数值 epsilon，不另设交通阈值）。
当前项目没有独立 `storage_length_m`，用进口车道 SUMO 长度作为有效储车长度近似。

### 2. 绿色低碳

| 指标 | 字段 | 单位 | 数据源 | 公式 |
|------|------|------|--------|------|
| 百公里燃油消耗 | `fuel_intensity_L_per_100km` | L/100km | TripInfo emissions | (Σfuel_ml/1000) / (ΣrouteLength/100000) |

运行中 Snapshot 为 `snapshot_provisional`；终态由 TripInfo 正式覆盖。

### 3. 辅助诊断指标

| 指标 | 字段 | 单位 | 说明 |
|------|------|------|------|
| 平均行程时间 | `avg_travel_time_s` / `avg_travel_time` | s | 全部已出发车辆；不是延误 |
| 平均停车等待时间 | `avg_waiting_time_s` / `avg_waiting_time` | s | 停车等待，不是完整延误；延误用 DTP |
| 进口车道平均排队车辆数 | `avg_queue_length_veh` / `avg_queue_length` | veh/lane | 不是米制排队长度；仿真时间加权 |
| 网络实际吞吐流率 | `throughput_veh_per_h` / `throughput` | veh/h | `arrived/duration×3600`，不是通行能力/capacity |
| 急刹事件 / 急刹率 | `hard_braking_events` / `hard_braking_rate` | 次、次/100辆 | |
| 完成率 | `completion_rate` | 0~1 | arrived/departed |

### 4. 工程性能指标

| 指标 | 字段 | 单位 | 说明 |
|------|------|------|------|
| 平均决策时延 | `avg_decision_latency_ms` | ms | 算法 perf_counter；Fixed 无样本为 null |

## 部署归属

- **Backend 容器封装**
- **SUMO Worker 容器封装**
- `traffic_control` 封装进SUMO Worker；`algorithms/` 不进部署镜像
