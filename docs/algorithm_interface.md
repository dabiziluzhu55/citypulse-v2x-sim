# 算法组 HTTP 接口（协议 2.0）

算法服务不需要安装 SUMO、读取地图 XML 或调用 TraCI。仿真端独占 TraCI，通过同步
HTTP/JSON 把路口和单车状态发送给算法，并执行算法返回的信号灯和车辆动作。

协议 2.0 不兼容旧版 1.0。请求和响应均使用 `Content-Type: application/json`；非 2xx、
超时、非法 JSON、版本或 episode/step 回显错误以及非法动作都会终止本轮仿真。

## 接口概览

| 接口 | 调用次数 | 用途 |
|---|---:|---|
| `POST /initialize` | 每轮 1 次 | 接收路口拓扑、相位、车道、车型和控制能力 |
| `POST /step` | 每个决策周期 1 次 | 接收路口与单车实时状态，返回全部动作 |
| `POST /finish` | 每轮 1 次 | 接收结束原因和汇总指标 |

## 1. 初始化

初始化请求保留原有 `intersections` 结构，并新增车型画像和车辆控制能力：

```json
{
  "protocol_version": "2.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "period": "morning_peak",
  "seed": 42,
  "decision_interval": 5.0,
  "minimum_green": 5.0,
  "intersections": {
    "demo_2": {
      "intersection_id": "demo_2",
      "phase_order": [1, 2],
      "phases": {
        "1": {
          "phase_id": 1,
          "name": "南北向直行",
          "movement": "through",
          "approaches": ["northeast_main", "southwest_main"],
          "green_seconds": 33.0,
          "yellow_seconds": 3.0,
          "clearance_seconds": 0.0,
          "connection_priorities": {"connection_0": "protected"}
        }
      },
      "lanes": {
        "-56734_0": {
          "lane_id": "-56734_0",
          "edge_id": "-56734",
          "lane_index": 0,
          "role": "incoming",
          "length": 217.4,
          "max_speed": 13.9,
          "intersection_id": "demo_2",
          "approach_id": "demo_2_northeast_main_in",
          "movements": ["through", "left"],
          "length_m": 217.4,
          "speed_limit_mps": 13.9,
          "downstream_lane_ids": ["-56736_0", "-45801_1"]
        }
      },
      "incoming_lanes": ["-56734_0"],
      "outgoing_lanes": ["-56736_0"],
      "connections": [
        {
          "connection_id": "connection_0",
          "approach": "northeast_main",
          "movement": "through",
          "from_lane": "-56734_0",
          "to_lane": "-56736_0",
          "direction": "s",
          "tls_id": "317",
          "link_index": 0
        }
      ],
      "direct_neighbors": []
    }
  },
  "vehicle_types": {
    "official_passenger": {
      "type_id": "official_passenger",
      "profile_id": "passenger",
      "pcu_factor": 1.0,
      "vehicle_class": "passenger",
      "powertrain": "gasoline",
      "emission_class": "HBEFA3/PC_G_EU4",
      "accel_mps2": 2.6,
      "decel_mps2": 4.5,
      "length_m": 5.0,
      "width_m": 1.8,
      "min_gap_m": 2.5,
      "max_speed_mps": 13.9,
      "fuel_density_mg_per_ml": 745.0,
      "hard_braking_threshold_mps2": -3.0
    },
    "official_electric_bicycle": {
      "type_id": "official_electric_bicycle",
      "profile_id": "electric_bicycle",
      "pcu_factor": 0.5,
      "vehicle_class": "bicycle",
      "powertrain": "electric",
      "emission_class": "HBEFA3/zero",
      "accel_mps2": 1.5,
      "decel_mps2": 3.0,
      "length_m": 1.8,
      "width_m": 0.65,
      "min_gap_m": 0.5,
      "max_speed_mps": 6.94,
      "fuel_density_mg_per_ml": 1.0,
      "hard_braking_threshold_mps2": -2.0
    }
  },
  "edge_lanes": {
    "-56734": [
      {
        "lane_id": "-56734_0",
        "edge_id": "-56734",
        "lane_index": 0,
        "length_m": 217.4,
        "speed_limit_mps": 13.9,
        "allowed_vehicle_classes": [],
        "disallowed_vehicle_classes": [],
        "allowed_vehicle_type_ids": ["official_passenger"]
      }
    ],
    "bike_edge": [
      {
        "lane_id": "bike_edge_0",
        "edge_id": "bike_edge",
        "lane_index": 0,
        "length_m": 80.0,
        "speed_limit_mps": 5.5,
        "allowed_vehicle_classes": ["bicycle"],
        "disallowed_vehicle_classes": [],
        "allowed_vehicle_type_ids": ["official_electric_bicycle"]
      }
    ]
  },
  "vehicle_control": {
    "supported_actions": ["target_speed_mps", "target_lane_index"],
    "action_lease_seconds": 5.0,
    "speed_unit": "m/s",
    "lane_change_scope": "current_edge"
  }
}
```

