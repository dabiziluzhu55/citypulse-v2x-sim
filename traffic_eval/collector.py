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
- 路径速度/TTI/DTP/停车次数：终态TripInfo completed车辆；运行中可发布 snapshot provisional
- 区域最大排队长度 / 溢流率：Snapshot 进口车道queue_length_m与车道长度近似
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from simulation_protocol.dto import SimulationSnapshot

from .models import EvalResult
from .powertrain import VehicleTypeFuelMeta
from .scope import EvaluationScope, snapshot_evaluation_scope
from .tpi import tpi_from_optional_dtp
from .tripinfo import (
    FUEL_POWERTRAINS,
    TRAVEL_WAIT_SOURCE,
    apply_tripinfo_official_metrics,
)

SCENE_AFFECTED_TRIP_SOURCE_PREFIX = "scene_affected_trip_metrics:"
SCENE_IN_SCOPE_SOURCE = "scene_in_scope_snapshot"
HARD_BRAKING_RATE_SOURCE = "final_snapshot_hard_braking_events_per_100_departed"
QUEUE_SOURCE = "incoming_lane_halting_count"
REGIONAL_MAX_QUEUE_SOURCE = "simulation_snapshot_incoming_lane_queue_length_m"
SPILLBACK_SOURCE = "simulation_snapshot_incoming_lane_queue_vs_storage_time_weighted"
PROVISIONAL_DTP_SOURCE = "snapshot_provisional_timeLoss_over_duration"
PROVISIONAL_TPI_SOURCE = (
    "GB/T33171-2016_Annex_C_DTP_piecewise_linear_snapshot_provisional"
)
PROVISIONAL_PATH_SPEED_SOURCE = "snapshot_provisional_distance_over_duration"
PROVISIONAL_STOPS_SOURCE = "snapshot_provisional_stop_transitions"
OVERFLOW_EPS_M = 1e-9
HALTING_SPEED_MPS = 0.1

IncomingQueueRecord = tuple[str, str, Optional[float], Optional[float]]


