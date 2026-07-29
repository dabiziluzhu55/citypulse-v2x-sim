# 事件检测冒烟测试交接（2026-07-29）

## 交接范围

本次交付是 `demo_2` SUMO 上基于规则的结构化交通状态事件检测基线，不使用 CARLA 图像或视频。检测器消费逐车道的 SUMO/TraCI 快照，输出检测记录以及可供前端、后端使用的事件卡片。

已支持演示的事件：

- normal：无告警；
- lane closure：输出 `lane_blocked` 卡片；
- downstream queue：输出 `spillback` 卡片；
- stopped/crashed vehicle：输出 `accident` 卡片。

## 已验证结果

服务器冒烟测试使用真实 SUMO 事件注入并导出快照；对应的 normal 对照均为 0 告警。

| 场景 | 结果 |
| --- | --- |
| normal | 0 告警 |
| lane closure | 识别 `lane_blocked` 并生成卡片 |
| queue spillback | 11 条检测记录、1 张 `spillback` 卡片 |
| accident | 10 条检测记录、生成 `accident` 卡片 |
| normal accident control | 0 告警 |

事故注入的方式是停止一个已存在的 SUMO 车辆，不是动态创建虚拟车辆。

## 关键代码与产物

- 规则与批处理 CLI：`algorithms/event_detection/rules.py`
- 事件卡片生成：`algorithms/event_detection/cards.py`
- 事件语义：`algorithms/event_detection/semantics.py`
- SUMO 事件与快照字段：`simulation/sumo/events.py`、`simulation/sumo/session.py`、`simulation/sumo/export_snapshots.py`
- 使用说明和数据契约：`algorithms/event_detection/README.md`

服务器产物独立存放于：

```text
/home/kemove/devdata1/zyh_v2x_ai/runs/event_detection_smoke_20260729_loader_compat/
```

其中包含 normal、spillback、accident 的 `*_lanes.csv`、`*_detections.csv` 与 `*_cards.json`。

## 给后端的交接说明

后端已具备仿真创建、事件注入、指标计算与 WebSocket 快照流能力，但尚未执行或暴露该检测器。

后端接入时应：

1. 在仿真运行期间，将每个快照喂入 `EventDetectionObserver`。
2. 持久化或保留最新的检测卡片与摘要。
3. 在 WebSocket 载荷中加入 `event_detection` 卡片，并增加读取接口，例如 `GET /simulations/{id}/event-detection`。
4. 如果前端需要注入该场景，增加 `queue_spillback` 事件请求；当前请求 schema 仅支持 lane closure、speed limit、accident。
5. 需要事故检测时设置 `EVENT_DETECTION_ENABLE_ACCIDENT=true`；该能力在 observer 配置中默认关闭。

## 当前边界与后续工作

这是可用的单路口 `demo_2` 基线，不是泛化的生产级检测器。它仅在一个拓扑、固定事件参数及匹配的 normal 对照下完成验证。

下一阶段应在另外两个拓扑或信号配置不同的路口重复四种场景检查，再完成后端实时接线。

`current_allowed_speed_mps` 已为容量限制路径导出，但当前冒烟测试中其值为 `0.0`；成功的事故演示依赖交通状态证据路径。正式宣称容量限制型事故检测前，应先排查该问题。

## 本地验证

已提交测试命令：

```bash
python -m unittest tests.test_event_detection_state tests.test_event_detection_cards \
  tests.test_disturbance_events tests.test_session_cli tests.test_event_detection_evaluate
```

结果：33 项测试通过。
