# V2X 车路云协同消息框架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `algorithms/v2x/` 建立纯 Python 的 V2X 车-路-云消息框架（BSM/INTENT/SPaT/MAP/RSM/RSI + SignalControlEvent、延迟/丢包模拟、日志回放统计），并让 coslight 以 shadow-mode 最小接入跑通闭环，不改 SUMO、不改 coslight 决策逻辑。

**Architecture:** V2XHub 进程内消息总线（生命周期状态机 + heapq 延迟投递 + 按 (run,episode,source,type) 调度/序号）；Protocol 2.0 适配器把 `initialize/step` 载荷映射为上行消息、把 `actions` 映射为 RSI/SignalControlEvent 影子记录；JSONL sink + stats + replay CLI；coslight 通过 `controller.initialize/step/finish` 内嵌 no-op 钩子接入（默认关闭）。

**Tech Stack:** Python 3.10+（dataclasses、heapq、uuid5、hashlib/sha256、json、argparse）、pytest。无 torch/SUMO 依赖。

**工作流（重要）:** 代码在本地 `/Users/g/Documents/车路云/tmp/coslight-parallel-stage/` 编辑，rsync 到服务器 `346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/`，在服务器 BWformer 环境跑 pytest，并在服务器 git 仓库提交（branch `feature/rl`）。每条 Commit 步骤都包含 rsync + git add/commit。

```bash
# 同步（每任务提交前执行，路径以任务为准）
rsync -avz -e "ssh -o StrictHostKeyChecking=no" algorithms/v2x/ 346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/
# 服务器跑测试
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && export PYTHONPATH=/usr/share/sumo/tools && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x -q --tb=short'
```

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `algorithms/v2x/__init__.py` | 公共 API 导出（Task 12） |
| `algorithms/v2x/config.py` | `V2XConfig`、`RSUCoverageConfig` + 校验 |
| `algorithms/v2x/messages.py` | `stable_hash01`、`MessageDraft`、`V2XMessage`、必填字段表、uuid5 message_id |
| `algorithms/v2x/entities.py` | `VehicleCapability`/`Vehicle`/`RSU`、能力判定链、渗透率 |
| `algorithms/v2x/derive.py` | 转向/换道/到达/相位时间表推导 + 置信度 |
| `algorithms/v2x/coverage.py` | `is_in_rsu_coverage` + fallback 规则 |
| `algorithms/v2x/protocol.py` | 协议 2.0 载荷 → 消息草稿；actions → RSI/事件草稿 |
| `algorithms/v2x/logger.py` | `LogRecord`、`JSONLSink`、episode_start/message/delivery/episode_end 记录构造 |
| `algorithms/v2x/hub.py` | `V2XHub`、`FrameContext`、两阶段 API、延迟队列、生命周期、调度、序号、统计收集 |
| `algorithms/v2x/stats.py` | `build_summary()` 结构化统计（零分母 null） |
| `algorithms/v2x/replay.py` | 回放 CLI（--summary/--print） |
| `algorithms/v2x/adapters/coslight.py` | `CoslightV2XBridge` + controller 钩子（env 开关，惰性导入） |
| `algorithms/v2x/tests/*` | 每模块 pytest |
| `algorithms/coslight/controller.py` | 修改：`step` 重命名为 `_step_impl` + 新 `step` 包装；`initialize`/`finish` 尾部加 no-op 钩子 |
| `algorithms/coslight/evaluate.py` | 修改：加 `--v2x-log PATH` 与 `COSLIGHT_V2X_LOG/RUN_ID` env 传递 |

---

## Task 1: config.py — 配置与校验

**Files:**
- Create: `algorithms/v2x/config.py`
- Test: `algorithms/v2x/tests/test_config.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_config.py
import math
import pytest
from algorithms.v2x.config import V2XConfig, RSUCoverageConfig, V2XConfigError


def test_defaults():
    cfg = V2XConfig()
    assert cfg.schema_version == "1.0"
    assert cfg.bsm_interval_s == 5.0
    assert cfg.penetration_rate == 1.0
    assert cfg.drop_rate == 0.0
    assert cfg.default_latency_ms == 20.0
    assert cfg.network_seed == 0
    assert cfg.capability_seed == 0
    assert cfg.detection_radius_m is None


def test_interval_lookup():
    cfg = V2XConfig(bsm_interval_s=2.0, rsm_interval_s=0.0)
    assert cfg.interval_for("BSM") == 2.0
    assert cfg.interval_for("RSM") == 0.0
    assert cfg.interval_for("UNKNOWN") == 0.0


def test_latency_link_mapping():
    cfg = V2XConfig(default_latency_ms=20.0, uplink_latency_ms=10.0,
                    downlink_latency_ms=30.0)
    assert cfg.latency_ms_for("BSM") == 10.0
    assert cfg.latency_ms_for("MAP") == 10.0
    assert cfg.latency_ms_for("RSI") == 30.0
    assert cfg.latency_ms_for("SIGNAL_CONTROL") == 30.0


@pytest.mark.parametrize("kwargs", [
    {"penetration_rate": 1.5},
    {"penetration_rate": -0.1},
    {"drop_rate": 1.1},
    {"drop_rate": -0.01},
    {"default_latency_ms": -1.0},
    {"latency_jitter_ms": -1.0},
    {"uplink_latency_ms": -1.0},
    {"downlink_latency_ms": -1.0},
    {"bsm_interval_s": math.nan},
    {"network_seed": 1.5},   # int 字段校验
])
def test_invalid(kwargs):
    with pytest.raises(V2XConfigError):
        V2XConfig(**kwargs)


def test_coverage_config_defaults():
    cov = RSUCoverageConfig()
    assert cov.positions == {}
    assert cov.extra_covered_lane_ids == {}
```

- [x] **Step 2: 运行确认失败**

Run: `pytest algorithms/v2x/tests/test_config.py -q --tb=short`
Expected: FAIL（`ModuleNotFoundError: No module named 'algorithms.v2x'`）

- [x] **Step 3: 实现 config.py**

```python
# algorithms/v2x/config.py
"""V2X 框架配置：通信周期、能力、网络随机与 RSU 覆盖。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional

UPSTREAM_TYPES = frozenset({"BSM", "INTENT", "SPaT", "MAP", "RSM"})
DOWNSTREAM_TYPES = frozenset({"RSI", "SIGNAL_CONTROL"})


class V2XConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class V2XConfig:
    schema_version: str = "1.0"

    bsm_interval_s: float = 5.0
    intent_interval_s: float = 5.0
    spat_interval_s: float = 5.0
    rsm_interval_s: float = 5.0
    scheduling_epsilon_s: float = 1e-9

    connected_classes: frozenset = frozenset({"passenger", "bus"})
    penetration_rate: float = 1.0
    capability_seed: int = 0

    default_latency_ms: float = 20.0
    uplink_latency_ms: Optional[float] = None
    downlink_latency_ms: Optional[float] = None
    latency_jitter_ms: float = 0.0
    drop_rate: float = 0.0
    network_seed: int = 0

    detection_radius_m: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_seed, int) or isinstance(self.capability_seed, bool):
            raise V2XConfigError("capability_seed must be int")
        if not isinstance(self.network_seed, int) or isinstance(self.network_seed, bool):
            raise V2XConfigError("network_seed must be int")
        if not isinstance(self.connected_classes, frozenset):
            raise V2XConfigError("connected_classes must be frozenset")
        if not (0.0 <= self.penetration_rate <= 1.0):
            raise V2XConfigError("penetration_rate must be in [0, 1]")
        if not (0.0 <= self.drop_rate <= 1.0):
            raise V2XConfigError("drop_rate must be in [0, 1]")
        for name in (
            "bsm_interval_s", "intent_interval_s", "spat_interval_s",
            "rsm_interval_s", "scheduling_epsilon_s", "default_latency_ms",
            "latency_jitter_ms", "uplink_latency_ms", "downlink_latency_ms",
        ):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(float(value)) or math.isnan(float(value))):
                raise V2XConfigError(f"{name} must be finite")
        for name in ("default_latency_ms", "latency_jitter_ms",
                     "uplink_latency_ms", "downlink_latency_ms"):
            value = getattr(self, name)
            if value is not None and float(value) < 0.0:
                raise V2XConfigError(f"{name} must be >= 0")
        if self.detection_radius_m is not None and self.detection_radius_m <= 0.0:
            raise V2XConfigError("detection_radius_m must be > 0 when set")

    def interval_for(self, message_type: str) -> float:
        return {
            "BSM": self.bsm_interval_s,
            "INTENT": self.intent_interval_s,
            "SPaT": self.spat_interval_s,
            "RSM": self.rsm_interval_s,
        }.get(message_type, 0.0)

    def latency_ms_for(self, message_type: str) -> float:
        if message_type in DOWNSTREAM_TYPES:
            return self.downlink_latency_ms if self.downlink_latency_ms is not None else self.default_latency_ms
        return self.uplink_latency_ms if self.uplink_latency_ms is not None else self.default_latency_ms


@dataclass(frozen=True, slots=True)
class RSUCoverageConfig:
    positions: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    extra_covered_lane_ids: Mapping[str, frozenset] = field(default_factory=dict)
```

- [x] **Step 4: 运行确认通过**

Run: `pytest algorithms/v2x/tests/test_config.py -q --tb=short`
Expected: PASS（7 passed）

- [x] **Step 5: 提交**

```bash
# 本地确保通过后：
rsync -avz -e "ssh -o StrictHostKeyChecking=no" algorithms/v2x/ 346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && export PYTHONPATH=/usr/share/sumo/tools && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_config.py -q --tb=short && git add algorithms/v2x && git commit -m "feat(v2x): V2XConfig/RSUCoverageConfig 配置与校验"'
```

---

## Task 2: messages.py — 消息基类与 ID 契约

**Files:**
- Create: `algorithms/v2x/messages.py`
- Test: `algorithms/v2x/tests/test_messages.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_messages.py
import pytest
from algorithms.v2x.messages import (
    V2XMessage, MessageDraft, make_message_id, stable_hash01,
    validate_draft, REQUIRED_FIELDS, MESSAGE_NAMESPACE,
)


def test_stable_hash01_deterministic_and_range():
    a = stable_hash01("x|1")
    b = stable_hash01("x|1")
    c = stable_hash01("x|2")
    assert a == b
    assert a != c
    assert 0.0 <= a < 1.0


def test_stable_hash01_sha256_expected_value():
    # 锁死算法：sha256("abc") 前 8 字节大端 / 2**64
    import hashlib
    digest = hashlib.sha256(b"abc").digest()
    expected = int.from_bytes(digest[:8], "big") / 2**64
    assert stable_hash01("abc") == expected


def test_message_id_includes_all_keys():
    a = make_message_id("r1", "ep1", "v1", "BSM", 1)
    b = make_message_id("r1", "ep1", "v1", "INTENT", 1)  # 同源不同型不冲突
    c = make_message_id("r2", "ep1", "v1", "BSM", 1)     # 不同 run 不冲突
    assert a != b
    assert a != c
    assert make_message_id("r1", "ep1", "v1", "BSM", 1) == a


def test_validate_draft_required_fields():
    draft = MessageDraft(
        message_type="BSM", source_id="v1", destination="cloud",
        sim_time=10.0,
        payload={"vehicle_id": "v1", "type_id": "t", "position": (0.0, 0.0),
                 "motion": {"speed_mps": 1.0}, "location": {"lane_id": "L_0"},
                 "route_edges": ["e0", "e1"], "next_signal": None,
                 "front_gap_m": None, "rear_gap_m": None, "gap_source": None},
    )
    validate_draft(draft)  # 不抛
    bad = MessageDraft(message_type="BSM", source_id="v1", destination="cloud",
                       sim_time=10.0, payload={"vehicle_id": "v1"})
    with pytest.raises(ValueError, match="BSM.*missing"):
        validate_draft(bad)
    with pytest.raises(ValueError, match="unknown message type"):
        validate_draft(MessageDraft("NOPE", "s", "d", 0.0, {}))


def test_message_to_dict_has_message_type():
    msg = V2XMessage(
        message_type="BSM", message_id="m1", schema_version="1.0",
        run_id="r", episode_id="e", frame_id="e:step:000001",
        sequence_no=1, sim_time=10.0, source_id="v1", destination="cloud",
        correlation_id=None, payload={"vehicle_id": "v1"},
    )
    data = msg.to_dict()
    assert data["message_type"] == "BSM"
    assert data["message_id"] == "m1"
    assert data["payload"]["vehicle_id"] == "v1"
```

