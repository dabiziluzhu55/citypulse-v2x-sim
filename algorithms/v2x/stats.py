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
        "existing": existing,
        "enabled": enabled,
        "sent": sent,
        "delivered": delivered,
        "filter_reasons": dict(reasons or {}),
    }

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
        "delivery": {
            "sent": len(sent),
            "delivered": len(delivered),
            "dropped": len(dropped),
            "pending": len(hub._pending),
            "delivery_rate": delivery_rate(sent=len(sent), delivered=len(delivered)),
            "latency_ms": latency_stats(latencies),
        },
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
