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

# RAG 检索策略

**【规划功能】** 本文约束一次 AI control retrieval 的文档优先级与禁止项。检索实现尚未接入。

可用过滤字段见 `manifest.json` 的 `documents[]`：`information_type`、`status`、`applicable_events`、`applicable_presets`、`code_revision`。

## 一次 AI control retrieval 的顺序

1. 当前项目能力事实（registry、protocol、SafePhaseController、API）
2. 当前 preset / 路口拓扑（`intersection_topology_catalog.md`、场景定义）
3. 当前信号能力与相位语义（`signal_phase_movement_catalog.md`、输出 schema）
4. 当前 event 专业知识（事故、占道、限速、开场、散场）
5. 多路口协同与事件响应信号控制原则
6. 安全 / 信号约束（标准术语、最小绿、黄灯、清空）
7. 场景特定知识（校园片区、窄路密网、雄安背景）
8. 历史仿真案例（`08_simulation_cases/`；当前多为模板，证据弱）

同一次检索应同时带上：运行时上下文协议、Executor、takeover 生命周期，避免模型只看到专业原则却不知道当前能执行什么。

## 信息类型过滤

| information_type | 用途 |
| --- | --- |
| `project_fact` | 回答“系统现在能做什么 / 字段叫什么” |
| `traffic_expertise` | 回答“交通工程上通常应怎样管” |
| `planning` | 回答“准备怎么接 Qwen”，**不得**当作已实现 API |
| `mixed` | 必须按段内【项目事实】/【规划功能】标签再拆 |

## 禁止

- 把规划设计文档当作“当前已实现 API”证据。
- 用 RAG catalog 的 phase 表覆盖 runtime `phase_order`。
- 用通用理论覆盖 Snapshot 实时观测。
- 检索到 Fixed/SOTL/Max Pressure/IPPO/MAPPO 原理后，让模型去“选择一个算法”。这些文档只解释 baseline，不提供推荐算法任务。

## 冲突处理

runtime payload 与 RAG 冲突时，**runtime payload 优先**。完整优先级见 `ai_runtime_context_contract.md`。

## 来源

1. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_knowledge/manifest.json
   - 用于支持：文档元数据过滤。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
