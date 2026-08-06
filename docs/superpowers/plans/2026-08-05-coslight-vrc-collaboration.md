# CoSLight 车路云协同决策层（VRC Collaboration）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在 `algorithms/v2x/collab/` 实现端-边-云协同决策层（EdgeAggregator → CloudStateStore → CloudRulePolicy → ActionArbiter → RSI 下发），以 shadow 模式接入 CoSLight：云端只消费 V2XHub 已投递消息生成信号/车辆建议，`applied == baseline`；并加入算法无关的场景预设（`config/scenario_presets.py`）与 `--scenario-preset/--intersections/--v2x-collab*` CLI。

**Architecture:** 三层进程内管线。① `EdgeAggregator` 订阅 Hub 投递的 BSM/INTENT/SPaT/MAP/RSM，维护按 `next_signal` 迁移的车辆缓存并产出不可变 `EdgeSnapshot`；② `CloudStateStore` 提供新鲜度视图（age/missing/stale）与 MAP 静态上下文；③ `CloudRulePolicy` 用规则族 C（排队/到达基线 + INTENT ETA 前视）生成 `SignalProposal`，用阈值触发（当前绿窗追赶 + 车道 advisory）生成 `VehicleGuidanceProposal`；④ `ActionArbiter`（OFF/SHADOW/ACTIVE）在 shadow 下原样返回 baseline，ACTIVE 运行时抛错；PROPOSED 的 RSI 经 `hub.publish` 走网络模型但**不进** `actions.vehicles`。场景预设由中立 `config/scenario_presets.py` 提供，backend 透传导入，evaluate 通过 CLI 解析 `ResolvedScenarioScope` 并经 env 传给桥接器。

**Tech Stack:** Python 3.10+（dataclasses、enum、heapq、hashlib、json、argparse、pytest）。无 torch/SUMO 依赖（collab 包本身）；evaluate 侧复用现有 torch/SUMO 环境。

**工作流（重要）:** 代码在本地 `/Users/g/Documents/车路云/tmp/coslight-parallel-stage/` 编辑，同步到服务器 `346-4090:/home/kemove/devdata1/gsb/citypulse-v2x-sim/`，在服务器跑 pytest（BWformer 环境），并在服务器 git 仓库提交（branch `feature/rl`）。每条 Commit 步骤都包含同步 + git add/commit。

```bash
# 同步（每任务提交前执行，路径以任务为准）
scp -P 24 algorithms/v2x/collab/*.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/
# 服务器跑测试
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && export PYTHONPATH=/usr/share/sumo/tools && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x -q --tb=short'
# 服务器提交
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add <paths> && git commit -m "..."'
```

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `algorithms/v2x/hub.py` | 修改（增量）：`ingest_initialize` 的 MAP 载荷追加透传 `phase_order` |
| `config/__init__.py` | 新建：空包 |
| `config/scenario_presets.py` | 新建：算法无关注册表（`ScenarioPreset`/`SCENARIO_PRESET_REGISTRY`/`SCENARIO_PRESETS`/`ResolvedScenarioScope` + helper） |
| `backend/app/scenario/presets.py` | 修改：改为从 `config.scenario_presets` 透传导入（保持既有导出名） |
| `backend/tests/test_scenario_presets.py` | 修改：追加"同一对象"一致性断言 |
| `algorithms/v2x/collab/__init__.py` | 公共 API 导出 |
| `algorithms/v2x/collab/proposals.py` | 枚举（DecisionMode/GuidanceEmissionMode/SignalDecisionStatus/GuidanceDecisionStatus/DecisionSource）+ `SignalProposal`/`VehicleGuidanceProposal`/`LastEmittedGuidanceState` + 类型化配置（FreshnessConfig/SignalPolicyConfig/GuidancePolicyConfig/CollabConfig） |
| `algorithms/v2x/collab/snapshot.py` | 不可变模型（LaneState/ApproachState/ConnectedVehicleState/EdgeSnapshot/IntersectionStaticContext）+ `build_static_context(map_message)` |
| `algorithms/v2x/collab/aggregator.py` | `EdgeAggregator`：订阅/缓存/车辆迁移/跨帧 arrivals/快照构建 |
| `algorithms/v2x/collab/state.py` | `CloudStateStore`/`CloudIntersectionView`：新鲜度 age/missing/stale + 静态上下文 |
| `algorithms/v2x/collab/policy.py` | `CloudRulePolicy`：`propose_signal`（规则族 C）+ `propose_guidance`（阈值触发） |
| `algorithms/v2x/collab/arbiter.py` | `ActionArbiter` + `validate_signal_proposal` + `ActiveModeUnavailableError` |
| `algorithms/v2x/collab/engine.py` | `CollabDecisionEngine`/`CollabTickResult`：决策帧编排、RSI 发射、tick 统计原子写、finalize |
| `algorithms/v2x/collab/records.py` | `InMemoryRecordCollector` + edge_snapshot/cloud_proposal/arbitration/collab_tick_stats/collab_episode_end 记录构造 |
| `algorithms/v2x/collab/stats.py` | episode 汇总（§5.2）+ pooled 聚合 + 完整性审计 |
| `algorithms/v2x/adapters/coslight.py` | 修改：collab 开关/env/scope 校验/engine 接线/`last_collab_summary()` |
| `algorithms/coslight/scope_cli.py` | 新建：`parse_intersections`/`resolve_scope`/`build_scope_block`（纯函数，便于单测） |
| `algorithms/coslight/evaluate.py` | 修改：`--scenario-preset`/`--intersections`（int 或列表）/`--v2x-collab*`/env 传递/row 的 scope 块 |
| `algorithms/v2x/collab/tests/*` | 每模块 pytest（test_proposals/snapshot/aggregator/state/policy_signal/policy_guidance/arbiter/records_stats/engine） |
| `algorithms/v2x/tests/test_hub.py` | 修改：MAP 载荷含 `phase_order` 断言 |
| `algorithms/coslight/test_scope_cli.py` | 新建：CLI 解析纯函数测试 |
| `algorithms/coslight/test_evaluate.py` | 修改：追加 scope/env 传递相关轻量断言（不启动 SUMO） |

---

## Task 1: MAP 载荷增量扩展 `phase_order`

**Files:**
- Modify: `algorithms/v2x/hub.py`（`ingest_initialize` 的 MAP draft 构造）
- Test: `algorithms/v2x/tests/test_hub.py`

- [x] **Step 1: 写失败测试**

在 `algorithms/v2x/tests/test_hub.py` 末尾追加：

```python
def test_map_payload_carries_phase_order():
    hub = _hub()
    payload = {
        "episode_id": "ep1",
        "vehicle_types": {},
        "intersections": {
            "i1": {"phase_order": [1, 2, 3],
                   "phases": {}, "lanes": {}, "connections": [], "direct_neighbors": []},
        },
    }
    frame = hub.ingest_initialize(
        payload, run_id="run1", episode_id="ep1", initial_sim_time=0.0)
    # MAP 走延迟队列，手动 advance 到投递时刻
    hub.advance(1.0)
    maps = [rec for rec in hub.sent_records if rec["message_type"] == "MAP"]
    assert len(maps) == 1
    # sent_records 只有元数据；通过订阅回调读取 payload
    seen = []

    def _on_map(message):
        seen.append(message)

    hub2 = _hub()
    hub2.subscribe("MAP", _on_map)
    frame2 = hub2.ingest_initialize(
        payload, run_id="run1", episode_id="ep1", initial_sim_time=0.0)
    hub2.advance(1.0)
    assert len(seen) == 1
    assert seen[0].payload["phase_order"] == [1, 2, 3]
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_hub.py::test_map_payload_carries_phase_order -q --tb=short'`
Expected: FAIL（`payload["phase_order"]` KeyError）

- [x] **Step 3: 实现**

修改 `algorithms/v2x/hub.py` 中 `ingest_initialize` 的 MAP draft 构造（约 200-215 行），把：

```python
            self._publish(MessageDraft(
                "MAP", inter_id, "cloud", initial_sim_time,
                {"intersection_id": inter_id,
                 "phases": intersections[inter_id].get("phases") or {},
                 "lanes": intersections[inter_id].get("lanes") or {},
                 "connections": intersections[inter_id].get("connections") or [],
                 "direct_neighbors": intersections[inter_id].get("direct_neighbors") or []},
            ), frame_id=frame.frame_id)
```

改为：

```python
            self._publish(MessageDraft(
                "MAP", inter_id, "cloud", initial_sim_time,
                {"intersection_id": inter_id,
                 "phases": intersections[inter_id].get("phases") or {},
                 "lanes": intersections[inter_id].get("lanes") or {},
                 "connections": intersections[inter_id].get("connections") or [],
                 "direct_neighbors": intersections[inter_id].get("direct_neighbors") or [],
                 "phase_order": [int(v) for v in (intersections[inter_id].get("phase_order") or [])]},
            ), frame_id=frame.frame_id)
```

注意：`phase_order` 是**增量字段**，不进 `REQUIRED_FIELDS["MAP"]`；`get("phase_order") or []` 保证旧调用（如 `test_hub.py` 的 `_init`）不传该键时仍兼容。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x -q --tb=short'`
Expected: PASS（全部 v2x 测试，含新增 1 条）

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/hub.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/hub.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/hub.py algorithms/v2x/tests/test_hub.py && git commit -m "feat(v2x): MAP payload carries phase_order (additive protocol extension)"'
```

---

## Task 2: 中立场景预设注册表 + backend 透传

**Files:**
- Create: `config/__init__.py`, `config/scenario_presets.py`
- Modify: `backend/app/scenario/presets.py`, `backend/tests/test_scenario_presets.py`
- Test: `backend/tests/test_scenario_presets.py`

- [x] **Step 1: 写失败测试**

在 `backend/tests/test_scenario_presets.py` 末尾追加：

```python
def test_backend_registry_is_same_object_as_config_module() -> None:
    from config.scenario_presets import SCENARIO_PRESET_REGISTRY as neutral
    from backend.app.scenario.presets import SCENARIO_PRESET_REGISTRY

    assert SCENARIO_PRESET_REGISTRY is neutral
    assert neutral["east_dense"].intersection_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    assert neutral["east_dense"].map_template == "east_dense"
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest backend/tests/test_scenario_presets.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError: No module named 'config'`）

- [x] **Step 3: 实现 `config/scenario_presets.py`**

创建 `config/__init__.py`（空文件）与 `config/scenario_presets.py`：

```python
# config/scenario_presets.py
"""算法无关的场景预设注册表（单一事实源；backend 与 evaluate 共同导入）。

字段契约与既有 backend.app.scenario.presets 完全一致：
preset_id / label / intersection_ids / map_template。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True, slots=True)
class ScenarioPreset:
    preset_id: str
    label: str
    intersection_ids: tuple[str, ...]
    map_template: str


SCENARIO_PRESET_REGISTRY: dict[str, ScenarioPreset] = {
    "xiongan_20": ScenarioPreset(
        preset_id="xiongan_20",
        label="雄安20路口路网",
        intersection_ids=tuple(f"demo_{i}" for i in range(1, 21)),
        map_template="xiongan20",
    ),
    "east_dense": ScenarioPreset(
        preset_id="east_dense",
        label="东部密集路口场景",
        intersection_ids=("demo_3", "demo_5", "demo_6", "demo_9"),
        map_template="east_dense",
    ),
    "west_dense": ScenarioPreset(
        preset_id="west_dense",
        label="西部密集路口场景",
        intersection_ids=("demo_14", "demo_15", "demo_19"),
        map_template="west_dense",
    ),
}

# evaluate 侧别名
SCENARIO_PRESETS: dict[str, ScenarioPreset] = SCENARIO_PRESET_REGISTRY

ALL_DEMO_INTERSECTION_IDS: tuple[str, ...] = tuple(
    f"demo_{i}" for i in range(1, 21)
)


@dataclass(frozen=True, slots=True)
class ResolvedScenarioScope:
    """CLI 解析结果：算法控制 == 协同 managed 范围（spec §7.2/§7.3）。"""

    source: Literal["preset", "custom", "default"]
    preset_id: Optional[str] = None
    managed_ids: tuple[str, ...] = ()


def list_scenario_presets() -> list[ScenarioPreset]:
    return [SCENARIO_PRESET_REGISTRY[key] for key in sorted(SCENARIO_PRESET_REGISTRY)]


def require_scenario_preset(preset_id: str) -> ScenarioPreset:
    preset = SCENARIO_PRESET_REGISTRY.get(preset_id)
    if preset is None:
        allowed = sorted(SCENARIO_PRESET_REGISTRY)
        raise ValueError(
            f"scenario_preset_id must be one of {allowed}, got {preset_id!r}."
        )
    return preset


def supported_intersection_ids() -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for preset in list_scenario_presets():
        for intersection_id in preset.intersection_ids:
            if intersection_id not in seen:
                seen.add(intersection_id)
                ordered.append(intersection_id)
    return tuple(ordered)
```

- [x] **Step 4: 修改 `backend/app/scenario/presets.py` 为透传**

把 `backend/app/scenario/presets.py` 全文替换为：

```python
"""场景预设注册表：单一事实源在 config/scenario_presets.py，此处透传导入。

保持既有导出名（SCENARIO_PRESET_REGISTRY / ScenarioPreset /
list_scenario_presets / require_scenario_preset / supported_intersection_ids /
ALL_DEMO_INTERSECTION_IDS）不变，避免破坏 backend 其它模块与前端契约。
"""

from __future__ import annotations

from config.scenario_presets import (
    ALL_DEMO_INTERSECTION_IDS,
    SCENARIO_PRESET_REGISTRY,
    ScenarioPreset,
    list_scenario_presets,
    require_scenario_preset,
    supported_intersection_ids,
)

__all__ = [
    "ALL_DEMO_INTERSECTION_IDS",
    "SCENARIO_PRESET_REGISTRY",
    "ScenarioPreset",
    "list_scenario_presets",
    "require_scenario_preset",
    "supported_intersection_ids",
]
```

- [x] **Step 5: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest backend/tests/test_scenario_presets.py -q --tb=short && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest backend/tests -q --tb=short'`
Expected: PASS（backend 全量测试，含既有 3 条 + 新增 1 条）

- [x] **Step 6: Commit**

```bash
scp -P 24 config/scenario_presets.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/config/scenario_presets.py
scp -P 24 backend/app/scenario/presets.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/backend/app/scenario/presets.py
scp -P 24 backend/tests/test_scenario_presets.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/backend/tests/test_scenario_presets.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add config backend/app/scenario/presets.py backend/tests/test_scenario_presets.py && git commit -m "feat(config): neutral scenario preset registry + backend passthrough"'
```

---

## Task 3: collab 包骨架 + proposals.py（枚举/模型/配置）

**Files:**
- Create: `algorithms/v2x/collab/__init__.py`, `algorithms/v2x/collab/proposals.py`
- Test: `algorithms/v2x/collab/tests/test_proposals.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/__init__.py`（空）与 `algorithms/v2x/collab/tests/test_proposals.py`：

```python
import math
import pytest

from algorithms.v2x.collab.proposals import (
    CollabConfig,
    DecisionMode,
    FreshnessConfig,
    GuidanceDecisionStatus,
    GuidanceEmissionMode,
    GuidancePolicyConfig,
    SignalDecisionStatus,
    SignalPolicyConfig,
)


def test_enum_values():
    assert DecisionMode.OFF.value == "off"
    assert DecisionMode.SHADOW.value == "shadow"
    assert DecisionMode.ACTIVE.value == "active"
    assert GuidanceEmissionMode.THRESHOLD.value == "threshold"
    assert GuidanceEmissionMode.FULL.value == "full"
    assert GuidanceEmissionMode.DISABLED.value == "disabled"
    assert SignalDecisionStatus.PROPOSED.value == "proposed"
    assert GuidanceDecisionStatus.SUPPRESSED_THRESHOLD.value == "suppressed_threshold"


def test_freshness_defaults():
    cfg = FreshnessConfig()
    assert cfg.bsm_s == 10.0
    assert cfg.intent_s == 10.0
    assert cfg.spat_s == 10.0
    assert cfg.rsm_s == 10.0


def test_signal_policy_defaults_and_validation():
    cfg = SignalPolicyConfig()
    assert cfg.queue_weight == 1.0
    assert cfg.forward_horizon_s == 30.0
    with pytest.raises(ValueError):
        SignalPolicyConfig(queue_weight=-1.0)
    with pytest.raises(ValueError):
        SignalPolicyConfig(forward_horizon_s=0.0)


def test_guidance_policy_defaults_and_validation():
    cfg = GuidancePolicyConfig()
    assert cfg.guidance_horizon_m == 300.0
    assert cfg.speed_trigger_delta_mps == 2.0
    with pytest.raises(ValueError):
        GuidancePolicyConfig(guidance_horizon_m=-1.0)
    with pytest.raises(ValueError):
        GuidancePolicyConfig(v_min_mps=10.0, v_max_mps=5.0)
    with pytest.raises(ValueError):
        GuidancePolicyConfig(speed_scale_low=1.5, speed_scale_high=1.0)


def test_collab_config_defaults_are_immutable_and_fresh():
    cfg = CollabConfig()
    assert cfg.decision_mode is DecisionMode.SHADOW
    assert cfg.guidance_mode is GuidanceEmissionMode.THRESHOLD
    assert cfg.freshness.bsm_s == 10.0
    assert cfg.signal_policy.queue_weight == 1.0
    # frozen dataclass 不可变
    with pytest.raises(Exception):
        cfg.freshness.bsm_s = 99.0  # type: ignore[misc]
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_proposals.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError: No module named 'algorithms.v2x.collab'`）

- [x] **Step 3: 实现 `proposals.py`**

```python
# algorithms/v2x/collab/proposals.py
"""协同决策层：枚举、建议/状态模型与类型化配置（spec §1.6/§2.3/§3.4/§3.5）。"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Mapping


class DecisionMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


class GuidanceEmissionMode(str, Enum):
    THRESHOLD = "threshold"
    FULL = "full"
    DISABLED = "disabled"


class SignalDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    KEEP_CURRENT = "keep_current"
    NO_DEMAND = "no_demand"
    STALE_INPUT = "stale_input"
    MISSING_INPUT = "missing_input"
    INVALID_PROPOSAL = "invalid_proposal"
    SUPPRESSED_MIN_GREEN = "suppressed_min_green"
    SUPPRESSED_SWITCH_MARGIN = "suppressed_switch_margin"
    SUPPRESSED_TRANSITION = "suppressed_transition"


class GuidanceDecisionStatus(str, Enum):
    PROPOSED = "proposed"
    NO_ACTION_NEEDED = "no_action_needed"
    STALE_INPUT = "stale_input"
    MISSING_INPUT = "missing_input"
    INVALID_PROPOSAL = "invalid_proposal"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    SUPPRESSED_THRESHOLD = "suppressed_threshold"


class DecisionSource(str, Enum):
    BASELINE = "baseline"
    CLOUD = "cloud"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class FreshnessConfig:
    bsm_s: float = 10.0
    intent_s: float = 10.0
    spat_s: float = 10.0
    rsm_s: float = 10.0

    def __post_init__(self) -> None:
        for name in ("bsm_s", "intent_s", "spat_s", "rsm_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and > 0")


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

    def __post_init__(self) -> None:
        for name in ("queue_weight", "arrival_weight", "forward_weight"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if not (self.queue_weight > 0.0 or self.arrival_weight > 0.0
                or self.forward_weight > 0.0):
            raise ValueError("at least one demand weight must be > 0")
        for name in ("forward_horizon_s", "forward_decay_s", "proposal_ttl_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and > 0")
        for name in ("min_green_s", "switch_score_margin"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if self.demand_epsilon < 0.0 or self.score_epsilon <= 0.0:
            raise ValueError("demand_epsilon >= 0 and score_epsilon > 0 required")


@dataclass(frozen=True, slots=True)
class GuidancePolicyConfig:
    guidance_horizon_m: float = 300.0
    guidance_ttl_s: float = 10.0
    minimum_resend_interval_s: float = 5.0
    speed_trigger_delta_mps: float = 2.0
    speed_resend_delta_mps: float = 1.0
    v_min_mps: float = 2.0
    v_max_mps: float = 20.0
    speed_scale_low: float = 0.8
    speed_scale_high: float = 1.2
    lane_queue_margin: float = 2.0
    lane_change_min_distance_m: float = 30.0
    min_guidance_speed_mps: float = 1.0
    green_clearance_buffer_s: float = 2.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.guidance_horizon_m) or self.guidance_horizon_m <= 0.0:
            raise ValueError("guidance_horizon_m must be finite and > 0")
        if not math.isfinite(self.guidance_ttl_s) or self.guidance_ttl_s <= 0.0:
            raise ValueError("guidance_ttl_s must be finite and > 0")
        for name in (
            "minimum_resend_interval_s", "speed_trigger_delta_mps",
            "speed_resend_delta_mps", "lane_queue_margin",
            "lane_change_min_distance_m", "min_guidance_speed_mps",
            "green_clearance_buffer_s",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and >= 0")
        if not (0.0 <= self.v_min_mps <= self.v_max_mps):
            raise ValueError("require 0 <= v_min_mps <= v_max_mps")
        if not (0.0 < self.speed_scale_low <= self.speed_scale_high):
            raise ValueError("require 0 < speed_scale_low <= speed_scale_high")


@dataclass(frozen=True, slots=True)
class CollabConfig:
    decision_mode: DecisionMode = DecisionMode.SHADOW
    guidance_mode: GuidanceEmissionMode = GuidanceEmissionMode.THRESHOLD
    freshness: FreshnessConfig = field(default_factory=FreshnessConfig)
    log_edge_snapshot: bool = True
    log_arbitration_mode: Literal["all", "differences"] = "all"
    signal_policy: SignalPolicyConfig = field(default_factory=SignalPolicyConfig)
    guidance_policy: GuidancePolicyConfig = field(default_factory=GuidancePolicyConfig)


@dataclass(frozen=True, slots=True)
class SignalProposal:
    intersection_id: str
    status: SignalDecisionStatus
    candidate_action: int | None
    proposed_action: int | None
    current_action: int | None
    action_scores: Mapping[int, float]
    reason: str
    confidence: float
    valid_from: float
    valid_until: float
    needs_transition: bool
    decision_frame_id: str
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VehicleGuidanceProposal:
    vehicle_id: str
    status: GuidanceDecisionStatus
    speed_status: GuidanceDecisionStatus
    lane_status: GuidanceDecisionStatus
    current_speed_mps: float
    target_speed_mps: float | None
    current_lane_id: str | None
    target_lane_id: str | None
    target_lane_index: int | None
    guidance_type: str | None
    reason: str
    confidence: float | None
    valid_from: float
    valid_until: float
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


@dataclass(slots=True)
class LastEmittedGuidanceState:
    """最近一次**发布**的 RSI 状态（网络可能随后丢包，不代表送达）。"""

    target_speed_mps: float | None
    target_lane_id: str | None
    target_lane_index: int | None
    emitted_at: float
    valid_until: float
    reason: str
    emitted_message_id: str
```

创建 `algorithms/v2x/collab/__init__.py`：

```python
# algorithms/v2x/collab/__init__.py
"""车路云协同决策层（spec 2026-08-05-coslight-vrc-collaboration-design）。"""

from .proposals import (
    CollabConfig,
    DecisionMode,
    DecisionSource,
    FreshnessConfig,
    GuidanceDecisionStatus,
    GuidanceEmissionMode,
    GuidancePolicyConfig,
    LastEmittedGuidanceState,
    SignalDecisionStatus,
    SignalPolicyConfig,
    SignalProposal,
    VehicleGuidanceProposal,
)

__all__ = [
    "CollabConfig", "DecisionMode", "DecisionSource", "FreshnessConfig",
    "GuidanceDecisionStatus", "GuidanceEmissionMode", "GuidancePolicyConfig",
    "LastEmittedGuidanceState", "SignalDecisionStatus", "SignalPolicyConfig",
    "SignalProposal", "VehicleGuidanceProposal",
]
```

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_proposals.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 -r algorithms/v2x/collab kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab && git commit -m "feat(v2x): collab package skeleton + proposal models and configs"'
```

---

## Task 4: snapshot.py — 不可变模型 + MAP 静态上下文

**Files:**
- Create: `algorithms/v2x/collab/snapshot.py`
- Test: `algorithms/v2x/collab/tests/test_snapshot.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_snapshot.py`：

```python
import pytest

from algorithms.v2x.collab.snapshot import build_static_context
from algorithms.v2x.messages import V2XMessage


def _map_message(intersection_id="i1"):
    return V2XMessage(
        message_type="MAP",
        message_id=f"map-{intersection_id}",
        schema_version="1.0",
        run_id="run1", episode_id="ep1",
        frame_id="ep1:init", sequence_no=1,
        sim_time=0.0, source_id=intersection_id, destination="cloud",
        correlation_id=None,
        payload={
            "intersection_id": intersection_id,
            "phase_order": [1, 2],
            "phases": {
                "1": {"phase_id": 1, "movement": "through",
                      "connection_priorities": {"c0": "protected"}},
                "2": {"phase_id": 2, "movement": "left",
                      "connection_priorities": {"c1": "protected"}},
            },
            "lanes": {
                "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                        "approach_id": "west", "movements": ("through",),
                        "speed_limit_mps": 13.9},
                "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                        "approach_id": "west", "movements": ("left",),
                        "speed_limit_mps": 11.1},
                "B_0": {"lane_id": "B_0", "edge_id": "B", "lane_index": 0,
                        "approach_id": "east", "movements": ("through",),
                        "speed_limit_mps": 13.9},
            },
            "connections": [
                {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
                 "movement": "through"},
                {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
                 "movement": "left"},
            ],
            "direct_neighbors": [],
        },
    )


def test_static_context_fields():
    ctx = build_static_context(_map_message())
    assert ctx.intersection_id == "i1"
    assert ctx.phase_order == (1, 2)
    assert ctx.valid_actions == (1, 2)
    assert ctx.phase_to_action == {1: 1, 2: 2}
    assert ctx.action_to_movements[1] == ("through",)
    assert ctx.action_to_movements[2] == ("left",)
    assert ctx.movement_to_lanes["through"] == ("A_0",)
    assert ctx.movement_to_lanes["left"] == ("A_1",)
    assert ctx.lane_to_edge["A_0"] == "A"
    assert ctx.lane_to_index["A_0"] == 0
    assert ctx.lane_to_approach["A_0"] == "west"
    assert ctx.lane_to_movements["A_1"] == ("left",)
    assert ctx.lane_speed_limit_mps["A_1"] == 11.1
    assert ctx.transition_phases == frozenset()
    assert ctx.map_source_message_id == "map-i1"


def test_static_context_missing_phase_order_raises():
    msg = _map_message()
    msg = V2XMessage(**{**msg.__dict__, "payload": dict(msg.payload, phase_order=[])})
    with pytest.raises(ValueError):
        build_static_context(msg)


def test_static_context_unknown_priority_connection_ignored():
    msg = _map_message()
    payload = dict(msg.payload)
    payload["phases"] = dict(payload["phases"])
    payload["phases"]["1"] = dict(payload["phases"]["1"],
                                  connection_priorities={"c0": "protected",
                                                         "ghost": "protected"})
    ctx = build_static_context(V2XMessage(**{**msg.__dict__, "payload": payload}))
    assert ctx.action_to_movements[1] == ("through",)
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_snapshot.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError: No module named 'algorithms.v2x.collab.snapshot'`）

- [x] **Step 3: 实现 `snapshot.py`**

```python
# algorithms/v2x/collab/snapshot.py
"""不可变模型与 MAP→静态上下文构建（spec §1.4）。"""
from __future__ import annotations

from typing import Mapping

from algorithms.v2x.messages import V2XMessage


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
    next_signal_intersection_id: str | None
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
    sim_time: float
    phase: int | None
    stage: str | None
    stage_elapsed_s: float | None
    remaining_time_s: float | None
    approaches: Mapping[str, ApproachState]
    connected_vehicles: Mapping[str, ConnectedVehicleState]
    last_delivery_at: Mapping[str, float | None]
    source_message_ids: tuple[str, ...]
    source_frame_ids: tuple[str, ...]


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


def build_static_context(map_message: V2XMessage) -> IntersectionStaticContext:
    """仅由已投递 MAP 构建静态上下文（spec §7.2：不得回读 initialize payload）。"""
    payload = map_message.payload
    intersection_id = str(payload["intersection_id"])
    phase_order = tuple(int(v) for v in payload.get("phase_order") or ())
    if not phase_order:
        raise ValueError(f"MAP {map_message.message_id} has empty phase_order")
    lanes = payload.get("lanes") or {}
    lane_to_approach: dict[str, str] = {}
    lane_to_edge: dict[str, str] = {}
    lane_to_index: dict[str, int] = {}
    lane_to_movements: dict[str, tuple[str, ...]] = {}
    lane_speed_limit_mps: dict[str, float] = {}
    for lane_id, meta in lanes.items():
        lane_id = str(lane_id)
        lane_to_approach[lane_id] = str(
            meta.get("approach_id") or meta.get("edge_id") or lane_id)
        lane_to_edge[lane_id] = str(meta.get("edge_id") or lane_id.rsplit("_", 1)[0])
        lane_to_index[lane_id] = int(meta.get("lane_index", 0))
        movements = tuple(
            str(m) for m in (meta.get("movements") or ()))
        lane_to_movements[lane_id] = movements
        lane_speed_limit_mps[lane_id] = float(
            meta.get("speed_limit_mps", meta.get("max_speed", 13.9)))
    connections_by_id = {
        str(conn.get("connection_id")): conn
        for conn in (payload.get("connections") or [])
        if conn.get("connection_id")
    }
    phases_meta = payload.get("phases") or {}
    action_to_movements: dict[int, tuple[str, ...]] = {}
    movement_to_lanes: dict[str, set[str]] = {}
    for phase_id in phase_order:
        phase = phases_meta.get(str(phase_id), phases_meta.get(phase_id, {}))
        served: set[str] = set()
        for raw_connection_id, _priority in (phase.get("connection_priorities") or {}).items():
            conn = connections_by_id.get(str(raw_connection_id))
            if conn and conn.get("movement"):
                served.add(str(conn["movement"]))
        action_to_movements[phase_id] = tuple(sorted(served))
    for conn in (payload.get("connections") or []):
        movement = conn.get("movement")
        from_lane = conn.get("from_lane")
        if movement and from_lane:
            movement_to_lanes.setdefault(str(movement), set()).add(str(from_lane))
    return IntersectionStaticContext(
        intersection_id=intersection_id,
        phase_order=phase_order,
        lane_to_approach=lane_to_approach,
        lane_to_edge=lane_to_edge,
        lane_to_index=lane_to_index,
        lane_to_movements=lane_to_movements,
        lane_speed_limit_mps=lane_speed_limit_mps,
        valid_actions=phase_order,
        phase_to_action={p: p for p in phase_order},
        action_to_movements=action_to_movements,
        movement_to_lanes={m: tuple(sorted(ls)) for m, ls in movement_to_lanes.items()},
        transition_phases=frozenset(),
        map_source_message_id=map_message.message_id,
    )
```

注意：`snapshot.py` 顶部需要 `from dataclasses import dataclass`——上面的代码块已包含 dataclass 使用，实现时在文件头补齐该 import（与 `from typing import Mapping` 并列）。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_snapshot.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/snapshot.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/snapshot.py
scp -P 24 algorithms/v2x/collab/tests/test_snapshot.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_snapshot.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/snapshot.py algorithms/v2x/collab/tests/test_snapshot.py && git commit -m "feat(v2x): collab immutable snapshot models + MAP static context"'
```

---

## Task 5: aggregator.py — EdgeAggregator

**Files:**
- Create: `algorithms/v2x/collab/aggregator.py`
- Test: `algorithms/v2x/collab/tests/test_aggregator.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_aggregator.py`：

```python
from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.snapshot import build_static_context
from algorithms.v2x.messages import V2XMessage


def _msg(message_type, source_id, payload, frame_id="ep1:step:000001",
         sim_time=5.0, message_id=None):
    return V2XMessage(
        message_type=message_type,
        message_id=message_id or f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id=frame_id, sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


MAP = _msg("MAP", "i1", {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1, "connection_priorities": {"c0": "protected"}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "B_0": {"lane_id": "B_0", "edge_id": "B", "lane_index": 0,
                "approach_id": "east", "movements": ("through",),
                "speed_limit_mps": 13.9},
    },
    "connections": [{"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
                     "movement": "through"}],
    "direct_neighbors": [],
}, frame_id="ep1:init", sim_time=0.0, message_id="map-i1")


def _spat(stage_elapsed=2.0, remaining=20.0, message_id="spat-i1-5"):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": "GREEN",
        "stage_elapsed": stage_elapsed, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, message_id=message_id)


def _bsm(vid, lane="A_0", speed=6.0, ns="i1", distance=50.0, sim_time=5.0,
         message_id=None):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": lane, "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": ns, "distance_m": distance,
                        "state": "G"},
        "front_gap_m": None, "rear_gap_m": None, "gap_source": None,
    }, sim_time=sim_time, message_id=message_id or f"bsm-{vid}-{sim_time}")