class _CoreMetricsCollector:
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
                "speed": float(vehicle.speed),
                "time_loss": float(vehicle.time_loss),
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
        tripinfo_vehicle_ids: Sequence[str] | None = None,
        snapshot_finish: bool = False,
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
            snapshot_finish=snapshot_finish,
        )
        if snapshot_finish:
            self._warnings = list(result.warnings)
            return result
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
                expected_departed=result.departed if tripinfo_vehicle_ids is None else None,
                include_vtypes=include_vtypes,
                vehicle_ids=tripinfo_vehicle_ids,
            )
            if tripinfo_vehicle_ids is not None:
                _mark_scene_affected_trip_sources(result)
            self._tripinfo_applied = TRAVEL_WAIT_SOURCE in str(
                result.metric_sources.get("avg_travel_time_s") or ""
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
                first_speed = float(vehicles[vid].get("speed", 0.0))
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "type_id": str(vehicles[vid].get("type_id", "")),
                    "last_waiting": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                    "last_time_loss": float(vehicles[vid].get("time_loss", 0.0)),
                    "last_speed": first_speed,
                    "last_duration": 0.0,
                    "stop_count": 0,
                    "was_stopped": first_speed < HALTING_SPEED_MPS,
                }

        arrived_vids = set(self._active.keys()) - set(vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            travel = max(0.0, sim_time - float(rec["first_seen_s"]))
            rec["last_duration"] = travel
            self._provisional_travel.append(travel)
            self._provisional_waiting.append(float(rec["last_waiting"]))
            self._closed.append(rec)

        for vid, vdata in vehicles.items():
            if vid not in self._active:
                continue
            rec = self._active[vid]
            speed = float(vdata.get("speed", 0.0))
            stop_count = int(rec.get("stop_count", 0))
            if not bool(rec.get("was_stopped")) and speed < HALTING_SPEED_MPS:
                stop_count += 1
            rec.update(
                {
                    "type_id": str(vdata.get("type_id", rec["type_id"])),
                    "last_waiting": float(vdata.get("waiting", 0.0)),
                    "last_distance": float(vdata.get("distance", 0.0)),
                    "last_fuel_ml": float(vdata.get("fuel_ml", 0.0)),
                    "last_time_loss": float(vdata.get("time_loss", 0.0)),
                    "last_speed": speed,
                    "last_duration": max(0.0, sim_time - float(rec["first_seen_s"])),
                    "stop_count": stop_count,
                    "was_stopped": speed < HALTING_SPEED_MPS,
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

    def _provisional_duration_records(self, sim_time: float) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = [dict(record) for record in self._closed]
        for record in self._active.values():
            current = dict(record)
            current["last_duration"] = max(
                0.0, sim_time - float(record["first_seen_s"])
            )
            records.append(current)
        return records

    def _apply_provisional_path_metrics(
        self, result: EvalResult, sim_time: float
    ) -> None:
        records = self._provisional_duration_records(sim_time)
        duration_sum = 0.0
        time_loss_sum = 0.0
        distance_sum = 0.0
        for record in records:
            duration = float(record.get("last_duration", 0.0))
            time_loss = float(record.get("last_time_loss", 0.0))
            if duration <= 0 or time_loss < 0:
                continue
            duration_sum += duration
            time_loss_sum += time_loss
            distance_sum += max(0.0, float(record.get("last_distance", 0.0)))
        if duration_sum > 0:
            dtp = min(1.0, max(0.0, time_loss_sum / duration_sum))
            result.delay_time_proportion = dtp
            result.metric_sources["delay_time_proportion"] = PROVISIONAL_DTP_SOURCE
            tpi, state, method = tpi_from_optional_dtp(dtp)
            result.traffic_performance_index = tpi
            result.traffic_state = state
            result.tpi_method = method
            result.metric_sources["traffic_performance_index"] = PROVISIONAL_TPI_SOURCE
            result.path_avg_speed_kmh = (distance_sum / duration_sum) * 3.6
            result.metric_sources["path_avg_speed_kmh"] = PROVISIONAL_PATH_SPEED_SOURCE
        if records:
            result.avg_stops_per_vehicle = sum(
                int(record.get("stop_count", 0)) for record in records
            ) / float(len(records))
            result.metric_sources["avg_stops_per_vehicle"] = PROVISIONAL_STOPS_SOURCE

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
        snapshot_finish: bool = False,
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

        publish_snapshot_vehicle_metrics = (not use_finish) or snapshot_finish

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

        if publish_snapshot_vehicle_metrics:
            travel_sum = sum(self._provisional_travel)
            waiting_sum = sum(self._provisional_waiting)
            for rec in self._active.values():
                travel_sum += max(0.0, sim_time - float(rec["first_seen_s"]))
                waiting_sum += float(rec["last_waiting"])
            seen = len(self._provisional_waiting) + len(self._active)
            if departed > 0 and seen > 0:
                r.avg_travel_time_s = travel_sum / float(departed)
                r.avg_waiting_time_s = waiting_sum / float(departed)
                travel_source = (
                    SCENE_IN_SCOPE_SOURCE if snapshot_finish else "snapshot_provisional"
                )
                r.metric_sources["avg_travel_time_s"] = travel_source
                r.metric_sources["avg_waiting_time_s"] = travel_source
                if snapshot_finish:
                    warnings.append(
                        "平均行程时间/等待时间为场景内累计（车辆位于评价范围内时），"
                        "不是 TripInfo 整段行程"
                    )
                else:
                    warnings.append(
                        "平均行程时间/等待时间为快照临时值（含未到达车），"
                        "终态将等待TripInfo回填"
                    )
            fuel = self._provisional_fuel_metric()
            r.fuel_intensity_L_per_100km = fuel
            if fuel is not None:
                r.metric_sources["fuel_intensity_L_per_100km"] = (
                    SCENE_IN_SCOPE_SOURCE if snapshot_finish else "snapshot_provisional"
                )
                if snapshot_finish:
                    warnings.append(
                        "燃油强度为场景内累计，不是 TripInfo 整段行程 fuel_abs"
                    )
                else:
                    warnings.append(
                        "燃油强度为快照临时值，终态将由TripInfo正式回填"
                    )
            self._apply_provisional_path_metrics(r, sim_time)
        elif use_finish:
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


@dataclass
class _ScopedVehicleView:
    vehicle_id: str
    waiting_time: float
    distance: float
    fuel_total_ml: float
    type_id: str
    speed: float
    time_loss: float
    hard_braking_events: int = 0


class _SceneMembershipTracker:
    """跟踪车辆进入/离开 evaluation_scope，并累计场景内增量。"""

    def __init__(self) -> None:
        self.ever_entered: set[str] = set()
        self.ever_exited: set[str] = set()
        self.in_scope: set[str] = set()
        self.scene_hard_braking_events: int = 0
        self.last_scene_vehicles: tuple[Any, ...] = ()
        self.last_covers_all: bool = True
        self._last: dict[str, dict[str, float]] = {}
        self._accum: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.ever_entered.clear()
        self.ever_exited.clear()
        self.in_scope.clear()
        self.scene_hard_braking_events = 0
        self.last_scene_vehicles = ()
        self.last_covers_all = True
        self._last.clear()
        self._accum.clear()

    def update(
        self,
        snapshot: SimulationSnapshot,
        scope: EvaluationScope | None,
    ) -> tuple[tuple[Any, ...], int, int, int, bool]:
        covers_all = scope is None or scope.covers_full_network
        present_ids = {str(vehicle.vehicle_id) for vehicle in snapshot.vehicles}
        current_in_scope: set[str] = set()
        scene_vehicles: list[Any] = []

        for vehicle in snapshot.vehicles:
            vid = str(vehicle.vehicle_id)
            lane_id = str(getattr(vehicle, "lane_id", "") or "")
            road_id = str(getattr(vehicle, "road_id", "") or "")
            in_scope = covers_all or (
                scope is not None and scope.contains_vehicle(lane_id, road_id)
            )
            waiting = float(getattr(vehicle, "waiting_time", 0.0) or 0.0)
            distance = float(getattr(vehicle, "distance", 0.0) or 0.0)
            fuel = float(getattr(vehicle, "fuel_total_ml", 0.0) or 0.0)
            time_loss = float(getattr(vehicle, "time_loss", 0.0) or 0.0)
            braking = int(getattr(vehicle, "hard_braking_events", 0) or 0)
            last = self._last.get(vid)
            acc = self._accum.setdefault(
                vid,
                {
                    "waiting": 0.0,
                    "distance": 0.0,
                    "fuel": 0.0,
                    "time_loss": 0.0,
                    "braking": 0,
                    "type_id": str(getattr(vehicle, "type_id", "") or ""),
                },
            )
            acc["type_id"] = str(getattr(vehicle, "type_id", acc["type_id"]) or "")
            was_in = vid in self.in_scope
            if last is not None and in_scope and was_in:
                dw = max(0.0, waiting - last["waiting"])
                dd = max(0.0, distance - last["distance"])
                df = max(0.0, fuel - last["fuel"])
                dt = max(0.0, time_loss - last["time_loss"])
                db = max(0, braking - int(last["braking"]))
                acc["waiting"] += dw
                acc["distance"] += dd
                acc["fuel"] += df
                acc["time_loss"] += dt
                acc["braking"] += db
                self.scene_hard_braking_events += db
            self._last[vid] = {
                "waiting": waiting,
                "distance": distance,
                "fuel": fuel,
                "time_loss": time_loss,
                "braking": float(braking),
            }
            if not in_scope:
                if vid in self.in_scope:
                    self.ever_exited.add(vid)
                continue
            current_in_scope.add(vid)
            if vid not in self.ever_entered:
                self.ever_entered.add(vid)
            if covers_all:
                scene_vehicles.append(vehicle)
            else:
                scene_vehicles.append(
                    _ScopedVehicleView(
                        vehicle_id=vid,
                        waiting_time=float(acc["waiting"]),
                        distance=float(acc["distance"]),
                        fuel_total_ml=float(acc["fuel"]),
                        type_id=str(acc["type_id"]),
                        speed=float(getattr(vehicle, "speed", 0.0) or 0.0),
                        time_loss=float(acc["time_loss"]),
                        hard_braking_events=int(acc["braking"]),
                    )
                )

        for vid in self.in_scope - current_in_scope:
            self.ever_exited.add(vid)
        for vid in self.in_scope - present_ids:
            self.ever_exited.add(vid)
        self.in_scope = current_in_scope
        self.last_scene_vehicles = tuple(scene_vehicles)
        self.last_covers_all = covers_all
        return (
            tuple(scene_vehicles),
            len(self.ever_entered),
            len(self.ever_exited),
            int(self.scene_hard_braking_events),
            covers_all,
        )


class _ScopedSnapshot:
    def __init__(
        self,
        snapshot: SimulationSnapshot,
        *,
        vehicles: tuple[Any, ...],
        departed: int,
        arrived: int,
        hard_braking_events: int,
    ) -> None:
        self.session_id = snapshot.session_id
        self.elapsed_seconds = snapshot.elapsed_seconds
        self.vehicles = vehicles
        self.intersections = snapshot.intersections
        self.metrics = type(
            "_ScopedMetrics",
            (),
            {
                "departed_vehicles": int(departed),
                "arrived_vehicles": int(arrived),
                "hard_braking_events": int(hard_braking_events),
            },
        )()


class TrafficMetricsCollector:
    """对外采集器：同时维护场景口径与全网口径。"""

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm
        self._network = _CoreMetricsCollector(algorithm)
        self._scene = _CoreMetricsCollector(algorithm)
        self._tracker = _SceneMembershipTracker()
        self._scope: EvaluationScope | None = None
        self._last_scene: EvalResult | None = None
        self._last_network: EvalResult | None = None
        self._tripinfo_applied = False

    def reset(self, algorithm: str = "") -> None:
        if algorithm:
            self._algorithm = algorithm
        self._network.reset(algorithm=algorithm)
        self._scene.reset(algorithm=algorithm)
        self._tracker.reset()
        self._scope = None
        self._last_scene = None
        self._last_network = None
        self._tripinfo_applied = False

    def set_fuel_meta_by_type(
        self, mapping: Mapping[str, VehicleTypeFuelMeta]
    ) -> None:
        self._network.set_fuel_meta_by_type(mapping)
        self._scene.set_fuel_meta_by_type(mapping)

    def update_fuel_meta_by_type(
        self, mapping: Mapping[str, VehicleTypeFuelMeta]
    ) -> None:
        self._network.update_fuel_meta_by_type(mapping)
        self._scene.update_fuel_meta_by_type(mapping)

    def missing_fuel_meta_type_ids(self, type_ids: Iterable[str]) -> list[str]:
        return self._network.missing_fuel_meta_type_ids(type_ids)

    def set_powertrain_by_type(self, mapping: Mapping[str, str]) -> None:
        self._network.set_powertrain_by_type(mapping)
        self._scene.set_powertrain_by_type(mapping)

    def extend_warnings(self, messages: list[str]) -> None:
        self._network.extend_warnings(messages)
        self._scene.extend_warnings(messages)

    def observe_snapshot(self, snapshot: SimulationSnapshot) -> None:
        # Guard before mutating membership and accumulated per-vehicle counters.
        if self._network._sample_accepted and float(snapshot.elapsed_seconds) <= self._network._last_sim_time:
            if float(snapshot.elapsed_seconds) < self._network._last_sim_time:
                self.extend_warnings(["评价帧时间倒退，已忽略该帧"])
            return
        scope = snapshot_evaluation_scope(snapshot)
        if scope is not None:
            self._scope = scope
        self._network.observe_snapshot(snapshot)
        scene_vehicles, entered, exited, scene_braking, covers_all = self._tracker.update(
            snapshot, self._scope
        )
        if covers_all:
            self._scene.observe_snapshot(snapshot)
            return
        self._scene.observe_snapshot(
            _ScopedSnapshot(
                snapshot,
                vehicles=scene_vehicles,
                departed=entered,
                arrived=exited,
                hard_braking_events=scene_braking,
            )
        )

    def finalize_from_snapshot(
        self,
        snapshot: SimulationSnapshot,
        *,
        decision_latency_ms: Optional[float] = None,
        tripinfo_path: str | Path | None = None,
    ) -> EvalResult:
        self.observe_snapshot(snapshot)
        network = self._network.finalize_from_snapshot(
            snapshot,
            decision_latency_ms=decision_latency_ms,
            tripinfo_path=tripinfo_path,
        )
        scope = self._scope
        covers_all = scope is None or scope.covers_full_network
        if covers_all:
            scene = network
            self._tripinfo_applied = self._network._tripinfo_applied
            affected = _scene_affected_trip_payload(network, int(network.departed))
        else:
            scene = self._scene.finalize_from_snapshot(
                _ScopedSnapshot(
                    snapshot,
                    vehicles=self._tracker.last_scene_vehicles,
                    departed=len(self._tracker.ever_entered),
                    arrived=len(self._tracker.ever_exited),
                    hard_braking_events=int(self._tracker.scene_hard_braking_events),
                ),
                decision_latency_ms=decision_latency_ms,
                tripinfo_path=None,
                snapshot_finish=True,
            )
            self._tripinfo_applied = False
            affected = None
            if tripinfo_path is not None:
                affected_result = EvalResult(algorithm=self._algorithm)
                apply_tripinfo_official_metrics(
                    affected_result,
                    tripinfo_path,
                    self._network._fuel_meta_by_type,
                    vehicle_ids=tuple(sorted(self._tracker.ever_entered)),
                )
                _mark_scene_affected_trip_sources(affected_result)
                affected = _scene_affected_trip_payload(
                    affected_result, len(self._tracker.ever_entered)
                )
        return self._bind_results(scene, network, affected=affected)

    def result(
        self,
        *,
        finished: bool = False,
        decision_latency_ms: Optional[float] = None,
    ) -> EvalResult:
        network = self._network.result(
            finished=finished,
            decision_latency_ms=decision_latency_ms,
        )
        scope = self._scope
        covers_all = scope is None or scope.covers_full_network
        if covers_all:
            scene = network
        else:
            scene = self._scene.result(
                finished=finished,
                decision_latency_ms=decision_latency_ms,
                snapshot_finish=finished,
            )
        return self._bind_results(scene, network)

    def _bind_results(
        self,
        scene: EvalResult,
        network: EvalResult,
        *,
        affected: dict[str, Any] | None = None,
    ) -> EvalResult:
        scene.algorithm = self._algorithm or scene.algorithm
        network.algorithm = self._algorithm or network.algorithm
        if self._scope is not None:
            scene.evaluation_scope = self._scope.to_dict()
            network.evaluation_scope = self._scope.to_dict()
        covers_all = self._scope is None or self._scope.covers_full_network
        if covers_all:
            sample_sizes = {
                "scene_entered_vehicles": int(network.departed),
                "scene_exited_vehicles": int(network.arrived),
                "scene_active_vehicles": int(network.departed - network.arrived)
                if network.departed >= network.arrived
                else 0,
                "scene_affected_trip_vehicles": int(network.departed),
                "network_departed": int(network.departed),
                "network_arrived": int(network.arrived),
                "scene_departed": int(scene.departed),
                "scene_arrived": int(scene.arrived),
            }
        else:
            sample_sizes = {
                "scene_entered_vehicles": len(self._tracker.ever_entered),
                "scene_exited_vehicles": len(self._tracker.ever_exited),
                "scene_active_vehicles": len(self._tracker.in_scope),
                "scene_affected_trip_vehicles": len(self._tracker.ever_entered),
                "network_departed": int(network.departed),
                "network_arrived": int(network.arrived),
                "scene_departed": int(scene.departed),
                "scene_arrived": int(scene.arrived),
            }
        if self._scope is not None:
            sample_sizes["intersection_count"] = len(self._scope.intersection_ids)
        scene.sample_sizes = dict(sample_sizes)
        network.sample_sizes = dict(sample_sizes)
        scene_payload = _flat_frontend_metrics(scene)
        network_payload = (
            scene_payload if scene is network else _flat_frontend_metrics(network)
        )
        scene.scene_metrics = dict(scene_payload)
        scene.network_metrics = dict(network_payload)
        if scene is not network:
            network.scene_metrics = dict(scene_payload)
            network.network_metrics = dict(network_payload)
        if affected is None:
            scene.scene_affected_trip_metrics = None
            if scene is not network:
                network.scene_affected_trip_metrics = None
        else:
            scene.scene_affected_trip_metrics = affected
            if scene is not network:
                network.scene_affected_trip_metrics = affected
        self._last_scene = scene
        self._last_network = network
        return scene

    def _observe(self, *args: Any, **kwargs: Any) -> None:
        self._network._observe(*args, **kwargs)
        self._scene._observe(*args, **kwargs)

    @property
    def _total_arrived(self) -> int:
        return int(self._network._total_arrived)

    @_total_arrived.setter
    def _total_arrived(self, value: int) -> None:
        self._network._total_arrived = int(value)
        self._scene._total_arrived = int(value)

    @property
    def _total_departed(self) -> int:
        return int(self._network._total_departed)

    @_total_departed.setter
    def _total_departed(self, value: int) -> None:
        self._network._total_departed = int(value)
        self._scene._total_departed = int(value)

    @property
    def _final_sim_time(self) -> float:
        return float(self._network._final_sim_time)

    @_final_sim_time.setter
    def _final_sim_time(self, value: float) -> None:
        self._network._final_sim_time = float(value)
        self._scene._final_sim_time = float(value)

    @property
    def _finished(self) -> bool:
        return self._network.finished or self._scene.finished

    @_finished.setter
    def _finished(self, value: bool) -> None:
        self._network._finished = bool(value)
        self._scene._finished = bool(value)

    @property
    def finished(self) -> bool:
        return self._network.finished or self._scene.finished


def _flat_frontend_metrics(result: EvalResult) -> dict[str, Any]:
    nested_scene = result.scene_metrics
    nested_network = result.network_metrics
    nested_affected = result.scene_affected_trip_metrics
    result.scene_metrics = None
    result.network_metrics = None
    result.scene_affected_trip_metrics = None
    try:
        payload = result.to_frontend_metrics()
    finally:
        result.scene_metrics = nested_scene
        result.network_metrics = nested_network
        result.scene_affected_trip_metrics = nested_affected
    for key in (
        "scene_metrics",
        "network_metrics",
        "scene_affected_trip_metrics",
    ):
        payload.pop(key, None)
    return payload


def _mark_scene_affected_trip_sources(result: EvalResult) -> None:
    keys = {
        "path_avg_speed_kmh",
        "travel_time_index",
        "delay_time_proportion",
        "traffic_performance_index",
        "avg_stops_per_vehicle",
        "avg_travel_time_s",
        "avg_waiting_time_s",
        "fuel_intensity_L_per_100km",
    }
    for key in keys:
        value = result.metric_sources.get(key)
        if not value:
            continue
        text = str(value)
        if not text.startswith(SCENE_AFFECTED_TRIP_SOURCE_PREFIX):
            result.metric_sources[key] = SCENE_AFFECTED_TRIP_SOURCE_PREFIX + text


def _scene_affected_trip_payload(
    result: EvalResult, sample_vehicle_count: int
) -> dict[str, Any]:
    payload = {
        "metric_kind": "scene_affected_trip_metrics",
        "note": (
            "TripInfo 记录整段行程（duration/routeLength/timeLoss/fuel），"
            "不是纯场景内累计"
        ),
        "sample_vehicle_count": int(sample_vehicle_count),
        "path_avg_speed_kmh": result.path_avg_speed_kmh,
        "travel_time_index": result.travel_time_index,
        "delay_time_proportion": result.delay_time_proportion,
        "traffic_performance_index": result.traffic_performance_index,
        "traffic_state": result.traffic_state,
        "avg_stops_per_vehicle": result.avg_stops_per_vehicle,
        "avg_travel_time": result.avg_travel_time_s,
        "avg_waiting_time": result.avg_waiting_time_s,
        "fuel_intensity_L_per_100km": result.fuel_intensity_L_per_100km,
        "metric_sources": {
            key: value
            for key, value in result.metric_sources.items()
            if key in {
                "path_avg_speed_kmh",
                "travel_time_index",
                "delay_time_proportion",
                "traffic_performance_index",
                "avg_stops_per_vehicle",
                "avg_travel_time_s",
                "avg_waiting_time_s",
                "fuel_intensity_L_per_100km",
            }
        },
    }
    return payload


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
