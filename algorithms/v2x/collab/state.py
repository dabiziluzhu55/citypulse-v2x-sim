# algorithms/v2x/collab/state.py
"""CloudStateStore：新鲜度视图（age/missing/stale）+ 静态上下文访问（spec §1.5）。"""
from __future__ import annotations

from dataclasses import dataclass
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
