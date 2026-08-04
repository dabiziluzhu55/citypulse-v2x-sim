# V2X 车路云协同消息框架设计（algorithms/v2x）

- 日期：2026-08-04（v2：按 review 修正 P0-1..7 与 P1-1..10）
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
- 不做"云端等待消息投递后再决策"的异步模式；
- 不做 RSM 多源融合（保留原始重复观测，融合留待未来）。

## 2. 约束与边界

- `simulation/sumo/` 只读：所有车/路/云语义都在算法层实现；SUMO 只当"车端传感器 + 执行器"。
- 协议 2.0 是唯一数据源：`initialize`（静态拓扑/车型）、`step`（路口/车道/车辆状态）、
  `actions.signals` / `actions.vehicles`（控制输出）。
- 框架**纯 Python**，不依赖 torch、不导入 SUMO/TraCI，可独立单测。
- 通信时间一律使用协议 `simulation_time`，**不使用真实墙钟**。
- V16 部署默认（`stage1_local_v16_lr1_8ep`）保持不动；coslight 默认行为不变（接入开关默认关）。

### 2.1 Shadow-mode 声明（重要）

v1 是 **shadow-mode V2X observability integration**：

> V2X 消息是 Protocol 状态与 coslight 动作的**类型化影子映射**，用于通信模拟、日志、统计和
> 回放；消息延迟、丢包和通信能力**不会改变原始动作的产生与执行**。

具体含义：

- `actions.vehicles` 即使目标车不是网联车，**原 actions 照常传给 Protocol 2.0**；只是不生成 RSI；
- `SignalControlEvent` 只是 `actions.signals` 的**影子记录**，不能控制、拦截或延迟原信号动作；
- 帧级关联展示的是"**完整同帧消息关联链**"（不是"消息驱动决策的因果链"）；
- 真正由投递消息驱动决策的异步模式属于未来扩展。

## 3. 架构总览

```text
Protocol 2.0 (SUMO Worker 侧只读接口)
        │  initialize / step / actions
        ▼
algorithms/v2x/  V2XHub（进程内消息总线 + 通信模拟）
  ├─ 车端 OBU   ── BSM / INTENT ──> 云 Cloud
  ├─ 路端 RSU   ── SPaT / MAP / RSM ──> 云 Cloud
  ├─ 云 Cloud   ── RSI ──> 网联车（影子，不改原 actions）
  └─ 云 Cloud   ── SignalControlEvent ──> RSU（影子，不改原 actions）
        │
        ▼
  JSONL 日志（发送/投递记录）→ 统计 → 回放 CLI
```

## 4. 包结构

```text
algorithms/v2x/
  __init__.py     # 公共 API：V2XHub、V2XConfig、消息类型、create_hub()
  messages.py     # MessageDraft / V2XMessage dataclass + 枚举 + uuid5 命名空间
  entities.py     # Vehicle/OBU、Intersection/RSU、Cloud 薄实体 + 通信能力判定
  hub.py          # V2XHub：publish/subscribe、延迟队列、生命周期、两阶段 API、统计
  derive.py       # 意图推导纯函数（转向/换道/到达/相位时间表）
  coverage.py     # RSU 感知覆盖判定（车道 + 可选半径 + fallback，空值保护）
  protocol.py     # 协议 2.0 适配器：initialize/step 载荷 → MessageDraft；actions → RSI/事件
  logger.py       # JSONL sink（LogRecord：episode_start/message/delivery/episode_end）
  stats.py        # 通信统计、渗透率、RSM 覆盖率、RSI 漏斗、控制事件（结构化零分母）
  replay.py       # 回放/摘要 CLI：python -m algorithms.v2x.replay <log> [--summary|--print]
  adapters/
    coslight.py   # CoslightV2XBridge：evaluate runner 接入
  tests/          # pytest（纯单测，不碰 SUMO/torch）
```

## 5. 消息模型

### 5.1 公共基类与 ID 契约

```python
@dataclass(frozen=True, slots=True)
class V2XMessage:
    message_type: str            # 显式字符串，如 "BSM"（JSON 序列化必须包含，不依赖类名）
    message_id: str              # uuid5(MESSAGE_NAMESPACE, f"{episode_id}|{source_id}|{message_type}|{sequence_no}")
    schema_version: str          # 默认 "1.0"
    episode_id: str              # 调用方提供，要求单次运行内全局唯一（或 message_id 再加 run_id）
    frame_id: str                # "{episode_id}:init" 或 "{episode_id}:step:{frame_index:06d}"
    sequence_no: int             # 按 (episode_id, source_id, message_type) 单调递增，每局重置
    sim_time: float              # Protocol 2.0 simulation_time
    source_id: str
    destination: str             # 具体实体 id，或 "*"（广播）
    correlation_id: str | None = None  # 串起"上报帧 → 决策 → 下发"；RSI/事件 = frame_id
```