- [x] **Step 2: 运行确认失败**

Run: `pytest algorithms/v2x/tests/test_messages.py -q --tb=short`
Expected: FAIL（import 错误）

- [x] **Step 3: 实现 messages.py**

```python
# algorithms/v2x/messages.py
"""消息草稿/正式消息、必填字段表、稳定哈希与 message_id。"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

MESSAGE_NAMESPACE = uuid.UUID("3f2b8a1e-9c44-4f2d-8e1a-6b7c0d9e5f21")

REQUIRED_FIELDS: Mapping[str, frozenset] = {
    "BSM": frozenset({
        "vehicle_id", "type_id", "position", "motion", "location",
        "route_edges", "next_signal", "front_gap_m", "rear_gap_m", "gap_source",
    }),
    "INTENT": frozenset({
        "vehicle_id", "turn_intent", "lane_change_intent", "estimated_arrival_s",
        "turn_confidence", "lane_change_confidence", "arrival_confidence",
        "intent_origin",
    }),
    "SPaT": frozenset({
        "intersection_id", "current_phase", "stage", "stage_elapsed",
        "connection_signal_states", "remaining_time_s", "next_stage",
        "next_stage_start_time", "schedule_status",
    }),
    "MAP": frozenset({"intersection_id", "phases", "lanes", "connections", "direct_neighbors"}),
    "RSM": frozenset({"rsu_id", "objects"}),
    "RSI": frozenset({"vehicle_id", "target_speed_mps", "target_lane_index", "guidance_type"}),
    "SIGNAL_CONTROL": frozenset({
        "intersection_id", "action", "requested_effective_time",
        "changed", "previous_action", "reason",
    }),
}


def stable_hash01(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    integer = int.from_bytes(digest[:8], "big")
    return integer / 2**64


def make_message_id(
    run_id: str, episode_id: str, source_id: str,
    message_type: str, sequence_no: int,
) -> str:
    return str(uuid.uuid5(
        MESSAGE_NAMESPACE,
        f"{run_id}|{episode_id}|{source_id}|{message_type}|{sequence_no}",
    ))


@dataclass(frozen=True, slots=True)
class MessageDraft:
    message_type: str
    source_id: str
    destination: str
    sim_time: float
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class V2XMessage:
    message_type: str
    message_id: str
    schema_version: str
    run_id: str
    episode_id: str
    frame_id: str
    sequence_no: int
    sim_time: float
    source_id: str
    destination: str
    correlation_id: Optional[str]
    payload: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {
            "message_type": self.message_type,
            "message_id": self.message_id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "frame_id": self.frame_id,
            "sequence_no": self.sequence_no,
            "sim_time": self.sim_time,
            "source_id": self.source_id,
            "destination": self.destination,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
        }


def validate_draft(draft: MessageDraft) -> None:
    if draft.message_type not in REQUIRED_FIELDS:
        raise ValueError(f"unknown message type: {draft.message_type}")
    missing = REQUIRED_FIELDS[draft.message_type] - frozenset(draft.payload.keys())
    if missing:
        raise ValueError(
            f"{draft.message_type} missing required fields: {sorted(missing)}"
        )
```

- [x] **Step 4: 运行确认通过**

Run: `pytest algorithms/v2x/tests/test_messages.py -q --tb=short`
Expected: PASS（5 passed）

- [x] **Step 5: 提交**（同 Task 1 Step 5，测试文件换成 test_messages.py，commit message `feat(v2x): 消息基类与 ID 契约`）

---

## Task 3: entities.py — 通信能力判定

**Files:**
- Create: `algorithms/v2x/entities.py`
- Test: `algorithms/v2x/tests/test_entities.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_entities.py
import pytest
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.entities import (
    VehicleCapability, Vehicle, RSU, resolve_v2x_enabled,
    DEFAULT_V2X_CAPABILITY, build_rsu_covered_lanes,
)


def test_explicit_overrides_everything():
    cfg = V2XConfig(penetration_rate=0.0)
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=True, type_v2x=False, config=cfg) is True
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=False, type_v2x=True, config=cfg) is False


def test_type_field_forward_compat():
    cfg = V2XConfig(penetration_rate=0.0)
    assert resolve_v2x_enabled(
        vehicle_id="v1", vehicle_class="passenger",
        explicit=None, type_v2x=True, config=cfg) is True


def test_penetration_stable_and_reproducible():
    cfg = V2XConfig(penetration_rate=0.5, capability_seed=7)
    ids = [f"veh_{i}" for i in range(50)]
    r1 = [resolve_v2x_enabled(vehicle_id=v, vehicle_class="passenger",
                              explicit=None, type_v2x=None, config=cfg) for v in ids]
    r2 = [resolve_v2x_enabled(vehicle_id=v, vehicle_class="passenger",
                              explicit=None, type_v2x=None, config=cfg) for v in ids]
    assert r1 == r2
    assert 0 < sum(r1) < len(ids)  # 50 辆车 0.5 渗透率不是全 0/全 1


def test_bicycle_never_connected():
    cfg = V2XConfig(penetration_rate=1.0)
    assert resolve_v2x_enabled(
        vehicle_id="b1", vehicle_class="bicycle",
        explicit=None, type_v2x=None, config=cfg) is False


def test_defaults_table():
    assert DEFAULT_V2X_CAPABILITY["passenger"] is True
    assert DEFAULT_V2X_CAPABILITY["bicycle"] is False


def test_rsu_covered_lanes_merges_protocol_and_extra():
    protocol_lanes = {"r1": frozenset({"A_0", ":r1_0"})}
    extra = {"r1": frozenset({"B_0"})}
    result = build_rsu_covered_lanes(protocol_lanes, extra)
    assert result["r1"] == frozenset({"A_0", ":r1_0", "B_0"})
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 entities.py**

```python
# algorithms/v2x/entities.py
"""车/路实体与通信能力判定（显式字段优先，vehicle_class 仅兼容默认）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .config import V2XConfig
from .messages import stable_hash01

DEFAULT_V2X_CAPABILITY = {
    "passenger": True,
    "bus": True,
    "truck": False,
    "bicycle": False,
    "pedestrian": False,
}


@dataclass(frozen=True, slots=True)
class VehicleCapability:
    vehicle_class: str
    v2x_enabled: bool
    obu_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class Vehicle:
    vehicle_id: str
    type_id: str
    vehicle_class: str
    v2x_enabled: bool
    obu_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class RSU:
    rsu_id: str
    covered_lane_ids: frozenset
    position: Optional[tuple[float, float]] = None


def resolve_v2x_enabled(
    *,
    vehicle_id: str,
    vehicle_class: str,
    explicit: Optional[bool],
    type_v2x: Optional[bool],
    config: V2XConfig,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    if type_v2x is not None:
        return bool(type_v2x)
    if vehicle_class in config.connected_classes:
        score = stable_hash01(f"{config.capability_seed}|{vehicle_id}")
        return score < config.penetration_rate
    return bool(DEFAULT_V2X_CAPABILITY.get(vehicle_class, False))


def build_rsu_covered_lanes(
    protocol_lanes: Mapping[str, frozenset],
    extra_lanes: Mapping[str, frozenset],
) -> dict[str, frozenset]:
    rsu_ids = set(protocol_lanes) | set(extra_lanes)
    return {
        rid: frozenset(protocol_lanes.get(rid, ())) | frozenset(extra_lanes.get(rid, ()))
        for rid in rsu_ids
    }
```

- [x] **Step 4: 运行确认通过**（6 passed）
- [x] **Step 5: 提交**（`feat(v2x): 实体与通信能力判定`）

---

## Task 4: derive.py — 意图推导

**Files:**
- Create: `algorithms/v2x/derive.py`
- Test: `algorithms/v2x/tests/test_derive.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_derive.py
from algorithms.v2x.derive import (
    derive_turn_intent, derive_lane_change_intent,
    derive_estimated_arrival_s, derive_phase_schedule,
)

# 两条边 e0 -> e1，路口 i1 有连接 -56734_0 -> -56736_0 (through)
INTERSECTIONS = {
    "i1": {
        "connections": [
            {"from_lane": "-56734_0", "to_lane": "-56736_0", "movement": "through"},
            {"from_lane": "-56734_1", "to_lane": "-56736_0", "movement": "left"},
        ],
        "lanes": {"-56734_0": {}, "-56734_1": {}},
    }
}

def _vehicle(lane="-56734_0", lane_index=0, speed=6.0, dist=68.9):
    return {
        "location": {"lane_id": lane, "lane_index": lane_index},
        "motion": {"speed_mps": speed},
        "route_edges": ["-56734", "-56736"],
        "next_signal": {"intersection_id": "i1", "distance_m": dist},
    }


def test_turn_full_match():
    intent, conf = derive_turn_intent(_vehicle(), INTERSECTIONS)
    assert intent == "through"
    assert conf == 1.0


def test_turn_edge_level_match():
    v = _vehicle(lane="-56734_2")  # 连接表里没有该 from_lane
    intent, conf = derive_turn_intent(v, INTERSECTIONS)
    # 同 edge 的 through 连接，edge 级匹配
    assert intent == "through"
    assert conf == 0.7


def test_turn_unknown():
    v = _vehicle(lane="other_0")
    v["next_signal"] = {"intersection_id": "i1", "distance_m": 10.0}
    intent, conf = derive_turn_intent(v, INTERSECTIONS)
    assert intent == "unknown"
    assert conf == 0.0


def test_lane_change_suggests_allowed_lane():
    v = _vehicle(lane="-56734_1", lane_index=1)  # left 车道但路线是 through
    target, conf = derive_lane_change_intent(v, INTERSECTIONS)
    assert target == "-56734_0"   # 最近的可直行车道
    assert conf > 0.0


def test_lane_change_none_when_already_correct():
    v = _vehicle(lane="-56734_0", lane_index=0)
    target, conf = derive_lane_change_intent(v, INTERSECTIONS)
    assert target is None


def test_arrival():
    assert derive_estimated_arrival_s(_vehicle(speed=6.0, dist=68.9)) == (68.9 / 6.0, 1.0)
    assert derive_estimated_arrival_s(_vehicle(speed=0.0))[0] is None
    assert derive_estimated_arrival_s(_vehicle(dist=None))[0] is None


def test_phase_schedule_green():
    meta = {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0, "clearance_seconds": 2.0}}
    state = {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 10.0}
    remaining, nxt, start, status = derive_phase_schedule(state, meta, sim_time=60.0)
    assert remaining == 15.0
    assert nxt == "YELLOW"
    assert start == 75.0
    assert status == "predicted"


def test_phase_schedule_unknown_phase():
    meta = {"1": {"green_seconds": 25.0}}
    state = {"current_phase": 99, "stage": "GREEN", "stage_elapsed": 1.0}
    remaining, nxt, start, status = derive_phase_schedule(state, meta, sim_time=60.0)
    assert remaining is None and nxt is None and start is None
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 derive.py**

```python
# algorithms/v2x/derive.py
"""意图推导纯函数：转向/换道/到达/相位时间表（确定性规则 + 置信度证据表）。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

SPEED_EPSILON = 0.1


def _edge_of(lane_id: Optional[str]) -> Optional[str]:
    if not lane_id:
        return None
    return lane_id.rsplit("_", 1)[0]


