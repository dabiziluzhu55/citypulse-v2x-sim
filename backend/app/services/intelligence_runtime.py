# 会话级事件识别短时预测与路段拥堵样式

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from algorithms.event_detection.cards import build_event_cards
from algorithms.event_detection.rules import RuleConfig, detect_states
from algorithms.event_detection.state import (
    IntersectionState,
    LaneState,
    edge_id_from_lane,
)

logger = logging.getLogger(__name__)

LonLatResolver = Callable[[str], tuple[float | None, float | None]]
IntersectionLonLatResolver = Callable[[str], tuple[float | None, float | None]]

TRAFFIC_STATE_DISPLAY = {
    "localized_blockage": ("localized_blockage", "局部占道"),
    "spillback": ("spillback", "排队溢出"),
    "unknown_abnormal": ("unknown_abnormal", "交通异常"),
    "capacity_drop": ("capacity_drop", "通行能力下降"),
    "normal": ("normal", "正常"),
}

OFFICIAL_NODE_ORDER = (
    "demo_1",
    "demo_10",
    "demo_11",
    "demo_12",
    "demo_13",
    "demo_14",
    "demo_15",
    "demo_16",
    "demo_17",
    "demo_18",
    "demo_19",
    "demo_2",
    "demo_20",
    "demo_3",
    "demo_4",
    "demo_5",
    "demo_6",
    "demo_7",
    "demo_8",
    "demo_9",
)


def _empty_payload(elapsed: float = 0.0) -> dict[str, Any]:
    return {
        "event_detection": {"as_of_seconds": elapsed, "cards": []},
        "prediction": {
            "horizon_seconds": 60.0,
            "as_of_seconds": elapsed,
            "model": "moving_average",
            "ready": False,
            "intersections": {},
        },
        "traffic_style": {"as_of_seconds": elapsed, "edges": {}},
    }


def _lane_has_green(lane: Any) -> bool:
    explicit = getattr(lane, "lane_has_green", None)
    if explicit is not None:
        return bool(explicit)
    signal = str(getattr(lane, "signal_state", "") or "")
    if len(signal) == 1:
        return signal in {"G", "g"}
    return False


def snapshot_to_states(snapshot: Any) -> list[IntersectionState]:
    states: list[IntersectionState] = []
    for intersection_id, intersection in snapshot.intersections.items():
        lanes: list[LaneState] = []
        for lane_id, lane in intersection.lanes.items():
            downstream = getattr(lane, "downstream_lane_ids", ()) or ()
            lanes.append(
                LaneState(
                    lane_id=str(lane_id),
                    edge_id=str(getattr(lane, "edge_id", "") or edge_id_from_lane(str(lane_id))),
                    lane_has_green=_lane_has_green(lane),
                    vehicle_count=int(lane.vehicle_count),
                    halting_count=int(lane.halting_count),
                    mean_speed=float(lane.mean_speed),
                    waiting_time=float(lane.waiting_time),
                    occupancy=float(lane.occupancy),
                    approach_id=str(getattr(lane, "approach_id", "") or ""),
                    signal_state=str(getattr(lane, "signal_state", "") or ""),
                    current_allowed_speed_mps=getattr(lane, "current_allowed_speed_mps", None),
                    downstream_lane_ids=tuple(str(item) for item in downstream),
                )
            )
        states.append(
            IntersectionState(
                source="backend",
                session_id=str(snapshot.session_id),
                sequence=int(snapshot.sequence),
                elapsed_seconds=float(snapshot.elapsed_seconds),
                official_time=str(snapshot.official_time),
                intersection_id=str(intersection_id),
                current_phase=getattr(intersection, "current_phase", None),
                pending_phase=getattr(intersection, "pending_phase", None),
                stage=str(getattr(intersection, "stage", "")),
                stage_elapsed=float(getattr(intersection, "stage_elapsed", 0.0)),
                lanes=tuple(sorted(lanes, key=lambda item: item.lane_id)),
            )
        )
    return states


def _congestion_level(
    *,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy: float,
) -> tuple[str, float]:
    # TraCI占有率为0-100
    if vehicle_count <= 0:
        return "free", 0.0
    halt_ratio = halting_count / max(vehicle_count, 1)
    if mean_speed <= 1.0 and (occupancy >= 35.0 or halt_ratio >= 0.6):
        return "severe", 1.0
    if mean_speed <= 3.0 and (occupancy >= 20.0 or halt_ratio >= 0.4):
        return "congested", 0.75
    if mean_speed <= 8.0 and occupancy >= 10.0:
        return "slow", 0.45
    return "free", 0.15