- `message_type` 是消息自身字段（不是 Python 类名推导）；
- `message_id` 由 Hub 在 publish 时生成（确定性 uuid5），调用方不构造；
- `episode_id` 全局唯一性由调用方保证；需要跨运行唯一时再加 `run_id`。

### 5.2 六类领域消息 + 平台控制事件

| 消息 | 方向 | 内容 | 频率 |
|---|---|---|---|
| BSM | 网联车→云 | 位置/速度/加速度/车道/路线/next_signal；`front_gap_m`/`rear_gap_m` 可选 + `gap_source` | 每 `bsm_interval_s` |
| INTENT | 网联车→云 | 转向/换道/到达意图 + 字段级置信度 + `intent_origin` | 每 `intent_interval_s` |
| SPaT | 路端→云 | current_phase/stage/灯色 + 相位时间表（`remaining_time_s`/`next_stage`/`next_stage_start_time`/`schedule_status`） | 每 `spat_interval_s` |
| MAP | 路端→云 | 路口拓扑/相位/车道/连接/邻居（静态）；**每个注册 RSU 每局 1 条** | 初始化时 |
| RSM | 路端→云 | 批量 `RSMObject`：非网联使用者观测（位置/速度/车道/类别/置信度=1.0，无意图） | 每 `rsm_interval_s` |
| RSI | 云→网联车 | `target_speed_mps`/`target_lane_index`/引导类型；只发 `v2x_enabled=True`；**影子记录，不过滤原 actions** | 每帧（有车辆动作时） |
| SignalControlEvent | 云→路 | `action`(相位)/`requested_effective_time`/`changed`/`previous_action`/`reason`/`correlation_id`；**影子记录** | 每个有效 `actions.signals` 条目 1 条 |

字段要点：

- `INTENT.intent_origin: Literal["derived", "override"]`，字段级置信度
  `turn_confidence` / `lane_change_confidence` / `arrival_confidence`；
- `SPaT.schedule_status: Literal["committed", "predicted"]`，v1 一律 `predicted`
  （由 phase 时长 + stage_elapsed 推导，无 committed 来源）；
- `RSM` 每条消息承载多个 `RSMObject`（`object_id`/`object_class`/`position`/`speed_mps`/`lane_id`/`confidence`）；
  v1 使用 SUMO 真值，`confidence` 固定 `1.0`（不实现感知误差模型）；
- `SignalControlEvent.changed: bool` 表示相对上一帧是否相位变化，`previous_action: int | None`；
  `requested_effective_time` 是**请求生效时间**，不表示已验证执行；
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

渗透率分配（**稳定哈希，不依赖遍历顺序**）：

```python
score = stable_hash(f"{capability_seed}|{vehicle_id}") / HASH_MAX
v2x_enabled = score < penetration_rate
```

- `capability_seed` 与 `network_seed` 分开（或独立命名空间），避免"加一辆车"改变通信随机序列；
- 只对 `connected_classes`（默认 `{"passenger", "bus"}`）中的机动车分配；非机动车永不分配 OBU；
- 同 `capability_seed` 得到完全相同的网联车辆集合。

## 7. V2XHub

### 7.1 生命周期与两阶段 API

```python
# 生命周期（run_id/episode_id 由调用方提供）
frame = hub.ingest_initialize(
    payload,
    *,
    run_id: str,
    episode_id: str,
    scenario: dict | None = None,
)          # 建实体、能力判定、发 MAP（每 RSU 1 条），frame_id = "{episode_id}:init"

frame = hub.ingest_step(payload, intent_overrides=None)
#   → FrameContext(episode_id, frame_id="{episode_id}:step:{index:06d}", sim_time, input_message_ids)
hub.ingest_actions(actions, frame=frame)          # 发 RSI / SignalControlEvent
hub.finish_episode(final_sim_time, drain_pending=False)

# 便利封装（内部即上述两阶段）：
hub.ingest_step_with_actions(payload, actions, intent_overrides=None)
```

生命周期规则：

- 未开始 episode 调用 `ingest_step`：报错（ValueError）；
- 同一 episode 重复 `ingest_initialize`：报错；
- `finish_episode` 后继续 ingest：报错；
- 新 episode 开始时：sequence、调度器、实体、统计状态**全部重置**。