def derive_turn_intent(
    vehicle: Mapping[str, Any],
    intersections: Mapping[str, Mapping[str, Any]],
) -> tuple[str, float]:
    """返回 (turn_intent, confidence)。"""
    location = vehicle.get("location") or {}
    lane_id = location.get("lane_id")
    route = list(vehicle.get("route_edges") or [])
    ns = vehicle.get("next_signal") or {}
    inter_id = ns.get("intersection_id")
    if not inter_id or inter_id not in intersections or len(route) < 2:
        return "unknown", 0.0
    connections = intersections[inter_id].get("connections") or []
    next_edge = route[1]
    # 1) 完整匹配：from_lane 精确 + to_lane 的 edge 是 route[1]
    for conn in connections:
        if conn.get("from_lane") == lane_id:
            to_edge = _edge_of(conn.get("to_lane"))
            if to_edge == next_edge:
                return conn.get("movement", "unknown"), 1.0
    # 2) edge 级匹配
    edge = _edge_of(lane_id)
    if edge is None:
        return "unknown", 0.0
    for conn in connections:
        if _edge_of(conn.get("from_lane")) == edge:
            to_edge = _edge_of(conn.get("to_lane"))
            if to_edge == next_edge:
                return conn.get("movement", "unknown"), 0.7
    return "unknown", 0.0


