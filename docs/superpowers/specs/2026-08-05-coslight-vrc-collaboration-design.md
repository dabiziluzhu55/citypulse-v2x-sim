# CoSLight 车路云协同决策层（VRC Collaboration）设计

- 日期：2026-08-05
- 状态：设计冻结，待实现
- 前置：`docs/superpowers/specs/2026-08-04-v2x-framework-design.md`（V2X 消息框架，已实现）
- 分支：`feature/rl`

## 0. 目标与硬约束

在已冻结并实现的 V2X 消息框架之上，新增**车路云协同决策层**：云端只消费 V2XHub 已投递消息，稳定构造路口/车辆状态，生成**合法、确定、可解释、可回放**的信号与车辆引导建议；第一版以 shadow 模式闭环，不接管信号灯，也不改变 CoSLight 原始决策。

硬约束：

- **不改 `simulation/sumo/` 代码**（只读）；
- **非机动车无法通信**：永不装 OBU，仅由 RSU 的 RSM 感知上报；
- CoSLight 原决策逻辑保持不动，shadow 下 `applied_actions == baseline_actions`；
- 云端只消费**已投递**消息（延迟/丢包/发送周期/渗透率/过期全部生效），不得回读原始 Protocol payload。

两阶段：① V2X 消息框架（已完成）；② 本设计（协同决策层，未实现）。

---

## 1. 总体架构

### 1.1 模块结构

```
algorithms/v2x/
├── hub.py / messages.py / protocol.py / ...   # 冻结协议，不改
└── collab/
    ├── __init__.py
    ├── snapshot.py    # 纯不可变模型：EdgeSnapshot / ApproachState / LaneState / ConnectedVehicleState
    ├── aggregator.py  # EdgeAggregator：订阅、缓存、聚合（有状态）
    ├── state.py       # CloudStateStore / CloudIntersectionView / IntersectionStaticContext
    ├── policy.py      # CloudRulePolicy：propose_signal / propose_guidance
    ├── proposals.py   # SignalProposal / VehicleGuidanceProposal / 状态枚举
    ├── arbiter.py     # ActionArbiter + validate_signal_proposal
    ├── engine.py      # CollabDecisionEngine：编排一次决策帧
    ├── records.py     # edge_snapshot / cloud_proposal / arbitration / collab_tick_stats / collab_episode_end
    ├── stats.py       # collab 汇总：pooled 聚合、完整性审计
    └── tests/
```

`CoslightV2XBridge` 保持薄适配器：只负责 env/CLI 开关、生命周期调用、把 `CollabTickResult.protocol_actions` 交给 `hub.ingest_actions`。所有协同编排在 `CollabDecisionEngine`。

### 1.2 数据流

```
车辆/路口 ──BSM/INTENT/SPaT/MAP/RSM──> V2XHub（延迟/丢包）
                                          │ 仅已投递（subscribe 同步回调）
                                          ▼
                              EdgeAggregator ──进程内不可变 EdgeSnapshot──> CloudStateStore
                                                                                │
                                                                                ▼
                                                                        CloudRulePolicy
                                                                          │        │
                                                          SignalProposal │        │ VehicleGuidanceProposal
                                                                          ▼        ▼
                                                                   ActionArbiter   ProposalEmitter（仅 PROPOSED → RSI → V2XHub）
                                                                          │
                                                                          ▼
                                                        CollabTickResult.protocol_actions
                                                                          │
                                                                          ▼
                                                          hub.ingest_actions(...) → SIGNAL_CONTROL
```

- 边→云在 v1 中**不作为独立通信链路建模**（不新增消息类型、不动冻结协议），可观测性靠 `edge_snapshot / cloud_proposal / arbitration` 三类内部记录；
- 订阅回调是 hub 投递事件处理时**同步调用**，不是后台线程。

### 1.3 生命周期时序（冻结）

`on_initialize`（顺序固定）：

```text
1. 创建 Hub + 记录器（必备 InMemoryRecordCollector，可选 JsonlSink）
2. 创建 CollabDecisionEngine（注册一次 subscriptions：EdgeAggregator 订阅 BSM/INTENT/SPaT/MAP/RSM）
3. hub.ingest_initialize(...)          # MAP 进入待投递队列
4. 首个决策帧可能尚未收到 MAP → 正常 MISSING_INPUT；单测不得绕过消息投递直接读 initialize payload
```

`on_step`（顺序固定）：

```python
frame = hub.ingest_step(payload)
# ingest_step 内部：1) advance(t) 投递到期消息（同步回调 EdgeAggregator）2) 建 frame 3) 发布 t 时刻新上行消息（调度到 t+latency）
result = engine.tick(frame=frame, baseline_actions=baseline_actions)
hub.ingest_actions(result.protocol_actions, frame=frame)
return result.protocol_actions
```

- 决策时刻 t 只能看到**此前已投递**的消息；t 时刻新发布的消息不可见；
- 不再单独调用 advance；`collab_tick_stats` 由 `engine.tick` 成功完成本帧决策后**原子写入**（不依赖 bridge 再 flush）；`stats_delta` 仍放进 `CollabTickResult` 供测试/控制台。

`on_finish`（顺序固定）：

```python
network_summary = hub.finish_episode(final_sim_time, drain_pending=True)  # 只 drain 网络，不触发新决策
collab_summary = engine.finalize_episode(records=record_collector.episode_records)
# 写 collab_episode_end 记录
return merge(network_summary, {"collab": collab_summary})
```

- `finalize_episode()` 不计算策略、不发布新 RSI、不产生新的 arbitration；
- 先 drain 网络再审计完整性，保证 RSI 终态 delivery 已写入。

`on_close`（顺序固定）：

```text
1. 当前 episode 必须已 finish
2. engine.close()：取消 subscriptions、flush 内部记录器；不得抢先关闭共享 sink
3. hub.close()：关闭共享 sink
```

多 episode：

- subscriptions **引擎创建时注册一次**；每个新 episode 调 `reset_episode()`：清空 aggregator 动态缓存、CloudStateStore 动态/静态上下文、LastEmittedGuidanceState、arbiter episode 状态、episode 统计；保留 subscription 对象；
- 多 episode 复用同一 sink；run 级汇总用 pooled 聚合（见 §6）。

### 1.4 数据结构（不可变模型）

