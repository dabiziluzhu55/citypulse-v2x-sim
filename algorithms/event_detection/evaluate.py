"""Evaluate event detections against SUMO disturbance event files."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from .semantics import (
    EVENT_NORMAL,
    normalize_event_type,
    traffic_state_for_event_type,
)


@dataclass(frozen=True)
class EventLabel:
    event_id: str
    raw_event_type: str
    event_type: str
    start_seconds: float
    end_seconds: float
    lane_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetricRow:
    sample_count: int
    positive_samples: int
    detected_samples: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float
    recall: float
    f1: float
    duration_seconds: float
    false_positive_rows_per_hour: float
    type_correct_samples: int
    type_incorrect_samples: int
    type_accuracy_on_positive: float
    lane_blocked_tp: int
    spillback_tp: int
    speed_restriction_tp: int
    event_count: int
    detected_event_count: int
    missed_event_count: int
    mean_detection_delay_seconds: float
    event_type_correct_count: int
    event_type_accuracy: float
    card_count: int
    card_detected_event_count: int
    card_missed_event_count: int
    card_type_correct_event_count: int
    card_type_accuracy: float
    mean_card_detection_delay_seconds: float
    traffic_state_correct_samples: int
    traffic_state_incorrect_samples: int
    traffic_state_accuracy_on_positive: float
    event_traffic_state_correct_count: int
    event_traffic_state_accuracy: float
    card_traffic_state_correct_event_count: int
    card_traffic_state_accuracy: float


def _to_float(row: Mapping[str, str], field: str, default: float = 0.0) -> float:
    value = row.get(field, "")
    if value == "" or value is None:
        return default
    return float(value)


def _is_detected(row: Mapping[str, str]) -> bool:
    return normalize_event_type(row.get("event_type", EVENT_NORMAL)) != EVENT_NORMAL


def load_events(path: Path | None) -> list[EventLabel]:
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    result = []
    for item in raw.get("events", []):
        event_type = str(item["event_type"])
        if "lane_ids" in item:
            lane_ids = tuple(str(value) for value in item["lane_ids"])
        elif "lane_id" in item:
            lane_ids = (str(item["lane_id"]),)
        else:
            raise ValueError(
                f"Event {item.get('event_id', '<unknown>')} must define lane_id or lane_ids."
            )
        result.append(
            EventLabel(
                event_id=str(item["event_id"]),
                raw_event_type=event_type,
                event_type=normalize_event_type(event_type),
                start_seconds=float(item["start_seconds"]),
                end_seconds=float(item["end_seconds"]),
                lane_ids=lane_ids,
            )
        )
    return result


def load_detections(path: Path) -> list[Mapping[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_cards(path: Path | None) -> list[Mapping[str, object]]:
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8-sig"))


def matching_events(row: Mapping[str, str], events: list[EventLabel]) -> list[EventLabel]:
    elapsed = _to_float(row, "elapsed_seconds")
    lane_id = row.get("lane_id", "")
    return [
        event
        for event in events
        if event.start_seconds <= elapsed < event.end_seconds and lane_id in event.lane_ids
    ]


def _card_lane_ids(card: Mapping[str, object]) -> set[str]:
    lane_ids = card.get("lane_ids", [])
    if isinstance(lane_ids, str):
        return {lane_ids}
    return {str(lane_id) for lane_id in lane_ids}


def _card_matches_event(card: Mapping[str, object], event: EventLabel) -> bool:
    if str(card.get("intersection_id", "")) == "":
        return False
    card_lanes = _card_lane_ids(card)
    if not card_lanes.intersection(event.lane_ids):
        return False
    start = float(card.get("start_seconds", 0.0))
    raw_end = card.get("end_seconds")
    end = float(raw_end) if raw_end not in {None, ""} else event.end_seconds
    return start < event.end_seconds and end >= event.start_seconds


def _row_traffic_state(row: Mapping[str, str]) -> str:
    value = row.get("traffic_state", "")
    return value or traffic_state_for_event_type(row.get("event_type", EVENT_NORMAL))


def _card_traffic_state(card: Mapping[str, object]) -> str:
    value = str(card.get("traffic_state", ""))
    return value or traffic_state_for_event_type(str(card.get("event_type", EVENT_NORMAL)))


def _evaluate_cards(
    cards: list[Mapping[str, object]],
    events: list[EventLabel],
) -> tuple[int, int, int, int, float]:
    delays = []
    detected = 0
    type_correct = 0
    traffic_state_correct = 0
    for event in events:
        matches = [
            card
            for card in cards
            if _card_matches_event(card, event)
        ]
        if not matches:
            continue
        detected += 1
        first = min(matches, key=lambda card: float(card.get("start_seconds", 0.0)))
        delays.append(float(first.get("start_seconds", 0.0)) - event.start_seconds)
        if any(normalize_event_type(str(card.get("event_type", ""))) == event.event_type for card in matches):
            type_correct += 1
        event_traffic_state = traffic_state_for_event_type(event.event_type)
        if any(_card_traffic_state(card) == event_traffic_state for card in matches):
            traffic_state_correct += 1
    mean_delay = sum(delays) / len(delays) if delays else 0.0
    return detected, len(events) - detected, type_correct, traffic_state_correct, mean_delay


def evaluate(
    detections: list[Mapping[str, str]],
    events: list[EventLabel],
    cards: list[Mapping[str, object]] | None = None,
) -> MetricRow:
    tp = fp = fn = tn = 0
    positives = detected = 0
    type_correct = type_incorrect = 0
    traffic_state_correct = traffic_state_incorrect = 0
    class_tp = {
        "lane_blocked": 0,
        "spillback": 0,
        "speed_restriction": 0,
    }
    times = [_to_float(row, "elapsed_seconds") for row in detections]
    duration = (max(times) - min(times)) if times else 0.0

    for row in detections:
        matches = matching_events(row, events)
        truth = bool(matches)
        prediction_type = normalize_event_type(row.get("event_type", "normal"))
        prediction = prediction_type != "normal"
        positives += int(truth)
        detected += int(prediction)
        if truth and prediction:
            tp += 1
            if any(event.event_type == prediction_type for event in matches):
                type_correct += 1
                if prediction_type in class_tp:
                    class_tp[prediction_type] += 1
            else:
                type_incorrect += 1
            prediction_traffic_state = _row_traffic_state(row)
            if any(
                traffic_state_for_event_type(event.event_type) == prediction_traffic_state
                for event in matches
            ):
                traffic_state_correct += 1
            else:
                traffic_state_incorrect += 1
        elif truth and not prediction:
            fn += 1
        elif not truth and prediction:
            fp += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    hours = duration / 3600.0 if duration > 0 else 0.0
    false_positive_rows_per_hour = fp / hours if hours > 0 else 0.0
    type_accuracy = type_correct / tp if tp else 0.0
    traffic_state_accuracy = traffic_state_correct / tp if tp else 0.0

    delays = []
    detected_events = 0
    event_type_correct = 0
    event_traffic_state_correct = 0
    for event in events:
        first_detection = None
        event_type_was_correct = False
        event_traffic_state_was_correct = False
        for row in detections:
            if not _is_detected(row):
                continue
            elapsed = _to_float(row, "elapsed_seconds")
            if event.start_seconds <= elapsed < event.end_seconds and row.get("lane_id") in event.lane_ids:
                first_detection = elapsed
                if normalize_event_type(row.get("event_type", "normal")) == event.event_type:
                    event_type_was_correct = True
                if _row_traffic_state(row) == traffic_state_for_event_type(event.event_type):
                    event_traffic_state_was_correct = True
                break
        if first_detection is not None:
            detected_events += 1
            delays.append(first_detection - event.start_seconds)
            event_type_correct += int(event_type_was_correct)
            event_traffic_state_correct += int(event_traffic_state_was_correct)
    event_type_accuracy = event_type_correct / detected_events if detected_events else 0.0
    event_traffic_state_accuracy = (
        event_traffic_state_correct / detected_events if detected_events else 0.0
    )
    card_rows = cards or []
    (
        card_detected_events,
        card_missed_events,
        card_type_correct_events,
        card_traffic_state_correct_events,
        mean_card_delay,
    ) = _evaluate_cards(card_rows, events)
    card_type_accuracy = (
        card_type_correct_events / card_detected_events if card_detected_events else 0.0
    )
    card_traffic_state_accuracy = (
        card_traffic_state_correct_events / card_detected_events
        if card_detected_events
        else 0.0
    )

    return MetricRow(
        sample_count=len(detections),
        positive_samples=positives,
        detected_samples=detected,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        duration_seconds=duration,
        false_positive_rows_per_hour=false_positive_rows_per_hour,
        type_correct_samples=type_correct,
        type_incorrect_samples=type_incorrect,
        type_accuracy_on_positive=type_accuracy,
        lane_blocked_tp=class_tp["lane_blocked"],
        spillback_tp=class_tp["spillback"],
        speed_restriction_tp=class_tp["speed_restriction"],
        event_count=len(events),
        detected_event_count=detected_events,
        missed_event_count=len(events) - detected_events,
        mean_detection_delay_seconds=sum(delays) / len(delays) if delays else 0.0,
        event_type_correct_count=event_type_correct,
        event_type_accuracy=event_type_accuracy,
        card_count=len(card_rows),
        card_detected_event_count=card_detected_events,
        card_missed_event_count=card_missed_events,
        card_type_correct_event_count=card_type_correct_events,
        card_type_accuracy=card_type_accuracy,
        mean_card_detection_delay_seconds=mean_card_delay,
        traffic_state_correct_samples=traffic_state_correct,
        traffic_state_incorrect_samples=traffic_state_incorrect,
        traffic_state_accuracy_on_positive=traffic_state_accuracy,
        event_traffic_state_correct_count=event_traffic_state_correct,
        event_traffic_state_accuracy=event_traffic_state_accuracy,
        card_traffic_state_correct_event_count=card_traffic_state_correct_events,
        card_traffic_state_accuracy=card_traffic_state_accuracy,
    )


def write_metrics(path: Path, row: MetricRow) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(row).keys()))
        writer.writeheader()
        writer.writerow(asdict(row))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate event detection CSV output.")
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--event-file", type=Path, default=None)
    parser.add_argument("--cards", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    detections = load_detections(args.detections)
    events = load_events(args.event_file)
    cards = load_cards(args.cards)
    metrics = evaluate(detections, events, cards)
    write_metrics(args.output, metrics)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(asdict(metrics), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"Wrote metrics to {args.output}")
    print(
        f"precision={metrics.precision:.4f} recall={metrics.recall:.4f} "
        f"f1={metrics.f1:.4f} type_acc={metrics.type_accuracy_on_positive:.4f} "
        f"delay={metrics.mean_detection_delay_seconds:.1f}s "
        f"fp_rows_per_hour={metrics.false_positive_rows_per_hour:.2f}"
    )


if __name__ == "__main__":
    main()