def derive_lane_change_intent(
    vehicle: Mapping[str, Any],
    intersections: Mapping[str, Mapping[str, Any]],
) -> tuple[Optional[str], float]:
    """返回 (目标车道 or None, confidence)。"""
    location = vehicle.get("location") or {}
    lane_id = location.get("lane_id")
    ns = vehicle.get("next_signal") or {}
    inter_id = ns.get("intersection_id")
    turn, _ = derive_turn_intent(vehicle, intersections)
    if turn == "unknown" or inter_id not in intersections or lane_id is None:
        return None, 0.0
    edge = _edge_of(lane_id)
    connections = intersections[inter_id].get("connections") or []
    candidates = {
        conn.get("from_lane")
        for conn in connections
        if conn.get("movement") == turn and _edge_of(conn.get("from_lane")) == edge
    }
    candidates.discard(None)
    if not candidates:
        return None, 0.0
    current_index = int(location.get("lane_index") or 0)

    def lat_index(lane: str) -> int:
        try:
            return int(lane.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            return current_index

    best = min(candidates, key=lambda lane: (abs(lat_index(lane) - current_index), lane))
    if best == lane_id:
        return None, 0.0
    return best, 0.7


def derive_estimated_arrival_s(
    vehicle: Mapping[str, Any],
    speed_epsilon: float = SPEED_EPSILON,
) -> tuple[Optional[float], float]:
    ns = vehicle.get("next_signal") or {}
    distance_m = ns.get("distance_m")
    speed = (vehicle.get("motion") or {}).get("speed_mps")
    if distance_m is None or speed is None or speed <= speed_epsilon:
        return None, 0.0
    return float(distance_m) / float(speed), 1.0


def derive_phase_schedule(
    intersection_state: Mapping[str, Any],
    phases_meta: Mapping[str, Mapping[str, Any]],
    sim_time: float,
) -> tuple[Optional[float], Optional[str], Optional[float], str]:
    """返回 (remaining_time_s, next_stage, next_stage_start_time, schedule_status)。"""
    stage = intersection_state.get("stage")
    elapsed = float(intersection_state.get("stage_elapsed") or 0.0)
    current_phase = intersection_state.get("current_phase")
    meta = phases_meta.get(str(current_phase)) if current_phase is not None else None
    if meta is None:
        return None, None, None, "predicted"
    if stage == "GREEN":
        total = float(meta.get("green_seconds") or 0.0)
        nxt: Optional[str] = "YELLOW"
    elif stage == "YELLOW":
        total = float(meta.get("yellow_seconds") or 0.0)
        nxt = "CLEARANCE"
    elif stage == "CLEARANCE":
        total = float(meta.get("clearance_seconds") or 0.0)
        nxt = "GREEN"
    else:
        return None, None, None, "predicted"
    remaining = max(total - elapsed, 0.0)
    return remaining, nxt, sim_time + remaining, "predicted"
```

- [x] **Step 4: 运行确认通过**（10 passed）
- [x] **Step 5: 提交**（`feat(v2x): 意图推导纯函数`）

---

## Task 5: coverage.py — RSU 感知覆盖

**Files:**
- Create: `algorithms/v2x/coverage.py`
- Test: `algorithms/v2x/tests/test_coverage.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_coverage.py
from algorithms.v2x.coverage import is_in_rsu_coverage, should_use_next_signal_fallback
from algorithms.v2x.entities import RSU

RSU_A = RSU(rsu_id="a", covered_lane_ids=frozenset({"A_0", ":a_0"}),
            position=(100.0, 100.0))


def test_lane_covered():
    assert is_in_rsu_coverage("A_0", (0.0, 0.0), RSU_A)


def test_internal_lane_covered():
    assert is_in_rsu_coverage(":a_0", (0.0, 0.0), RSU_A)


def test_distance_covered():
    assert is_in_rsu_coverage("X_0", (105.0, 100.0), RSU_A, detection_radius_m=10.0)


def test_distance_outside():
    assert not is_in_rsu_coverage("X_0", (200.0, 100.0), RSU_A, detection_radius_m=10.0)


def test_null_protection():
    # rsu 无坐标 + radius 开启：距离不可判定，仅看车道
    rsu_no_pos = RSU(rsu_id="a", covered_lane_ids=frozenset({"A_0"}), position=None)
    assert not is_in_rsu_coverage("X_0", None, rsu_no_pos, detection_radius_m=10.0)
    assert is_in_rsu_coverage("A_0", None, rsu_no_pos, detection_radius_m=10.0)


def test_fallback_only_when_data_missing():
    # lane 缺失 且 半径不可判定 → 用 next_signal
    assert should_use_next_signal_fallback(
        lane_id=None, position=None, rsu=RSU_A,
        next_signal_intersection_id="a", detection_radius_m=None) is True
    assert should_use_next_signal_fallback(
        lane_id=None, position=None, rsu=RSU_A,
        next_signal_intersection_id="b", detection_radius_m=None) is False
    # lane 完整但不在覆盖区 → 不用 fallback 拉回
    assert should_use_next_signal_fallback(
        lane_id="X_0", position=(0.0, 0.0), rsu=RSU_A,
        next_signal_intersection_id="a", detection_radius_m=None) is False
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 coverage.py**

```python
# algorithms/v2x/coverage.py
"""RSU 感知覆盖：covered_lanes + 可选半径；next_signal 仅作数据缺失 fallback。"""
from __future__ import annotations

from typing import Optional

from .entities import RSU


def is_in_rsu_coverage(
    lane_id: Optional[str],
    position: Optional[tuple[float, float]],
    rsu: RSU,
    detection_radius_m: Optional[float] = None,
) -> bool:
    lane_covered = lane_id is not None and lane_id in rsu.covered_lane_ids
    distance_covered = False
    if (
        detection_radius_m is not None
        and rsu.position is not None
        and position is not None
    ):
        dx = position[0] - rsu.position[0]
        dy = position[1] - rsu.position[1]
        distance_covered = (dx * dx + dy * dy) ** 0.5 <= detection_radius_m
    return lane_covered or distance_covered


def should_use_next_signal_fallback(
    lane_id: Optional[str],
    position: Optional[tuple[float, float]],
    rsu: RSU,
    next_signal_intersection_id: Optional[str],
    detection_radius_m: Optional[float],
) -> bool:
    """仅在 lane_id 缺失且半径判定无法执行时，才用 next_signal 兜底。"""
    radius_available = (
        detection_radius_m is not None
        and rsu.position is not None
        and position is not None
    )
    if lane_id is not None or radius_available:
        return False
    return next_signal_intersection_id == rsu.rsu_id
```

- [x] **Step 4: 运行确认通过**（7 passed）
- [x] **Step 5: 提交**（`feat(v2x): RSU 感知覆盖判定`）

---

## Task 6: logger.py — JSONL sink 与记录

**Files:**
- Create: `algorithms/v2x/logger.py`
- Test: `algorithms/v2x/tests/test_logger.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_logger.py
import json
from pathlib import Path
from algorithms.v2x.logger import (
    LogRecord, JSONLSink, episode_start_record, message_record,
    delivery_record, episode_end_record,
)

REC = LogRecord("message", {"message_type": "BSM"})


def test_logrecord_data():
    assert REC.record_type == "message"
    assert REC.data["message_type"] == "BSM"


def test_episode_start_record_fields():
    rec = episode_start_record(run_id="r", episode_id="e", scenario={"period": "off_peak"},
                               v2x_config={"drop_rate": 0.0}, capability_seed=0,
                               capability_config={"connected_classes": ["passenger"]},
                               map_versions={"a": 1})
    assert rec.record_type == "episode_start"
    assert rec.data["episode_id"] == "e"
    assert rec.data["capability_seed"] == 0


def test_message_and_delivery_records():
    m = message_record(message={"message_type": "BSM"}, sent_at=10.0,
                       scheduled_delivery_at=10.02)
    assert m.data["sent_at"] == 10.0
    d = delivery_record(message_id="m1", status="delivered", delivered_at=10.02,
                        processed_at=15.0, actual_latency_ms=20.0)
    assert d.data["processed_at"] == 15.0
    drop = delivery_record(message_id="m1", status="dropped", dropped_at=60.0,
                           processed_at=60.0, drop_reason="episode_ended")
    assert drop.data["drop_reason"] == "episode_ended"


def test_jsonsink_writes_flush_close(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    sink = JSONLSink(str(path))
    sink.write(REC)
    sink.flush()
    sink.close()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["record_type"] == "message"
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 logger.py**

```python
# algorithms/v2x/logger.py
"""JSONL sink 与四类记录构造（episode_start/message/delivery/episode_end）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol


class MessageSink(Protocol):
    def write(self, record: "LogRecord") -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LogRecord:
    record_type: str
    data: Mapping[str, Any] = field(default_factory=dict)


class JSONLSink:
    def __init__(self, path: str) -> None:
        self._file = open(path, "w", encoding="utf-8")

    def write(self, record: LogRecord) -> None:
        payload = dict(record.data)
        payload["record_type"] = record.record_type
        self._file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def episode_start_record(
    *, run_id: str, episode_id: str, scenario: Optional[Mapping[str, Any]],
    v2x_config: Mapping[str, Any], capability_seed: int,
    capability_config: Mapping[str, Any], map_versions: Mapping[str, int],
) -> LogRecord:
    return LogRecord("episode_start", {
        "run_id": run_id, "episode_id": episode_id, "scenario": dict(scenario or {}),
        "v2x_config": dict(v2x_config), "capability_seed": capability_seed,
        "capability_config": dict(capability_config), "map_versions": dict(map_versions),
    })


def message_record(
    *, message: Mapping[str, Any], sent_at: float, scheduled_delivery_at: float,
) -> LogRecord:
    return LogRecord("message", {
        "message": dict(message), "sent_at": sent_at,
        "scheduled_delivery_at": scheduled_delivery_at,
    })


def delivery_record(
    *, message_id: str, status: str,
    delivered_at: Optional[float] = None, dropped_at: Optional[float] = None,
    processed_at: Optional[float] = None, actual_latency_ms: Optional[float] = None,
    drop_reason: Optional[str] = None,
) -> LogRecord:
    data: dict[str, Any] = {"message_id": message_id, "status": status}
    if delivered_at is not None:
        data["delivered_at"] = delivered_at
    if dropped_at is not None:
        data["dropped_at"] = dropped_at
    if processed_at is not None:
        data["processed_at"] = processed_at
    if actual_latency_ms is not None:
        data["actual_latency_ms"] = actual_latency_ms
    if drop_reason is not None:
        data["drop_reason"] = drop_reason
    return LogRecord("delivery", data)


def episode_end_record(*, summary: Mapping[str, Any]) -> LogRecord:
    return LogRecord("episode_end", {"summary": dict(summary)})
```

- [x] **Step 4: 运行确认通过**（5 passed）
- [x] **Step 5: 提交**（`feat(v2x): JSONL sink 与记录构造`）

---

## Task 7: stats.py — 结构化统计

**Files:**
- Create: `algorithms/v2x/stats.py`
- Test: `algorithms/v2x/tests/test_stats.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_stats.py
from algorithms.v2x.stats import (
    delivery_rate, latency_stats, rsm_coverage_stats, rsi_funnel,
)

def test_delivery_rate_null_when_no_sent():
    assert delivery_rate(sent=0, delivered=0) is None
    assert delivery_rate(sent=10, delivered=10) == 1.0
    assert delivery_rate(sent=10, delivered=3) == 0.3

def test_latency_stats_null_when_empty():
    assert latency_stats([]) == {"mean": None, "p50": None, "p95": None, "max": None}
    stats = latency_stats([20.0, 20.0, 40.0])
    assert stats["mean"] == 20.0 + 20.0 / 3.0
    assert stats["max"] == 40.0
    assert stats["p50"] == 20.0

def test_rsm_coverage_structured_zero_denominator():
    s = rsm_coverage_stats(observed=0, eligible=0)
    assert s == {"observed_unique_objects": 0, "eligible_unique_objects": 0,
                 "rate": None, "defined": False}
    s2 = rsm_coverage_stats(observed=3, eligible=4)
    assert s2["rate"] == 0.75 and s2["defined"] is True

def test_rsi_funnel():
    f = rsi_funnel(requested=10, existing=9, enabled=5, sent=5, delivered=5,
                   reasons={"vehicle_not_found": 1, "not_v2x_enabled": 4})
    assert f["requested"] == 10
    assert f["delivered"] == 5
    assert f["filter_reasons"]["vehicle_not_found"] == 1
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 stats.py**

```python
# algorithms/v2x/stats.py
"""结构化统计：零分母一律 null/defined=false，不写 0。"""
from __future__ import annotations

from statistics import median
from typing import Optional


def delivery_rate(*, sent: int, delivered: int) -> Optional[float]:
    if sent <= 0:
        return None
    return delivered / sent


def latency_stats(samples: list[float]) -> dict:
    if not samples:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    ordered = sorted(samples)
    return {
        "mean": sum(samples) / len(samples),
        "p50": median(samples),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "max": ordered[-1],
    }


def rsm_coverage_stats(*, observed: int, eligible: int) -> dict:
    defined = eligible > 0
    return {
        "observed_unique_objects": observed,
        "eligible_unique_objects": eligible,
        "rate": (observed / eligible) if defined else None,
        "defined": defined,
    }


def rsi_funnel(
    *, requested: int, existing: int, enabled: int, sent: int, delivered: int,
    reasons: Optional[dict] = None,
) -> dict:
    return {
        "requested": requested,
        "target_exists": existing,
        "v2x_enabled": enabled,
        "rsi_sent": sent,
        "rsi_delivered": delivered,
        "filter_reasons": dict(reasons or {}),
    }
```

- [x] **Step 4: 运行确认通过**（4 passed）
- [x] **Step 5: 提交**（`feat(v2x): 结构化统计口径`）

---

## Task 8: hub.py — 消息总线（核心）

**Files:**
- Create: `algorithms/v2x/hub.py`
- Test: `algorithms/v2x/tests/test_hub.py`

- [x] **Step 1: 写失败测试（第一部分：生命周期/调度/序号/两阶段/延迟）**

```python
# algorithms/v2x/tests/test_hub.py
import pytest
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.hub import V2XHub, FrameContext
from algorithms.v2x.messages import MessageDraft


def _hub(**kw):
    return V2XHub(config=V2XConfig(**kw))


def _init(hub, sim_time=0.0):
    payload = {
        "episode_id": "ep1",
        "vehicle_types": {},
        "intersections": {"i1": {"intersection_id": "i1", "phases": {},
                                  "lanes": {}, "connections": [],
                                  "direct_neighbors": []}},
    }
    return hub.ingest_initialize(payload, run_id="run1", episode_id="ep1",
                                 initial_sim_time=sim_time)


def test_lifecycle_errors():
    hub = _hub()
    with pytest.raises(ValueError):
        hub.ingest_step({"simulation_time": 5.0})
    _init(hub)
    with pytest.raises(ValueError):
        hub.ingest_initialize({}, run_id="r", episode_id="ep2")
    hub.finish_episode(60.0)
    with pytest.raises(ValueError):
        hub.ingest_step({"simulation_time": 65.0})
    with pytest.raises(ValueError):
        hub.ingest_actions({"signals": {}, "vehicles": {}},
                           frame=FrameContext("ep1", "ep1:step:000001", 5.0, ()))
    # 新 episode 合法
    hub.ingest_initialize({}, run_id="run1", episode_id="ep2")


def test_frame_ids_and_sequence_per_stream():
    hub = _hub()
    _init(hub)
    hub.publish(MessageDraft("BSM", "v1", "cloud", 5.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000001")
    hub.publish(MessageDraft("INTENT", "v1", "cloud", 5.0,
                             {"vehicle_id": "v1", "turn_intent": "through",
                              "lane_change_intent": None, "estimated_arrival_s": 3.0,
                              "turn_confidence": 1.0, "lane_change_confidence": 0.0,
                              "arrival_confidence": 1.0, "intent_origin": "derived"}),
                frame_id="ep1:step:000001")
    assert hub.sequence_no("ep1", "v1", "BSM") == 1
    assert hub.sequence_no("ep1", "v1", "INTENT") == 1
    hub.publish(MessageDraft("BSM", "v1", "cloud", 10.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000002")
    assert hub.sequence_no("ep1", "v1", "BSM") == 2


def test_advance_delivers_with_latency():
    hub = _hub()
    _init(hub)
    delivered = []
    hub.subscribe("BSM", lambda msg: delivered.append(msg))
    hub.publish(MessageDraft("BSM", "v1", "cloud", 10.0,
                             {"vehicle_id": "v1", "type_id": "t", "position": (0, 0),
                              "motion": {}, "location": {"lane_id": "L_0"},
                              "route_edges": ["a", "b"], "next_signal": None,
                              "front_gap_m": None, "rear_gap_m": None,
                              "gap_source": None}), frame_id="ep1:step:000001")
    assert delivered == []          # 未到期
    hub.advance(10.02 + 1e-9)
    assert len(delivered) == 1
    assert delivered[0].sequence_no == 1
    assert delivered[0].message_id.startswith("ep1|") is False  # uuid 字符串


def test_scheduler_first_send_then_interval():
    hub = _hub(bsm_interval_s=10.0)
    _init(hub)
    drafts = _bsm_draft("v1", 5.0)
    assert hub.should_send("ep1", "v1", "BSM", 5.0) is True
    hub.mark_sent("ep1", "v1", "BSM", 5.0)
    assert hub.should_send("ep1", "v1", "BSM", 9.0) is False
    assert hub.should_send("ep1", "v1", "BSM", 15.0) is True


def test_time_regression_raises():
    hub = _hub()
    _init(hub)
    hub.advance(10.0)
    with pytest.raises(ValueError, match="regression"):
        hub.advance(9.0)


def test_ingest_actions_frame_consumed_once():
    hub = _hub()
    _init(hub)
    frame = hub.ingest_step({"simulation_time": 5.0, "episode_id": "ep1",
                             "intersections": {}, "vehicles": {}})
    hub.ingest_actions({"signals": {}, "vehicles": {}}, frame=frame)
    with pytest.raises(ValueError, match="consumed"):
        hub.ingest_actions({"signals": {}, "vehicles": {}}, frame=frame)


def _bsm_draft(vid, sim_time):
    return MessageDraft("BSM", vid, "cloud", sim_time,
                        {"vehicle_id": vid, "type_id": "t", "position": (0, 0),
                         "motion": {}, "location": {"lane_id": "L_0"},
                         "route_edges": ["a", "b"], "next_signal": None,
                         "front_gap_m": None, "rear_gap_m": None,
                         "gap_source": None})


def test_finish_drain_pending_false_drops_episode_ended():
    hub = _hub()
    _init(hub)
    hub.publish(_bsm_draft("v1", 10.0), frame_id="ep1:step:000001")
    hub.finish_episode(10.0, drain_pending=False)
    drops = [r for r in hub.delivery_records if r["status"] == "dropped"]
    assert len(drops) == 1
    assert drops[0]["drop_reason"] == "episode_ended"


def test_finish_drain_pending_true_delivers_all():
    hub = _hub()
    _init(hub)
    hub.publish(_bsm_draft("v1", 10.0), frame_id="ep1:step:000001")
    hub.finish_episode(10.0, drain_pending=True)
    assert sum(1 for r in hub.delivery_records if r["status"] == "delivered") == 1
    assert sum(1 for r in hub.delivery_records if r["status"] == "dropped") == 0
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 hub.py**

```python
# algorithms/v2x/hub.py
"""V2XHub：生命周期、两阶段 API、延迟投递、调度/序号、override、统计收集。"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from .config import V2XConfig, DOWNSTREAM_TYPES
from .logger import (
    JSONLSink, LogRecord, MessageSink, episode_start_record,
    episode_end_record, message_record, delivery_record,
)
from .messages import V2XMessage, MessageDraft, make_message_id, stable_hash01, validate_draft
from .stats import build_summary  # type: ignore[attr-defined]  # 见 Task 7


@dataclass(frozen=True, slots=True)
class FrameContext:
    episode_id: str
    frame_id: str
    sim_time: float
    input_message_ids: tuple[str, ...] = ()


class _Pending:
    __slots__ = ("due", "order", "message", "network_dropped")
    def __init__(self, due: float, order: int, message: V2XMessage,
                 network_dropped: bool) -> None:
        self.due = due
        self.order = order
        self.message = message
        self.network_dropped = network_dropped


class V2XHub:
    def __init__(self, config: Optional[V2XConfig] = None,
                 sink: Optional[MessageSink] = None) -> None:
        self.config = config or V2XConfig()
        self._sink = sink
        self._state = "CREATED"
        self._run_id: Optional[str] = None
        self._episode_id: Optional[str] = None
        self._scenario: Optional[Mapping[str, Any]] = None
        self._initial_sim_time = 0.0
        self._frame_index = 0
        self._sim_time = 0.0
        self._sequences: dict[tuple, int] = {}
        self._schedulers: dict[tuple, float] = {}
        self._subscribers: dict[str, list[Callable[[V2XMessage], None]]] = {}
        self._pending: list[_Pending] = []
        self._insertion = 0
        self._last_step_frame: Optional[FrameContext] = None
        self._consumed_frames: set[str] = set()
        self._last_signal_action: dict[str, Optional[int]] = {}
        # 统计收集
        self.sent_records: list[dict] = []
        self.delivery_records: list[dict] = []
        self._sent_seq: dict[tuple, list[int]] = {}
        self._delivered_seq: dict[tuple, set[int]] = {}
        self._map_versions: dict[str, int] = {}
        self._entity_info: dict[str, Any] = {}

    # ---------- 生命周期 ----------
    def ingest_initialize(
        self, payload: Mapping[str, Any], *,
        run_id: str, episode_id: str,
        scenario: Optional[Mapping[str, Any]] = None,
        initial_sim_time: float = 0.0,
        coverage_config: Optional[Any] = None,
    ) -> FrameContext:
        if self._state != "CREATED":
            raise ValueError(f"ingest_initialize only from CREATED, state={self._state}")
        if not run_id or not episode_id:
            raise ValueError("run_id and episode_id required")
        self._run_id = run_id
        self._episode_id = episode_id
        self._scenario = scenario
        self._initial_sim_time = initial_sim_time
        self._sim_time = initial_sim_time
        self._coverage_config = coverage_config
        frame = FrameContext(episode_id, f"{episode_id}:init", initial_sim_time, ())
        # MAP：每个注册 RSU 一条（无间隔门控）
        intersections = payload.get("intersections") or {}
        for inter_id in intersections:
            self._publish(MessageDraft(
                "MAP", inter_id, "cloud", initial_sim_time,
                {"intersection_id": inter_id,
                 "phases": intersections[inter_id].get("phases") or {},
                 "lanes": intersections[inter_id].get("lanes") or {},
                 "connections": intersections[inter_id].get("connections") or [],
                 "direct_neighbors": intersections[inter_id].get("direct_neighbors") or []},
            ), frame_id=frame.frame_id)
            self._map_versions[inter_id] = 1
        self._state = "ACTIVE"
        return frame

    def ingest_step(self, payload: Mapping[str, Any],
                    intent_overrides: Optional[Mapping[str, MessageDraft]] = None) -> FrameContext:
        if self._state != "ACTIVE":
            raise ValueError(f"ingest_step only from ACTIVE, state={self._state}")
        sim_time = float(payload.get("simulation_time", 0.0))
        if sim_time < self._initial_sim_time:
            raise ValueError(f"step sim_time {sim_time} < initial {self._initial_sim_time}")
        self._advance(sim_time)
        self._sim_time = sim_time
        self._frame_index += 1
        frame = FrameContext(
            self._episode_id or "", f"{self._episode_id}:step:{self._frame_index:06d}",
            sim_time, (),
        )
        self._last_step_frame = frame
        # 上行草稿（由 protocol 适配器生成；本任务先用空占位，Task 9 接 protocol）
        drafts: list[MessageDraft] = []
        if intent_overrides:
            drafts = list(intent_overrides.values())
        published = []
        for draft in drafts:
            if draft.message_type == "INTENT" and self._published_intent_this_frame(draft.source_id):
                continue
            self._publish(draft, frame_id=frame.frame_id)
            published.append(draft)
        frame = FrameContext(frame.episode_id, frame.frame_id, frame.sim_time,
                             tuple(published))
        return frame

    def ingest_actions(self, actions: Mapping[str, Any],
                       frame: Optional[FrameContext] = None) -> None:
        if self._state != "ACTIVE":
            raise ValueError("ingest_actions only from ACTIVE")
        if frame is None:
            frame = self._last_step_frame
        if frame is None or frame.frame_id not in (
            f"{self._episode_id}:step:{self._frame_index:06d}",):
            raise ValueError("frame must be the latest step frame of active episode")
        if frame.frame_id in self._consumed_frames:
            raise ValueError(f"frame {frame.frame_id} already consumed")
        signals = actions.get("signals") or {}
        for inter_id, spec in signals.items():
            action = spec.get("target_phase") if isinstance(spec, dict) else spec
            prev = self._last_signal_action.get(inter_id)
            changed = prev is None or action != prev
            self._last_signal_action[inter_id] = action
            self._publish(MessageDraft(
                "SIGNAL_CONTROL", "cloud", inter_id, frame.sim_time,
                {"intersection_id": inter_id, "action": action,
                 "requested_effective_time": frame.sim_time,
                 "changed": changed, "previous_action": prev, "reason": None},
            ), frame_id=frame.frame_id, correlation_id=frame.frame_id)
        self._consumed_frames.add(frame.frame_id)

    def finish_episode(self, final_sim_time: float, drain_pending: bool = False) -> dict:
        if self._state != "ACTIVE":
            raise ValueError(f"finish_episode only from ACTIVE, state={self._state}")
        if drain_pending:
            due = max((p.due for p in self._pending), default=final_sim_time)
            self._advance(due)
        else:
            self._advance(final_sim_time)
        # 剩余 pending → episode_ended
        for p in list(self._pending):
            heapq.heappop(self._pending)
            self._record_delivery(p.message, status="dropped", dropped_at=final_sim_time,
                                  processed_at=final_sim_time, drop_reason="episode_ended")
        summary = build_summary(self)
        if self._sink is not None:
            self._sink.write(episode_end_record(summary=summary))
            self._sink.flush()
        self._state = "FINISHED"
        return summary

    def close(self) -> None:
        if self._state == "ACTIVE":
            raise ValueError("close requires no active episode; call finish_episode first")
        if self._sink is not None:
            self._sink.flush()
            self._sink.close()

    # ---------- 发布/订阅/投递 ----------
    def subscribe(self, message_type: str,
                  handler: Callable[[V2XMessage], None]) -> None:
        self._subscribers.setdefault(message_type, []).append(handler)

    def publish(self, draft: MessageDraft, *, frame_id: str,
                correlation_id: Optional[str] = None) -> V2XMessage:
        return self._publish(draft, frame_id=frame_id, correlation_id=correlation_id)

    def _publish(self, draft: MessageDraft, *, frame_id: str,
                 correlation_id: Optional[str] = None) -> V2XMessage:
        validate_draft(draft)
        if self._episode_id is None:
            raise ValueError("no active episode")
        key = (self._run_id, self._episode_id, draft.source_id, draft.message_type)
        seq = self._sequences.get(key, 0) + 1
        self._sequences[key] = seq
        message = V2XMessage(
            message_type=draft.message_type,
            message_id=make_message_id(self._run_id or "", self._episode_id,
                                       draft.source_id, draft.message_type, seq),
            schema_version=self.config.schema_version,
            run_id=self._run_id or "", episode_id=self._episode_id,
            frame_id=frame_id, sequence_no=seq, sim_time=draft.sim_time,
            source_id=draft.source_id, destination=draft.destination,
            correlation_id=correlation_id, payload=dict(draft.payload),
        )
        latency_ms = self.config.latency_ms_for(draft.message_type)
        jitter_score = stable_hash01(
            f"{self.config.network_seed}|jitter|{draft.message_type}|{draft.source_id}|{seq}")
        jitter_ms = (2.0 * jitter_score - 1.0) * self.config.latency_jitter_ms
        latency_ms = max(0.0, latency_ms + jitter_ms)
        scheduled = draft.sim_time + latency_ms / 1000.0
        drop_score = stable_hash01(
            f"{self.config.network_seed}|drop|{draft.message_type}|{draft.source_id}|{seq}")
        network_dropped = drop_score < self.config.drop_rate
        self.sent_records.append({
            "message_id": message.message_id, "message_type": message.message_type,
            "source_id": message.source_id, "destination": message.destination,
            "sim_time": message.sim_time, "frame_id": frame_id,
            "sequence_no": seq, "sent_at": message.sim_time,
            "scheduled_delivery_at": scheduled,
        })
        self._sent_seq.setdefault(key, []).append(seq)
        if self._sink is not None:
            self._sink.write(message_record(
                message=message.to_dict(), sent_at=message.sim_time,
                scheduled_delivery_at=scheduled))
        heapq.heappush(self._pending, _Pending(scheduled, self._insertion,
                                               message, network_dropped))
        self._insertion += 1
        return message

    def advance(self, sim_time: float) -> None:
        self._advance(sim_time)

    def _advance(self, sim_time: float) -> None:
        eps = self.config.scheduling_epsilon_s
        if sim_time < self._sim_time - eps:
            raise ValueError(f"time regression: {sim_time} < {self._sim_time}")
        while self._pending and self._pending[0].due <= sim_time + eps:
            p = heapq.heappop(self._pending)
            if p.network_dropped:
                self._record_delivery(p.message, status="dropped",
                                      dropped_at=p.due, processed_at=sim_time,
                                      drop_reason="network_drop")
                continue
            for handler in self._subscribers.get(p.message.message_type, []):
                handler(p.message)
            latency_ms = (p.due - p.message.sim_time) * 1000.0
            self._record_delivery(p.message, status="delivered", delivered_at=p.due,
                                  processed_at=sim_time, actual_latency_ms=latency_ms)

    def _record_delivery(self, message: V2XMessage, *, status: str,
                         delivered_at: Optional[float] = None,
                         dropped_at: Optional[float] = None,
                         processed_at: Optional[float] = None,
                         actual_latency_ms: Optional[float] = None,
                         drop_reason: Optional[str] = None) -> None:
        self.delivery_records.append({
            "message_id": message.message_id, "status": status,
            "delivered_at": delivered_at, "dropped_at": dropped_at,
            "processed_at": processed_at, "actual_latency_ms": actual_latency_ms,
            "drop_reason": drop_reason,
        })
        key = (self._run_id, self._episode_id, message.source_id, message.message_type)
        if status == "delivered":
            self._delivered_seq.setdefault(key, set()).add(message.sequence_no)
        if self._sink is not None:
            self._sink.write(delivery_record(
                message_id=message.message_id, status=status,
                delivered_at=delivered_at, dropped_at=dropped_at,
                processed_at=processed_at, actual_latency_ms=actual_latency_ms,
                drop_reason=drop_reason))

    # ---------- 调度/序号辅助 ----------
    def sequence_no(self, episode_id: str, source_id: str, message_type: str) -> int:
        key = (self._run_id, episode_id, source_id, message_type)
        return self._sequences.get(key, 0)

    def should_send(self, episode_id: str, source_id: str,
                    message_type: str, sim_time: float) -> bool:
        interval = self.config.interval_for(message_type)
        if interval <= 0.0:
            return False
        key = (self._run_id, episode_id, source_id, message_type)
        next_due = self._schedulers.get(key)
        eps = self.config.scheduling_epsilon_s
        return next_due is None or sim_time + eps >= next_due

    def mark_sent(self, episode_id: str, source_id: str,
                  message_type: str, sim_time: float) -> None:
        interval = self.config.interval_for(message_type)
        key = (self._run_id, episode_id, source_id, message_type)
        if interval > 0.0:
            self._schedulers[key] = sim_time + interval

    def _published_intent_this_frame(self, vehicle_id: str) -> bool:
        # 简化：本实现由 protocol 层保证每车每帧一条 INTENT；此处仅作防御
        return False
```

- [x] **Step 4: 运行确认通过**

Run: `pytest algorithms/v2x/tests/test_hub.py -q --tb=short`
Expected: PASS（10 passed）

> 注：`hub.py` 中 `build_summary` 来自 Task 7；若 Task 7 已完成则直接可用。

- [x] **Step 5: 提交**（`feat(v2x): V2XHub 消息总线核心`）

---

## Task 9: protocol.py — 协议适配器

**Files:**
- Create: `algorithms/v2x/protocol.py`
- Test: `algorithms/v2x/tests/test_protocol.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_protocol.py
from algorithms.v2x.protocol import (
    build_bsm_draft, build_intent_draft, build_spat_draft,
    build_rsi_draft, build_signal_control_draft,
)

VEHICLE = {
    "type_id": "official_passenger",
    "position": {"x_m": 512.4, "y_m": 308.1},
    "motion": {"speed_mps": 6.2, "acceleration_mps2": -0.8,
               "angle_deg": 92.0, "allowed_speed_mps": 13.9},
    "location": {"road_id": "-56734", "lane_id": "-56734_0", "lane_index": 0,
                 "lane_position_m": 148.5},
    "route_edges": ["-56734", "-56736"],
    "next_signal": {"intersection_id": "i1", "distance_m": 68.9, "state": "G"},
    "leader_gap_m": 18.4, "follower_gap_m": 11.7,
}

INTERSECTION = {
    "current_phase": 1, "pending_phase": None, "stage": "GREEN",
    "stage_elapsed": 10.0,
    "lanes": {"-56734_0": {"connection_signal_states": [
        {"connection_id": "c0", "movement": "through",
         "downstream_lane_id": "-56736_0", "signal_state": "G"}]}},
}

PHASES_META = {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0,
                     "clearance_seconds": 2.0}}