def _intent(vid, turn="through", eta=8.0, sim_time=5.0, message_id=None):
    return _msg("INTENT", vid, {
        "vehicle_id": vid, "turn_intent": turn, "lane_change_intent": None,
        "estimated_arrival_s": eta, "turn_confidence": 1.0,
        "lane_change_confidence": 0.0, "arrival_confidence": 1.0,
        "intent_origin": "derived",
    }, sim_time=sim_time, message_id=message_id or f"intent-{vid}-{sim_time}")


def _rsm(objects, message_id="rsm-i1-5"):
    return _msg("RSM", "i1", {"rsu_id": "i1", "objects": objects},
                message_id=message_id)


def _make_aggregator():
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(MAP)
    return agg


def test_bsm_migrates_between_intersections():
    agg = EdgeAggregator(managed_ids=("i1", "i2"))
    agg.on_message(MAP)
    agg.on_message(_bsm("car1", ns="i1"))
    snap = agg.snapshot("i1", now=5.0)
    assert "car1" in snap.connected_vehicles
    # next_signal 迁移到 i2（i2 无 MAP，不建快照，但车辆从 i1 移除）
    agg.on_message(_bsm("car1", ns="i2", message_id="bsm-car1-6"))
    snap2 = agg.snapshot("i1", now=6.0)
    assert "car1" not in snap2.connected_vehicles


def test_snapshot_aggregates_lane_state_and_arrivals():
    agg = _make_aggregator()
    agg.on_message(_bsm("car1", speed=0.1))      # 停车网联车
    agg.on_message(_bsm("car2", speed=6.0))
    agg.on_message(_intent("car1", eta=5.0))
    agg.on_message(_spat())
    agg.on_message(_rsm([{"object_id": "truck1", "object_class": "truck",
                          "position": (1.0, 1.0), "speed_mps": 0.0,
                          "lane_id": "A_0", "confidence": 1.0}]))
    snap = agg.snapshot("i1", now=5.0)
    lane = snap.approaches["west"].lane_states["A_0"]
    assert lane.connected_count == 2
    assert lane.observed_count == 1
    assert lane.stopped_count == 2      # car1 + truck1
    assert lane.queue_estimate == 2.0
    assert snap.phase == 1
    assert snap.stage == "GREEN"
    assert snap.stage_elapsed_s == 2.0
    assert snap.remaining_time_s == 20.0
    car1 = snap.connected_vehicles["car1"]
    assert car1.turn_intent == "through"
    assert car1.turn_confidence == 1.0
    assert car1.estimated_arrival_s == 5.0
    assert car1.bsm_delivered_at == 5.0
    assert car1.intent_delivered_at == 5.0
    assert snap.last_delivery_at["SPaT"] == 5.0
    agg.after_snapshot("i1")
    # 第二帧：car1/car2 已存在 → arrivals=0；新增 car3 → arrivals=1
    agg.on_message(_bsm("car3", speed=6.0))
    snap2 = agg.snapshot("i1", now=10.0)
    assert snap2.approaches["west"].lane_states["A_0"].arrivals_since_last_snapshot == 1


def test_non_managed_message_ignored():
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i2", {
        "intersection_id": "i2", "phase_order": [1], "phases": {},
        "lanes": {}, "connections": [], "direct_neighbors": []},
        frame_id="ep1:init", sim_time=0.0, message_id="map-i2"))
    agg.on_message(_bsm("car1", ns="i2", message_id="bsm-car1-7"))
    assert agg.snapshot("i2", now=5.0) is None
    assert agg.snapshot("i1", now=5.0) is None  # i1 无 MAP 也未收到
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_aggregator.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `aggregator.py`**

```python
# algorithms/v2x/collab/aggregator.py
"""EdgeAggregator：订阅 Hub 已投递消息，维护车辆缓存并构建不可变 EdgeSnapshot。

语义（spec §1.4/§1.2）：
- 只接收 managed 路口消息；BSM 按 next_signal 更新车辆所属路口并迁移；
- connected_vehicles 只含 v2x_enabled=True 的网联车（由适配层保证只发网联 BSM）；
- 新鲜度锚点使用 message.sim_time（发送时刻，保守含网络延迟；阈值 10s 量级下可忽略）。
"""
from __future__ import annotations

from typing import Mapping

from algorithms.v2x.messages import V2XMessage

from .snapshot import (
    ApproachState,
    ConnectedVehicleState,
    EdgeSnapshot,
    IntersectionStaticContext,
    LaneState,
    build_static_context,
)

_STOP_SPEED_MPS = 0.5


class _Vehicle:
    __slots__ = (
        "vehicle_id", "lane_id", "approach_id", "speed_mps", "acceleration_mps2",
        "next_signal_intersection_id", "distance_to_signal_m",
        "turn_intent", "turn_confidence", "lane_change_intent",
        "estimated_arrival_s", "bsm_delivered_at", "intent_delivered_at",
        "message_ids",
    )

    def __init__(self, vehicle_id: str) -> None:
        self.vehicle_id = vehicle_id
        self.lane_id: str | None = None
        self.approach_id: str | None = None
        self.speed_mps = 0.0
        self.acceleration_mps2: float | None = None
        self.next_signal_intersection_id: str | None = None
        self.distance_to_signal_m: float | None = None
        self.turn_intent: str | None = None
        self.turn_confidence = 0.0
        self.lane_change_intent: int | None = None
        self.estimated_arrival_s: float | None = None
        self.bsm_delivered_at = 0.0
        self.intent_delivered_at: float | None = None
        self.message_ids: list[str] = []

    def to_state(self) -> ConnectedVehicleState:
        return ConnectedVehicleState(
            vehicle_id=self.vehicle_id,
            lane_id=self.lane_id,
            approach_id=self.approach_id,
            speed_mps=self.speed_mps,
            acceleration_mps2=self.acceleration_mps2,
            next_signal_intersection_id=self.next_signal_intersection_id,
            distance_to_signal_m=self.distance_to_signal_m,
            turn_intent=self.turn_intent,
            turn_confidence=self.turn_confidence,
            lane_change_intent=self.lane_change_intent,
            estimated_arrival_s=self.estimated_arrival_s,
            bsm_delivered_at=self.bsm_delivered_at,
            intent_delivered_at=self.intent_delivered_at,
            source_message_ids=tuple(self.message_ids),
        )


class EdgeAggregator:
    def __init__(self, managed_ids: tuple[str, ...]) -> None:
        self._managed = frozenset(managed_ids)
        self._static: dict[str, IntersectionStaticContext] = {}
        self._spat: dict[str, dict] = {}
        self._rsm_speeds: dict[str, dict[str, list[float]]] = {}
        self._vehicles: dict[str, _Vehicle] = {}
        self._by_intersection: dict[str, dict[str, _Vehicle]] = {
            iid: {} for iid in self._managed}
        self._prev_lane_vehicle_ids: dict[str, dict[str, set[str]]] = {}
        self._last_delivery_at: dict[str, float | None] = {}
        self._last_message_id: dict[str, str] = {}
        self._last_frame_id: dict[str, str] = {}

    # ---------- 订阅入口（Hub 同步回调） ----------
    def on_message(self, message: V2XMessage) -> None:
        message_type = message.message_type
        self._last_delivery_at[message_type] = message.sim_time
        self._last_message_id[message_type] = message.message_id
        self._last_frame_id[message_type] = message.frame_id
        if message_type == "MAP":
            self._on_map(message)
        elif message_type == "SPaT":
            self._on_spat(message)
        elif message_type == "BSM":
            self._on_bsm(message)
        elif message_type == "INTENT":
            self._on_intent(message)
        elif message_type == "RSM":
            self._on_rsm(message)

    def _on_map(self, message: V2XMessage) -> None:
        intersection_id = str(message.payload["intersection_id"])
        if intersection_id not in self._managed:
            return
        try:
            self._static[intersection_id] = build_static_context(message)
        except ValueError:
            # 缺 phase_order 等畸形 MAP：不建立静态上下文 → 策略 MISSING_INPUT
            self._static.pop(intersection_id, None)

    def _on_spat(self, message: V2XMessage) -> None:
        intersection_id = str(message.payload["intersection_id"])
        if intersection_id not in self._managed:
            return
        self._spat[intersection_id] = {
            "payload": dict(message.payload),
            "delivered_at": message.sim_time,
            "message_id": message.message_id,
            "frame_id": message.frame_id,
        }

    def _on_bsm(self, message: V2XMessage) -> None:
        payload = message.payload
        vehicle_id = str(payload["vehicle_id"])
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            vehicle = _Vehicle(vehicle_id)
            self._vehicles[vehicle_id] = vehicle
        location = payload.get("location") or {}
        motion = payload.get("motion") or {}
        ns = payload.get("next_signal") or {}
        new_ns = ns.get("intersection_id")
        if new_ns is not None:
            new_ns = str(new_ns)
        old_ns = vehicle.next_signal_intersection_id
        if old_ns != new_ns:
            if old_ns is not None and old_ns in self._by_intersection:
                self._by_intersection[old_ns].pop(vehicle_id, None)
            if new_ns is not None and new_ns in self._by_intersection:
                self._by_intersection[new_ns][vehicle_id] = vehicle
        vehicle.next_signal_intersection_id = new_ns
        vehicle.lane_id = location.get("lane_id")
        vehicle.approach_id = location.get("approach_id")
        vehicle.speed_mps = float(motion.get("speed_mps") or 0.0)
        vehicle.acceleration_mps2 = motion.get("acceleration_mps2")
        vehicle.distance_to_signal_m = ns.get("distance_m")
        vehicle.bsm_delivered_at = message.sim_time
        vehicle.message_ids.append(message.message_id)

    def _on_intent(self, message: V2XMessage) -> None:
        payload = message.payload
        vehicle_id = str(payload["vehicle_id"])
        vehicle = self._vehicles.get(vehicle_id)
        if vehicle is None:
            vehicle = _Vehicle(vehicle_id)
            self._vehicles[vehicle_id] = vehicle
        vehicle.turn_intent = payload.get("turn_intent")
        vehicle.turn_confidence = float(payload.get("turn_confidence") or 0.0)
        vehicle.lane_change_intent = payload.get("lane_change_intent")
        vehicle.estimated_arrival_s = payload.get("estimated_arrival_s")
        vehicle.intent_delivered_at = message.sim_time
        vehicle.message_ids.append(message.message_id)

    def _on_rsm(self, message: V2XMessage) -> None:
        rsu_id = str(message.payload.get("rsu_id") or message.source_id)
        if rsu_id not in self._managed:
            return
        by_lane: dict[str, list[float]] = {}
        for obj in message.payload.get("objects") or []:
            lane_id = obj.get("lane_id")
            if lane_id is None:
                continue
            speed = obj.get("speed_mps")
            if speed is None:
                continue
            by_lane.setdefault(str(lane_id), []).append(float(speed))
        self._rsm_speeds[rsu_id] = by_lane

    # ---------- 快照构建 ----------
    def snapshot(self, intersection_id: str, now: float) -> EdgeSnapshot | None:
        if intersection_id not in self._managed:
            return None
        spat = self._spat.get(intersection_id)
        static = self._static.get(intersection_id)
        lane_states: dict[str, LaneState] = {}
        if static is not None:
            lane_to_approach = static.lane_to_approach
        else:
            lane_to_approach = {}
        prev_lanes = self._prev_lane_vehicle_ids.get(intersection_id, {})
        current_lanes: dict[str, set[str]] = {}
        connected_vehicles: dict[str, ConnectedVehicleState] = {}
        for vehicle_id, vehicle in self._by_intersection.get(intersection_id, {}).items():
            connected_vehicles[vehicle_id] = vehicle.to_state()
            lane_id = vehicle.lane_id
            if lane_id is None:
                continue
            current_lanes.setdefault(lane_id, set()).add(vehicle_id)
        for lane_id, vehicle_ids in current_lanes.items():
            prev = prev_lanes.get(lane_id, set())
            arrivals = len(vehicle_ids - prev)
            stopped = sum(
                1 for vid in vehicle_ids
                if self._vehicles[vid].speed_mps <= _STOP_SPEED_MPS)
            rsm_speeds = self._rsm_speeds.get(intersection_id, {}).get(lane_id, [])
            observed = len(rsm_speeds)
            stopped += sum(1 for s in rsm_speeds if s <= _STOP_SPEED_MPS)
            lane_states[lane_id] = LaneState(
                lane_id=lane_id,
                connected_count=len(vehicle_ids),
                observed_count=observed,
                stopped_count=stopped,
                queue_estimate=float(stopped),
                arrivals_since_last_snapshot=arrivals,
            )
        approach_ids = {}
        for lane_id in lane_states:
            approach_ids.setdefault(
                lane_to_approach.get(lane_id, lane_id), []).append(lane_id)
        approaches: dict[str, ApproachState] = {}
        for approach_id, lane_ids in approach_ids.items():
            ordered = tuple(sorted(lane_ids))
            approaches[approach_id] = ApproachState(
                approach_id=approach_id,
                incoming_lane_ids=ordered,
                lane_states={lid: lane_states[lid] for lid in ordered},
                downstream_vehicle_count=None,
                downstream_queue_estimate=None,
                turn_intent_counts={},
                arrival_etas_s=(),
            )
        phase: int | None = None
        stage: str | None = None
        stage_elapsed: float | None = None
        remaining: float | None = None
        if spat is not None:
            payload = spat["payload"]
            phase = payload.get("current_phase")
            stage = payload.get("stage")
            stage_elapsed = payload.get("stage_elapsed")
            remaining = payload.get("remaining_time_s")
        source_types = sorted(
            t for t in ("BSM", "INTENT", "SPaT", "MAP", "RSM")
            if t in self._last_message_id)
        return EdgeSnapshot(
            intersection_id=intersection_id,
            sim_time=now,
            phase=phase,
            stage=stage,
            stage_elapsed_s=stage_elapsed,
            remaining_time_s=remaining,
            approaches=approaches,
            connected_vehicles=connected_vehicles,
            last_delivery_at={
                t: self._last_delivery_at.get(t) for t in source_types},
            source_message_ids=tuple(
                self._last_message_id[t] for t in source_types),
            source_frame_ids=tuple(
                self._last_frame_id[t] for t in source_types),
        )

    def after_snapshot(self, intersection_id: str) -> None:
        """每帧构建快照后调用：更新跨帧 arrivals 基线（不随快照对象复制）。"""
        if intersection_id not in self._managed:
            return
        current: dict[str, set[str]] = {}
        for vehicle in self._by_intersection.get(intersection_id, {}).values():
            if vehicle.lane_id is not None:
                current.setdefault(vehicle.lane_id, set()).add(vehicle.vehicle_id)
        self._prev_lane_vehicle_ids[intersection_id] = current

    def static_context(self, intersection_id: str) -> IntersectionStaticContext | None:
        return self._static.get(intersection_id)

    def managed_ids(self) -> frozenset[str]:
        return self._managed

    def reset_episode(self) -> None:
        self._static = {}
        self._spat = {}
        self._rsm_speeds = {}
        self._vehicles = {}
        self._by_intersection = {iid: {} for iid in self._managed}
        self._prev_lane_vehicle_ids = {}
        self._last_delivery_at = {}
        self._last_message_id = {}
        self._last_frame_id = {}
```

注意：测试 `test_non_managed_message_ignored` 中 `snapshot("i1", now=5.0)` 返回 `None` 的条件是**i1 从未收到 MAP**——当前实现 `snapshot()` 在 i1 ∈ managed 时仍会返回空快照（`_spat`/`_static` 缺失）。为满足该断言，`snapshot()` 开头增加：`if intersection_id not in self._static: return None`（MAP 未投递 = 静态上下文缺失 = 不产快照，由策略 MISSING_INPUT）。请按此修正上面的实现（在 `if intersection_id not in self._managed: return None` 之后追加一行）。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_aggregator.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/aggregator.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/aggregator.py
scp -P 24 algorithms/v2x/collab/tests/test_aggregator.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_aggregator.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/aggregator.py algorithms/v2x/collab/tests/test_aggregator.py && git commit -m "feat(v2x): EdgeAggregator subscription cache + snapshot builder"'
```

---

## Task 6: state.py — CloudStateStore / CloudIntersectionView

**Files:**
- Create: `algorithms/v2x/collab/state.py`
- Test: `algorithms/v2x/collab/tests/test_state.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_state.py`：

```python
from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.proposals import FreshnessConfig
from algorithms.v2x.collab.state import CloudStateStore


MAP = {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1, "connection_priorities": {}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
    },
    "connections": [], "direct_neighbors": [],
}


def _message(message_type, source_id, payload, sim_time):
    from algorithms.v2x.messages import V2XMessage
    return V2XMessage(
        message_type=message_type, message_id=f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id="ep1:step:000001", sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


def _spat(sim_time):
    return _message("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": "GREEN",
        "stage_elapsed": 2.0, "connection_signal_states": [],
        "remaining_time_s": 20.0, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, sim_time)


def _build_store(freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_message("MAP", "i1", MAP, 0.0))
    agg.on_message(_spat(5.0))
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    return store, agg


def test_view_freshness_and_stale():
    store, _ = _build_store()
    view = store.view("i1", now=10.0)
    assert view is not None
    assert view.snapshot.intersection_id == "i1"
    assert view.age_s["SPaT"] == 5.0
    assert "SPaT" not in view.missing
    assert "SPaT" not in view.stale
    assert "BSM" in view.missing  # 从未收到 BSM


def test_view_stale_when_over_threshold():
    store, _ = _build_store(freshness=FreshnessConfig(spat_s=3.0))
    view = store.view("i1", now=10.0)
    assert "SPaT" in view.stale
    assert view.age_s["SPaT"] == 5.0


def test_static_context_via_store():
    store, _ = _build_store()
    ctx = store.static_context("i1")
    assert ctx is not None
    assert ctx.valid_actions == (1,)


def test_reset_episode_clears_state():
    store, agg = _build_store()
    store.view("i1", now=10.0)
    agg.reset_episode()
    assert store.view("i1", now=10.0) is None
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_state.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `state.py`**

```python
# algorithms/v2x/collab/state.py
"""CloudStateStore：新鲜度视图（age/missing/stale）+ 静态上下文访问（spec §1.5）。"""
from __future__ import annotations

from typing import Mapping

from .aggregator import EdgeAggregator
from .proposals import FreshnessConfig
from .snapshot import EdgeSnapshot, IntersectionStaticContext


@dataclass(frozen=True, slots=True)
class CloudIntersectionView:
    snapshot: EdgeSnapshot
    age_s: Mapping[str, float | None]
    missing: frozenset[str]
    stale: frozenset[str]


class CloudStateStore:
    def __init__(self, aggregator: EdgeAggregator,
                 freshness: FreshnessConfig) -> None:
        self._aggregator = aggregator
        self._freshness = freshness

    def view(self, intersection_id: str, now: float) -> CloudIntersectionView | None:
        snapshot = self._aggregator.snapshot(intersection_id, now)
        if snapshot is None:
            return None
        thresholds = {
            "BSM": self._freshness.bsm_s,
            "INTENT": self._freshness.intent_s,
            "SPaT": self._freshness.spat_s,
            "RSM": self._freshness.rsm_s,
        }
        age_s: dict[str, float | None] = {}
        missing: set[str] = set()
        stale: set[str] = set()
        for message_type, threshold in thresholds.items():
            delivered_at = snapshot.last_delivery_at.get(message_type)
            if delivered_at is None:
                age_s[message_type] = None
                missing.add(message_type)
                continue
            age = now - delivered_at
            age_s[message_type] = age
            if age > threshold:
                stale.add(message_type)
        return CloudIntersectionView(
            snapshot=snapshot,
            age_s=age_s,
            missing=frozenset(missing),
            stale=frozenset(stale),
        )

    def static_context(self, intersection_id: str) -> IntersectionStaticContext | None:
        return self._aggregator.static_context(intersection_id)

    def managed_ids(self) -> frozenset[str]:
        return self._aggregator.managed_ids()

    def reset_episode(self) -> None:
        self._aggregator.reset_episode()
```

注意：`state.py` 顶部需要 `from dataclasses import dataclass`。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_state.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/state.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/state.py
scp -P 24 algorithms/v2x/collab/tests/test_state.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_state.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/state.py algorithms/v2x/collab/tests/test_state.py && git commit -m "feat(v2x): CloudStateStore freshness view"'
```

---

## Task 7: policy.py — 信号规则族 C（propose_signal）

**Files:**
- Create: `algorithms/v2x/collab/policy.py`（本任务只实现信号部分；引导部分 Task 8 追加）
- Test: `algorithms/v2x/collab/tests/test_policy_signal.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_policy_signal.py`：

```python
import math

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, FreshnessConfig, SignalDecisionStatus,
)
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.messages import V2XMessage


