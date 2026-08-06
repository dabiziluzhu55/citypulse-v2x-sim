# VRC 主算法预注册成功标准（Pre-registration Protocol v1）

- 日期：2026-08-06
- 状态：**冻结（v1）**（2026-08-06 评审通过；本版完成评审全部必改项后提交）
- 分支：`feature/rl`
- 提交位置：`docs/superpowers/specs/2026-08-06-vrc-preregistration-design.md`
- 范围：只定义评估与统计协议（成功标准）；技术路线（方案 3 最小验证）另文冻结

## 0. 目标与边界

主方法 = 以 CoSLight 为出发点、自研的车路云协同（VRC）算法。成功标准 = 主指标相对最强现有基线**大幅提升**，其余核心指标对基线**统计非劣**，且通过 NoCollab 因果对照证明提升来自“协同”本身。

边界（不可违反）：
- 算法与评估代码改动原则上限于 `algorithms/`；预注册文档位于 `docs/superpowers/specs/`；不修改 SUMO 路网、交通需求、backend 业务逻辑与前端。若统一 `official_metrics` 必须修改共享收集器，须在 M0 manifest 中列出版本化例外。
- 不触碰 `main`；只提交 `feature/rl`。
- 对本文档的任何修改都必须版本化（新增修订版本），禁止静默改阈值或种子。

## 1. 评估协议

| 项 | 冻结值 |
|---|---|
| 场景 | `xiongan_20`（demo_1..20，**全局 20 路口全量**） |
| 场景解析约束 | **不改后端**：不使用典型场景预设（east_dense/west_dense）与 backend 透传链；评估入口直接用全量 20 路口（`--preset xiongan_20` 或等价 `--intersections 20`，二者由 `algorithms/config/scenario_presets.py` 单一事实源解析）。M0 必须验证所有方法（含 MAPPO/IPPO/CoSLight/MaxPressure）在“不改后端”前提下都能跑全量 20 路口（历史证据：REPORT_step01.md 已用同一入口完成五方法 × 8 seeds） |
| 时段 | `off_peak` |
| 时长 | 300 s，SUMO step 0.1 s |
| 开发集 | `66501–66508`（8 seeds；架构/消融/超参/锚点选择） |
| 最终集 | `77501–77510`（10 seeds；全仓零命中已验证；代码/模型/阈值全部冻结后只跑一次） |
| V2X 通信（VRC 用） | 渗透率 0.6；上行/下行基础延迟 100 ms；抖动 uniform ±50 ms（最终延迟 uniform[50,150] ms，逐消息独立）；丢包 5%（逐消息独立 Bernoulli）；消息周期 BSM/INTENT/SPaT/RSM = 5 s |
| 通信种子 | `communication_base_seed = traffic_seed + 1_000_000`；`capability_seed = network_seed = communication_base_seed`（数值相同）；域分离由稳定哈希命名空间保证（见 1.1） |
| NoCollab | 同权重、同 checkpoint、评估时**协作输入置零**（zero-out，语义介入点见 1.2）；不使用通信网络、不消耗通信种子 |

### 1.1 V2X 随机性的可执行定义（已对照实现冻结）

实现位置：`algorithms/v2x/config.py`、`algorithms/v2x/entities.py`、`algorithms/v2x/hub.py`、`algorithms/v2x/messages.py`。

