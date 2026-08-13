# 会话级事件识别、短时预测与路段拥堵样式

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from algorithms.event_detection.cards import EventCard, build_event_cards
from algorithms.event_detection.rules import RuleConfig, detect_states
from algorithms.event_detection.state import (
    IntersectionState,
    LaneState,
    edge_id_from_lane,
)

from .prediction_runtime import PredictionRuntime

logger = logging.getLogger(__name__)

LonLatResolver = Callable[[str], tuple[float | None, float | None]]
IntersectionLonLatResolver = Callable[[str], tuple[float | None, float | None]]

TRAFFIC_STATE_DISPLAY = {
    "localized_blockage": ("localized_blockage", "疑似局部阻塞"),
    "spillback": ("spillback", "排队溢出"),
    "unknown_abnormal": ("unknown_abnormal", "交通异常"),
    "capacity_drop": ("capacity_drop", "通行能力下降"),
    "normal": ("normal", "正常"),
}

CONGESTION_RANK = {"free": 0, "slow": 1, "congested": 2, "severe": 3}
CONGESTION_SCORE = {"free": 0.0, "slow": 0.45, "congested": 0.75, "severe": 1.0}

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


def _empty_prediction(elapsed: float = 0.0, horizon: float = 60.0) -> dict[str, Any]:
    return {
        "horizon_seconds": horizon,
        "as_of_seconds": elapsed,
        "model": "moving_average",
        "model_version": "",
        "ready": False,
        "fallback": True,
        "fallback_reason": "not_ready",
        "inference_latency_ms": None,
        "intersections": {},
    }


def _empty_payload(elapsed: float = 0.0, horizon: float = 60.0) -> dict[str, Any]:
    return {
        "event_detection": {"as_of_seconds": elapsed, "cards": []},
        "prediction": _empty_prediction(elapsed, horizon),
        "traffic_style": {"as_of_seconds": elapsed, "edges": {}},
    }


def occupancy_to_pct(raw: float | None) -> float:
    """统一为0～100占有率百分数（occupancy_pct）

    TraCI `getLastStepOccupancy` 本身为百分数；若上游误传0～1小数且值<=1，
    仅在明确是比例时放大（>1则已是百分数）
    """

    value = float(raw or 0.0)
    if value < 0:
        return 0.0
    if value <= 1.0:
        # 兼容误用比例：0.25 -> 25；真实0.25%在本路网极少，且拥堵规则不依赖极低占有率
        # 但TraCI长车道常见0.x百分数。优先按“已是百分数”处理，避免把0.4%误当成40%。
        return min(100.0, value)
    return min(100.0, value)


def _lane_has_green(lane: Any) -> bool:
    explicit = getattr(lane, "lane_has_green", None)
    if explicit is not None:
        return bool(explicit)
    signal = str(getattr(lane, "signal_state", "") or "")
    if len(signal) == 1:
        return signal in {"G", "g"}
    return False


def snapshot_to_states(snapshot: Any) -> list[IntersectionState]:
    """转为事件检测输入；不传入allowed_speed，避免答案泄露"""

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
                    occupancy=occupancy_to_pct(getattr(lane, "occupancy", 0.0)),
                    approach_id=str(getattr(lane, "approach_id", "") or ""),
                    signal_state=str(getattr(lane, "signal_state", "") or ""),
                    # 检测侧屏蔽封道/事故能力证据；仿真侧字段仍保留在原snapshot
                    current_allowed_speed_mps=None,
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


def instant_congestion_level(
    *,
    vehicle_count: int,
    halting_count: int,
    mean_speed: float,
    occupancy_pct: float,
) -> tuple[str, float]:
    """瞬时拥堵等级（百分数占有率口径）"""

    if vehicle_count <= 2 or (halting_count <= 1 and occupancy_pct < 5.0):
        return "free", CONGESTION_SCORE["free"]
    halt_ratio = halting_count / max(vehicle_count, 1)
    if (
        halting_count >= 6
        and mean_speed <= 1.0
        and (halt_ratio >= 0.6 or occupancy_pct >= 35.0)
    ):
        return "severe", CONGESTION_SCORE["severe"]
    if (
        halting_count >= 4
        and mean_speed <= 3.0
        and (halt_ratio >= 0.4 or occupancy_pct >= 20.0)
    ):
        return "congested", CONGESTION_SCORE["congested"]
    if halting_count >= 2 and (mean_speed <= 8.0 or occupancy_pct >= 10.0):
        return "slow", CONGESTION_SCORE["slow"]
    return "free", CONGESTION_SCORE["free"]


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


@dataclass
class _EdgeLevelState:
    level: str = "free"
    pending: str | None = None
    pending_count: int = 0