```python
@dataclass(frozen=True, slots=True)
class LaneState:
    lane_id: str
    connected_count: int
    observed_count: int
    stopped_count: int
    queue_estimate: float
    arrivals_since_last_snapshot: int

@dataclass(frozen=True, slots=True)
class ApproachState:
    approach_id: str
    incoming_lane_ids: tuple[str, ...]
    lane_states: Mapping[str, LaneState]
    downstream_vehicle_count: int | None
    downstream_queue_estimate: float | None
    turn_intent_counts: Mapping[str, int]
    arrival_etas_s: tuple[float, ...]

@dataclass(frozen=True, slots=True)
class ConnectedVehicleState:
    vehicle_id: str
    lane_id: str | None
    approach_id: str | None
    speed_mps: float
    acceleration_mps2: float | None
    next_signal_intersection_id: str | None   # 车辆下一路口（算法管理路口 ID，场景范围门禁 §7）
    distance_to_signal_m: float | None
    turn_intent: str | None
    turn_confidence: float
    lane_change_intent: int | None
    estimated_arrival_s: float | None
    bsm_delivered_at: float
    intent_delivered_at: float | None
    source_message_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class EdgeSnapshot:
    intersection_id: str
    sim_time: float                 # 快照生成时刻
    phase: int | None               # SPaT current_phase
    stage: str | None
    stage_elapsed_s: float | None   # 直接取自 SPaT（冻结字段）；缺失时策略保守抑制
    remaining_time_s: float | None
    approaches: Mapping[str, ApproachState]
    connected_vehicles: Mapping[str, ConnectedVehicleState]   # 只含 v2x_enabled=True 的网联车
    last_delivery_at: Mapping[str, float | None]   # 各消息源最近投递 sim_time（纯事实）
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]
```

聚合器语义（EdgeAggregator）：
- 每次收到新 BSM 按 `next_signal` 更新车辆所属路口；车辆 `next_signal` 变化时从旧路口缓存移除并迁移到新路口；
- `connected_vehicles` 只含 `v2x_enabled=True` 的网联车（非机动车/无 OBU 车辆永不进入，仅由 RSM 聚合为 lane/approach 观测）；
- 每路口快照的 `connected_vehicles` 为该路口聚合结果，供 CloudStateStore 与 CloudRulePolicy 消费。

静态上下文（MAP 投递后建立，保存在 CloudStateStore，不随帧复制）：

```python
@dataclass(frozen=True, slots=True)
class IntersectionStaticContext:
    intersection_id: str
    phase_order: tuple[int, ...]
    lane_to_approach: Mapping[str, str]
    lane_to_edge: Mapping[str, str]
    lane_to_index: Mapping[str, int]
    lane_to_movements: Mapping[str, tuple[str, ...]]
    lane_speed_limit_mps: Mapping[str, float]
    valid_actions: tuple[int, ...]
    phase_to_action: Mapping[int, int | None]
    action_to_movements: Mapping[int, tuple[str, ...]]
    movement_to_lanes: Mapping[str, tuple[str, ...]]
    transition_phases: frozenset[int]
    map_source_message_id: str
```

> 注：`next_stage` 只是阶段名（GREEN/YELLOW/CLEARANCE），不含下一相位 id；CoSLight 切相是动态决策。v1 **不做下一服务相位的预测**（见 §3.2 第 4 步），因此静态上下文不提供 `stage_to_action` 单值映射。

### 1.5 新鲜度视图（CloudStateStore）

```python
@dataclass(frozen=True, slots=True)
class CloudIntersectionView:
    snapshot: EdgeSnapshot
    age_s: Mapping[str, float | None]
    missing: frozenset[str]     # 从未收到
    stale: frozenset[str]       # 收到过但超过阈值
```

- 新鲜度阈值统一由 `FreshnessConfig`（§1.6）提供，默认 `bsm_s = intent_s = spat_s = rsm_s = 10.0`；
- MAP 是静态拓扑，**不按该阈值过期**；
- BSM/INTENT 按车辆判断，SPaT/RSM 按路口判断。

### 1.6 CollabConfig

```python
@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    bsm_s: float = 10.0
    intent_s: float = 10.0
    spat_s: float = 10.0
    rsm_s: float = 10.0

@dataclass(frozen=True, slots=True)
class CollabConfig:
    decision_mode: DecisionMode = DecisionMode.SHADOW
    guidance_mode: GuidanceEmissionMode = GuidanceEmissionMode.THRESHOLD
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    log_edge_snapshot: bool = True
    log_arbitration_mode: Literal["all", "differences"] = "all"
    signal_policy: SignalPolicyConfig = field(default_factory=SignalPolicyConfig)
    guidance_policy: GuidancePolicyConfig = field(default_factory=GuidancePolicyConfig)
```

- 无 `enabled` 字段：**是否创建引擎 = 是否启用**；`DecisionMode.OFF` 仅显式消融（引擎存在但 tick 短路）；
- `signal_policy` / `guidance_policy` 类型化配置见 §2.5 / §3.7。

### 1.7 日志记录类型与量控制

| 记录 | 频率 | 可否关闭 |
|---|---|---|
| `collab_tick_stats` | 每决策帧 1 条 | **不可关闭**（可重放汇总的基础） |
| `edge_snapshot` | 每路口每帧 1 条 | 可关（`log_edge_snapshot=False`，默认开） |
| `cloud_proposal` | 信号：路口×帧；引导：车×帧（非 NO_ACTION 或 FULL 全量） | 属解释性记录 |
| `arbitration` | 路口×帧（`log_arbitration_mode="all"` 默认；`"differences"` 只减详细记录） | `"differences"` 可减 |
| `collab_episode_end` | 每 episode 1 条 | 不可关闭 |

---

## 2. 信号规则策略（CloudRulePolicy.propose_signal）

### 2.1 规则族（已选 C）

**排队/到达基线 + INTENT 前视修正**。按 lane → movement → action 聚合：

```text
queued_demand(action)           = Σ lane.queue_estimate           # action 服务 movement 覆盖的 lane
recent_arrival_demand(action)   = Σ lane.arrivals_since_last_snapshot
forward_demand(action)          = Σ vehicle.turn_confidence * exp(−eta / forward_decay_s)
                                  # fresh INTENT、eta ∈ [0, forward_horizon_s]、movement 被 action 服务

score(action) = queue_weight * queued
              + arrival_weight * recent_arrival
              + forward_weight * forward
```

- 不重复计数：`queue_estimate`（BSM 停车 + RSM 低速观测）与 `recent_arrival`（上一快照周期新到达）是两个独立需求项，**不叠加 connected/observed 总数**；
- forward 语义 = **近期预计到达需求**，不是"绿窗命中"（v1 无候选绿窗模型）。

### 2.2 决策流程（冻结顺序）