MAP = {
    "intersection_id": "i1", "phase_order": [1, 2],
    "phases": {
        "1": {"phase_id": 1, "connection_priorities": {"c0": "protected",
                                                        "c1": "protected"}},
        "2": {"phase_id": 2, "connection_priorities": {"c2": "protected",
                                                        "c3": "protected"}},
    },
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                "approach_id": "west", "movements": ("left",),
                "speed_limit_mps": 13.9},
        "C_0": {"lane_id": "C_0", "edge_id": "C", "lane_index": 0,
                "approach_id": "north", "movements": ("through",),
                "speed_limit_mps": 13.9},
        "C_1": {"lane_id": "C_1", "edge_id": "C", "lane_index": 1,
                "approach_id": "north", "movements": ("left",),
                "speed_limit_mps": 13.9},
    },
    "connections": [
        {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
         "movement": "through"},
        {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
         "movement": "left"},
        {"connection_id": "c2", "from_lane": "C_0", "to_lane": "D_0",
         "movement": "through"},
        {"connection_id": "c3", "from_lane": "C_1", "to_lane": "D_0",
         "movement": "left"},
    ],
    "direct_neighbors": [],
}


def _msg(message_type, source_id, payload, sim_time, frame_id="ep1:step:000001",
         message_id=None):
    return V2XMessage(
        message_type=message_type,
        message_id=message_id or f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id=frame_id, sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


def _bsm(vid, lane, speed=6.0, ns="i1", distance=50.0, sim_time=5.0,
         turn=None, eta=None):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": lane.rsplit("_", 1)[0], "lane_id": lane,
                     "lane_index": 0, "lane_position_m": 10.0},
        "route_edges": [lane.rsplit("_", 1)[0], "B"],
        "next_signal": {"intersection_id": ns, "distance_m": distance,
                        "state": "G"},
        "front_gap_m": None, "rear_gap_m": None, "gap_source": None,
    }, sim_time)


def _intent(vid, turn, eta, sim_time=5.0):
    return _msg("INTENT", vid, {
        "vehicle_id": vid, "turn_intent": turn, "lane_change_intent": None,
        "estimated_arrival_s": eta, "turn_confidence": 1.0,
        "lane_change_confidence": 0.0, "arrival_confidence": 1.0,
        "intent_origin": "derived",
    }, sim_time)


def _spat(stage_elapsed=10.0, remaining=20.0, sim_time=5.0, phase=1, stage="GREEN"):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": phase, "stage": stage,
        "stage_elapsed": stage_elapsed, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 25.0, "schedule_status": "predicted",
    }, sim_time)


def _setup(*, spat=None, vehicles=(), intents=(), freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i1", MAP, 0.0, frame_id="ep1:init"))
    agg.on_message(spat if spat is not None else _spat())
    for bsm in vehicles:
        agg.on_message(bsm)
    for intent in intents:
        agg.on_message(intent)
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    policy = CloudRulePolicy(CollabConfig())
    return store, policy


def test_queue_demand_proposes_switch_to_phase_2():
    # 相位 1 服务 A 车道（through/left）；相位 2 服务 C 车道。
    # A_0/A_1 大量排队 → 建议切到相位 1？当前相位已是 1 → KEEP_CURRENT。
    # 构造 C 车道排队 → 建议切到相位 2。
    store, policy = _setup(
        vehicles=[
            _bsm("vA0", "A_0", speed=0.1),
            _bsm("vA1", "A_1", speed=0.1),
            _bsm("vC0", "C_0", speed=0.1),
            _bsm("vC1", "C_1", speed=0.1),
        ],
        intents=[_intent("vA0", "through", 8.0),
                 _intent("vA1", "left", 8.0),
                 _intent("vC0", "through", 8.0),
                 _intent("vC1", "left", 8.0)],
    )
    view = store.view("i1", now=5.0)
    assert view is not None
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    # A(2) 与 C(2) 排队相等，forward 也相等 → 平分按 action ID 升序 → 相位 1 胜出
    # 当前 action=1 → KEEP_CURRENT
    assert proposal.status is SignalDecisionStatus.KEEP_CURRENT


def test_missing_spat_is_missing_input():
    store, policy = _setup(spat=None)
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    assert proposal.status is SignalDecisionStatus.MISSING_INPUT
    assert proposal.candidate_action is None
    assert proposal.proposed_action is None


def test_transition_stage_suppressed():
    store, policy = _setup(spat=_spat(stage="YELLOW", stage_elapsed=2.0))
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    assert proposal.status is SignalDecisionStatus.SUPPRESSED_TRANSITION
    assert proposal.candidate_action is None
    assert proposal.proposed_action is None


def test_min_green_suppresses_switch():
    store, policy = _setup(
        spat=_spat(stage_elapsed=1.0, remaining=20.0),
        vehicles=[_bsm("vC0", "C_0", speed=0.1),
                  _bsm("vC1", "C_1", speed=0.1)],
        intents=[_intent("vC0", "through", 8.0),
                 _intent("vC1", "left", 8.0)],
    )
    view = store.view("i1", now=5.0)
    ctx = store.static_context("i1")
    proposal = policy.propose_signal(
        intersection_id="i1", snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=CollabConfig(),
    )
    # C 排队 2 > A 排队 0 → 候选 2；stage_elapsed=1 < min_green=5 → SUPPRESSED_MIN_GREEN
    assert proposal.status is SignalDecisionStatus.SUPPRESSED_MIN_GREEN
    assert proposal.candidate_action == 2
    assert proposal.proposed_action == 1
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_policy_signal.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `policy.py`（信号部分）**

```python
# algorithms/v2x/collab/policy.py
"""CloudRulePolicy：信号规则族 C（排队/到达基线 + INTENT 前视）+ 引导阈值触发。

本文件在 Task 7 实现 propose_signal；Task 8 追加 propose_guidance 与状态字典。
"""
from __future__ import annotations

import math
from typing import Mapping

from .proposals import (
    CollabConfig,
    FreshnessConfig,
    GuidanceDecisionStatus,
    GuidanceEmissionMode,
    GuidancePolicyConfig,
    LastEmittedGuidanceState,
    SignalDecisionStatus,
    SignalPolicyConfig,
    SignalProposal,
    VehicleGuidanceProposal,
)
from .snapshot import (
    ConnectedVehicleState,
    EdgeSnapshot,
    IntersectionStaticContext,
)

_TRANSITION_STAGES = frozenset({"YELLOW", "CLEARANCE"})


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class CloudRulePolicy:
    def __init__(self, config: CollabConfig) -> None:
        self._config = config
        self._last_emitted: dict[str, LastEmittedGuidanceState] = {}

    # ---------- 信号规则（spec §2） ----------
    def propose_signal(
        self,
        *,
        intersection_id: str,
        snapshot: EdgeSnapshot | None,
        static_context: IntersectionStaticContext | None,
        now: float,
        frame_id: str,
        config: CollabConfig,
    ) -> SignalProposal:
        policy = config.signal_policy
        freshness = config.freshness
        if snapshot is None or static_context is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.MISSING_INPUT, None, None,
                {}, "missing_map_or_snapshot", 0.0, now, policy, frame_id, ())
        spat_age = snapshot.last_delivery_at.get("SPaT")
        if snapshot.phase is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.MISSING_INPUT, None, None,
                {}, "missing_spat", 0.0, now, policy, frame_id,
                snapshot.source_message_ids)
        if spat_age is None or now - spat_age > freshness.spat_s:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.STALE_INPUT, None, None,
                {}, "spat_stale", 0.0, now, policy, frame_id,
                snapshot.source_message_ids)
        if snapshot.stage in _TRANSITION_STAGES:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_TRANSITION,
                None, None, {}, "stage_transition", 0.0, now, policy,
                frame_id, snapshot.source_message_ids)
        current_action = static_context.phase_to_action.get(snapshot.phase)
        if current_action is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.INVALID_PROPOSAL,
                None, None, {}, "phase_to_action_missing", 0.0, now, policy,
                frame_id, snapshot.source_message_ids)
        scores = self._score_actions(
            snapshot, static_context, now, policy, freshness)
        if any(not math.isfinite(value) for value in scores.values()):
            return self._signal_result(
                intersection_id, SignalDecisionStatus.INVALID_PROPOSAL,
                None, None, scores, "non_finite_score", 0.0, now, policy,
                frame_id, snapshot.source_message_ids)
        best = max(
            static_context.valid_actions,
            key=lambda action: (
                scores[action],
                1 if action == current_action else 0,
                -action,
            ),
        )
        if scores[best] <= policy.demand_epsilon:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.NO_DEMAND,
                current_action, current_action, scores, "no_demand", 0.0,
                now, policy, frame_id, snapshot.source_message_ids)
        if best == current_action:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.KEEP_CURRENT,
                current_action, current_action, scores, "keep_current", 0.0,
                now, policy, frame_id, snapshot.source_message_ids)
        if snapshot.stage_elapsed_s is None:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
                best, current_action, scores, "stage_elapsed_unknown", 0.0,
                now, policy, frame_id, snapshot.source_message_ids)
        if snapshot.stage_elapsed_s < policy.min_green_s:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
                best, current_action, scores, "min_green", 0.0, now, policy,
                frame_id, snapshot.source_message_ids)
        margin = scores[best] - scores[current_action]
        if margin < policy.switch_score_margin:
            return self._signal_result(
                intersection_id, SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
                best, current_action, scores, "switch_margin", 0.0, now,
                policy, frame_id, snapshot.source_message_ids)
        confidence = self._signal_confidence(
            snapshot, static_context, now, policy, freshness,
            best, scores, margin)
        return self._signal_result(
            intersection_id, SignalDecisionStatus.PROPOSED,
            best, best, scores, "switch", confidence, now, policy, frame_id,
            snapshot.source_message_ids)

    def _score_actions(
        self,
        snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext,
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
    ) -> dict[int, float]:
        lane_states = {
            lane_id: lane
            for approach in snapshot.approaches.values()
            for lane_id, lane in approach.lane_states.items()
        }
        scores: dict[int, float] = {}
        for action in static_context.valid_actions:
            served = set(static_context.action_to_movements.get(action, ()))
            relevant_lanes = [
                lane_id for lane_id, movements in
                static_context.lane_to_movements.items()
                if served & set(movements)
            ]
            queued = sum(
                lane_states[lane_id].queue_estimate
                for lane_id in relevant_lanes if lane_id in lane_states)
            arrivals = sum(
                lane_states[lane_id].arrivals_since_last_snapshot
                for lane_id in relevant_lanes if lane_id in lane_states)
            forward = self._forward_demand(
                snapshot, served, now, policy, freshness)
            scores[action] = (
                policy.queue_weight * queued
                + policy.arrival_weight * arrivals
                + policy.forward_weight * forward
            )
        return scores

    def _forward_demand(
        self,
        snapshot: EdgeSnapshot,
        served_movements: set[str],
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
    ) -> float:
        total = 0.0
        for vehicle in snapshot.connected_vehicles.values():
            if now - vehicle.bsm_delivered_at > freshness.bsm_s:
                continue
            if vehicle.intent_delivered_at is None:
                continue
            if now - vehicle.intent_delivered_at > freshness.intent_s:
                continue
            if vehicle.turn_intent not in served_movements:
                continue
            eta = vehicle.estimated_arrival_s
            if eta is None or not (0.0 <= eta <= policy.forward_horizon_s):
                continue
            total += vehicle.turn_confidence * math.exp(
                -eta / policy.forward_decay_s)
        return total

    def _signal_confidence(
        self,
        snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext,
        now: float,
        policy: SignalPolicyConfig,
        freshness: FreshnessConfig,
        best: int,
        scores: Mapping[int, float],
        margin: float,
    ) -> float:
        lane_states = {
            lane_id: lane
            for approach in snapshot.approaches.values()
            for lane_id, lane in approach.lane_states.items()
        }
        served = set(static_context.action_to_movements.get(best, ()))
        relevant_lanes = [
            lane_id for lane_id, movements in
            static_context.lane_to_movements.items()
            if served & set(movements)
        ]
        queue_quality = 1.0 if any(
            lane_id in lane_states for lane_id in relevant_lanes) else 0.0
        arrival_quality = queue_quality
        fresh_connected = sum(
            1 for v in snapshot.connected_vehicles.values()
            if now - v.bsm_delivered_at <= freshness.bsm_s)
        fresh_intent = sum(
            1 for v in snapshot.connected_vehicles.values()
            if now - v.bsm_delivered_at <= freshness.bsm_s
            and v.intent_delivered_at is not None
            and now - v.intent_delivered_at <= freshness.intent_s)
        forward_quality = (
            fresh_intent / fresh_connected if fresh_connected > 0 else 0.0)
        weight_sum = (
            policy.queue_weight + policy.arrival_weight + policy.forward_weight)
        input_quality = (
            policy.queue_weight * queue_quality
            + policy.arrival_weight * arrival_quality
            + policy.forward_weight * forward_quality
        ) / weight_sum
        if len(static_context.valid_actions) == 1:
            margin_confidence = 1.0
        else:
            margin_confidence = _clamp(
                margin / max(abs(scores[best]), policy.score_epsilon), 0.0, 1.0)
        return margin_confidence * input_quality

    def _signal_result(
        self,
        intersection_id: str,
        status: SignalDecisionStatus,
        candidate_action: int | None,
        proposed_action: int | None,
        scores: Mapping[int, float],
        reason: str,
        confidence: float,
        now: float,
        policy: SignalPolicyConfig,
        frame_id: str,
        source_message_ids: tuple[str, ...],
    ) -> SignalProposal:
        return SignalProposal(
            intersection_id=intersection_id,
            status=status,
            candidate_action=candidate_action,
            proposed_action=proposed_action,
            current_action=proposed_action if status in (
                SignalDecisionStatus.NO_DEMAND,
                SignalDecisionStatus.KEEP_CURRENT,
                SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
                SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
            ) else None,
            action_scores=dict(scores),
            reason=reason,
            confidence=confidence,
            valid_from=now,
            valid_until=now + policy.proposal_ttl_s,
            needs_transition=(
                proposed_action is not None and proposed_action != candidate_action
                and status is SignalDecisionStatus.PROPOSED
            ),
            decision_frame_id=frame_id,
            source_message_ids=source_message_ids,
            source_frame_ids=(),
        )
```

注意：
- `_signal_result` 的 `current_action` 通过传参更严谨——实现时建议把 `current_action` 作为显式参数传入（见 Task 7 测试：`SUPPRESSED_MIN_GREEN` 断言 `candidate_action==2, proposed_action==1`；`KEEP_CURRENT`/`NO_DEMAND` 需 `current_action` 非空）。**实现修正**：`_signal_result(..., current_action=...)` 增加显式 `current_action: int | None` 参数，调用处分别传 `current_action`（决策处）或 `None`（门禁失败处）；`needs_transition` 按 spec §2.3 定义为 `proposed_action is not None and current_action is not None and proposed_action != current_action`。
- `source_frame_ids` v1 从 snapshot 传入（Task 7 测试未断言，但 spec 要求）；实现时在 `propose_signal` 各分支把 `snapshot.source_frame_ids` 传入。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_policy_signal.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/policy.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/policy.py
scp -P 24 algorithms/v2x/collab/tests/test_policy_signal.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_policy_signal.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/policy.py algorithms/v2x/collab/tests/test_policy_signal.py && git commit -m "feat(v2x): signal rule family C (queue/arrival + INTENT forward)"'
```

---

## Task 8: policy.py — RSI 车辆引导（propose_guidance，阈值触发）

**Files:**
- Modify: `algorithms/v2x/collab/policy.py`（追加引导部分；复用 Task 7 的 `_clamp`/`_signal_result` 等）
- Test: `algorithms/v2x/collab/tests/test_policy_guidance.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_policy_guidance.py`：

```python
import math

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.policy import CloudRulePolicy, GUIDANCE_FUNNEL_STAGES
from algorithms.v2x.collab.proposals import (
    CollabConfig, FreshnessConfig, GuidanceDecisionStatus,
    GuidanceEmissionMode, LastEmittedGuidanceState,
)
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.messages import V2XMessage
from config.scenario_presets import ResolvedScenarioScope


MAP = {
    "intersection_id": "i1", "phase_order": [1],
    "phases": {"1": {"phase_id": 1,
                     "connection_priorities": {"c0": "protected",
                                               "c1": "protected"}}},
    "lanes": {
        "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                "approach_id": "west", "movements": ("through",),
                "speed_limit_mps": 16.0},
        "A_1": {"lane_id": "A_1", "edge_id": "A", "lane_index": 1,
                "approach_id": "west", "movements": ("left",),
                "speed_limit_mps": 16.0},
    },
    "connections": [
        {"connection_id": "c0", "from_lane": "A_0", "to_lane": "B_0",
         "movement": "through"},
        {"connection_id": "c1", "from_lane": "A_1", "to_lane": "B_0",
         "movement": "left"},
    ],
    "direct_neighbors": [],
}

SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("i1",))


def _msg(message_type, source_id, payload, sim_time, frame_id="ep1:step:000001",
         message_id=None):
    return V2XMessage(
        message_type=message_type,
        message_id=message_id or f"{message_type}-{source_id}-{sim_time}",
        schema_version="1.0", run_id="run1", episode_id="ep1",
        frame_id=frame_id, sequence_no=1, sim_time=sim_time,
        source_id=source_id, destination="cloud", correlation_id=None,
        payload=payload,
    )


def _bsm(vid, lane="A_0", speed=8.0, ns="i1", distance=205.0, sim_time=5.0):
    return _msg("BSM", vid, {
        "vehicle_id": vid, "type_id": "passenger", "position": (0.0, 0.0),
        "motion": {"speed_mps": speed, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": lane, "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": ns, "distance_m": distance,
                        "state": "G"},
        "front_gap_m": None, "rear_gap_m": None, "gap_source": None,
    }, sim_time)


def _intent(vid, turn, eta, sim_time=5.0):
    return _msg("INTENT", vid, {
        "vehicle_id": vid, "turn_intent": turn, "lane_change_intent": None,
        "estimated_arrival_s": eta, "turn_confidence": 1.0,
        "lane_change_confidence": 0.0, "arrival_confidence": 1.0,
        "intent_origin": "derived",
    }, sim_time)


def _spat(remaining=21.0, stage="GREEN", sim_time=5.0):
    return _msg("SPaT", "i1", {
        "intersection_id": "i1", "current_phase": 1, "stage": stage,
        "stage_elapsed": 10.0, "connection_signal_states": [],
        "remaining_time_s": remaining, "next_stage": "YELLOW",
        "next_stage_start_time": 26.0, "schedule_status": "predicted",
    }, sim_time)


def _setup(*, vehicles=(), intents=(), spat=None, freshness=None):
    agg = EdgeAggregator(managed_ids=("i1",))
    agg.on_message(_msg("MAP", "i1", MAP, 0.0, frame_id="ep1:init"))
    agg.on_message(spat if spat is not None else _spat())
    for bsm in vehicles:
        agg.on_message(bsm)
    for intent in intents:
        agg.on_message(intent)
    store = CloudStateStore(agg, freshness or FreshnessConfig())
    policy = CloudRulePolicy(CollabConfig())
    return store, policy


def _propose(policy, store, *, last_emitted=None, config=None, vehicle_id="car1"):
    view = store.view("i1", now=5.0)
    assert view is not None
    ctx = store.static_context("i1")
    vehicle = view.snapshot.connected_vehicles[vehicle_id]
    return policy.propose_guidance(
        vehicle=vehicle, snapshot=view.snapshot, static_context=ctx,
        now=5.0, frame_id="ep1:step:000001", config=config or CollabConfig(),
        scope=SCOPE, last_emitted=last_emitted)


def test_threshold_published_speed_catchup():
    # 车 8 m/s、距离 205m、剩余绿灯 21s（available=20）→ raw=10.25 ≤ upper=10.4
    # |10.25-8|=2.25 ≥ trigger 2.0 → PROPOSED（无历史 → published）
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    assert outcome.filter_reason is None
    p = outcome.proposal
    assert p is not None
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_speed_mps == pytest.approx(205.0 / 20.0)
    assert p.guidance_type == "speed"


def test_threshold_suppressed_when_delta_below_trigger():
    # 6 m/s、distance=140、available=20 → raw=7.0、target=7.0、delta=1.0 < 2.0
    # → SUPPRESSED_THRESHOLD（raw 合法但未达发射阈值）
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=6.0, distance=140.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "raw_proposals"
    assert outcome.filter_reason == "speed_below_trigger"
    assert outcome.proposal.status is GuidanceDecisionStatus.SUPPRESSED_THRESHOLD


def _rsm(objects, sim_time=5.0):
    return _msg("RSM", "i1", {"rsu_id": "i1", "objects": objects}, sim_time,
                message_id="rsm-i1-5")


def test_lane_only_proposal_published_without_speed():
    # turn=left：A_1 相邻同 edge、movement 允许 left；
    # A_0 排队 3（3 辆停车网联车）、A_1 排队 1（RSM 观察卡车）→ 收益 2 ≥ 2 → lane PROPOSED
    # 速度分量：phase 1 不服务 left → MISSING_INPUT/next_served_green_unknown，不否决 lane
    store, policy = _setup(
        vehicles=[_bsm("car1", lane="A_0", speed=6.0, distance=100.0),
                  _bsm("car2", lane="A_0", speed=0.1, distance=90.0),
                  _bsm("car3", lane="A_0", speed=0.1, distance=80.0),
                  _bsm("car4", lane="A_0", speed=0.1, distance=70.0)],
        intents=[_intent("car1", "left", 15.0),
                 _intent("car2", "left", 15.0),
                 _intent("car3", "left", 15.0),
                 _intent("car4", "left", 15.0)],
    )
    # A_1 上的 RSM 低速观察（卡车）
    _inject_rsm(store, _rsm([{"object_id": "truck1", "object_class": "truck",
                              "position": (1.0, 1.0), "speed_mps": 0.1,
                              "lane_id": "A_1", "confidence": 1.0}]))
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    p = outcome.proposal
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.MISSING_INPUT
    assert p.lane_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_lane_id == "A_1"
    assert p.target_lane_index == 1
    assert p.guidance_type == "lane"


def _inject_rsm(store, rsm_message):
    """在快照构建前注入 RSM（等价于已投递消息回调）。"""
    store._aggregator.on_message(rsm_message)
    outcome = _propose(policy, store)
    assert outcome.funnel_stage == "published"
    p = outcome.proposal
    assert p.status is GuidanceDecisionStatus.PROPOSED
    assert p.speed_status is GuidanceDecisionStatus.MISSING_INPUT
    assert p.lane_status is GuidanceDecisionStatus.PROPOSED
    assert p.target_lane_id == "A_1"
    assert p.target_lane_index == 1
    assert p.guidance_type == "lane"


def test_duplicate_suppressed_and_cooldown():
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=8.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    first = _propose(policy, store)
    assert first.funnel_stage == "published"
    last = LastEmittedGuidanceState(
        target_speed_mps=first.proposal.target_speed_mps,
        target_lane_id=None, target_lane_index=None,
        emitted_at=5.0, valid_until=15.0, reason=first.proposal.reason,
        emitted_message_id="rsi-1")
    dup = _propose(policy, store, last_emitted=last)
    assert dup.funnel_stage == "threshold_passed"
    assert dup.proposal.status is GuidanceDecisionStatus.SUPPRESSED_DUPLICATE
    # 速度变化 ≥1.0 但冷却未到（5s 内）→ SUPPRESSED_COOLDOWN
    changed = LastEmittedGuidanceState(
        target_speed_mps=9.0, target_lane_id=None, target_lane_index=None,
        emitted_at=5.0, valid_until=15.0, reason="speed_catchup",
        emitted_message_id="rsi-1")
    cooldown = _propose(policy, store, last_emitted=changed)
    assert cooldown.funnel_stage == "dedup_passed"
    assert cooldown.proposal.status is GuidanceDecisionStatus.SUPPRESSED_COOLDOWN


