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
from algorithms.config.scenario_presets import ResolvedScenarioScope

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
    # ---- 引导（含 FULL 模式诊断键） ----
    funnel_counter: Counter[str] = Counter()
    for t in ticks:
        for key, value in t["guidance"].items():
            if key == "filter_reason_counts" or not isinstance(value, int):
                continue
            funnel_counter[key] += value
    # 标准漏斗键始终存在（零分母/无记录时取 0）；FULL 诊断键透传
    funnel = {key: funnel_counter.get(key, 0)
              for key in GUIDANCE_FUNNEL_KEYS}
    for key, value in funnel_counter.items():
        if key not in GUIDANCE_FUNNEL_KEYS:
            funnel[key] = value
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
    # SIGNAL_CONTROL 的 source 是 cloud、destination 才是路口（与 arbitration_refs 对齐）
    signal_event_refs = {
        (rec["frame_id"], rec["destination"]) for rec in hub.sent_records
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
                "selection_status_counts": dict(Counter(
                    rec["selection_status"] for rec in arbitrations)),
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
        for key in ("would_pass_threshold", "would_be_duplicate",
                    "would_be_in_cooldown"):
            if key in guid["funnel"]:
                funnel[key] += guid["funnel"][key]
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