算法必须响应：

```json
{
  "protocol_version": "2.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "ready": true
}
```

`phase_order` 是该路口允许的动作集合；`connections` 描述上下游车道；
`connection_priorities` 表示相位放行的 protected/permissive movement。算法应保存这些
静态信息，不要在每一步重新推断拓扑。

`movements` 使用 `through/left/right/uturn`，因为一个物理车道可以支持多个转向，所以不是
单值。纯出口车道的 `approach_id=null`、`movements=[]`、`downstream_lane_ids=[]`。
`length/max_speed` 为兼容字段，`length_m/speed_limit_mps` 是带单位的同值别名。

`edge_lanes` 是全网普通 edge 的静态车道权限表，不包含 `:` 开头的 internal edge。算法选择
`target_lane_index` 时，应使用车辆 `type_id` 和 `location.road_id` 查询
`edge_lanes[road_id]`，只选择 `allowed_vehicle_type_ids` 包含该 `type_id` 的 lane。
`allowed_vehicle_classes/disallowed_vehicle_classes` 是 SUMO 原始权限，
`allowed_vehicle_type_ids` 是仿真端按本轮官方车辆类型计算后的可控结果。若 `road_id` 不在
`edge_lanes`，或 `road_id` 以 `:` 开头，算法不应返回换道动作。

## 2. 决策请求

仿真端按 `decision_interval` 调用 `/step`：