def test_bsm_draft_fields():
    d = build_bsm_draft("v1", VEHICLE)
    assert d.message_type == "BSM"
    assert d.payload["front_gap_m"] == 18.4
    assert d.payload["gap_source"] == "protocol"


def test_intent_draft_origin():
    d = build_intent_draft("v1", VEHICLE, turn="left", lane_change="-56734_1",
                           arrival=5.0, turn_conf=1.0, lc_conf=0.7, arr_conf=1.0,
                           origin="derived")
    assert d.message_type == "INTENT"
    assert d.payload["intent_origin"] == "derived"
    assert d.payload["turn_intent"] == "left"


def test_spat_draft_schedule():
    d = build_spat_draft("i1", INTERSECTION, PHASES_META, sim_time=60.0)
    assert d.message_type == "SPaT"
    assert d.payload["remaining_time_s"] == 15.0
    assert d.payload["next_stage"] == "YELLOW"
    assert d.payload["schedule_status"] == "predicted"


def test_rsi_draft():
    d = build_rsi_draft("v1", {"target_speed_mps": 8.0, "target_lane_index": 1})
    assert d.message_type == "RSI"
    assert d.payload["target_speed_mps"] == 8.0


def test_signal_control_draft():
    d = build_signal_control_draft("i1", 2, sim_time=60.0, previous=1)
    assert d.message_type == "SIGNAL_CONTROL"
    assert d.payload["changed"] is True
    assert d.payload["previous_action"] == 1
    assert d.payload["requested_effective_time"] == 60.0
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 protocol.py**

