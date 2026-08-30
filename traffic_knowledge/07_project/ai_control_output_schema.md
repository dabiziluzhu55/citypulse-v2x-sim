# CityPulse-Qwen 输出与执行适配

本文说明 AI 管控方案应如何落到当前 SUMO Worker 已能执行的动作。**【规划功能】** 的计划对象尚未实现；**【项目事实】** 的 Protocol 2.0 才是当前唯一可执行接口。

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
- 每个路口信号动作 **只能** 有 `target_phase` 键，值为官方相位整数，且必须属于该路口 `phase_order`。
- 算法不得输出红黄绿灯色字符串。Worker 用官方相位模板的 `green` / `yellow` / `clearance` 调用 `setRedYellowGreenState`。
- `SafePhaseController` 在 GREEN 未满 `minimum_green`（默认 5.0 s）时不切换；切换必须经过官方方案中的 yellow 和 all_red（clearance）。
- 车辆动作契约存在 `target_speed_mps` 和 `target_lane_index`，但当前产品信号算法不依赖它们完成控灯。CityPulse-Qwen 第一版不应把车辆级动作当作主输出。

因此，“当前系统支持哪些信号控制动作”的准确答案是：**对指定受控路口请求合法 `target_phase`，由安全状态机完成黄灯和清空。**

## 建议的上层计划对象

**【规划功能】** CityPulse-Qwen 按 30–60 仿真秒输出一份中短周期区域计划，而不是每个 decision_interval=5 s 直接扮演 SOTL。字段不要当成已实现 API。建议语义如下，实现时再冻结 schema：

| 建议字段 | 作用 | 编译到现有能力时的约束 |
| --- | --- | --- |
| `control_scope` | 本周期接管的 `intersection_id` 列表 | 必须是当前 preset 子集，且包含或邻接事件路口 |
| `objective` | 本周期目标，如防回溢、入口限流、走廊疏散 | 只用于解释和校验，不直接进 TraCI |
| `priority_direction` | 优先服务的进口或走廊方向 | 必须能映射到该路口官方相位所服务的 approach |
| `preferred_phase` | 路口 → 希望保持或切到的官方相位 | 必须在 `phase_order` 内 |
| `phase_priority` | 候选相位的相对优先级 | 并列时由适配器按当前相位、最小绿和下游占用抉择 |
| `max_hold_seconds` | 同一相位最长保持 | 不得超过计划 `valid_seconds`，且仍受最小绿约束 |
| `valid_seconds` | 本计划有效时长 | 建议等于一个 AI 决策周期 |
| `confidence` | 0–1 | 低于阈值则整单 fallback |
| `reason` | 简短依据 | 供审计和前端展示 |
| `fallback_to_baseline` | 是否立即放弃接管 | true 时该 scope 恢复原 `control_mode` |

第一版不要发明“改周期长度”“改黄灯”“改路网”或“直接写灯色”字段。这些当前代码不能安全执行。

## 计划到 Protocol 2.0 的适配原则

**【规划功能】** 执行适配器（建议名 AI Control Orchestrator）运行在 Backend 或 Worker 决策环，而不是模型进程内：

1. 校验会话仍在运行，AI control window 未结束。
2. 校验 `control_scope` 中每个路口存在、属于预设、属于允许的 AI 范围。
3. 校验 `preferred_phase` 属于该路口官方相位。
4. 若当前阶段是 YELLOW 或 CLEARANCE，或 GREEN 未满最小绿，则本步不强制切换。
5. 在计划有效期内，按 `preferred_phase` / `phase_priority` 生成 `{intersection_id: target_phase}`。
6. 调用现有 `_validate_actions` 与 `SafePhaseController.request_phase`。
7. scope 外路口不写入 AI signals，继续 baseline controller。

适配器失败时不得把半套非法动作送给 SUMO。

## 安全校验清单

无论计划如何措辞，执行前必须满足：

- 路口存在且在当前会话 catalog 中；
- 路口位于允许的 AI 控制范围；
- `target_phase` 合法；
- 最小绿灯、黄灯、全红清空由现有状态机保证；
- 相位冲突由官方相位模板保证，AI 不得拼装新灯色；
- 控制持续时间不超过 AI control window 和 simulation end；
- 会话状态为可控制（非 stopped/failed）。

## 低置信度与故障回退

**【规划功能】** `confidence` 过低、模型超时、JSON 解析失败、schema 失败或安全校验失败时，适配器对原 scope 停止下发 AI `target_phase`，这些路口立即回到 baseline controller。这是安全默认，不是可选提示。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 89e1a8173132fc734b4d0c51fb0b71fa36dd4b9d
   - file: traffic_control/protocol.py; simulation/sumo/algorithm/policy.py; simulation/sumo/algorithm/policy_transport.py; simulation/sumo/engine/run.py; simulation/sumo/engine/signal.py
   - 用于支持：可执行动作、相位校验和安全过渡。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