- 随机原语：`stable_hash01(s) = sha256(s)[:8] / 2^64`（`algorithms/v2x/messages.py`）。
- 渗透率（车辆级、整 episode 固定）：`v2x_enabled(vehicle_id) = stable_hash01(f"{capability_seed}|capability|{vehicle_id}") < penetration_rate`。**注意**：当前实现为 `f"{capability_seed}|{vehicle_id}"`（无 `capability` 域标签）；M0 前置任务要求补上 capability 域标签（仅改 `algorithms/v2x/entities.py`，见 4.6），使 capability 流与 jitter/drop 流显式域分离。
- 抖动（逐消息独立）：`jitter_ms = (2 × stable_hash01(f"{network_seed}|jitter|{message_type}|{source_id}|{seq}") − 1) × latency_jitter_ms`；`latency_jitter_ms = 50` ⇒ 延迟 uniform[50,150] ms。上下行基础延迟由 `message_type` 分流（uplink/downlink/default_latency_ms），上下行因此独立采样。
- 丢包（逐消息独立 Bernoulli）：`dropped = stable_hash01(f"{network_seed}|drop|{message_type}|{source_id}|{seq}") < drop_rate`。
- 消息周期：`bsm/intent/spat/rsm_interval_s = 5.0`；起始相位由消息生成循环决定，M0 在 manifest 中记录首条消息时刻。
- 当前 `algorithms/coslight/evaluate.py` 尚无设置 `V2XConfig` 的 CLI/环境入口（默认值全部生效）；M0 前置任务须在 `algorithms/` 内新增配置入口并 hash 锁定（见 4.6）。

### 1.2 NoCollab 语义介入点（冻结）

NoCollab 在 **delivered collaborator set 进入 learned Top-K 聚合之前**实施干预：

```
delivered V2X messages → [NoCollab: 协作者消息集合置空] → learned Top-K 聚合 → collaboration context → phase-conditioned collaboration logits
```

- 冻结公式：
  - Full：`z_j = z_j^policy + α·z_j^pressure + z_j^collab`
  - NoCollab：`z_j = z_j^policy + α·z_j^pressure`（collab context 与 collaboration logits 精确为零）
- **不得**清零本地观测、pressure prior、动作 mask 或非通信特征；Full 与 NoCollab 使用相同 checkpoint、相同策略参数、相同 prior scale。
- 具体 tensor 名称在方案 3 技术 spec 中落定；语义介入点本版即冻结，不再保留“V2X 消息 context 或 learned Top-K context 二选一”的表述。
- `shuffle-message` 仅作辅助负对照（验证模型利用的是正确消息而非一般噪声），**不替代 NoCollab**。

## 2. 指标与口径

### 2.1 主指标（per-seed 与 pooled 区分）

- per-seed：`M_{m,s} = allWaitingTotal_{m,s} / departedCount_{m,s}`（m = 方法，s = seed）。
- pooled：`M_m^pool = Σ_s allWaitingTotal_{m,s} / Σ_s departedCount_{m,s}`。
- 用途：
  - 开发集锚点选择：`M_m^pool`；
  - 7/10、Wilcoxon、中位相对改善：per-seed `M_{m,s}`；
  - 报告总体数值：同时报告 pooled 与 per-seed 分布。
- 字段来源：`algorithms/evaluation/tripinfo_diagnostics.py` 的 `parse_tripinfo_diagnostics()`（`all_waiting_total_s` / `trip_records`）；tripinfo 必须 `write-unfinished=true`（session.sumocfg 已含）。`departedCount = trip_records = completed + unfinished`。
- 新字段加入统一 per-run schema：`all_waiting_total_s`、`unfinished_waiting_total_s`、`departed_count`、`end_waiting_total_s`、`end_queue_veh`，以及对应 availability/provenance。所有方法（Fixed / IPPO / MAPPO / senior MaxPressure / VRC / NoCollab）使用同一实现；缺失字段判 `INVALID`，禁止退化到近似指标。
- 现状核对（2026-08-06，供 M0 参考）：MAPPO/IPPO(重跑)/Fixed/senior 的 per-run 已含 `all_waiting_total_s`、`unfinished_waiting_total_s`、`departed`、`trip_records`；尚缺 `end_waiting_total_s`、`end_queue_veh`；IPPO/MAPPO/senior 的安全 exposure 为 N/A。

### 2.2 主 H1（竞争力）