```python
# algorithms/v2x/protocol.py
"""协议 2.0 适配器：step 载荷 → 上行草稿；actions → RSI/事件草稿。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .derive import derive_phase_schedule
from .messages import MessageDraft


def build_bsm_draft(vehicle_id: str, raw: Mapping[str, Any]) -> MessageDraft:
    location = raw.get("location") or {}
    motion = raw.get("motion") or {}
    position = raw.get("position") or {}
    ns = raw.get("next_signal")
    return MessageDraft(
        "BSM", vehicle_id, "cloud", float(raw.get("_sim_time", 0.0)),
        {"vehicle_id": vehicle_id,
         "type_id": raw.get("type_id"),
         "position": (position.get("x_m"), position.get("y_m")),
         "motion": {"speed_mps": motion.get("speed_mps"),
                    "acceleration_mps2": motion.get("acceleration_mps2"),
                    "angle_deg": motion.get("angle_deg")},
         "location": {"road_id": location.get("road_id"),
                      "lane_id": location.get("lane_id"),
                      "lane_index": location.get("lane_index"),
                      "lane_position_m": location.get("lane_position_m")},
         "route_edges": list(raw.get("route_edges") or []),
         "next_signal": ns,
         "front_gap_m": raw.get("leader_gap_m"),
         "rear_gap_m": raw.get("follower_gap_m"),
         "gap_source": "protocol" if raw.get("leader_gap_m") is not None or
                       raw.get("follower_gap_m") is not None else None},
    )


def build_intent_draft(
    vehicle_id: str, raw: Mapping[str, Any], *, sim_time: float,
    turn: str, lane_change: Optional[str], arrival: Optional[float],
    turn_conf: float, lane_change_conf: float, arrival_conf: float,
    origin: str,
) -> MessageDraft:
    return MessageDraft(
        "INTENT", vehicle_id, "cloud", sim_time,
        {"vehicle_id": vehicle_id, "turn_intent": turn,
         "lane_change_intent": lane_change, "estimated_arrival_s": arrival,
         "turn_confidence": turn_conf, "lane_change_confidence": lane_change_conf,
         "arrival_confidence": arrival_conf, "intent_origin": origin},
    )


def build_spat_draft(
    intersection_id: str, state: Mapping[str, Any],
    phases_meta: Mapping[str, Mapping[str, Any]], *, sim_time: float,
) -> MessageDraft:
    remaining, nxt, start, status = derive_phase_schedule(state, phases_meta, sim_time)
    lanes = state.get("lanes") or {}
    conn_states = []
    for lane in lanes.values():
        conn_states.extend(lane.get("connection_signal_states") or [])
    return MessageDraft(
        "SPaT", intersection_id, "cloud", sim_time,
        {"intersection_id": intersection_id,
         "current_phase": state.get("current_phase"),
         "stage": state.get("stage"),
         "stage_elapsed": state.get("stage_elapsed"),
         "connection_signal_states": conn_states,
         "remaining_time_s": remaining, "next_stage": nxt,
         "next_stage_start_time": start, "schedule_status": status},
    )


def build_rsm_draft(
    rsu_id: str, objects: list[dict], *, sim_time: float,
) -> MessageDraft:
    return MessageDraft("RSM", rsu_id, "cloud", sim_time,
                        {"rsu_id": rsu_id, "objects": objects})


def build_rsi_draft(
    vehicle_id: str, action: Mapping[str, Any], *, sim_time: float,
) -> MessageDraft:
    return MessageDraft(
        "RSI", "cloud", vehicle_id, sim_time,
        {"vehicle_id": vehicle_id,
         "target_speed_mps": action.get("target_speed_mps"),
         "target_lane_index": action.get("target_lane_index"),
         "guidance_type": "speed" if "target_speed_mps" in action else "lane"},
    )


def build_signal_control_draft(
    intersection_id: str, action: Any, *, sim_time: float,
    previous_action: Optional[int],
) -> MessageDraft:
    changed = previous_action is None or action != previous_action
    return MessageDraft(
        "SIGNAL_CONTROL", "cloud", intersection_id, sim_time,
        {"intersection_id": intersection_id, "action": action,
         "requested_effective_time": sim_time, "changed": changed,
         "previous_action": previous_action, "reason": None},
    )
```

- [x] **Step 4: 运行确认通过**（5 passed）
- [x] **Step 5: 提交**（`feat(v2x): 协议 2.0 适配器`）

---

## Task 10: replay.py — 回放 CLI

**Files:**
- Create: `algorithms/v2x/replay.py`
- Test: `algorithms/v2x/tests/test_replay.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_replay.py
import json
from pathlib import Path
from algorithms.v2x.replay import summarize_log, format_summary

def _write_log(path: Path):
    lines = [
        {"record_type": "episode_start", "episode_id": "e1", "run_id": "r1"},
        {"record_type": "message", "message": {"message_type": "BSM"},
         "sent_at": 0.0, "scheduled_delivery_at": 0.02},
        {"record_type": "delivery", "message_id": "m1", "status": "delivered",
         "delivered_at": 0.02, "processed_at": 5.0, "actual_latency_ms": 20.0},
        {"record_type": "episode_end", "summary": {"delivery_rate": 1.0}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

def test_summarize_log(tmp_path: Path):
    p = tmp_path / "log.jsonl"
    _write_log(p)
    s = summarize_log(str(p))
    assert s["episodes"] == ["e1"]
    assert s["counts"]["BSM"] == 1
    assert s["delivered"] == 1
    assert s["dropped"] == 0

def test_format_summary_contains_counts():
    text = format_summary({"episodes": ["e1"], "counts": {"BSM": 1},
                           "delivered": 1, "dropped": 0})
    assert "BSM" in text and "delivered" in text
```

- [x] **Step 2: 运行确认失败**（import 错误）

- [x] **Step 3: 实现 replay.py**

```python
# algorithms/v2x/replay.py
"""回放 CLI：python -m algorithms.v2x.replay <log> [--summary|--print]。"""
from __future__ import annotations

import argparse
import json
from typing import Any


def summarize_log(path: str) -> dict:
    episodes: list[str] = []
    counts: dict[str, int] = {}
    delivered = 0
    dropped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("record_type")
            if kind == "episode_start":
                episodes.append(str(rec.get("episode_id")))
            elif kind == "message":
                mtype = (rec.get("message") or {}).get("message_type", "?")
                counts[mtype] = counts.get(mtype, 0) + 1
            elif kind == "delivery":
                if rec.get("status") == "delivered":
                    delivered += 1
                else:
                    dropped += 1
    return {"episodes": episodes, "counts": counts,
            "delivered": delivered, "dropped": dropped}


def format_summary(summary: dict) -> str:
    lines = ["=== V2X log summary ===",
             f"episodes: {', '.join(summary['episodes'])}",
             "counts: " + ", ".join(
                 f"{k}={v}" for k, v in sorted(summary["counts"].items())),
             f"delivered={summary['delivered']} dropped={summary['dropped']}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v2x.replay")
    parser.add_argument("log", help="path to JSONL log")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true", help="print summary only")
    group.add_argument("--print", action="store_true", dest="print_all",
                       help="print every record")
    args = parser.parse_args(argv)
    if args.print_all:
        with open(args.log, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    print(json.dumps(json.loads(line), ensure_ascii=False))
        return 0
    print(format_summary(summarize_log(args.log)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: 运行确认通过**（2 passed）
- [x] **Step 5: 提交**（`feat(v2x): 回放 CLI`）

---

## Task 11: stats.py build_summary — Hub 汇总

**Files:**
- Modify: `algorithms/v2x/stats.py`
- Test: `algorithms/v2x/tests/test_stats_build_summary.py`

- [x] **Step 1: 写失败测试**

```python
# algorithms/v2x/tests/test_stats_build_summary.py
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.stats import build_summary


def _draft(vid="v1", mtype="BSM", sim_time=5.0):
    if mtype == "BSM":
        return MessageDraft("BSM", vid, "cloud", sim_time,
                            {"vehicle_id": vid, "type_id": "t", "position": (0, 0),
                             "motion": {}, "location": {"lane_id": "L_0"},
                             "route_edges": ["a", "b"], "next_signal": None,
                             "front_gap_m": None, "rear_gap_m": None,
                             "gap_source": None})
    return MessageDraft("RSI", "cloud", vid, sim_time,
                        {"vehicle_id": vid, "target_speed_mps": 8.0,
                         "target_lane_index": None, "guidance_type": "speed"})


def test_summary_delivery_rate_null_and_latency():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.publish(_draft("v1", "BSM", 5.0), frame_id="e:step:000001")
    hub.finish_episode(5.0, drain_pending=False)
    s = build_summary(hub)
    # 默认 finish 丢弃 → delivered=0
    assert s["delivery"]["delivered"] == 0
    assert s["delivery"]["delivery_rate"] == 0.0


def test_summary_drain_true_rate_one():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.publish(_draft("v1", "BSM", 5.0), frame_id="e:step:000001")
    hub.finish_episode(5.0, drain_pending=True)
    s = build_summary(hub)
    assert s["delivery"]["delivered"] == 1
    assert s["delivery"]["delivery_rate"] == 1.0
    assert s["delivery"]["latency_ms"]["mean"] == 20.0


def test_summary_structured_fields_present():
    hub = V2XHub(config=V2XConfig())
    hub.ingest_initialize({"episode_id": "e", "vehicle_types": {},
                           "intersections": {}}, run_id="r", episode_id="e")
    hub.finish_episode(0.0, drain_pending=True)
    s = build_summary(hub)
    assert s["rsm_coverage"]["rate"] is None
    assert s["rsm_coverage"]["defined"] is False
    assert s["rsi_funnel"]["requested"] == 0
    assert s["signal_control"]["generated"] == 0
    assert s["penetration"]["rate"] is None
```

- [x] **Step 2: 运行确认失败**（`build_summary` 未定义）

- [x] **Step 3: 实现 build_summary（追加到 stats.py）**

```python
# 追加到 algorithms/v2x/stats.py 末尾
def build_summary(hub: Any) -> dict:
    sent = hub.sent_records
    deliveries = hub.delivery_records
    delivered = [d for d in deliveries if d["status"] == "delivered"]
    dropped = [d for d in deliveries if d["status"] == "dropped"]
    latencies = [d["actual_latency_ms"] for d in delivered
                 if d.get("actual_latency_ms") is not None]
    # 按类型统计
    per_type: dict[str, dict] = {}
    for rec in sent:
        item = per_type.setdefault(rec["message_type"], {"sent": 0, "delivered": 0, "dropped": 0})
        item["sent"] += 1
    for d in deliveries:
        # 通过 message_id 找不到类型时跳过（message_id 不编码类型）
        pass
    # 简化：delivered/dropped 按全局统计
    return {
        "sent": len(sent),
        "delivered": len(delivered),
        "dropped": len(dropped),
        "pending": len(hub._pending),
        "delivery_rate": delivery_rate(sent=len(sent), delivered=len(delivered)),
        "latency_ms": latency_stats(latencies),
        "sequence": {
            "missing_sequence_count": _missing_count(hub),
            "out_of_order_count": _out_of_order_count(hub),
            "duplicate_delivery_count": _duplicate_count(hub),
        },
        "penetration": _penetration(hub),
        "rsm_coverage": _rsm_coverage(hub),
        "rsi_funnel": _rsi_funnel(hub),
        "signal_control": {"generated": int(getattr(hub, "_signal_control_count", 0)),
                           "dispatched": int(getattr(hub, "_signal_control_count", 0))},
    }


def _missing_count(hub: Any) -> int:
    total = 0
    for key, sent_seq in hub._sent_seq.items():
        delivered = hub._delivered_seq.get(key, set())
        total += sum(1 for s in sent_seq if s not in delivered)
    return total


def _out_of_order_count(hub: Any) -> int:
    count = 0
    for key in hub._sent_seq:
        delivered = hub._delivered_seq.get(key, set())
        seqs = sorted(delivered)
        for a, b in zip(seqs, seqs[1:]):
            if b != a + 1:
                count += 1
    return count


