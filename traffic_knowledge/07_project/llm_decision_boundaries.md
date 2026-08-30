# Traffic-Qwen 决策边界

## 可以做

- 汇总实时快照、短时历史、预测和正式评估结果，研判交通状态。
- 输出正常、一般拥堵、严重拥堵等相对场景基线的等级，并说明证据。
- 判断候选事件类型和主要原因，区分注入真值、规则检测和外部确认。
- 只从 Backend 当前启用白名单中的 `fixed`、`sotl`、`max_pressure`、`ippo`、`mappo` 推荐策略。
- 指定重点路口/车道、观察窗口、替代解释、置信度和推荐理由。
- 在证据不足时返回“需更多数据/人工确认”，并列出缺失项。

## 不可以直接做

- 推荐未注册算法并要求系统执行，包括 CoLight/COSLight、SUMO actuated 或任意新算法。
- 自行修改 SUMO 路网、需求、相位方案、模型 checkpoint、系统配置或标准阈值。
- 绕过 Backend 调用 Worker、TraCI/libsumo、`traffic_control` 或文件系统会话。
- 将知识库条目、历史实验或预测值当作当前实时交通事实。
- 把规则候选事故说成已确认事故，把急刹率说成事故率，或把缺失指标填 0。
- 以机动车效率为由忽略黄灯、清空、最小绿、行人/非机动车和校园安全约束。

## 必须经过的 Backend 校验

建议应使用结构化对象，至少包含 `recommended_control_mode`、`scenario_preset_id`、`model_alias`（如适用）、`focus_intersections`、`evidence`、`confidence`、`fallback_mode` 和 `reason`。Backend 必须验证模式白名单、预设兼容、路口范围、模型别名、会话状态和参数范围；失败时拒绝或回退，不得直接透传。

## 证据优先级

实时观测 > 同会话短时历史 > 带 fallback 信息的预测 > 同条件正式评估 > 项目知识 > 通用理论。知识库用于解释“什么可能发生”，不能覆盖实时数据。若实时与预测冲突，应说明预测模型、历史是否充分和 fallback 状态。

## 建议执行流程

1. Backend 生成只读上下文并标注数据时间、来源和缺失。
2. LLM 输出候选研判与受限策略建议，不输出底层灯色。
3. Backend 做 schema、能力、场景和安全规则校验。
4. 可选的仿真评估或人工确认通过后，才把 `control_mode` 交给既有控制链路。
5. 记录输入、检索片段、模型输出、校验结果和最终执行模式，便于审计。

## 来源

1. GB/T 39900-2021《道路交通信号控制系统通用技术要求》
   - 发布机构：国家市场监督管理总局、国家标准化管理委员会；公安部主管
   - 年份：2021
   - URL：https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=8ACBD03506C5D9D2727D803850885CFE
   - 用于支持：现实交通信号系统具有独立技术与安全要求，LLM 建议不能替代控制系统校验。
2. citypulse-v2x-sim
   - source: citypulse-v2x-sim
   - branch: main
   - file: traffic_control/registry.py; backend/app/services/simulation_service.py; backend/app/scenario/resolver.py; traffic_control/protocol.py; traffic_eval/models.py
   - 用于支持：白名单、场景、协议、指标和执行边界。
   - URL：https://github.com/dabiziluzhu55/citypulse-v2x-sim