```text
1. 门禁（顺序固定；以下均 proposed_action=None）：
   缺 MAP / 缺 SPaT                        → MISSING_INPUT
   SPaT 过期                               → STALE_INPUT
   phase ∈ transition_phases               → SUPPRESSED_TRANSITION（candidate=None，不产切换建议）
   phase_to_action 映射失败                 → INVALID_PROPOSAL（防御性）
2. 对每个 valid_action 计算 score（§2.1）
3. max_score ≤ demand_epsilon              → NO_DEMAND（proposed=current，confidence=0.0）
4. argmax；平分优先 current，否则按 action ID 升序（确定性）
5. best == current                         → KEEP_CURRENT
6. stage_elapsed_s is None                 → SUPPRESSED_MIN_GREEN，reason="stage_elapsed_unknown"
   stage_elapsed_s < min_green_s           → SUPPRESSED_MIN_GREEN（proposed=current）
7. best 相对 current 的 margin < switch_score_margin → SUPPRESSED_SWITCH_MARGIN（proposed=current）
8. 其余                                     → PROPOSED（candidate → proposed_action，needs_transition=True）
```

`INVALID_PROPOSAL` **仅防御性触发**：`valid_actions` 为空、movement/action 映射不完整、分数 NaN/Infinity、输出验证器拒绝。

### 2.3 SignalProposal 与状态语义

```python
@dataclass(frozen=True, slots=True)
class SignalProposal:
    intersection_id: str
    status: SignalDecisionStatus
    candidate_action: int | None        # 策略原始选择（argmax；transition 门禁在算分前 → None）
    proposed_action: int | None         # 约束后最终建议
    current_action: int | None
    action_scores: Mapping[int, float]
    reason: str
    confidence: float
    valid_from: float                   # = decision_sim_time
    valid_until: float                  # = decision_sim_time + proposal_ttl_s
    needs_transition: bool              # = proposed_action is not None and current_action is not None and proposed_action != current_action
    decision_frame_id: str              # 在哪一帧生成建议
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]
```

| 状态 | candidate_action | proposed_action |
|---|---|---|
| `PROPOSED` | 其他 action | candidate |
| `KEEP_CURRENT` | current | current |
| `NO_DEMAND` | current | current |
| `SUPPRESSED_MIN_GREEN` | 其他 action | current |
| `SUPPRESSED_SWITCH_MARGIN` | 其他 action | current |
| `SUPPRESSED_TRANSITION` | `None` | `None` |
| `MISSING_INPUT` / `STALE_INPUT` / `INVALID_PROPOSAL` | `None` | `None` |

### 2.4 confidence（固定公式）

```text
active_weight_sum = queue_weight + arrival_weight + forward_weight

queue_quality   = 1.0（候选 action 相关车道有新鲜 BSM/RSM 支撑）否则 0.0
arrival_quality = 1.0（存在上一份快照可算区间到达量）否则 0.0
forward_quality = fresh INTENT 车辆数 / fresh connected vehicles 数（分母 0 → 0.0）

input_quality = (queue_weight*queue_quality + arrival_weight*arrival_quality + forward_weight*forward_quality) / active_weight_sum

margin_confidence = clamp(margin / max(|best_score|, score_epsilon), 0.0, 1.0)
                   # 唯一合法 action 时 = 1.0
confidence = margin_confidence * input_quality
            # 全零需求 → 0.0
```

文档明示：confidence 是**启发式决策强度，不是校准概率**。

### 2.5 SignalPolicyConfig

```python
@dataclass(frozen=True, slots=True)
class SignalPolicyConfig:
    queue_weight: float = 1.0
    arrival_weight: float = 0.25
    forward_weight: float = 0.5
    forward_horizon_s: float = 30.0
    forward_decay_s: float = 15.0
    min_green_s: float = 5.0
    switch_score_margin: float = 1.0
    demand_epsilon: float = 1e-6
    score_epsilon: float = 1e-9
    proposal_ttl_s: float = 5.0
```

校验：权重有限且 ≥0、至少一个 >0；horizon/decay/ttl >0；min_green/margin ≥0。

---

## 3. RSI 车辆引导策略（CloudRulePolicy.propose_guidance）

### 3.1 候选筛选

```python
candidate = (
    bsm 新鲜（bsm_delivered_at ≥ now − freshness.bsm_s）     # connected_vehicles 已只含网联车
    and vehicle.next_signal_intersection_id is not None
    and vehicle.next_signal_intersection_id in scope.managed_ids   # 场景范围门禁（§7.3）
    and vehicle.distance_to_signal_m is not None
    and vehicle.distance_to_signal_m <= guidance_horizon_m
)
```

- 非机动车/非网联车永不进入（`connected_vehicles` 已只含 v2x_enabled=True）；
- 驶向**非算法管理路口**的车（`next_signal_intersection_id ∉ managed_ids`）不产生 RSI 候选，漏斗原因 `next_signal_not_managed`；
- 候选数（`in_horizon_candidates`）单独统计；**网络 delivery rate 的分母必须是 published，不是 candidates**。

### 3.2 速度建议（v1 只做"当前绿窗追赶"，两阶段：raw 生成 → 发射判定）

```
1. 解析 vehicle movement：
   fresh INTENT.turn_intent → 取该 movement
   否则当前车道仅允许一个 movement → 取该 movement
   否则 → MISSING_INPUT / reason="movement_unknown"
2. 顺序固定：SPaT missing/stale → stage ∈ {YELLOW, CLEARANCE} → phase_to_action → movement 服务判定
3. 当前 action 服务该 movement 且 stage=GREEN：
      available_green_s = remaining_time_s − green_clearance_buffer_s
      available_green_s ≤ 0           → NO_ACTION_NEEDED / "insufficient_green_remaining"
      eta_now = distance / max(speed, eps)
      eta_now ≤ available_green_s     → NO_ACTION_NEEDED（能安全通过）
      raw_target = distance / available_green_s
      lower = max(v_min_mps, speed_scale_low * current_speed_mps)
      upper = min(v_eff_max, speed_scale_high * current_speed_mps)
      lower > upper                   → INVALID_PROPOSAL / "empty_speed_interval"
      raw_target > upper              → NO_ACTION_NEEDED / "cannot_catch_green_within_limits"
      否则 target_speed = max(raw_target, lower)   # 可向上取到 lower；禁止把超上限 raw 截断后声称可行
4. 当前 action 不服务该 movement      → MISSING_INPUT / "next_served_green_unknown"（v1 不预测下一绿灯，不生成减速赶绿建议）
5. v_cur < min_guidance_speed_mps     → NO_ACTION_NEEDED / "vehicle_too_slow"（不给基本静止车生成速度引导）
6. SPaT 缺失/过期                     → MISSING_INPUT / STALE_INPUT
```