def _prediction_summary(
    intersection_id: str,
    prediction: dict[str, Any] | None,
) -> str:
    if not prediction:
        return "该路口短时流量预测尚未就绪"
    current = float(prediction["current_vehicle_count"])
    predicted = float(prediction["predicted_vehicle_count"])
    delta = predicted - current
    if abs(current) < 1e-6:
        change = f"预计约{predicted:.0f}辆"
    else:
        ratio = delta / current * 100.0
        direction = "增加" if delta >= 0 else "减少"
        change = f"预计从{current:.0f}辆{direction}到{predicted:.0f}辆({ratio:+.1f}%)"
    return f"该路口未来约1分钟车流量{change}"


class _SessionIntelligence:
    def __init__(
        self,
        *,
        sample_seconds: float,
        history_frames: int,
        horizon_seconds: float,
        lane_to_node: dict[str, str],
        nodes: tuple[str, ...],
        rule_config: RuleConfig,
        lane_lonlat: LonLatResolver,
        intersection_lonlat: IntersectionLonLatResolver,
    ) -> None:
        self.sample_seconds = sample_seconds
        self.history_frames = history_frames
        self.horizon_seconds = horizon_seconds
        self.lane_to_node = lane_to_node
        self.nodes = nodes
        self.rule_config = rule_config
        self.lane_lonlat = lane_lonlat
        self.intersection_lonlat = intersection_lonlat
        self._states: list[IntersectionState] = []
        self._last_bucket: int | None = None
        self._history: deque[dict[str, float]] = deque(maxlen=history_frames)
        self._payload = _empty_payload()
        self._lock = threading.Lock()

    def observe(self, snapshot: Any) -> dict[str, Any]:
        elapsed = float(snapshot.elapsed_seconds)
        bucket = int(elapsed // self.sample_seconds)
        with self._lock:
            traffic_style = self._build_traffic_style(snapshot, elapsed)
            if self._last_bucket is not None and bucket <= self._last_bucket:
                self._payload = {
                    **self._payload,
                    "traffic_style": traffic_style,
                }
                return dict(self._payload)
            self._last_bucket = bucket
            states = snapshot_to_states(snapshot)
            self._states.extend(states)
            detections = detect_states(self._states, config=self.rule_config)
            cards = build_event_cards(detections)
            node_counts = self._aggregate_nodes(snapshot)
            self._history.append(node_counts)
            prediction = self._build_prediction(elapsed, node_counts)
            event_detection = {
                "as_of_seconds": elapsed,
                "cards": [
                    self._serialize_card(card, prediction["intersections"])
                    for card in cards
                    if card.traffic_state != "normal"
                ],
            }
            self._payload = {
                "event_detection": event_detection,
                "prediction": prediction,
                "traffic_style": traffic_style,
            }
            return dict(self._payload)

    def payload(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)

    def _aggregate_nodes(self, snapshot: Any) -> dict[str, float]:
        totals: dict[str, float] = {node: 0.0 for node in self.nodes}
        for intersection_id, intersection in snapshot.intersections.items():
            mapped = 0.0
            total = 0.0
            for lane_id, lane in intersection.lanes.items():
                count = float(lane.vehicle_count)
                total += count
                node = self.lane_to_node.get(str(lane_id))
                if node is None:
                    continue
                totals[node] += count
                if node == str(intersection_id):
                    mapped += count
            if str(intersection_id) in totals and mapped <= 0.0 and total > 0.0:
                totals[str(intersection_id)] = total
        return totals

    def _build_prediction(
        self,
        elapsed: float,
        current_counts: dict[str, float],
    ) -> dict[str, Any]:
        ready = len(self._history) >= min(3, self.history_frames)
        intersections: dict[str, Any] = {}
        for node in self.nodes:
            series = [frame.get(node, 0.0) for frame in self._history]
            current = float(current_counts.get(node, 0.0))
            predicted = sum(series) / len(series) if series else current
            delta = predicted - current
            ratio = None if abs(current) < 1e-6 else delta / current
            intersections[node] = {
                "current_vehicle_count": round(current, 3),
                "predicted_vehicle_count": round(predicted, 3),
                "delta": round(delta, 3),
                "delta_ratio": None if ratio is None else round(ratio, 4),
            }
        return {
            "horizon_seconds": self.horizon_seconds,
            "as_of_seconds": elapsed,
            "model": "moving_average",
            "ready": ready,
            "intersections": intersections,
        }

    def _build_traffic_style(
        self,
        snapshot: Any,
        elapsed: float,
    ) -> dict[str, Any]:
        buckets: dict[str, list[tuple[int, int, float, float]]] = defaultdict(list)
        for intersection in snapshot.intersections.values():
            for lane_id, lane in intersection.lanes.items():
                edge_id = str(getattr(lane, "edge_id", "") or edge_id_from_lane(str(lane_id)))
                if not edge_id or edge_id.startswith(":"):
                    continue
                buckets[edge_id].append(
                    (
                        int(lane.vehicle_count),
                        int(lane.halting_count),
                        float(lane.mean_speed),
                        float(lane.occupancy),
                    )
                )
        edges: dict[str, Any] = {}
        for edge_id, rows in buckets.items():
            vehicle_count = sum(item[0] for item in rows)
            halting_count = sum(item[1] for item in rows)
            speed_num = sum(item[2] * item[0] for item in rows)
            mean_speed = speed_num / vehicle_count if vehicle_count else 0.0
            occupancy = sum(item[3] for item in rows) / len(rows)
            level, score = _congestion_level(
                vehicle_count=vehicle_count,
                halting_count=halting_count,
                mean_speed=mean_speed,
                occupancy=occupancy,
            )
            edges[edge_id] = {
                "level": level,
                "score": round(score, 3),
                "mean_speed": round(mean_speed, 3),
                "occupancy": round(occupancy, 4),
                "vehicle_count": vehicle_count,
                "halting_count": halting_count,
            }
        return {"as_of_seconds": elapsed, "edges": edges}

    def _serialize_card(
        self,
        card: Any,
        predictions: dict[str, Any],
    ) -> dict[str, Any]:
        traffic_state = str(card.traffic_state)
        display_type, display_label = TRAFFIC_STATE_DISPLAY.get(
            traffic_state,
            ("unknown_abnormal", "交通异常"),
        )
        longitude = None
        latitude = None
        for lane_id in card.lane_ids:
            longitude, latitude = self.lane_lonlat(str(lane_id))
            if longitude is not None and latitude is not None:
                break
        if longitude is None or latitude is None:
            longitude, latitude = self.intersection_lonlat(str(card.intersection_id))
        prediction = predictions.get(str(card.intersection_id))
        payload = asdict(card)
        payload.update(
            {
                "display_type": display_type,
                "display_label": display_label,
                "longitude": longitude,
                "latitude": latitude,
                "prediction_summary": _prediction_summary(
                    str(card.intersection_id),
                    prediction,
                ),
            }
        )
        payload["lane_ids"] = list(card.lane_ids)
        payload["evidence"] = list(card.evidence)
        return payload


class IntelligenceHub:
    def __init__(
        self,
        *,
        tls_manifest_path: Path,
        sample_seconds: float = 5.0,
        history_frames: int = 12,
        horizon_seconds: float = 60.0,
        lane_lonlat: LonLatResolver | None = None,
        intersection_lonlat: IntersectionLonLatResolver | None = None,
    ) -> None:
        self.sample_seconds = sample_seconds
        self.history_frames = history_frames
        self.horizon_seconds = horizon_seconds
        self.lane_lonlat = lane_lonlat or (lambda _lane_id: (None, None))
        self.intersection_lonlat = intersection_lonlat or (
            lambda _intersection_id: (None, None)
        )
        self.nodes, self.lane_to_node = _load_lane_to_node(tls_manifest_path)
        # 连帧确认更适合在线快照；CUSUM更依赖封闭残差
        self.rule_config = RuleConfig(
            use_cusum=False,
            consecutive_points=7,
            enable_empty_lane_closure=False,
            enable_queue_blockage=True,
            enable_speed_restriction=True,
            enable_accident=False,
        )
        self._sessions: dict[str, _SessionIntelligence] = {}
        self._lock = threading.RLock()

    def observe(self, snapshot: Any) -> dict[str, Any]:
        session = self._session(snapshot.session_id)
        return session.observe(snapshot)

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return _empty_payload()
            return session.payload()

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _session(self, session_id: str) -> _SessionIntelligence:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = _SessionIntelligence(
                    sample_seconds=self.sample_seconds,
                    history_frames=self.history_frames,
                    horizon_seconds=self.horizon_seconds,
                    lane_to_node=self.lane_to_node,
                    nodes=self.nodes,
                    rule_config=self.rule_config,
                    lane_lonlat=self.lane_lonlat,
                    intersection_lonlat=self.intersection_lonlat,
                )
                self._sessions[session_id] = session
            return session


def _load_lane_to_node(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    if not path.is_file():
        logger.warning("tls_manifest缺失，短时预测仅使用快照中的路口聚合: %s", path)
        return OFFICIAL_NODE_ORDER, {}
    data = json.loads(path.read_text(encoding="utf-8"))["intersections"]
    nodes = tuple(data) if data else OFFICIAL_NODE_ORDER
    lane_to_node: dict[str, str] = {}
    for node, item in data.items():
        for lanes in item.get("incoming_lanes", {}).values():
            for lane_id in lanes:
                lane_to_node[str(lane_id)] = str(node)
    return nodes, lane_to_node