```json
{
  "protocol_version": "2.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "step_id": 12,
  "simulation_time": 60.0,
  "intersections": {
    "demo_2": {
      "current_phase": 1,
      "pending_phase": null,
      "stage": "GREEN",
      "stage_elapsed": 10.0,
      "lanes": {
        "-56734_0": {
          "vehicle_count": 8,
          "halting_count": 5,
          "mean_speed": 3.2,
          "waiting_time": 41.5,
          "occupancy": 27.0,
          "lane_has_green": true,
          "signal_state": "mixed",
          "queue_length_m": 95.0,
          "queue_length_is_estimate": true,
          "current_allowed_speed_mps": 13.9,
          "controlled_vehicle_count": 2,
          "min_target_speed_mps": 6.0,
          "mean_target_speed_mps": 7.0,
          "connection_signal_states": [
            {
              "connection_id": "connection_0",
              "movement": "through",
              "downstream_lane_id": "-56736_0",
              "signal_state": "G"
            },
            {
              "connection_id": "connection_1",
              "movement": "left",
              "downstream_lane_id": "-45801_1",
              "signal_state": "r"
            }
          ]
        }
      }
    }
  },
  "vehicles": {
    "demo_2_morning_peak_00_west_left.0": {
      "type_id": "demo_2_official_passenger",
      "position": {"x_m": 512.4, "y_m": 308.1},
      "motion": {
        "speed_mps": 6.2,
        "acceleration_mps2": -0.8,
        "angle_deg": 92.0,
        "allowed_speed_mps": 13.9
      },
      "location": {
        "road_id": "-56734",
        "lane_id": "-56734_0",
        "lane_index": 0,
        "lane_position_m": 148.5,
        "route_id": "route_0",
        "route_index": 0,
        "route_edges": ["-56734", "-56736"]
      },
      "traffic": {
        "waiting_time_s": 0.0,
        "accumulated_waiting_time_s": 4.2,
        "time_loss_s": 8.5,
        "distance_m": 320.1
      },
      "next_signal": {
        "intersection_id": "demo_2",
        "tls_id": "317",
        "distance_m": 68.9,
        "state": "G"
      },
      "energy": {
        "fuel_rate_mg_s": 684.2,
        "fuel_since_last_decision_mg": 2410.5,
        "fuel_total_mg": 10520.4,
        "fuel_total_ml": 14.1213
      },
      "driving_events": {
        "hard_braking_since_last_decision": 0,
        "hard_braking_total": 1
      },
      "leader_gap_m": 18.4,
      "follower_gap_m": 11.7,
      "time_since_last_lane_change_s": 6.5
    }
  },
  "traffic": {
    "active_vehicles": 37,
    "departed_vehicles": 4,
    "arrived_vehicles": 3,
    "min_expected_vehicles": 1200,
    "fuel_consumed_mg": 245800.0,
    "fuel_consumed_ml": 329.9329,
    "hard_braking_events": 12
  },
  "previous_action_results": {
    "step_id": 11,
    "vehicles": {
      "demo_2_morning_peak_00_west_left.0": {
        "requested": {"target_speed_mps": 7.0, "target_lane_index": 1},
        "actual_speed_mps": 6.2,
        "actual_lane_index": 1,
        "speed_status": "applied",
        "lane_change_status": "completed"
      }
    }
  }
}
```

字段口径：

- `stage` 为 `GREEN`、`YELLOW` 或 `CLEARANCE`；信号切换安全过程由仿真端负责。
- `lane_has_green=true` 表示至少一个 connection 的原始灯色为 `G/g`；同车道 connection 灯色
  不同时，`signal_state="mixed"`。纯出口车道这两个汇总字段均为 `null`。
- `queue_length_m` 由停车车辆空间范围、停车数和占有率确定性估算并限制在车道长度内，当前
  始终有 `queue_length_is_estimate=true`。
- `current_allowed_speed_mps` 是车道当前允许速度，受原始限速、施工限速和封闭影响；它不是
  单车控制目标。后者由 `controlled_vehicle_count/min_target_speed_mps/mean_target_speed_mps` 汇总。
- 速度单位为 m/s，加速度为 m/s²，位置、里程和信号距离为米，角度为度。
- `waiting_time_s` 是当前连续等待，`accumulated_waiting_time_s` 是车辆累计等待，
  `time_loss_s` 是相对理想行程损失的累计时间。
- `fuel_rate_mg_s` 来自 SUMO HBEFA 排放模型；决策请求中的周期和累计油耗是按观测周期
  采样得到的在线估计，最终汇总由 SUMO tripinfo 的 emissions 结果覆盖。
- 急制动在观测到加速度首次进入 `<= -3.0 m/s²` 时计一次，持续制动不重复计数。
- `next_signal` 只返回下一处已选受控路口；不存在时为 `null`。
- `leader_gap_m` 和 `follower_gap_m` 是同车道可观察车辆之间的保险杠间距；对应方向没有
  可观察车辆时为 `null`。二者由决策前刷新的车道位置与车型长度计算。
- `time_since_last_lane_change_s` 按仿真时间计算；车辆从未换道时为 `null`，换道发生帧为
  `0.0`。只有同一普通 edge 内 lane index 改变才记为换道，正常驶入下一条 edge 不会重置。
- `active_vehicles` 等于本次 `vehicles` 中的官方可控车辆数，不包含事故占位车。
- `departed_vehicles` 和 `arrived_vehicles` 是本决策周期增量，其余汇总油耗和急制动为
  本轮累计值。

## 3. 决策响应