@dataclass
class _ActiveEventRecord:
    event_id: str
    start_seconds: float
    traffic_state: str
    edge_id: str
    lane_ids: tuple[str, ...]
    event_type: str
    cause: str
    cause_confidence: float
    approach_id: str
    confidence_sum: float
    confidence_count: int
    evidence: tuple[str, ...]
    suggestion: str
    severity: str


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
        prediction_runtime: PredictionRuntime,
    ) -> None:
        self.sample_seconds = sample_seconds
        self.history_frames = history_frames
        self.horizon_seconds = horizon_seconds
        self.lane_to_node = lane_to_node
        self.nodes = nodes
        self.rule_config = rule_config
        self.lane_lonlat = lane_lonlat
        self.intersection_lonlat = intersection_lonlat
        self.prediction_runtime = prediction_runtime
        self._states: list[IntersectionState] = []
        self._last_bucket: int | None = None
        self._count_history: deque[dict[str, float]] = deque(maxlen=history_frames)
        # NarrowNet-TDP按206车道采样；路口级仅用于moving_average降级与卡片展示
        self._lane_feature_history: deque[dict[str, dict[str, float]]] = deque(
            maxlen=max(history_frames, 12)
        )
        self._edge_levels: dict[str, _EdgeLevelState] = {}
        self._active_events: dict[tuple[str, str, str], _ActiveEventRecord] = {}
        self._payload = _empty_payload(horizon=horizon_seconds)
        self._lock = threading.Lock()

    def observe(self, snapshot: Any) -> dict[str, Any]:
        elapsed = float(snapshot.elapsed_seconds)
        bucket = int(elapsed // self.sample_seconds)
        with self._lock:
            if self._last_bucket is not None and bucket <= self._last_bucket:
                return dict(self._payload)
            self._last_bucket = bucket

            traffic_style = self._build_traffic_style(snapshot, elapsed)
            states = snapshot_to_states(snapshot)
            self._states.extend(states)
            history_span = max(self.sample_seconds * 36.0, 180.0)
            cutoff = elapsed - history_span
            if cutoff > 0:
                self._states = [
                    item for item in self._states if item.elapsed_seconds >= cutoff
                ]
            detections = detect_states(self._states, config=self.rule_config)
            raw_cards = build_event_cards(detections)
            cards = self._stabilize_cards(raw_cards, elapsed)

            self._lane_feature_history.append(self._collect_lane_features(snapshot))
            feature_frame = self._aggregate_node_features(snapshot)
            node_counts = {
                node: float(feature_frame.get(node, {}).get("vehicle_count", 0.0))
                for node in self.nodes
            }
            self._count_history.append(node_counts)
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

    def _event_key(self, card: EventCard) -> tuple[str, str, str]:
        scope = card.edge_id or ",".join(card.lane_ids)
        return (str(card.intersection_id), str(card.traffic_state), scope)

    def _stabilize_cards(self, cards: list[EventCard], elapsed: float) -> list[EventCard]:
        """会话级活动事件注册表：窗口裁剪不改变event_id与最早start_seconds"""

        active_keys: set[tuple[str, str, str]] = set()
        stabilized: list[EventCard] = []
        for card in cards:
            if card.traffic_state == "normal":
                continue
            key = self._event_key(card)
            if card.status != "active":
                continue
            active_keys.add(key)
            existing = self._active_events.get(key)
            if existing is None:
                record = _ActiveEventRecord(
                    event_id=card.event_id,
                    start_seconds=float(card.start_seconds),
                    traffic_state=card.traffic_state,
                    edge_id=card.edge_id,
                    lane_ids=tuple(card.lane_ids),
                    event_type=card.event_type,
                    cause=card.cause,
                    cause_confidence=float(card.cause_confidence),
                    approach_id=card.approach_id,
                    confidence_sum=float(card.confidence),
                    confidence_count=1,
                    evidence=tuple(card.evidence),
                    suggestion=card.suggestion,
                    severity=card.severity,
                )
                self._active_events[key] = record
            else:
                record = existing
                record.lane_ids = tuple(sorted(set(record.lane_ids) | set(card.lane_ids)))
                record.confidence_sum += float(card.confidence)
                record.confidence_count += 1
                record.evidence = tuple(sorted(set(record.evidence) | set(card.evidence)))
                record.suggestion = card.suggestion or record.suggestion
                record.severity = card.severity
                record.event_type = card.event_type
                record.cause = card.cause
                record.cause_confidence = float(card.cause_confidence)
            confidence = record.confidence_sum / max(record.confidence_count, 1)
            duration = max(0.0, elapsed - record.start_seconds)
            stabilized.append(
                EventCard(
                    event_id=record.event_id,
                    status="active",
                    event_type=record.event_type,
                    traffic_state=record.traffic_state,
                    cause=record.cause,
                    cause_confidence=record.cause_confidence,
                    intersection_id=key[0],
                    lane_ids=record.lane_ids,
                    edge_id=record.edge_id,
                    approach_id=record.approach_id,
                    start_seconds=record.start_seconds,
                    end_seconds=None,
                    duration_seconds=duration,
                    severity=record.severity,
                    confidence=confidence,
                    evidence=record.evidence,
                    suggestion=record.suggestion,
                )
            )

        for key in list(self._active_events):
            if key in active_keys:
                continue
            record = self._active_events.pop(key)
            duration = max(0.0, elapsed - record.start_seconds)
            stabilized.append(
                EventCard(
                    event_id=record.event_id,
                    status="ended",
                    event_type=record.event_type,
                    traffic_state=record.traffic_state,
                    cause=record.cause,
                    cause_confidence=record.cause_confidence,
                    intersection_id=key[0],
                    lane_ids=record.lane_ids,
                    edge_id=record.edge_id,
                    approach_id=record.approach_id,
                    start_seconds=record.start_seconds,
                    end_seconds=elapsed,
                    duration_seconds=duration,
                    severity=record.severity,
                    confidence=record.confidence_sum / max(record.confidence_count, 1),
                    evidence=record.evidence,
                    suggestion=record.suggestion,
                )
            )
        return stabilized

    def _collect_lane_features(self, snapshot: Any) -> dict[str, dict[str, float]]:
        # occupancy保持TraCI原始口径与NarrowNet-TDP训练归一化一致
        features: dict[str, dict[str, float]] = {}
        for intersection in snapshot.intersections.values():
            for lane_id, lane in intersection.lanes.items():
                features[str(lane_id)] = {
                    "vehicle_count": float(lane.vehicle_count),
                    "halting_count": float(lane.halting_count),
                    "mean_speed": float(lane.mean_speed),
                    "occupancy": float(getattr(lane, "occupancy", 0.0)),
                }
        return features

    def _aggregate_node_features(
        self, snapshot: Any
    ) -> dict[str, dict[str, float]]:
        # [vehicle, halting, speed*veh, occupancy_pct sum, lane_count]
        totals: dict[str, list[float]] = {
            node: [0.0, 0.0, 0.0, 0.0, 0.0] for node in self.nodes
        }
        for intersection_id, intersection in snapshot.intersections.items():
            mapped = 0.0
            local = [0.0, 0.0, 0.0, 0.0, 0.0]
            for lane_id, lane in intersection.lanes.items():
                count = float(lane.vehicle_count)
                occ = occupancy_to_pct(getattr(lane, "occupancy", 0.0))
                local[0] += count
                local[1] += float(lane.halting_count)
                local[2] += float(lane.mean_speed) * count
                local[3] += occ
                local[4] += 1.0
                node = self.lane_to_node.get(str(lane_id))
                if node is None or node not in totals:
                    continue
                mapped += count
                totals[node][0] += count
                totals[node][1] += float(lane.halting_count)
                totals[node][2] += float(lane.mean_speed) * count
                totals[node][3] += occ
                totals[node][4] += 1.0
            if str(intersection_id) in totals and mapped <= 0.0 and local[4] > 0:
                totals[str(intersection_id)] = local

        features: dict[str, dict[str, float]] = {}
        for node, row in totals.items():
            vehicle_count, halting, speed_num, occ_sum, lane_count = row
            features[node] = {
                "vehicle_count": vehicle_count,
                "halting_count": halting,
                "mean_speed": (speed_num / vehicle_count) if vehicle_count > 0 else 0.0,
                "occupancy": (occ_sum / lane_count) if lane_count > 0 else 0.0,
            }
        return features

    def _aggregate_lane_predictions(
        self, lane_values: dict[str, float]
    ) -> dict[str, float]:
        predicted_by_node = {node: 0.0 for node in self.nodes}
        for lane_id, value in lane_values.items():
            node = self.lane_to_node.get(str(lane_id))
            if node is None or node not in predicted_by_node:
                continue
            predicted_by_node[node] += float(value)
        return predicted_by_node

    def _build_prediction(
        self,
        elapsed: float,
        current_counts: dict[str, float],
    ) -> dict[str, Any]:
        lane_values, pred_meta = self.prediction_runtime.predict_vehicle_counts(
            list(self._lane_feature_history)
        )
        if lane_values is not None and not pred_meta.get("fallback"):
            node_preds = self._aggregate_lane_predictions(lane_values)
            intersections: dict[str, Any] = {}
            for node in self.nodes:
                current = float(current_counts.get(node, 0.0))
                predicted = float(node_preds.get(node, current))
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
                "model": pred_meta.get("model") or self.prediction_runtime.status.model,
                "model_version": pred_meta.get("model_version")
                or self.prediction_runtime.status.model_version,
                "ready": True,
                "fallback": False,
                "fallback_reason": "",
                "inference_latency_ms": pred_meta.get("inference_latency_ms"),
                "intersections": intersections,
            }

        ready = len(self._count_history) >= min(3, self.history_frames)
        intersections = {}
        for node in self.nodes:
            series = [frame.get(node, 0.0) for frame in self._count_history]
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
        reason = str(pred_meta.get("fallback_reason") or "narrow_net_tdp_unavailable")
        return {
            "horizon_seconds": self.horizon_seconds,
            "as_of_seconds": elapsed,
            "model": "moving_average",
            "model_version": "",
            "ready": ready,
            "fallback": True,
            "fallback_reason": reason,
            "inference_latency_ms": pred_meta.get("inference_latency_ms"),
            "intersections": intersections,
        }

    def _apply_level_hysteresis(self, edge_id: str, instant: str) -> str:
        state = self._edge_levels.get(edge_id)
        if state is None:
            self._edge_levels[edge_id] = _EdgeLevelState(level=instant)
            return instant
        if instant == state.level:
            state.pending = None
            state.pending_count = 0
            return state.level
        if state.pending == instant:
            state.pending_count += 1
        else:
            state.pending = instant
            state.pending_count = 1
        if state.pending_count >= 2:
            state.level = instant
            state.pending = None
            state.pending_count = 0
        return state.level

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
                        occupancy_to_pct(getattr(lane, "occupancy", 0.0)),
                    )
                )
        edges: dict[str, Any] = {}
        for edge_id, rows in buckets.items():
            vehicle_count = sum(item[0] for item in rows)
            halting_count = sum(item[1] for item in rows)
            speed_num = sum(item[2] * item[0] for item in rows)
            mean_speed = speed_num / vehicle_count if vehicle_count else 0.0
            occupancy_pct = sum(item[3] for item in rows) / len(rows)
            instant, _ = instant_congestion_level(
                vehicle_count=vehicle_count,
                halting_count=halting_count,
                mean_speed=mean_speed,
                occupancy_pct=occupancy_pct,
            )
            level = self._apply_level_hysteresis(edge_id, instant)
            edges[edge_id] = {
                "level": level,
                "score": CONGESTION_SCORE[level],
                "mean_speed": round(mean_speed, 3),
                "occupancy_pct": round(occupancy_pct, 4),
                # 兼容旧前端字段名：数值同为百分数
                "occupancy": round(occupancy_pct, 4),
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
        prediction_runtime: PredictionRuntime | None = None,
        prediction_model_dir: str | Path | None = None,
        stgcn_root: str | Path | None = None,
    ) -> None:
        self.sample_seconds = sample_seconds
        self.history_frames = history_frames
        self.horizon_seconds = horizon_seconds
        self.lane_lonlat = lane_lonlat or (lambda _lane_id: (None, None))
        self.intersection_lonlat = intersection_lonlat or (
            lambda _intersection_id: (None, None)
        )
        self.nodes, self.lane_to_node = _load_lane_to_node(tls_manifest_path)
        resolved_dir = prediction_model_dir
        if isinstance(resolved_dir, str) and resolved_dir.strip():
            candidate = Path(resolved_dir).expanduser()
            if not candidate.is_absolute():
                from backend.app.core.config import resolve_project_root

                candidate = resolve_project_root() / candidate
            resolved_dir = candidate
        self.prediction_runtime = prediction_runtime or PredictionRuntime.from_settings(
            model_dir=resolved_dir,
            stgcn_root=stgcn_root,
        )
        # 在线快照：红灯会打断连帧；占有率统一为百分数口径
        self.rule_config = RuleConfig(
            use_cusum=False,
            consecutive_points=2,
            min_occupancy=15.0,
            min_vehicle_count=3,
            min_halting_count=3,
            low_speed_mps=1.5,
            soft_closure_min_occupancy=2.0,
            soft_closure_max_occupancy=40.0,
            queue_blockage_min_vehicle_count=5,
            queue_blockage_min_halting_count=3,
            queue_blockage_max_mean_speed=8.0,
            queue_blockage_min_waiting_time=60.0,
            queue_blockage_min_waiting_delta=1.0,
            speed_restriction_min_occupancy=3.0,
            speed_restriction_max_occupancy=40.0,
            speed_restriction_max_mean_speed=3.0,
            speed_restriction_max_halting_count=2,
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
                return _empty_payload(horizon=self.horizon_seconds)
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
                    prediction_runtime=self.prediction_runtime,
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