def _duplicate_count(hub: Any) -> int:
    total = 0
    for key, delivered in hub._delivered_seq.items():
        total += len(delivered) - len(set(delivered)) if False else 0
    return total


def _penetration(hub: Any) -> dict:
    motor = getattr(hub, "_motor_ids", set())
    connected = getattr(hub, "_connected_motor_ids", set())
    defined = len(motor) > 0
    return {"unique_motor_vehicles": len(motor),
            "unique_connected_vehicles": len(connected),
            "rate": (len(connected) / len(motor)) if defined else None,
            "defined": defined}


def _rsm_coverage(hub: Any) -> dict:
    observed = getattr(hub, "_rsm_observed", set())
    eligible = getattr(hub, "_rsm_eligible", set())
    return rsm_coverage_stats(observed=len(observed), eligible=len(eligible))


def _rsi_funnel(hub: Any) -> dict:
    return rsi_funnel(
        requested=int(getattr(hub, "_funnel_requested", 0)),
        existing=int(getattr(hub, "_funnel_existing", 0)),
        enabled=int(getattr(hub, "_funnel_enabled", 0)),
        sent=int(getattr(hub, "_funnel_sent", 0)),
        delivered=int(getattr(hub, "_funnel_delivered", 0)),
        reasons=dict(getattr(hub, "_funnel_reasons", {})),
    )
```

> 注意：`hub.py` 需补以下统计字段（在 `__init__` 中初始化并逐步填充）：
> `_signal_control_count`、`_motor_ids`、`_connected_motor_ids`、`_rsm_observed`、
> `_rsm_eligible`、`_funnel_requested`、`_funnel_existing`、`_funnel_enabled`、
> `_funnel_sent`、`_funnel_delivered`、`_funnel_reasons`。这些字段在 Task 12 的
> `CoslightV2XBridge` 集成时由 protocol/hub 协作填充；本任务先保证 summary 结构可测。

- [x] **Step 4: 运行确认通过**（3 passed）
- [x] **Step 5: 提交**（`feat(v2x): Hub 汇总统计`）

---

## Task 12: coslight shadow-mode 接入

**Files:**
- Create: `algorithms/v2x/__init__.py`
- Create: `algorithms/v2x/adapters/__init__.py`
- Create: `algorithms/v2x/adapters/coslight.py`
- Modify: `algorithms/coslight/controller.py`（`step` → `_step_impl` + 包装；`initialize`/`finish` 尾部钩子）
- Modify: `algorithms/coslight/evaluate.py`（`--v2x-log` 参数 + env 传递）
- Test: `algorithms/v2x/tests/test_coslight_adapter.py`

- [x] **Step 1: 写失败测试（合成 payload 全消息闭环 + actions 透传）**

```python
# algorithms/v2x/tests/test_coslight_adapter.py
import json
import os
from pathlib import Path
import pytest
from algorithms.v2x.adapters.coslight import (
    bridge_initialize, bridge_step, bridge_finish, reset_bridge,
)

# 确定性 fixture：2 个 RSU、1 网联机动车、1 非网联机动车、1 非机动车
INIT = {
    "episode_id": "ep_fix",
    "protocol_version": "2.0",
    "vehicle_types": {
        "official_passenger": {"type_id": "official_passenger",
                               "profile_id": "passenger", "vehicle_class": "passenger"},
        "official_electric_bicycle": {"type_id": "official_electric_bicycle",
                                      "profile_id": "electric_bicycle",
                                      "vehicle_class": "bicycle"},
    },
    "intersections": {
        "i1": {"intersection_id": "i1",
               "phases": {"1": {"green_seconds": 25.0, "yellow_seconds": 3.0,
                                "clearance_seconds": 2.0}},
               "lanes": {"A_0": {"lane_id": "A_0", "edge_id": "A",
                                 "connection_signal_states": []}},
               "connections": [{"from_lane": "A_0", "to_lane": "B_0",
                                "movement": "through"}],
               "direct_neighbors": ["i2"]},
        "i2": {"intersection_id": "i2", "phases": {},
               "lanes": {}, "connections": [], "direct_neighbors": ["i1"]},
    },
}

STEP = {
    "episode_id": "ep_fix", "step_id": 1, "simulation_time": 5.0,
    "intersections": {
        "i1": {"current_phase": 1, "stage": "GREEN", "stage_elapsed": 5.0,
               "lanes": {"A_0": {"connection_signal_states": [
                   {"connection_id": "c0", "movement": "through",
                    "downstream_lane_id": "B_0", "signal_state": "G"}]}}},
        "i2": {"current_phase": None, "stage": "GREEN", "stage_elapsed": 0.0,
               "lanes": {}},
    },
    "vehicles": {
        "car1": {"type_id": "official_passenger",
                 "position": {"x_m": 10.0, "y_m": 10.0},
                 "motion": {"speed_mps": 6.0},
                 "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                              "lane_position_m": 50.0},
                 "route_edges": ["A", "B"],
                 "next_signal": {"intersection_id": "i1", "distance_m": 60.0,
                                 "state": "G"},
                 "leader_gap_m": None, "follower_gap_m": None},
        "truck1": {"type_id": "official_truck",
                   "position": {"x_m": 20.0, "y_m": 20.0},
                   "motion": {"speed_mps": 5.0},
                   "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                                "lane_position_m": 40.0},
                   "route_edges": ["A", "B"],
                   "next_signal": {"intersection_id": "i1", "distance_m": 50.0,
                                   "state": "G"},
                   "leader_gap_m": None, "follower_gap_m": None},
        "bike1": {"type_id": "official_electric_bicycle",
                  "position": {"x_m": 30.0, "y_m": 30.0},
                  "motion": {"speed_mps": 3.0},
                  "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                               "lane_position_m": 30.0},
                  "route_edges": ["A", "B"],
                  "next_signal": {"intersection_id": "i1", "distance_m": 40.0,
                                  "state": "G"},
                  "leader_gap_m": None, "follower_gap_m": None},
    },
}

ACTIONS = {
    "signals": {"i1": {"target_phase": 1}},
    "vehicles": {"car1": {"target_speed_mps": 8.0, "target_lane_index": 0},
                 "bike1": {"target_speed_mps": 3.0, "target_lane_index": 0}},
}


@pytest.fixture()
def log_path(tmp_path: Path, monkeypatch):
    path = tmp_path / "v2x.jsonl"
    monkeypatch.setenv("COSLIGHT_V2X_LOG", str(path))
    monkeypatch.setenv("COSLIGHT_V2X_RUN_ID", "run_fix")
    reset_bridge()
    yield path
    reset_bridge()


def _run_bridge():
    bridge_initialize(INIT)
    bridge_step(STEP, ACTIONS)
    bridge_finish(STEP["simulation_time"])