- 锚点：开发集上按主指标口径（pooled `M^pool`）复评锚点候选集合（见 2.7），取最低者为锚点；**锚点选择只看开发集**，最终集只做验收。
- 通过条件（三条件同时满足）：
  1. 10 个配对 seed 中至少 7/10 改善（`Δ_s = M_{VRC,s} − M_{anchor,s} < 0`；并列不算胜）；
  2. 配对单侧 Wilcoxon `p < 0.05`；
  3. 配对中位相对改善 ≥ 5%（`median_s((M_{anchor,s} − M_{VRC,s}) / M_{anchor,s}) ≥ 0.05`）。

### 2.3 第二主 H1（NoCollab 因果）

- 同权重、协作输入置零（介入点见 1.2），10 配对 seed：
  1. ≥ 7/10 改善；
  2. 配对单侧 Wilcoxon `p < 0.05`；
  3. `mean_s(M_{VRC,s} − M_{NoCollab,s}) < 0`（**seed 算术平均**，非 pooled）。
- 不设幅度门禁（因果证明，不承诺幅度）。

### 2.4 二级指标非劣门禁（UCB95/LCB95，全部 10 配对 seed）

| 指标 | 方向 | 门禁 | 定值 / 口径 |
|---|---|---|---|
| 平均行程时间 | ↓ | UCB95(VRC − anchor) ≤ +1.0 s | 预设容差；口径 = 全部已出发车辆（completed + unfinished，见 4.7） |
| 平均排队 | ↓ | UCB95(VRC − anchor) ≤ +0.005 veh | 预设容差（口径见 4.7） |
| 燃油强度 | ↓ | UCB95(VRC − anchor) ≤ +0.05 L/100km | **先过 M0 审计（4.1）**；审计通过后生效 |
| 吞吐 | ↑ | LCB95(R_T) ≥ 0.98 | R_T = (Σarr_VRC/Σhours_VRC)/(Σarr_anchor/Σhours_anchor)；本协议所有 seed 时长均为 300 s/step 0.1，可简化为 Σarr_VRC/Σarr_anchor；M0 校验时长一致，不一致则恢复含时长公式 |
| 未完成等待（per departed） | ↓ | UCB95(R_U) ≤ 1.05 | R_U = (Σunfin_VRC/Σdep_VRC)/(Σunfin_anchor/Σdep_anchor) |
| 端末等待积压 | ↓ | UCB95(R_endW) ≤ 1.05 | R_endW = (ΣendW_VRC/Σdep_VRC)/(ΣendW_anchor/Σdep_anchor)；锚点零 → 绝对门禁（2.6） |
| 端末排队积压 | ↓ | UCB95(R_endQ) ≤ 1.05 | R_endQ = (ΣendQ_VRC/Σdep_VRC)/(ΣendQ_anchor/Σdep_anchor)；锚点零 → 绝对门禁（2.6） |

口径注记（重要）：
- `avg_travel_time_s` / `avg_waiting_time_s` 的统计口径已更新为“全部已出发车辆”（`algorithms/evaluation/metrics.py` 未提交修改，见 4.7），不再只统计 completed。因此“沿用 MAPPO-v2（口径已核一致）”的表述不再成立；本版改为：δ 数值沿用既有预设，口径以本 spec 的 all-departed 定义为准，M0 复核量级并记录（不校准）。
- **定义重叠提示**：当前 `tripinfo_diagnostics.py` 中 `end_waiting_total_s ≡ all_waiting_total_s`（同一求和），故端末等待门禁在数值上与主指标 pooled 比率等价，属于冗余但无害的反作弊约束；若 M0 新增“端末时刻快照”类字段，必须版本化修订并重定义本行。
- `end_queue_veh` 为新增字段，定义见 2.6；`departed_count` 与 `trip_records` 等价，统一 schema 时二选一输出并互检。

### 2.5 安全门禁（两档制，聚合率，原始计数）

```
r_B = 1000 × ΣK_B / ΣpassageCount     （次/千次 passage）
r_C = 10000 × ΣK_C / ΣpassageCount    （次/万次 passage）
```

