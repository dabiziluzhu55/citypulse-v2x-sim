# CityPulse V2X 后端 Mock 规格文档

> 版本：与前端 `citypulse-v2x-frontend@0.1.0` 对齐  
> 基础路径：`/api/v1`（可通过前端 `VITE_API_BASE_URL` 覆盖）  
> 开发代理：Vite → `http://localhost:8000`  
> 本文档供后端实现与 Mock 服务联调使用，请求/响应结构以前端 TypeScript 类型为准。

---

## 1. 通用约定

### 1.1 HTTP

| 项目 | 值 |
|------|-----|
| Base URL | `/api/v1` |
| Content-Type | `application/json` |
| 超时（前端） | 10 000 ms |
| 错误响应 | `{ "detail": "错误描述字符串" }` |

### 1.2 标识符来源

| 标识符 | 说明 |
|--------|------|
| `scenario_id` | `POST /scenarios` 返回；或 URL `?scenario_id=` |
| `run_id` | `POST /runs` 返回；持久化于 `localStorage: citypulse.active_run_id`；或 URL `?run_id=` |
| `experiment_id` | URL `?experiment_id=` → 回退 `overview.scenario_id` → 环境变量 `VITE_DEFAULT_EXPERIMENT_ID` |

### 1.3 WebSocket 通用

- 前端 **仅建立连接，不上行业务 JSON**。
- 断线后 **3 秒** 自动重连。
- 数组字段为空 `[]` 时，前端 **视为无增量更新**（不覆盖已有数据），适用于 `traffic_state` / `collaboration_state` 部分推送。

### 1.4 前端轮询间隔（Mock 可参考）

| 接口 | 间隔 |
|------|------|
| `GET /runs/{run_id}/status` | 2 s |
| `GET /runs/{run_id}/overview` | 5 s |
| `GET /runs/{run_id}/traffic-state` | 启动时 + WS |
| `GET /runs/{run_id}/collaboration-state` | 启动时 + WS |
| `GET /runs/{run_id}/metrics/realtime` | 5 s |
| `GET /runs/{run_id}/metrics/timeseries` | 5 s |
| `GET /experiments/{id}/comparison` | 30 s |
| `GET /runs/{run_id}/events` | 30 s |
| `GET /runs/{run_id}/prediction` | 30 s |

---

## 2. 场景构建（Module 2）

### 2.1 获取场景模板

```
GET /api/v1/scenario-templates
```

**Response 200**

```json
{
  "templates": [
    {
      "template_id": "xiongan20",
      "name": "雄安窄路密网20路口",
      "intersection_count": 20,
      "description": "窄路密网典型通勤场景",
      "map_center": [115.9348, 39.0631],
      "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
      "default_zoom": 15
    },
    {
      "template_id": "corridor4",
      "name": "4路口走廊控制",
      "intersection_count": 4,
      "description": "走廊协调控制实验场景",
      "map_center": [115.9348, 39.0631],
      "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
      "default_zoom": 15
    }
  ]
}
```

**模板可选地理字段（地图定位）**

| 字段 | 类型 | 说明 |
|------|------|------|
| `map_center` | `[lon, lat]` | 模板默认中心 |
| `map_bounds` | `[minLon, minLat, maxLon, maxLat]` | 模板范围 |
| `default_zoom` | `number` | 默认缩放级别 |

---

### 2.1.1 场景模板地图元数据（预留）

```
GET /api/v1/scenario-templates/{template_id}/map-meta
```

**Response 200**

```json
{
  "template_id": "xiongan20",
  "map_center": [115.9348, 39.0631],
  "map_bounds": [115.92696, 39.05825, 115.94267, 39.06798],
  "default_zoom": 15
}
```

---

### 2.2 创建场景

```
POST /api/v1/scenarios
```

**Request Body**

```json
{
  "name": "雄安20路口早高峰施工占道场景",
  "template_id": "xiongan20",
  "network_source": "prebuilt_sumo",
  "traffic_flow": {
    "mode": "morning_peak",
    "flow_scale": 1.2,
    "vehicle_types": {
      "car": 0.75,
      "bus": 0.1,
      "truck": 0.1,
      "bike": 0.05
    },
    "duration": 3600
  },
  "od_groups": [
    {
      "od_id": "od_001",
      "origin": "main_road_entrance",
      "destination": "school",
      "vehicles_per_hour": 800,
      "start_time": 0,
      "end_time": 3600
    }
  ],
  "traffic_light": {
    "initial_plan": "fixed_time",
    "cycle_length": 90,
    "min_green": 10,
    "max_green": 60,
    "yellow_time": 3
  },
  "disturbances": [
    {
      "type": "lane_closure",
      "edge_id": "E12",
      "lane_id": "E12_0",
      "start_time": 600,
      "duration": 900
    }
  ]
}
```