- `v_eff_max = min(config v_max_mps, lane_speed_limit_mps（MAP lane 元数据）)`，均缺失才退回 `v_max_mps`；
- 发射判定（§3.4）：THRESHOLD 下 `|target − current| ≥ speed_trigger_delta_mps` 才进入去重/冷却，否则 `SUPPRESSED_THRESHOLD`；FULL 绕过阈值/去重/冷却。

### 3.3 车道建议（严格校验 + advisory 边界）

- 前置：`distance_to_signal_m ≥ lane_change_min_distance_m(30.0)`；`turn_intent` 新鲜（无新鲜 INTENT 时仅当前车道唯一 movement 才 fallback）；
- 目标车道必须：存在（`lane_to_approach`）、**同一 incoming edge**（`lane_to_edge`）、**可证明相邻**（`|lane_to_index 差| = 1`；顺序不可靠 → `MISSING_INPUT / "lane_adjacency_unknown"`，不生成建议）、movement 允许 `turn_intent`；`same approach` 仅作附加校验；
- 价值：`当前车道 queue_estimate − 目标车道 queue_estimate ≥ lane_queue_margin(2.0)`；
- 新鲜度门禁：当前/目标车道 queue_estimate 无新鲜 BSM/RSM 支撑 → `STALE_INPUT/MISSING_INPUT`（`lane_queue_stale/lane_queue_missing`）；速度分量仍独立；
- 边界声明：**v1 车道建议仅路线/排队级 advisory，不验证目标车道间隙、碰撞风险或实际可执行性；active 车辆控制不得直接执行**。

### 3.4 组件化 Proposal 与状态

```python
@dataclass(frozen=True, slots=True)
class VehicleGuidanceProposal:
    vehicle_id: str
    status: GuidanceDecisionStatus            # 总体
    speed_status: GuidanceDecisionStatus      # 分量
    lane_status: GuidanceDecisionStatus
    current_speed_mps: float
    target_speed_mps: float | None
    current_lane_id: str | None
    target_lane_id: str | None
    target_lane_index: int | None
    guidance_type: str | None                 # speed / lane / combined
    reason: str
    confidence: float | None                  # v1 恒为 None（规则判定不输出置信度）
    valid_from: float
    valid_until: float
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]
```

- 总体状态：任一分量 `PROPOSED` → 总体 `PROPOSED`；均非 PROPOSED 时按固定优先级汇总：`STALE_INPUT > MISSING_INPUT > INVALID_PROPOSAL > SUPPRESSED_COOLDOWN > SUPPRESSED_DUPLICATE > SUPPRESSED_THRESHOLD > NO_ACTION_NEEDED`；组件状态为准，总体仅粗粒度统计；
- 速度 PROPOSED + 车道 STALE → 总体 PROPOSED，只发 speed RSI（合法分量不被无效分量否决）。

### 3.5 触发、去重、撤销与下发

```python
class GuidanceEmissionMode(str, Enum):
    THRESHOLD = "threshold"   # 合法 raw + 达阈值 + 过去重/冷却才发布（默认）
    FULL = "full"             # 合法 raw 即发布；绕过阈值/去重/冷却；仍不发空 RSI
    DISABLED = "disabled"     # 不生成不发布；信号规则照常
```

```python
@dataclass
class LastEmittedGuidanceState:
    target_speed_mps: float | None
    target_lane_id: str | None
    target_lane_index: int | None
    emitted_at: float
    valid_until: float
    reason: str
    emitted_message_id: str
```

- `should_emit = no_previous or target_lane_changed or |new−old| ≥ speed_resend_delta_mps or previous_expired(now ≥ emitted_at + guidance_ttl_s) or guidance_reason_changed`；冷却 `minimum_resend_interval_s` 未到 → `SUPPRESSED_COOLDOWN`；
- `LastEmittedGuidanceState` 在 `hub.publish(...).message_id` 返回后更新；记录的是"最近发布"，不是"最近送达"；
- **v1 不发撤销 RSI**：`NO_ACTION_NEEDED` 时旧建议在 `valid_until` 后自然失效；前端按 `now < valid_until` 判有效，延迟投递且已过期显示"已投递但已过期"，**不重算 TTL**；active 车控前必须设计显式取消语义；
- `guidance_reason_changed` 仅在已生成合法新建议时触发重发，不得把 `NO_ACTION_NEEDED` 变成空 RSI；
- 仅总体 `PROPOSED` → `build_rsi_draft` → `hub.publish`（`correlation_id=frame_id`）→ 走网络延迟/丢包；**不转换 `actions.vehicles`**。

### 3.6 漏斗（两层）

策略层：

```text
connected_seen → fresh_bsm → next_signal_known → next_signal_managed → distance_known
→ in_horizon_candidates → raw_proposals → threshold_passed → dedup_passed → cooldown_passed → published
```

过滤原因：`stale_bsm / missing_spat / missing_map / movement_unknown / next_served_green_unknown / next_signal_not_managed / not_in_guidance_horizon / no_action_needed / duplicate_guidance / cooldown_active / invalid_target_speed / invalid_target_lane`（另加 lane 分量：`lane_adjacency_unknown / lane_queue_stale / lane_queue_missing`）。

网络层：`published / delivered / network_dropped / episode_ended`（`message_dropped` 不是策略原因）。

`next_signal_not_managed = next_signal_known − next_signal_managed`；RSI 发布前**防御性双检** `next_signal_intersection_id ∈ scope.managed_ids`（即使候选筛选已通过，发布前仍校验，防止状态迁移竞态）。

### 3.7 GuidancePolicyConfig

```python
@dataclass(frozen=True, slots=True)
class GuidancePolicyConfig:
    guidance_horizon_m: float = 300.0
    speed_trigger_delta_mps: float = 2.0
    speed_resend_delta_mps: float = 1.0
    guidance_ttl_s: float = 10.0
    minimum_resend_interval_s: float = 5.0
    v_min_mps: float = 0.0
    v_max_mps: float = 16.0
    speed_scale_low: float = 0.5
    speed_scale_high: float = 1.3
    lane_queue_margin: float = 2.0
    lane_change_min_distance_m: float = 30.0
    min_guidance_speed_mps: float = 0.5
    green_clearance_buffer_s: float = 1.0
```

