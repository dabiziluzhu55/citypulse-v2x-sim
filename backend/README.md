# CityPulse V2X Backend

基于 `SimulationManager` 的真实 SUMO 仿真后端，入口为 `backend.app.main:app`。

- 前端本地运行在 `http://localhost:5173`
- 后端本地运行在 `http://localhost:8000`

## 架构边界

- FastAPI **不直接** import 或调用 `traci`
- FastAPI **不通过 subprocess** 启动 `python -m simulation.sumo.run`
- FastAPI **不自行**启动 `sumo` / `sumo-gui`
- 唯一 TraCI 所有者是 `SimulationManager` 内部工作线程
- 全应用只创建一个全局 `SimulationManager` 实例
- 同一时间只允许一个活动仿真会话

分层职责：

| 层 | 路径 | 说明 |
|---|---|---|
| API | `app/api/v1/` | 对外统一 REST / WebSocket |
| 仿真控制 | `app/simulation/` | 将 `control_mode` 映射为 SUMO 配置 |
| 管控算法 | `app/controllers/` | Max Pressure 等纯决策逻辑 |
| 指标计算 | `app/metrics/` | 评估指标采集与汇总 |
| 内部算法协议 | `app/api/v1/internal_algorithm.py` | 供 SUMO worker HTTP 回调 |

## 配置 SUMO_HOME

```bash
export SUMO_HOME=/usr/share/sumo
```

或复制环境模板：

```bash
cp backend/.env.example backend/.env
```

## 构建 demo_2 生成文件

在仓库根目录执行：

```bash
python -m simulation.sumo.build_tls --intersections demo_2
python -m simulation.sumo.build_traffic --intersections demo_2
```

生成目录：

```text
data/maps/sumo/generated/
├── traffic_manifest.json
├── tls_manifest.json
└── TotalMap_20.signals.net.xml
```

若这些文件缺失，后端仍可启动，但 `/api/v1/health` 会返回 `degraded`，仿真相关接口返回 `503`。

## 安装依赖

在仓库根目录执行：

```bash
pip install -r backend/requirements.txt
```

## 启动后端

在仓库根目录执行：

```bash
uvicorn backend.app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1
```

注意：

- 必须使用 `--workers 1`
- **活动仿真运行时不要使用** `--reload`，否则 reload 会导致活动会话丢失

Swagger 文档：

```text
http://localhost:8000/docs
```

## 健康检查

```bash
curl http://localhost:8000/api/v1/health
```

## 获取仿真目录

```bash
curl http://localhost:8000/api/v1/catalog
```

MVP 只返回 `demo_2`。lane ID、进口方向、时段都必须从 catalog 获取，不要硬编码。
`control_modes` 当前包含 `fixed` 与 `max_pressure`。

## 获取 demo_2 路网 GeoJSON

```bash
curl "http://localhost:8000/api/v1/maps/demo_2/geojson?radius_m=600"
```

返回 Cesium 可加载的 WGS84 坐标系下的 GeoJSON，坐标来自 `sumolib.net.convertXY2LonLat()`。

## 启动仿真

```bash
curl -X POST http://localhost:8000/api/v1/simulations \
  -H "Content-Type: application/json" \
  -d '{
    "intersection_ids": ["demo_2"],
    "period": "morning_peak",
    "origins": {},
    "window_start_seconds": 0,
    "duration_seconds": 600,
    "flow_multiplier": 1.2,
    "control_mode": "fixed",
    "seed": 42,
    "step_length": 0.05,
    "realtime": true,
    "gui": false,
    "snapshot_interval_seconds": 0.2,
    "initial_events": []
  }'
```

`control_mode` 可选 `fixed` 或 `max_pressure`。成功时返回：

```json
{
  "session_id": "...",
  "state": "STARTING",
  "status_url": "/api/v1/simulations/{session_id}",
  "websocket_url": "/api/v1/simulations/{session_id}/stream",
  "metrics_url": "/api/v1/simulations/{session_id}/metrics"
}
```

## WebSocket 实时流

连接地址示例：

```text
ws://localhost:8000/api/v1/simulations/{session_id}/stream
```

消息类型：

- `snapshot`：完整仿真快照（算法模式下可含 `evaluation` 指标）
- `heartbeat`：2 秒内无新快照时发送

## 查询评估指标

```bash
curl http://localhost:8000/api/v1/simulations/{session_id}/metrics
```

## 停止仿真

```bash
curl -X POST http://localhost:8000/api/v1/simulations/{session_id}/stop
```

## 添加扰动事件

先获取 catalog，再从 `demo_2.lanes` 中选择 `role="incoming"` 的 `lane_id`：

```bash
curl http://localhost:8000/api/v1/catalog
```

施工占道示例：

```bash
curl -X POST http://localhost:8000/api/v1/simulations/{session_id}/events \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "lane_closure",
    "event_id": "construction-1",
    "start_seconds": 60,
    "end_seconds": 300,
    "lane_ids": ["<lane_id_from_catalog>"]
  }'
```

## 取消扰动事件

```bash
curl -X DELETE http://localhost:8000/api/v1/simulations/{session_id}/events/{event_id}
```

## 前端 Cesium 接入说明

1. 页面初始化时请求 `GET /api/v1/maps/demo_2/geojson`
2. 使用 `Cesium.GeoJsonDataSource.load()` 加载路网
3. 创建仿真后连接响应中的 `websocket_url`
4. 以 `vehicle_id` 为唯一键创建或更新车辆实体
5. 使用 `longitude`、`latitude` 和 `angle` 更新车辆位置
6. 仿真结束时移除不再存在的车辆实体

## 前端字段映射

| 前端字段 | 后端字段 | MVP 说明 |
|---|---|---|
| 场景模式 | `intersection_ids` | 固定 `["demo_2"]` |
| 交通流模式 | `period` | `morning_peak` / `off_peak` / `evening_peak` |
| 起始点 OD | `origins` | 目前只支持进口筛选，不是 A→B OD |
| 扰动事件 | `initial_events` | 支持施工占道、事故、临时限速 |
| 交通流倍率 | `flow_multiplier` | `0.1` 到 `5.0` |
| 仿真时长 | `duration_seconds` | 必须大于 0 |
| 管控算法 | `control_mode` | `fixed` / `max_pressure` |

## 暂停按钮

- “开始仿真”和“结束仿真”已接通
- “暂停仿真”**暂未实现**
- 前端应禁用暂停按钮

当前后端对外会话接口未暴露 pause/resume，不会破坏 TraCI 线程所有权设计。

## 测试

单元测试：

```bash
pytest backend/tests -q
```

语法检查：

```bash
python -m compileall backend/app
```

可选 SUMO 集成测试（需先启动后端并生成 demo_2 文件）：

```bash
python backend/tests/integration_demo_2.py
```

## 当前功能范围

已实现：

- 健康检查
- catalog
- demo_2 GeoJSON 路网显示
- 启动/查询/停止仿真
- WebSocket 实时推送数据
- 扰动事件增删
- 车辆经纬度转换
- Max Pressure 管控（`control_mode=max_pressure`）
- 评估指标查询（`/metrics`）

## 安全约定

- 不要提交 `frontend/.env`、后端 `.env` 或真实 token。
- Cesium ion 和天地图 token 应通过本地忽略文件或部署平台 Secrets 注入。
- `.env.example` 只能保留变量名和空值。
