# 交通事件检测：验证与交接

更新时间：2026-07-30（Asia/Shanghai）

> 状态：当前事件识别基线。该文件对应规则型事件检测实现，后续模型化事件识别应另行建立带版本和状态的交接文档。

## 交接范围

这是基于 SUMO/TraCI 结构化车道状态的规则型交通事件检测基线，不使用 CARLA 图像或视频。检测器消费逐车道快照，输出检测记录和供前端、后端使用的事件卡片。

已演示的事件类型：

- `normal`：不产生告警；
- `lane_blocked`：车道封闭或明确的近零通行能力；
- `spillback`：下游排队回溢；
- `accident`：停驶/碰撞车辆导致的异常。

核心实现位于：

- 规则与批处理 CLI：`algorithms/event_detection/rules.py`
- 实时观察器：`algorithms/event_detection/ai_observer.py`
- 事件卡片：`algorithms/event_detection/cards.py`
- 状态协议：`algorithms/event_detection/state.py`
- SUMO 事件、快照导出：`simulation/sumo/events.py`、`simulation/sumo/export_snapshots.py`

## 已验证结果

所有验证均使用真实 SUMO/TraCI 快照和隔离的服务器产物。最初的 `demo_2` 冒烟验证确认了 normal、lane closure、spillback、accident 及 normal accident control；随后在拓扑和信号配置不同的路口扩展验证：

| 路口 / 随机种子 | 场景 | 结果 |
| --- | --- | --- |
| `demo_2` / 42 | normal | 2,440 条检测记录；0 告警、无卡片 |
| `demo_1` / 42 | normal | 2,150 条检测记录；0 告警、无卡片 |
| `demo_3` / 42 | normal | 1,168 条检测记录；0 告警、无卡片 |
| `demo_1` / 42 | lane closure | 37 条 `lane_blocked`；180 s 起在 `-56384_0` 生成卡片 |
| `demo_3` / 42 | accident | 24 条 `accident`；245 s 起在 `-52565_1` 生成卡片 |
| `demo_3` / 42 | spillback | 10 条 `spillback`；315 s 起在 `-50816_1` 生成卡片 |
| `demo_1` / 43 | lane closure | 与 seed 42 一致：37 条 `lane_blocked` |
| `demo_3` / 43 | accident | 与 seed 42 一致：24 条 `accident` |
| `demo_3` / 43 | spillback | 延长至 120--420 s 后，315 s 起产生 45 条记录和卡片 |

多路口验证产物位于：

```text
/home/kemove/devdata1/zyh_v2x_ai/runs/event_detection_multisite_20260729/
```

关键文件为 `normal_demo{1,2,3}_detections_*.csv`、`demo1_lane_closure_detections_v6.csv`、`demo3_accident_detections_v1.csv`、`demo3_spillback_detections_v1.csv` 及对应的 `*_cards_*.json`。

## 运行与接入约定

快照导出可使用隔离的产物目录：

```bash
python -m simulation.sumo.export_snapshots \
  --intersection demo_1 \
  --generated-dir /path/to/generated_artifacts \
  --session-root /path/to/session_files \
  --output /path/to/lanes.csv
```

实时观察器可通过 SUMO 本地 AI observer 机制接收帧。事故检测默认关闭，需显式设置 `EVENT_DETECTION_ENABLE_ACCIDENT=true`；`spillback` 由 `EVENT_DETECTION_ENABLE_SPILLBACK` 控制。

后端接入时应将每帧交给 `EventDetectionObserver`，保存最新事件卡片，并在 WebSocket 载荷和读取接口中暴露 `event_detection`。这份交接不包含后端部署改动。

多路口封闭场景使用 `lane_closure` 加 `max_speed: 0.1`，而不是硬性禁行；后者会使部分 SUMO 全局路线失效。绿色、已占用且显式近零 `current_allowed_speed_mps` 的车道会作为 `lane_blocked` 直接证据，不依赖降低通用 CUSUM 阈值。

验证命令：

```bash
python -m unittest tests.test_event_detection_state tests.test_event_detection_cards \
  tests.test_disturbance_events tests.test_session_cli tests.test_event_detection_evaluate
```

服务器验证时上述集合为 36 项通过。

## 当前边界与下一步

该实现已在 3 个路口验证四类场景，但仍是规则基线，不是召回率/精确率基准，也没有证明每种异常在每个路口均成立。事故检测的容量限制证据仍需额外核验：早期冒烟产物中的 `current_allowed_speed_mps` 曾为 `0.0`，当前事故演示主要依赖交通状态证据。

下一步应：扩展到更多信号配置和随机种子；建立带标注的精确率、召回率、误报率与检测延迟评估；完成实时后端接线；将事件输出接入协同信号控制策略并与固定配时比较。
