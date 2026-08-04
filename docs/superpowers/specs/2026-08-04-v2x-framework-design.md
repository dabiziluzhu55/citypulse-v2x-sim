# V2X 车路云协同消息框架设计（algorithms/v2x）

- 日期：2026-08-04
- 状态：已评审，待实现
- 范围：`algorithms/v2x/` 独立纯 Python 框架 + coslight 最小接入闭环

## 1. 背景与目标

CityPulse V2X Sim 目前通过 Protocol 2.0 让算法与 SUMO 交互，但只有 `signals`/`vehicles`
两类动作，**没有 V2X 消息模型**：仿真中的车不会"收发消息"，车-路-云协同没有显式数据流。
此前对 V17 相位动作的微观优化（collab_gate / collab_bias）属于"框架未定时的局部优化"，暂停。

本轮目标：在**算法层**建立可复用、可测试、可回放的 V2X 车-路-云消息框架：

1. 六类领域消息：BSM / INTENT / SPaT / MAP / RSM / RSI，外加平台控制事件 `SignalControlEvent`；
2. 意图通信（车→云转向/换道/到达意图，路→云相位时间表），框架默认推导 + 算法可覆写；
3. 基于仿真时间的轻量通信模型（固定延迟 + 可选抖动/丢包）；
4. 帧级关联（frame_id / correlation_id）、发送/投递日志、统计与回放；
5. coslight 作为第一个接入方，把现有决策"翻译"成 V2X 消息流跑通闭环，**不改 coslight 决策逻辑**。

非目标（本轮不做）：

- 不修改 `simulation/sumo/`（只读）；不直接调 TraCI；
- 不改变 coslight 的信号/车辆决策逻辑；
- 不接入 backend/frontend 产品链路（只预留 sink 接口）；
- 不做"云端等待消息投递后再决策"的异步模式。

## 2. 约束与边界

- `simulation/sumo/` 只读：所有车/路/云语义都在算法层实现；SUMO 只当"车端传感器 + 执行器"。
- 协议 2.0 是唯一数据源：`initialize`（静态拓扑/车型）、`step`（路口/车道/车辆状态）、
  `actions.signals` / `actions.vehicles`（控制输出）。
- 框架**纯 Python**，不依赖 torch、不导入 SUMO/TraCI，可独立单测。
- 通信时间一律使用协议 `simulation_time`，**不使用真实墙钟**。
- V16 部署默认（`stage1_local_v16_lr1_8ep`）保持不动；coslight 默认行为不变（接入开关默认关）。

## 3. 架构总览

```text
Protocol 2.0 (SUMO Worker 侧只读接口)
        │  initialize / step / actions
        ▼
algorithms/v2x/  V2XHub（进程内消息总线 + 通信模拟）
  ├─ 车端 OBU   ── BSM / INTENT ──> 云 Cloud
  ├─ 路端 RSU   ── SPaT / MAP / RSM ──> 云 Cloud
  ├─ 云 Cloud   ── RSI ──> 网联车
  └─ 云 Cloud   ── SignalControlEvent ──> RSU / 原协议信号动作适配器
        │
        ▼
  JSONL 日志（发送/投递记录）→ 统计 → 回放 CLI
```

## 4. 包结构

```text
algorithms/v2x/
  __init__.py     # 公共 API：V2XHub、V2XConfig、消息类型、create_hub()
  messages.py     # 消息 dataclass + 枚举（6 类领域消息 + SignalControlEvent）
  entities.py     # Vehicle/OBU、Intersection/RSU、Cloud 薄实体 + 通信能力判定
  hub.py          # V2XHub：publish/subscribe、延迟队列、两阶段 API、统计
  derive.py       # 意图推导纯函数（转向/换道/到达/相位时间表）
  coverage.py     # RSU 感知覆盖判定（车道 + 可选半径 + fallback）
  protocol.py     # 协议 2.0 适配器：initialize/step 载荷 → 消息；actions → RSI/事件
  logger.py       # JSONL sink（episode_start/message/delivery/episode_end）
  stats.py        # 通信统计、渗透率、RSM 覆盖率、RSI 漏斗、控制事件
  replay.py       # 回放/摘要 CLI：python -m algorithms.v2x.replay <log> [--summary|--print]
  adapters/
    coslight.py   # CoslightV2XBridge：evaluate runner 接入
  tests/          # pytest（纯单测，不碰 SUMO/torch）
```

## 5. 消息模型

### 5.1 公共基类