**枚举值**

| 字段 | 允许值 |
|------|--------|
| `network_source` | `osm_import`, `prebuilt_sumo`, `manual_netedit` |
| `traffic_flow.mode` | `flat`, `morning_peak`, `evening_peak`, `event_dispersal` |
| `traffic_light.initial_plan` | `fixed_time`, `default_sumo`, `custom` |
| `disturbances[].type` | `lane_closure`, `accident`, `event_dispersal`, `speed_limit` |

**扰动子类型字段**

```json
// lane_closure
{ "type": "lane_closure", "edge_id": "string", "lane_id": "string", "start_time": 0, "duration": 0 }

// accident
{ "type": "accident", "vehicle_id": "string?", "random_vehicle": true, "edge_id": "string", "start_time": 0, "duration": 0 }

// event_dispersal
{ "type": "event_dispersal", "origin": "string", "destination": "string", "surge_flow": 0, "start_time": 0 }

// speed_limit
{ "type": "speed_limit", "edge_id": "string", "speed_limit": 8.33, "start_time": 0, "duration": 0 }
```

**Response 201**

```json
{
  "scenario_id": "scenario_20260704_001",
  "status": "ready",
  "files": {
    "net": "scenarios/scenario_20260704_001/net.xml",
    "route": "scenarios/scenario_20260704_001/rou.xml",
    "config": "scenarios/scenario_20260704_001/sumo.cfg"
  }
}
```

**Mock 建议**

- `scenario_id` 格式：`scenario_{YYYYMMDD}_{序号}`。
- 重复提交可返回新 ID 或 409，前端仅处理 2xx + `scenario_id`。

---

## 3. 仿真控制（Module 3）

### 3.1 启动仿真

```
POST /api/v1/runs
```

**Request Body**

```json
{
  "scenario_id": "scenario_20260704_001",
  "algorithm": "ippo",
  "cloud_edge_enabled": true,
  "realtime": true,
  "step_length": 1.0
}
```

| 字段 | 前端当前默认值 |
|------|----------------|
| `algorithm` | 右侧算法面板选中项，回退 `fixed_time` |
| `cloud_edge_enabled` | `true` |
| `realtime` | `true` |
| `step_length` | `1.0` |

**Response 201**

```json
{
  "run_id": "run_20260704_001",
  "status": "starting",
  "message": "仿真已启动，正在加载 SUMO 场景"
}
```

**`status` 枚举：** `starting`, `running`, `paused`, `stopped`, `idle`, `error`

---

### 3.2 控制仿真

```
POST /api/v1/runs/{run_id}/control
```

**Request Body**

```json
{
  "command": "pause"
}
```