校验：`guidance_horizon_m/guidance_ttl_s > 0`；`minimum_resend_interval_s ≥ 0`；`speed_trigger/resend_delta ≥ 0`；`0 ≤ v_min ≤ v_max`；`0 < speed_scale_low ≤ speed_scale_high`；`lane_queue_margin/lane_change_min_distance_m/min_guidance_speed_mps/green_clearance_buffer_s ≥ 0`。

---

## 4. ActionArbiter 与 shadow 语义

### 4.1 DecisionMode

```python
class DecisionMode(str, Enum):
    OFF = "off"        # 显式消融：引擎存在，tick() 短路（不调策略/仲裁、不发 RSI、不写逐帧协同记录），原样返回 baseline
    SHADOW = "shadow"  # 默认：建议照常生成/记录/下发 RSI，信号实际动作 = baseline
    ACTIVE = "active"  # v1 运行时禁用：选择即 raise ActiveModeUnavailableError
```

- 未传 `--v2x-collab`：不创建引擎（无运行副作用）；
- RSI **不受仲裁**：SHADOW/未来 ACTIVE 下都是 advisory，走 hub 网络模型，从不写入 `actions.vehicles`；仲裁只针对信号动作。

### 4.2 仲裁输出与合并规则

```python
@dataclass(frozen=True, slots=True)
class CollabTickResult:
    protocol_actions: Actions                       # 完整 Protocol actions（SHADOW = baseline 完整等价副本）
    signal_sources: Mapping[str, DecisionSource]    # 每路口 baseline / cloud / fallback
    emitted_rsi_message_ids: tuple[str, ...]
    emitted_rsi_message_ids_by_intersection: Mapping[str, tuple[str, ...]]
    stats_delta: CollabStatsDelta
    frame_id: str
    sim_time: float
```

"逐字节一致"定义为：**规范化 JSON 序列化结果一致，且输入 `baseline_actions` 对象不被原地修改**（不比较字典内存布局/键插入顺序）。

路口集合合并（保守）：

```text
baseline 有、proposal 缺失：保留 baseline
baseline 与 proposal 都有：执行仲裁
proposal 有、baseline 无：不得新增动作，忽略并记录 proposal_without_baseline
仲裁不删除 baseline 原有路口
```

未来 ACTIVE：仅替换 `protocol_actions.signals` 中被云端接管的路口；`actions.vehicles` 与其他字段保持 baseline。

### 4.3 ACTIVE 采纳规则与 validator

```python
CLOUD_SELECTABLE_STATUSES = {
    SignalDecisionStatus.PROPOSED,
    SignalDecisionStatus.KEEP_CURRENT,
    SignalDecisionStatus.NO_DEMAND,
    SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
    SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
}
FALLBACK_STATUSES = {
    SignalDecisionStatus.MISSING_INPUT,
    SignalDecisionStatus.STALE_INPUT,
    SignalDecisionStatus.INVALID_PROPOSAL,
    SignalDecisionStatus.SUPPRESSED_TRANSITION,
}
```

```python
validate_signal_proposal(proposal, *, run_id, frame_id, intersection_id, now,
                         current_action, in_transition, valid_actions) -> ProposalValidationResult
```

至少检查：交集匹配、`decision_frame_id == 当前决策帧`、`valid_from <= now < valid_until`（严格半开区间）、`current_action` 与 Cloud view 一致、`proposed_action ∈ valid_actions`、非 transition、状态 ∈ CLOUD_SELECTABLE_STATUSES、所有分数与关键字段有限。

- **SHADOW 也运行同一 validator**，记录 `validation_status / would_select_cloud / would_select_action / validation_failure_reason`（active 就绪度）；
- ACTIVE：通过验证且有约束后动作 → `selected=proposed_action, source=CLOUD`；否则 → `selected=baseline, source=FALLBACK`。

### 4.4 仲裁记录（按路口，与 RSI emission 分离）

```text
run_id / episode_id / frame_id / intersection_id / sim_time
baseline_action / candidate_action / proposed_action / selected_action
proposal_status / validation_status / validation_failure_reason
decision_source / selection_status / confidence / reason
signal_event_ref = (run_id, frame_id, intersection_id)
```

- `selection_status ∈ {selected_baseline_shadow, selected_cloud, selected_fallback}`（替代 application_status，避免暗示 SUMO 已执行）；
- 仲裁时**不预测** SIGNAL_CONTROL 的 message_id；SIGNAL_CONTROL 在后续 `hub.ingest_actions(result.protocol_actions, frame)` 生成，通过稳定引用 `(run_id, frame_id, intersection_id)` 关联；
- RSI 的 `emitted_message_id`/投递结果放 vehicle-guidance 记录与 tick 结果，不放进逐路口 arbitration。

### 4.5 active 门禁清单（v1 不实现）

1. 所有信号建议通过合法相位与安全 mask（valid_actions/transition 排除）；
2. 最小绿灯、黄灯过渡、全红约束不被绕过；
3. 输入缺失/过期稳定回退 baseline；
4. shadow 下逐帧 `applied == baseline` 断言全绿（回归强制）；
5. 云端建议输出确定性、可回放；
6. 多 seed 建议覆盖率/冲突率/切相频率审计通过；
7. active 先小规模短程场景 → 再 20 路口实验。

额外明确：Arbiter 只选择目标动作；**不负责生成黄灯、全红等中间阶段**；必须证明现有 Protocol/SUMO 执行器会安全插入过渡，或新增独立 transition-aware executor；不能仅凭 `not transition phase` 就认为直接切换目标相位是安全的。

---

## 5. 指标与统计（collab/stats.py）

### 5.1 必写记录：collab_tick_stats

每决策帧 1 条，`engine.tick` 原子写入，**不可关闭**：

```json
{
  "record_type": "collab_tick_stats",
  "run_id": "run_1", "episode_id": "ep_1",
  "frame_id": "ep_1:step:000010", "sim_time": 50.0,
  "signal": {
    "baseline_slots": 20, "decision_records": 20,
    "status_counts": {}, "validation_counts": {},
    "proposal_without_baseline": 0
  },
  "guidance": {
    "connected_seen": 30, "fresh_bsm": 27,
    "next_signal_known": 25, "next_signal_managed": 8,
    "distance_known": 7, "in_horizon_candidates": 5,
    "raw_proposals": 3, "threshold_passed": 2,
    "dedup_passed": 2, "cooldown_passed": 2,
    "published": 1, "filter_reason_counts": {}
  }
}
```

- `edge_snapshot` = 可选详细审计；`cloud_proposal` = 决策解释；`arbitration="differences"` 只减详细记录，**不影响汇总可重放性**；
- 网络 `delivered/dropped` 从 Hub delivery 记录补齐。

### 5.2 summary 结构