其中 `K_B` = 急刹车事件原始计数、`K_C` = 严重冲突事件原始计数、`passageCount` = 受控路口通行原始计数（全部为 seed 求和）。**manifest 必须保留原始计数**（`emergency_braking_event_count` / `severe_conflict_event_count` / `passage_count`），禁止只存预缩放字段。

| 指标 | 高事件 | 低事件 | 锚点零 |
|---|---|---|---|
| 急刹车 | r_B,A ≥ 100/千次：UCB95(RR_B) ≤ 1.10 | 0 ≤ r_B,A < 100/千次：UCB95(RD_B) ≤ +10/千次 | 执行低事件规则（允许极小新增，不要求零） |
| 严重冲突 | r_C,A ≥ 200/万次：UCB95(RR_C) ≤ 1.05 | 0 < r_C,A < 200/万次：UCB95(RD_C) ≤ +10/万次 | **ΣK_C,VRC = 0（硬规则，按整数事件数判断，不用浮点率）** |

- RR = r_V / r_A；RD = r_V − r_A（聚合率，非逐 seed 均值）；RD 输出单位：急刹 = 次/千次 passage，严重冲突 = 次/万次 passage。
- 碰撞字段可用后：碰撞采用零增加硬门禁（任何 seed 碰撞增加即失败）。
- 任一分组 exposure 为 N/A 或 10/10 seed 不完整 → 安全门禁 `INVALID`（不通过、不失败）。

### 2.6 端末积压零锚点绝对门禁

- `end_queue_veh` 定义为**评估结束时受控进口车道平均 halting count**（车道平均口径，与 `avg_queue` 同数量级；取队列时序最后一个观测值，精确采集实现由 M0 冻结）。锚点 pooled 端末排队为零 → `UCB95(mean_s endQueue_{VRC,s}) ≤ 0.05 veh`。
- `end_waiting_total_s` 锚点 pooled 为零（实际不可能）→ `UCB95(A_endW,VRC) ≤ 5.0 s/departed`，其中 `A_endW,VRC = Σ_s endWaiting_{VRC,s} / Σ_s departed_{VRC,s}`（**每 departed 车辆**，不是全网络总计 5 s）。
- 两个绝对门禁都作用在 **UCB95 置信上界**，不是点估计。

### 2.7 锚点候选集合（冻结）

实际参加开发集锚点竞争的基线共 **4 个**（当前仓库可复现）：

| 方法 ID | 身份 / checkpoint 或配置 | hash（sha256，除标注外） | 开发集复评命令 | 备注 |
|---|---|---|---|---|
| `fixed` | SUMO 官方固定配时（session 生成 fixed program，无 checkpoint） | 场景 net `ca0adc2e…` / 基线 route `82584cb9…`（见下） | `python -m algorithms.maxpressure_benchmark.evaluate --methods fixed --seeds 66501 66502 66503 66504 66505 66506 66507 66508 --workers 8 --duration 300 --intersections 20 --period off_peak --step-length 0.1 --output <out>.json` | 无决策延迟字段（非劣门禁不包含决策延迟） |
| `senior` | 组外 senior MaxPressure，pinned git ref `2df19c3ac3bda831d1dbec3a5c2f50f216f4b652`，源文件 `backend/app/controllers/max_pressure.py`（只读 `git show`） | git blob `56374ee198f70c70e85aaf10b0bd5ffc1d6969a0`；内容 sha256 `1f1db3d9…` | 同上，`--methods senior`（`MAXPRESSURE_SENIOR_REF` 默认即 pinned ref） | 由 `algorithms/maxpressure_benchmark/__init__.py` 加载 |
| `ippo_v8_ep160` | `algorithms/ippo/runs/v8_full160_pair3_step01/ippo_v8_20tls_ep160.pt` | `4055ec30bcd03c65572720cea38e51a338f466c351e21124be5fa683e6339449` | `python algorithms/ippo/evaluate_ckpt.py algorithms/ippo/runs/v8_full160_pair3_step01/ippo_v8_20tls_ep160.pt --seeds 66501 66502 66503 66504 66505 66506 66507 66508 --duration 300 --action-interval 15 --step-length 0.1 --output <out>.json` | REPORT_step01.md 同款命令 |
| `mappo_cooperative_ep160` | `algorithms/mappo/runs/cooperative_mappo_v1/full_baseline_20tls_ep160_seed95501_step01/global/cooperative_mappo_ep160.pt` | `2fef38f7fbdd90250770a2c5f5ac7b9a7e3e68e919259f0392c1b16076a276f1` | `python algorithms/mappo/evaluate_checkpoint.py --checkpoint <上述路径> --seeds 66501 66502 66503 66504 66505 66506 66507 66508 --workers 8 --duration 300 --period off_peak --step-length 0.1 --preset xiongan_20 --output <out>.json` | REPORT_step01.md 同款命令 |

