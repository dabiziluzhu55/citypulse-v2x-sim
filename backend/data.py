"""Static mock fixtures aligned with docs/backend_mock_spec.md."""

from __future__ import annotations

SCENARIO_TEMPLATES = {
    "templates": [
        {
            "template_id": "xiongan20",
            "name": "雄安窄路密网20路口",
            "intersection_count": 20,
            "description": "窄路密网典型通勤场景",
            "map_center": [115.9348, 39.0631],
            "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
            "default_zoom": 15,
        },
        {
            "template_id": "corridor4",
            "name": "4路口走廊控制",
            "intersection_count": 4,
            "description": "走廊协调控制实验场景",
            "map_center": [115.9348, 39.0631],
            "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
            "default_zoom": 15,
        },
        {
            "template_id": "school",
            "name": "学校周边人车混行",
            "intersection_count": 8,
            "description": "学校片区上下学高峰，行人混行与接送车辆交织。",
            "map_center": [115.9385, 39.0602],
            "map_bounds": [115.936, 39.058, 115.941, 39.062],
            "default_zoom": 16,
        },
        {
            "template_id": "event",
            "name": "大型活动散场疏散",
            "intersection_count": 12,
            "description": "活动场馆散场流量突增，重点观察排队溢出与走廊优先策略。",
            "map_center": [115.9325, 39.0662],
            "map_bounds": [115.929, 39.064, 115.936, 39.068],
            "default_zoom": 15,
        },
    ]
}

TEMPLATE_MAP_META = {
    "xiongan20": {
        "template_id": "xiongan20",
        "map_center": [115.9348, 39.0631],
        "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
        "default_zoom": 15,
    },
    "corridor4": {
        "template_id": "corridor4",
        "map_center": [115.9348, 39.0631],
        "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
        "default_zoom": 15,
    },
}

ALGORITHMS = {
    "algorithms": [
        {
            "algorithm_id": "fixed_time",
            "name": "固定配时",
            "type": "baseline",
            "description": "SUMO 默认固定配时方案",
        },
        {
            "algorithm_id": "actuated",
            "name": "感应控制",
            "type": "rule_based",
            "description": "基于检测器感应控制",
        },
        {
            "algorithm_id": "max_pressure",
            "name": "Max-Pressure",
            "type": "rule_based",
            "description": "压力最大化自适应控制",
        },
        {
            "algorithm_id": "ippo",
            "name": "多路口 IPPO",
            "type": "reinforcement_learning",
            "description": "多路口强化学习协同控制",
        },
    ]
}

COMPARISON_BASELINES = ["fixed_time", "actuated", "max_pressure", "ippo"]

COMPARISON_RESULTS = {
    "fixed_time": {
        "algorithm": "fixed_time",
        "avg_waiting_time": 60.2,
        "avg_travel_time": 480.0,
        "avg_queue_length": 25.4,
        "throughput": 1200,
        "fuel_consumption": 100.0,
    },
    "actuated": {
        "algorithm": "actuated",
        "avg_waiting_time": 52.1,
        "avg_travel_time": 445.0,
        "avg_queue_length": 22.1,
        "throughput": 1280,
        "fuel_consumption": 96.5,
    },
    "max_pressure": {
        "algorithm": "max_pressure",
        "avg_waiting_time": 48.3,
        "avg_travel_time": 428.0,
        "avg_queue_length": 20.2,
        "throughput": 1310,
        "fuel_consumption": 94.2,
    },
    "ippo": {
        "algorithm": "ippo",
        "avg_waiting_time": 42.5,
        "avg_travel_time": 410.2,
        "avg_queue_length": 18.3,
        "throughput": 1370,
        "fuel_consumption": 91.0,
    },
}

DEFAULT_EVENTS = [
    {
        "event_id": "event_001",
        "time": 1250,
        "type": "congestion",
        "level": "high",
        "location": {"intersection_id": "J12", "lane_id": "E12_0"},
        "description": "J12南向进口道持续排队，平均速度低于3m/s",
        "evidence": {
            "avg_speed": 2.8,
            "queue_length": 38,
            "avg_waiting_time": 72.5,
        },
        "suggestion": "extend north-south green phase",
    }
]

INTERSECTIONS = [
    {
        "intersection_id": "J12",
        "name": "J12",
        "x": 470,
        "y": 330,
        "current_phase": 1,
        "phase_name": "南北直行",
        "phase_duration": 35,
        "queue_length": 38,
        "avg_waiting_time": 64.2,
        "avg_speed": 2.8,
        "status": "congested",
    },
    {
        "intersection_id": "J09",
        "name": "J09",
        "x": 380,
        "y": 310,
        "current_phase": 2,
        "phase_name": "东西直行",
        "phase_duration": 30,
        "queue_length": 12,
        "avg_waiting_time": 28.5,
        "avg_speed": 6.2,
        "status": "slow",
    },
]

LANES = [
    {
        "lane_id": "E12_0",
        "edge_id": "E12",
        "vehicle_count": 12,
        "queue_length": 8,
        "avg_speed": 3.1,
        "occupancy": 0.72,
        "status": "slow",
    }
]

VEHICLES = [
    {
        "vehicle_id": "veh_1024",
        "x": 438,
        "y": 378,
        "speed": 0,
        "waiting_time": 45,
        "lane_id": "E12_0",
        "type": "car",
        "angle": 90,
    }
]