帧 ID 规则：`{episode_id}:init`；step 帧 `{episode_id}:step:{frame_index:06d}`（1 起）。

调用链：

```text
Protocol step payload
        ↓
hub.ingest_step()
        1. 校验 simulation_time 单调性
        2. advance(sim_time)：处理此前所有到期事件
        3. 创建当前 FrameContext
        4. 发布当前帧上行消息（BSM/INTENT/SPaT/RSM，进延迟队列）
        ↓
coslight 计算 actions（同步，框架不改变决策）
        ↓
hub.ingest_actions()
        ↓
RSI（仅 v2x_enabled 车，影子） / SignalControlEvent（影子，correlation_id = frame_id）
```

### 7.2 通信延迟模型（离散事件时间）

- 基于 `heapq` 的待投递队列；`publish` 记录 `MESSAGE_SENT`，按链路延迟算
  `scheduled_delivery_at`，`advance(sim_time)` 投递并记录 `MESSAGE_DELIVERED` / `MESSAGE_DROPPED`；
- **两种时间必须区分**：
  - `scheduled_delivery_at`：网络模型计算的**逻辑投递时间**；
  - `processed_at`：Hub 实际处理该事件时的当前仿真时间；
  - 统计延迟：`actual_latency_ms = (scheduled_delivery_at - sent_at) * 1000`
    （**不是** `processed_at - sent_at`，否则 20ms 配置会被统计成 5000ms）；
- 到期事件按 `(scheduled_delivery_at, insertion_order)` 稳定排序，保证同时间回调顺序确定；
- 延迟配置：`default_latency_ms=20`、`uplink_latency_ms`/`downlink_latency_ms` 可选覆盖、
  `latency_jitter_ms=0`、`drop_rate=0`、`network_seed=0`；
- v1 默认：固定 20ms + 零抖动 + 零丢包；接口保留抖动/丢包，供后续通信条件消融；
- 所有时间用 `simulation_time`，不用墙钟；
- **coslight 决策保持同步**（拿到 payload 直接算动作）；延迟模型做"投递簿记"——
  记录 sent/delivered、统计延迟、回放展示，不让算法真的等待；
- 说明：v1 的投递时间是离散事件模型中的逻辑时间；subscriber 回调可能在下一个
  Protocol step 才被处理，但日志和延迟统计采用 `scheduled_delivery_at`。

### 7.3 帧级关联

- 同一次 `ingest_step` 产生的上行消息共享 `frame_id`；
- 该帧产生的 RSI / SignalControlEvent：`correlation_id = frame_id`；
- 展示口径：**完整同帧消息关联链**（BSM×N + INTENT×N + SPaT×M + RSM×M → RSI×K + 事件×M），
  按 `frame_id` 聚合；不声称"消息驱动决策"（见 §2.1 shadow-mode）。

### 7.4 sequence_no 与投递质量检测

- `sequence_no` 按 `(episode_id, source_id, message_type)` 计数，每局重置；
- 跳号检测在 episode 结束时按 stream 计算：
  - `missing_sequence_count = |sent_set - delivered_set|`；
  - `out_of_order_count`：投递序与发送序不一致的次数（抖动可导致乱序，**乱序≠跳号**）；
  - `duplicate_delivery_count`；
- 不用"相邻投递序号差 != 1"作为唯一判据。

### 7.5 周期调度（按来源实体 + 消息类型）

```python
schedule_key = (episode_id, source_id, message_type)
# 每 key 维护 next_due_time
```

发送规则：

1. 实体**首次被观察到时立即发送**（不等全局周期）；
2. 此后按 `interval_s` 发送：`sim_time + epsilon >= next_due_time` 时发送（**不用浮点取模**）；
3. 跨过多个周期时**只发送当前最新快照一次**，不补发历史快照；
4. 发送后 `next_due_time` 推进到**严格大于当前 sim_time**；
5. `interval_s <= 0` 表示关闭该消息类型；
6. 实体消失后不再发送，但本局历史 sequence **不复用**；
7. `simulation_time` 倒退：v1 直接报错（ValueError），不做静默重置。

### 7.6 override 解析

- `hub.ingest_step(payload, intent_overrides={"vehicle_001": override_intent})`；
- Hub 在发布前完成：先算 derived → 检查同帧 override → override 存在则不发布 derived →
  **每车每帧最终只发布一条 INTENT**；JSONL 只写解析后的有效 INTENT；
- v1 不支持"消息已发布后再替换"；未来需要晚到 override 时引入
  `supersedes_message_id`，保留两条原始记录由回放层解析。

