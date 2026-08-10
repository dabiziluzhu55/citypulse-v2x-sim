# 东4/西3 零样本门禁报告

> 判据性质：**预注册工程判据，非行业标准**（preregistration.json 冻结于 2026-08-04）。
> checkpoint: /home/kemove/devdata1/gsb/citypulse-v2x-sim/traffic_control/ippo/models/ippo_v8_20tls_ep160.pt；seeds: [1042, 1142, 1242, 1342, 1442, 1542, 1642, 1742, 1842, 1942]；duration=300s

## 总判定

- **east_dense**: primary=strong (p=0.0010, Holm=0.0020, wins=10/10, 改善=66.69%)
- **west_dense**: primary=strong (p=0.0016, Holm=0.0016, wins=10/10, 改善=45.99%)
- **整体（交并判定，不做 Bonferroni 校正）**: 通过（主判据 + 非劣护栏）；安全门禁 incomplete（暴露量不足，不作为通过/不通过判定）

## east_dense

- primary=strong；非劣护栏=pass；安全门禁=incomplete

### 主判据（受控区域平均等待）

```json
{
  "scenario": "east_dense",
  "metric": "controlled_avg_waiting_time_s",
  "status": "strong",
  "p_value": 0.0009765625,
  "method": "exact",
  "wins": 10,
  "ties": 0,
  "losses": 0,
  "valid_pairs": 10,
  "relative_improvement": 0.666939666939667,
  "details": {
    "required_wins": 7,
    "win_rate": 1.0
  }
}
```

### 非劣护栏

```json
{
  "avg_travel_time_s": {
    "metric": "avg_travel_time_s",
    "status": "pass",
    "paired_mean": -0.024106619477169904,
    "paired_median": -0.0256411904751608,
    "worst_seed": -0.007928983883478425,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "avg_queue_length_veh": {
    "metric": "avg_queue_length_veh",
    "status": "pass",
    "paired_mean": -0.6363636363636366,
    "paired_median": -0.6363636363636365,
    "worst_seed": -0.6363636363636365,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": 0,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "fuel_intensity_L_per_100km": {
    "metric": "fuel_intensity_L_per_100km",
    "status": "pass",
    "paired_mean": -0.007157721067918897,
    "paired_median": -0.0072311712310801305,
    "worst_seed": -0.005513439007580984,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "throughput_veh_per_h": {
    "metric": "throughput_veh_per_h",
    "status": "pass",
    "paired_mean": -0.0014492753623188406,
    "paired_median": 0.0,
    "worst_seed": 0.0,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  }
}
```

### 安全门禁

```json
{
  "severe_conflict_exposure_per_10000": {
    "metric": "severe_conflict_exposure_per_10000",
    "status": "insufficient",
    "reason": "total controlled passages below 5000"
  },
  "emergency_braking_exposure_per_1000": {
    "metric": "emergency_braking_exposure_per_1000",
    "status": "insufficient",
    "reason": "total controlled passages below 5000"
  }
}
```

## west_dense

- primary=strong；非劣护栏=pass；安全门禁=incomplete

### 主判据（受控区域平均等待）

```json
{
  "scenario": "west_dense",
  "metric": "controlled_avg_waiting_time_s",
  "status": "strong",
  "p_value": 0.0015998400159984002,
  "method": "permutation",
  "wins": 10,
  "ties": 0,
  "losses": 0,
  "valid_pairs": 10,
  "relative_improvement": 0.4599401143271934,
  "details": {
    "required_wins": 7,
    "win_rate": 1.0
  }
}
```

### 非劣护栏

```json
{
  "avg_travel_time_s": {
    "metric": "avg_travel_time_s",
    "status": "pass",
    "paired_mean": -0.018714858450426763,
    "paired_median": -0.01962613622999855,
    "worst_seed": -0.009244723530437836,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "avg_queue_length_veh": {
    "metric": "avg_queue_length_veh",
    "status": "pass",
    "paired_mean": -0.7591119333950047,
    "paired_median": -0.7849213691026826,
    "worst_seed": -0.6808510638297872,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": 0,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "fuel_intensity_L_per_100km": {
    "metric": "fuel_intensity_L_per_100km",
    "status": "pass",
    "paired_mean": -0.010368750449404657,
    "paired_median": -0.01024240474458683,
    "worst_seed": -0.00886766712141888,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  },
  "throughput_veh_per_h": {
    "metric": "throughput_veh_per_h",
    "status": "pass",
    "paired_mean": -0.07165501165501166,
    "paired_median": -0.07575757575757576,
    "worst_seed": -0.06060606060606061,
    "violation_count": 0,
    "violation_ratio": 0.0,
    "abs_violation_count": null,
    "bound": 0.05,
    "max_violation_ratio": 0.3
  }
}
```

### 安全门禁

```json
{
  "severe_conflict_exposure_per_10000": {
    "metric": "severe_conflict_exposure_per_10000",
    "status": "insufficient",
    "reason": "total controlled passages below 5000"
  },
  "emergency_braking_exposure_per_1000": {
    "metric": "emergency_braking_exposure_per_1000",
    "status": "insufficient",
    "reason": "total controlled passages below 5000"
  }
}
```