```json
{
  "protocol_version": "2.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "step_id": 12,
  "actions": {
    "signals": {
      "demo_2": {"target_phase": 2}
    },
    "vehicles": {
      "demo_2_morning_peak_00_west_left.0": {
        "target_speed_mps": 8.0,
        "target_lane_index": 1
      }
    }
  }
}
```

动作规则：

1. `protocol_version`、`episode_id` 和 `step_id` 必须原样回显。
2. `actions` 必须且只能包含对象 `signals` 和 `vehicles`，二者允许为空。
3. 信号动作必须使用初始化给出的路口和 phase ID；省略路口表示保持当前目标相位。
4. 车辆动作只能引用本次请求的车辆；动作至少设置速度或车道之一。
5. `target_speed_mps` 必须在 `[0, allowed_speed_mps]` 内。SUMO 仍执行跟车、防碰撞和限速。
6. `target_lane_index` 只指当前 road 上的车道；算法应先用初始化阶段的 `edge_lanes` 过滤候选 lane，internal edge、越界车道和禁行车道非法。
7. 单车动作只租用一个决策周期。下一周期省略速度会恢复 SUMO 自主速度，换道不续期。
8. 换道可能因安全间隙不足而未完成；下一步返回 `completed` 或 `not_completed`，这不是协议错误。
9. 仿真端在写入任何 TraCI 状态前验证全部动作；任意非法动作都会拒绝整步并终止 episode。

## 4. 结束

```json
{
  "protocol_version": "2.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "reason": "completed",
  "simulation_time": 7500.0,
  "departed_vehicles": 2761,
  "arrived_vehicles": 2761,
  "fuel_consumed_mg": 18230000.0,
  "fuel_consumed_ml": 24469.7987,
  "hard_braking_events": 184
}
```

`reason` 为 `completed`、`stopped` 或 `error`。`completed` 和 `stopped` 的车辆完成数及
油耗来自已写完的 `tripinfo.xml`，不是在线遥测积分。服务返回任意 JSON 对象即可，例如
`{"ok": true}`。

## 最小 FastAPI 服务

```python
from fastapi import FastAPI

app = FastAPI()


@app.post("/initialize")
def initialize(body: dict):
    return {
        "protocol_version": "2.0",
        "episode_id": body["episode_id"],
        "ready": True,
    }


@app.post("/step")
def step(body: dict):
    signal_actions = {
        intersection_id: {"target_phase": state["current_phase"]}
        for intersection_id, state in body["intersections"].items()
    }
    return {
        "protocol_version": "2.0",
        "episode_id": body["episode_id"],
        "step_id": body["step_id"],
        "actions": {"signals": signal_actions, "vehicles": {}},
    }


@app.post("/finish")
def finish(body: dict):
    return {"ok": True}
```

```bash
pip install fastapi uvicorn
uvicorn algorithm_server:app --host 0.0.0.0 --port 8001

python -m simulation.sumo.run --mode algorithm --gui --realtime \
  --algorithm-endpoint http://127.0.0.1:8001 \
  --intersection demo_2 --period morning_peak --decision-interval 1
```

算法与 SUMO 可以位于不同机器，只需将 endpoint 换成算法服务可访问地址。多轮训练时改变
`--seed`；每轮都会生成新的 `episode_id` 并调用 initialize/step/finish。

## 同进程本地算法

本地算法与 HTTP 使用同一协议校验和完全相同的 JSON 形状字典，但不经过网络或 JSON 编解码。
模块实现 `initialize(payload)`、`step(payload)`、`finish(payload)`，示例见
`algorithms/local_policy_example.py`：

```bash
python -m simulation.sumo.run --mode algorithm --gui \
  --algorithm-transport local \
  --algorithm-module algorithms.local_policy_example \
  --intersection demo_2 --period morning_peak
```

模块在 SUMO worker 线程同步执行；`step` 返回前仿真不会进入下一步。导入失败、函数缺失、
异常或非法响应都会使会话进入 `FAILED`。AI 观察者的独立异步接口见
[`local_transport_ai_observer.md`](local_transport_ai_observer.md)。