### 7.7 finish_episode 双语义

```python
hub.finish_episode(final_sim_time: float, drain_pending: bool = False)
```

- `drain_pending=False`（默认）：先处理 `scheduled_delivery_at <= final_sim_time` 的消息；
  其余 pending 标记 `dropped / drop_reason="episode_ended"`；**不得静默丢弃**；
- `drain_pending=True`：将逻辑时间推进到最后一条 pending 的 `scheduled_delivery_at`，
  处理全部**非网络随机丢包**消息；不产生 `episode_ended` 丢包；
- coslight bridge 使用 `drain_pending=True`（零丢包配置下 `delivery_rate=1.0` 才成立）；
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
    distance_covered = (
        detection_radius_m is not None
        and rsu.position is not None
        and road_user.position is not None
        and road_user.distance_to(rsu.position) <= detection_radius_m
    )
    return lane_covered or distance_covered
```

- `covered_lane_ids`：incoming lanes + junction/internal lanes（按 `:路口id` 前缀匹配）+
  可选的近端 outgoing lanes（配置）；
- 半径判定为可选：协议无路口坐标，配置提供 RSU 坐标时才启用；**完整空值保护**；
- `next_signal.intersection_id` 仅作**协议数据缺失时的 fallback**（再无法定位则不生成 RSM）；
- 相邻 RSU 可重复观测同一对象（**保留多源原始观测**）；
- **本轮不实现云端融合/去重**：消费方未来可按 `(object_id, sim_time)` 融合；
  本轮日志和统计不删除重复原始观测。

## 10. 日志 / 统计 / 回放

### 10.1 接口契约

```python
class MessageSink(Protocol):
    def write(self, record: LogRecord) -> None: ...   # LogRecord，不是裸消息
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

Hub 侧：

```python
hub.subscribe(message_type: type[V2XMessage], handler: Callable[[V2XMessage], None]) -> Subscription
```

- subscriber 收到的是**投递后**的消息（经延迟队列）；
- 所有消息只能通过 Hub factory / `publish(draft)` 生成正式 `V2XMessage`；
  调用方构造 `MessageDraft`（只有业务 payload），Hub 负责 stamp
  `message_id/sequence_no/episode_id/frame_id/sim_time`；禁止外部伪造缺 ID 的 `V2XMessage`。

### 10.2 JSONL 记录类型（每行一条记录，追加式）

```jsonc
{"record_type": "episode_start", "run_id": "...", "episode_id": "...",
 "scenario": {...}, "v2x_config": {...}, "capability_seed": 0,
 "capability_config": {...}, "map_versions": {...}}
{"record_type": "message", "message": {"message_type": "BSM", "...": "..."},
 "sent_at": 12.0, "scheduled_delivery_at": 12.02}   // 时间单位：仿真秒
{"record_type": "delivery", "message_id": "...", "status": "delivered",
 "delivered_at": 12.02, "actual_latency_ms": 20.0}
{"record_type": "delivery", "message_id": "...", "status": "dropped",
 "drop_reason": "episode_ended"}
{"record_type": "episode_end", "summary": {...}}
```

### 10.3 统计口径（stats.py，结构化零分母）

- 通信：sent / delivered / dropped / pending、`delivery_rate`（sent=0 时 `null`）、
  latency mean/p50/p95/max（无已投递消息时 `null`），按消息类型/来源/目的地；
  `missing_sequence_count` / `out_of_order_count` / `duplicate_delivery_count`；
- 网联渗透率：唯一网联车辆数 / 唯一机动车辆数（**不是 BSM 数量比**）；
- RSM 感知覆盖率（结构化，无样本不写 "0/0"）：

  ```json
  {"rsm_coverage": {"observed_unique_objects": 0, "eligible_unique_objects": 0,
                    "rate": null, "defined": false}}
  ```

  批量 RSM 统计按 RSMObject 计数；
- RSI 下发漏斗：`actions.vehicles` 请求数 → 目标车辆存在数 → v2x_enabled 目标数 →
  RSI 已发送数 → RSI 已投递数；过滤原因：`vehicle_not_found / not_v2x_enabled /
  invalid_action / message_dropped`；
- 控制事件：SignalControlEvent 只证明"已生成/已下发"，**不声称"已执行"**
  （执行确认需从下一帧实际信号相位验证，本轮不做）。

## 11. coslight 最小接入（shadow mode，不动决策逻辑）

