# CityPulse-Qwen 输出与执行适配

本文区分 **30–60 s 高层 AI plan** 与 **约 5 s 的 signal action**。**【规划功能】** 的计划对象尚未实现；**【项目事实】** 的 Protocol 2.0 才是当前唯一可执行接口。

## 两层输出不可混用

| 层 | 周期 | 内容 | 是否已实现 |
| --- | --- | --- | --- |
| CityPulse-Qwen plan | 建议 30–60 simulation seconds | objective、scope、相位优先级、保护/限制意图、valid_seconds | 规划 schema |
| Protocol 2.0 signal action | 现有 `decision_interval`（默认 5.0 s） | `{intersection_id: {target_phase}}` | 项目事实 |

`preferred_phase` **不能**被理解为“在整个 30–60 s 内持续保持该相位”。它只是高层偏好；AI Plan Executor 每个决策周期结合实时状态、最小绿和下游占用，选择合法 `target_phase`。详见 `ai_plan_executor.md`。

## 当前系统允许的信号动作

**【项目事实】** Protocol 2.0 的算法 `step` 响应必须为：

```json
{
  "protocol_version": "2.0",
  "episode_id": "...",
  "step_id": 0,
  "actions": {
    "signals": {
      "demo_5": {"target_phase": 2}
    },
    "vehicles": {}
  }
}
```

约束：

- `actions` 必须恰好包含 `signals` 和 `vehicles`。
- 每个路口信号动作 **只能** 有 `target_phase` 键，值为官方相位整数，且必须属于该路口当前 runtime `phase_order`。
- 算法不得输出红黄绿灯色字符串。Worker 用官方相位模板的 `green` / `yellow` / `clearance` 调用 `setRedYellowGreenState`。
- `SafePhaseController` 在 GREEN 未满 `minimum_green`（默认 5.0 s）时不切换；切换必须经过官方方案中的 yellow 和 all_red（clearance）。
- 车辆动作契约存在，但 Qwen 第一版不应把车辆级动作当作主输出。

因此，当前可执行信号控制是：**对指定受控路口请求合法 `target_phase`，由安全状态机完成黄灯和清空。**

## 路口级计划与全局校验

**【规划功能】** 一份 AI plan 必须是 intersection-specific：每个受控路口单独给出可映射的相位优先级或 protect/avoid movement，而不是只给一个全局口号。

全局 plan validation 在执行任何路口动作前完成：

1. schema validation（可解析、类型合法）
2. scope validation（路口属于当前 preset 与 AI scope）
3. phase validation（所有引用的 phase 属于 **当前运行时** `phase_order`，不是 RAG catalog）
4. safety validation（不要求改黄灯/灯色；时间窗不超过仿真结束）
5. Executor 编译 `target_phase`
6. Worker `validate_signal_action` + `SafePhaseController`

RAG 文档中的 phase 信息只用于语义理解，**不能替代 runtime phase metadata**。

## 原子计划

第一版采用原子计划：任意关键字段非法、安全检查失败或模型输出不可解析，**整个 AI plan fallback baseline**，不进行半合法半非法执行。

`confidence` 可以保留，用于审计和展示，**不要凭空设绝对阈值**（例如“低于 0.6 必须拒绝”）。拒绝条件以解析失败和校验失败为准。

## 规划 schema 字段

字段不要当成已实现 API。建议语义：

| 建议字段 | 作用 | 编译约束 |
| --- | --- | --- |
| `controlled_intersections` | 本周期接管路口 | 必须是当前 preset 子集 |
| `objective` | 防回溢、入口限流、走廊疏散 | 只解释，不进 TraCI |
| `priority_movements` | 优先进口或转向 | 必须能映射到该路口官方相位 |
| `phase_priority` | 候选相位相对优先级 | Executor 在决策周期内选择 |
| `preferred_phase` | 偏好相位，**不是锁相** | 必须在 runtime `phase_order` 内 |
| `avoid_movement` / `protect_movement` | 少放或保护 | 能映射则编译；否则未来 Adapter |
| `upstream_gating_intent` | 上游限流意图 | 规划字段，需未来 Adapter 支持 |
| `downstream_spillback_protection` | 下游保护意图 | 规划字段，需未来 Adapter 支持 |
| `max_green_extension` | 最长延长 | 规划字段，需未来 Adapter 支持 |
| `valid_seconds` | 计划有效期，不是锁相时长 | 过期后停止使用该 plan |
| `confidence` | 0–1 自评 | 无绝对阈值 |
| `reason` | 依据 | 审计 |
| `fallback_to_baseline` | 立即放弃接管 | true 时整单恢复 baseline |

第一版不要发明“改周期长度”“改黄灯”“改路网”或“直接写灯色”字段。

## 安全校验清单

- 路口存在且在当前会话 catalog 中；
- 路口位于允许的 AI 控制范围；
- `target_phase` 属于当前 runtime 合法相位集合；
- 最小绿灯、黄灯、全红由现有状态机保证；
- AI 不得拼装新灯色；
- 控制持续时间不超过 AI control window 和 simulation end；
- 会话状态为可控制（非 stopped/failed）。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_control/protocol.py; simulation/sumo/algorithm/policy.py; simulation/sumo/engine/run.py; simulation/sumo/engine/signal.py
   - 用于支持：可执行动作、相位校验和安全过渡。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