明确排除（不参与锚点竞争）：
- `ours MaxPressure`：已于 commit `7c194eb` 从仓库删除（`algorithms/max_pressure/` 与 `maxpressure_benchmark` 的 ours 变体），当前仓库无法复现，不参与竞争；其历史 dev 集数值仅作参考（弱于 senior）。
- `CoSLight v16/v17`：当前仅有 `algorithms/coslight/runs/stage1_local_v16_lr1_8ep/checkpoints/coslight_parallel_ep8.pt`（sha256 `d836bcd0…`），评估仅 2 seeds（9901–9902）、top_k=5、pressure reward，不构成有效基线，不参与锚点竞争。若技术路线阶段建立 ep160/step0.1/8-seed 的有效 CoSLight 基线，须经版本化修订加入竞争。

场景/网络/route 身份（供 manifest 复用）：
- 网络：`data/maps/sumo/generated/network/TotalMap_20.signals.net.xml`（sha256 `ca0adc2edffe0b4db746e561771b56b87607995278e0c557561ac2f4a933bd34`）。
- 基线需求：`data/maps/sumo/generated/traffic/global/off_peak/routes.rou.xml`（sha256 `82584cb91d17bb6a294e4bddcddb2daae0af60df9aa4d2a3eda888cc0504d34c`）。
- 会话级 `session.sumocfg` 由 `simulation/sumo/session.py` 按 seed 生成（含 `--seed`、begin 0/end 300/step-length 0.1/time-to-teleport -1/emissions 1/write-unfinished true）；M0 记录生成器与首份 session 的 hash。

Tie-break（预先冻结，不允许 M0 临时决定）：
1. 锚点 = 开发集 pooled `M^pool` 最低者；
2. 并列（相对差 ≤ 1e-9）→ per-seed 中位数更低者；
3. 仍并列 → 固定顺序 `senior > mappo_cooperative_ep160 > ippo_v8_ep160 > fixed`（历史最强优先，仅用于确定性打破平局）。

### 2.8 评估动作方式（冻结）

- 所有模型方法（VRC / NoCollab / IPPO / MAPPO）评估时：模型处于 `eval()`；禁用 dropout 与探索噪声；使用 **deterministic argmax**。
- Full 与 NoCollab 使用相同 checkpoint、相同确定性动作生成路径，仅协作输入不同（1.2）。
- 本协议不采用随机策略采样；若未来改用随机采样，必须冻结动作 RNG 并对 Full/NoCollab 使用 common random numbers，且须版本化修订。

### 2.9 最终只跑一次与失败重跑规则（冻结）

- 产生有效正式指标 JSON 的运行即视为**一次最终暴露**。
- 在指标生成前因进程崩溃、SUMO 异常或文件损坏而失败，可原 seed 重跑，但必须保留失败日志并在 manifest 记录。
- 禁止因指标“不好看”选择性重跑。
- 一旦查看任何有效最终结果后修改 checkpoint、代码、阈值、场景或配置，`77501–77510` 立即视为已暴露，不得继续作为最终集。

