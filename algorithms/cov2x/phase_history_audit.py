"""Micro-step, movement-specific stop-line crossing observer.

This module is an observer only.  It does not issue actions or mutate the
CoV2X policy.  SUMO invokes ``initialize/on_frame/finish`` through the existing
AI observer hook at the configured simulation step interval.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from threading import Lock
from typing import Any, Mapping
import json
import math
import os
import tempfile
import xml.etree.ElementTree as ET

from algorithms.cov2x.vehicle.movement_corridor import MovementApproachCorridor


SUMO_TIMEBASE_CONTRACT = "SUMO_TIME2STEPS_MS_V1"
SUMO_TIME_TICKS_PER_SECOND = 1000


def sumo_time_to_ticks(seconds: float) -> int:
    """Match SUMO TIME2STEPS exactly for its integer-ms timebase."""
    value = float(seconds)
    if not math.isfinite(value):
        raise ValueError("SUMO time must be finite")
    padding = 0.5 if value >= 0.0 else -0.5
    return int(value * SUMO_TIME_TICKS_PER_SECOND + padding)


LEGAL_PROTECTED_GREEN = "LEGAL_PROTECTED_GREEN"
LEGAL_PERMISSIVE_GREEN = "LEGAL_PERMISSIVE_GREEN"
YELLOW_ENTRY = "YELLOW_ENTRY"
TRUE_RED_ENTRY = "TRUE_RED_ENTRY"
TRUE_RED_YELLOW_ENTRY = "TRUE_RED_YELLOW_ENTRY"
RIGHT_ON_RED_SPECIAL = "RIGHT_ON_RED_SPECIAL"
UNCONTROLLED_OR_OFF = "UNCONTROLLED_OR_OFF"
AMBIGUOUS_PHASE_TRANSITION = "AMBIGUOUS_PHASE_TRANSITION"

CROSSING_CLASSES = (
    LEGAL_PROTECTED_GREEN,
    LEGAL_PERMISSIVE_GREEN,
    YELLOW_ENTRY,
    TRUE_RED_ENTRY,
    TRUE_RED_YELLOW_ENTRY,
    RIGHT_ON_RED_SPECIAL,
    UNCONTROLLED_OR_OFF,
    AMBIGUOUS_PHASE_TRANSITION,
)


def classify_signal_state(state: str | None) -> str:
    mapping = {
        "G": LEGAL_PROTECTED_GREEN,
        "g": LEGAL_PERMISSIVE_GREEN,
        "y": YELLOW_ENTRY,
        "Y": YELLOW_ENTRY,
        "r": TRUE_RED_ENTRY,
        "R": TRUE_RED_ENTRY,
        "u": TRUE_RED_YELLOW_ENTRY,
        "U": TRUE_RED_YELLOW_ENTRY,
        "s": RIGHT_ON_RED_SPECIAL,
        "S": RIGHT_ON_RED_SPECIAL,
        "o": UNCONTROLLED_OR_OFF,
        "O": UNCONTROLLED_OR_OFF,
    }
    return mapping.get(str(state or ""), AMBIGUOUS_PHASE_TRANSITION)


def load_controlled_via_map(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the authoritative via-lane -> TLS link mapping from SUMO net.xml.

    An internal-lane connection index is not necessarily the TLS link index:
    SUMO permits TLS indices to be assigned independently.  Safety audit code
    must therefore use the explicit connection attributes rather than infer a
    TLS index from the internal-lane identifier.
    """
    result: dict[str, dict[str, Any]] = {}
    for _event, element in ET.iterparse(Path(path), events=("end",)):
        if element.tag != "connection":
            element.clear()
            continue
        via = str(element.get("via", ""))
        tls_id = str(element.get("tl", ""))
        link_index = element.get("linkIndex")
        if via and tls_id and link_index is not None:
            record = {
                "via_lane_id": via,
                "tls_id": tls_id,
                "link_index": int(link_index),
                "from_lane": (
                    f"{element.get('from', '')}_{int(element.get('fromLane', '0'))}"
                ),
                "to_lane": (
                    f"{element.get('to', '')}_{int(element.get('toLane', '0'))}"
                ),
            }
            previous = result.get(via)
            if previous is not None and previous != record:
                raise ValueError(f"conflicting controlled via mapping: {via}")
            result[via] = record
        element.clear()
    if not result:
        raise ValueError(f"no TLS-controlled via mappings found in {path}")
    return result