**`command` 枚举：** `pause`, `resume`, `stop`, `reset`, `step`

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "status": "paused"
}
```

---

### 3.3 查询运行状态

```
GET /api/v1/runs/{run_id}/status
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "status": "running",
  "sim_time": 1250,
  "step": 1250,
  "vehicle_count": 842,
  "message": "仿真运行中"
}
```

**Mock 建议：** `sim_time` 在 `running` 状态下随轮询递增；`stopped` / `idle` / `error` 时前端停止轮询。

---

## 4. 系统总览（Module 1）

### 4.1 HTTP 轮询

```
GET /api/v1/runs/{run_id}/overview
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "scenario_id": "scenario_20260704_001",
  "scenario_name": "雄安窄路密网20路口",
  "status": "running",
  "sim_time": 1250,
  "vehicle_count": 842,
  "active_vehicle_count": 820,
  "algorithm": "ippo",
  "cloud_edge_enabled": true,
  "avg_speed": 8.7,
  "avg_waiting_time": 42.5,
  "avg_queue_length": 18.3,
  "congested_intersections": 3
}
```

**`status` 枚举：** `idle`, `running`, `paused`, `stopped`, `error`

---

### 4.2 WebSocket 推送

```
WS /api/v1/ws?run_id={run_id}
```

**下行消息**

```json
{
  "type": "overview",
  "data": {
    "run_id": "run_20260704_001",
    "scenario_id": "scenario_20260704_001",
    "scenario_name": "雄安窄路密网20路口",
    "status": "running",
    "sim_time": 1250,
    "vehicle_count": 842,
    "active_vehicle_count": 820,
    "algorithm": "ippo",
    "cloud_edge_enabled": true,
    "avg_speed": 8.7,
    "avg_waiting_time": 42.5,
    "avg_queue_length": 18.3,
    "congested_intersections": 3
  }
}
```

**Mock 建议：** 每 5 s 推送一次，或与 HTTP 轮询返回相同结构。

---

## 5. 交通可视化（Module 4）

### 5.1 HTTP

```
GET /api/v1/runs/{run_id}/traffic-state
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "sim_time": 1250,
  "intersections": [
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
      "status": "congested"
    }
  ],
  "lanes": [
    {
      "lane_id": "E12_0",
      "edge_id": "E12",
      "vehicle_count": 12,
      "queue_length": 8,
      "avg_speed": 3.1,
      "occupancy": 0.72,
      "status": "slow"
    }
  ],
  "vehicles": [
    {
      "vehicle_id": "veh_1024",
      "x": 438,
      "y": 378,
      "speed": 0,
      "waiting_time": 45,
      "lane_id": "E12_0",
      "type": "car",
      "angle": 90
    }
  ]
}
```

**`status` 枚举（路口/车道）：** `free`, `slow`, `congested`

---

### 5.2 WebSocket（与 Module 5/7 共用连接）

```
WS /api/v1/ws/runs/{run_id}
```

**下行：`traffic_state`（支持增量）**

```json
{
  "type": "traffic_state",
  "timestamp": 1250,
  "data": {
    "vehicle_count": 842,
    "avg_speed": 8.7,
    "intersections": [],
    "lanes": [],
    "vehicles": [
      {
        "vehicle_id": "veh_1024",
        "x": 440,
        "y": 378,
        "speed": 2.5,
        "waiting_time": 46,
        "lane_id": "E12_0",
        "type": "car"
      }
    ]
  }
}
```

**Mock 规则**

- `data.intersections` / `lanes` / `vehicles` 为空数组 → 前端 **不更新** 对应字段。
- 非空数组 → 按 ID 合并替换。

---

## 6. 车路云协同（Module 5）

### 6.1 HTTP

```
GET /api/v1/runs/{run_id}/collaboration-state
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "sim_time": 1250,
  "cloud": {
    "strategy": "corridor_priority",
    "target_area": "J09-J12-J16",
    "reason": "南北走廊排队超阈值",
    "algorithm": "ippo"
  },
  "edges": [
    {
      "edge_agent_id": "agent_J12",
      "intersection_id": "J12",
      "local_state": {
        "queue_length": 35,
        "avg_waiting_time": 64.2,
        "current_phase": 1
      },
      "local_rule_check": {
        "min_green_satisfied": true,
        "conflict_free": true
      },
      "last_action": {
        "action_type": "extend_green",
        "target_phase": 1,
        "duration": 10
      },
      "status": "executed"
    }
  ],
  "vehicles": [
    {
      "vehicle_id": "veh_1024",
      "lane_id": "E12_0",
      "speed": 9.5,
      "waiting_time": 12,
      "received_advice": {
        "type": "speed_advice",
        "recommended_speed": 10,
        "recommended_path": "J12→J16"
      }
    }
  ]
}
```

---

### 6.2 WebSocket

**下行：`collaboration_state`（支持增量）**

```json
{
  "type": "collaboration_state",
  "timestamp": 1250,
  "data": {
    "cloud": {
      "strategy": "corridor_priority",
      "target_area": "J09-J12-J16",
      "reason": "南北走廊排队超阈值",
      "algorithm": "ippo"
    },
    "edges": [],
    "vehicles": []
  }
}
```

---

## 7. 管控算法（Module 6）

### 7.1 算法列表

```
GET /api/v1/algorithms
```

**Response 200**

```json
{
  "algorithms": [
    {
      "algorithm_id": "fixed_time",
      "name": "固定配时",
      "type": "baseline",
      "description": "SUMO 默认固定配时方案"
    },
    {
      "algorithm_id": "actuated",
      "name": "感应控制",
      "type": "rule_based",
      "description": "基于检测器感应控制"
    },
    {
      "algorithm_id": "max_pressure",
      "name": "Max-Pressure",
      "type": "rule_based",
      "description": "压力最大化自适应控制"
    },
    {
      "algorithm_id": "ippo",
      "name": "多路口 IPPO",
      "type": "reinforcement_learning",
      "description": "多路口强化学习协同控制"
    }
  ]
}
```

**`type` 枚举：** `baseline`, `rule_based`, `reinforcement_learning`

---

### 7.2 切换算法

```
POST /api/v1/runs/{run_id}/algorithm
```

**Request Body**

```json
{
  "algorithm_id": "ippo",
  "parameters": {
    "min_green": 10,
    "max_green": 60
  }
}
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "algorithm": "ippo",
  "status": "applied"
}
```

---

## 8. 事件识别与预测（Module 7）

### 8.1 事件列表

```
GET /api/v1/runs/{run_id}/events
```

**Response 200**

```json
{
  "events": [
    {
      "event_id": "event_001",
      "time": 1250,
      "type": "congestion",
      "level": "high",
      "location": {
        "intersection_id": "J12",
        "lane_id": "E12_0"
      },
      "description": "J12南向进口道持续排队，平均速度低于3m/s",
      "evidence": {
        "avg_speed": 2.8,
        "queue_length": 38,
        "avg_waiting_time": 72.5
      },
      "suggestion": "extend north-south green phase"
    }
  ]
}
```

**`type` 常见值：** `congestion`, `abnormal_parking`, `lane_closure`, `queue_spillover`  
**`level` 枚举：** `low`, `medium`, `high`

---

### 8.2 交通流预测

```
GET /api/v1/runs/{run_id}/prediction?target=J12&horizon=300
```

| Query | 类型 | 默认 | 说明 |
|-------|------|------|------|
| `target` | string | `J12` | 路口 ID |
| `horizon` | number | `300` | 预测窗口（秒） |

**Response 200**

```json
{
  "target": "J12",
  "horizon": 300,
  "predictions": [
    {
      "time_offset": 60,
      "predicted_flow": 86,
      "predicted_queue": 22,
      "congestion_risk": 0.62
    },
    {
      "time_offset": 300,
      "predicted_flow": 110,
      "predicted_queue": 35,
      "congestion_risk": 0.81
    }
  ],
  "model": "gru_onnx",
  "updated_at": 1250
}
```

---

### 8.3 WebSocket 事件推送

**下行：`event_detected`（约 30 s 一次即可）**

```json
{
  "type": "event_detected",
  "timestamp": 1250,
  "data": {
    "event_id": "event_001",
    "type": "congestion",
    "level": "high",
    "location": {
      "intersection_id": "J12"
    }
  }
}
```

**Mock 规则：** 推送字段可与 HTTP 事件合并；缺少 `description` / `evidence` / `suggestion` 时前端自动生成占位文案。

---

## 9. 数据指标展示（Module 8）

### 9.1 实时指标

```
GET /api/v1/runs/{run_id}/metrics/realtime
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "time": 1250,
  "metrics": {
    "avg_speed": 8.7,
    "avg_waiting_time": 42.5,
    "avg_travel_time": 410.2,
    "avg_queue_length": 18.3,
    "throughput": 1370,
    "fuel_consumption": 91.0,
    "co2_emission": 88.4
  }
}
```

---

### 9.2 实验对比指标

```
GET /api/v1/experiments/{experiment_id}/comparison
```

**Response 200**

```json
{
  "experiment_id": "exp_001",
  "scenario_id": "scenario_20260704_001",
  "baselines": ["fixed_time", "actuated", "max_pressure", "ippo"],
  "results": [
    {
      "algorithm": "fixed_time",
      "avg_waiting_time": 60.2,
      "avg_travel_time": 480.0,
      "avg_queue_length": 25.4,
      "throughput": 1200,
      "fuel_consumption": 100.0
    },
    {
      "algorithm": "ippo",
      "avg_waiting_time": 42.5,
      "avg_travel_time": 410.2,
      "avg_queue_length": 18.3,
      "throughput": 1370,
      "fuel_consumption": 91.0
    }
  ]
}
```

**Mock 规则**

- 前端以 `fixed_time` 为基线、以 `overview.algorithm` 为当前算法做对比行。
- `results` 必须包含 `fixed_time` 与当前运行算法条目。
- 前端改善率：等待/行程/排队/燃油 → 百分比；通行量 → 比值（如 `0.142`）。

---

### 9.3 指标时序曲线

```
GET /api/v1/runs/{run_id}/metrics/timeseries
```

**Response 200**

```json
{
  "run_id": "run_20260704_001",
  "series": [
    {
      "time": 0,
      "avg_waiting_time": 0,
      "avg_queue_length": 0,
      "throughput": 0
    },
    {
      "time": 300,
      "avg_waiting_time": 18.2,
      "avg_queue_length": 10.4,
      "throughput": 220
    },
    {
      "time": 1250,
      "avg_waiting_time": 42.5,
      "avg_queue_length": 18.3,
      "throughput": 1370
    }
  ]
}
```

**Mock 建议：** 每次请求可返回累积全量序列；`time` 与 `sim_time` 对齐。

---

## 10. 接口索引

| # | 方法 | 路径 | 写/读 |
|---|------|------|-------|
| 1 | GET | `/scenario-templates` | 读 |
| 2 | POST | `/scenarios` | 写 |
| 3 | POST | `/runs` | 写 |
| 4 | POST | `/runs/{run_id}/control` | 写 |
| 5 | GET | `/runs/{run_id}/status` | 读 |
| 6 | GET | `/runs/{run_id}/overview` | 读 |
| 7 | GET | `/runs/{run_id}/traffic-state` | 读 |
| 8 | GET | `/runs/{run_id}/collaboration-state` | 读 |
| 9 | GET | `/algorithms` | 读 |
| 10 | POST | `/runs/{run_id}/algorithm` | 写 |
| 11 | GET | `/runs/{run_id}/events` | 读 |
| 12 | GET | `/runs/{run_id}/prediction` | 读 |
| 13 | GET | `/runs/{run_id}/metrics/realtime` | 读 |
| 14 | GET | `/runs/{run_id}/metrics/timeseries` | 读 |
| 15 | GET | `/experiments/{experiment_id}/comparison` | 读 |

| # | 协议 | 路径 |
|---|------|------|
| W1 | WS | `/api/v1/ws?run_id={run_id}` |
| W2 | WS | `/api/v1/ws/runs/{run_id}` |

---

## 11. Mock 服务最小实现清单

按联调优先级建议实现顺序：

1. **P0 — 跑通主链路**
   - `GET /scenario-templates`
   - `POST /scenarios`
   - `POST /runs`
   - `GET /runs/{run_id}/status`
   - `GET /runs/{run_id}/overview`

2. **P1 — 仪表盘完整**
   - `POST /runs/{run_id}/control`
   - `GET /algorithms`
   - `POST /runs/{run_id}/algorithm`
   - `GET /runs/{run_id}/traffic-state`
   - `WS /api/v1/ws/runs/{run_id}`（`traffic_state`）

3. **P2 — 协同 / 事件 / 指标**
   - `GET /runs/{run_id}/collaboration-state`
   - `GET /runs/{run_id}/events`
   - `GET /runs/{run_id}/prediction`
   - `GET /runs/{run_id}/metrics/realtime`
   - `GET /runs/{run_id}/metrics/timeseries`
   - `GET /experiments/{experiment_id}/comparison`
   - `WS` 补充 `collaboration_state`、`event_detected`
   - `WS /api/v1/ws?run_id=`（`overview`）

---

## 12. 联调示例：curl

```bash
# 模板
curl http://localhost:8000/api/v1/scenario-templates

# 创建场景
curl -X POST http://localhost:8000/api/v1/scenarios \
  -H "Content-Type: application/json" \
  -d @docs/fixtures/scenario_create.json

# 启动仿真
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"scenario_id":"scenario_20260704_001","algorithm":"ippo","cloud_edge_enabled":true,"realtime":true,"step_length":1.0}'

# 状态
curl http://localhost:8000/api/v1/runs/run_20260704_001/status

# 停止
curl -X POST http://localhost:8000/api/v1/runs/run_20260704_001/control \
  -H "Content-Type: application/json" \
  -d '{"command":"stop"}'
```

---

## 13. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-06 | 初版：对齐前端 8 模块、15 HTTP + 2 WS |