```python
@dataclass(frozen=True, slots=True)
class V2XMessage:
    message_id: str            # 全局唯一（episode_id + source + seq 派生）
    schema_version: str        # 默认 "1.0"
    episode_id: str
    frame_id: str              # 产生该消息的 Protocol step 帧
    sequence_no: int           # 按 (episode_id, source_id, message_type) 单调递增，每局重置
    sim_time: float            # Protocol 2.0 simulation_time
    source_id: str
    destination: str           # 具体实体 id，或 "*"（广播）
    correlation_id: str | None = None  # 串起"上报帧 → 决策 → 下发"；RSI/事件 = frame_id
```

### 5.2 六类领域消息 + 平台控制事件

| 消息 | 方向 | 内容 | 频率 |
|---|---|---|---|
| BSM | 网联车→云 | 位置/速度/加速度/车道/路线/next_signal；`front_gap_m`/`rear_gap_m` 可选 + `gap_source` | 每 `bsm_interval_s` |
| INTENT | 网联车→云 | 转向/换道/到达意图 + 字段级置信度 + `intent_origin` | 每 `intent_interval_s` |
| SPaT | 路端→云 | current_phase/stage/灯色 + 相位时间表（`remaining_time_s`/`next_stage`/`next_stage_start_time`/`schedule_status`） | 每 `spat_interval_s` |
| MAP | 路端→云 | 路口拓扑/相位/车道/连接/邻居（静态） | 每局 1 次，拓扑变化按版本重发 |
| RSM | 路端→云 | 批量 `RSMObject`：非网联使用者观测（位置/速度/车道/类别/置信度，无意图） | 每 `rsm_interval_s` |
| RSI | 云→网联车 | `target_speed_mps`/`target_lane_index`/引导类型；只发 `v2x_enabled=True` | 每帧（有动作时） |
| SignalControlEvent | 云→路 | `action`(相位)/`target_stage`/`effective_time`/`reason`/`correlation_id`；平台控制事件，非标准 V2X | 每帧（有信号动作时） |

字段要点：

- `INTENT.intent_origin: Literal["derived", "override"]`，字段级置信度
  `turn_confidence` / `lane_change_confidence` / `arrival_confidence`；
- `SPaT.schedule_status: Literal["committed", "predicted"]`，v1 一律 `predicted`
  （由 phase 时长 + stage_elapsed 推导，无 committed 来源）；
- `RSM` 每条消息承载多个 `RSMObject`（`object_id`/`object_class`/`position`/`speed_mps`/`lane_id`/`confidence`）；
- 前后车距可为 None，`gap_source: Literal["protocol", "derived"] | None`。

## 6. 实体与通信能力

```python
@dataclass(frozen=True, slots=True)
class VehicleCapability:
    vehicle_class: str
    v2x_enabled: bool
    obu_type: str | None = None
```

判定顺序（**显式字段优先，vehicle_class 只是兼容默认**）：

```text
车辆实例 v2x_enabled（框架配置/渗透率分配后写入实例）
    ↓ 若不存在
车辆类型 v2x_enabled（协议 initialize.vehicle_types 若未来提供该字段，前向兼容读取）
    ↓ 若不存在
配置文件 connected_classes / penetration_rate（本轮主要来源）
    ↓ 若不存在
兼容默认值
```

兼容默认值（仅当协议/配置都缺省时使用）：

```python
DEFAULT_V2X_CAPABILITY = {
    "passenger": True,
    "bus": True,
    "truck": False,
    "bicycle": False,
    "pedestrian": False,
}
```

说明：协议 2.0 目前**不带** `v2x_enabled` 字段，且 `simulation/sumo/` 只读不能加，
因此本轮实际主要走"配置文件/渗透率"分支；框架必须支持读取显式字段（前向兼容），
但不得假设协议会提供。

渗透率：`V2XConfig.penetration_rate`（默认 1.0）按 `network_seed` 确定性分配给
`connected_classes` 中的机动车（同 seed 复现）；非机动车永不分配 OBU。

## 7. V2XHub

### 7.1 两阶段 API

```python
@dataclass(frozen=True, slots=True)
class FrameContext:
    episode_id: str
    frame_id: str
    sim_time: float
    input_message_ids: tuple[str, ...]   # 本帧已发布的上行消息

hub = V2XHub(config, sink=None)

frame = hub.ingest_step(payload, intent_overrides=None)   # 阶段一：发 BSM/INTENT/SPaT/RSM
hub.ingest_actions(actions, frame=frame)                   # 阶段二：发 RSI/SignalControlEvent
# 便利封装（内部即上述两阶段）：
hub.ingest_step_with_actions(payload, actions, intent_overrides=None)
```

调用链：

```text
Protocol step payload
        ↓
hub.ingest_step()
        ↓
BSM / INTENT / SPaT / RSM（上行，进延迟队列）
        ↓
coslight 计算 actions（同步，框架不改变决策）
        ↓
hub.ingest_actions()
        ↓
RSI（仅 v2x_enabled 车） / SignalControlEvent（correlation_id = frame_id）
```