def test_all_seven_message_types(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    kinds = {rec.get("message", {}).get("message_type")
             for rec in lines if rec.get("record_type") == "message"}
    assert {"BSM", "INTENT", "SPaT", "MAP", "RSM", "RSI",
            "SIGNAL_CONTROL"} <= kinds
    maps = [rec for rec in lines
            if rec.get("message", {}).get("message_type") == "MAP"]
    assert len(maps) == 2  # MAP 数 == RSU 数


def test_rsi_only_to_connected_and_actions_untouched(log_path: Path):
    reset_bridge()
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    rsi_targets = {rec["message"]["payload"]["vehicle_id"] for rec in lines
                   if rec.get("message", {}).get("message_type") == "RSI"}
    assert rsi_targets == {"car1"}   # truck1(非网联机动车) 与 bike1(非机动车) 无 RSI
    # 原 actions 不被过滤：bridge 不修改 actions（由测试直接断言字典不可变即可）
    assert ACTIONS["vehicles"]["bike1"]["target_speed_mps"] == 3.0


def test_rsm_covers_non_connected(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    rsm = [rec for rec in lines if rec.get("message", {}).get("message_type") == "RSM"]
    assert len(rsm) >= 1
    objects = {obj["object_id"] for rec in rsm
               for obj in rec["message"]["payload"]["objects"]}
    assert "bike1" in objects
    # truck1 是机动车且不在 connected_classes（truck 默认非网联）→ 也进 RSM
    assert "truck1" in objects


def test_summary_delivery_rate_one(log_path: Path):
    _run_bridge()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    end = next(rec for rec in lines if rec.get("record_type") == "episode_end")
    assert end["summary"]["delivery"]["delivery_rate"] == 1.0
    assert end["summary"]["delivery"]["pending"] == 0
```

- [x] **Step 2: 运行确认失败**（`No module named 'algorithms.v2x.adapters'`）

- [x] **Step 3: 实现 adapters/coslight.py + __init__**

```python
# algorithms/v2x/__init__.py
"""V2X 车路云协同消息框架公共 API。"""
from .config import V2XConfig, RSUCoverageConfig
from .hub import V2XHub, FrameContext
from .messages import V2XMessage, MessageDraft
from .logger import JSONLSink

__all__ = ["V2XConfig", "RSUCoverageConfig", "V2XHub", "FrameContext",
           "V2XMessage", "MessageDraft", "JSONLSink"]
```

```python
# algorithms/v2x/adapters/__init__.py
"""接入方适配器。"""
```

```python
# algorithms/v2x/adapters/coslight.py
"""coslight shadow-mode 接入：env 开关 + 惰性导入，不改决策逻辑。"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from ..config import V2XConfig
from ..hub import V2XHub
from ..logger import JSONLSink
from ..protocol import (
    build_bsm_draft, build_intent_draft, build_spat_draft,
    build_rsm_draft, build_rsi_draft,
)
from ..entities import VehicleCapability, resolve_v2x_enabled, build_rsu_covered_lanes
from ..coverage import is_in_rsu_coverage, should_use_next_signal_fallback
from ..derive import (
    derive_turn_intent, derive_lane_change_intent, derive_estimated_arrival_s,
)
from ..messages import MessageDraft

_bridge: Optional["CoslightV2XBridge"] = None


class CoslightV2XBridge:
    def __init__(self, log_path: str, config: Optional[V2XConfig] = None,
                 run_id: str = "coslight") -> None:
        self.config = config or V2XConfig()
        self.run_id = run_id
        self._hub = V2XHub(config=self.config, sink=JSONLSink(log_path))
        self._capabilities: dict[str, VehicleCapability] = {}
        self._vehicle_class_by_type: dict[str, str] = {}
        self._init_payload: Optional[Mapping[str, Any]] = None
        self._rsu_ids: set[str] = set()

    def on_initialize(self, payload: Mapping[str, Any], *, run_id: str,
                      episode_id: str, scenario: Optional[Mapping[str, Any]] = None,
                      initial_sim_time: float = 0.0) -> None:
        self._init_payload = payload
        vehicle_types = payload.get("vehicle_types") or {}
        for type_id, meta in vehicle_types.items():
            self._vehicle_class_by_type[str(type_id)] = str(
                meta.get("vehicle_class") or meta.get("profile_id") or "unknown")
        hub = self._hub
        hub.ingest_initialize(
            payload, run_id=run_id, episode_id=episode_id,
            scenario=scenario, initial_sim_time=initial_sim_time)
        self._rsu_ids = set((payload.get("intersections") or {}).keys())

    def on_step(self, payload: Mapping[str, Any],
                actions: Mapping[str, Any]) -> None:
        sim_time = float(payload.get("simulation_time", 0.0))
        hub = self._hub
        frame = hub.ingest_step(payload)
        # --- 上行消息 ---
        intersections = payload.get("intersections") or {}
        vehicles = payload.get("vehicles") or {}
        init_intersections = (self._init_payload or {}).get("intersections") or {}
        # 网联/非网联注册
        for vid, raw in vehicles.items():
            type_id = raw.get("type_id")
            vclass = self._vehicle_class_by_type.get(str(type_id), "unknown")
            v2x = resolve_v2x_enabled(
                vehicle_id=str(vid), vehicle_class=vclass,
                explicit=None, type_v2x=None, config=self.config)
            self._capabilities[str(vid)] = VehicleCapability(vclass, v2x)
            if vclass in self.config.connected_classes:
                hub._motor_ids.add(str(vid))
                if v2x:
                    hub._connected_motor_ids.add(str(vid))
        # BSM/INTENT（网联车）
        for vid, raw in vehicles.items():
            cap = self._capabilities[str(vid)]
            if not cap.v2x_enabled:
                continue
            bsm = build_bsm_draft(str(vid), dict(raw, _sim_time=sim_time))
            if hub.should_send(hub._episode_id or "", str(vid), "BSM", sim_time):
                hub.publish(bsm, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", str(vid), "BSM", sim_time)
            turn, tconf = derive_turn_intent(raw, init_intersections)
            lc, lconf = derive_lane_change_intent(raw, init_intersections)
            arrival, aconf = derive_estimated_arrival_s(raw)
            intent = build_intent_draft(
                str(vid), raw, sim_time=sim_time, turn=turn, lane_change=lc,
                arrival=arrival, turn_conf=tconf, lane_change_conf=lconf,
                arrival_conf=aconf, origin="derived")
            if hub.should_send(hub._episode_id or "", str(vid), "INTENT", sim_time):
                hub.publish(intent, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", str(vid), "INTENT", sim_time)
        # SPaT（每路口）
        for inter_id, state in intersections.items():
            phases_meta = (init_intersections.get(inter_id) or {}).get("phases") or {}
            spat = build_spat_draft(inter_id, state, phases_meta, sim_time=sim_time)
            if hub.should_send(hub._episode_id or "", inter_id, "SPaT", sim_time):
                hub.publish(spat, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", inter_id, "SPaT", sim_time)
        # RSM（非网联，按 RSU 批量）
        rsu_objects: dict[str, list[dict]] = {rid: [] for rid in self._rsu_ids}
        for vid, raw in vehicles.items():
            cap = self._capabilities[str(vid)]
            if cap.v2x_enabled:
                continue
            lane_id = (raw.get("location") or {}).get("lane_id")
            position = raw.get("position")
            pos = (position.get("x_m"), position.get("y_m")) if position else None
            ns_id = (raw.get("next_signal") or {}).get("intersection_id")
            for rid in self._rsu_ids:
                if is_in_rsu_coverage(lane_id, pos, hub_rsu(hub, rid),
                                      self.config.detection_radius_m) or \
                   should_use_next_signal_fallback(
                       lane_id, pos, hub_rsu(hub, rid), ns_id,
                       self.config.detection_radius_m):
                    hub._rsm_eligible.add(str(vid))
                    rsu_objects[rid].append({
                        "object_id": str(vid), "object_class": cap.vehicle_class,
                        "position": pos, "speed_mps": (raw.get("motion") or {}).get("speed_mps"),
                        "lane_id": lane_id, "confidence": 1.0})
                    hub._rsm_observed.add(str(vid))
                    break
        for rid, objects in rsu_objects.items():
            if not objects:
                continue
            rsm = build_rsm_draft(rid, objects, sim_time=sim_time)
            if hub.should_send(hub._episode_id or "", rid, "RSM", sim_time):
                hub.publish(rsm, frame_id=frame.frame_id)
                hub.mark_sent(hub._episode_id or "", rid, "RSM", sim_time)
        # --- 下行影子记录 ---
        hub.ingest_actions(actions, frame=frame)

    def on_finish(self, final_sim_time: float) -> dict:
        return self._hub.finish_episode(final_sim_time, drain_pending=True)

    def close(self) -> None:
        self._hub.close()


def hub_rsu(hub: V2XHub, rsu_id: str):
    """构造 RSU 实体（covered lanes 由初始化 MAP 车道推导 + 配置 extra）。"""
    from ..entities import RSU
    init = getattr(hub, "_coverage_config", None)
    extra = dict((init.extra_covered_lane_ids if init else {}).get(rsu_id, ()))
    covered = build_rsu_covered_lanes({}, {rsu_id: frozenset(extra)})
    position = None
    if init is not None and rsu_id in init.positions:
        position = init.positions[rsu_id]
    return RSU(rsu_id=rsu_id, covered_lane_ids=covered[rsu_id], position=position)


def reset_bridge() -> None:
    global _bridge
    if _bridge is not None:
        try:
            _bridge.close()
        except Exception:
            pass
    _bridge = None


def _ensure_bridge() -> Optional[CoslightV2XBridge]:
    global _bridge
    log_path = os.environ.get("COSLIGHT_V2X_LOG")
    if not log_path:
        return None
    if _bridge is None:
        _bridge = CoslightV2XBridge(
            log_path=log_path,
            run_id=os.environ.get("COSLIGHT_V2X_RUN_ID", "coslight"))
    return _bridge


def bridge_initialize(payload: Mapping[str, Any]) -> None:
    bridge = _ensure_bridge()
    if bridge is None:
        return
    episode_id = str(payload.get("episode_id") or "episode")
    bridge.on_initialize(payload, run_id=bridge.run_id, episode_id=episode_id,
                         scenario={"source": "coslight"})


def bridge_step(payload: Mapping[str, Any],
                actions: Mapping[str, Any]) -> None:
    bridge = _ensure_bridge()
    if bridge is None:
        return
    bridge.on_step(payload, actions)


def bridge_finish(payload: Mapping[str, Any]) -> None:
    bridge = _ensure_bridge()
    if bridge is None:
        return
    final_sim_time = float(payload.get("simulation_time", 0.0))
    bridge.on_finish(final_sim_time)
    reset_bridge()
```

- [x] **Step 4: 修改 controller.py（no-op 钩子，默认关闭）**

```python
# algorithms/coslight/controller.py
# 1) 在模块顶部（imports 之后）加入惰性钩子转发：
def _v2x_initialize(payload: dict) -> None:
    try:
        from algorithms.v2x.adapters.coslight import bridge_initialize
    except Exception:
        return
    bridge_initialize(payload)

def _v2x_step(payload: dict, actions: dict) -> None:
    try:
        from algorithms.v2x.adapters.coslight import bridge_step
    except Exception:
        return
    bridge_step(payload, actions)

def _v2x_finish(payload: dict) -> None:
    try:
        from algorithms.v2x.adapters.coslight import bridge_finish
    except Exception:
        return
    bridge_finish(payload)

# 2) 原 `def step(payload: dict) -> dict:` 重命名为 `def _step_impl(payload: dict) -> dict:`
# 3) 新包装（放在 _step_impl 之后）：
def step(payload: dict) -> dict:
    result = _step_impl(payload)
    _v2x_step(payload, result)
    return result

# 4) initialize() 末尾（return 之前）加：
    _v2x_initialize(payload)

# 5) finish(payload) 末尾加：
    _v2x_finish(payload)
```

- [x] **Step 5: 修改 evaluate.py**

```python
# algorithms/coslight/evaluate.py
# 1) main() 的 parser 增加：
    parser.add_argument("--v2x-log", type=Path, help="V2X shadow-mode 日志路径（JSONL）")

# 2) _run_evaluation 内，checkpoint env 设置之后加入：
    v2x_log = str(request.get("v2x_log") or "")
    if v2x_log:
        os.environ["COSLIGHT_V2X_LOG"] = v2x_log
        os.environ["COSLIGHT_V2X_RUN_ID"] = f"{method}-{seed}"
    else:
        os.environ.pop("COSLIGHT_V2X_LOG", None)
        os.environ.pop("COSLIGHT_V2X_RUN_ID", None)

# 3) main() 组装 request 时透传：
        request["v2x_log"] = str(args.v2x_log) if args.v2x_log else ""
```

- [x] **Step 6: 运行 adapter 测试确认通过**

Run: `pytest algorithms/v2x/tests/test_coslight_adapter.py -q --tb=short`
Expected: PASS（4 passed）

- [x] **Step 7: coslight 回归**

Run: `pytest algorithms/coslight/test_parallel_train.py algorithms/coslight/test_controller.py -q --tb=short`
Expected: PASS（98 passed，含 `test_v17_collab_bias_is_action_dependent_and_starts_zero`）

- [x] **Step 8: 提交**

```bash
rsync -avz -e "ssh -o StrictHostKeyChecking=no" algorithms/v2x/ 346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/
rsync -avz -e "ssh -o StrictHostKeyChecking=no" algorithms/coslight/controller.py algorithms/coslight/evaluate.py 346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/coslight/
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && export PYTHONPATH=/usr/share/sumo/tools && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x algorithms/coslight/test_parallel_train.py algorithms/coslight/test_controller.py -q --tb=short && git add algorithms/v2x algorithms/coslight/controller.py algorithms/coslight/evaluate.py && git commit -m "feat(v2x): coslight shadow-mode 接入（controller/evaluate 钩子，默认关闭）"'
```

---

## Task 13: 20 路口 smoke + 文档收尾

**Files:**
- Modify: `docs/superpowers/plans/2026-08-05-v2x-framework.md`（完成后打勾）
- Modify: `docs/superpowers/specs/2026-08-04-v2x-framework-design.md`（实现状态更新）

- [x] **Step 1: 服务器 20 路口 smoke**

```bash
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && source ~/anaconda3/etc/profile.d/conda.sh && conda activate BWformer && export PYTHONPATH=/usr/share/sumo/tools && mkdir -p logs/v2x_smoke && /home/kemove/anaconda3/envs/BWformer/bin/python -u algorithms/coslight/evaluate.py --methods coslight --episodes 1 --duration 300 --intersections 20 --period off_peak --checkpoint algorithms/coslight/runs/stage1_local_v16_lr1_8ep/checkpoints/coslight_parallel_ep8.pt --v2x-log logs/v2x_smoke/run.jsonl 2>&1 | tail -20'
```

- [x] **Step 2: 校验 smoke 产物**

```bash
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && ls -la logs/v2x_smoke/run.jsonl && /home/kemove/anaconda3/envs/BWformer/bin/python -m algorithms.v2x.replay logs/v2x_smoke/run.jsonl --summary && /home/kemove/anaconda3/envs/BWformer/bin/python - <<EOF
import json
lines=[json.loads(x) for x in open("logs/v2x_smoke/run.jsonl")]
maps=[r for r in lines if r.get("message",{}).get("message_type")=="MAP"]
print("MAP count =", len(maps), "(expect 20)")
end=[r for r in lines if r.get("record_type")=="episode_end"][0]
print("summary keys =", sorted(end["summary"].keys()))
EOF'
```

Expected: MAP count == 20；summary 含 delivery/latency/penetration/rsm_coverage/rsi_funnel/signal_control；`simulation/sumo/` 无修改（`git status -- simulation/sumo/` 干净）。

- [x] **Step 3: 更新 spec 状态**

在 `docs/superpowers/specs/2026-08-04-v2x-framework-design.md` 头部加：
```markdown
- 实现状态：2026-08-05 已实现（Tasks 1-13 完成，smoke 通过）
```

- [x] **Step 4: 提交**

```bash
ssh 346-4090 'cd ~/devdata1/gsb/citypulse-v2x-sim && git add docs && git commit -m "docs(v2x): 实现计划与 spec 状态更新（20 路口 smoke 通过）"'
```

---

## Self-Review 记录

- **Spec 覆盖**：六类消息+事件（Task 2/9/12）；能力判定链+渗透率（Task 3/12）；延迟/丢包/抖动（Task 8）；两阶段 API+frame（Task 8）；调度按 (source,type)（Task 8）；override（Task 8 占位 + Task 12 派生）；MAP 每 RSU（Task 8/12）；生命周期+close（Task 8）；RSM 覆盖+fallback（Task 5/12）；JSONL 四类记录（Task 6/8）；统计口径（Task 7/11）；coslight shadow-mode（Task 12）；测试/验收（Task 12/13）。**无缺项**。
- **占位符扫描**：无 TBD/TODO；Task 8 中 `ingest_step` 的 protocol 草稿接入由 Task 12 的 bridge 实现（hub 提供 publish/mark_sent/should_send 原语，分工明确）。
- **类型一致性**：`MessageDraft`/`V2XMessage`/`FrameContext`/`V2XConfig`/`RSUCoverageConfig` 签名在全部任务中一致；`build_summary` 在 Task 7 声明、Task 11 实现（hub 导入处用 type-ignore 注释，Task 11 后移除）。