```json
{
  "collab": {
    "schema_version": "1.0",
    "decision_mode": "shadow",
    "guidance_mode": "threshold",
    "signal": {
      "baseline_signal_slots": 100,
      "decision_record_coverage": 1.0,
      "selectable_output_rate": 0.86,
      "suggested_switch_rate": 0.21,
      "action_agreement_rate": 0.74,
      "disagreement_matrix": {},
      "stale_input_rate": 0.0,
      "missing_input_rate": 0.0,
      "decision_input_age_s": {"mean": 0.02, "p50": 0.02, "p95": 0.03}
    },
    "guidance": {"funnel": {}, "rates": {}, "guidance_type_counts": {},
                 "delivered_count": 0, "expired_on_delivery_count": 0,
                 "expired_on_delivery_rate": null, "effective_delivery_rate": null},
    "arbitration": {"selection_status_counts": {}, "proposal_without_baseline": 0},
    "validation": {"validation_pass_rate": 0.82, "failure_reason_counts": {}},
    "integrity": {
      "missing_source_delivery_refs": 0,
      "orphan_rsi_messages": 0,
      "orphan_rsi_deliveries": 0,
      "missing_signal_event_refs": 0,
      "duplicate_terminal_delivery_records": 0
    }
  }
}
```

- 顶层另有 `scope` 块（§7.4）：`source / preset_id / registered_intersections / algorithm_controlled_intersections / fixed_intersections / managed_ids`。

### 5.3 指标定义与分母（冻结）

信号（分母统一 `baseline_signal_slots` = baseline `actions.signals` 路口×帧总数）：

```text
decision_record_coverage = 有 SignalProposal 记录的 baseline 路口×帧 / baseline_signal_slots
selectable_output_rate   = (status ∈ CLOUD_SELECTABLE_STATUSES 且 proposed_action 非空) / baseline_signal_slots
validation_pass_rate     = validator 通过数 / baseline_signal_slots
fallback_readiness_rate  = (shadow) would_select_cloud == false / baseline_signal_slots
fallback_rate            = (active 将来) selected_fallback / baseline_signal_slots   # 不与 shadow 共用字段
```

其余分母：

```text
action_agreement_rate    = validator 通过且 proposed==baseline / validator 通过数
suggested_switch_rate    = PROPOSED / decision_records
stale_input_rate         = STALE_INPUT / decision_records
missing_input_rate       = MISSING_INPUT / decision_records
```

引导（`candidates := in_horizon_candidates`）：

```text
guidance_generation_rate = raw_proposals / in_horizon_candidates
threshold_pass_rate      = threshold_passed / raw_proposals
proposal_publish_rate    = published / raw_proposals
candidate_to_publish_rate= published / in_horizon_candidates
network_delivery_rate    = delivered / published
```

- FULL 模式：诊断性计算 `would_pass_threshold / would_be_duplicate / would_be_in_cooldown`，`threshold_pass_rate` 标注 `diagnostic=true`；DISABLED：漏斗计数 0/`null`；
- `decision_input_age_s = decision_sim_time − max(支撑消息 delivered_at)`（输入新鲜度，非计算延迟）；计算耗时另用墙钟 `policy_compute_time_ms`（可选，不与仿真时间混用）；
- 过期投递：`delivered_but_expired = delivery.status=="delivered" and delivered_at ≥ proposal.valid_until`；统计 `delivered_count / expired_on_delivery_count / expired_on_delivery_rate / effective_delivery_rate=(delivered−expired)/published`；过期 RSI 仍计入 delivered，不计入"有效送达建议"。

### 5.4 完整性审计（引用集合，非计数）

- `arbitration_refs == signal_event_refs`：比较 `(run_id, frame_id, intersection_id)` 集合（SIGNAL_CONTROL 的 **message 记录**，非 delivery；网络丢包不影响；`proposal_without_baseline` 不进入 refs）；适用条件：collab 启用且 `log_arbitration_mode=="all"`；`"differences"` 模式由 `collab_tick_stats` 保证完整性；
- RSI 审计（限定 collab 发布）：每条 `emitted_rsi_message_id` 必须有且仅有一条 RSI message 记录、episode 结束后有且仅有一条终态 delivery（delivered/network_drop/episode_ended）；反向：emitted 集合内的 RSI 必须有对应 vehicle-guidance 记录；`source_id` 保持冻结的 `"cloud"`，关联靠 emitted 集合；
- integrity 块显式暴露 `missing_source_delivery_refs / orphan_rsi_messages / orphan_rsi_deliveries / missing_signal_event_refs / duplicate_terminal_delivery_records`，不静默忽略。

### 5.5 多 episode 聚合（pooled）

- episode 级输出完整 counts/rates/分布；
- run 级：`aggregate rate = Σ numerator / Σ denominator`（**不是** mean(episode rate)）；分布合并全部有效样本、disagreement matrix 逐格求和、filter reason 计数求和；零总分母 `null`；
- `per_episode_rate_mean/median/min/max` 仅作 seed 稳定性参考，不替代 pooled。

---

## 6. 集成与测试

### 6.1 CLI / env（优先级：CLI > env > 默认）

```
evaluate.py 新增：
  --scenario-preset {xiongan_20,east_dense,west_dense}   # 标准场景入口（§7）
  --intersections demo_3,demo_5,...                     # 专家覆盖入口；单个整数 N 兼容为 demo_1..N（§7.2）
  --v2x-collab                              # 启用协同层（隐含启动 hub）
  --v2x-collab-mode {off,shadow,active}     # 默认 shadow；active → 运行时 ActiveModeUnavailableError
  --v2x-guidance-mode {threshold,full,disabled}   # 默认 threshold
env：
  COSLIGHT_V2X_COLLAB=1
  COSLIGHT_V2X_COLLAB_MODE=...
  COSLIGHT_V2X_GUIDANCE_MODE=...
```

- 非法枚举**启动阶段立即报错**，不运行到 episode 中途；
- 未启用 `--v2x-collab`：不创建引擎，mode 参数无副作用。

### 6.2 Sink 组合（无真 NullSink）

```text
CompositeSink(
    required=[InMemoryRecordCollector()],   # collab 开启时必有，不可关闭
    optional=[JsonlSink(path)],             # 仅 --v2x-log 开启时存在
)
```

| 配置 | Sink |
|---|---|
| collab 关闭、无日志 | 可不创建 Hub/协同记录器 |
| collab 开启、无日志 | `InMemoryRecordCollector` |
| collab 开启、有日志 | `InMemoryRecordCollector + JsonlSink` |
| 仅 V2X 日志、无 collab | 现有 JSONL sink |

