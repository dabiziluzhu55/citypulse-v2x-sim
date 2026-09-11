"""Merge lane-level detections into event cards for backend/frontend display."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .semantics import (
    CAUSE_UNKNOWN,
    TRAFFIC_LOCALIZED_BLOCKAGE,
    TRAFFIC_SPILLBACK,
    cause_for_event_type,
    traffic_state_for_event_type,
)


NORMAL_EVENT_TYPE = "normal"
NON_EVIDENCE_REASONS = {"normal", "no_lane_green", "green_startup_loss"}
EVENT_SUGGESTIONS = {
    "lane_blocked": "关注该进口车道，必要时调整信号或提示绕行",
    "spillback": "关注下游出口拥堵，避免继续向受阻方向放行过多车流",
    "speed_restriction": "关注该车道通行能力下降，必要时降低诱导速度或提示慢行",
    "accident": "关注疑似事故车辆，必要时发布警示并组织绕行",
}
TRAFFIC_STATE_SUGGESTIONS = {
    "localized_blockage": "关注该进口车道，必要时调整信号或提示绕行",
    "spillback": "关注下游出口拥堵，避免继续向受阻方向放行过多车流",
    "capacity_drop": "关注该车道通行能力下降，必要时降低诱导速度或提示慢行",
}


@dataclass(frozen=True)
class EventCard:
    event_id: str
    status: str
    event_type: str
    traffic_state: str
    cause: str
    cause_confidence: float
    intersection_id: str
    lane_ids: tuple[str, ...]
    edge_id: str
    approach_id: str
    start_seconds: float
    end_seconds: float | None
    duration_seconds: float
    severity: str
    confidence: float
    evidence: tuple[str, ...]
    suggestion: str


@dataclass
class _OpenCard:
    session_id: str
    event_type: str
    traffic_state: str
    cause: str
    cause_confidence: float
    intersection_id: str
    lane_ids: set[str]
    edge_id: str
    approach_id: str
    start_seconds: float
    last_seconds: float
    confidence_sum: float
    confidence_count: int
    reasons: set[str]


@dataclass(frozen=True)
class VisualCauseEvidence:
    intersection_id: str
    lane_ids: tuple[str, ...]
    elapsed_seconds: float
    cause: str
    confidence: float
    source: str = "visual"


def _to_float(row: Mapping[str, object], field: str, default: float = 0.0) -> float:
    value = row.get(field, "")
    if value == "" or value is None:
        return default
    return float(value)


def _event_type(row: object) -> str:
    if isinstance(row, Mapping):
        return str(row.get("event_type", NORMAL_EVENT_TYPE))
    return str(getattr(row, "event_type", NORMAL_EVENT_TYPE))


def _traffic_state(row: object, event_type: str) -> str:
    value = _field(row, "traffic_state", "")
    return str(value) if value not in {None, ""} else traffic_state_for_event_type(event_type)


def _cause(row: object, event_type: str) -> str:
    value = _field(row, "cause", "")
    return str(value) if value not in {None, ""} else cause_for_event_type(event_type)


def _field(row: object, field: str, default: object = "") -> object:
    if isinstance(row, Mapping):
        return row.get(field, default)
    return getattr(row, field, default)


def _reason_to_text(reason: str) -> str:
    labels = {
        "closure_cusum_threshold": "异常分数持续累积达到报警阈值",
        "green_lane_empty_after_history": "绿灯期间目标车道异常空置",
        "soft_closure_lane_slow_peer_moving": "目标车道低速，但相邻车道仍可通行",
        "queue_blockage_not_releasing": "绿灯期间队列持续无法释放",
        "queue_blockage_cusum_threshold": "回溢异常分数持续累积达到报警阈值",
        "speed_restriction_low_speed_with_flow": "车辆仍在通行，但速度长期偏低",
        "speed_restriction_cusum_threshold": "限速异常分数持续累积达到报警阈值",
        "accident_lane_capacity_drop": "车道通行能力异常下降，疑似事故阻断",
        "traffic_style_slow": "路线拥堵等级为黄色（slow）",
        "traffic_style_congested": "路线拥堵等级为橙色（congested）",
        "traffic_style_severe": "路线拥堵等级为红色（severe）",
        "vehicle_count_high": "车道车辆数偏高",
        "halting_count_high": "停车车辆数偏高",
        "mean_speed_low": "平均速度偏低",
        "occupancy_high": "占有率偏高",
        "waiting_time_increasing": "等待时间持续增加",
    }
    return labels.get(reason, reason)


def _severity(duration_seconds: float, confidence: float) -> str:
    if duration_seconds >= 180 or confidence >= 0.85:
        return "high"
    if duration_seconds >= 60 or confidence >= 0.65:
        return "medium"
    return "low"


def _close_card(card: _OpenCard, end_seconds: float | None, final_seconds: float) -> EventCard:
    confidence = (
        card.confidence_sum / card.confidence_count if card.confidence_count else 0.0
    )
    status = "active" if end_seconds is None else "ended"
    effective_end = final_seconds if end_seconds is None else end_seconds
    duration = max(0.0, effective_end - card.start_seconds)
    evidence = tuple(
        _reason_to_text(reason)
        for reason in sorted(card.reasons)
        if reason and reason not in NON_EVIDENCE_REASONS
    )
    lane_ids = tuple(sorted(card.lane_ids))
    return EventCard(
        event_id=(
            f"{card.session_id}_{card.intersection_id}_{card.edge_id or '_'.join(lane_ids)}_"
            f"{card.traffic_state}_{int(card.start_seconds)}"
        ),
        status=status,
        event_type=card.event_type,
        traffic_state=card.traffic_state,
        cause=card.cause,
        cause_confidence=card.cause_confidence,
        intersection_id=card.intersection_id,
        lane_ids=lane_ids,
        edge_id=card.edge_id,
        approach_id=card.approach_id,
        start_seconds=card.start_seconds,
        end_seconds=end_seconds,
        duration_seconds=duration,
        severity=_severity(duration, confidence),
        confidence=confidence,
        evidence=evidence,
        suggestion=(
            EVENT_SUGGESTIONS.get(card.event_type)
            or TRAFFIC_STATE_SUGGESTIONS.get(card.traffic_state)
            or "关注该位置的异常交通状态"
        ),
    )


def build_event_cards(
    detections: list[object],
    *,
    max_gap_seconds: float = 180.0,
    visual_evidence: list[VisualCauseEvidence | Mapping[str, object]] | None = None,
) -> list[EventCard]:
    """Merge consecutive non-normal lane detections into display cards."""

    if not detections:
        return []
    ordered = sorted(
        detections,
        key=lambda row: (
            str(_field(row, "session_id", "")),
            str(_field(row, "intersection_id", "")),
            str(_field(row, "edge_id", "")),
            str(_field(row, "lane_id", "")),
            _to_float_object(row, "elapsed_seconds"),
        ),
    )
    final_seconds = max(_to_float_object(row, "elapsed_seconds") for row in ordered)
    open_cards: dict[tuple[str, str, str, str], _OpenCard] = {}
    cards: list[EventCard] = []

    for row in ordered:
        session_id = str(_field(row, "session_id", ""))
        intersection_id = str(_field(row, "intersection_id", ""))
        lane_id = str(_field(row, "lane_id", ""))
        edge_id = str(_field(row, "edge_id", ""))
        approach_id = str(_field(row, "approach_id", ""))
        elapsed = _to_float_object(row, "elapsed_seconds")
        event_type = _event_type(row)

        if event_type == NORMAL_EVENT_TYPE:
            stale_keys = [
                key
                for key, card in open_cards.items()
                if (
                    card.session_id == session_id
                    and card.intersection_id == intersection_id
                    and lane_id in card.lane_ids
                    and elapsed - card.last_seconds > max_gap_seconds
                )
            ]
            for key in stale_keys:
                current = open_cards.pop(key)
                cards.append(
                    _close_card(
                        current,
                        current.last_seconds + max_gap_seconds,
                        final_seconds,
                    )
                )
            continue

        traffic_state = _traffic_state(row, event_type)
        cause = _cause(row, event_type)
        key = _card_key(
            session_id=session_id,
            intersection_id=intersection_id,
            lane_id=lane_id,
            edge_id=edge_id,
            traffic_state=traffic_state,
        )
        current = open_cards.get(key)

        if current is not None and elapsed - current.last_seconds > max_gap_seconds:
            cards.append(
                _close_card(
                    current,
                    current.last_seconds + max_gap_seconds,
                    final_seconds,
                )
            )
            open_cards.pop(key, None)
            current = None

        confidence = _to_float_object(row, "confidence")
        reason = str(_field(row, "reason", ""))
        cause_confidence = _to_float_object(row, "cause_confidence")
        if (
            current is None
            or current.event_type != event_type
            or current.traffic_state != traffic_state
        ):
            if current is not None:
                cards.append(
                    _close_card(
                        current,
                        min(elapsed, current.last_seconds + max_gap_seconds),
                        final_seconds,
                    )
                )
            open_cards[key] = _OpenCard(
                session_id=session_id,
                event_type=event_type,
                traffic_state=traffic_state,
                cause=cause,
                cause_confidence=cause_confidence,
                intersection_id=intersection_id,
                lane_ids={lane_id},
                edge_id=edge_id,
                approach_id=approach_id,
                start_seconds=elapsed,
                last_seconds=elapsed,
                confidence_sum=confidence,
                confidence_count=1,
                reasons={reason},
            )
            continue

        current.lane_ids.add(lane_id)
        current.last_seconds = elapsed
        current.confidence_sum += confidence
        current.confidence_count += 1
        current.reasons.add(reason)
        if cause_confidence > current.cause_confidence:
            current.cause = cause
            current.cause_confidence = cause_confidence

    for card in open_cards.values():
        if final_seconds - card.last_seconds > max_gap_seconds:
            cards.append(
                _close_card(
                    card,
                    card.last_seconds + max_gap_seconds,
                    final_seconds,
                )
            )
        else:
            cards.append(_close_card(card, None, final_seconds))
    if visual_evidence:
        cards = apply_visual_evidence(cards, visual_evidence)
    return sorted(
        cards,
        key=lambda card: (card.start_seconds, card.intersection_id, card.lane_ids),
    )


def _card_key(
    *,
    session_id: str,
    intersection_id: str,
    lane_id: str,
    edge_id: str,
    traffic_state: str,
) -> tuple[str, str, str, str]:
    location_key = (
        edge_id
        if traffic_state in {TRAFFIC_LOCALIZED_BLOCKAGE, TRAFFIC_SPILLBACK} and edge_id
        else lane_id
    )
    return (session_id, intersection_id, traffic_state, location_key)


def apply_visual_evidence(
    cards: list[EventCard],
    evidence: list[VisualCauseEvidence | Mapping[str, object]],
) -> list[EventCard]:
    visual_rows = [_coerce_visual_evidence(item) for item in evidence]
    result = []
    for card in cards:
        best = None
        for row in visual_rows:
            if not _visual_matches_card(row, card):
                continue
            if best is None or row.confidence > best.confidence:
                best = row
        if best is None or best.confidence <= card.cause_confidence:
            result.append(card)
            continue
        result.append(
            EventCard(
                event_id=card.event_id,
                status=card.status,
                event_type=card.event_type,
                traffic_state=card.traffic_state,
                cause=best.cause,
                cause_confidence=best.confidence,
                intersection_id=card.intersection_id,
                lane_ids=card.lane_ids,
                edge_id=card.edge_id,
                approach_id=card.approach_id,
                start_seconds=card.start_seconds,
                end_seconds=card.end_seconds,
                duration_seconds=card.duration_seconds,
                severity=card.severity,
                confidence=max(card.confidence, best.confidence),
                evidence=(*card.evidence, f"视觉证据：{best.cause}"),
                suggestion=card.suggestion,
            )
        )
    return result


def _coerce_visual_evidence(
    item: VisualCauseEvidence | Mapping[str, object],
) -> VisualCauseEvidence:
    if isinstance(item, VisualCauseEvidence):
        return item
    lane_ids = item.get("lane_ids", ())
    if isinstance(lane_ids, str):
        parsed_lane_ids = (lane_ids,)
    else:
        parsed_lane_ids = tuple(str(value) for value in lane_ids)
    return VisualCauseEvidence(
        intersection_id=str(item.get("intersection_id", "")),
        lane_ids=parsed_lane_ids,
        elapsed_seconds=float(item.get("elapsed_seconds", 0.0)),
        cause=str(item.get("cause", CAUSE_UNKNOWN)),
        confidence=float(item.get("confidence", 0.0)),
        source=str(item.get("source", "visual")),
    )


def _visual_matches_card(row: VisualCauseEvidence, card: EventCard) -> bool:
    if row.intersection_id != card.intersection_id:
        return False
    if not set(row.lane_ids).intersection(card.lane_ids):
        return False
    end = card.end_seconds if card.end_seconds is not None else card.start_seconds + card.duration_seconds
    return card.start_seconds <= row.elapsed_seconds <= end


def _to_float_object(row: object, field: str, default: float = 0.0) -> float:
    if isinstance(row, Mapping):
        return _to_float(row, field, default)
    value = getattr(row, field, default)
    if value is None:
        return default
    return float(value)


def load_detections(path: Path) -> list[Mapping[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_cards(path: Path, cards: list[EventCard]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(card) for card in cards]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge detection CSV rows into event cards.")
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-gap-seconds", type=float, default=180.0)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    detections = load_detections(args.detections)
    cards = build_event_cards(detections, max_gap_seconds=args.max_gap_seconds)
    write_cards(args.output, cards)
    print(f"Wrote {len(cards)} event cards to {args.output}")


if __name__ == "__main__":
    main()