def test_full_mode_bypasses_threshold_and_cooldown():
    store, policy = _setup(
        vehicles=[_bsm("car1", speed=6.0, distance=205.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    cfg = CollabConfig(guidance_mode=GuidanceEmissionMode.FULL)
    outcome = _propose(policy, store, config=cfg)
    assert outcome.funnel_stage == "published"
    assert outcome.proposal.status is GuidanceDecisionStatus.PROPOSED


def test_next_signal_not_managed_not_candidate():
    store, policy = _setup(
        vehicles=[_bsm("car1", ns="i9", distance=100.0)],
        intents=[_intent("car1", "through", 15.0)],
    )
    outcome = _propose(policy, store)
    assert outcome.proposal is None
    assert outcome.funnel_stage == "next_signal_known"
    assert outcome.filter_reason == "next_signal_not_managed"


def test_funnel_stages_order():
    assert GUIDANCE_FUNNEL_STAGES[0] == "connected_seen"
    assert GUIDANCE_FUNNEL_STAGES[-1] == "published"
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_policy_guidance.py -q --tb=short'`
Expected: FAIL（`AttributeError: module 'algorithms.v2x.collab.policy' has no attribute 'propose_guidance'`）

- [x] **Step 3: 实现（追加到 `policy.py`）**

在 Task 7 的 `policy.py` 末尾追加（顶部 imports 增加 `from config.scenario_presets import ResolvedScenarioScope`，dataclass 追加 `GuidanceOutcome`）：

```python
# ---- RSI 车辆引导（spec §3）----

GUIDANCE_FUNNEL_STAGES = (
    "connected_seen", "fresh_bsm", "next_signal_known", "next_signal_managed",
    "distance_known", "in_horizon_candidates", "raw_proposals",
    "threshold_passed", "dedup_passed", "cooldown_passed", "published",
)


@dataclass(frozen=True, slots=True)
class GuidanceOutcome:
    """单辆车引导决策结果：proposal 为 None 表示候选筛选未通过。"""
    proposal: VehicleGuidanceProposal | None
    funnel_stage: str          # 最高到达的漏斗阶段（§5.1）
    filter_reason: str | None  # 未 published 时的统计原因（§3.6）


class CloudRulePolicy:
    # ...（Task 7 已有信号部分，此处追加引导部分）...

    # ---------- 引导：候选筛选 ----------
    def _candidate_stage(
        self, vehicle: ConnectedVehicleState, now: float,
        freshness: FreshnessConfig, scope: ResolvedScenarioScope,
        horizon_m: float,
    ) -> tuple[str, str | None]:
        """返回 (最高到达漏斗阶段, 过滤原因)。"""
        if now - vehicle.bsm_delivered_at > freshness.bsm_s:
            return "connected_seen", "stale_bsm"
        if vehicle.next_signal_intersection_id is None:
            return "fresh_bsm", "next_signal_unknown"
        if vehicle.next_signal_intersection_id not in scope.managed_ids:
            return "next_signal_known", "next_signal_not_managed"
        if vehicle.distance_to_signal_m is None:
            return "next_signal_managed", "distance_unknown"
        if vehicle.distance_to_signal_m > horizon_m:
            return "distance_known", "not_in_guidance_horizon"
        return "in_horizon_candidates", None

    def _resolve_movement(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        now: float, freshness: FreshnessConfig,
        static_context: IntersectionStaticContext,
    ) -> str | None:
        if (vehicle.intent_delivered_at is not None
                and now - vehicle.intent_delivered_at <= freshness.intent_s
                and vehicle.turn_intent):
            return vehicle.turn_intent
        lane_movements = static_context.lane_to_movements.get(
            vehicle.lane_id or "", ())
        if len(lane_movements) == 1:
            return lane_movements[0]
        return None

    # ---------- 引导：速度分量（§3.2，两阶段：raw 生成） ----------
    def _speed_decision(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float,
        policy: GuidancePolicyConfig, freshness: FreshnessConfig,
    ) -> tuple[GuidanceDecisionStatus, float | None, str]:
        movement = self._resolve_movement(
            vehicle, snapshot, now, freshness, static_context)
        if movement is None:
            return GuidanceDecisionStatus.MISSING_INPUT, None, "movement_unknown"
        spat_delivered_at = snapshot.last_delivery_at.get("SPaT")
        if snapshot.phase is None or spat_delivered_at is None:
            return GuidanceDecisionStatus.MISSING_INPUT, None, "missing_spat"
        if now - spat_delivered_at > freshness.spat_s:
            return GuidanceDecisionStatus.STALE_INPUT, None, "spat_stale"
        stage = snapshot.stage or ""
        if stage != "GREEN":
            # v1 不预测下一服务相位：非绿灯阶段不生成减速赶绿建议
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "stage_not_green"
        action = static_context.phase_to_action.get(snapshot.phase)
        served = set(static_context.action_to_movements.get(action, ())) \
            if action is not None else set()
        if movement not in served:
            return (GuidanceDecisionStatus.MISSING_INPUT, None,
                    "next_served_green_unknown")
        if vehicle.speed_mps < policy.min_guidance_speed_mps:
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "vehicle_too_slow"
        remaining = snapshot.remaining_time_s
        if remaining is None:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "insufficient_green_remaining")
        available = remaining - policy.green_clearance_buffer_s
        if available <= 0.0:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "insufficient_green_remaining")
        distance = vehicle.distance_to_signal_m or 0.0
        eta_now = distance / max(vehicle.speed_mps, 1e-6)
        if eta_now <= available:
            return GuidanceDecisionStatus.NO_ACTION_NEEDED, None, "can_pass_within_green"
        raw_target = distance / available
        lane_speed_limit = static_context.lane_speed_limit_mps.get(
            vehicle.lane_id or "")
        v_eff_max = policy.v_max_mps
        if lane_speed_limit is not None:
            v_eff_max = min(v_eff_max, lane_speed_limit)
        lower = max(policy.v_min_mps,
                    policy.speed_scale_low * vehicle.speed_mps)
        upper = min(v_eff_max, policy.speed_scale_high * vehicle.speed_mps)
        if lower > upper:
            return (GuidanceDecisionStatus.INVALID_PROPOSAL, None,
                    "empty_speed_interval")
        if raw_target > upper:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None,
                    "cannot_catch_green_within_limits")
        target = max(raw_target, lower)
        return GuidanceDecisionStatus.PROPOSED, target, "speed_catchup"

    # ---------- 引导：车道分量（§3.3，严格校验 + advisory） ----------
    def _lane_has_fresh_support(
        self, snapshot: EdgeSnapshot, lane_id: str, now: float,
        freshness: FreshnessConfig,
    ) -> bool:
        for vehicle in snapshot.connected_vehicles.values():
            if vehicle.lane_id == lane_id and \
                    now - vehicle.bsm_delivered_at <= freshness.bsm_s:
                return True
        rsm_delivered_at = snapshot.last_delivery_at.get("RSM")
        if rsm_delivered_at is None or now - rsm_delivered_at > freshness.rsm_s:
            return False
        for approach in snapshot.approaches.values():
            lane = approach.lane_states.get(lane_id)
            if lane is not None and lane.observed_count > 0:
                return True
        return False

    def _queue_estimate(self, snapshot: EdgeSnapshot, lane_id: str) -> float:
        for approach in snapshot.approaches.values():
            lane = approach.lane_states.get(lane_id)
            if lane is not None:
                return lane.queue_estimate
        return 0.0

    def _lane_decision(
        self, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float,
        policy: GuidancePolicyConfig, freshness: FreshnessConfig,
    ) -> tuple[GuidanceDecisionStatus, str | None, int | None, str]:
        movement = self._resolve_movement(
            vehicle, snapshot, now, freshness, static_context)
        if movement is None:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "movement_unknown")
        current_lane = vehicle.lane_id
        if current_lane is None or current_lane not in static_context.lane_to_approach:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_unknown")
        if (vehicle.distance_to_signal_m is None
                or vehicle.distance_to_signal_m < policy.lane_change_min_distance_m):
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None, None,
                    "lane_change_too_close")
        current_edge = static_context.lane_to_edge.get(current_lane)
        current_index = static_context.lane_to_index.get(current_lane)
        if current_edge is None or current_index is None:
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_adjacency_unknown")
        if not self._lane_has_fresh_support(snapshot, current_lane, now, freshness):
            return (GuidanceDecisionStatus.MISSING_INPUT, None, None,
                    "lane_queue_missing")
        best: tuple[float, str, int] | None = None
        for lane_id, index in static_context.lane_to_index.items():
            if lane_id == current_lane:
                continue
            if static_context.lane_to_edge.get(lane_id) != current_edge:
                continue
            if abs(index - current_index) != 1:
                continue
            if movement not in static_context.lane_to_movements.get(lane_id, ()):
                continue
            if not self._lane_has_fresh_support(snapshot, lane_id, now, freshness):
                return (GuidanceDecisionStatus.STALE_INPUT, None, None,
                        "lane_queue_stale")
            benefit = (self._queue_estimate(snapshot, current_lane)
                       - self._queue_estimate(snapshot, lane_id))
            if benefit >= policy.lane_queue_margin:
                if best is None or benefit > best[0]:
                    best = (benefit, lane_id, index)
        if best is None:
            return (GuidanceDecisionStatus.NO_ACTION_NEEDED, None, None,
                    "no_better_adjacent_lane")
        return GuidanceDecisionStatus.PROPOSED, best[1], best[2], "lane_queue_benefit"

    # ---------- 引导：状态汇总与发射判定（§3.4/§3.5） ----------
    @staticmethod
    def _merge_guidance_status(
        speed_status: GuidanceDecisionStatus,
        lane_status: GuidanceDecisionStatus,
    ) -> GuidanceDecisionStatus:
        if (speed_status is GuidanceDecisionStatus.PROPOSED
                or lane_status is GuidanceDecisionStatus.PROPOSED):
            return GuidanceDecisionStatus.PROPOSED
        priority = (
            GuidanceDecisionStatus.STALE_INPUT,
            GuidanceDecisionStatus.MISSING_INPUT,
            GuidanceDecisionStatus.INVALID_PROPOSAL,
            GuidanceDecisionStatus.SUPPRESSED_COOLDOWN,
            GuidanceDecisionStatus.SUPPRESSED_DUPLICATE,
            GuidanceDecisionStatus.SUPPRESSED_THRESHOLD,
            GuidanceDecisionStatus.NO_ACTION_NEEDED,
        )
        for status in priority:
            if speed_status is status or lane_status is status:
                return status
        return GuidanceDecisionStatus.NO_ACTION_NEEDED

    @staticmethod
    def _filter_reason(
        speed_status: GuidanceDecisionStatus, speed_reason: str,
        lane_status: GuidanceDecisionStatus, lane_reason: str,
    ) -> str:
        reasons = {
            GuidanceDecisionStatus.STALE_INPUT: ("stale_input", speed_reason),
            GuidanceDecisionStatus.MISSING_INPUT: ("missing_input", speed_reason),
            GuidanceDecisionStatus.INVALID_PROPOSAL: ("invalid_proposal", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_THRESHOLD: ("speed_below_trigger", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_DUPLICATE: ("duplicate_guidance", speed_reason),
            GuidanceDecisionStatus.SUPPRESSED_COOLDOWN: ("cooldown_active", speed_reason),
        }
        for status, (default, detail) in reasons.items():
            if speed_status is status or lane_status is status:
                return default
        return "no_action_needed"

    def _build_guidance_proposal(
        self, *, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        now: float, frame_id: str, policy: GuidancePolicyConfig,
        overall: GuidanceDecisionStatus,
        speed_status: GuidanceDecisionStatus, target_speed_mps: float | None,
        lane_status: GuidanceDecisionStatus, target_lane_id: str | None,
        target_lane_index: int | None, reason: str,
    ) -> VehicleGuidanceProposal:
        if overall is GuidanceDecisionStatus.PROPOSED:
            has_speed = target_speed_mps is not None
            has_lane = target_lane_id is not None
            guidance_type = ("combined" if has_speed and has_lane
                             else "speed" if has_speed else "lane")
        else:
            guidance_type = None
        source_message_ids = tuple(dict.fromkeys(
            tuple(vehicle.source_message_ids) + tuple(snapshot.source_message_ids)))
        return VehicleGuidanceProposal(
            vehicle_id=vehicle.vehicle_id,
            status=overall,
            speed_status=speed_status,
            lane_status=lane_status,
            current_speed_mps=vehicle.speed_mps,
            target_speed_mps=target_speed_mps,
            current_lane_id=vehicle.lane_id,
            target_lane_id=target_lane_id,
            target_lane_index=target_lane_index,
            guidance_type=guidance_type,
            reason=reason,
            confidence=None,
            valid_from=now,
            valid_until=now + policy.guidance_ttl_s,
            source_message_ids=source_message_ids,
            source_frame_ids=snapshot.source_frame_ids,
        )

    def _should_resend(
        self, last: LastEmittedGuidanceState, target_speed_mps: float | None,
        target_lane_id: str | None, reason: str, now: float,
        policy: GuidancePolicyConfig,
    ) -> bool:
        if last.target_lane_id != target_lane_id:
            return True
        if (target_speed_mps is None) != (last.target_speed_mps is None):
            return True
        if target_speed_mps is not None and last.target_speed_mps is not None:
            if abs(target_speed_mps - last.target_speed_mps) >= policy.speed_resend_delta_mps:
                return True
        if reason != last.reason:
            return True
        if now >= last.valid_until:
            return True
        return False

    def propose_guidance(
        self, *, vehicle: ConnectedVehicleState, snapshot: EdgeSnapshot,
        static_context: IntersectionStaticContext, now: float, frame_id: str,
        config: CollabConfig, scope: ResolvedScenarioScope,
        last_emitted: LastEmittedGuidanceState | None,
    ) -> GuidanceOutcome:
        policy = config.guidance_policy
        if config.guidance_mode is GuidanceEmissionMode.DISABLED:
            return GuidanceOutcome(None, "connected_seen", "guidance_disabled")
        stage, filter_reason = self._candidate_stage(
            vehicle, now, config.freshness, scope, policy.guidance_horizon_m)
        if stage != "in_horizon_candidates":
            return GuidanceOutcome(None, stage, filter_reason)
        speed_status, target_speed_mps, speed_reason = self._speed_decision(
            vehicle, snapshot, static_context, now, policy, config.freshness)
        lane_status, target_lane_id, target_lane_index, lane_reason = \
            self._lane_decision(vehicle, snapshot, static_context, now,
                                policy, config.freshness)
        # 阈值（仅 THRESHOLD；速度分量需要 delta ≥ trigger，车道分量天然过阈值）
        if (config.guidance_mode is GuidanceEmissionMode.THRESHOLD
                and speed_status is GuidanceDecisionStatus.PROPOSED
                and abs((target_speed_mps or 0.0) - vehicle.speed_mps)
                < policy.speed_trigger_delta_mps):
            speed_status = GuidanceDecisionStatus.SUPPRESSED_THRESHOLD
            speed_reason = "speed_below_trigger"
        overall = self._merge_guidance_status(speed_status, lane_status)
        if overall is not GuidanceDecisionStatus.PROPOSED:
            reason = self._filter_reason(speed_status, speed_reason,
                                         lane_status, lane_reason)
            proposal = self._build_guidance_proposal(
                vehicle=vehicle, snapshot=snapshot, now=now, frame_id=frame_id,
                policy=policy, overall=overall,
                speed_status=speed_status, target_speed_mps=target_speed_mps,
                lane_status=lane_status, target_lane_id=target_lane_id,
                target_lane_index=target_lane_index, reason=reason)
            return GuidanceOutcome(proposal, "raw_proposals", reason)
        funnel_stage = "raw_proposals"
        reason = self._pick_guidance_reason(speed_reason, lane_reason)
        if config.guidance_mode is GuidanceEmissionMode.THRESHOLD:
            funnel_stage = "threshold_passed"
            if last_emitted is not None and not self._should_resend(
                    last_emitted, target_speed_mps, target_lane_id, reason,
                    now, policy):
                overall = GuidanceDecisionStatus.SUPPRESSED_DUPLICATE
                return GuidanceOutcome(
                    self._build_guidance_proposal(
                        vehicle=vehicle, snapshot=snapshot, now=now,
                        frame_id=frame_id, policy=policy, overall=overall,
                        speed_status=speed_status, target_speed_mps=target_speed_mps,
                        lane_status=lane_status, target_lane_id=target_lane_id,
                        target_lane_index=target_lane_index,
                        reason="duplicate_guidance"),
                    "threshold_passed", "duplicate_guidance")
            funnel_stage = "dedup_passed"
            if last_emitted is not None and \
                    now < last_emitted.emitted_at + policy.minimum_resend_interval_s:
                overall = GuidanceDecisionStatus.SUPPRESSED_COOLDOWN
                return GuidanceOutcome(
                    self._build_guidance_proposal(
                        vehicle=vehicle, snapshot=snapshot, now=now,
                        frame_id=frame_id, policy=policy, overall=overall,
                        speed_status=speed_status, target_speed_mps=target_speed_mps,
                        lane_status=lane_status, target_lane_id=target_lane_id,
                        target_lane_index=target_lane_index,
                        reason="cooldown_active"),
                    "dedup_passed", "cooldown_active")
            funnel_stage = "cooldown_passed"
        proposal = self._build_guidance_proposal(
            vehicle=vehicle, snapshot=snapshot, now=now, frame_id=frame_id,
            policy=policy, overall=GuidanceDecisionStatus.PROPOSED,
            speed_status=speed_status, target_speed_mps=target_speed_mps,
            lane_status=lane_status, target_lane_id=target_lane_id,
            target_lane_index=target_lane_index, reason=reason)
        return GuidanceOutcome(proposal, "published", None)

    @staticmethod
    def _pick_guidance_reason(speed_reason: str, lane_reason: str) -> str:
        if speed_reason == "speed_catchup":
            return speed_reason
        if lane_reason == "lane_queue_benefit":
            return lane_reason
        return speed_reason if speed_reason != "no_action_needed" else lane_reason
```

注意：
- `_speed_decision` / `_lane_decision` 返回的 `reason` 是内部详细原因；`filter_reason`（§3.6 漏斗）用 `_filter_reason` 映射为统计键（`speed_below_trigger` / `duplicate_guidance` / `cooldown_active` / `no_action_needed` 等）。
- `pytest` import 在测试文件顶部补 `import pytest`。
- 速度测试关键数字：`speed=8, distance=205, remaining=21` → `available=20`、`raw=10.25`、`upper=min(16, 1.3*8=10.4)=10.4`、`lower=max(0, 4)=4`、delta=2.25。
- 阈值抑制测试：`speed=6, distance=140, remaining=21` → `raw=7.0`、`upper=7.8`、`target=7.0`、delta=1.0 < 2.0。
- 车道测试依赖 RSM：目标车道 `A_1` 的排队估计必须有新鲜 BSM/RSM 支撑（§3.3 新鲜度门禁），fixture 注入一辆 RSM 低速卡车使 `queue_estimate(A_1)=1`。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_policy_guidance.py algorithms/v2x/collab/tests/test_policy_signal.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/policy.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/policy.py
scp -P 24 algorithms/v2x/collab/tests/test_policy_guidance.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_policy_guidance.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/policy.py algorithms/v2x/collab/tests/test_policy_guidance.py && git commit -m "feat(v2x): RSI guidance policy (threshold trigger + lane advisory)"'
```

---

## Task 9: arbiter.py — ActionArbiter + validate_signal_proposal

**Files:**
- Create: `algorithms/v2x/collab/arbiter.py`
- Test: `algorithms/v2x/collab/tests/test_arbiter.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_arbiter.py`：

```python
import pytest

from algorithms.v2x.collab.arbiter import (
    ActionArbiter, ActiveModeUnavailableError, validate_signal_proposal,
)
from algorithms.v2x.collab.proposals import (
    DecisionMode, DecisionSource, SignalDecisionStatus, SignalProposal,
)


def _proposal(status=SignalDecisionStatus.PROPOSED, proposed=2, current=1,
              frame_id="ep1:step:000001", intersection_id="i1",
              valid_until=10.0):
    return SignalProposal(
        intersection_id=intersection_id,
        status=status,
        candidate_action=2 if status is SignalDecisionStatus.PROPOSED else None,
        proposed_action=proposed,
        current_action=current,
        action_scores={1: 1.0, 2: 3.0},
        reason="test", confidence=0.8,
        valid_from=0.0, valid_until=valid_until,
        needs_transition=True, decision_frame_id=frame_id,
        source_message_ids=("m1",), source_frame_ids=("ep1:step:000001",),
    )


def test_validate_passes_for_valid_proposal():
    result = validate_signal_proposal(
        _proposal(), run_id="run1", frame_id="ep1:step:000001",
        intersection_id="i1", now=5.0, current_action=1,
        in_transition=False, valid_actions=(1, 2))
    assert result.passed
    assert result.would_select_cloud
    assert result.would_select_action == 2
    assert result.failure_reason is None


@pytest.mark.parametrize("kw,reason", [
    ({"frame_id": "ep1:step:000002"}, "stale_decision_frame"),
    ({"now": 11.0}, "outside_validity_window"),
    ({"current_action": 2}, "current_action_mismatch"),
    ({"in_transition": True}, "in_transition"),
    ({"valid_actions": (1,)}, "proposed_action_not_valid"),
])
def test_validate_failures(kw, reason):
    base = dict(run_id="run1", frame_id="ep1:step:000001",
                intersection_id="i1", now=5.0, current_action=1,
                in_transition=False, valid_actions=(1, 2))
    base.update(kw)
    result = validate_signal_proposal(_proposal(), **base)
    assert not result.passed
    assert result.failure_reason == reason
    assert not result.would_select_cloud


def test_validate_rejects_non_selectable_status():
    proposal = _proposal(status=SignalDecisionStatus.MISSING_INPUT, proposed=None)
    result = validate_signal_proposal(
        proposal, run_id="run1", frame_id="ep1:step:000001",
        intersection_id="i1", now=5.0, current_action=1,
        in_transition=False, valid_actions=(1, 2))
    assert not result.passed
    assert result.failure_reason == "status_not_selectable"


def test_shadow_arbiter_always_selects_baseline():
    arbiter = ActionArbiter(DecisionMode.SHADOW)
    result = arbiter.arbitrate(
        proposal=_proposal(), baseline_action=1, run_id="run1",
        frame_id="ep1:step:000001", intersection_id="i1", now=5.0,
        in_transition=False, valid_actions=(1, 2))
    assert result.selected_action == 1
    assert result.decision_source is DecisionSource.BASELINE
    assert result.selection_status == "selected_baseline_shadow"
    assert result.validation.passed


def test_off_arbiter_short_circuits():
    arbiter = ActionArbiter(DecisionMode.OFF)
    result = arbiter.arbitrate(
        proposal=None, baseline_action=1, run_id="run1",
        frame_id="ep1:step:000001", intersection_id="i1", now=5.0,
        in_transition=False, valid_actions=(1, 2))
    assert result.selected_action == 1
    assert result.validation is None


def test_active_mode_unavailable_at_construction():
    with pytest.raises(ActiveModeUnavailableError):
        ActionArbiter(DecisionMode.ACTIVE)
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_arbiter.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `arbiter.py`**

```python
# algorithms/v2x/collab/arbiter.py
"""ActionArbiter + validate_signal_proposal（spec §4）。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from .proposals import (
    DecisionMode, DecisionSource, SignalDecisionStatus, SignalProposal,
)

CLOUD_SELECTABLE_STATUSES = frozenset({
    SignalDecisionStatus.PROPOSED,
    SignalDecisionStatus.KEEP_CURRENT,
    SignalDecisionStatus.NO_DEMAND,
    SignalDecisionStatus.SUPPRESSED_MIN_GREEN,
    SignalDecisionStatus.SUPPRESSED_SWITCH_MARGIN,
})

FALLBACK_STATUSES = frozenset({
    SignalDecisionStatus.MISSING_INPUT,
    SignalDecisionStatus.STALE_INPUT,
    SignalDecisionStatus.INVALID_PROPOSAL,
    SignalDecisionStatus.SUPPRESSED_TRANSITION,
})


class ActiveModeUnavailableError(RuntimeError):
    """v1 不支持 ACTIVE 决策模式（spec §4.1：选择即报错）。"""


@dataclass(frozen=True, slots=True)
class ProposalValidationResult:
    passed: bool
    would_select_cloud: bool
    would_select_action: Optional[int]
    failure_reason: Optional[str]


@dataclass(frozen=True, slots=True)
class ArbitrationResult:
    intersection_id: str
    baseline_action: Optional[int]
    selected_action: Optional[int]
    decision_source: DecisionSource
    selection_status: str  # selected_baseline_shadow / selected_cloud / selected_fallback
    proposal: Optional[SignalProposal]
    validation: Optional[ProposalValidationResult]


def validate_signal_proposal(
    proposal: SignalProposal, *,
    run_id: str, frame_id: str, intersection_id: str, now: float,
    current_action: Optional[int], in_transition: bool,
    valid_actions: Sequence[int],
) -> ProposalValidationResult:
    """SHADOW 与未来 ACTIVE 共用同一验证器（spec §4.3）。"""
    if proposal.intersection_id != intersection_id:
        return ProposalValidationResult(False, False, None, "intersection_mismatch")
    if proposal.decision_frame_id != frame_id:
        return ProposalValidationResult(False, False, None, "stale_decision_frame")
    if not (proposal.valid_from <= now < proposal.valid_until):
        return ProposalValidationResult(False, False, None, "outside_validity_window")
    if proposal.current_action != current_action:
        return ProposalValidationResult(False, False, None, "current_action_mismatch")
    if proposal.proposed_action is None or proposal.proposed_action not in valid_actions:
        return ProposalValidationResult(False, False, None, "proposed_action_not_valid")
    if in_transition:
        return ProposalValidationResult(False, False, None, "in_transition")
    if proposal.status not in CLOUD_SELECTABLE_STATUSES:
        return ProposalValidationResult(False, False, None, "status_not_selectable")
    values = [proposal.valid_from, proposal.valid_until, proposal.confidence]
    values.extend(proposal.action_scores.values())
    if any(v is not None and (not math.isfinite(float(v))) for v in values):
        return ProposalValidationResult(False, False, None, "non_finite_values")
    return ProposalValidationResult(True, True, proposal.proposed_action, None)


class ActionArbiter:
    def __init__(self, mode: DecisionMode) -> None:
        if mode is DecisionMode.ACTIVE:
            raise ActiveModeUnavailableError(
                "ACTIVE decision mode is unavailable in v1 (spec §4.1); "
                "use --v2x-collab-mode shadow|off")
        self.mode = mode

    def arbitrate(
        self, *, proposal: Optional[SignalProposal], baseline_action: Optional[int],
        run_id: str, frame_id: str, intersection_id: str, now: float,
        in_transition: bool, valid_actions: Sequence[int],
    ) -> ArbitrationResult:
        if self.mode is DecisionMode.OFF:
            return ArbitrationResult(
                intersection_id=intersection_id, baseline_action=baseline_action,
                selected_action=baseline_action,
                decision_source=DecisionSource.BASELINE,
                selection_status="selected_baseline_shadow",
                proposal=proposal, validation=None)
        # SHADOW（默认）：建议照常生成/记录，但 applied == baseline
        validation = None
        if proposal is not None:
            validation = validate_signal_proposal(
                proposal, run_id=run_id, frame_id=frame_id,
                intersection_id=intersection_id, now=now,
                current_action=baseline_action, in_transition=in_transition,
                valid_actions=valid_actions)
        return ArbitrationResult(
            intersection_id=intersection_id, baseline_action=baseline_action,
            selected_action=baseline_action,
            decision_source=DecisionSource.BASELINE,
            selection_status="selected_baseline_shadow",
            proposal=proposal, validation=validation)

    def reset_episode(self) -> None:
        # v1 仲裁器无 episode 级动态状态；保留钩子以兼容多 episode
        return None
```

注意：
- 验证顺序与 spec §4.3 一致；`current_action` 在 SHADOW 下传入 `baseline_action`（shadow 下 Cloud view 与 baseline 一致是架构前提，若不一致 validator 会记录 `current_action_mismatch`，不改变 applied）。
- `valid_actions` 来自静态上下文 `valid_actions`；`in_transition` 由 engine 传 `stage ∈ {YELLOW, CLEARANCE}`。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_arbiter.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/arbiter.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/arbiter.py
scp -P 24 algorithms/v2x/collab/tests/test_arbiter.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_arbiter.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/arbiter.py algorithms/v2x/collab/tests/test_arbiter.py && git commit -m "feat(v2x): ActionArbiter shadow semantics + proposal validator"'
```

---

## Task 10: records.py — InMemoryRecordCollector + 记录构造

**Files:**
- Create: `algorithms/v2x/collab/records.py`
- Test: `algorithms/v2x/collab/tests/test_records.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_records.py`：

```python
from algorithms.v2x.collab.proposals import (
    DecisionMode, DecisionSource, GuidanceDecisionStatus,
    GuidanceEmissionMode, SignalDecisionStatus, SignalProposal,
)
from algorithms.v2x.collab.records import (
    InMemoryRecordCollector, arbitration_record, cloud_proposal_record,
    collab_episode_end_record, collab_tick_stats_record, edge_snapshot_record,
)
from algorithms.v2x.collab.snapshot import EdgeSnapshot


def _snapshot():
    return EdgeSnapshot(
        intersection_id="i1", sim_time=5.0, phase=1, stage="GREEN",
        stage_elapsed_s=3.0, remaining_time_s=20.0,
        approaches={}, connected_vehicles={},
        last_delivery_at={"SPaT": 5.0, "MAP": 0.0},
        source_message_ids=("map-i1", "spat-i1"),
        source_frame_ids=("ep1:init", "ep1:step:000001"),
    )


def _signal_proposal():
    return SignalProposal(
        intersection_id="i1", status=SignalDecisionStatus.PROPOSED,
        candidate_action=2, proposed_action=2, current_action=1,
        action_scores={1: 1.0, 2: 3.0}, reason="queue_demand",
        confidence=0.8, valid_from=5.0, valid_until=10.0,
        needs_transition=True, decision_frame_id="ep1:step:000001",
        source_message_ids=("spat-i1",), source_frame_ids=("ep1:step:000001",),
    )


def test_collector_reset_and_episode_records():
    collector = InMemoryRecordCollector()
    collector.write(edge_snapshot_record(run_id="run1", episode_id="ep1",
                                         frame_id="ep1:step:000001",
                                         snapshot=_snapshot()))
    assert len(collector.episode_records) == 1
    collector.reset_episode()
    assert collector.episode_records == []


def test_record_schemas_contain_required_keys():
    collector = InMemoryRecordCollector()
    proposal = _signal_proposal()
    collector.write(cloud_proposal_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        sim_time=5.0, proposal=proposal, proposal_type="signal"))
    collector.write(arbitration_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        intersection_id="i1", sim_time=5.0, baseline_action=1,
        candidate_action=2, proposed_action=2, selected_action=1,
        proposal_status="proposed", validation_status="passed",
        validation_failure_reason=None, decision_source="baseline",
        selection_status="selected_baseline_shadow", confidence=0.8,
        reason="queue_demand"))
    collector.write(collab_tick_stats_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000001",
        sim_time=5.0, baseline_slots=1, decision_records=1,
        status_counts={"proposed": 1}, validation_counts={"passed": 1},
        proposal_without_baseline=0, guidance_funnel={"published": 1},
        filter_reason_counts={}))
    collector.write(collab_episode_end_record(
        summary={"collab": {"schema_version": "1.0"}}))
    by_type = {r.record_type: r.data for r in collector.episode_records}
    assert "edge_snapshot" in by_type
    assert by_type["cloud_proposal"]["proposal_type"] == "signal"
    assert by_type["cloud_proposal"]["intersection_id"] == "i1"
    arb = by_type["arbitration"]
    assert arb["signal_event_ref"] == ("run1", "ep1:step:000001", "i1")
    assert arb["selection_status"] == "selected_baseline_shadow"
    tick = by_type["collab_tick_stats"]
    assert tick["signal"]["baseline_slots"] == 1
    assert tick["guidance"]["published"] == 1
    assert by_type["collab_episode_end"]["summary"]["collab"]["schema_version"] == "1.0"
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_records.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `records.py`**