- `algorithms/v2x/adapters/coslight.py`：

  ```python
  class CoslightV2XBridge:
      def __init__(
          self,
          log_path: str,
          config: V2XConfig | None = None,
      ):
          self.config = config or V2XConfig()      # 避免可变默认参数

      def on_initialize(self, payload, *, run_id, episode_id, scenario=None):
          hub.ingest_initialize(payload, run_id=run_id, episode_id=episode_id, scenario=scenario)

      def on_step(self, payload, actions):
          frame = hub.ingest_step(payload)
          hub.ingest_actions(actions, frame)

      def on_finish(self, final_sim_time):
          hub.finish_episode(final_sim_time, drain_pending=True)
  ```

- 挂载点：`algorithms/coslight/evaluate.py` episode runner，新增 `--v2x-log PATH` 开关；
- 默认关：开关关闭时行为与现在完全一致（V16 golden 不动）；
- **shadow mode**：V2X 层不过滤、不延迟、不修改任何原始 actions（见 §2.1）；
- 输出：`PATH.jsonl` + `PATH.summary.json` + 控制台回放摘要。

## 12. 测试与验收

### 12.1 单测（algorithms/v2x/tests/，纯 Python）

| 模块 | 覆盖 |
|---|---|
| messages | 公共字段、message_type 显式、uuid5 message_id 唯一性（同源不同型不同号不冲突） |
| lifecycle | 未 start 报错、重复 initialize 报错、finish 后 ingest 报错、新 episode 重置 |
| entities | 能力判定链（实例→类型→配置→兼容默认）、稳定哈希渗透率（同 seed 完全一致） |
| hub | 延迟投递（20ms）、pending/丢弃（episode_ended）、drain_pending 双语义、sequence 按 source+type、missing/out_of_order/duplicate 检测 |
| frame | 两阶段 API、correlation_id=frame_id、override 同帧唯一 INTENT、帧 ID 规则 |
| derive | 转向/换道/ETA/置信度证据表/多候选车道排序（确定性断言） |
| coverage | 车道覆盖 + next_signal fallback + 多 RSU 重复观测 + 批量对象 + 空值保护 |
| intervals | next_due_time 按 (source,type)、首帧立即发送、interval≤0 关闭、时间倒退报错 |
| logger/replay | 四类记录、summary/print |
| stats | 渗透率、RSM 覆盖率（结构化 null）、RSI 漏斗（过滤原因）、控制事件计数、延迟按 scheduled 时间 |

### 12.2 端到端验收

**A. 确定性最小闭环 fixture**（必须包含：≥2 个 RSU、1 辆网联机动车、1 辆非网联机动车、
1 个非机动车、signal action、vehicle action）：

1. 七类消息/事件（BSM/INTENT/SPaT/MAP/RSM/RSI/SignalControlEvent）全部生成；
2. MAP 数 == 注册 RSU 数，`set(map.source_id) == expected_rsu_ids`；
3. 每个 `(source, message_type)` 的 sequence 从 1 连续递增；
4. 固定 20ms、零丢包、`drain_pending=True`：`delivery_rate=1.0`，所有 `actual_latency_ms=20`；
5. `drain_pending=False` 时，仅 episode 结束边界消息允许 `drop_reason="episode_ended"`；
6. RSI 只为 `v2x_enabled` 目标生成，但**原 `actions.vehicles` 不被过滤**；
7. SignalControlEvent 数 == 有效 `actions.signals` 条目数；
8. 相同 `capability_seed` 得到完全相同的网联车辆集合；
9. V2X 开关关闭和开启时，传给 Protocol 2.0 的 actions 逐帧完全一致；
10. `simulation/sumo/` 无修改。

**B. 20 路口实际场景 smoke**（`python -m algorithms.coslight.evaluate --v2x-log ...`）：

- 不崩溃；日志可回放；
- MAP 数 == RSU 数；
- 默认开关关闭时结果不变；V2X 开启后原始 actions 与关闭时完全一致。

### 12.3 回归契约

- 现有 `algorithms/coslight` 测试集**零失败**（当前基线 98 个，仅作说明不作固定断言）；
- 新增 `algorithms/v2x` 测试集**零失败**；
- 完整 pytest 命令退出码为 0。

## 13. 未来扩展（非本轮）

- 网联渗透率消融（20%/50%/80%）与通信条件消融（抖动/丢包）；
- sink 接 backend/frontend，前端按 frame_id 展示同帧消息关联链；
- 异步"云端等投递再决策"模式（真正由消息驱动决策）；
- 晚到 override（`supersedes_message_id`）；
- RSM 多源融合（`fusion.py`：冲突位置/速度/confidence 融合策略）；
- 施工遮挡、感知半径实验；公交优先、货车编队等扩展消息。
