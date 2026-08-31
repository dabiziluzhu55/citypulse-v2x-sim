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

# AI takeover 生命周期

**【规划功能】** 下列状态机尚未实现。当前代码没有 `ai_enabled`、takeover 状态或 Qwen 编排器。

## 主路径

```
INACTIVE → ARMED → ACTIVE → RECOVERY → FINISHED
```

异常：

```
任何 ACTIVE / RECOVERY → FALLBACK → BASELINE
```

`BASELINE` 不是独立业务状态，表示受控路口的 `target_phase` 来源恢复为用户原来的 `control_mode`。FALLBACK 之后会话继续跑，只是 AI 不再写入 signals。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| INACTIVE | 用户未启用 AI。全程 baseline controller。 |
| ARMED | 用户已选择启用 AI，但注入事件尚未开始（`SCHEDULED`）或尚未满足接管条件。 |
| ACTIVE | 从 event start 到 event end，AI 对 `controlled_intersections` 临时接管。 |
| RECOVERY | 事件已结束（`COMPLETED`），但局部排队可能仍在，继续恢复控制。 |
| FINISHED | 恢复 baseline，takeover 结束。 |
| FALLBACK | 模型超时、JSON 解析失败、schema/scope/phase/safety 失败、RAG/编排异常等。立即回到 baseline。 |

推荐时间关系：

- AI start ≈ event start
- AI end ≈ min(event end + recovery_seconds, simulation end)
- `event duration ≠ AI control duration`

## 进入与退出

进入 ARMED：用户显式启用 AI，且存在已提交的注入事件。规则检测卡片不能单独武装。

进入 ACTIVE：事件 `state=ACTIVE`，scope 非空且属于当前 preset。

进入 RECOVERY：事件 `COMPLETED` 或到达 `end_seconds`，但恢复条件未满足。

进入 FINISHED：恢复条件满足，或到达仿真结束。把相位决策交还 baseline。

进入 FALLBACK：任一安全/解析/超时失败。第一版建议 **整单退出 AI**，不保留半合法计划。

## 待实现策略（当前未实现，禁止写成已有行为）

| 情况 | 待实现策略 |
| --- | --- |
| pause / resume 仿真 | 暂停期间不应调用 Qwen 生成新 plan；恢复后应重读最新 snapshot 再决定是否继续 ACTIVE。 |
| 仿真提前结束 | takeover 立即 FINISHED；不得在会话销毁后继续下发相位。 |
| 用户提前取消事件 | 事件进入 `CANCELLED` 后进入 RECOVERY 或直接 FINISHED；不得假装事件仍在。 |
| 多个事件同时出现 | 第一版建议拒绝第二个 AI takeover，或合并为单一原子 plan 覆盖并集 scope。未实现前不得并行两套 plan。 |
| 两个 AI scope 重叠 | 待实现：按路口互斥，同一路口同一时刻只接受一份 plan。 |
| 新事件发生在已有 AI takeover 内 | 待实现：中止当前 plan 并重新生成原子 plan，或保持原 plan 直到 valid_seconds 结束。需显式策略，不能静默叠加。 |
| recovery 尚未结束又出现新事件 | 待实现：RECOVERY → ACTIVE，scope 按新事件重算。 |

## 与 baseline 的关系

整个生命周期中，用户选择的 `control_mode` 不变。AI 只是临时覆盖局部 intersection 的 `target_phase` 来源。FINISHED / FALLBACK 后不得把 Qwen 登记成新的 control_mode。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: simulation/sumo/engine/events.py; backend/app/schemas/events.py; traffic_control/registry.py
   - 用于支持：事件状态与 baseline controller 不变。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
