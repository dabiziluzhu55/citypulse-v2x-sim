"""公共交通指标采集器：数据源为SimulationManager推送的SimulationSnapshot

本模块属于traffic_eval评估模块（Backend与algoritms共用）

指标口径：
- 行程/等待：全部已出发车辆 duration/waitingTime 总和 ÷ departed；
  终态由TripInfo回填；进行中可为快照临时值（含仍在路上的车）
- 进口车道平均排队车辆数：仅 role==incoming，每帧先求车道 halting_count 均值，
  再按仿真时间 Δt 右端点加权（时刻 t_k 的观测代表 (t_{k-1}, t_k]，t_{-1}=0）
- 网络实际吞吐流率：arrived/evaluation_duration_seconds*3600（单位时间到达量）
- 决策延迟：由外部注入；无样本为None
- 燃油强度：终态解析TripInfo emissions；运行中可用快照临时值
- 急刹车：读取终态快照累计hard_braking_events
- 路径速度/TTI/DTP/停车次数：终态TripInfo completed车辆
- 区域最大排队长度 / 溢流率：Snapshot 进口车道queue_length_m与车道长度近似
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from simulation.sumo.engine.session import SimulationSnapshot

from .models import EvalResult
from .powertrain import VehicleTypeFuelMeta
from .tripinfo import (
    FUEL_POWERTRAINS,
    TRAVEL_WAIT_SOURCE,
    apply_tripinfo_official_metrics,
)

HARD_BRAKING_RATE_SOURCE = "final_snapshot_hard_braking_events_per_100_departed"
QUEUE_SOURCE = "incoming_lane_halting_count"
REGIONAL_MAX_QUEUE_SOURCE = "simulation_snapshot_incoming_lane_queue_length_m"
SPILLBACK_SOURCE = "simulation_snapshot_incoming_lane_queue_vs_storage_time_weighted"
OVERFLOW_EPS_M = 1e-9

IncomingQueueRecord = tuple[str, str, Optional[float], Optional[float]]


class TrafficMetricsCollector:
    """按session生命周期采集交通运行指标"""

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm
        self._active: dict[str, dict[str, Any]] = {}
        self._closed: list[dict[str, Any]] = []
        self._provisional_travel: list[float] = []
        self._provisional_waiting: list[float] = []
        self._queue_weighted_sum: float = 0.0
        self._queue_weighted_time: float = 0.0
        self._spill_overflow_lane_s: float = 0.0
        self._spill_exposed_lane_s: float = 0.0
        self._max_queue_m: Optional[float] = None
        self._max_queue_intersection_id: Optional[str] = None
        self._max_queue_lane_id: Optional[str] = None
        self._max_queue_sim_time_s: Optional[float] = None
        self._fuel_meta_by_type: dict[str, VehicleTypeFuelMeta] = {}
        self._seen_vehicle_ids: set[str] = set()
        self._warnings: list[str] = []
        self._total_departed: int = 0
        self._total_arrived: int = 0
        self._final_sim_time: float = 0.0
        self._last_sim_time: float = 0.0
        self._sample_accepted: bool = False
        self._finished: bool = False
        self._tripinfo_applied: bool = False
        self._has_incoming_lanes: bool = False
        self._missing_incoming_warned: bool = False
        self._missing_queue_length_warned: bool = False
        self._hard_braking_events: Optional[int] = None

    def reset(self, algorithm: str = "") -> None:
        if algorithm:
            self._algorithm = algorithm
        self._active.clear()
        self._closed.clear()
        self._provisional_travel.clear()
        self._provisional_waiting.clear()
        self._queue_weighted_sum = 0.0
        self._queue_weighted_time = 0.0
        self._spill_overflow_lane_s = 0.0
        self._spill_exposed_lane_s = 0.0
        self._max_queue_m = None
        self._max_queue_intersection_id = None
        self._max_queue_lane_id = None
        self._max_queue_sim_time_s = None
        self._fuel_meta_by_type.clear()
        self._seen_vehicle_ids.clear()
        self._warnings.clear()
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._last_sim_time = 0.0
        self._sample_accepted = False
        self._finished = False
        self._tripinfo_applied = False
        self._has_incoming_lanes = False
        self._missing_incoming_warned = False
        self._missing_queue_length_warned = False
        self._hard_braking_events = None

    def set_fuel_meta_by_type(
        self, mapping: Mapping[str, VehicleTypeFuelMeta]
    ) -> None:
        self._fuel_meta_by_type = {
            str(type_id): meta for type_id, meta in mapping.items()
        }
        if not self._fuel_meta_by_type:
            self._warn("初始化数据缺少车辆燃油元数据，燃油强度不可计算")

    def update_fuel_meta_by_type(
        self, mapping: Mapping[str, VehicleTypeFuelMeta]
    ) -> None:
        """合并新增/更新的车型燃油元数据，不覆盖已有映射中未出现的类型"""

        for type_id, meta in mapping.items():
            key = str(type_id)
            if not key:
                continue
            self._fuel_meta_by_type[key] = meta

    def missing_fuel_meta_type_ids(self, type_ids: Iterable[str]) -> list[str]:
        missing: list[str] = []
        seen: set[str] = set()
        for type_id in type_ids:
            key = str(type_id or "")
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in self._fuel_meta_by_type:
                missing.append(key)
        return missing

    def set_powertrain_by_type(self, mapping: Mapping[str, str]) -> None:

        self.set_fuel_meta_by_type(
            {
                str(type_id): VehicleTypeFuelMeta(
                    powertrain=str(powertrain).lower(),
                    fuel_density_mg_per_ml=0.0,
                )
                for type_id, powertrain in mapping.items()
            }
        )

    def extend_warnings(self, messages: list[str]) -> None:
        for message in messages:
            self._warn(message)

    def observe_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """从统一Snapshot取数据"""
        if self._finished:
            return
        sim_time = float(snapshot.elapsed_seconds)
        vehicles: dict[str, Mapping[str, Any]] = {}
        for vehicle in snapshot.vehicles:
            vehicles[vehicle.vehicle_id] = {
                "waiting": float(vehicle.waiting_time),
                "distance": float(vehicle.distance),
                "fuel_ml": float(vehicle.fuel_total_ml),
                "type_id": str(vehicle.type_id or ""),
            }
        incoming_halting: list[float] = []
        incoming_queues: list[IncomingQueueRecord] = []
        saw_any_lane = False
        for intersection_id, i_state in snapshot.intersections.items():
            for lane_id, lane in i_state.lanes.items():
                saw_any_lane = True
                if str(lane.role) != "incoming":
                    continue
                incoming_halting.append(float(lane.halting_count))
                incoming_queues.append(
                    (
                        str(intersection_id),
                        str(lane_id),
                        _optional_finite_float(getattr(lane, "queue_length_m", None)),
                        _optional_finite_float(getattr(lane, "lane_length_m", None)),
                    )
                )
        if incoming_halting:
            self._has_incoming_lanes = True
        elif saw_any_lane and not self._missing_incoming_warned:
            self._warn("没有进口车道，平均排队长度不可计")
            self._missing_incoming_warned = True

        self._observe(
            sim_time=sim_time,
            vehicles=vehicles,
            incoming_halting=incoming_halting,
            incoming_queues=incoming_queues,
        )
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)
        self._track_hard_braking(int(snapshot.metrics.hard_braking_events))

    def finalize_from_snapshot(
        self,
        snapshot: SimulationSnapshot,
        *,
        decision_latency_ms: Optional[float] = None,
        tripinfo_path: str | Path | None = None,
    ) -> EvalResult:
        """会话结束时结算最终交通指标，并可选TripInfo回填"""
        self.observe_snapshot(snapshot)
        self._finished = True
        self._final_sim_time = float(snapshot.elapsed_seconds)
        self._last_sim_time = self._final_sim_time
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)
        self._track_hard_braking(int(snapshot.metrics.hard_braking_events))
        result = self.result(
            finished=True,
            decision_latency_ms=decision_latency_ms,
        )
        if tripinfo_path is not None:
            include_vtypes = (
                list(self._fuel_meta_by_type.keys())
                if self._fuel_meta_by_type
                else None
            )
            apply_tripinfo_official_metrics(
                result,
                tripinfo_path,
                self._fuel_meta_by_type,
                expected_departed=result.departed,
                include_vtypes=include_vtypes,
            )
            self._tripinfo_applied = (
                result.metric_sources.get("avg_travel_time_s") == TRAVEL_WAIT_SOURCE
            )
            self._warnings = list(result.warnings)
        else:
            result.fuel_intensity_L_per_100km = None
            result.metric_sources.pop("fuel_intensity_L_per_100km", None)
            if "缺少TripInfo" not in result.warnings:
                result.warnings.append("缺少TripInfo")
            self._warnings = list(result.warnings)
        return result

    def _warn(self, message: str) -> None:
        if message and message not in self._warnings:
            self._warnings.append(message)

    def _track_hard_braking(self, events: int) -> None:
        """急刹车为单调累计值：取历史最大值，禁止多帧相加"""

        value = max(0, int(events))
        if self._hard_braking_events is None:
            self._hard_braking_events = value
        else:
            self._hard_braking_events = max(self._hard_braking_events, value)

    def _observe(
        self,
        *,
        sim_time: float,
        vehicles: Mapping[str, Mapping[str, Any]],
        incoming_halting: list[float],
        incoming_queues: Sequence[IncomingQueueRecord] = (),
    ) -> None:
        if self._sample_accepted and sim_time < self._last_sim_time:
            self._warn("评价帧时间倒退，已忽略该帧")
            return
        if self._sample_accepted and sim_time == self._last_sim_time:
            # 与算法侧一致：同一仿真时刻不重复采样
            return

        dt = 0.0
        if self._sample_accepted:
            dt = sim_time - self._last_sim_time
        elif sim_time > 0:
            dt = sim_time

        if incoming_halting:
            self._has_incoming_lanes = True
        if dt > 0 and incoming_halting:
            mean_halting = sum(incoming_halting) / len(incoming_halting)
            self._queue_weighted_sum += mean_halting * dt
            self._queue_weighted_time += dt
        if dt > 0:
            n_valid = 0
            n_overflow = 0
            for _, _, queue_m, length_m in incoming_queues:
                if queue_m is None or length_m is None or length_m <= 0:
                    continue
                n_valid += 1
                if queue_m + OVERFLOW_EPS_M >= length_m:
                    n_overflow += 1
            if n_valid > 0:
                self._spill_overflow_lane_s += n_overflow * dt
                self._spill_exposed_lane_s += n_valid * dt

        for intersection_id, lane_id, queue_m, _length_m in incoming_queues:
            if queue_m is None:
                continue
            if self._max_queue_m is None or queue_m > self._max_queue_m:
                self._max_queue_m = queue_m
                self._max_queue_intersection_id = intersection_id
                self._max_queue_lane_id = lane_id
                self._max_queue_sim_time_s = sim_time

        if (
            incoming_queues
            and all(item[2] is None for item in incoming_queues)
            and not self._missing_queue_length_warned
        ):
            self._warn(
                "进口车道缺少 queue_length_m，区域最大排队长度和溢流率不可计算"
            )
            self._missing_queue_length_warned = True

        self._last_sim_time = sim_time
        self._sample_accepted = True

        for vid in vehicles:
            if vid not in self._active:
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "type_id": str(vehicles[vid].get("type_id", "")),
                    "last_waiting": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                }

        arrived_vids = set(self._active.keys()) - set(vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            travel = max(0.0, sim_time - float(rec["first_seen_s"]))
            self._provisional_travel.append(travel)
            self._provisional_waiting.append(float(rec["last_waiting"]))
            self._closed.append(rec)

        for vid, vdata in vehicles.items():
            if vid not in self._active:
                continue
            self._active[vid].update(
                {
                    "type_id": str(vdata.get("type_id", self._active[vid]["type_id"])),
                    "last_waiting": float(vdata.get("waiting", 0.0)),
                    "last_distance": float(vdata.get("distance", 0.0)),
                    "last_fuel_ml": float(vdata.get("fuel_ml", 0.0)),
                }
            )
            self._seen_vehicle_ids.add(vid)

    def _provisional_fuel_metric(self) -> Optional[float]:
        """运行中临时燃油强度：仅用快照采样的同批燃油车辆汇总，不作终态正式结果"""

        if not self._fuel_meta_by_type:
            self._warn("缺少车辆powertrain元数据，燃油强度不可计算")
            return None

        records = self._closed + list(self._active.values())
        fuel_records: list[dict[str, Any]] = []
        for record in records:
            type_id = str(record.get("type_id", "") or "")
            meta = self._fuel_meta_by_type.get(type_id)
            if meta is None:
                if type_id:
                    self._warn(
                        f"车辆类型 {type_id!r} 缺少powertrain"
                    )
                else:
                    self._warn("存在空车辆类型")
                continue
            if meta.powertrain in FUEL_POWERTRAINS:
                fuel_records.append(record)

        if not fuel_records:
            self._warn("没有可用的燃油车辆数据，燃油强度记为不可用")
            return None
        total_distance_m = sum(float(r["last_distance"]) for r in fuel_records)
        total_fuel_ml = sum(float(r["last_fuel_ml"]) for r in fuel_records)
        if total_distance_m <= 0:
            self._warn("没有可用的燃油车辆行驶里程，燃油强度记为不可用")
            return None
        return (total_fuel_ml / 1000.0) / (total_distance_m / 100000.0)

    def _hard_braking_metrics(
        self, *, departed: int, use_finish: bool
    ) -> tuple[Optional[int], Optional[float], Optional[str]]:
        if self._hard_braking_events is None:
            if use_finish:
                return None, None, "缺少急刹车累计数据，急刹车率不可用"
            return None, None, None

        events = int(self._hard_braking_events)
        if departed <= 0:
            warning = "出发车辆数为 0，急刹车率不可用" if use_finish else None
            return events, None, warning
        rate = events / float(departed) * 100.0
        return events, rate, None

    def result(
        self,
        *,
        finished: bool = False,
        decision_latency_ms: Optional[float] = None,
    ) -> EvalResult:
        r = EvalResult(algorithm=self._algorithm)
        use_finish = finished or self._finished
        warnings = list(self._warnings)

        arrived = self._total_arrived
        departed = self._total_departed
        if not use_finish:
            if arrived <= 0:
                arrived = len(self._provisional_waiting)
            if departed <= 0:
                departed = arrived + len(self._active)

        sim_time = (
            self._final_sim_time
            if use_finish and self._final_sim_time > 0
            else self._last_sim_time
        )

        r.departed = departed
        r.arrived = arrived
        if departed > 0:
            r.completion_rate = arrived / departed
        else:
            r.completion_rate = None

        if decision_latency_ms is not None:
            r.avg_decision_latency_ms = float(decision_latency_ms)
            r.metric_sources["avg_decision_latency_ms"] = "algorithm_perf_counter"
        else:
            r.avg_decision_latency_ms = None
            if use_finish:
                warnings.append("缺少算法决策耗时样本，平均决策延迟不可用")

        if self._queue_weighted_time > 0 and self._has_incoming_lanes:
            r.avg_queue_length_veh = (
                self._queue_weighted_sum / self._queue_weighted_time
            )
            r.metric_sources["avg_queue_length_veh"] = QUEUE_SOURCE
        elif use_finish:
            warnings.append("缺少进口车道排队样本，平均排队长度不可用")

        if self._max_queue_m is not None:
            r.regional_max_queue_length_m = self._max_queue_m
            r.regional_max_queue_intersection_id = self._max_queue_intersection_id
            r.regional_max_queue_lane_id = self._max_queue_lane_id
            r.regional_max_queue_sim_time_s = self._max_queue_sim_time_s
            r.metric_sources["regional_max_queue_length_m"] = REGIONAL_MAX_QUEUE_SOURCE
        elif use_finish and self._has_incoming_lanes:
            warnings.append("缺少进口车道 queue_length_m，区域最大排队长度不可用")

        if self._spill_exposed_lane_s > 0:
            r.spillback_rate = (
                self._spill_overflow_lane_s / self._spill_exposed_lane_s * 100.0
            )
            r.metric_sources["spillback_rate"] = SPILLBACK_SOURCE
        elif use_finish and self._has_incoming_lanes:
            warnings.append(
                "缺少进口车道排队长度与储车长度样本，溢流率不可用"
            )

        if sim_time > 0:
            r.throughput_veh_per_h = arrived / sim_time * 3600.0
            r.metric_sources["throughput_veh_per_h"] = (
                "finish_totals" if use_finish else "snapshot_running_totals"
            )
        elif use_finish:
            warnings.append("缺少有效评估时长，网络实际吞吐流率不可用")

        if not use_finish:
            travel_sum = sum(self._provisional_travel)
            waiting_sum = sum(self._provisional_waiting)
            for rec in self._active.values():
                travel_sum += max(0.0, sim_time - float(rec["first_seen_s"]))
                waiting_sum += float(rec["last_waiting"])
            seen = len(self._provisional_waiting) + len(self._active)
            if departed > 0 and seen > 0:
                r.avg_travel_time_s = travel_sum / float(departed)
                r.avg_waiting_time_s = waiting_sum / float(departed)
                r.metric_sources["avg_travel_time_s"] = "snapshot_provisional"
                r.metric_sources["avg_waiting_time_s"] = "snapshot_provisional"
                warnings.append(
                    "平均行程时间/等待时间为快照临时值（含未到达车），"
                    "终态将等待TripInfo回填"
                )
            fuel = self._provisional_fuel_metric()
            r.fuel_intensity_L_per_100km = fuel
            if fuel is not None:
                r.metric_sources[
                    "fuel_intensity_L_per_100km"
                ] = "snapshot_provisional"
                warnings.append(
                    "燃油强度为快照临时值，终态将由TripInfo正式回填"
                )
        else:
            # 终态默认不发布快照近似值；由TripInfo回填覆盖
            r.avg_travel_time_s = None
            r.avg_waiting_time_s = None
            r.fuel_intensity_L_per_100km = None
            if not self._tripinfo_applied:
                warnings.append(
                    "终态平均行程时间和等待时间等待TripInfo回填"
                )

        events, rate, braking_warning = self._hard_braking_metrics(
            departed=departed, use_finish=use_finish
        )
        r.hard_braking_events = events
        r.hard_braking_rate = rate
        if rate is not None:
            r.metric_sources["hard_braking_rate"] = HARD_BRAKING_RATE_SOURCE
        if braking_warning and braking_warning not in warnings:
            warnings.append(braking_warning)

        # _provisional_fuel_metric 可能追加 warning
        for message in self._warnings:
            if message not in warnings:
                warnings.append(message)
        r.warnings = warnings
        return r

    @property
    def finished(self) -> bool:
        return self._finished


# 兼容旧导入名
MetricsCollector = TrafficMetricsCollector


def _optional_finite_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number
