---
information_type: mixed
status: current
code_revision: 8efb0c5acc3aaac326e9b5dbf8d2106fa4fb220d
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

# AI 管控评估规范

用于判断 CityPulse-Qwen takeover **是否真正改善交通**。**【项目事实】** 交通效果优先复用 `traffic_eval.EvalResult`。**【规划功能】** 局部交通指标和全部 AI 工程指标当前代码尚未计算。

## 公平对比

必须固定：

- `scenario_preset_id`
- `period`
- 需求（含 `scenario_scope` 实际生效值）
- `seed`
- 扰动类型、目标、时窗
- `baseline_controller`（同一个 `control_mode`）

对比轴只有：

**AI takeover OFF vs AI takeover ON**

合法例子：

- `MAPPO + accident + AI OFF` vs `MAPPO + accident + AI ON`
- `fixed + lane_closure + AI OFF` vs `fixed + lane_closure + AI ON`

不合法例子：

- `Max Pressure` vs `Qwen + MAPPO`
- `SOTL` vs `Qwen`
- 把 Qwen 当成第六种普通 `control_mode` 去和 Fixed/SOTL 横比

这类设计无法分离 AI 独立效果。

## A. 交通效果

### 当前正式指标（项目事实）

交通效果评价以最新版 `traffic_eval.EvalResult` 和对应的专项指标口径为准。通行效率的标准核心指标为：

| 字段 | 含义 |
| --- | --- |
| `path_avg_speed_kmh` | 路径平均速度 |
| `travel_time_index` | 行程时间比 |
| `delay_time_proportion` | 延误时间比，范围为 0~1 |
| `traffic_performance_index` | 城市交通运行指数 |
| `traffic_state` | 交通运行状态 |
| `avg_stops_per_vehicle` | 路径平均停车次数 |
| `regional_max_queue_length_m` | 区域最大排队长度，单位为 m |
| `spillback_rate` | 溢流率，单位为 % |

安全和能源专项指标按对应专项口径纳入评估：`hard_braking_events`、`hard_braking_rate` 和 `fuel_intensity_L_per_100km`。它们分别反映急刹行为和燃油强度，不替代通行效率核心指标。

口径见 `efficiency_metrics.md`、`safety_metrics.md`、`emission_energy_metrics.md`。同条件下，行程时间比、延误时间比、交通运行指数、排队、溢流率、停车、急刹和燃油强度通常越低越好；路径平均速度以及完成交通任务的能力通常越高越好；运行状态越接近“畅通”越好。单一指标改善不能替代综合判断。

### 规划型局部指标（尚未计算）

| 规划指标 | 意图 |
| --- | --- |
| event scope maximum queue | 事件 scope 内进口最大排队 |
| queue spillback duration | 回溢持续时间 |
| recovery time | 事件结束后指标回到基线所需仿真秒 |
| post-event clearance time | 事件结束后排队清空时间 |

这些 **不是** 当前 `EvalResult` 字段。

## B. AI 工程指标（规划指标）

以下全部为规划指标，不要伪装成当前 metrics：

| 规划指标 | 意图 |
| --- | --- |
| LLM inference latency | 一次高层 plan 推理时延 |
| valid structured output rate | 可解析且通过 schema 的比例 |
| safety validation rejection rate | 安全/相位/范围校验拒绝比例 |
| fallback rate | 整单回退 baseline 的比例 |
| AI takeover duration | ACTIVE+RECOVERY 仿真时长 |
| number of AI decisions | 高层 plan 次数（不是 5 s signal step 次数） |
| controlled intersection count | 实际接管路口数 |

`avg_decision_latency_ms` 当前衡量的是 baseline 算法 step 延迟，不能拿来冒充 LLM latency。

## 报告要求

每组实验必须写明：preset、period、seed、disturbance、baseline、AI ON/OFF、AI 是否发生 fallback。若 AI ON 组因解析失败几乎全程 fallback，不得宣称“AI 改善了交通”。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 8efb0c5acc3aaac326e9b5dbf8d2106fa4fb220d
   - file: traffic_eval/models.py; traffic_eval/collector.py; traffic_eval/tripinfo.py
   - 用于支持：当前正式指标字段。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
2. FHWA Consistent Application of Traffic Analysis Tools
   - 发布机构：美国联邦公路管理局
   - URL：https://www.fhwa.dot.gov/publications/research/operations/11064/004.cfm
   - 用于支持：对比必须控制实验条件。