无 `--v2x-log` 时不持久化文件，但所有 Hub 与 collab 记录仍进内存收集器，用于 episode/run summary 与完整性审计。

### 6.3 测试分层

| 层 | 内容 |
|---|---|
| 模块单测 | snapshot 不可变；aggregator 只收已投递消息/`last_delivery_at`/跨帧 arrivals/stage_elapsed 透传；state 的 age/missing/stale + MAP 不按 10s 过期 + 静态上下文；policy 信号（评分/平分/min_green/margin/no_demand/transition 顺序）；policy 引导（候选/区间/相邻/新鲜度/组件独立）；arbiter（shadow 不变量/OFF 短路/ACTIVE 报错/validator）；records/stats（schema/必写/ pooled/完整性）；engine（RSI 仅 PROPOSED/emitted_message_id/stats_delta） |
| 集成回归 | shadow 逐帧规范化 JSON 一致；`arbitration_refs == signal_event_refs`；每条 collab RSI 恰 1 条 message + 1 条终态 delivery；`summary["collab"]` 可从记录重建 |
| replay 等价 | ① JSONL 模式：文件重放 == 运行时 summary（规范化一致）；② 无文件模式：InMemoryRecordCollector 重建 == 运行时 summary。规范化规则：字典 key 排序、浮点容差、分布确定排序、`null` 与无定义结构一致 |
| 确定性 fixture | 构造已知场景保证至少一辆车：当前绿灯服务其 movement、当前速度无法安全通过、速度范围内可追赶、速度差 > 2 m/s → 断言 `raw_proposals>0`、`published>0`、RSI message 恰 1 条、终态 delivery 恰 1 条；另构造 lane queue 差异覆盖车道建议正向分支 |
| 20 路口 smoke | 见 6.4 |

基线数量说明：现有 v2x ≈69、coslight ≈98 测试仅作现状参考；**验收契约是相关测试集零失败、完整 pytest 退出码 0**，不把具体数量作为长期断言。

### 6.4 20 路口 smoke 验收（`--methods model --v2x-collab`）

- 运行完成、无异常；
- shadow 下完整 Protocol actions 与 baseline 规范化一致；
- `baseline_signal_slots > 0`；`decision_record_coverage ∈ [0,1]` 可计算；
- guidance funnel 字段完整；若 `published > 0` 则 `network_delivery_rate` 可计算，若 `published == 0` 则 `network_delivery_rate = null`；
- `integrity` 全部为 0；
- MAP/SPaT 延迟导致的初始缺失状态被正常记录（不绕过消息投递）；
- `simulation/sumo/` 零修改。

`selectable_output_rate > 0` 不作通用硬门槛（除非固定 seed/fixture 已证明必然具备有效输入）。

东部典型场景 smoke（`--methods model --scenario-preset east_dense --v2x-collab`）：

- 验证 §7.5 清单 1~11：`demo_3/5/6/9` 算法 + 其余 16 路口固定配时；
- 未选路口无 SignalProposal / arbitration / RSI 候选、不出现在 `actions.signals`；
- `scope` 块记录 `algorithm_controlled=4 / fixed=16 / managed_ids`；
- `traffic_metrics.network_wide` 仍覆盖 20 路口。

### 6.5 产出物

- 本 spec；随后 writing-plans 产出实现计划；
- 服务器 `feature/rl` 提交。

---

## 7. 场景预设与路口范围

### 7.1 注册表（单一事实源）

新增**算法无关**的纯 Python 模块 `config/scenario_presets.py`（无依赖、不导入任何算法/后端代码）：

```python
@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    preset_id: str
    label: str
    intersection_ids: tuple[str, ...]
    map_template: str
    description: str

SCENARIO_PRESETS = {
    "xiongan_20": ScenarioPreset(
        "xiongan_20", "雄安20路口路网", tuple(f"demo_{i}" for i in range(1, 21)),
        "TotalMap_20", "全量 20 路口（= 现有默认行为）"),
    "east_dense": ScenarioPreset(
        "east_dense", "东部密集路口场景", ("demo_3", "demo_5", "demo_6", "demo_9"),
        "TotalMap_20", "雄安东部典型密集路口场景"),
    "west_dense": ScenarioPreset(
        "west_dense", "西部密集路口场景", ("demo_14", "demo_15", "demo_19"),
        "TotalMap_20", "雄安西部密集路口场景"),
}
```

- 中立模块由 **backend 与 evaluate 共同导入**（单一事实源，不复制两份）：backend `app/scenario/presets.py` 改为透传 `from config.scenario_presets import SCENARIO_PRESETS`（保持既有 backend 导出名不变），evaluate 直接导入同一模块；新增一致性测试 `test_scenario_presets.py`，断言 backend 导出与 `config/scenario_presets.py` 完全一致（`preset_id / label / intersection_ids / map_template`）。
- **算法无关**：`ScenarioPreset` / `ResolvedScenarioScope`（§7.2）不依赖任何具体算法；CoSLight、IPPO、MAPPO 及未来算法均可复用「preset 路口跑算法 + 其余路口 SUMO 内置固定配时」机制，各算法 evaluate 入口接入方式见 §7.2「跨算法接入」。

### 7.2 CLI 与解析规则

```text
--scenario-preset {xiongan_20,east_dense,west_dense}   # 标准入口
--intersections demo_3,demo_5,demo_6,demo_9            # 专家覆盖入口（逗号分隔 ID）
两者互斥（argparse mutually exclusive group），同时传入 → 参数冲突报错

解析规则：
  指定 --scenario-preset → 从注册表解析路口集合
  指定 --intersections   → 使用自定义路口集合；单个整数 N 兼容为 demo_1..N（保持 gate_experiment.sh 等旧调用可用）
  两者都未指定           → 沿用现有默认（demo_1..20，等价 xiongan_20 范围）
```

解析产出（evaluate 与 backend 共享）：

```python
@dataclass(frozen=True, slots=True)
class ResolvedScenarioScope:
    source: Literal["preset", "custom", "default"]   # 本次范围来源
    preset_id: str | None                            # source=preset 时非空
    managed_ids: tuple[str, ...]                     # 算法控制 == 协同 managed 范围（§7.3）
```