### 7.2 通信延迟模型

- 基于 `heapq` 的待投递队列；`publish` 记录 `MESSAGE_SENT`，按链路延迟算
  `deliver_at`，`advance(sim_time)` 投递并记录 `MESSAGE_DELIVERED` / `MESSAGE_DROPPED`；
- 延迟配置：`default_latency_ms=20`、`uplink_latency_ms`/`downlink_latency_ms` 可选覆盖、
  `latency_jitter_ms=0`、`drop_rate=0`、`network_seed=0`；
- v1 默认：固定 20ms + 零抖动 + 零丢包；接口保留抖动/丢包，供后续通信条件消融；
- 所有时间用 `simulation_time`，不用墙钟；
- **coslight 决策保持同步**（拿到 payload 直接算动作）；延迟模型做"投递簿记"——
  记录 sent/delivered、统计延迟、回放展示，不让算法真的等待。

### 7.3 帧级关联

- 同一次 `ingest_step` 产生的上行消息共享 `frame_id`；
- 该帧产生的 RSI / SignalControlEvent：`correlation_id = frame_id`；
- 前端/回放可按 `frame_id` 展示一次完整协同决策链（BSM×N + INTENT×N + SPaT×M + RSM×M → RSI×K + 事件×M）。

### 7.4 sequence_no 与跳号检测

- `sequence_no` 按 `(episode_id, source_id, message_type)` 计数，每局重置；
- 跳号检测针对**已投递记录**（Hub 发送序号天然连续，只有投递端体现丢包）。

### 7.5 周期调度

- 每类消息维护 `next_due_time`；判定用 `sim_time + epsilon >= next_due_time`，**不用浮点取模**；
- `interval_s <= 0` 表示关闭该消息类型；
- 本帧只实际发送一次，不补发多份历史快照；
- `simulation_time` 倒退：v1 直接报错（ValueError），不做静默重置；

### 7.6 override 解析

- `hub.ingest_step(payload, intent_overrides={"vehicle_001": override_intent})`；
- Hub 在发布前完成：先算 derived → 检查同帧 override → override 存在则不发布 derived →
  **每车每帧最终只发布一条 INTENT**；JSONL 只写解析后的有效 INTENT；
- v1 不支持"消息已发布后再替换"；未来需要晚到 override 时引入
  `supersedes_message_id`，保留两条原始记录由回放层解析。

### 7.7 finish_episode

```python
hub.finish_episode(final_sim_time: float, drain_pending: bool = False)
```

- `drain_pending=True`：已到期消息正常投递；
- v1 默认：未到期消息标记 `dropped`，`drop_reason="episode_ended"`，**不得静默丢弃**；
- flush 日志并输出汇总。

## 8. 意图推导（derive.py，纯函数）

| 意图 | 确定性规则 |
|---|---|
| 转向 `turn_intent` | 由 `route_edges` + MAP `connections`（from_lane→to_lane→movement）推出下一路口 through/left/right/uturn；推不出 = `unknown` |
| 换道 `lane_change_intent` | 当前车道不允许意图转向、同 edge 存在允许车道 → 目标车道；否则 None。多候选排序：满足 movement 的车道中选与当前车道横向索引最近者；并列按 lane index/lane_id 固定排序 |
| 到达 `estimated_arrival_s` | 直接用协议 `next_signal.distance_m`（沿路线距离）；`distance_m is None or speed <= 0.1` → None，否则 `distance_m / speed_mps` |
| 路端相位时间表 | `remaining_time_s` / `next_stage` / `next_stage_start_time`，`schedule_status="predicted"`（phase 时长 + stage_elapsed 推导） |

置信度证据表（字段级：`turn_confidence` / `lane_change_confidence` / `arrival_confidence`）：

| 证据 | confidence |
|---|---|
| route + current lane + MAP connection 完整匹配 | 1.0 |
| route 明确，仅匹配到 edge 级 movement | 0.7 |
| 依赖 next_signal fallback | 0.5 |
| 无法确定 | 0.0 |

## 9. RSM 感知覆盖（coverage.py）

```python
def is_in_rsu_coverage(road_user, rsu, detection_radius_m=None) -> bool:
    lane_covered = road_user.lane_id in rsu.covered_lane_ids
    distance_covered = (detection_radius_m is not None
                        and road_user.distance_to(rsu.position) <= detection_radius_m)
    return lane_covered or distance_covered
```

- `covered_lane_ids`：incoming lanes + junction/internal lanes（按 `:路口id` 前缀匹配）+
  可选的近端 outgoing lanes（配置）；
- 半径判定为可选：协议无路口坐标，配置提供 RSU 坐标时才启用；
- `next_signal.intersection_id` 仅作**协议数据缺失时的 fallback**（再无法定位则不生成 RSM）；
- 相邻 RSU 可重复观测同一对象（保留多源观测），云端按 `(object_id, sim_time)` 融合/去重。