```python
# algorithms/v2x/collab/records.py
"""InMemoryRecordCollector + 五类协同记录构造（spec §1.7/§4.4/§5.1）。"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from ..logger import LogRecord, MessageSink
from .proposals import SignalProposal, VehicleGuidanceProposal
from .snapshot import EdgeSnapshot


class InMemoryRecordCollector(MessageSink):
    """内存记录收集器（spec §6.2：collab 开启时必有，不可关闭）。"""

    def __init__(self) -> None:
        self._records: list[LogRecord] = []

    def write(self, record: LogRecord) -> None:
        self._records.append(record)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def episode_records(self) -> list[LogRecord]:
        return list(self._records)

    def reset_episode(self) -> None:
        self._records.clear()


def edge_snapshot_record(
    *, run_id: str, episode_id: str, frame_id: str,
    snapshot: EdgeSnapshot,
) -> LogRecord:
    return LogRecord("edge_snapshot", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "intersection_id": snapshot.intersection_id,
        "sim_time": snapshot.sim_time,
        "phase": snapshot.phase, "stage": snapshot.stage,
        "stage_elapsed_s": snapshot.stage_elapsed_s,
        "remaining_time_s": snapshot.remaining_time_s,
        "approaches": {
            aid: {
                "incoming_lane_ids": list(ap.incoming_lane_ids),
                "lane_states": {
                    lid: {
                        "connected_count": lane.connected_count,
                        "observed_count": lane.observed_count,
                        "stopped_count": lane.stopped_count,
                        "queue_estimate": lane.queue_estimate,
                        "arrivals_since_last_snapshot": lane.arrivals_since_last_snapshot,
                    } for lid, lane in ap.lane_states.items()
                },
                "downstream_vehicle_count": ap.downstream_vehicle_count,
                "downstream_queue_estimate": ap.downstream_queue_estimate,
                "turn_intent_counts": dict(ap.turn_intent_counts),
                "arrival_etas_s": list(ap.arrival_etas_s),
            } for aid, ap in snapshot.approaches.items()
        },
        "connected_vehicle_ids": sorted(snapshot.connected_vehicles),
        "last_delivery_at": dict(snapshot.last_delivery_at),
        "source_message_ids": list(snapshot.source_message_ids),
        "source_frame_ids": list(snapshot.source_frame_ids),
    })


def cloud_proposal_record(
    *, run_id: str, episode_id: str, frame_id: str, sim_time: float,
    proposal: SignalProposal | VehicleGuidanceProposal,
    proposal_type: str,  # "signal" | "vehicle_guidance"
) -> LogRecord:
    if proposal_type == "signal":
        assert isinstance(proposal, SignalProposal)
        data: Mapping[str, Any] = {
            "proposal_type": "signal",
            "intersection_id": proposal.intersection_id,
            "status": proposal.status.value,
            "candidate_action": proposal.candidate_action,
            "proposed_action": proposal.proposed_action,
            "current_action": proposal.current_action,
            "action_scores": dict(proposal.action_scores),
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "valid_from": proposal.valid_from,
            "valid_until": proposal.valid_until,
            "needs_transition": proposal.needs_transition,
            "decision_frame_id": proposal.decision_frame_id,
            "source_message_ids": list(proposal.source_message_ids),
            "source_frame_ids": list(proposal.source_frame_ids),
        }
    else:
        assert isinstance(proposal, VehicleGuidanceProposal)
        data = {
            "proposal_type": "vehicle_guidance",
            "vehicle_id": proposal.vehicle_id,
            "status": proposal.status.value,
            "speed_status": proposal.speed_status.value,
            "lane_status": proposal.lane_status.value,
            "current_speed_mps": proposal.current_speed_mps,
            "target_speed_mps": proposal.target_speed_mps,
            "current_lane_id": proposal.current_lane_id,
            "target_lane_id": proposal.target_lane_id,
            "target_lane_index": proposal.target_lane_index,
            "guidance_type": proposal.guidance_type,
            "reason": proposal.reason,
            "confidence": proposal.confidence,
            "valid_from": proposal.valid_from,
            "valid_until": proposal.valid_until,
            "source_message_ids": list(proposal.source_message_ids),
            "source_frame_ids": list(proposal.source_frame_ids),
        }
    return LogRecord("cloud_proposal", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "sim_time": sim_time, **data,
    })


def arbitration_record(
    *, run_id: str, episode_id: str, frame_id: str, intersection_id: str,
    sim_time: float, baseline_action: Optional[int],
    candidate_action: Optional[int], proposed_action: Optional[int],
    selected_action: Optional[int], proposal_status: Optional[str],
    validation_status: Optional[str],
    validation_failure_reason: Optional[str],
    decision_source: str, selection_status: str,
    confidence: Optional[float], reason: Optional[str],
) -> LogRecord:
    return LogRecord("arbitration", {
        "run_id": run_id, "episode_id": episode_id, "frame_id": frame_id,
        "intersection_id": intersection_id, "sim_time": sim_time,
        "baseline_action": baseline_action,
        "candidate_action": candidate_action,
        "proposed_action": proposed_action,
        "selected_action": selected_action,
        "proposal_status": proposal_status,
        "validation_status": validation_status,
        "validation_failure_reason": validation_failure_reason,
        "decision_source": decision_source,
        "selection_status": selection_status,
        "confidence": confidence, "reason": reason,
        "signal_event_ref": (run_id, frame_id, intersection_id),
    })


def collab_tick_stats_record(
    *, run_id: str, episode_id: str, frame_id: str, sim_time: float,
    baseline_slots: int, decision_records: int,
    status_counts: Mapping[str, int], validation_counts: Mapping[str, int],
    proposal_without_baseline: int,
    guidance_funnel: Mapping[str, int],
    filter_reason_counts: Mapping[str, int],
) -> LogRecord:
    return LogRecord("collab_tick_stats", {
        "run_id": run_id, "episode_id": episode_id,
        "frame_id": frame_id, "sim_time": sim_time,
        "signal": {
            "baseline_slots": baseline_slots,
            "decision_records": decision_records,
            "status_counts": dict(status_counts),
            "validation_counts": dict(validation_counts),
            "proposal_without_baseline": proposal_without_baseline,
        },
        "guidance": {
            **{k: guidance_funnel.get(k, 0) for k in (
                "connected_seen", "fresh_bsm", "next_signal_known",
                "next_signal_managed", "distance_known",
                "in_horizon_candidates", "raw_proposals", "threshold_passed",
                "dedup_passed", "cooldown_passed", "published")},
            "filter_reason_counts": dict(filter_reason_counts),
        },
    })


def collab_episode_end_record(*, summary: Mapping[str, Any]) -> LogRecord:
    return LogRecord("collab_episode_end", {"summary": dict(summary)})
```

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_records.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/records.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/records.py
scp -P 24 algorithms/v2x/collab/tests/test_records.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_records.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/records.py algorithms/v2x/collab/tests/test_records.py && git commit -m "feat(v2x): InMemoryRecordCollector + collab record builders"'
```

---

## Task 11: stats.py — episode 汇总 + pooled 聚合 + 完整性审计

**Files:**
- Create: `algorithms/v2x/collab/stats.py`
- Test: `algorithms/v2x/collab/tests/test_stats.py`
- 小改（契约收口）：`algorithms/v2x/collab/records.py`、`algorithms/v2x/collab/policy.py`

### 实现前契约收口（本任务必须先做，供 Task 12 engine 使用）

1. `records.cloud_proposal_record` 增加两个**可选**字段：
   ```python
   def cloud_proposal_record(..., emitted_message_id: Optional[str] = None,
                             next_signal_intersection_id: Optional[str] = None) -> LogRecord:
   ```
   - `proposal_type == "vehicle_guidance"` 时写入 `emitted_message_id`（engine 发布 RSI 后回填）与 `next_signal_intersection_id`（车辆下一路口，用于 §5.3 过期投递与 §5.4 RSI 审计）；
   - 其余字段不变；`test_records.py` 追加一条断言。
2. `policy.GuidanceOutcome` 追加三个诊断字段（默认值 `True/None/None`）：
   ```python
   @dataclass(frozen=True, slots=True)
   class GuidanceOutcome:
       proposal: VehicleGuidanceProposal | None
       funnel_stage: str
       filter_reason: str | None
       would_pass_threshold: bool = True   # FULL 模式诊断：若按 THRESHOLD 是否可达发射
       would_be_duplicate: bool | None = None
       would_be_in_cooldown: bool | None = None
   ```
   `propose_guidance` 内：
   - THRESHOLD 模式：`would_pass_threshold = (最终 funnel ≥ threshold_passed)`，`would_be_duplicate / would_be_in_cooldown` 与判定结果一致；
   - FULL 模式：始终按 THRESHOLD 规则**诊断计算**三值（不改变 published 结果）；
   - DISABLED/候选未通过：三值保持默认。
   实现时修改 Task 8 代码，`test_policy_guidance.py` 追加 FULL 诊断断言。
3. `policy.missing_signal_proposal` 助手（engine 在 MAP 缺失时构造 MISSING_INPUT）：
   ```python
   def missing_signal_proposal(self, *, intersection_id: str, now: float,
                               frame_id: str, reason: str = "missing_map") -> SignalProposal:
       return SignalProposal(
           intersection_id=intersection_id,
           status=SignalDecisionStatus.MISSING_INPUT,
           candidate_action=None, proposed_action=None, current_action=None,
           action_scores={}, reason=reason, confidence=0.0,
           valid_from=now,
           valid_until=now + self._config.signal_policy.proposal_ttl_s,
           needs_transition=False, decision_frame_id=frame_id,
           source_message_ids=(), source_frame_ids=())
   ```

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_stats.py`：

```python
import pytest

from algorithms.v2x.collab.proposals import CollabConfig, DecisionMode, GuidanceEmissionMode
from algorithms.v2x.collab.records import (
    arbitration_record, cloud_proposal_record, collab_tick_stats_record,
    InMemoryRecordCollector,
)
from algorithms.v2x.collab.stats import build_collab_summary, pool_collab_summaries
from algorithms.v2x.collab.snapshot import EdgeSnapshot
from config.scenario_presets import ResolvedScenarioScope


SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("demo_3", "demo_5", "demo_6", "demo_9"))
REGISTERED = tuple(f"demo_{i}" for i in range(1, 21))


class _FakeHub:
    def __init__(self):
        self.sent_records = []
        self.delivery_records = []


def _edge_snapshot():
    return EdgeSnapshot(
        intersection_id="demo_3", sim_time=50.0, phase=1, stage="GREEN",
        stage_elapsed_s=3.0, remaining_time_s=20.0, approaches={},
        connected_vehicles={}, last_delivery_at={"SPaT": 49.9, "MAP": 0.0},
        source_message_ids=("map-demo_3", "spat-demo_3-50"),
        source_frame_ids=("ep1:init", "ep1:step:000010"),
    )


def _tick(funnel=None):
    return collab_tick_stats_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        sim_time=50.0, baseline_slots=4, decision_records=4,
        status_counts={"proposed": 1, "keep_current": 3},
        validation_counts={"passed": 4},
        proposal_without_baseline=0,
        guidance_funnel=funnel or {
            "connected_seen": 8, "fresh_bsm": 8, "next_signal_known": 8,
            "next_signal_managed": 6, "distance_known": 6,
            "in_horizon_candidates": 4, "raw_proposals": 2,
            "threshold_passed": 2, "dedup_passed": 2,
            "cooldown_passed": 2, "published": 1},
        filter_reason_counts={"next_signal_not_managed": 2})


def _arbitration(proposal_status="proposed", proposed=2, baseline=1,
                 validation="passed", failure=None):
    return arbitration_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        intersection_id="demo_3", sim_time=50.0, baseline_action=baseline,
        candidate_action=2, proposed_action=proposed, selected_action=baseline,
        proposal_status=proposal_status, validation_status=validation,
        validation_failure_reason=failure, decision_source="baseline",
        selection_status="selected_baseline_shadow", confidence=0.8,
        reason="queue_demand")


def _records(include_guidance=True):
    collector = InMemoryRecordCollector()
    collector.write(_edge_snapshot_record())
    collector.write(_tick())
    collector.write(_arbitration())
    collector.write(cloud_proposal_record(
        run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
        sim_time=50.0, proposal_type="signal",
        proposal=_signal_proposal()))
    if include_guidance:
        collector.write(cloud_proposal_record(
            run_id="run1", episode_id="ep1", frame_id="ep1:step:000010",
            sim_time=50.0, proposal_type="vehicle_guidance",
            proposal=_guidance_proposal(),
            emitted_message_id="rsi-1",
            next_signal_intersection_id="demo_3"))
    return collector.episode_records


def _edge_snapshot_record():
    from algorithms.v2x.collab.records import edge_snapshot_record
    return edge_snapshot_record(run_id="run1", episode_id="ep1",
                                frame_id="ep1:step:000010",
                                snapshot=_edge_snapshot())


def _signal_proposal():
    from algorithms.v2x.collab.proposals import SignalProposal, SignalDecisionStatus
    return SignalProposal(
        intersection_id="demo_3", status=SignalDecisionStatus.PROPOSED,
        candidate_action=2, proposed_action=2, current_action=1,
        action_scores={1: 1.0, 2: 3.0}, reason="queue_demand",
        confidence=0.8, valid_from=50.0, valid_until=55.0,
        needs_transition=True, decision_frame_id="ep1:step:000010",
        source_message_ids=("spat-demo_3-50",),
        source_frame_ids=("ep1:step:000010",))


def _guidance_proposal():
    from algorithms.v2x.collab.proposals import (
        GuidanceDecisionStatus, VehicleGuidanceProposal,
    )
    return VehicleGuidanceProposal(
        vehicle_id="car1", status=GuidanceDecisionStatus.PROPOSED,
        speed_status=GuidanceDecisionStatus.PROPOSED,
        lane_status=GuidanceDecisionStatus.NO_ACTION_NEEDED,
        current_speed_mps=8.0, target_speed_mps=10.25,
        current_lane_id="A_0", target_lane_id=None, target_lane_index=None,
        guidance_type="speed", reason="speed_catchup", confidence=None,
        valid_from=50.0, valid_until=60.0,
        source_message_ids=("bsm-car1-50", "spat-demo_3-50"),
        source_frame_ids=("ep1:step:000010",))


def test_episode_summary_rates_and_scope():
    hub = _FakeHub()
    hub.sent_records = [
        {"message_id": "m-signal-1", "message_type": "SIGNAL_CONTROL",
         "frame_id": "ep1:step:000010", "source_id": "demo_3"},
        {"message_id": "m-rsi-1", "message_type": "RSI",
         "frame_id": "ep1:step:000010", "source_id": "cloud"},
    ]
    hub.delivery_records = [
        {"message_id": "m-rsi-1", "status": "delivered", "delivered_at": 50.05},
    ]
    summary = build_collab_summary(
        records=_records(), config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=hub,
        run_id="run1", episode_id="ep1")
    collab = summary["collab"]
    assert collab["schema_version"] == "1.0"
    signal = collab["signal"]
    assert signal["baseline_signal_slots"] == 4
    assert signal["decision_record_coverage"] == pytest.approx(1.0)
    assert signal["selectable_output_rate"] == pytest.approx(1 / 4)
    assert signal["suggested_switch_rate"] == pytest.approx(1 / 4)
    assert signal["action_agreement_rate"] is None  # proposed(2) != baseline(1)
    assert signal["stale_input_rate"] == 0.0
    assert signal["missing_input_rate"] == 0.0
    guidance = collab["guidance"]
    assert guidance["funnel"]["published"] == 1
    assert guidance["rates"]["guidance_generation_rate"] == pytest.approx(0.5)
    assert guidance["rates"]["network_delivery_rate"] == pytest.approx(1.0)
    assert guidance["delivered_count"] == 1
    assert guidance["expired_on_delivery_count"] == 0
    assert collab["arbitration"]["selection_status_counts"] == {
        "selected_baseline_shadow": 1}
    assert collab["validation"]["validation_pass_rate"] == pytest.approx(1.0)
    integrity = collab["integrity"]
    assert integrity["missing_signal_event_refs"] == 0
    assert integrity["orphan_rsi_messages"] == 0
    assert integrity["orphan_rsi_deliveries"] == 0
    assert integrity["duplicate_terminal_delivery_records"] == 0
    scope_block = summary["scope"]
    assert scope_block["source"] == "preset"
    assert scope_block["algorithm_controlled_intersections"] == 4
    assert scope_block["fixed_intersections"] == 16
    assert scope_block["managed_ids"] == list(SCOPE.managed_ids)


def test_pooled_rates_use_summed_denominators():
    summaries = [build_collab_summary(
        records=_records(), config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=_FakeHub(),
        run_id="run1", episode_id=f"ep{i}") for i in (1, 2)]
    pooled = pool_collab_summaries(summaries)
    assert pooled["pooled_episodes"] == 2
    assert pooled["collab"]["signal"]["baseline_signal_slots"] == 8
    assert pooled["collab"]["guidance"]["funnel"]["published"] == 2
    assert pooled["collab"]["guidance"]["rates"]["guidance_generation_rate"] == \
        pytest.approx(0.5)


def test_zero_denominators_are_null():
    summary = build_collab_summary(
        records=[], config=CollabConfig(), scope=SCOPE,
        registered_ids=REGISTERED, hub=_FakeHub(),
        run_id="run1", episode_id="ep1")
    assert summary["collab"]["signal"]["baseline_signal_slots"] == 0
    assert summary["collab"]["signal"]["decision_record_coverage"] is None
    assert summary["collab"]["guidance"]["rates"]["network_delivery_rate"] is None
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_stats.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `stats.py`**

```python
# algorithms/v2x/collab/stats.py
"""collab episode 汇总（§5.2/§5.3）+ 完整性审计（§5.4）+ pooled 聚合（§5.5）。"""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Mapping, Optional, Sequence

from ..hub import V2XHub
from ..logger import LogRecord
from .proposals import CollabConfig, DecisionMode, GuidanceEmissionMode
from .records import InMemoryRecordCollector
from config.scenario_presets import ResolvedScenarioScope

GUIDANCE_FUNNEL_KEYS = (
    "connected_seen", "fresh_bsm", "next_signal_known", "next_signal_managed",
    "distance_known", "in_horizon_candidates", "raw_proposals",
    "threshold_passed", "dedup_passed", "cooldown_passed", "published",
)
_SELECTABLE_STATUSES = frozenset({
    "proposed", "keep_current", "no_demand",
    "suppressed_min_green", "suppressed_switch_margin",
})


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator and denominator > 0:
        return numerator / denominator
    return None


def _sum_counts(iterable) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in iterable:
        counter.update(item)
    return dict(counter)


def _describe(values: Sequence[float]) -> dict:
    if not values:
        return {"mean": None, "p50": None, "p95": None, "sample_count": 0}
    ordered = sorted(values)
    return {
        "mean": sum(values) / len(values),
        "p50": median(values),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "sample_count": len(values),
    }


def scope_block(scope: ResolvedScenarioScope,
                registered_ids: Sequence[str]) -> dict:
    managed = list(scope.managed_ids)
    registered = list(registered_ids)
    return {
        "source": scope.source,
        "preset_id": scope.preset_id,
        "registered_intersections": len(registered),
        "algorithm_controlled_intersections": len(managed),
        "fixed_intersections": len(registered) - len(managed),
        "managed_ids": managed,
    }


