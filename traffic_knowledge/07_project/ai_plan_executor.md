---
information_type: planning
status: current
code_revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
applicable_events:
  - accident
  - lane_closure
  - speed_limit
  - major_event_opening
  - major_event_closing
applicable_presets:
  - xiongan_20
  - east_dense
  - west_dense
priority: high
---

# AI Plan Executor

**【规划功能】** AI Plan Executor 是规划中的确定性执行器，不是当前代码模块。CityPulse-Qwen **不**每 30–60 s 选定一个 `preferred_phase` 并在整个窗口锁死该相位。

## 双时间尺度

| 层 | 时间单位 | 当前代码 | 规划角色 |
| --- | --- | --- | --- |
| SUMO | `step_length`，Backend 默认 0.1 s | 项目事实 | 仿真推进 |
| 现有交通控制 | `decision_interval`，Backend 默认 5.0 s | 项目事实 | baseline 与 Executor 选择 `target_phase` |
| CityPulse-Qwen | 建议 30–60 simulation seconds 生成一次高层 plan | 规划功能 | 目标、范围、相位优先级、保护意图 |
| SafePhaseController | 最小绿默认 5 s + 官方 yellow / all_red | 项目事实 | 合法过渡 |

`AI plan interval ≠ signal decision interval ≠ SUMO step`。

## 执行链

```
CityPulse-Qwen
  → high-level AI Control Plan
  → schema / scope / phase / safety validation
  → AI Plan Executor（在每个 decision_interval 读取计划 + 实时状态）
  → Protocol 2.0 {intersection_id: {target_phase}}
  → validate_signal_action
  → SafePhaseController
  → SUMO
```

CityPulse-Qwen 本身不调用 TraCI / libsumo，也不跳过 Backend / Worker。

## 高层 plan 适合描述什么

下列字段属于 **规划 schema**，不是已实现 API：

| 规划字段 | 意图 | 能否编译成当前 `target_phase` |
| --- | --- | --- |
| `objective` | 防回溢、入口限流、疏散走廊等 | 否，只用于解释与校验 |
| `controlled_intersections` | 本窗口允许接管的路口 | 能：限制 AI signals 写入范围 |
| `priority_movements` | 优先服务的进口/转向 | 若能映射到 runtime `phase_order` 中的相位，则能间接编译 |
| `phase_priority` | 路口候选相位相对优先级 | 能：Executor 在决策周期内按优先级选合法相位 |
| `avoid_movement` / `protect_movement` | 少放或保护某流向 | 若能映射到相位则能间接编译；否则 Adapter 未来支持 |
| `upstream_gating_intent` | 上游少向受损方向送车 | 只能通过少选“送入该方向”的相位间接实现；**规划字段，需未来 Adapter 支持**显式限流时长 |
| `downstream_spillback_protection` | 下游存储保护 | 同上，间接通过相位选择；**规划字段，需未来 Adapter 支持**占用阈值策略 |
| `max_green_extension` | 同一相位最多延长 | **规划字段，需未来 Adapter 支持**。当前协议没有独立延时动作，只能靠后续决策周期继续选择同一 `target_phase`，且仍受最小绿/黄灯约束 |
| `valid_seconds` | 计划有效期 | 能：过期后不再使用该 plan；**不是**锁相时长 |
| `confidence` | 模型自评 | 可保留；**不设绝对阈值**。不可解析或安全失败时整单 fallback |
| `reason` | 审计说明 | 否 |

不要输出灯色字符串、改黄灯、改周期、改路网或车辆级动作作为主输出。

## Executor 每个 decision_interval 做什么

**【规划功能】** 在现有 5 s 决策周期内读取：

1. 当前快照交通状态；
2. 仍有效的 AI plan（未过 `valid_seconds`，takeover 仍为 ACTIVE/RECOVERY）；
3. 各受控路口 `current_phase` / `stage` / `stage_elapsed`；
4. `SafePhaseController` 是否允许切换（GREEN 未满最小绿、或处于 YELLOW/CLEARANCE 时本步不强制切换）。

然后为每个 `controlled_intersection` 选择一个属于 runtime `phase_order` 的 `target_phase`：

- 优先满足 `phase_priority` 与可映射的 protect/avoid movement；
- 不得把 `preferred_phase` 理解为“未来 30–60 s 一直保持该相位”；
- 若当前相位已在为优先运动服务且下游占用高，可以在合法约束下保持，也可以按计划在下一决策周期切换。

scope 外路口不写入 AI signals，继续 baseline controller。

## 原子计划

第一版采用原子计划：任意关键字段非法、JSON 不可解析、schema 失败、scope/phase/safety 失败，则 **整个 AI plan fallback baseline**，不对部分路口半执行。

关键字段至少包括：`controlled_intersections`、各路口引用的 phase id、`valid_seconds`。`confidence` 不是单独的硬拒绝阈值。

## 失败与 fallback

模型超时、不可解析、校验失败、Executor 无法把意图映射到任何合法相位时：该 takeover scope 立即恢复 baseline `target_phase` 来源。仿真不得停止，用户原来的 `control_mode` 不得被改写。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_control/protocol.py; simulation/sumo/engine/signal.py; simulation/sumo/engine/run.py; backend/app/schemas/simulations.py
   - 用于支持：decision_interval、target_phase 与安全过渡。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