## 3. 统计规程（统一）

- 主 H1 / NoCollab H1 的 Wilcoxon 与 win-rate：**复用** `algorithms/ippo/gate_stats.py` 的 `wilcoxon_one_sided_less` / `win_rate`（冻结实现，禁止另写一套或改参数）。
- RR / RD / 比例门禁（R_T、R_U、R_endW、R_endQ）：**配对 seed cluster bootstrap**（新实现，见下）：
  - 重采样单位 = seed 对；每次有放回抽取 10 对；簇内保留双方原始计数 `(K_V,s, E_V,s, K_A,s, E_A,s)`（比例门禁保留对应分子分母）。
  - 在该次重采样内聚合：`r_V^(b) = ΣK_V/ΣE_V`、`r_A^(b) = ΣK_A/ΣE_A`，`RR^(b) = r_V^(b)/r_A^(b)`、`RD^(b) = r_V^(b) − r_A^(b)`。
  - `b = 100_000`；RNG seed = `20260806`（`numpy.random.default_rng(20260806)`）；单侧 UCB95 = 95% 分位数、LCB95 = 5% 分位数。
  - 某次重采样分母为 0 → 跳过并记录；有效重采样 < 90% → 该门禁 `INVALID`。
  - 10/10 seed 必须有效；任一控制器 exposure 为 N/A → 安全门禁 `INVALID`。
  - 说明：`gate_stats.bootstrap_ci` 是单方法、双侧（b=2000，seed=20260804），不能直接用于双方法 RR/RD；`preregistration_vrc.py` 实现配对 cluster bootstrap，并对其“单方法率”退化情形与 `gate_stats.bootstrap_ci` 做区间一致性单测。
- 吞吐注记：off_peak 确定性需求下，若同方法内 `arrived` 在 seed 间恒定，R_T 的 bootstrap 分布退化为点估计；仍按同一规程计算，不另行处理，也不把“无方差”解释为更可靠。
- Overall 判定（**intersection-union，合取**，不额外做多重比较校正；禁止选择性忽略失败门禁）：
  - **PASS**：竞争力主 H1 PASS ∧ NoCollab 第二主 H1 PASS ∧ 全部二级非劣门禁 PASS ∧ 全部已启用安全门禁 PASS ∧ 不存在任何 mandatory gate = INVALID。
  - **FAIL**：任一可计算的强制 H1、非劣或安全门禁 FAIL。
  - **INVALID**：M0 未通过；数据/schema/exposure 不完整；任一强制门禁不可计算；最终评估不是 10/10 有效配对 seed。
  - **INVALID 语义**：整个验收**不可下结论**——INVALID 不是“不通过也不失败后仍可宣布成功”。

## 4. M0 审计任务（只审计，不校准）

1. **fuel telemetry 四查**（相同仿真轨迹上）：两条采集路径统计相同车辆集合；包含未完成车辆；距离分母相同；单位换算后仅数值误差。任一不满足 → fuel δ 重新推导（版本化）。若 `fuel_powertrain_vehicle_totals_legacy_ml` 与逐车协议累计在车辆范围/采样频率/缺失处理上不同，即使单位可换算也不能视为同一口径。
2. **exposure 补齐**：Fixed / IPPO / MAPPO / senior / VRC / NoCollab 全部产生 `passage_count` + `severe_conflict_event_count` + `emergency_braking_event_count` 原始计数（率由 2.5 公式计算）；明确 `passage` 定义与计数边界；所有方法一致；报告中说明同一车辆可贡献多个事件（当前率 >1000/千次，不能解释为“事件车辆占比”）。
3. **schema 统一**：所有方法 per-run schema 含 `all_waiting_total_s / unfinished_waiting_total_s / departed_count / end_waiting_total_s / end_queue_veh` + availability/provenance；IPPO 评估输出结构改造为同一 per-run schema。
4. **分档实现测试**：θ_B/θ_C、RR/RD、零锚点分支的单元测试与边界测试（含整数零判断）。
5. **量级复核**：用开发集锚点率复核“典型率量级”并记录为审计证据；**不得根据 M0 数据修改任何已冻结阈值**。若审计发现定义/数量级前提错误，必须通过新的版本化决策显式重开门禁。
6. **V2X 可执行定义落地（前置）**：
   - `algorithms/v2x/entities.py` 增加 `capability` 域标签（`f"{capability_seed}|capability|{vehicle_id}"`）；
   - `algorithms/coslight/evaluate.py` 增加 `V2XConfig` 配置入口（CLI/env，algorithms/ 内），使 1.1 的参数可复现；
   - 对 jitter/drop/penetration 公式写单测（与 1.1 的哈希字符串逐字一致）；
   - manifest 记录 `V2X config hash`、消息周期起始相位、`capability_seed/network_seed` 派生实现。