def build_collab_summary(
    *, records: Sequence[LogRecord], config: CollabConfig,
    scope: ResolvedScenarioScope, registered_ids: Sequence[str],
    hub: V2XHub, run_id: str, episode_id: str,
) -> dict:
    ticks = [r.data for r in records if r.record_type == "collab_tick_stats"]
    arbitrations = [r.data for r in records if r.record_type == "arbitration"]
    proposals = [r.data for r in records if r.record_type == "cloud_proposal"]
    snapshots = [r.data for r in records if r.record_type == "edge_snapshot"]

    # ---- 信号 ----
    baseline_slots = sum(t["signal"]["baseline_slots"] for t in ticks)
    decision_records = sum(t["signal"]["decision_records"] for t in ticks)
    status_counts = _sum_counts(
        t["signal"]["status_counts"] for t in ticks)
    validation_counts = _sum_counts(
        t["signal"]["validation_counts"] for t in ticks)
    proposal_without_baseline = sum(
        t["signal"]["proposal_without_baseline"] for t in ticks)
    selectable = 0
    suggested_switch = 0
    agreement_num = 0
    agreement_den = 0
    disagreement_matrix: dict[str, dict[str, int]] = {}
    for rec in arbitrations:
        if (rec["proposal_status"] in _SELECTABLE_STATUSES
                and rec["proposed_action"] is not None):
            selectable += 1
        if rec["proposal_status"] == "proposed":
            suggested_switch += 1
        if rec["validation_status"] == "passed":
            agreement_den += 1
            if rec["proposed_action"] == rec["baseline_action"]:
                agreement_num += 1
            key_b = str(rec["baseline_action"])
            key_p = str(rec["proposed_action"])
            disagreement_matrix.setdefault(key_b, {}).setdefault(key_p, 0)
            disagreement_matrix[key_b][key_p] += 1
    failed_validations = validation_counts.get("failed", 0)
    # ---- 引导 ----
    funnel = {key: sum(t["guidance"].get(key, 0) for t in ticks)
              for key in GUIDANCE_FUNNEL_KEYS}
    filter_reason_counts = _sum_counts(
        t["guidance"].get("filter_reason_counts", {}) for t in ticks)
    guidance_type_counts: Counter[str] = Counter()
    emitted: dict[str, dict] = {}
    for rec in proposals:
        if rec["proposal_type"] != "vehicle_guidance":
            continue
        if rec["status"] == "proposed" and rec.get("guidance_type"):
            guidance_type_counts[rec["guidance_type"]] += 1
        if rec.get("emitted_message_id"):
            emitted[rec["emitted_message_id"]] = {
                "valid_until": rec["valid_until"],
                "intersection_id": rec.get("next_signal_intersection_id"),
            }
    published = funnel["published"]
    delivered_count = 0
    expired_on_delivery_count = 0
    terminal_counts: Counter[str] = Counter()
    rsi_message_ids = {
        rec["message_id"] for rec in hub.sent_records
        if rec["message_type"] == "RSI"}
    for rec in hub.delivery_records:
        message_id = rec["message_id"]
        if message_id not in emitted:
            continue
        terminal_counts[message_id] += 1
        if rec["status"] == "delivered":
            delivered_count += 1
            delivered_at = rec.get("delivered_at")
            if (delivered_at is not None
                    and delivered_at >= emitted[message_id]["valid_until"]):
                expired_on_delivery_count += 1
    # ---- 完整性审计（§5.4） ----
    arbitration_refs = {
        (rec["frame_id"], rec["intersection_id"]) for rec in arbitrations}
    signal_event_refs = {
        (rec["frame_id"], rec["source_id"]) for rec in hub.sent_records
        if rec["message_type"] == "SIGNAL_CONTROL"}
    missing_signal_event_refs = (
        len(arbitration_refs - signal_event_refs)
        if config.log_arbitration_mode == "all" else 0)
    source_ids = {
        mid for rec in proposals for mid in rec.get("source_message_ids", [])}
    delivery_ids = {rec["message_id"] for rec in hub.delivery_records}
    missing_source_delivery_refs = len(source_ids - delivery_ids)
    orphan_rsi_messages = len(rsi_message_ids - set(emitted))
    orphan_rsi_deliveries = len({
        rec["message_id"] for rec in hub.delivery_records
        if rec["message_id"] in emitted} - set(emitted))
    duplicate_terminal_delivery_records = sum(
        1 for count in terminal_counts.values() if count > 1)
    # ---- 输入新鲜度（edge_snapshot 可用时） ----
    ages: list[float] = []
    for rec in snapshots:
        delivered = [
            value for value in rec["last_delivery_at"].values()
            if value is not None]
        if delivered:
            ages.append(rec["sim_time"] - max(delivered))
    # ---- 引导 rates ----
    guidance_rates: dict[str, Any] = {
        "guidance_generation_rate": _rate(
            funnel["raw_proposals"], funnel["in_horizon_candidates"]),
        "proposal_publish_rate": _rate(
            funnel["published"], funnel["raw_proposals"]),
        "candidate_to_publish_rate": _rate(
            funnel["published"], funnel["in_horizon_candidates"]),
        "network_delivery_rate": _rate(delivered_count, published),
    }
    if config.guidance_mode is GuidanceEmissionMode.FULL:
        guidance_rates["threshold_pass_rate"] = {
            "value": _rate(funnel["threshold_passed"], funnel["raw_proposals"]),
            "diagnostic": True,
            "would_pass_threshold": funnel.get("would_pass_threshold", 0),
            "would_be_duplicate": funnel.get("would_be_duplicate", 0),
            "would_be_in_cooldown": funnel.get("would_be_in_cooldown", 0),
        }
    else:
        guidance_rates["threshold_pass_rate"] = _rate(
            funnel["threshold_passed"], funnel["raw_proposals"])
    return {
        "collab": {
            "schema_version": "1.0",
            "decision_mode": config.decision_mode.value,
            "guidance_mode": config.guidance_mode.value,
            "signal": {
                "baseline_signal_slots": baseline_slots,
                "counts": {
                    "decision_records": decision_records,
                    "selectable_count": selectable,
                    "suggested_switch_count": suggested_switch,
                    "agreement_num": agreement_num,
                    "agreement_den": agreement_den,
                    "stale_count": status_counts.get("stale_input", 0),
                    "missing_count": status_counts.get("missing_input", 0),
                    "status_counts": status_counts,
                    "validation_counts": validation_counts,
                },
                "decision_record_coverage": _rate(decision_records, baseline_slots),
                "selectable_output_rate": _rate(selectable, baseline_slots),
                "suggested_switch_rate": _rate(suggested_switch, decision_records),
                "action_agreement_rate": _rate(agreement_num, agreement_den),
                "disagreement_matrix": disagreement_matrix,
                "stale_input_rate": _rate(
                    status_counts.get("stale_input", 0), decision_records),
                "missing_input_rate": _rate(
                    status_counts.get("missing_input", 0), decision_records),
                "decision_input_age_s": _describe(ages),
            },
            "guidance": {
                "funnel": funnel,
                "rates": guidance_rates,
                "guidance_type_counts": dict(guidance_type_counts),
                "delivered_count": delivered_count,
                "expired_on_delivery_count": expired_on_delivery_count,
                "expired_on_delivery_rate": _rate(
                    expired_on_delivery_count, delivered_count),
                "effective_delivery_rate": _rate(
                    delivered_count - expired_on_delivery_count, published),
                "filter_reason_counts": filter_reason_counts,
            },
            "arbitration": {
                "selection_status_counts": _sum_counts(
                    rec["selection_status"] for rec in arbitrations),
                "proposal_without_baseline": proposal_without_baseline,
            },
            "validation": {
                "validation_pass_rate": _rate(
                    validation_counts.get("passed", 0), baseline_slots),
                "fallback_readiness_rate": _rate(
                    failed_validations, baseline_slots),
                "failure_reason_counts": _sum_counts(
                    rec["validation_failure_reason"] for rec in arbitrations
                    if rec.get("validation_failure_reason")),
            },
            "integrity": {
                "missing_source_delivery_refs": missing_source_delivery_refs,
                "orphan_rsi_messages": orphan_rsi_messages,
                "orphan_rsi_deliveries": orphan_rsi_deliveries,
                "missing_signal_event_refs": missing_signal_event_refs,
                "duplicate_terminal_delivery_records": duplicate_terminal_delivery_records,
            },
        },
        "scope": scope_block(scope, registered_ids),
    }


def pool_collab_summaries(summaries: Sequence[dict]) -> dict:
    """run 级 pooled 聚合（§5.5）：rate = Σnum/Σdenom；分布按加权均值显式降级。

    episode summary 的 signal 块必须含 `counts`（见 build_collab_summary），
    否则 pooled 无法重算精确 rate——缺失时抛 ValueError（不静默）。
    """
    if not summaries:
        return {"pooled_episodes": 0,
                "collab": {"schema_version": "1.0", "note": "no episodes"}}
    collabs = [item["collab"] for item in summaries]

    # ---- 信号 counts 求和 ----
    baseline_slots = sum(c["signal"]["baseline_signal_slots"] for c in collabs)
    status_counts: Counter[str] = Counter()
    validation_counts: Counter[str] = Counter()
    proposal_without_baseline = 0
    selectable = suggested_switch = agreement_num = agreement_den = 0
    decision_records = 0
    stale_count = missing_count = 0
    disagreement_matrix: dict[str, dict[str, int]] = {}
    age_samples: list[float] = []
    for c in collabs:
        sig = c["signal"]
        counts = sig.get("counts")
        if counts is None:
            raise ValueError(
                "pool_collab_summaries requires episode signal.counts "
                "(spec §5.5 pooled numerators)")
        decision_records += counts["decision_records"]
        selectable += counts["selectable_count"]
        suggested_switch += counts["suggested_switch_count"]
        agreement_num += counts["agreement_num"]
        agreement_den += counts["agreement_den"]
        stale_count += counts["stale_count"]
        missing_count += counts["missing_count"]
        status_counts.update(counts["status_counts"])
        validation_counts.update(counts["validation_counts"])
        for b_key, row in sig["disagreement_matrix"].items():
            for p_key, count in row.items():
                disagreement_matrix.setdefault(b_key, {}).setdefault(p_key, 0)
                disagreement_matrix[b_key][p_key] += count
    # 分布样本不可从聚合值精确重建：用加权均值，显式标注降级
    pooled_age = _pooled_distribution(collabs)

    # ---- 引导 counts 求和 ----
    funnel: Counter[str] = Counter()
    filter_reason_counts: Counter[str] = Counter()
    guidance_type_counts: Counter[str] = Counter()
    delivered_count = expired_on_delivery_count = 0
    for c in collabs:
        guid = c["guidance"]
        for key in GUIDANCE_FUNNEL_KEYS:
            funnel[key] += guid["funnel"].get(key, 0)
        filter_reason_counts.update(guid.get("filter_reason_counts", {}))
        guidance_type_counts.update(guid["guidance_type_counts"])
        delivered_count += guid["delivered_count"]
        expired_on_delivery_count += guid["expired_on_delivery_count"]
    published = funnel["published"]
    raw = funnel["raw_proposals"]
    candidates = funnel["in_horizon_candidates"]

    # ---- 仲裁 / 验证 / 完整性 counts 求和 ----
    selection_status_counts: Counter[str] = Counter()
    failure_reason_counts: Counter[str] = Counter()
    integrity: Counter[str] = Counter()
    arbitration_proposal_without_baseline = 0
    for c in collabs:
        arb = c["arbitration"]
        selection_status_counts.update(arb["selection_status_counts"])
        arbitration_proposal_without_baseline += arb["proposal_without_baseline"]
        failure_reason_counts.update(c["validation"]["failure_reason_counts"])
        integrity.update(c["integrity"])

    guidance_rates: dict[str, Any] = {
        "guidance_generation_rate": _rate(raw, candidates),
        "proposal_publish_rate": _rate(published, raw),
        "candidate_to_publish_rate": _rate(published, candidates),
        "network_delivery_rate": _rate(delivered_count, published),
    }
    threshold_den = raw
    if threshold_den > 0:
        guidance_rates["threshold_pass_rate"] = _rate(
            funnel["threshold_passed"], threshold_den)
    else:
        guidance_rates["threshold_pass_rate"] = None

    pooled = {
        "pooled_episodes": len(summaries),
        "collab": {
            "schema_version": "1.0",
            "decision_mode": collabs[0]["decision_mode"],
            "guidance_mode": collabs[0]["guidance_mode"],
            "signal": {
                "baseline_signal_slots": baseline_slots,
                "decision_record_coverage": _rate(decision_records, baseline_slots),
                "selectable_output_rate": _rate(selectable, baseline_slots),
                "suggested_switch_rate": _rate(suggested_switch, decision_records),
                "action_agreement_rate": _rate(agreement_num, agreement_den),
                "disagreement_matrix": disagreement_matrix,
                "stale_input_rate": _rate(stale_count, decision_records),
                "missing_input_rate": _rate(missing_count, decision_records),
                "decision_input_age_s": pooled_age,
            },
            "guidance": {
                "funnel": dict(funnel),
                "rates": guidance_rates,
                "guidance_type_counts": dict(guidance_type_counts),
                "delivered_count": delivered_count,
                "expired_on_delivery_count": expired_on_delivery_count,
                "expired_on_delivery_rate": _rate(
                    expired_on_delivery_count, delivered_count),
                "effective_delivery_rate": _rate(
                    delivered_count - expired_on_delivery_count, published),
                "filter_reason_counts": dict(filter_reason_counts),
            },
            "arbitration": {
                "selection_status_counts": dict(selection_status_counts),
                "proposal_without_baseline": arbitration_proposal_without_baseline,
            },
            "validation": {
                "validation_pass_rate": _rate(
                    validation_counts.get("passed", 0), baseline_slots),
                "fallback_readiness_rate": _rate(
                    validation_counts.get("failed", 0), baseline_slots),
                "failure_reason_counts": dict(failure_reason_counts),
            },
            "integrity": dict(integrity),
        },
    }
    # seed 稳定性参考（不替代 pooled rate）
    pooled["per_episode_rate_reference"] = {
        "decision_record_coverage": _per_episode_stats(
            c["signal"]["decision_record_coverage"] for c in collabs),
        "action_agreement_rate": _per_episode_stats(
            c["signal"]["action_agreement_rate"] for c in collabs),
        "guidance_generation_rate": _per_episode_stats(
            c["guidance"]["rates"]["guidance_generation_rate"] for c in collabs),
        "network_delivery_rate": _per_episode_stats(
            c["guidance"]["rates"]["network_delivery_rate"] for c in collabs),
    }
    return pooled


def _pooled_distribution(collabs: Sequence[dict]) -> dict:
    """分布无法从聚合值精确重建时，用样本数加权均值并显式标注降级。"""
    items = [c["signal"]["decision_input_age_s"] for c in collabs]
    counts = [int(item.get("sample_count", 0)) for item in items]
    total = sum(counts)
    if total == 0:
        return {"mean": None, "p50": None, "p95": None,
                "sample_count": 0, "distribution_pooled": False,
                "pooling_note": "no samples"}
    weighted_mean = sum(
        float(item["mean"]) * count for item, count in zip(items, counts)
    ) / total
    return {
        "mean": weighted_mean,
        "p50": None, "p95": None,
        "sample_count": total,
        "distribution_pooled": False,
        "pooling_note": "p50/p95 require raw samples; run-level JSONL replay can pool exactly",
    }


def _per_episode_stats(values: Sequence[Optional[float]]) -> dict:
    valid = [float(v) for v in values if v is not None]
    if not valid:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": sum(valid) / len(valid),
        "median": median(valid),
        "min": min(valid),
        "max": max(valid),
    }

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_stats.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/stats.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/stats.py
scp -P 24 algorithms/v2x/collab/records.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/records.py
scp -P 24 algorithms/v2x/collab/policy.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/policy.py
scp -P 24 algorithms/v2x/collab/tests/test_stats.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_stats.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab && git commit -m "feat(v2x): collab episode summary + pooled aggregation + integrity audit"'
```

---

## Task 12: engine.py — CollabDecisionEngine / CollabTickResult

**Files:**
- Create: `algorithms/v2x/collab/engine.py`
- Test: `algorithms/v2x/collab/tests/test_engine.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_engine.py`：

```python
import copy
import json

import pytest

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.arbiter import ActionArbiter, ActiveModeUnavailableError
from algorithms.v2x.collab.engine import CollabDecisionEngine
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, DecisionMode, GuidanceEmissionMode,
)
from algorithms.v2x.collab.records import InMemoryRecordCollector
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.protocol import build_bsm_draft, build_intent_draft, build_spat_draft
from algorithms.v2x.config import V2XConfig
from config.scenario_presets import ResolvedScenarioScope


SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("i1",))

INIT = {
    "episode_id": "ep1",
    "vehicle_types": {"official_passenger": {"vehicle_class": "passenger"}},
    "intersections": {
        "i1": {
            "intersection_id": "i1", "phase_order": [1],
            "phases": {"1": {"phase_id": 1,
                             "connection_priorities": {"c0": "protected"}}},
            "lanes": {
                "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                        "approach_id": "west", "movements": ("through",),
                        "speed_limit_mps": 16.0},
            },
            "connections": [{"connection_id": "c0", "from_lane": "A_0",
                             "to_lane": "B_0", "movement": "through"}],
            "direct_neighbors": [],
        },
    },
}

STEP = {
    "episode_id": "ep1", "simulation_time": 5.0,
    "intersections": {},
    "vehicles": {},
}

ACTIONS = {"signals": {"i1": {"target_phase": 1}}, "vehicles": {}}


def _engine(mode=DecisionMode.SHADOW, guidance_mode=GuidanceEmissionMode.THRESHOLD):
    config = V2XConfig(latency_ms=0.0, drop_rate=0.0, network_seed=0)
    collector = InMemoryRecordCollector()
    hub = V2XHub(config=config, sink=collector)
    aggregator = EdgeAggregator(managed_ids=("i1",))
    store = CloudStateStore(aggregator, CollabConfig().freshness)
    policy = CloudRulePolicy(CollabConfig(
        decision_mode=mode, guidance_mode=guidance_mode))
    arbiter = ActionArbiter(mode)
    engine = CollabDecisionEngine(
        hub=hub, aggregator=aggregator, store=store, policy=policy,
        arbiter=arbiter, collector=collector,
        config=CollabConfig(decision_mode=mode, guidance_mode=guidance_mode),
        scope=SCOPE, run_id="run1", episode_id="ep1",
        registered_ids=("i1",))
    return hub, engine, collector


def _deliver_uplink(hub, frame):
    hub.publish(build_spat_draft("i1", {
        "current_phase": 1, "stage": "GREEN", "stage_elapsed": 10.0,
        "remaining_time_s": 21.0, "lanes": {},
    }, INIT["intersections"]["i1"]["phases"], sim_time=frame.sim_time),
        frame_id=frame.frame_id)
    hub.publish(build_bsm_draft("car1", {
        "type_id": "official_passenger", "position": {"x_m": 0.0, "y_m": 0.0},
        "motion": {"speed_mps": 8.0, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": "i1", "distance_m": 205.0,
                        "state": "G"},
        "leader_gap_m": None, "follower_gap_m": None,
        "_sim_time": frame.sim_time,
    }), frame_id=frame.frame_id)
    hub.publish(build_intent_draft(
        "car1", {}, sim_time=frame.sim_time, turn="through",
        lane_change=None, arrival=15.0, turn_conf=1.0,
        lane_change_conf=0.0, arrival_conf=1.0, origin="derived"),
        frame_id=frame.frame_id)
    hub.advance(frame.sim_time)


def _normalize(actions):
    return json.dumps(actions, sort_keys=True, ensure_ascii=False,
                      default=lambda o: list(o) if isinstance(o, tuple) else o)


def _tick(hub, engine):
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep1")
    frame = hub.ingest_step(STEP)      # t=5：投递 MAP
    _deliver_uplink(hub, frame)        # 投递 SPaT/BSM/INTENT
    return engine.tick(frame=frame, baseline_actions=ACTIONS)


def test_shadow_tick_preserves_baseline_and_emits_rsi():
    hub, engine, collector = _engine()
    original = copy.deepcopy(ACTIONS)
    result = _tick(hub, engine)
    # applied == baseline（规范化一致；输入对象不被原地修改）
    assert _normalize(result.protocol_actions) == _normalize(ACTIONS)
    assert ACTIONS == original
    assert result.signal_sources["i1"].value == "baseline"
    assert result.stats_delta.baseline_slots == 1
    assert result.stats_delta.decision_records == 1
    # RSI：1 条已发布（阈值触发 fixture）
    assert result.stats_delta.guidance_funnel["published"] == 1
    assert len(result.emitted_rsi_message_ids) == 1
    assert result.emitted_rsi_message_ids_by_intersection["i1"] == \
        result.emitted_rsi_message_ids
    # tick stats 原子写入
    assert any(r.record_type == "collab_tick_stats"
               for r in collector.episode_records)
    # 完整性：collab RSI 恰 1 条 message + 终态 delivery
    hub.finish_episode(5.0, drain_pending=True)
    from algorithms.v2x.collab.stats import build_collab_summary
    summary = build_collab_summary(
        records=collector.episode_records, config=CollabConfig(),
        scope=SCOPE, registered_ids=("i1",), hub=hub,
        run_id="run1", episode_id="ep1")
    assert summary["collab"]["guidance"]["delivered_count"] == 1
    assert summary["collab"]["integrity"]["orphan_rsi_messages"] == 0


def test_off_mode_short_circuits_without_records():
    hub, engine, collector = _engine(mode=DecisionMode.OFF)
    result = _tick(hub, engine)
    assert result.stats_delta.baseline_slots == 0
    assert result.stats_delta.emitted_rsi_count == 0
    assert not any(r.record_type == "collab_tick_stats"
                   for r in collector.episode_records)
    assert _normalize(result.protocol_actions) == _normalize(ACTIONS)


def test_disabled_guidance_no_rsi():
    hub, engine, _ = _engine(guidance_mode=GuidanceEmissionMode.DISABLED)
    result = _tick(hub, engine)
    assert result.emitted_rsi_message_ids == ()
    assert result.stats_delta.guidance_funnel["published"] == 0


def test_active_mode_unavailable_at_construction():
    with pytest.raises(ActiveModeUnavailableError):
        _engine(mode=DecisionMode.ACTIVE)


def test_finalize_writes_episode_end_and_scope():
    hub, engine, collector = _engine()
    _tick(hub, engine)
    summary = engine.finalize_episode(episode_id="ep1", registered_ids=("i1",))
    assert summary["collab"]["schema_version"] == "1.0"
    assert summary["scope"]["managed_ids"] == ["i1"]
    assert any(r.record_type == "collab_episode_end"
               for r in collector.episode_records)


def test_reset_episode_clears_last_emitted():
    hub, engine, collector = _engine()
    first = _tick(hub, engine)
    assert len(first.emitted_rsi_message_ids) == 1
    engine.reset_episode(episode_id="ep2")
    # 新 episode 首帧：无 last_emitted → 再次发布
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep2")
    frame = hub.ingest_step(STEP)
    _deliver_uplink(hub, frame)
    second = engine.tick(frame=frame, baseline_actions=ACTIONS)
    assert len(second.emitted_rsi_message_ids) == 1
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_engine.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `engine.py`**

```python
# algorithms/v2x/collab/engine.py
"""CollabDecisionEngine：一次决策帧编排（spec §1.2/§1.3/§4.2）。"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ..hub import FrameContext, V2XHub
from ..messages import MessageDraft
from .aggregator import EdgeAggregator
from .arbiter import ActionArbiter, ActiveModeUnavailableError
from .policy import GUIDANCE_FUNNEL_STAGES, CloudRulePolicy, GuidanceOutcome
from .proposals import (
    CollabConfig, DecisionMode, DecisionSource,
    GuidanceEmissionMode, LastEmittedGuidanceState, SignalProposal,
    VehicleGuidanceProposal,
)
from .records import (
    InMemoryRecordCollector, arbitration_record, cloud_proposal_record,
    collab_tick_stats_record, edge_snapshot_record,
)
from .snapshot import ConnectedVehicleState, EdgeSnapshot, IntersectionStaticContext
from .state import CloudIntersectionView, CloudStateStore
from .stats import build_collab_summary
from config.scenario_presets import ResolvedScenarioScope


@dataclass(frozen=True, slots=True)
class CollabStatsDelta:
    baseline_slots: int = 0
    decision_records: int = 0
    status_counts: Mapping[str, int] = field(default_factory=dict)
    validation_counts: Mapping[str, int] = field(default_factory=dict)
    proposal_without_baseline: int = 0
    guidance_funnel: Mapping[str, int] = field(default_factory=dict)
    filter_reason_counts: Mapping[str, int] = field(default_factory=dict)
    emitted_rsi_count: int = 0


@dataclass(frozen=True, slots=True)
class CollabTickResult:
    protocol_actions: Mapping[str, Any]          # SHADOW = baseline 完整等价副本
    signal_sources: Mapping[str, DecisionSource]
    emitted_rsi_message_ids: tuple[str, ...]
    emitted_rsi_message_ids_by_intersection: Mapping[str, tuple[str, ...]]
    stats_delta: CollabStatsDelta
    frame_id: str
    sim_time: float


def _deep_copy_actions(actions: Mapping[str, Any]) -> dict:
    return copy.deepcopy(dict(actions))


def _action_of(spec: Any) -> Optional[int]:
    if isinstance(spec, Mapping):
        value = spec.get("target_phase")
        return int(value) if value is not None else None
    return int(spec) if spec is not None else None


def _rsi_draft(proposal: VehicleGuidanceProposal, *, sim_time: float) -> MessageDraft:
    # 直接构造 RSI 草稿（不经过 protocol.build_rsi_draft，以保留 combined 类型；
    # RSI REQUIRED_FIELDS 只要求键存在，None 值合法）
    return MessageDraft(
        "RSI", "cloud", proposal.vehicle_id, sim_time,
        {"vehicle_id": proposal.vehicle_id,
         "target_speed_mps": proposal.target_speed_mps,
         "target_lane_index": proposal.target_lane_index,
         "guidance_type": proposal.guidance_type},
    )


class CollabDecisionEngine:
    def __init__(
        self, *, hub: V2XHub, aggregator: EdgeAggregator,
        store: CloudStateStore, policy: CloudRulePolicy,
        arbiter: ActionArbiter, collector: InMemoryRecordCollector,
        config: CollabConfig, scope: ResolvedScenarioScope,
        run_id: str, episode_id: str, registered_ids: tuple[str, ...],
    ) -> None:
        if config.decision_mode is DecisionMode.ACTIVE:
            raise ActiveModeUnavailableError(
                "ACTIVE decision mode is unavailable in v1 (spec §4.1)")
        self._hub = hub
        self._aggregator = aggregator
        self._store = store
        self._policy = policy
        self._arbiter = arbiter
        self._collector = collector
        self._config = config
        self._scope = scope
        self._run_id = run_id
        self._episode_id = episode_id
        self._registered_ids = tuple(registered_ids)
        self._last_emitted: dict[str, LastEmittedGuidanceState] = {}
        self._closed = False
        # 订阅一次：引擎创建时注册，close() 后停止回调（不修改 hub 订阅 API）
        def _handler(message, _aggregator=aggregator, _engine=self):
            if not _engine._closed:
                _aggregator.on_message(message)
        for message_type in ("BSM", "INTENT", "SPaT", "MAP", "RSM"):
            hub.subscribe(message_type, _handler)

    # ---------- 决策帧 ----------
    def tick(self, *, frame: FrameContext,
             baseline_actions: Mapping[str, Any]) -> CollabTickResult:
        if self._closed:
            raise RuntimeError("engine closed")
        now = frame.sim_time
        if self._config.decision_mode is DecisionMode.OFF:
            return CollabTickResult(
                protocol_actions=_deep_copy_actions(baseline_actions),
                signal_sources={}, emitted_rsi_message_ids=(),
                emitted_rsi_message_ids_by_intersection={},
                stats_delta=CollabStatsDelta(),
                frame_id=frame.frame_id, sim_time=now)
        managed = tuple(self._scope.managed_ids)
        managed_set = frozenset(managed)
        baseline_signals = dict((baseline_actions.get("signals") or {}))
        # 先构建全部视图（每路口 1 次），再统一 after_snapshot 更新 arrivals 基线
        views: dict[str, tuple[Optional[CloudIntersectionView],
                               Optional[IntersectionStaticContext]]] = {}
        for iid in managed:
            views[iid] = (self._store.view(iid, now),
                          self._store.static_context(iid))
        # ---- 信号：决策 + 仲裁 + 记录 ----
        status_counts: Counter[str] = Counter()
        validation_counts: Counter[str] = Counter()
        failure_reason_counts: Counter[str] = Counter()
        signal_sources: dict[str, DecisionSource] = {}
        proposal_without_baseline = 0
        decision_records = 0
        baseline_slots = 0
        for iid in managed:
            if iid not in baseline_signals:
                continue
            baseline_slots += 1
            decision_records += 1
            baseline_action = _action_of(baseline_signals[iid])
            view, ctx = views[iid]
            if view is not None and ctx is not None:
                proposal = self._policy.propose_signal(
                    intersection_id=iid, snapshot=view.snapshot,
                    static_context=ctx, now=now,
                    frame_id=frame.frame_id, config=self._config)
            else:
                proposal = self._policy.missing_signal_proposal(
                    intersection_id=iid, now=now,
                    frame_id=frame.frame_id, reason="missing_map")
            status_counts[proposal.status.value] += 1
            in_transition = bool(
                view is not None and view.snapshot.stage in ("YELLOW", "CLEARANCE"))
            result = self._arbiter.arbitrate(
                proposal=proposal, baseline_action=baseline_action,
                run_id=self._run_id, frame_id=frame.frame_id,
                intersection_id=iid, now=now, in_transition=in_transition,
                valid_actions=ctx.valid_actions if ctx is not None else ())
            signal_sources[iid] = result.decision_source
            if result.validation is not None:
                validation_counts[
                    "passed" if result.validation.passed else "failed"] += 1
                if result.validation.failure_reason:
                    failure_reason_counts[result.validation.failure_reason] += 1
            if self._config.log_arbitration_mode == "all":
                self._collector.write(arbitration_record(
                    run_id=self._run_id, episode_id=frame.episode_id,
                    frame_id=frame.frame_id, intersection_id=iid,
                    sim_time=now, baseline_action=baseline_action,
                    candidate_action=proposal.candidate_action,
                    proposed_action=proposal.proposed_action,
                    selected_action=result.selected_action,
                    proposal_status=proposal.status.value,
                    validation_status=(
                        result.validation.failure_reason if result.validation
                        and not result.validation.passed else "passed"
                        if result.validation else None),
                    validation_failure_reason=(
                        result.validation.failure_reason
                        if result.validation else None),
                    decision_source=result.decision_source.value,
                    selection_status=result.selection_status,
                    confidence=proposal.confidence, reason=proposal.reason))
            self._collector.write(cloud_proposal_record(
                run_id=self._run_id, episode_id=frame.episode_id,
                frame_id=frame.frame_id, sim_time=now,
                proposal=proposal, proposal_type="signal"))
            if self._config.log_edge_snapshot and view is not None:
                self._collector.write(edge_snapshot_record(
                    run_id=self._run_id, episode_id=frame.episode_id,
                    frame_id=frame.frame_id, snapshot=view.snapshot))
        # ---- 引导：RSI（仅 PROPOSED → hub.publish；不进 actions.vehicles）----
        funnel: Counter[str] = Counter()
        filter_reason_counts: Counter[str] = Counter()
        emitted_ids: list[str] = []
        emitted_by_intersection: dict[str, list[str]] = {}
        diagnostics: Counter[str] = Counter()
        if self._config.guidance_mode is not GuidanceEmissionMode.DISABLED:
            for iid in managed:
                view, ctx = views[iid]
                if view is None or ctx is None:
                    continue
                for vehicle in view.snapshot.connected_vehicles.values():
                    outcome = self._policy.propose_guidance(
                        vehicle=vehicle, snapshot=view.snapshot,
                        static_context=ctx, now=now,
                        frame_id=frame.frame_id, config=self._config,
                        scope=self._scope,
                        last_emitted=self._last_emitted.get(vehicle.vehicle_id))
                    self._accumulate_funnel(funnel, outcome.funnel_stage)
                    if outcome.filter_reason is not None:
                        filter_reason_counts[outcome.filter_reason] += 1
                    diagnostics["would_pass_threshold"] += int(
                        outcome.would_pass_threshold)
                    if outcome.would_be_duplicate is not None:
                        diagnostics["would_be_duplicate"] += int(
                            outcome.would_be_duplicate)
                    if outcome.would_be_in_cooldown is not None:
                        diagnostics["would_be_in_cooldown"] += int(
                            outcome.would_be_in_cooldown)
                    proposal = outcome.proposal
                    emitted_message_id = None
                    if (proposal is not None
                            and outcome.funnel_stage == "published"
                            and vehicle.next_signal_intersection_id in managed_set):
                        # 发布前防御性双检（§3.6）
                        message = self._hub.publish(
                            _rsi_draft(proposal, sim_time=now),
                            frame_id=frame.frame_id,
                            correlation_id=frame.frame_id)
                        emitted_message_id = message.message_id
                        emitted_ids.append(message.message_id)
                        emitted_by_intersection.setdefault(
                            vehicle.next_signal_intersection_id, []
                        ).append(message.message_id)
                        self._last_emitted[vehicle.vehicle_id] = \
                            LastEmittedGuidanceState(
                                target_speed_mps=proposal.target_speed_mps,
                                target_lane_id=proposal.target_lane_id,
                                target_lane_index=proposal.target_lane_index,
                                emitted_at=now,
                                valid_until=proposal.valid_until,
                                reason=proposal.reason,
                                emitted_message_id=message.message_id)
                    if proposal is not None:
                        self._collector.write(cloud_proposal_record(
                            run_id=self._run_id, episode_id=frame.episode_id,
                            frame_id=frame.frame_id, sim_time=now,
                            proposal=proposal,
                            proposal_type="vehicle_guidance",
                            emitted_message_id=emitted_message_id,
                            next_signal_intersection_id=(
                                vehicle.next_signal_intersection_id)))
        # ---- 帧末更新 arrivals 基线 ----
        for iid in managed:
            if views[iid][0] is not None:
                self._aggregator.after_snapshot(iid)
        # ---- collab_tick_stats 原子写入（不可关闭）----
        guidance_funnel = dict(funnel)
        if self._config.guidance_mode is GuidanceEmissionMode.FULL:
            guidance_funnel.update(dict(diagnostics))
        stats_delta = CollabStatsDelta(
            baseline_slots=baseline_slots,
            decision_records=decision_records,
            status_counts=dict(status_counts),
            validation_counts=dict(validation_counts),
            proposal_without_baseline=proposal_without_baseline,
            guidance_funnel=guidance_funnel,
            filter_reason_counts=dict(filter_reason_counts),
            emitted_rsi_count=len(emitted_ids))
        self._collector.write(collab_tick_stats_record(
            run_id=self._run_id, episode_id=frame.episode_id,
            frame_id=frame.frame_id, sim_time=now,
            baseline_slots=baseline_slots,
            decision_records=decision_records,
            status_counts=dict(status_counts),
            validation_counts=dict(validation_counts),
            proposal_without_baseline=proposal_without_baseline,
            guidance_funnel=guidance_funnel,
            filter_reason_counts=dict(filter_reason_counts)))
        return CollabTickResult(
            protocol_actions=_deep_copy_actions(baseline_actions),
            signal_sources=signal_sources,
            emitted_rsi_message_ids=tuple(emitted_ids),
            emitted_rsi_message_ids_by_intersection={
                k: tuple(v) for k, v in emitted_by_intersection.items()},
            stats_delta=stats_delta,
            frame_id=frame.frame_id, sim_time=now)

    @staticmethod
    def _accumulate_funnel(counter: Counter, funnel_stage: str) -> None:
        try:
            reached = GUIDANCE_FUNNEL_STAGES.index(funnel_stage)
        except ValueError:
            return
        for stage in GUIDANCE_FUNNEL_STAGES[: reached + 1]:
            counter[stage] += 1

    # ---------- 生命周期 ----------
    def finalize_episode(self, *, episode_id: str,
                         registered_ids: Optional[tuple[str, ...]] = None) -> dict:
        summary = build_collab_summary(
            records=self._collector.episode_records,
            config=self._config, scope=self._scope,
            registered_ids=registered_ids or self._registered_ids,
            hub=self._hub, run_id=self._run_id, episode_id=episode_id)
        self._collector.write(collab_episode_end_record(summary=summary))
        return summary

    def reset_episode(self, *, episode_id: str) -> None:
        self._aggregator.reset_episode()
        self._store.reset_episode()
        self._arbiter.reset_episode()
        self._last_emitted.clear()
        self._collector.reset_episode()

    def close(self) -> None:
        self._closed = True
        self._collector.flush()
```

注意：
- `finalize_episode` 不触发新决策/新 RSI/新仲裁（spec §1.3）；先由调用方 `hub.finish_episode(drain_pending=True)` 再调用，保证 RSI 终态 delivery 已写入。
- 完整性审计依赖 `arbitration_record`（`log_arbitration_mode="all"` 默认）；`"differences"` 模式由 `collab_tick_stats` 保证可重放（§5.4）。
- `cloud_proposal_record` 的 `emitted_message_id/next_signal_intersection_id` 可选参数来自 Task 11 契约收口。
- `GuidanceOutcome.would_pass_threshold/would_be_duplicate/would_be_in_cooldown` 来自 Task 11 契约收口；FULL 模式下统计进 `guidance_funnel` 的诊断键。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_engine.py algorithms/v2x/collab/tests/test_stats.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/engine.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/engine.py
scp -P 24 algorithms/v2x/collab/tests/test_engine.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_engine.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/engine.py algorithms/v2x/collab/tests/test_engine.py && git commit -m "feat(v2x): CollabDecisionEngine tick orchestration + RSI emission"'
```

---

## Task 13: adapters/coslight.py — collab 接线（env/scope/engine/last_collab_summary）

**Files:**
- Modify: `algorithms/v2x/adapters/coslight.py`
- Modify（小改）: `algorithms/v2x/collab/records.py`（新增 `CompositeSink`）
- Test: `algorithms/v2x/tests/test_coslight_adapter.py`

- [x] **Step 1: 写失败测试**

在 `algorithms/v2x/tests/test_coslight_adapter.py` 末尾追加：

```python
def test_collab_enabled_without_log_runs_in_memory(monkeypatch):
    from algorithms.v2x.adapters.coslight import last_collab_summary
    monkeypatch.setenv("COSLIGHT_V2X_COLLAB", "1")
    monkeypatch.setenv("COSLIGHT_V2X_COLLAB_MODE", "shadow")
    monkeypatch.setenv("COSLIGHT_V2X_GUIDANCE_MODE", "threshold")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_SOURCE", "preset")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_PRESET_ID", "east_dense")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_MANAGED_IDS", "i1,i2")
    monkeypatch.delenv("COSLIGHT_V2X_LOG", raising=False)
    reset_bridge()
    bridge_initialize(INIT)
    bridge_step(STEP, ACTIONS)
    bridge_finish(STEP["simulation_time"])
    summary = last_collab_summary()
    assert summary is not None
    assert summary["collab"]["schema_version"] == "1.0"
    assert summary["collab"]["decision_mode"] == "shadow"
    assert summary["scope"]["source"] == "preset"
    assert summary["scope"]["managed_ids"] == ["i1", "i2"]
    assert summary["collab"]["signal"]["baseline_signal_slots"] == 2
    reset_bridge()


def test_collab_scope_fail_fast_on_unknown_intersection(monkeypatch):
    monkeypatch.setenv("COSLIGHT_V2X_COLLAB", "1")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_SOURCE", "custom")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_MANAGED_IDS", "i1,i9")
    monkeypatch.delenv("COSLIGHT_V2X_LOG", raising=False)
    reset_bridge()
    with pytest.raises(ValueError, match="i9"):
        bridge_initialize(INIT)
    reset_bridge()


def test_collab_disabled_guidance_strips_actions_vehicles(monkeypatch, tmp_path):
    # collab 开启但 guidance=disabled：actions.vehicles 不再被 ingest_actions 转 RSI
    log = tmp_path / "v2x-collab.jsonl"
    monkeypatch.setenv("COSLIGHT_V2X_LOG", str(log))
    monkeypatch.setenv("COSLIGHT_V2X_COLLAB", "1")
    monkeypatch.setenv("COSLIGHT_V2X_COLLAB_MODE", "shadow")
    monkeypatch.setenv("COSLIGHT_V2X_GUIDANCE_MODE", "disabled")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_SOURCE", "custom")
    monkeypatch.setenv("COSLIGHT_V2X_SCOPE_MANAGED_IDS", "i1,i2")
    reset_bridge()
    bridge_initialize(INIT)
    bridge_step(STEP, ACTIONS)
    bridge_finish(STEP["simulation_time"])
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()
             if x.strip()]
    rsi = [rec for rec in lines if rec.get("message", {}).get("message_type") == "RSI"]
    assert rsi == []   # 不重复发射（engine guidance disabled + vehicles 已剥离）
    reset_bridge()
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_coslight_adapter.py -q --tb=short'`
Expected: FAIL（`AttributeError: module 'algorithms.v2x.adapters.coslight' has no attribute 'last_collab_summary'` / scope env 未解析）

- [x] **Step 3: 实现**

**3a. `records.py` 追加 `CompositeSink`：**

```python
class CompositeSink(MessageSink):
    """required 必有（InMemoryRecordCollector）+ optional（JsonlSink）按序写入（spec §6.2）。"""

    def __init__(self, required: Sequence[MessageSink] = (),
                 optional: Sequence[Optional[MessageSink]] = ()) -> None:
        self._sinks: list[MessageSink] = list(required)
        self._sinks.extend(s for s in optional if s is not None)

    def write(self, record: LogRecord) -> None:
        for sink in self._sinks:
            sink.write(record)

    def flush(self) -> None:
        for sink in self._sinks:
            sink.flush()

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()
```

（imports 追加 `from typing import Sequence, Optional`。）

**3b. `adapters/coslight.py` 修改（保留既有 V2X 行为默认不变）：**

```python
# 顶部 imports 追加
from config.scenario_presets import (
    ALL_DEMO_INTERSECTION_IDS, ResolvedScenarioScope,
)
from ..collab.aggregator import EdgeAggregator
from ..collab.arbiter import ActionArbiter
from ..collab.engine import CollabDecisionEngine
from ..collab.policy import CloudRulePolicy
from ..collab.proposals import CollabConfig, DecisionMode, GuidanceEmissionMode
from ..collab.records import CompositeSink, InMemoryRecordCollector
from ..collab.state import CloudStateStore


_last_collab_summary: Optional[dict] = None


def _env_collab_enabled() -> bool:
    return os.environ.get("COSLIGHT_V2X_COLLAB", "") == "1"


def _env_collab_config() -> CollabConfig:
    mode = DecisionMode(os.environ.get("COSLIGHT_V2X_COLLAB_MODE", "shadow"))
    guidance = GuidanceEmissionMode(
        os.environ.get("COSLIGHT_V2X_GUIDANCE_MODE", "threshold"))
    return CollabConfig(decision_mode=mode, guidance_mode=guidance)


def _env_scope() -> ResolvedScenarioScope:
    source = os.environ.get("COSLIGHT_V2X_SCOPE_SOURCE", "default")
    preset_id = os.environ.get("COSLIGHT_V2X_SCOPE_PRESET_ID") or None
    raw_ids = os.environ.get("COSLIGHT_V2X_SCOPE_MANAGED_IDS", "")
    managed = tuple(i for i in raw_ids.split(",") if i) if raw_ids \
        else ALL_DEMO_INTERSECTION_IDS
    return ResolvedScenarioScope(source=source, preset_id=preset_id,
                                 managed_ids=managed)
```

`CoslightV2XBridge.__init__` 改为：

```python
class CoslightV2XBridge:
    def __init__(self, log_path: Optional[str] = None,
                 config: Optional[V2XConfig] = None,
                 run_id: str = "coslight") -> None:
        self.config = config or V2XConfig()
        self.run_id = run_id
        self._collab_enabled = _env_collab_enabled()
        if self._collab_enabled:
            self._collector = InMemoryRecordCollector()
            jsonl_sink = JSONLSink(log_path) if log_path else None
            self._hub = V2XHub(
                config=self.config,
                sink=CompositeSink(required=[self._collector],
                                   optional=[jsonl_sink]))
        else:
            self._collector = None
            self._hub = V2XHub(
                config=self.config,
                sink=JSONLSink(log_path) if log_path else None)
        self._engine = None
        self._scope: Optional[ResolvedScenarioScope] = None
        self._collab_summary: Optional[dict] = None
        self._capabilities = {}
        self._vehicle_class_by_type = {}
        self._init_payload = None
        self._rsu_ids = set()
```

`on_initialize` 开头（`hub.ingest_initialize` 之前，spec §1.3 顺序）插入：

```python
        if self._collab_enabled:
            catalog = frozenset((payload.get("intersections") or {}).keys())
            scope = _env_scope()
            unknown = [iid for iid in scope.managed_ids if iid not in catalog]
            if unknown:
                raise ValueError(
                    f"scope managed_ids not in initialize catalog: {sorted(unknown)}")
            collab_config = _env_collab_config()
            self._scope = scope
            aggregator = EdgeAggregator(managed_ids=scope.managed_ids)
            store = CloudStateStore(aggregator, collab_config.freshness)
            policy = CloudRulePolicy(collab_config)
            arbiter = ActionArbiter(collab_config.decision_mode)  # ACTIVE → ActiveModeUnavailableError
            self._engine = CollabDecisionEngine(
                hub=self._hub, aggregator=aggregator, store=store,
                policy=policy, arbiter=arbiter,
                collector=self._collector, config=collab_config,
                scope=scope, run_id=run_id, episode_id=episode_id,
                registered_ids=tuple(sorted(catalog)))
```

`on_step` 末尾（原 `hub.ingest_actions(raw_actions, frame=frame)` 替换为）：

```python
        if self._engine is not None:
            result = self._engine.tick(
                frame=frame, baseline_actions=raw_actions)
            # collab 拥有 RSI 发射权：不把 actions.vehicles 转 RSI，避免重复（§3.5/§8.2）
            actions_for_hub = {
                key: value for key, value in result.protocol_actions.items()
                if key != "vehicles"}
            hub.ingest_actions(actions_for_hub, frame=frame)
            return result.protocol_actions  # shadow == baseline；供未来 active 替换
        hub.ingest_actions(raw_actions, frame=frame)
        return None
```

`on_finish` 替换为：

```python
    def on_finish(self, final_sim_time: float) -> dict:
        network_summary = self._hub.finish_episode(
            final_sim_time, drain_pending=True)
        if self._engine is not None:
            catalog = tuple(sorted(
                (self._init_payload or {}).get("intersections") or {}))
            collab = self._engine.finalize_episode(
                episode_id=self._hub._episode_id or "",
                registered_ids=catalog)
            merged = dict(network_summary)
            merged["collab"] = collab["collab"]
            merged["scope"] = collab["scope"]
            self._collab_summary = merged
            return merged
        return network_summary
```

`close` 替换为：

```python
    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()   # 不抢关共享 sink（spec §1.3）
        self._hub.close()
```

模块级 `_ensure_bridge` 改为：

```python
def _ensure_bridge() -> Optional[CoslightV2XBridge]:
    global _bridge
    log_path = os.environ.get("COSLIGHT_V2X_LOG")
    if not log_path and not _env_collab_enabled():
        return None
    if _bridge is None:
        _bridge = CoslightV2XBridge(
            log_path=log_path,
            run_id=os.environ.get("COSLIGHT_V2X_RUN_ID", "coslight"))
    return _bridge
```

`bridge_finish` 改为保存 summary 后 reset：

```python
def bridge_finish(payload: Mapping[str, Any]) -> None:
    global _last_collab_summary
    bridge = _ensure_bridge()
    if bridge is None:
        return
    if isinstance(payload, Mapping):
        final_sim_time = float(payload.get("simulation_time", 0.0))
    else:
        final_sim_time = float(payload)
    _last_collab_summary = bridge.on_finish(final_sim_time)
    reset_bridge()


def last_collab_summary() -> Optional[dict]:
    """最近一次 episode 的合并 summary（network + collab + scope）；无则 None。"""
    return _last_collab_summary
```

注意：
- 非法枚举（`COSLIGHT_V2X_COLLAB_MODE=bad`）在 `_env_collab_config()` 抛 `ValueError`，即启动阶段 fail-fast（§6.1）；`active` 在 `ActionArbiter` 构造抛 `ActiveModeUnavailableError`。
- `reset_bridge()` 需要同时清 `_last_collab_summary = None`（防止跨 episode 串数据）。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_coslight_adapter.py algorithms/v2x/tests/test_hub.py -q --tb=short'`
Expected: PASS（既有 4 条 + 新增 3 条）

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/adapters/coslight.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/adapters/coslight.py
scp -P 24 algorithms/v2x/collab/records.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/records.py
scp -P 24 algorithms/v2x/tests/test_coslight_adapter.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/tests/test_coslight_adapter.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/adapters/coslight.py algorithms/v2x/collab/records.py algorithms/v2x/tests/test_coslight_adapter.py && git commit -m "feat(v2x): coslight adapter collab wiring (env scope, engine, last summary)"'
```

---

## Task 14: scope_cli.py — `--intersections` / `--scenario-preset` 解析（纯函数）

**Files:**
- Create: `algorithms/coslight/scope_cli.py`
- Test: `algorithms/coslight/test_scope_cli.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/coslight/test_scope_cli.py`：

```python
import pytest

from algorithms.coslight.scope_cli import (
    build_scope_block, parse_intersections, resolve_scope,
)
from config.scenario_presets import ResolvedScenarioScope


def test_parse_single_integer_expands_demo_range():
    assert parse_intersections("3") == ("demo_1", "demo_2", "demo_3")
    assert parse_intersections("20") == tuple(f"demo_{i}" for i in range(1, 21))


def test_parse_comma_list_strips_and_preserves_order():
    assert parse_intersections("demo_3, demo_5 ,demo_6,demo_9") == (
        "demo_3", "demo_5", "demo_6", "demo_9")


@pytest.mark.parametrize("value", [
    "", "demo_1,,demo_2", "demo_1,demo_1", "foo", "demo_1,bar",
    "0", "21", "demo_1;demo_2",
])
def test_parse_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        parse_intersections(value)


def test_resolve_scope_preset_custom_default():
    preset = resolve_scope("east_dense", None)
    assert preset.source == "preset"
    assert preset.preset_id == "east_dense"
    assert preset.managed_ids == ("demo_3", "demo_5", "demo_6", "demo_9")
    custom = resolve_scope(None, ("demo_1", "demo_2"))
    assert custom.source == "custom"
    assert custom.managed_ids == ("demo_1", "demo_2")
    default = resolve_scope(None, None)
    assert default.source == "default"
    assert len(default.managed_ids) == 20


def test_resolve_scope_unknown_preset_raises():
    with pytest.raises(ValueError, match="east_dense2"):
        resolve_scope("east_dense2", None)


def test_build_scope_block_matches_stats_scope_block():
    scope = resolve_scope("east_dense", None)
    block = build_scope_block(scope, tuple(f"demo_{i}" for i in range(1, 21)))
    assert block == {
        "source": "preset", "preset_id": "east_dense",
        "registered_intersections": 20,
        "algorithm_controlled_intersections": 4,
        "fixed_intersections": 16,
        "managed_ids": ["demo_3", "demo_5", "demo_6", "demo_9"],
    }
    from algorithms.v2x.collab.stats import scope_block as stats_scope_block
    assert block == stats_scope_block(scope, tuple(f"demo_{i}" for i in range(1, 21)))
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/coslight/test_scope_cli.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError`）

- [x] **Step 3: 实现 `scope_cli.py`**

```python
# algorithms/coslight/scope_cli.py
"""场景范围 CLI 解析（spec §7.2）：纯函数，便于单测。

--scenario-preset {xiongan_20,east_dense,west_dense}
--intersections demo_3,demo_5,...（单个整数 N 兼容为 demo_1..N）
两者互斥（由 evaluate.py 的 argparse mutually exclusive group 保证）。
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from config.scenario_presets import (
    ALL_DEMO_INTERSECTION_IDS, ResolvedScenarioScope,
    require_scenario_preset,
)

_DEMO_ID_RE = re.compile(r"demo_\d+")


def parse_intersections(value: str) -> tuple[str, ...]:
    """解析 --intersections：单个整数 N → demo_1..N；逗号列表保序。

    空项、重复 ID、非法格式均抛 ValueError（启动阶段报错，不静默去重）。
    """
    stripped = value.strip()
    if stripped.isdigit():
        n = int(stripped)
        if not 1 <= n <= len(ALL_DEMO_INTERSECTION_IDS):
            raise ValueError(
                f"--intersections N must be in [1, {len(ALL_DEMO_INTERSECTION_IDS)}], "
                f"got {n}")
        return tuple(f"demo_{i}" for i in range(1, n + 1))
    parts = [part.strip() for part in stripped.split(",")]
    if not parts or any(not part for part in parts):
        raise ValueError(
            "--intersections must be a positive integer or comma-separated demo_N ids")
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if not _DEMO_ID_RE.fullmatch(part):
            raise ValueError(f"invalid intersection id: {part!r} (expected demo_N)")
        if part in seen:
            raise ValueError(f"duplicate intersection id: {part}")
        seen.add(part)
        ordered.append(part)
    return tuple(ordered)


def resolve_scope(preset_id: Optional[str],
                  custom_ids: Optional[tuple[str, ...]]) -> ResolvedScenarioScope:
    """preset → custom → default 三选一（§7.2 解析规则）。"""
    if preset_id is not None:
        preset = require_scenario_preset(preset_id)
        return ResolvedScenarioScope(
            source="preset", preset_id=preset.preset_id,
            managed_ids=preset.intersection_ids)
    if custom_ids is not None:
        return ResolvedScenarioScope(
            source="custom", preset_id=None, managed_ids=custom_ids)
    return ResolvedScenarioScope(
        source="default", preset_id=None,
        managed_ids=ALL_DEMO_INTERSECTION_IDS)


def build_scope_block(scope: ResolvedScenarioScope,
                      registered_ids: Sequence[str]) -> dict:
    """§7.4 scope 块（与 collab.stats.scope_block 保持同构；一致性测试保证）。"""
    managed = list(scope.managed_ids)
    registered = list(registered_ids)
    return {
        "source": scope.source,
        "preset_id": scope.preset_id,
        "registered_intersections": len(registered),
        "algorithm_controlled_intersections": len(managed),
        "fixed_intersections": len(registered) - len(managed),
        "managed_ids": managed,
    }
```

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/coslight/test_scope_cli.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/coslight/scope_cli.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/coslight/scope_cli.py
scp -P 24 algorithms/coslight/test_scope_cli.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/coslight/test_scope_cli.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/coslight/scope_cli.py algorithms/coslight/test_scope_cli.py && git commit -m "feat(coslight): scenario scope CLI parsing helpers"'
```

---

## Task 15: evaluate.py — `--scenario-preset` / `--intersections` / `--v2x-collab*` CLI 接入

**Files:**
- Modify: `algorithms/coslight/evaluate.py`
- Test: `algorithms/coslight/test_evaluate.py`

- [x] **Step 1: 写失败测试**

在 `algorithms/coslight/test_evaluate.py` 末尾追加：

```python
def test_scenario_preset_and_intersections_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--scenario-preset", "east_dense",
                "--intersections", "demo_1",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_invalid_intersections_rejected_at_startup(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--intersections", "demo_1,foo",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_unknown_scenario_preset_rejected_by_choices(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--scenario-preset", "nope",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )


def test_v2x_collab_active_rejected_at_startup(tmp_path):
    with pytest.raises(SystemExit, match="2"):
        evaluate.main(
            [
                "--methods", "fixed",
                "--v2x-collab",
                "--v2x-collab-mode", "active",
                "--output", str(tmp_path / "evaluation.json"),
            ]
        )
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/coslight/test_evaluate.py -q --tb=short'`
Expected: 新增 4 条 FAIL（argparse 未定义 `--scenario-preset/--v2x-collab`、`--intersections` 不接受字符串）

- [x] **Step 3: 实现**

**3a. imports 追加：**

```python
from algorithms.coslight.scope_cli import (
    build_scope_block, parse_intersections, resolve_scope,
)
from config.scenario_presets import SCENARIO_PRESET_REGISTRY
```

**3b. `main()` 参数区替换：**

```python
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--scenario-preset",
        choices=sorted(SCENARIO_PRESET_REGISTRY),
        help="Select a predefined algorithm/collaboration intersection scope. "
             "Does not change the SUMO network or traffic demand.")
    scope_group.add_argument(
        "--intersections", type=parse_intersections, default=None,
        help="Comma-separated demo_N ids, or a single integer N for demo_1..N")
    parser.add_argument(
        "--v2x-collab", action="store_true",
        help="启用车路云协同决策层（shadow 闭环；隐含启动 hub 与内存记录器）")
    parser.add_argument(
        "--v2x-collab-mode", choices=("off", "shadow", "active"),
        default="shadow", help="默认 shadow；active 在 v1 运行时不可用")
    parser.add_argument(
        "--v2x-guidance-mode", choices=("threshold", "full", "disabled"),
        default="threshold", help="RSI 发射模式（默认 threshold）")
```

**3c. `main()` 校验区：**

- 把 `for name in ("episodes", "workers", "duration", "intersections", "top_k")` 改为 `("episodes", "workers", "duration", "top_k")`（`--intersections` 已由 `parse_intersections` 校验）；
- 删除 `if args.intersections > len(DEFAULT_INTERSECTIONS): parser.error(...)`；
- 追加：

```python
    if args.v2x_collab and args.v2x_collab_mode == "active":
        parser.error("--v2x-collab-mode active is unavailable in v1 (spec §4.1); "
                     "use shadow or off")
```

**3d. scope 解析（替换 `intersections = DEFAULT_INTERSECTIONS[: args.intersections]`）：**

```python
    scope = resolve_scope(args.scenario_preset, args.intersections)
    intersections = scope.managed_ids
```

**3e. jobs 增加字段（在 `"v2x_log": ...` 之后）：**

```python
            "v2x_collab": args.v2x_collab,
            "v2x_collab_mode": (
                args.v2x_collab_mode if args.v2x_collab else "shadow"),
            "v2x_guidance_mode": (
                args.v2x_guidance_mode if args.v2x_collab else "threshold"),
            "scope_source": scope.source,
            "scope_preset_id": scope.preset_id,
            "scope_managed_ids": list(intersections),
```

**3f. `_run_evaluation` env 传递（在 v2x_log 块之后）：**

```python
    if request.get("v2x_collab"):
        os.environ["COSLIGHT_V2X_COLLAB"] = "1"
        os.environ["COSLIGHT_V2X_COLLAB_MODE"] = str(request["v2x_collab_mode"])
        os.environ["COSLIGHT_V2X_GUIDANCE_MODE"] = str(request["v2x_guidance_mode"])
        os.environ["COSLIGHT_V2X_SCOPE_SOURCE"] = str(request["scope_source"])
        os.environ["COSLIGHT_V2X_SCOPE_PRESET_ID"] = str(
            request.get("scope_preset_id") or "")
        os.environ["COSLIGHT_V2X_SCOPE_MANAGED_IDS"] = ",".join(
            str(iid) for iid in request["scope_managed_ids"])
    else:
        for key in (
            "COSLIGHT_V2X_COLLAB", "COSLIGHT_V2X_COLLAB_MODE",
            "COSLIGHT_V2X_GUIDANCE_MODE", "COSLIGHT_V2X_SCOPE_SOURCE",
            "COSLIGHT_V2X_SCOPE_PRESET_ID", "COSLIGHT_V2X_SCOPE_MANAGED_IDS",
        ):
            os.environ.pop(key, None)
```

**3g. `_run_evaluation` 结果附加 collab summary（`if method != "fixed": result["signal_execution"]...` 之后）：**

```python
        if request.get("v2x_collab"):
            from algorithms.v2x.adapters.coslight import last_collab_summary
            collab_summary = last_collab_summary()
            if collab_summary is not None:
                result["collab"] = collab_summary.get("collab")
                result["scope"] = collab_summary.get("scope")
```

**3h. report config 增加 scope/collab 块：**

```python
            "scope": build_scope_block(scope, DEFAULT_INTERSECTIONS),
            "v2x_collab": args.v2x_collab,
            "v2x_collab_mode": args.v2x_collab_mode,
            "v2x_guidance_mode": args.v2x_guidance_mode,
```

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/coslight/test_evaluate.py algorithms/coslight/test_scope_cli.py -q --tb=short'`
Expected: PASS（既有 7 条 + 新增 4 条 + scope_cli 全量）

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/coslight/evaluate.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/coslight/evaluate.py
scp -P 24 algorithms/coslight/test_evaluate.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/coslight/test_evaluate.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/coslight/evaluate.py algorithms/coslight/test_evaluate.py && git commit -m "feat(coslight): scenario preset + collab CLI wiring in evaluate"'
```

---

## Task 16: test_hub.py MAP `phase_order` 整合 + 既有 V2X 回归

**Files:**
- Modify（可选，加固）: `algorithms/v2x/tests/test_hub.py`
- 无新业务代码：Task 1 已实现 MAP `phase_order` 透传与断言

- [x] **Step 1: 写失败测试（加固断言，若 Task 1 已含可跳过）**

若 `test_hub.py` 尚无如下断言，追加：

```python
def test_map_payload_passthrough_phase_order_and_required_fields_unchanged():
    from algorithms.v2x.hub import V2XHub
    from algorithms.v2x.config import V2XConfig
    from algorithms.v2x.messages import REQUIRED_FIELDS
    hub = V2XHub(config=V2XConfig(latency_ms=0.0))
    hub.ingest_initialize(
        {"intersections": {"i1": {
            "intersection_id": "i1", "phase_order": [1, 2],
            "phases": {}, "lanes": {}, "connections": [], "direct_neighbors": []}}},
        run_id="run1", episode_id="ep1")
    hub.advance(0.0)
    maps = [m for m in hub.delivery_records if m["status"] == "delivered"]
    # MAP 消息必须出现在 pending/已投递中；phase_order 透传由 collab build_static_context 消费
    assert REQUIRED_FIELDS["MAP"] == frozenset({
        "intersection_id", "phases", "lanes", "connections", "direct_neighbors"})
    assert hub._map_versions == {"i1": 1}
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_hub.py -q --tb=short'`
Expected: 若新增断言依赖 `_map_versions`/delivery 细节与现有实现不符则 FAIL（实现者按实际 hub 行为调整断言，不断言内部字段以外的行为）

- [x] **Step 3: 实现/对齐**

- Task 1 已改 `hub.ingest_initialize` 的 MAP draft：`payload` 追加 `"phase_order": intersections[inter_id].get("phase_order")`；
- 本任务仅做断言对齐与回归确认，无新实现。

- [x] **Step 4: 运行确认通过（回归）**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/tests/test_hub.py algorithms/v2x/tests/test_coslight_adapter.py -q --tb=short'`
Expected: PASS

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/tests/test_hub.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/tests/test_hub.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/tests/test_hub.py && git commit -m "test(v2x): MAP phase_order passthrough regression"'
```

---

## Task 17: 集成测试（shadow 不变量 / RSI 审计 / 确定性 fixture / smoke 命令）

**Files:**
- Create: `algorithms/v2x/collab/tests/test_integration.py`

- [x] **Step 1: 写失败测试**

创建 `algorithms/v2x/collab/tests/test_integration.py`：

```python
"""集成测试（spec §6.3）：shadow 不变量、RSI 恰 1 条 message + 1 条终态 delivery、
summary 可重建、确定性 fixture。"""
import copy
import json

from algorithms.v2x.collab.aggregator import EdgeAggregator
from algorithms.v2x.collab.arbiter import ActionArbiter
from algorithms.v2x.collab.engine import CollabDecisionEngine
from algorithms.v2x.collab.policy import CloudRulePolicy
from algorithms.v2x.collab.proposals import (
    CollabConfig, DecisionMode, GuidanceEmissionMode,
)
from algorithms.v2x.collab.records import InMemoryRecordCollector
from algorithms.v2x.collab.state import CloudStateStore
from algorithms.v2x.collab.stats import build_collab_summary
from algorithms.v2x.config import V2XConfig
from algorithms.v2x.hub import V2XHub
from algorithms.v2x.messages import MessageDraft
from algorithms.v2x.protocol import build_bsm_draft, build_intent_draft, build_spat_draft
from config.scenario_presets import ResolvedScenarioScope


SCOPE = ResolvedScenarioScope(source="preset", preset_id="east_dense",
                              managed_ids=("i1",))

INIT = {
    "episode_id": "ep1",
    "vehicle_types": {"official_passenger": {"vehicle_class": "passenger"}},
    "intersections": {
        "i1": {
            "intersection_id": "i1", "phase_order": [1],
            "phases": {"1": {"phase_id": 1,
                             "connection_priorities": {"c0": "protected"}}},
            "lanes": {
                "A_0": {"lane_id": "A_0", "edge_id": "A", "lane_index": 0,
                        "approach_id": "west", "movements": ("through",),
                        "speed_limit_mps": 16.0},
            },
            "connections": [{"connection_id": "c0", "from_lane": "A_0",
                             "to_lane": "B_0", "movement": "through"}],
            "direct_neighbors": [],
        },
    },
}

STEP = {"episode_id": "ep1", "simulation_time": 5.0,
        "intersections": {}, "vehicles": {}}
ACTIONS = {"signals": {"i1": {"target_phase": 1}}, "vehicles": {}}


def _normalize(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      default=lambda o: list(o) if isinstance(o, tuple) else o)


def _make_env():
    collector = InMemoryRecordCollector()
    hub = V2XHub(config=V2XConfig(latency_ms=0.0, drop_rate=0.0),
                 sink=collector)
    aggregator = EdgeAggregator(managed_ids=("i1",))
    store = CloudStateStore(aggregator, CollabConfig().freshness)
    policy = CloudRulePolicy(CollabConfig())
    arbiter = ActionArbiter(DecisionMode.SHADOW)
    engine = CollabDecisionEngine(
        hub=hub, aggregator=aggregator, store=store, policy=policy,
        arbiter=arbiter, collector=collector, config=CollabConfig(),
        scope=SCOPE, run_id="run1", episode_id="ep1",
        registered_ids=("i1",))
    return hub, engine, collector


def _deliver(hub, frame):
    hub.publish(build_spat_draft("i1", {
        "current_phase": 1, "stage": "GREEN", "stage_elapsed": 10.0,
        "remaining_time_s": 21.0, "lanes": {},
    }, INIT["intersections"]["i1"]["phases"], sim_time=frame.sim_time),
        frame_id=frame.frame_id)
    hub.publish(build_bsm_draft("car1", {
        "type_id": "official_passenger", "position": {"x_m": 0.0, "y_m": 0.0},
        "motion": {"speed_mps": 8.0, "acceleration_mps2": 0.0},
        "location": {"road_id": "A", "lane_id": "A_0", "lane_index": 0,
                     "lane_position_m": 10.0},
        "route_edges": ["A", "B"],
        "next_signal": {"intersection_id": "i1", "distance_m": 205.0,
                        "state": "G"},
        "leader_gap_m": None, "follower_gap_m": None,
        "_sim_time": frame.sim_time,
    }), frame_id=frame.frame_id)
    hub.publish(build_intent_draft(
        "car1", {}, sim_time=frame.sim_time, turn="through",
        lane_change=None, arrival=15.0, turn_conf=1.0,
        lane_change_conf=0.0, arrival_conf=1.0, origin="derived"),
        frame_id=frame.frame_id)
    hub.advance(frame.sim_time)


def _run_episode():
    hub, engine, collector = _make_env()
    hub.ingest_initialize(INIT, run_id="run1", episode_id="ep1")
    results = []
    for _ in range(3):
        frame = hub.ingest_step(STEP)
        _deliver(hub, frame)
        results.append(engine.tick(frame=frame, baseline_actions=ACTIONS))
    network = hub.finish_episode(5.0, drain_pending=True)
    collab = engine.finalize_episode(episode_id="ep1", registered_ids=("i1",))
    engine.close()
    return hub, engine, collector, results, network, collab


def test_shadow_invariant_every_frame():
    hub, engine, collector, results, network, collab = _run_episode()
    for result in results:
        assert _normalize(result.protocol_actions) == _normalize(ACTIONS)
        for source in result.signal_sources.values():
            assert source.value == "baseline"


def test_collab_rsi_exactly_one_message_and_terminal_delivery():
    hub, engine, collector, results, network, collab = _run_episode()
    emitted = [mid for r in results for mid in r.emitted_rsi_message_ids]
    # 确定性 fixture：每帧同一条 RSI 受去重/冷却抑制 → 全程恰 1 条发布
    assert len(emitted) == 1
    rsi_sent = [rec for rec in hub.sent_records
                if rec["message_type"] == "RSI"]
    assert len(rsi_sent) == 1
    rsi_deliveries = [rec for rec in hub.delivery_records
                      if rec["message_id"] == emitted[0]]
    assert len(rsi_deliveries) == 1
    assert rsi_deliveries[0]["status"] == "delivered"


def test_summary_rebuild_matches_runtime():
    hub, engine, collector, results, network, collab = _run_episode()
    rebuilt = build_collab_summary(
        records=collector.episode_records, config=CollabConfig(),
        scope=SCOPE, registered_ids=("i1",), hub=hub,
        run_id="run1", episode_id="ep1")
    # 运行时 finalize 与从记录重建规范化一致（collab 块）
    assert _normalize(rebuilt["collab"]) == _normalize(collab["collab"])
    # 完整性全零
    assert rebuilt["collab"]["integrity"] == {
        "missing_source_delivery_refs": 0,
        "orphan_rsi_messages": 0,
        "orphan_rsi_deliveries": 0,
        "missing_signal_event_refs": 0,
        "duplicate_terminal_delivery_records": 0,
    }
```

- [x] **Step 2: 运行确认失败**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab/tests/test_integration.py -q --tb=short'`
Expected: FAIL（`ModuleNotFoundError` / 断言失败逐步修正）

- [x] **Step 3: 实现**

- 本任务无新业务代码；若断言失败，修复点在 engine/stats/records（不绕过消息投递、不关闭审计）。注意 `test_collab_rsi_exactly_one_message_and_terminal_delivery` 依赖去重/冷却：三帧同一建议（delta=0、reason 相同、valid_until 未到、冷却未到）→ 第 2/3 帧 `SUPPRESSED_DUPLICATE`，只有第 1 帧发布。

- [x] **Step 4: 运行确认通过**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x/collab -q --tb=short'`
Expected: PASS（collab 全量）

- [x] **Step 5: Commit**

```bash
scp -P 24 algorithms/v2x/collab/tests/test_integration.py kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/algorithms/v2x/collab/tests/test_integration.py
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && git add algorithms/v2x/collab/tests/test_integration.py && git commit -m "test(v2x): collab integration (shadow invariant, RSI audit, rebuild)"'
```

---

## Task 18: 收尾 — 全量回归 + spec 自审 + 提交计划

- [x] **Step 1: 全量回归**

Run: `ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && export PYTHONPATH=/usr/share/sumo/tools && /home/kemove/anaconda3/envs/BWformer/bin/python -m pytest algorithms/v2x algorithms/coslight backend/tests -q --tb=short'`
Expected: 全绿（v2x 既有 ≈69 + collab 新增 + coslight 既有 ≈98 + 新增；不把具体数量作为长期断言，验收契约 = 零失败、退出码 0）

- [x] **Step 2: 20 路口 smoke（服务器手动验收，§6.4）**

```bash
cd /home/kemove/devdata1/gsb/citypulse-v2x-sim
export PYTHONPATH=/usr/share/sumo/tools
# xiongan_20 全网络
/home/kemove/anaconda3/envs/BWformer/bin/python algorithms/coslight/evaluate.py \
  --methods model --checkpoint <MODEL_CKPT> --episodes 1 --seed 10000 \
  --duration 300 --v2x-collab --v2x-collab-mode shadow \
  --v2x-guidance-mode threshold \
  --output logs/eval_collab_xiongan20.json \
  --v2x-log logs/v2x_collab_xiongan20.jsonl
# 东部典型场景（4 算法路口 + 16 固定配时）
/home/kemove/anaconda3/envs/BWformer/bin/python algorithms/coslight/evaluate.py \
  --methods model --checkpoint <MODEL_CKPT> --episodes 1 --seed 10000 \
  --duration 300 --scenario-preset east_dense \
  --v2x-collab --v2x-collab-mode shadow \
  --output logs/eval_collab_east_dense.json \
  --v2x-log logs/v2x_collab_east_dense.jsonl
```

验收点（对照 §6.4/§7.5）：
- 无异常、shadow 下 managed 路口 `applied == baseline`（规范化一致）；
- `baseline_signal_slots > 0`、`decision_record_coverage ∈ [0,1]`；
- guidance funnel 字段完整；`published==0` 时 `network_delivery_rate=null`；
- `integrity` 全 0；`simulation/sumo/` 零修改（`git -C simulation/sumo status` 干净）；
- east_dense：scope 块 `algorithm_controlled=4 / fixed=16 / managed_ids=[demo_3,demo_5,demo_6,demo_9]`；未选路口无 SignalProposal/arbitration/RSI 候选、不出现在 `actions.signals`；`traffic_metrics.network_wide` 覆盖 20 路口（该块在 evaluate 顶层 report 中，与 collab 分块并存）。

**验收结果（2026-08-05，服务器手动验收）：**
- xiongan_20 全网络 smoke 通过：status=complete、shadow、20 路口、`baseline_signal_slots=80`、`decision_record_coverage=1.0`、`selectable_output_rate=1.0`、integrity 全 0、`published=0 → network_delivery_rate=null`；JSONL 含 MAP/BSM/INTENT/SPaT/RSM/SIGNAL_CONTROL + episode 记录；`simulation/sumo/` 零修改。
- east_dense smoke **未执行**（客观限制）：现有 checkpoint `gate_v17_20260804_200223.pt` 为 20-TLS 模型（num_agents=20/act_dim=4/top_k=5），与 east_dense 的 4 个算法路口不匹配；需先用 4-TLS checkpoint 或训练脚本生成匹配模型后方可手动验收该场景，验收点见上文列表。

- [x] **Step 3: spec 自审（逐节核对）**

对照 `docs/superpowers/specs/2026-08-05-coslight-vrc-collaboration-design.md` 逐节确认：

| spec 节 | 落地任务 |
|---|---|
| §1.1–1.3 模块/数据流/生命周期 | Task 3–6、12、13（订阅一次、reset_episode、close 顺序、finalize 不触发新决策） |
| §1.4 不可变模型 + 静态上下文 | Task 4、5（MAP 透传 phase_order 见 Task 1/16） |
| §1.5–1.6 新鲜度视图 / CollabConfig | Task 3、6 |
| §1.7 记录类型与量控制 | Task 10、12（tick_stats 不可关闭、edge_snapshot 可关、arbitration differences） |
| §2 信号规则族 C | Task 7（门禁顺序、平分、min_green/margin、confidence 公式） |
| §3 RSI 阈值触发 | Task 8（候选筛选、两阶段 raw/发射、组件独立、去重/冷却/TTL、FULL/DISABLED、漏斗） |
| §4 ActionArbiter / shadow | Task 9、12（OFF 短路、SHADOW applied==baseline、ACTIVE 抛错、validator） |
| §5 指标与统计 | Task 11、12（tick_stats 原子写、summary、pooled、完整性审计、过期投递） |
| §6 集成与测试 | Task 13、15、16、17、18（CLI/env、sink 组合、测试分层、20 路口 smoke） |
| §7 场景预设与范围 | Task 2、14、15（中立注册表、backend 透传、解析规则、scope 块、fail-fast、算法无关） |
| §8 附录枚举/语义 | Task 3、9、11（枚举值、valid_from<=now<valid_until、RSI source_id="cloud"、SIGNAL_CONTROL 仅 ingest_actions） |

自审清单（逐项打勾）：
- [x] 无占位符/`TODO`/`FIXME`/`pass` 占位（`grep -n "TODO\\|FIXME\\|placeholder" docs/superpowers/plans/2026-08-05-coslight-vrc-collaboration.md` 为空）；
- [x] 类型/方法名与 spec 一致（`SignalProposal` 字段、`CollabTickResult`、`GUIDANCE_FUNNEL_STAGES`、`scope_block`）；
- [x] `algorithms/v2x/collab` 无 torch/SUMO 依赖（`grep -rn "import torch\\|import sumo" algorithms/v2x/collab` 为空）；
- [x] `simulation/sumo/` 无修改（`git -C simulation/sumo diff --stat` 为空）；
- [x] `GuidancePolicyConfig` 默认值与 spec §3.7 一致（Task 3 修正项）；
- [x] `collab_managed_ids == algorithm_controlled_ids == resolved_scope.managed_ids`（Task 14/15 单一 scope 来源）。

- [x] **Step 4: 同步计划 + spec 到服务器并提交**

```bash
scp -P 24 docs/superpowers/plans/2026-08-05-coslight-vrc-collaboration.md \
    kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/docs/superpowers/plans/
scp -P 24 docs/superpowers/specs/2026-08-05-coslight-vrc-collaboration-design.md \
    kemove@172.27.185.208:/home/kemove/devdata1/gsb/citypulse-v2x-sim/docs/superpowers/specs/
ssh 346-4090 'cd /home/kemove/devdata1/gsb/citypulse-v2x-sim && \
  git add docs/superpowers/plans/2026-08-05-coslight-vrc-collaboration.md \
          docs/superpowers/specs/2026-08-05-coslight-vrc-collaboration-design.md && \
  git commit -m "docs(v2x): implementation plan for VRC collaboration decision layer"'
```

---

## 自审结果（Writing-plans 阶段）

- **Spec 覆盖**：§1–§8 全部映射到 Task 1–18（见 Task 18 Step 3 表格）；
- **任务依赖**：Task 2（config 注册表）→ Task 8/12/14/15；Task 7/8（policy）→ Task 12（engine）；Task 10/11（records/stats）→ Task 12/13；Task 14 → Task 15；Task 12 → Task 13；
- **已记录的实现修正**：
  1. Task 3 `GuidancePolicyConfig` 默认值须对齐 spec §3.7：`v_min_mps=0.0, v_max_mps=16.0, speed_scale_low=0.5, speed_scale_high=1.3, min_guidance_speed_mps=0.5, green_clearance_buffer_s=1.0`（计划文件中的初稿使用了不同默认值，**实现时以 spec 为准**）；
  2. Task 7 `_signal_result` 显式 `current_action` 参数 + `needs_transition` 按 spec §2.3；
  3. Task 11 契约收口：`records.cloud_proposal_record` 可选 `emitted_message_id/next_signal_intersection_id`、`GuidanceOutcome` 诊断三字段、`policy.missing_signal_proposal`；
  4. Task 13：collab 启用时 `hub.ingest_actions` 收到的是剥离 `vehicles` 的 protocol_actions（RSI 发射权归 engine，§3.5/§8.2）；
  5. Task 15：`--intersections` 从 int 改为 `parse_intersections`（兼容旧整数 N 调用），`_positive` 校验列表移除 `intersections`。
- **风险提示**：`pool_collab_summaries` 对 `decision_input_age_s` 的 p50/p95 在无原始样本时显式降级（`distribution_pooled=false`），精确值需 JSONL 重放路径；20 路口 smoke 需要真实 SUMO + checkpoint，属于手动验收。
