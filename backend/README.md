# CityPulse V2X Backend

本项目包含两套 FastAPI 后端，可按需选择启动。

## 1. 仿真后端（SimulationManager）

基于 `SimulationManager` 的真实 SUMO 仿真接口，为前端提供 `demo_2` 的仿真能力。

- 前端本地运行在 `http://localhost:5173`
- 后端本地运行在 `http://localhost:8000`

### 架构边界

- FastAPI **不直接** import 或调用 `traci`
- FastAPI **不通过 subprocess** 启动 `python -m simulation.sumo.run`
- FastAPI **不自行**启动 `sumo` / `sumo-gui`
- 唯一 TraCI 所有者是 `SimulationManager` 内部工作线程
- 全应用只创建一个全局 `SimulationManager` 实例
- 同一时间只允许一个活动仿真会话

### 配置 SUMO_HOME

```bash
export SUMO_HOME=/usr/share/sumo
```

或复制环境模板：

```bash
cp backend/.env.example backend/.env
```

### 构建 demo_2 生成文件

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

### 安装依赖

在仓库根目录执行：

```bash
pip install -r backend/requirements.txt
```

### 启动仿真后端

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

### 健康检查

```bash
curl http://localhost:8000/api/v1/health
```

### 获取仿真目录

```bash
curl http://localhost:8000/api/v1/catalog
```

MVP 只返回 `demo_2`。lane ID、进口方向、时段都必须从 catalog 获取，不要硬编码。

### 获取 demo_2 路网 GeoJSON

```bash
curl "http://localhost:8000/api/v1/maps/demo_2/geojson?radius_m=600"
```

返回 Cesium 可加载的 WGS84 坐标系下的 GeoJSON，坐标来自 `sumolib.net.convertXY2LonLat()`。

### 启动仿真

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

成功时返回：

```json
{
  "session_id": "...",
  "state": "STARTING",
  "status_url": "/api/v1/simulations/{session_id}",
  "websocket_url": "/api/v1/simulations/{session_id}/stream"
}
```

### WebSocket 实时流

连接地址示例：

```text
ws://localhost:8000/api/v1/simulations/{session_id}/stream
```

消息类型：

- `snapshot`：完整仿真快照
- `heartbeat`：2 秒内无新快照时发送

### 停止仿真

```bash
curl -X POST http://localhost:8000/api/v1/simulations/{session_id}/stop
```

### 添加扰动事件

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

### 取消扰动事件

```bash
curl -X DELETE http://localhost:8000/api/v1/simulations/{session_id}/events/{event_id}
```

### 前端 Cesium 接入说明

1. 页面初始化时请求 `GET /api/v1/maps/demo_2/geojson`
2. 使用 `Cesium.GeoJsonDataSource.load()` 加载路网
3. 创建仿真后连接响应中的 `websocket_url`
4. 以 `vehicle_id` 为唯一键创建或更新车辆实体
5. 使用 `longitude`、`latitude` 和 `angle` 更新车辆位置
6. 仿真结束时移除不再存在的车辆实体

### 前端字段映射

| 前端字段 | 后端字段 | MVP 说明 |
|---|---|---|
| 场景模式 | `intersection_ids` | 固定 `["demo_2"]` |
| 交通流模式 | `period` | `morning_peak` / `off_peak` / `evening_peak` |
| 起始点 OD | `origins` | 目前只支持进口筛选，不是 A→B OD |
| 扰动事件 | `initial_events` | 支持施工占道、事故、临时限速 |
| 交通流倍率 | `flow_multiplier` | `0.1` 到 `5.0` |
| 仿真时长 | `duration_seconds` | 必须大于 0 |
| 管控算法 | `control_mode` | MVP 只允许 `fixed` |

### 暂停按钮

- “开始仿真”和“结束仿真”已接通
- “暂停仿真”**暂未实现**
- 前端应禁用暂停按钮

当前 `SimulationManager` 没有 pause/resume 接口，后端不会暂停，也不会破坏 TraCI 线程所有权设计。

### 测试

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

### 当前功能范围

已实现：

- 健康检查
- catalog
- demo_2 GeoJSON 路网显示
- 启动/查询/停止仿真
- WebSocket 实时推送数据
- 扰动事件增删
- 车辆经纬度转换

---

## 2. Mock 后端（前端联调）

FastAPI Mock 服务，供前端开发联调。实现 `docs/backend_mock_spec.md` 中的 HTTP 与 WebSocket 接口，并通过 Swagger 发布 OpenAPI 契约。

### 本地端口

| 服务 | 地址 |
|---|---|
| 前端 Vite | `http://localhost:5173` |
| 后端 FastAPI | `http://localhost:8000` |
| Swagger UI | `http://localhost:8000/docs` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |
| 健康检查 | `http://localhost:8000/health` |

### 启动

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查会同时返回 API 基础路径、主 WebSocket 路径和 3D Tiles 可用状态。

### 前端联调

1. 终端 1 启动后端 `8000`。
2. 终端 2 执行 `cd frontend && npm run dev`，启动前端 `5173`。
3. 前端使用同源 `/api/v1`，Vite 将 `/api` 转发到 `http://127.0.0.1:8000`。
4. WebSocket 主入口是 `/api/v1/ws/runs/{run_id}`，同样通过 Vite `/api` 代理。
5. 3D Tiles 使用 `/3dtiles/xiongan/tileset.json`，由 Vite 转发到后端。

`frontend/.env` 可覆盖目标，但本地标准配置应为：

```text
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
VITE_API_BASE_URL=/api/v1
VITE_TRAFFIC_WS_URL=
VITE_XIONGAN_3DTILES_URL=/3dtiles/xiongan/tileset.json
```

`VITE_TRAFFIC_WS_URL` 留空时，前端根据当前页面协议与 host 自动生成同源 WebSocket 地址。生产环境前后端分域时才需要显式填写完整 `ws://` 或 `wss://` URL。

### 3D Tiles

后端默认读取：

```text
E:\city\3dtiles\雄安新区建筑_彩色_3dtiles\tileset.json
```

也可以通过环境变量指定包含 `tileset.json` 的目录：

```powershell
$env:XIONGAN_3DTILES_DIR = "D:\data\xiongan-3dtiles"
```

只有目录中的 `tileset.json` 存在时，后端才会挂载 `/3dtiles/xiongan`。可通过 `/health` 的 `tiles_available` 判断资源是否可用。

### 默认数据

- 预置运行：`run_20260704_001`，状态为 `running`。
- 与前端 `.env.example` 中 `VITE_DEFAULT_RUN_ID` 对齐。

### 安全约定

- 不要提交 `frontend/.env`、后端 `.env` 或真实 token。
- Cesium ion 和天地图 token 应通过本地忽略文件或部署平台 Secrets 注入。
- `.env.example` 只能保留变量名和空值。
