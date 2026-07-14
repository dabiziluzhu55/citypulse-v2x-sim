# 算法组接口文档

算法组只需要实现一个 HTTP 服务，不需要安装 SUMO，不需要读取地图 XML，也不要调用
TraCI。固定配时由仿真端自行运行；Max Pressure、IPPO 和多路口强化学习共用下面同一套
接口。

## 你们需要实现的接口

服务地址由双方约定，例如 `http://127.0.0.1:8001`。请求和响应均为
`Content-Type: application/json`。

| 接口 | 调用次数 | 用途 |
|---|---:|---|
| `POST /initialize` | 每轮 1 次 | 接收路口、相位、车道和连接关系 |
| `POST /step` | 每个决策周期 1 次 | 接收实时交通状态，返回目标相位 |
| `POST /finish` | 每轮 1 次 | 通知本轮结束和汇总指标 |

HTTP 非 2xx、超时、非法 JSON、错误 `step_id` 或非法相位都会让本轮仿真停止。

## 1. 初始化

仿真端请求 `POST /initialize`。下面是精简示例，真实请求会包含所有受控路口、车道和
connection：

```json
{
  "protocol_version": "1.0",
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
          "connection_priorities": {
            "connection_0": "protected",
            "connection_1": "permissive"
          }
        }
      },
      "lanes": {
        "-56734_0": {
          "lane_id": "-56734_0",
          "edge_id": "-56734",
          "lane_index": 0,
          "role": "incoming",
          "length": 217.4,
          "max_speed": 13.9
        }
      },
      "incoming_lanes": ["-56734_0", "-56734_1"],
      "outgoing_lanes": ["-56736_0", "-56736_1"],
      "connections": [
        {
          "connection_id": "connection_0",
          "approach": "northeast_main",
          "movement": "through",
          "from_lane": "-56734_0",
          "to_lane": "-56736_0",
          "direction": "s"
        }
      ],
      "direct_neighbors": []
    }
  }
}
```

算法服务必须响应：

```json
{"ready": true}
```

需要保存的静态字段：

- `phase_order`：允许返回的动作集合，顺序在本轮内固定。
- `incoming_lanes`、`outgoing_lanes`：状态向量的推荐固定顺序。
- `connections`：每个车流运动的上游和下游车道。
- `phases[*].connection_priorities`：该相位放行的 connection；值为
  `protected` 或 `permissive`。
- `direct_neighbors`：路网中直接连接的其他受控路口。多路口算法也可以直接使用请求中
  的全部 `intersections`。

`role` 为 `incoming`、`outgoing` 或 `both`。`max_speed` 单位是 m/s，`length` 单位是
米。`period` 表示当前官方车流时段，`seed` 是本轮实际传给 SUMO 的随机种子。

## 2. 决策

仿真端按 `decision_interval` 请求 `POST /step`：

```json
{
  "protocol_version": "1.0",
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
          "occupancy": 27.0
        },
        "-56736_0": {
          "vehicle_count": 2,
          "halting_count": 0,
          "mean_speed": 11.8,
          "waiting_time": 0.0,
          "occupancy": 5.0
        }
      }
    }
  },
  "traffic": {
    "active_vehicles": 37,
    "departed_vehicles": 4,
    "arrived_vehicles": 3,
    "min_expected_vehicles": 1200
  }
}
```

算法服务返回：

```json
{
  "step_id": 12,
  "actions": {
    "demo_2": 2
  }
}
```

规则只有四条：

1. `step_id` 必须原样返回，防止旧响应作用到新状态。
2. 动作值必须来自该路口的 `phase_order`。
3. 返回 `null` 或省略某个路口表示保持当前目标相位。
4. 只返回目标相位。黄灯、全红、最小绿灯和多个物理 TLS 同步由仿真端负责。

`stage` 可能是 `GREEN`、`YELLOW` 或 `CLEARANCE`。切换过程中可以继续给新目标，仿真端
会在安全条件满足后执行最新目标。

`current_phase` 是当前仍在执行或正在退出的相位；`pending_phase` 是已经接收、等待切入
的目标相位，没有待切换目标时为 `null`。

`vehicle_count` 和 `halting_count` 是车辆数，`mean_speed` 单位是 m/s，
`waiting_time` 是该车道所有车辆累计等待秒数，`occupancy` 是百分比。空车道的
`mean_speed` 固定为 `0.0`。

## 3. 结束

仿真端请求 `POST /finish`：

```json
{
  "protocol_version": "1.0",
  "episode_id": "550e8400-e29b-41d4-a716-446655440000",
  "reason": "completed",
  "simulation_time": 7500.0,
  "departed_vehicles": 2761,
  "arrived_vehicles": 2761
}
```

`reason` 为 `completed` 或 `error`。算法服务返回任意 JSON 对象即可，例如：

```json
{"ok": true}
```

## 各算法怎么取数据

- 固定配时：不启动算法服务，仿真端使用官方配时。
- Max Pressure：遍历某相位的 `connection_priorities`，用 connection 的
  `from_lane.halting_count - to_lane.halting_count` 计算压力，再对相位求和。
- IPPO：每个路口是一个 agent；按 `incoming_lanes + outgoing_lanes` 顺序拼车道状态，
  按 `phase_order` 把离散动作下标还原成 phase ID。
- 多路口强化学习：一次 `/step` 已包含所有路口；可使用全部路口状态进行集中训练，或用
  `direct_neighbors` 截取邻居状态。

奖励由算法组自行定义。常用量已经提供：排队车辆 `halting_count`、累计等待
`waiting_time`、速度、占有率和本决策周期到达车辆数 `traffic.arrived_vehicles`。
`/finish` 即 episode 结束信号。

## 最小 Python 空壳

下面代码没有算法逻辑，只保持当前相位，适合先联通接口：

```python
from fastapi import FastAPI

app = FastAPI()
metadata = None

@app.post("/initialize")
def initialize(body: dict):
    global metadata
    metadata = body
    return {"ready": True}

@app.post("/step")
def step(body: dict):
    actions = {
        intersection_id: state["current_phase"]
        for intersection_id, state in body["intersections"].items()
    }
    return {"step_id": body["step_id"], "actions": actions}

@app.post("/finish")
def finish(body: dict):
    return {"ok": True}
```

例如保存为 `algorithm_server.py` 后运行：

```bash
pip install fastapi uvicorn
uvicorn algorithm_server:app --host 0.0.0.0 --port 8001
```

启动算法服务后，仿真组使用：

```bash
python -m simulation.sumo.run --mode algorithm \
  --algorithm-endpoint http://127.0.0.1:8001 \
  --intersection demo_2 --period morning_peak --seed 42
```

算法服务和仿真可以在不同机器，只需把 `127.0.0.1` 换成算法服务可访问的 IP。
强化学习需要多轮 episode 时，多次启动该命令并改变 `--seed` 即可；每轮会产生新的
`episode_id`，结束时都会调用 `/finish`。