7. **metrics.py all-departed 修复（前置提交）**：`algorithms/evaluation/metrics.py` 的 avg_travel/avg_waiting 已改为“全部已出发车辆”口径（当前工作区未提交）；该修改作为 M0 前置提交项，与测试一起提交；**最终评估开始时 worktree 必须 `repository_dirty = false`**。

## 5. 统计脚本与 manifest

- 新增 `algorithms/evaluation/preregistration_vrc.py`：主指标、锚点选择、配对 cluster bootstrap、安全分档、门禁判定、manifest 生成与校验。
- 复用 `algorithms/ippo/gate_stats.py` 的 Wilcoxon / win-rate 原语（范围见 3）；配对 cluster bootstrap 为新实现并做退化单测。
- manifest：`algorithms/evaluation/preregistration_vrc_manifest.json`，内容至少包括：
  - 冻结种子（66501–66508 / 77501–77510）、δ/θ 表、bootstrap 参数（b=100_000, seed=20260806）；
  - `repository_commit`、`repository_dirty = false`、`selected_anchor_id`、`anchor_checkpoint_or_config_hash`、`all_anchor_candidate_hashes`；
  - 场景/网络/route/config hashes（2.7）、`SUMO version`、`evaluation action mode`、`metric collector hash`、`gate_stats.py hash`、`V2X config hash`、`NoCollab intervention hook/version`、`technical-route spec hash`；
  - M0 审计记录、最终评估 checkpoint hash、评估输出 hash、重跑/失败日志。
- 最终评估只跑一次；评估输出与 checkpoint 一起 hash 锁定；任何变更走版本化修订。

## 6. 依赖与后续

- 技术路线（方案 3 最小验证：动作相关 context + pressure prior 降权）单独成文；NoCollab 的 tensor 级载体随其冻结（语义介入点已在 1.2 冻结）。
- 本 spec 的修订必须版本化；禁止以“实现时发现”为由静默修改。

## 7. 冻结记录

| 日期 | 决策 |
|---|---|
| 2026-08-06 | 最终测试集换为 77501–77510（1042–1942 因 IPPO 已暴露降级为已知集）；主指标、锚点、δ 表、安全两档制、bootstrap 规程、通信配置、NoCollab 定义冻结 |
| 2026-08-06 | 用户约束：不改后端 → 场景仅限全局 20 路口全量（xiongan_20），禁用典型场景预设与 backend 透传链 |
| 2026-08-06 | 评审通过（有条件）：端末等待零锚点改为 UCB95 ≤ 5 s/departed；NoCollab 删除“二选一”、冻结语义介入点；区分 per-seed 与 pooled；锚点候选清单（4 候选 + 排除项 + tie-break）；Overall PASS/FAIL/INVALID 与 INVALID 语义；安全率原始计数与单位闭合；V2X 可执行定义；评估 deterministic argmax；失败重跑规则；manifest 身份字段；metrics.py all-departed 前置提交 |