## 10. 日志 / 统计 / 回放

JSONL 记录类型（每行一条记录，追加式）：

```jsonc
{"record_type": "episode_start", "run_id": "...", "episode_id": "...",
 "scenario": {...}, "v2x_config": {...}, "network_seed": 0,
 "capability_config": {...}, "map_version": "1"}
{"record_type": "message", "message": {"...": "..."},
 "sent_at": 12.0, "scheduled_delivery_at": 12.02}  // 时间单位：仿真秒
{"record_type": "delivery", "message_id": "...", "status": "delivered",
 "delivered_at": 12.02, "actual_latency_ms": 20.0}
{"record_type": "delivery", "message_id": "...", "status": "dropped",
 "drop_reason": "episode_ended"}
{"record_type": "episode_end", "summary": {...}}
```

统计口径（stats.py）：

- 通信：sent / delivered / dropped / pending、delivery_rate、latency mean/p50/p95/max，
  按消息类型/来源/目的地；sequence gap 数（投递端）；
- 网联渗透率：唯一网联车辆数 / 唯一机动车辆数（**不是 BSM 数量比**）；
- RSM 感知覆盖率：被 RSM 观测到的唯一非网联对象数 ÷ 进入 RSU 感知范围的唯一非网联对象数；
  批量 RSM 统计按 RSMObject 计数；
- RSI 下发漏斗：`actions.vehicles` 请求数 → 目标车辆存在数 → v2x_enabled 目标数 →
  RSI 已发送数 → RSI 已投递数；过滤原因：`vehicle_not_found / not_v2x_enabled /
  invalid_action / message_dropped`；
- 控制事件：SignalControlEvent 只证明"已生成/已下发"，**不声称"已执行"**（执行确认需从下一帧
  实际信号相位验证，本轮不做）。

## 11. coslight 最小接入（不动决策逻辑）

- `algorithms/v2x/adapters/coslight.py`：

  ```python
  class CoslightV2XBridge:
      def __init__(self, log_path, config=V2XConfig()): ...
      def on_initialize(self, payload): hub.ingest_initialize(payload)   # 发 MAP
      def on_step(self, payload, actions):
          frame = hub.ingest_step(payload)
          hub.ingest_actions(actions, frame)
      def on_finish(self, final_sim_time): hub.finish_episode(final_sim_time)
  ```

- 挂载点：`algorithms/coslight/evaluate.py` episode runner，新增 `--v2x-log PATH` 开关；
- 默认关：开关关闭时行为与现在完全一致（V16 golden 不动）；
- 输出：`PATH.jsonl` + `PATH.summary.json` + 控制台回放摘要。

## 12. 测试与验收

### 12.1 单测（algorithms/v2x/tests/，纯 Python）

| 模块 | 覆盖 |
|---|---|
| messages | 公共字段 + 各类型必填字段 |
| entities | 能力判定链（实例→类型→配置→兼容默认）、渗透率种子确定性 |
| hub | 延迟投递（20ms）、pending/丢弃（episode_ended）、sequence 按 source+type、投递端跳号检测 |
| frame | 两阶段 API、correlation_id=frame_id、override 同帧唯一 INTENT |
| derive | 转向/换道/ETA/置信度证据表/多候选车道排序（确定性断言） |
| coverage | 车道覆盖 + next_signal fallback + 多 RSU 重复观测 + 批量对象 |
| intervals | next_due_time、interval≤0 关闭、时间倒退报错 |
| logger/replay | 四类记录、summary/print |
| stats | 渗透率、RSM 覆盖率、RSI 漏斗（过滤原因）、控制事件计数 |

### 12.2 端到端验收

1. `python -m algorithms.coslight.evaluate --v2x-log <path>`（off_peak，20 路口或含非机动车子集）；
2. 日志断言：1 条 MAP；每间隔 BSM/INTENT/SPaT；含非机动车时有 RSM（无则记录 0/0 并说明）；
   RSI 只发 `v2x_enabled=True`；信号变化时有 SignalControlEvent；
3. summary：delivery_rate=1.0、latency=20ms、渗透率、RSI 漏斗、控制事件"已下发"；
4. 回归：现有 98 个 coslight pytest 全绿；`simulation/sumo/` 零改动；V16 部署默认不动。

## 13. 未来扩展（非本轮）

- 网联渗透率消融（20%/50%/80%）与通信条件消融（抖动/丢包）；
- sink 接 backend/frontend，前端按 frame_id 展示协同决策链；
- 异步"云端等投递再决策"模式；
- 晚到 override（`supersedes_message_id`）；
- 施工遮挡、感知半径实验；公交优先、货车编队等扩展消息。
