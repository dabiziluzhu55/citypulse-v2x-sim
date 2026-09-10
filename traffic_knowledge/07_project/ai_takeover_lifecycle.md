---
information_type: project_fact
status: current
code_revision: 0847ae894e1456fa43d97c3332b1418399a04194
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

**【项目事实】** 事件范围的异步 AI 接管已由 Backend `takeover_orchestrator` 与 SUMO session 实现。用户必须在启动时为至多一个扰动设置 `ai_control_enabled=true`；运行中不能再把普通事件改成 AI 目标。规则检测卡片不能单独触发接管。

## 主路径

快照 `ai_takeover.state` 对前端展示 ACTIVE / RECOVERY 等业务态。执行层还区分是否正在安装计划、是否因规划暂停仿真（`PLANNING_PAUSED`）、以及 `is_currently_executing`。Copilot 必须以 `get_ai_takeover_status` 为准，不能只看事件上的 `ai_control_enabled`。

异常时立即 fallback 到用户原来的 baseline `control_mode`，仿真不因模型不可用而停止。

## 进入与退出

进入接管：存在启动时配置的 AI 扰动，事件生效，scope 属于当前 preset。

退出或回退：事件结束并完成恢复、仿真终态、计划校验失败、模型超时或不可用。CityPulse-Qwen 不会变成新的 `control_mode`。

Narrow-TDP 仅在 ready 且非 fallback 时，可用预测桶变化请求提前再规划。

## 与 baseline 的关系

整个生命周期中，用户选择的 `control_mode` 不变。AI 只是临时覆盖局部 intersection 的 `target_phase` 来源。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 0847ae894e1456fa43d97c3332b1418399a04194
   - file: backend/app/services/takeover_orchestrator.py; simulation/sumo/engine/session.py; backend/app/api/v1/copilot.py
   - 用于支持：当前 AI 接管触发、回退和 Copilot 查询口径。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