def classify_crossing_phase(
    *,
    state_before: str | None,
    state_after: str | None,
    crossing_time_tick: int,
    transition_time_tick: int | None,
    history_available: bool,
) -> tuple[str, str | None]:
    """Classify on SUMO integer time, with post-move switches owning T+1.

    The project applies controller phase changes after simulationStep and
    before publishing the frame at the same SUMO tick. A crossing first
    observed at the exact phase-switch tick therefore completed under
    state_before. Floating stop-line interpolation is diagnostic only.
    """
    if isinstance(crossing_time_tick, bool) or not isinstance(
        crossing_time_tick, int
    ):
        raise TypeError("crossing_time_tick must be a canonical integer tick")
    if transition_time_tick is not None and (
        isinstance(transition_time_tick, bool)
        or not isinstance(transition_time_tick, int)
    ):
        raise TypeError("transition_time_tick must be a canonical integer tick")
    if not history_available or not state_before or not state_after:
        return AMBIGUOUS_PHASE_TRANSITION, None
    if state_before == state_after:
        return classify_signal_state(state_before), state_before
    if transition_time_tick is None:
        return AMBIGUOUS_PHASE_TRANSITION, None
    if crossing_time_tick <= transition_time_tick:
        return classify_signal_state(state_before), state_before
    return classify_signal_state(state_after), state_after


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class PhaseHistoryCrossingObserver:
    def __init__(
        self,
        metadata: Mapping[str, Any],
        *,
        step_length_s: float,
        controlled_via: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.metadata = dict(metadata)
        self.episode_id = str(metadata.get("episode_id", ""))
        self.step_length_s = float(step_length_s)
        self.step_length_ticks = sumo_time_to_ticks(self.step_length_s)
        if self.step_length_ticks <= 0:
            raise ValueError("phase-history step length must be at least 1 ms")
        self.corridor = MovementApproachCorridor(metadata)
        if controlled_via is None:
            network_path = os.environ.get("COV2X_PHASE_HISTORY_NET_PATH", "")
            controlled_via = (
                load_controlled_via_map(network_path) if network_path else {}
            )
        self.controlled_via = {
            str(via): dict(_as_mapping(record))
            for via, record in controlled_via.items()
        }
        self.connections: dict[tuple[str, str], dict[str, Any]] = {}
        self.terminal_intersections: dict[str, set[str]] = {}
        self.lane_lengths: dict[str, float] = {}
        for tls_id, intersection in _as_mapping(
            metadata.get("intersections")
        ).items():
            item = _as_mapping(intersection)
            for lane_id, lane in _as_mapping(item.get("lanes")).items():
                lane_item = _as_mapping(lane)
                length = float(
                    lane_item.get("length_m", lane_item.get("length", 0.0))
                    or lane_item.get("length", 0.0)
                    or 0.0
                )
                self.lane_lengths[str(lane_id)] = length
            for raw in item.get("connections", ()) or ():
                connection = dict(_as_mapping(raw))
                connection_id = str(connection.get("connection_id", ""))
                if not connection_id:
                    continue
                key = (str(tls_id), connection_id)
                self.connections[key] = connection
                terminal = str(connection.get("from_lane", ""))
                self.terminal_intersections.setdefault(terminal, set()).add(
                    str(tls_id)
                )
        self.previous_frame: dict[str, Any] | None = None
        self.previous_signals: dict[tuple[str, str], dict[str, Any]] = {}
        self.previous_controller: dict[str, tuple[Any, ...]] = {}
        self.controller_epochs: Counter[str] = Counter()
        self.events: list[dict[str, Any]] = []
        self.frames_seen = 0
        self.phase_record_count = 0
        self.consecutive_frame_pairs = 0
        self.nonconsecutive_frame_pairs = 0

    def _signal_snapshot(
        self, frame: Mapping[str, Any]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for tls_id, intersection in _as_mapping(
            frame.get("intersections")
        ).items():
            item = _as_mapping(intersection)
            for lane_id, lane in _as_mapping(item.get("lanes")).items():
                for detail in _as_mapping(lane).get(
                    "connection_signal_states", ()
                ) or ():
                    signal = _as_mapping(detail)
                    connection_id = str(signal.get("connection_id", ""))
                    if not connection_id:
                        continue
                    result[(str(tls_id), connection_id)] = {
                        "state": str(signal.get("signal_state", "")),
                        "terminal_lane_id": str(lane_id),
                        "current_phase": item.get("current_phase"),
                        "pending_phase": item.get("pending_phase"),
                        "stage": str(item.get("stage", "")),
                        "stage_elapsed_s": float(
                            item.get("stage_elapsed", 0.0) or 0.0
                        ),
                    }
        return result

    def _update_controller_epochs(self, frame: Mapping[str, Any]) -> None:
        for tls_id, intersection in _as_mapping(
            frame.get("intersections")
        ).items():
            item = _as_mapping(intersection)
            signature = (
                item.get("current_phase"),
                item.get("pending_phase"),
                str(item.get("stage", "")),
            )
            key = str(tls_id)
            if key in self.previous_controller:
                if signature != self.previous_controller[key]:
                    self.controller_epochs[key] += 1
            else:
                self.controller_epochs[key] = 0
            self.previous_controller[key] = signature

    @staticmethod
    def _controller_span_is_continuous(
        *,
        before_signal: Mapping[str, Any],
        after_signal: Mapping[str, Any],
        before_time_tick: int,
        after_time_tick: int,
    ) -> bool:
        """Prove an unchanged controller stage across a dropped-frame span.

        A latest-frame observer may drop a pending frame under load.  The
        signal history is still complete only when the controller identity is
        unchanged and its stage clock advances by the exact canonical SUMO
        tick span.  Any reset or intervening transition breaks that equality
        and therefore remains ambiguous.
        """
        identity_fields = ("state", "current_phase", "pending_phase", "stage")
        if any(
            before_signal.get(field) != after_signal.get(field)
            for field in identity_fields
        ):
            return False
        try:
            before_elapsed_tick = sumo_time_to_ticks(
                float(before_signal["stage_elapsed_s"])
            )
            after_elapsed_tick = sumo_time_to_ticks(
                float(after_signal["stage_elapsed_s"])
            )
        except (KeyError, TypeError, ValueError):
            return False
        return bool(
            before_elapsed_tick >= 0
            and after_elapsed_tick - before_elapsed_tick
            == after_time_tick - before_time_tick
        )

    def _crossing_time(
        self,
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
        before_time_s: float,
        after_time_s: float,
    ) -> tuple[float, float | None, float | None, str]:
        previous_location = _as_mapping(previous.get("location"))
        current_location = _as_mapping(current.get("location"))
        terminal_lane = str(previous_location.get("lane_id", ""))
        lane_length = self.lane_lengths.get(terminal_lane, 0.0)
        position_before = float(
            previous_location.get("lane_position_m", 0.0) or 0.0
        )
        distance_before = max(0.0, lane_length - position_before)
        distance_after = -float(
            current_location.get("lane_position_m", 0.0) or 0.0
        )
        previous_distance = float(
            _as_mapping(previous.get("traffic")).get("distance_m", 0.0)
            or 0.0
        )
        current_distance = float(
            _as_mapping(current.get("traffic")).get("distance_m", 0.0)
            or 0.0
        )
        traveled = max(0.0, current_distance - previous_distance)
        if traveled > 1e-9:
            fraction = min(1.0, max(0.0, distance_before / traveled))
            method = "cumulative_distance_linear_interpolation"
        else:
            fraction = 1.0
            method = "frame_boundary"
        crossing_time = before_time_s + fraction * (
            after_time_s - before_time_s
        )
        return crossing_time, distance_before, distance_after, method

    def _resolve_tls(
        self, terminal_lane: str, previous: Mapping[str, Any]
    ) -> str | None:
        signal = _as_mapping(previous.get("next_signal"))
        tls_id = str(
            signal.get("intersection_id", signal.get("tls_id", "")) or ""
        )
        if tls_id in self.terminal_intersections.get(terminal_lane, set()):
            return tls_id
        candidates = sorted(self.terminal_intersections.get(terminal_lane, ()))
        return candidates[0] if len(candidates) == 1 else None

    def _crossing_event(
        self,
        *,
        vehicle_id: str,
        previous: Mapping[str, Any],
        current: Mapping[str, Any],
        previous_frame: Mapping[str, Any],
        frame: Mapping[str, Any],
        previous_signals: Mapping[tuple[str, str], Mapping[str, Any]],
        current_signals: Mapping[tuple[str, str], Mapping[str, Any]],
        consecutive: bool,
    ) -> dict[str, Any]:
        before_time = float(previous_frame.get("simulation_time", 0.0) or 0.0)
        after_time = float(frame.get("simulation_time", 0.0) or 0.0)
        previous_location = _as_mapping(previous.get("location"))
        current_location = _as_mapping(current.get("location"))
        terminal_lane = str(previous_location.get("lane_id", ""))
        via_lane = str(current_location.get("lane_id", ""))
        tls_id = self._resolve_tls(terminal_lane, previous)
        resolution = self.corridor.resolve(tls_id or "", previous)
        candidate_ids = tuple(resolution.candidate_connection_ids)
        candidates = [
            self.connections[(str(tls_id), connection_id)]
            for connection_id in candidate_ids
            if (str(tls_id), connection_id) in self.connections
        ]
        candidate_indexes = sorted(
            {
                int(item.get("link_index", -1))
                for item in candidates
                if int(item.get("link_index", -1)) >= 0
            }
        )
        via_record = _as_mapping(self.controlled_via.get(via_lane))
        actual_index_raw = via_record.get("link_index")
        actual_index = (
            int(actual_index_raw) if actual_index_raw is not None else None
        )
        exact_candidates = [
            item
            for (candidate_tls_id, _connection_id), item in self.connections.items()
            if candidate_tls_id == str(tls_id)
            if actual_index is not None
            and int(item.get("link_index", -1)) == actual_index
            and str(item.get("from_lane", "")) == terminal_lane
            and str(item.get("to_lane", ""))
            == str(via_record.get("to_lane", ""))
            and str(item.get("tls_id", ""))
            == str(via_record.get("tls_id", ""))
        ]
        # The net.xml via record owns the physical crossing.  Some generated
        # topology metadata coarsens the lane pair used by the movement
        # corridor, while retaining the authoritative TLS/linkIndex.  In that
        # case use the unique route candidate on the exact authoritative link
        # for phase lookup; never guess from the internal-lane identifier.
        route_link_candidates = [
            item
            for item in candidates
            if actual_index is not None
            and int(item.get("link_index", -1)) == actual_index
            and str(item.get("tls_id", "")) == str(via_record.get("tls_id", ""))
        ]
        if len(exact_candidates) == 1:
            actual_candidates = exact_candidates
            connection_resolution_method = "authoritative_via_lane_tuple"
        elif len(route_link_candidates) == 1:
            actual_candidates = route_link_candidates
            connection_resolution_method = "authoritative_tls_link_index"
        else:
            actual_candidates = []
            connection_resolution_method = "unavailable"
        movement_id = resolution.resolved_movement_id
        mapping_available = bool(
            tls_id
            and resolution.resolved
            and actual_index is not None
            and len(actual_candidates) == 1
        )
        connection_id = (
            str(actual_candidates[0].get("connection_id", ""))
            if mapping_available
            else None
        )
        before_signal = (
            previous_signals.get((str(tls_id), str(connection_id)), {})
            if connection_id
            else {}
        )
        after_signal = (
            current_signals.get((str(tls_id), str(connection_id)), {})
            if connection_id
            else {}
        )
        state_before = str(before_signal.get("state", "")) or None
        state_after = str(after_signal.get("state", "")) or None
        before_time_tick = sumo_time_to_ticks(before_time)
        after_time_tick = sumo_time_to_ticks(after_time)
        previous_frame_id = int(previous_frame.get("frame_id", -1))
        current_frame_id = int(frame.get("frame_id", -1))
        frame_id_delta = current_frame_id - previous_frame_id
        frame_span_canonical = bool(
            frame_id_delta >= 1
            and after_time_tick - before_time_tick
            == frame_id_delta * self.step_length_ticks
        )
        controller_span_continuous = bool(
            not consecutive
            and frame_span_canonical
            and mapping_available
            and state_before
            and state_after
            and self._controller_span_is_continuous(
                before_signal=before_signal,
                after_signal=after_signal,
                before_time_tick=before_time_tick,
                after_time_tick=after_time_tick,
            )
        )
        history_available = bool(
            mapping_available
            and state_before
            and state_after
            and (consecutive or controller_span_continuous)
        )
        phase_history_method = (
            "consecutive_frame_pair"
            if consecutive
            else "controller_stage_tick_continuity"
            if controller_span_continuous
            else "unavailable"
        )
        crossing_time, distance_before, distance_after, timing_method = (
            self._crossing_time(
                previous, current, before_time, after_time
            )
        )
        crossing_event_tick = after_time_tick
        transition_time_tick = (
            after_time_tick
            if history_available and state_before != state_after
            else None
        )
        crossing_class, crossing_state = classify_crossing_phase(
            state_before=state_before,
            state_after=state_after,
            crossing_time_tick=crossing_event_tick,
            transition_time_tick=transition_time_tick,
            history_available=history_available,
        )
        stage_before = str(before_signal.get("stage", ""))
        stage_after = str(after_signal.get("stage", ""))
        route = tuple(
            str(value)
            for value in previous_location.get("route_edges", ()) or ()
        )
        return {
            "vehicle_id": str(vehicle_id),
            "assignment_epoch": None,
            "timebase_contract": SUMO_TIMEBASE_CONTRACT,
            "simulation_time_before_ms": before_time_tick,
            "simulation_time_after_ms": after_time_tick,
            "crossing_event_time_ms": crossing_event_tick,
            "phase_transition_time_ms": transition_time_tick,
            "exact_boundary_owner": "state_before",
            "simulation_time_before_s": before_time,
            "simulation_time_after_s": after_time,
            "crossing_time_s": crossing_time,
            "crossing_time_method": timing_method,
            "crossing_time_diagnostic_only": True,
            "intersection_id": tls_id,
            "movement_id": movement_id,
            "route": list(route),
            "current_lane_before": terminal_lane,
            "current_lane_after": via_lane,
            "terminal_lane_id": terminal_lane,
            "via_lane_id": via_lane,
            "actual_tls_link_index": actual_index,
            "actual_tls_id": str(via_record.get("tls_id", "")) or None,
            "candidate_tls_link_indexes": candidate_indexes,
            "candidate_connection_ids": list(candidate_ids),
            "actual_connection_id": connection_id,
            "connection_resolution_method": connection_resolution_method,
            "lane_position_before_m": float(
                previous_location.get("lane_position_m", 0.0) or 0.0
            ),
            "lane_position_after_m": float(
                current_location.get("lane_position_m", 0.0) or 0.0
            ),
            "distance_to_stopline_before_m": distance_before,
            "distance_to_stopline_after_m": distance_after,
            "phase_state_before": state_before,
            "phase_state_after": state_after,
            "phase_state_at_crossing": crossing_state,
            "phase_transition_timestamp_s": (
                after_time if transition_time_tick is not None else None
            ),
            "phase_transition_timestamp_s_diagnostic_only": True,
            "controller_stage_before": stage_before,
            "controller_stage_after": stage_after,
            "controller_epoch": int(self.controller_epochs[str(tls_id)])
            if tls_id is not None
            else None,
            "spat_source_epoch": None,
            "active_cap_at_crossing": False,
            "movement_link_mapping_available": mapping_available,
            "phase_history_available": history_available,
            "phase_history_method": phase_history_method,
            "phase_history_span_proven": history_available,
            "frame_pair_consecutive": consecutive,
            "frame_span_canonical": frame_span_canonical,
            "frame_id_delta": frame_id_delta,
            "missing_frame_count": max(0, frame_id_delta - 1),
            "crossing_class": crossing_class,
        }

    def on_frame(self, frame: Mapping[str, Any]) -> None:
        current = dict(frame)
        self.frames_seen += 1
        current_signals = self._signal_snapshot(current)
        if current_signals:
            self.phase_record_count += 1
        self._update_controller_epochs(current)
        previous_frame = self.previous_frame
        if previous_frame is not None:
            previous_id = int(previous_frame.get("frame_id", -1))
            current_id = int(current.get("frame_id", -1))
            before_time = float(
                previous_frame.get("simulation_time", 0.0) or 0.0
            )
            after_time = float(current.get("simulation_time", 0.0) or 0.0)
            before_time_tick = sumo_time_to_ticks(before_time)
            after_time_tick = sumo_time_to_ticks(after_time)
            consecutive = bool(
                current_id == previous_id + 1
                and after_time_tick - before_time_tick
                == self.step_length_ticks
            )
            if consecutive:
                self.consecutive_frame_pairs += 1
            else:
                self.nonconsecutive_frame_pairs += 1
            previous_vehicles = _as_mapping(previous_frame.get("vehicles"))
            current_vehicles = _as_mapping(current.get("vehicles"))
            for vehicle_id, previous_vehicle in previous_vehicles.items():
                vehicle = current_vehicles.get(vehicle_id)
                if not isinstance(vehicle, Mapping):
                    continue
                previous_item = _as_mapping(previous_vehicle)
                terminal_lane = str(
                    _as_mapping(previous_item.get("location")).get(
                        "lane_id", ""
                    )
                )
                via_lane = str(
                    _as_mapping(vehicle.get("location")).get("lane_id", "")
                )
                if (
                    terminal_lane not in self.terminal_intersections
                    or not via_lane.startswith(":")
                ):
                    continue
                event = self._crossing_event(
                    vehicle_id=str(vehicle_id),
                    previous=previous_item,
                    current=vehicle,
                    previous_frame=previous_frame,
                    frame=current,
                    previous_signals=self.previous_signals,
                    current_signals=current_signals,
                    consecutive=consecutive,
                )
                self.events.append(event)
                if (
                    os.environ.get("COV2X_PHASE_HISTORY_FAIL_FAST") == "1"
                    and event.get("crossing_class")
                    in {TRUE_RED_ENTRY, TRUE_RED_YELLOW_ENTRY}
                ):
                    raise RuntimeError(
                        "phase-history fail-fast: "
                        f"{event.get('crossing_class')} "
                        f"vehicle={event.get('vehicle_id')} "
                        f"tick_ms={event.get('crossing_event_time_ms')}"
                    )
        self.previous_frame = current
        self.previous_signals = current_signals

    def finish(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        ledger = dict(_as_mapping(summary.get("observer_ledger")))
        executed_tick_ids = list(ledger.get("executed_tick_ids", ()) or ())
        committed_tick_ids = list(
            ledger.get("observer_committed_tick_ids", ()) or ()
        )
        executed_set = set(executed_tick_ids)
        committed_set = set(committed_tick_ids)
        crossing_event_count = len(self.events)
        classified_crossing_count = sum(
            str(event.get("crossing_class")) in CROSSING_CLASSES
            and str(event.get("crossing_class"))
            != AMBIGUOUS_PHASE_TRANSITION
            for event in self.events
        )
        executed_count = len(executed_tick_ids)
        return {
            "episode_id": self.episode_id,
            "timebase_contract": SUMO_TIMEBASE_CONTRACT,
            "step_length_s": self.step_length_s,
            "step_length_ms": self.step_length_ticks,
            "delivery_mode": ledger.get("delivery_mode", "latest"),
            "executed_tick_ids": executed_tick_ids,
            "observer_committed_tick_ids": committed_tick_ids,
            "missing_ticks": list(ledger.get("missing_ticks", ()) or ()),
            "duplicate_ticks": list(ledger.get("duplicate_ticks", ()) or ()),
            "out_of_order_ticks": list(
                ledger.get("out_of_order_ticks", ()) or ()
            ),
            "unexpected_committed_ticks": list(
                ledger.get("unexpected_committed_ticks", ()) or ()
            ),
            "tick_coverage": (
                len(executed_set & committed_set) / executed_count
                if executed_count
                else 0.0
            ),
            "phase_record_count": self.phase_record_count,
            "phase_coverage": (
                self.phase_record_count / executed_count
                if executed_count
                else 0.0
            ),
            "crossing_event_count": crossing_event_count,
            "classified_crossing_count": classified_crossing_count,
            "crossing_classification_coverage": (
                classified_crossing_count / crossing_event_count
                if crossing_event_count
                else 0.0
            ),
            "finalized": ledger.get("finalized") is True,
            "summary_persisted": False,
            "frames_seen": self.frames_seen,
            "consecutive_frame_pairs": self.consecutive_frame_pairs,
            "nonconsecutive_frame_pairs": self.nonconsecutive_frame_pairs,
            "observer_frames": dict(
                _as_mapping(summary.get("observer_frames"))
            ),
            "crossing_events": self.events,
        }


def observer_integrity_failures(
    summary: Mapping[str, Any], *, require_persisted: bool = False
) -> list[str]:
    """Return exact synchronous-observer contract failures."""
    failures: list[str] = []
    executed = list(summary.get("executed_tick_ids", ()) or ())
    committed = list(summary.get("observer_committed_tick_ids", ()) or ())
    frames = dict(_as_mapping(summary.get("observer_frames")))
    if summary.get("delivery_mode") != "synchronous":
        failures.append("delivery_mode")
    if not executed:
        failures.append("executed_tick_ids")
    if float(summary.get("tick_coverage", 0.0)) != 1.0:
        failures.append("tick_coverage")
    if float(summary.get("phase_coverage", 0.0)) != 1.0:
        failures.append("phase_coverage")
    if float(summary.get("crossing_classification_coverage", 0.0)) != 1.0:
        failures.append("crossing_classification_coverage")
    if list(summary.get("missing_ticks", ()) or ()):
        failures.append("missing_ticks")
    if list(summary.get("duplicate_ticks", ()) or ()):
        failures.append("duplicate_ticks")
    if list(summary.get("out_of_order_ticks", ()) or ()):
        failures.append("out_of_order_ticks")
    if list(summary.get("unexpected_committed_ticks", ()) or ()):
        failures.append("unexpected_committed_ticks")
    if summary.get("finalized") is not True:
        failures.append("finalized")
    if require_persisted and summary.get("summary_persisted") is not True:
        failures.append("summary_persisted")
    if (
        int(frames.get("generated", -1)) != len(executed)
        or int(frames.get("consumed", -1)) != len(committed)
        or int(frames.get("dropped", -1)) != 0
        or int(summary.get("frames_seen", -1)) != len(committed)
        or int(summary.get("phase_record_count", -1)) != len(executed)
    ):
        failures.append("observer_count_identity")
    return sorted(set(failures))


def _atomic_write_summary(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"observer summary already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(dict(payload), handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o444)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise


_lock = Lock()
_active: PhaseHistoryCrossingObserver | None = None
_completed: list[dict[str, Any]] = []
_last_fatal_event: dict[str, Any] | None = None


def initialize(metadata: Mapping[str, Any]) -> None:
    global _active
    step_length = float(
        os.environ.get("COV2X_PHASE_HISTORY_STEP_LENGTH", "0.05") or 0.05
    )
    with _lock:
        _active = PhaseHistoryCrossingObserver(
            metadata, step_length_s=step_length
        )


def on_frame(frame: Mapping[str, Any]) -> None:
    global _last_fatal_event
    with _lock:
        observer = _active
    if observer is None:
        raise RuntimeError("phase-history observer is not initialized")
    try:
        observer.on_frame(frame)
    except RuntimeError:
        event = dict(observer.events[-1]) if observer.events else None
        if event and event.get("crossing_class") in {
            TRUE_RED_ENTRY,
            TRUE_RED_YELLOW_ENTRY,
        }:
            with _lock:
                _last_fatal_event = event
        raise


def finish(summary: Mapping[str, Any]) -> None:
    global _active
    with _lock:
        observer = _active
        _active = None
    if observer is None:
        raise RuntimeError("phase-history observer is not initialized")
    result = observer.finish(summary)
    summary_path_raw = os.environ.get("COV2X_PHASE_HISTORY_SUMMARY_PATH", "")
    persistence_error: BaseException | None = None
    require_complete = (
        os.environ.get("COV2X_PHASE_HISTORY_REQUIRE_COMPLETE") == "1"
    )
    if summary_path_raw:
        persisted = dict(result)
        persisted["summary_persisted"] = True
        persisted["integrity_failures"] = observer_integrity_failures(
            persisted, require_persisted=require_complete
        )
        try:
            _atomic_write_summary(Path(summary_path_raw), persisted)
        except BaseException as exc:
            persistence_error = exc
        else:
            result = persisted
    failures = observer_integrity_failures(
        result, require_persisted=require_complete
    )
    result["integrity_failures"] = failures
    with _lock:
        _completed.append(result)
    if persistence_error is not None:
        raise RuntimeError(
            f"phase-history observer summary persistence failed: {persistence_error}"
        ) from persistence_error
    if (
        require_complete and failures
    ):
        raise RuntimeError(
            f"phase-history synchronous observer incomplete: {failures}"
        )


def take_completed(episode_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if episode_id is None:
            return _completed.pop(0) if _completed else None
        for index, result in enumerate(_completed):
            if result.get("episode_id") == episode_id:
                return _completed.pop(index)
    return None


def fatal_event_snapshot() -> dict[str, Any] | None:
    with _lock:
        return dict(_last_fatal_event) if _last_fatal_event is not None else None


def reset_completed() -> None:
    global _active, _last_fatal_event
    with _lock:
        _completed.clear()
        _active = None
        _last_fatal_event = None
