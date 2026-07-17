"""
HTTP 指标采集器 —— 从 v2.0 /step + /finish 数据直接计算 6 大指标。

不依赖 tripinfo.xml，在算法服务内部实时跟踪车辆状态，
/finish 时一次性出结果。

原理：
  - 每个 /step 采样排队长度，跟踪路网上所有车辆的抵达/消失
  - 车辆从 vehicles 中消失 = 到达，结算其等待时间/油耗/里程
  - /finish 时汇总出 6 大指标
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvalResult:
    """单次仿真的指标。"""
    algorithm: str = ""

    avg_travel_time_s: float = 0.0            # 1. 平均行程时间
    avg_waiting_time_s: float = 0.0           # 2. 平均等待时间
    avg_queue_length_veh: float = 0.0         # 3. 平均排队长度
    throughput_veh_per_h: float = 0.0         # 4. 有效吞吐量
    avg_decision_latency_ms: float = 0.0      # 5. 平均决策耗时（纯计算）
    fuel_intensity_L_per_100km: float = 0.0   # 6. 单位车公里燃油消耗

    departed: int = 0
    arrived: int = 0

    def to_dict(self) -> Dict[str, float]:
        return {
            "avg_travel_time_s": round(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": round(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": round(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": round(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": round(self.avg_decision_latency_ms, 3),
            "fuel_intensity_L_per_100km": round(self.fuel_intensity_L_per_100km, 2),
            "departed": self.departed,
            "arrived": self.arrived,
        }


class HttpMetricsCollector:
    """在每个 /step 和 /finish 调用时收集数据，最后产出 EvalResult。

    用法：
        collector = HttpMetricsCollector()
        collector.on_initialize(init_data)
        for each /step:
            actions = controller.compute_actions(step_data)
            collector.on_step(step_data)
            return actions
        collector.on_finish(finish_data)
        result = collector.result()
    """

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm

        # 活跃车辆: vid → {first_seen_s, last_waiting_s, last_time_loss_s,
        #                    last_distance_m, last_fuel_ml}
        self._active: Dict[str, Dict[str, float]] = {}

        # 已到达车辆的最终快照
        self._arrived_waiting: List[float] = []     # 每车累计等待
        self._arrived_travel: List[float] = []      # 每车行程时间（近似）
        self._arrived_time_loss: List[float] = []   # 每车时间损失
        self._arrived_distance: List[float] = []    # 每车行驶距离(m)
        self._arrived_fuel_ml: List[float] = []     # 每车燃油(mL)

        # 排队长度采样
        self._queue_samples: List[float] = []
        # 决策耗时采样（ms）
        self._latency_samples: List[float] = []

        # /finish 汇总
        self._total_departed: int = 0
        self._total_arrived: int = 0
        self._final_sim_time: float = 0.0
        self._final_fuel_ml: float = 0.0

    # ------------------------------------------------------------------
    # 公共接口（由 server.py 调用）
    # ------------------------------------------------------------------

    def on_initialize(self, _body: Dict[str, Any]) -> None:
        """重置所有状态。"""
        self._active.clear()
        self._arrived_waiting.clear()
        self._arrived_travel.clear()
        self._arrived_time_loss.clear()
        self._arrived_distance.clear()
        self._arrived_fuel_ml.clear()
        self._queue_samples.clear()
        self._latency_samples.clear()
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._final_fuel_ml = 0.0

    def record_latency(self, ms: float) -> None:
        """记录本步的纯算法计算耗时（毫秒）。由 server.py 在 compute_actions 前后调用。"""
        self._latency_samples.append(ms)

    def on_step(self, body: Dict[str, Any]) -> None:
        """记录本步的车辆状态和排队数据。"""
        sim_time = float(body.get("simulation_time", 0.0))
        current_vehicles: Dict[str, Any] = body.get("vehicles", {})

        # ── 1. 采样排队长度 ──
        for iid, i_state in body.get("intersections", {}).items():
            for lane_id, lane_data in i_state.get("lanes", {}).items():
                self._queue_samples.append(
                    float(lane_data.get("halting_count", 0))
                )

        # ── 2. 检测新车辆（首次出现） ──
        for vid in current_vehicles:
            if vid not in self._active:
                # 首次出现 — 近似 depart_time = 当前 sim_time
                # 无法拿到真实 depart_time，用首次出现时间近似
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "last_waiting": 0.0,
                    "last_time_loss": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                }

        # ── 3. 检测到达车辆（消失 = 已到达） ──
        arrived_vids = set(self._active.keys()) - set(current_vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            # 行程时间近似 = 消失时间 − 首次出现时间
            travel = sim_time - rec["first_seen_s"]
            if travel > 0:
                self._arrived_travel.append(travel)
            self._arrived_waiting.append(rec["last_waiting"])
            self._arrived_time_loss.append(rec["last_time_loss"])
            self._arrived_distance.append(rec["last_distance"])
            self._arrived_fuel_ml.append(rec["last_fuel_ml"])

        # ── 4. 更新活跃车辆的最新数据 ──
        for vid, vdata in current_vehicles.items():
            if vid in self._active:
                traffic = vdata.get("traffic", {})
                energy = vdata.get("energy", {})
                self._active[vid].update({
                    "last_waiting": float(traffic.get("accumulated_waiting_time_s", 0)),
                    "last_time_loss": float(traffic.get("time_loss_s", 0)),
                    "last_distance": float(traffic.get("distance_m", 0)),
                    "last_fuel_ml": float(energy.get("fuel_total_ml", 0)),
                })

    def on_finish(self, body: Dict[str, Any]) -> None:
        """记录 /finish 的汇总数据。"""
        self._total_departed = int(body.get("departed_vehicles", 0))
        self._total_arrived = int(body.get("arrived_vehicles", 0))
        self._final_sim_time = float(body.get("simulation_time", 0.0))
        self._final_fuel_ml = float(body.get("fuel_consumed_ml", 0.0))

    def result(self) -> EvalResult:
        """计算最终 6 大指标。"""
        r = EvalResult(algorithm=self._algorithm)
        arrived = self._total_arrived
        if arrived <= 0:
            arrived = len(self._arrived_waiting)
        departed = self._total_departed
        sim_time = self._final_sim_time

        r.departed = departed
        r.arrived = arrived

        # 1. 平均行程时间（近似）
        if self._arrived_travel:
            r.avg_travel_time_s = sum(self._arrived_travel) / len(self._arrived_travel)

        # 2. 平均等待时间
        if self._arrived_waiting:
            r.avg_waiting_time_s = sum(self._arrived_waiting) / len(self._arrived_waiting)

        # 3. 平均排队长度
        if self._queue_samples:
            r.avg_queue_length_veh = sum(self._queue_samples) / len(self._queue_samples)

        # 4. 吞吐量
        if sim_time > 0 and arrived > 0:
            r.throughput_veh_per_h = arrived / sim_time * 3600.0

        # 5. 平均决策耗时
        if self._latency_samples:
            r.avg_decision_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

        # 6. 单位车公里油耗
        total_distance_m = sum(self._arrived_distance)
        total_fuel = self._final_fuel_ml if self._final_fuel_ml > 0 else sum(self._arrived_fuel_ml)
        if total_distance_m > 0 and total_fuel > 0:
            fuel_L = total_fuel / 1000.0
            dist_100km = total_distance_m / 100000.0
            r.fuel_intensity_L_per_100km = fuel_L / dist_100km if dist_100km > 0 else 0.0

        return r
