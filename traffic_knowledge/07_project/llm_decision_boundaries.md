# CityPulse-Qwen 决策边界

CityPulse-Qwen 是 **面向突发交通扰动的知识增强局部多路口协同信号管控模型**。**【规划功能】** 接管、编排器与 Executor 尚未实现；本文同时约束规划设计和当前代码能力。

## 可以做

- 理解扰动事件（注入真值：类型、目标、时窗、状态）。
- 分析交通状态（仅使用 Snapshot 真实字段）。
- 使用短时预测（路口级聚合结果；数值预测由 NarrowNet-TDP / moving_average 提供）。
- 使用道路拓扑（runtime 邻接 + RAG catalog，catalog 不得覆盖 runtime）。
- 使用 RAG 专业知识（事件响应、协同、约束、系统能力）。
- 生成局部多路口结构化 AI Control Plan。
- 在用户明确授权后，经校验与 Executor **临时**参与实际交通控制（编译为 `target_phase`）。

## 不可以做

- 默认控制所有会话，或在无扰动、用户未启用 AI 时接管。
- 替用户选择 baseline controller，或在 `fixed` / `sotl` / `max_pressure` / `ippo` / `mappo` 之间做算法推荐/选择。
- 直接调用 TraCI / libsumo。
- 绕过 Backend / Worker。
- 输出任意灯色字符串。
- 绕过 `SafePhaseController`。
- 控制当前 preset 以外路口。
- 编造不存在的 phase。
- 编造系统不存在的接口、算法或路口。
- 把自己登记为新的普通 `control_mode`。
- 每个 SUMO step 推理，或把 30–60 s 高层 plan 理解成锁死单一相位。
- 把知识库条目或预测值当作当前实时观测。
- 自己做数值型短时交通流预测。

## 必须经过的校验与执行链

**【规划功能】** AI plan 必须整体经过：

```
schema validation
  → scope validation
  → phase validation
  → safety validation
  → deterministic executor / adapter
  → Protocol 2.0 target_phase
```

然后由 Worker 经 `validate_signal_action` 与 `SafePhaseController` 写入 SUMO。

失败、超时或不可解析时，**必须整体 fallback 到原 baseline controller**，不得半合法半非法执行，也不能让信号灯停止工作。

**【项目事实】** 当前可落地的信号动作只有 `target_phase`。规划字段若无法映射到该动作，不得声称系统能执行。

## 证据优先级

1. 运行时实时状态
2. 当前代码事实
3. 当前场景配置
4. 项目事实型 RAG
5. 交通专业知识
6. 规划设计知识

规划设计知识不得覆盖实时事实。runtime payload 与 RAG 冲突时，runtime 优先。

## 建议执行流程

1. Backend 生成只读上下文（见 `ai_runtime_context_contract.md`）。
2. 按 `rag_retrieval_policy.md` 检索。
3. CityPulse-Qwen 输出高层结构化计划，不输出底层灯色。
4. 校验后由 AI Plan Executor 在现有 `decision_interval` 内编译 `target_phase`。
5. Worker 经 `SafePhaseController` 执行；scope 外保持 baseline。
6. 按 `ai_takeover_lifecycle.md` 在窗口结束或恢复条件满足后退出。
7. 记录输入、检索片段、模型输出、校验结果和最终执行相位。

## 来源

1. GB/T 39900-2021《道路交通信号控制系统通用技术要求》
   - 发布机构：国家市场监督管理总局、国家标准化管理委员会；公安部主管
   - 年份：2021
   - URL：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=8ACBD03506C5D9D2727D803850885CFE
   - 用于支持：现实信号系统具有独立安全要求，模型输出不能替代校验。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - revision: 1331ba87d6cd77e9052953d894a5dc83e1953009
   - file: traffic_control/registry.py; traffic_control/protocol.py; simulation/sumo/engine/signal.py; backend/app/services/prediction_runtime.py
   - 用于支持：现有控制模式、可执行动作和预测职责。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim
