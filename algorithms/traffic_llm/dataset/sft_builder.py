"""Convert expert traces into LLaMA-Factory / Qwen SFT JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from simulation.sumo.engine.ai_control import AIControlPlan, AIControlConfig

from .action_parser import parse_target_phases
from .feature_builder import build_observation, build_observation_v2
from .io_utils import read_jsonl
from .schema import DATASET_VERSION, OBSERVATION_VERSION_V2, ScenarioSpec
from .teacher_selector import signal_sft_reason


SYSTEM_PROMPT = (
    "你是 CityPulse 的高层交通信号控制规划器。"
    "只根据用户提供的当前交通观测和扰动事件生成一个 30 秒信号计划。"
    "必须只输出一个严格 JSON 对象，字段仅限："
    "controlled_intersections, valid_seconds, signal_plan, objective, reason, fallback_to_baseline。"
    "valid_seconds 必须为 30；signal_plan 每个路口必须是长度为 6 的整数相位数组。"
    "相位必须来自 allowed_phases，不得输出车辆控制。"
    "若应保持固定配时基线，则 controlled_intersections=[], signal_plan={}, fallback_to_baseline=true。"
)


def _nearest_record(records: Sequence[Mapping[str, Any]], time_s: float) -> Mapping[str, Any] | None:
    best = None
    best_dist = 1e18
    for record in records:
        sim_t = record.get("simulation_time")
        if sim_t is None:
            continue
        dist = abs(float(sim_t) - time_s)
        if dist < best_dist:
            best = record
            best_dist = dist
    if best is None or best_dist > 2.6:
        return None
    return best


def _phase_at(record: Mapping[str, Any], intersection_id: str) -> int | None:
    actions = record.get("actions") or {}
    phases = parse_target_phases((actions or {}).get("signals") or {})
    if intersection_id in phases:
        return phases[intersection_id]
    executed = (record.get("executed_signal_state") or {}).get(intersection_id) or {}
    if executed.get("current_phase") is not None:
        return int(executed["current_phase"])
    observation = record.get("observation") or {}
    i_obs = (observation.get("intersections") or {}).get(intersection_id) or {}
    if i_obs.get("current_phase") is not None:
        return int(i_obs["current_phase"])
    return None


def extract_signal_plan(
    records: Sequence[Mapping[str, Any]],
    *,
    anchor_time: float,
    slot_seconds: float,
    slot_count: int,
    controlled: Sequence[str],
) -> dict[str, list[int]] | None:
    plan: dict[str, list[int]] = {}
    for iid in controlled:
        sequence: list[int] = []
        for slot in range(slot_count):
            t = anchor_time + slot * slot_seconds
            record = _nearest_record(records, t)
            if record is None:
                return None
            phase = _phase_at(record, iid)
            if phase is None:
                return None
            sequence.append(int(phase))
        plan[iid] = sequence
    return plan


FUTURE_LEAK_PATTERNS = (
    "恢复",
    "recovery_time",
    "did not recover",
    "composite",
    "winner_score",
    "expert_confidence",
    "traffic_performance_index",
    "traffic_eval",
    "最终",
)


def _incoming_lane_maps(observation: Mapping[str, Any], intersection_id: str) -> list[Mapping[str, Any]]:
    ix_map = observation.get("intersections") or observation.get("ix") or {}
    ix = ix_map.get(intersection_id) or {}
    raw_lanes = ix.get("lanes") or {}
    items = raw_lanes.values() if isinstance(raw_lanes, Mapping) else raw_lanes
    lanes = []
    for lane in items:
        if not isinstance(lane, Mapping):
            continue
        role = str(lane.get("role") or "incoming")
        if role in {"outgoing"}:
            continue
        lanes.append(lane)
    return lanes


def _lane_field(lane: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in lane and lane[key] is not None:
            return lane[key]
    return None


def _mean(values: Sequence[Any]) -> float | None:
    present = [float(item) for item in values if item is not None]
    if not present:
        return None
    return sum(present) / len(present)


def factual_reason(
    *,
    spec: ScenarioSpec,
    observation: Mapping[str, Any],
    fallback: bool,
) -> tuple[str, str]:
    """Anchor-time factual templates only. No recovery / final eval / future state."""

    target = spec.event.intersection_id
    target_lanes = _incoming_lane_maps(observation, target)
    halt = _mean([_lane_field(lane, "halting_count", "halt") for lane in target_lanes])
    speed = _mean([_lane_field(lane, "mean_speed", "speed") for lane in target_lanes])
    occupancy = _mean([_lane_field(lane, "occupancy", "occ") for lane in target_lanes])
    spill = False
    for lane in target_lanes:
        queue = _lane_field(lane, "queue_length_m", "queue_m")
        length = lane.get("lane_length_m")
        occ = _lane_field(lane, "occupancy", "occ")
        try:
            if queue is not None and length is not None and float(length) > 0:
                if float(queue) + 1e-9 >= 0.85 * float(length):
                    spill = True
            elif occ is not None and float(occ) >= 0.85:
                spill = True
        except (TypeError, ValueError):
            continue
    neighbor_halt = []
    ix_map = observation.get("intersections") or observation.get("ix") or {}
    for iid in dict(ix_map):
        if str(iid) == str(target):
            continue
        lanes = _incoming_lane_maps(observation, str(iid))
        value = _mean([_lane_field(lane, "halting_count", "halt") for lane in lanes])
        if value is not None:
            neighbor_halt.append(value)

    facts: list[str] = []
    if halt is not None and halt >= 1.0:
        facts.append("目标路口停车车辆较多，当前进口持续排队")
    if speed is not None and speed < 4.0:
        facts.append("目标进口平均速度较低")
    if spill or (occupancy is not None and occupancy >= 0.45):
        facts.append("当前存在局部溢流风险")
    if halt is not None and neighbor_halt and max(neighbor_halt) >= max(0.8, halt * 0.6):
        facts.append("相邻路口排队同步上升")
    if fallback:
        objective = "保持固定配时基线"
        if not facts:
            facts.append("当前观测下维持固定配时")
        return objective, "；".join(facts)
    objective = "缓解扰动路口排队并抑制上游回溢"
    if not facts:
        facts.append("扰动目标路口附近交通正在受事件影响")
    reason = "；".join(facts)
    for token in FUTURE_LEAK_PATTERNS:
        if token in reason:
            reason = "扰动目标路口附近交通正在受事件影响"
            break
    return objective, reason


def snapshot_at(
    snapshots: Sequence[Mapping[str, Any]],
    time_s: float,
) -> Mapping[str, Any] | None:
    best = None
    best_dist = 1e18
    for item in snapshots:
        elapsed = item.get("elapsed_seconds")
        if elapsed is None:
            continue
        dist = abs(float(elapsed) - time_s)
        if dist < best_dist:
            best = item
            best_dist = dist
    if best is None or best_dist > 1.5:
        return None
    return best.get("summary") or best


def messages_to_prompt_completion(sample: Mapping[str, Any]) -> dict[str, Any]:
    messages = list(sample.get("messages") or ())
    prompt = [item for item in messages if item.get("role") in {"system", "user"}]
    completion = [item for item in messages if item.get("role") == "assistant"]
    return {
        "prompt": prompt,
        "completion": completion,
        "metadata": dict(sample.get("metadata") or {}),
    }


def build_sft_samples_for_run(
    *,
    spec: ScenarioSpec,
    run: Mapping[str, Any],
    selection: Mapping[str, Any],
    traces: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    dataset_config: Mapping[str, Any],
    neighbors: Mapping[str, Sequence[str]],
    allowed_phases: Mapping[str, Sequence[int]],
    observation_version: str = "v1",
    phase_service: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> list[dict[str, Any]]:
    fallback = bool(selection.get("fallback_to_baseline"))
    teacher_mode = str(selection.get("winner") or "")
    if fallback:
        teacher_mode = str(selection.get("baseline_mode") or "fixed")
    if str(run.get("control_mode")) != teacher_mode:
        return []
    if signal_sft_reason(selection) and not fallback:
        return []

    planning = dict(dataset_config.get("planning") or {})
    slot_seconds = float(planning.get("slot_seconds", 5))
    plan_valid = float(planning.get("plan_valid_seconds", 30))
    slot_count = int(round(plan_valid / slot_seconds))
    hops = int(planning.get("scope_hops", 1))
    anchors_cfg = dict(dataset_config.get("anchors") or {})
    offsets = [float(item) for item in anchors_cfg.get("offsets_seconds") or (0, 30, 60)]
    require_horizon = bool(anchors_cfg.get("require_horizon_inside_episode", True))
    episode_end = float(run.get("elapsed_seconds") or spec.duration_seconds)
    policy = AIControlConfig(plan_valid_seconds=plan_valid, slot_seconds=slot_seconds)

    samples: list[dict[str, Any]] = []
    event_start = float(spec.event.start_seconds)
    for offset in offsets:
        anchor = event_start + offset
        if require_horizon and anchor + plan_valid > episode_end + 1e-9:
            continue
        if anchor < 0 or anchor > episode_end:
            continue
        snap = snapshot_at(snapshots, anchor)
        if snap is None:
            continue
        use_v2 = str(observation_version).lower() in {"v2", OBSERVATION_VERSION_V2, "traffic_observation_v2"}
        if use_v2:
            observation = build_observation_v2(
                spec=spec,
                simulation_time=anchor,
                snapshot_summary=snap,
                allowed_phases=allowed_phases,
                neighbors=neighbors,
                scope_hops=hops,
                phase_service=phase_service,
            )
        else:
            observation = build_observation(
                spec=spec,
                simulation_time=anchor,
                snapshot_summary=snap,
                allowed_phases=allowed_phases,
                neighbors=neighbors,
                scope_hops=hops,
                prediction=None,
            )
        controlled = list(observation["controlled_region"])
        if fallback:
            plan_obj = {
                "controlled_intersections": [],
                "valid_seconds": 30.0,
                "signal_plan": {},
                "objective": "",
                "reason": "",
                "fallback_to_baseline": True,
            }
        else:
            signal_plan = extract_signal_plan(
                traces,
                anchor_time=anchor,
                slot_seconds=slot_seconds,
                slot_count=slot_count,
                controlled=controlled,
            )
            if not signal_plan:
                continue
            plan_obj = {
                "controlled_intersections": list(controlled),
                "valid_seconds": 30.0,
                "signal_plan": signal_plan,
                "objective": "",
                "reason": "",
                "fallback_to_baseline": False,
            }
        objective, rationale = factual_reason(
            spec=spec,
            observation=observation,
            fallback=fallback,
        )
        plan_obj["objective"] = objective
        plan_obj["reason"] = rationale
        AIControlPlan.from_mapping(plan_obj, config=policy)
        user_payload = {
            "instruction": "根据当前观测生成未来 30 秒多路口信号计划。",
            "observation": observation,
        }
        samples.append(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(plan_obj, ensure_ascii=False, separators=(",", ":")),
                    },
                ],
                "metadata": {
                    "scenario_id": spec.scenario_id,
                    "scenario_group_id": spec.scenario_group_id,
                    "anchor_time": anchor,
                    "teacher": run.get("control_mode"),
                    "teacher_score": selection.get("winner_score"),
                    "expert_confidence": selection.get("expert_confidence"),
                    "event_type": spec.event.event_type,
                    "action_space": run.get("teacher_action_space"),
                    "fallback_to_baseline": fallback,
                    "dataset_version": spec.dataset_version or DATASET_VERSION,
                    "observation_version": observation.get("observation_version"),
                    "ambiguous": bool(selection.get("ambiguous")),
                    "recovery_time_s": (run.get("recovery") or {}).get("recovery_time_s"),
                    "winner_score": selection.get("winner_score"),
                },
            }
        )
    return samples


def load_trace_file(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))
