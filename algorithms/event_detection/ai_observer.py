"""AI observer bridge for realtime SUMO protocol 2.0 frames."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from .cards import build_event_cards, write_cards
from .rules import DetectionRow, RuleConfig, detect_states, write_detections
from .state import IntersectionState, realtime_json_to_intersection_states


DEFAULT_OUTPUT_ROOT = Path("outputs/event_detection/ai_observer")


class MetadataGreenResolver:
    """Resolve lane topology and fallback green rights from initialize metadata."""

    def __init__(self) -> None:
        self._lane_metadata: dict[tuple[str, str], Mapping[str, object]] = {}
        self._phase_lanes: dict[tuple[str, int], set[str]] = {}

    def initialize(self, metadata: Mapping[str, object]) -> None:
        self._lane_metadata.clear()
        self._phase_lanes.clear()
        intersections = _mapping(metadata.get("intersections"), "intersections")
        for intersection_id, raw_intersection in intersections.items():
            intersection = _mapping(
                raw_intersection,
                f"intersections.{intersection_id}",
            )
            lanes = _mapping(intersection.get("lanes", {}), f"{intersection_id}.lanes")
            for lane_id, lane in lanes.items():
                self._lane_metadata[(str(intersection_id), str(lane_id))] = _mapping(
                    lane,
                    f"{intersection_id}.lanes.{lane_id}",
                )

            connection_lanes = {}
            for raw_connection in intersection.get("connections", []) or []:
                connection = _mapping(raw_connection, f"{intersection_id}.connections")
                connection_id = str(connection.get("connection_id", ""))
                from_lane = str(connection.get("from_lane", ""))
                if connection_id and from_lane:
                    connection_lanes[connection_id] = from_lane

            phases = _mapping(intersection.get("phases", {}), f"{intersection_id}.phases")
            for phase_id, raw_phase in phases.items():
                phase = _mapping(raw_phase, f"{intersection_id}.phases.{phase_id}")
                green_lanes = set()
                priorities = _mapping(
                    phase.get("connection_priorities", {}),
                    f"{intersection_id}.phases.{phase_id}.connection_priorities",
                )
                for connection_id, priority in priorities.items():
                    if str(priority) in {"protected", "permissive"}:
                        lane_id = connection_lanes.get(str(connection_id))
                        if lane_id:
                            green_lanes.add(lane_id)
                try:
                    self._phase_lanes[(str(intersection_id), int(phase_id))] = green_lanes
                except ValueError:
                    continue

    def lane_metadata(self, intersection_id: str, lane_id: str) -> Mapping[str, object]:
        return self._lane_metadata.get((intersection_id, lane_id), {})

    def lane_has_green(
        self,
        row: Mapping[str, object],
        current_phase: int | None,
        stage: str,
    ) -> bool:
        if stage != "GREEN" or current_phase is None:
            return False
        intersection_id = str(row.get("intersection_id", ""))
        lane_id = str(row.get("lane_id", ""))
        return lane_id in self._phase_lanes.get((intersection_id, current_phase), set())


class EventDetectionObserver:
    """Update event detections as realtime SUMO frames arrive."""

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        config: RuleConfig | None = None,
        max_gap_seconds: float = 180.0,
        live_output: bool | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.config = config or _config_from_env()
        self.max_gap_seconds = max_gap_seconds
        self.live_output = (
            _env_bool("EVENT_DETECTION_LIVE_OUTPUT", True)
            if live_output is None
            else live_output
        )
        self.resolver = MetadataGreenResolver()
        self._states: list[IntersectionState] = []
        self._episode_id = ""
        self._metadata: Mapping[str, object] = {}
        self._frame_count = 0
        self._live_update_count = 0
        self._latest_simulation_time: float | None = None

    def initialize(self, metadata: Mapping[str, object]) -> None:
        self._metadata = metadata
        self._episode_id = str(metadata.get("episode_id", "latest"))
        self.resolver.initialize(metadata)
        self._states = []
        self._frame_count = 0
        self._live_update_count = 0
        self._latest_simulation_time = None

    def on_frame(self, frame: Mapping[str, object]) -> None:
        enriched = self._enrich_frame(frame)
        self._states.extend(
            realtime_json_to_intersection_states(
                enriched,
                resolver=self.resolver,
                source="ai_observer",
            )
        )
        self._frame_count += 1
        self._latest_simulation_time = _optional_float(frame.get("simulation_time"))
        if self.live_output:
            self._write_outputs(status="live")

    def finish(self, summary: Mapping[str, object]) -> dict[str, object]:
        return self._write_outputs(status="final", summary=summary)

    def _write_outputs(
        self,
        *,
        status: str,
        summary: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        output_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        detections = detect_states(self._states, config=self.config)
        cards = build_event_cards(detections, max_gap_seconds=self.max_gap_seconds)

        detections_path = output_dir / "event_detection_realtime_detections.csv"
        cards_path = output_dir / "event_detection_realtime_cards.json"
        summary_path = output_dir / "event_detection_observer_summary.json"

        if detections:
            write_detections(detections_path, detections)
        write_cards(cards_path, cards)
        observer_frames = (
            summary.get("observer_frames", {})
            if summary is not None and isinstance(summary.get("observer_frames"), Mapping)
            else {}
        )
        generated_frames = (
            int(observer_frames.get("generated", 0))
            if observer_frames
            else self._frame_count
        )
        summary_payload = {
            "episode_id": self._episode_id,
            "status": status,
            "frames": generated_frames,
            "processed_frames": self._frame_count,
            "live_update_count": self._live_update_count,
            "latest_simulation_time": self._latest_simulation_time,
            "intersection_state_count": len(self._states),
            "detection_row_count": len(detections),
            "event_card_count": len(cards),
            "event_types": _event_type_counts(detections),
            "traffic_states": _field_counts(detections, "traffic_state"),
            "causes": _field_counts(detections, "cause"),
            "output_dir": str(output_dir),
        }
        if observer_frames:
            summary_payload["observer_frames"] = dict(observer_frames)
        if status == "live":
            self._live_update_count += 1
            summary_payload["live_update_count"] = self._live_update_count
        summary_path.write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary_payload

    def _resolve_output_dir(self) -> Path:
        if self.output_dir is not None:
            return self.output_dir
        configured = os.environ.get("EVENT_DETECTION_OUTPUT_DIR")
        if configured:
            return Path(configured)
        return DEFAULT_OUTPUT_ROOT / (self._episode_id or "latest")

    def _enrich_frame(self, frame: Mapping[str, object]) -> dict[str, object]:
        payload = dict(frame)
        payload.setdefault("step_id", frame.get("frame_id"))
        intersections = _mapping(frame.get("intersections"), "intersections")
        enriched_intersections = {}
        for intersection_id, raw_intersection in intersections.items():
            intersection = dict(_mapping(raw_intersection, f"intersections.{intersection_id}"))
            lanes = _mapping(intersection.get("lanes"), f"intersections.{intersection_id}.lanes")
            enriched_lanes = {}
            for lane_id, raw_lane in lanes.items():
                lane = dict(self.resolver.lane_metadata(str(intersection_id), str(lane_id)))
                lane.update(_mapping(raw_lane, f"lanes.{lane_id}"))
                if "movement" not in lane and "movements" in lane:
                    lane["movement"] = ";".join(str(item) for item in lane["movements"])
                _fill_signal_summary(lane)
                enriched_lanes[str(lane_id)] = lane
            intersection["lanes"] = enriched_lanes
            enriched_intersections[str(intersection_id)] = intersection
        payload["intersections"] = enriched_intersections
        return payload


def initialize(metadata: dict) -> None:
    _GLOBAL_OBSERVER.initialize(metadata)


def on_frame(frame: dict) -> None:
    _GLOBAL_OBSERVER.on_frame(frame)


def finish(summary: dict) -> None:
    _GLOBAL_OBSERVER.finish(summary)


def _config_from_env() -> RuleConfig:
    return RuleConfig(
        use_cusum=_env_bool("EVENT_DETECTION_USE_CUSUM", True),
        enable_empty_lane_closure=_env_bool(
            "EVENT_DETECTION_ENABLE_EMPTY_LANE_CLOSURE",
            False,
        ),
        enable_queue_blockage=_env_bool("EVENT_DETECTION_ENABLE_SPILLBACK", True),
        enable_speed_restriction=_env_bool(
            "EVENT_DETECTION_ENABLE_SPEED_RESTRICTION",
            True,
        ),
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _optional_float(value: object | None) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _event_type_counts(rows: list[DetectionRow]) -> dict[str, int]:
    return _field_counts(rows, "event_type")


def _field_counts(rows: list[DetectionRow], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(getattr(row, field))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _fill_signal_summary(lane: dict[str, object]) -> None:
    if "lane_has_green" in lane and lane.get("signal_state") not in {None, ""}:
        return
    states = [
        str(item.get("signal_state", ""))
        for item in lane.get("connection_signal_states", []) or []
        if isinstance(item, Mapping) and item.get("signal_state") not in {None, ""}
    ]
    if not states:
        return
    if "lane_has_green" not in lane:
        lane["lane_has_green"] = any(state in {"G", "g"} for state in states)
    if lane.get("signal_state") in {None, ""}:
        lane["signal_state"] = states[0] if len(set(states)) == 1 else "mixed"


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"field {field!r} must be an object")
    return value


_GLOBAL_OBSERVER = EventDetectionObserver()
