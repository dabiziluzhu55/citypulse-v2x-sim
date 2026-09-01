# CityPulse-Qwen AI 接管与 Stage 7 验证说明

本文记录 `feature/perception` 分支当前已经实现的 Traffic Copilot、历史数据、RAG 和事件级 AI 信号接管能力，以及在个人隔离环境中的真实 SUMO 验证结果。

## 1. 当前链路

```text
SUMO 快照
  -> Backend IntelligenceHub / HistoryRecorder
  -> 事件级 TakeoverOrchestrator
  -> Qwen2.5-7B-Instruct + control profile RAG
  -> Backend 严格校验 AIControlPlan
  -> SUMO worker 再次校验并由 SafePhaseController 执行
```

AI 接管只对仿真启动请求中设置 `ai_control_enabled=true` 的扰动事件生效。普通事件和未启用 AI 的仿真继续使用原有基线控制器。

Qwen 只能返回严格 JSON 控制计划：每个受控路口提供 6 个连续 5 秒槽位的 `target_phase`。相位编号必须来自运行时拓扑提供的真实 SUMO 相位编号，不能控制允许范围外的路口或相位。黄灯、全红、最小绿灯和恢复过程由 SUMO worker 的安全控制器负责，Qwen 不直接访问 TraCI。

## 2. 已实现的主要功能

- Traffic Copilot Qwen Provider 和 Copilot API。
- 按仿真 session 保存的交通历史数据。
- Markdown 知识库、Qwen3-Embedding 和 Chroma 向量检索。
- 事件级 AI 接管状态机：`INACTIVE`、`ARMED`、`ACTIVE`、`FALLBACK`、`RECOVERY`、`FINISHED`。
- 事件范围内路口计算、历史/预测/RAG 上下文组合和 Qwen 控制计划生成。
- Backend 与 SUMO worker 两级计划校验。
- Qwen 或 RAG 不可用时回退原基线，不停止仿真。
- 事件结束后的安全恢复。

## 3. 真实 SUMO 验证结果

验证使用学校服务器上项目个人环境的隔离副本，未修改原始仓库或其他用户环境。Qwen 服务只监听服务器回环地址。

### 基线回归（AI 关闭）

使用相同路网、时段和随机种子的短时仿真结果：

| 基线 | 结果 |
| --- | --- |
| Fixed | `COMPLETED` |
| Max Pressure | `COMPLETED` |
| SOTL | `COMPLETED` |
| MAPPO | `COMPLETED` |
| IPPO | 启动阶段失败，原因是现有 checkpoint 与当前路网拓扑指纹不一致 |

IPPO 的失败发生在 AI 接管之前，属于已有模型/路网配置问题，不能作为 AI 接管通过结果。

### AI 开启场景

以下场景使用真实 Qwen、真实 Chroma RAG 和真实 SUMO worker：

- Fixed：事故、可用车道上的施工占道、大型活动开场、大型活动散场。
- SOTL：事故。
- Max Pressure：事故。
- MAPPO：事故。

通过特征：

- Qwen 返回合法控制计划，例如 `demo_3: [2, 1, 2, 1, 2, 1]`。
- RAG 状态为 `ready`。
- AI 状态能够从 `ARMED` 进入 `ACTIVE`，事件结束后经过 `RECOVERY` 回到 `FINISHED`。
- SUMO 最终状态为 `COMPLETED`，没有因 AI 计划产生仿真错误。

### 故障回退

将 RAG 明确置为不可用后：

- Qwen 调用次数为 0；
- 状态为 `INACTIVE -> ARMED -> FALLBACK -> RECOVERY -> FINISHED`；
- 原有 Fixed 基线继续运行；
- SUMO 最终仍为 `COMPLETED`。

这保证了知识检索不可用时不会让模型编造交通知识或阻断仿真。

## 4. 自动化测试

在隔离环境中已验证：

- AI 控制、计划校验、编排和 codec 测试：8 passed，另有 2 个需要完整 HTTP fixture 的测试未纳入该次单测命令。
- RAG 测试：10 passed。
- RAG 查询返回向量检索结果，`control` profile 不返回 baseline 算法原理文档。

## 5. 已知限制和后续工作

1. IPPO checkpoint 需要与当前生成路网重新对齐，或选择匹配当前拓扑的模型后再做 AI 接管测试。
2. Redis/Celery 分布式模式尚未完成真实服务器验收。
3. 还需要补充真实 FastAPI HTTP 服务、暂停/恢复/重启、越界计划和非法计划的端到端测试。
4. 目前是链路冒烟验证，尚未完成固定随机种子下 AI ON/OFF 的正式交通效果对照实验。
5. 某些施工占道候选车道本身存在 SUMO 路由不可达问题；验证时使用了可用车道，该问题与 AI 接管逻辑无关。

## 6. 部署约定

Qwen 推理服务和 Embedding 模型建议继续部署在 GPU 服务器内部，Backend 通过内网或 SSH 隧道访问。模型权重和 Chroma 二进制索引不提交 Git；知识切片、构建脚本、配置说明和本验证文档可以提交。