- 帮助文案：`Select a predefined algorithm/collaboration intersection scope. Does not change the SUMO network or traffic demand.`
- `--intersections` 解析规则：单个整数 N → `demo_1..N`（保序）；逗号列表 → 每个元素 `strip()` 后按序保留；空项、重复 ID、非 `demo_\d+` 格式 → **启动阶段报错**（重复 ID 报错而非静默去重，避免配置笔误被吞）。
- 校验分两阶段：**CLI 阶段仅格式**（`--intersections` 格式、`--scenario-preset` 由 argparse choices 拒绝）；**`on_initialize` 阶段存在性 fail-fast**（解析出的每个路口必须存在于 initialize catalog，否则**立即报错**）。catalog **仅用于校验 scope 合法性，不能绕过 MAP 投递直接建立静态上下文**（静态上下文仍只能由已投递 MAP 构建）。

跨算法接入：

- **CoSLight**：`algorithms/coslight/evaluate.py` 现有 `--intersections`（整数 N）+ 本轮新增 `--scenario-preset`；
- **IPPO**：`algorithms/ippo/evaluate_ckpt.py` 的 `intersection_ids` 来自 checkpoint 元数据（无 `--intersections` 参数）；preset 接入为后续任务：校验 `checkpoint 范围 ⊆ resolved_scope.managed_ids`，超范围报错；
- **MAPPO**：不在本仓库（独立 patch/workspace）；接入时直接复用 `config/scenario_presets.py` + `ResolvedScenarioScope`，无需移动算法代码；
- 机制本身**算法无关**：混合控制是 `SimulationConfig(intersection_ids, control_mode="algorithm")` 层语义，任何走该接口的算法都能获得「preset 路口算法控制 + 其余路口 SUMO 内置固定配时」。

### 7.3 语义（v1 冻结）

- **`collab_managed_ids == algorithm_controlled_ids == resolved_scope.managed_ids`**（`source=preset` 时即 `preset.intersection_ids`）；v1 不允许两套范围独立配置（未来研究需要时再开放 `--v2x-collab-intersections`）；
- 未选路口：不创建 controller、不产生 SignalProposal、不进 arbitration、不产生 RSI 候选、不出现在 `actions.signals`；由 SUMO 网络内置固定配时 program 自动运行（机制已验证：algorithm 模式下 controllers 只覆盖 selected intersections，TotalMap_20 含全部 20 路口及各自固定 program）；
- preset 不改变：网络文件、OD 需求、period、fixed program、仿真时长（`map_template` 不切网，v1 不做 C）；
- 候选门禁：RSI 只面向 `next_signal_intersection_id ∈ managed_ids` 的网联车（§3.1）；发布前防御性双检（§3.6）。

### 7.4 指标与输出

- summary 新增 `scope` 块：

```json
{
  "scope": {
    "source": "preset",
    "preset_id": "east_dense",
    "registered_intersections": 20,
    "algorithm_controlled_intersections": 4,
    "fixed_intersections": 16,
    "managed_ids": ["demo_3", "demo_5", "demo_6", "demo_9"]
  }
}
```

- `fixed_intersections = len(registered_intersections) − len(managed_ids)`（动态计算，不硬编码）；

- **collab 指标分母 = managed scope**：`baseline_signal_slots = managed 路口数 × 有 baseline 动作的帧数`；decision coverage / selectable / agreement / switch / arbitration / validation / managed RSI 候选均只算 managed 路口；
- **交通指标三块报告**（口径不得互换）：
  - `traffic_metrics.network_wide`：现有全网络指标（20 路口 tripinfo 口径）；
  - `traffic_metrics.managed_trip_scope`：途经 managed 路口的行程子集（按 tripinfo 途经 managed 路口过滤；trip 数据不可得时 `available=false` + `unavailable_reason`，不静默退化）；
  - `traffic_metrics.managed_intersections`：managed 路口本身的逐路口等待/排队统计；
  避免只报局部改善而漏掉外围拥堵转移。

### 7.5 验证清单

1. `east_dense` 精确解析为 `demo_3/5/6/9`（`west_dense` 为 `demo_14/15/19`，`xiongan_20` 为 1..20）；
2. preset 中路口不存在于 initialize → 启动立即报错；
3. `--scenario-preset` 与 `--intersections` 同时传入 → 参数冲突；
4. controller 只为 managed 路口创建；
5. collab proposal/arbitration 只出现 managed 路口；
6. RSI 目标车辆 `next_signal_intersection_id` 必须 ∈ managed_ids；
7. 其他路口不出现在 `actions.signals`；
8. summary 正确记录 `scope.source / scope.preset_id / algorithm_controlled / fixed_intersections`；
9. shadow 下 managed 路口 applied == baseline（规范化一致）；
10. 全网 traffic metrics 仍覆盖 20 个路口；
11. `config/scenario_presets.py` 与 backend 导出的一致性测试通过（backend 为透传导入）。

## 8. 附录

### 8.1 枚举总表

```python
class DecisionMode(str, Enum):      OFF / SHADOW / ACTIVE
class GuidanceEmissionMode(str, Enum): THRESHOLD / FULL / DISABLED
class SignalDecisionStatus(str, Enum):
    PROPOSED / KEEP_CURRENT / NO_DEMAND / STALE_INPUT / MISSING_INPUT /
    INVALID_PROPOSAL / SUPPRESSED_MIN_GREEN / SUPPRESSED_SWITCH_MARGIN /
    SUPPRESSED_TRANSITION
class GuidanceDecisionStatus(str, Enum):
    PROPOSED / NO_ACTION_NEEDED / STALE_INPUT / MISSING_INPUT /
    INVALID_PROPOSAL / SUPPRESSED_DUPLICATE / SUPPRESSED_COOLDOWN /
    SUPPRESSED_THRESHOLD
class DecisionSource(str, Enum):    BASELINE / CLOUD / FALLBACK
# selection_status: selected_baseline_shadow / selected_cloud / selected_fallback
```

### 8.2 关键冻结语义速查

- `SPaT.next_stage_start_time` = **绝对仿真时刻**（`sim_time + remaining`），与 ETA 比较前必须换算 `time_to_next_green_s`；v1 不使用该分支；
- `SPaT.stage_elapsed` 冻结字段直接透传为 `stage_elapsed_s`；
- `valid_from <= now < valid_until`（严格半开）；RSI 过期不重算 TTL；
- RSI `source_id` 保持 `"cloud"`（协议不动），collab 关联靠 `emitted_message_ids`；
- SIGNAL_CONTROL 只由 `ingest_actions()` 产生；信号建议不发布为 SIGNAL_CONTROL；
- `collab_tick_stats` 不可关闭；`log_edge_snapshot` / `log_arbitration_mode` 可调。

### 8.3 渗透率实验假设（待验证，非工程依据）

> 通过 5% / 20% / 50% / 80% 网联渗透率消融，检验 INTENT 前视修正在低渗透率下的有效性。
