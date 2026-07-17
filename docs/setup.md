# CityPulse V2X Sim 单机启动说明

本文说明如何在一台 Windows 电脑上同时运行前端、FastAPI 后端和 SUMO 仿真。命令默认在仓库根目录执行。

## 1. 环境要求

- Windows 10/11 64 位
- Git
- Python 3.11 或 3.12 64 位
- Node.js 20 或更高版本
- 支持 WebGL 2 的 Chrome 或 Edge
- 建议至少 16 GB 内存；加载本地 3D Tiles 时建议使用独立显卡

检查版本：

```powershell
git --version
python --version
node --version
npm --version
```

## 2. 获取代码

```powershell
git clone https://github.com/dabiziluzhu55/citypulse-v2x-sim.git
cd citypulse-v2x-sim
```

如果已经克隆：

```powershell
git pull
```

## 3. 安装后端与 SUMO

### 3.1 创建虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止激活脚本：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### 3.2 安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

`backend/requirements.txt` 已包含官方 `eclipse-sumo`、`pyproj`、FastAPI 和 Uvicorn。官方 SUMO wheel 包含 `sumo.exe`、`sumo-gui.exe`、`netconvert.exe`、`sumolib` 和 `traci`。

后端会自动查找 Python 环境中的 SUMO，无须每次手工设置 `SUMO_HOME`。验证：

```powershell
python -c "from backend.app.core.config import Settings; print(Settings().resolved_sumo_home())"
```

正常输出类似：

```text
C:\...\.venv\Lib\site-packages\sumo
```

如使用独立安装的 Eclipse SUMO，也可在 `backend/.env` 配置：

```env
SUMO_HOME=C:\Program Files (x86)\Eclipse\Sumo
```

### 3.3 生成仿真产物

首次运行或路网更新后执行：

```powershell
python -m simulation.sumo.build_tls --intersections demo_2
python -m simulation.sumo.build_traffic --intersections demo_2
```

应生成：

```text
data\maps\sumo\generated\traffic_manifest.json
data\maps\sumo\generated\tls_manifest.json
data\maps\sumo\generated\TotalMap_20.signals.net.xml
```

`Found sharp turn`、`Intersecting left turns` 等为原始路网几何警告；只要命令最终显示 `Built official ...`，生成通常已成功。

## 4. 配置地图 Token

复制前端环境模板：

```powershell
Copy-Item frontend\.env.example frontend\.env
```

编辑 `frontend/.env`：

```env
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
VITE_API_BASE_URL=/api/v1
VITE_TRAFFIC_WS_URL=

VITE_CESIUM_ION_TOKEN=你的_Cesium_Ion_Token
VITE_TIANDITU_TOKEN=你的天地图_Token

VITE_XIONGAN_3DTILES_URL=/3dtiles/xiongan/tileset.json
```

### 4.1 Cesium Ion Token

申请地址：https://ion.cesium.com/tokens

用途：

- Cesium 全球影像回退
- Cesium World Terrain
- 本地 3D Buildings 失败时加载 OSM Buildings

建议：

1. 注册 Cesium Ion 账号。
2. 创建 Access Token。
3. 确保 Token 可访问全球影像、World Terrain 和 OSM Buildings。
4. 将 Token 仅写入 `frontend/.env`。

### 4.2 天地图 Token

申请地址：https://console.tianditu.gov.cn/

用途：

- `img_w` 卫星影像
- `cia_w` 中文注记

建议创建浏览器端应用，并将以下开发来源加入白名单：

```text
http://127.0.0.1:5173
http://localhost:5173
```

共享 Token 容易触发 HTTP 429。发生限流时，前端会按以下顺序降级：

```text
天地图影像 → Cesium Ion 全球影像 → NaturalEarthII 离线底图
```

### 4.3 Token 安全

- 不要将真实 Token 写入 `src/constants/tokens.ts`。
- 不要提交 `frontend/.env`；根目录 `.gitignore` 已忽略 `.env`。
- `.env.example` 只保留变量名，不包含真实凭据。
- 修改 `frontend/.env` 后必须重启 Vite。

## 5. 安装前端依赖

```powershell
cd frontend
npm install
cd ..
```

## 6. 同时启动后端和前端

需要两个 PowerShell 窗口。

### 窗口 A：启动 FastAPI 与 SUMO 管理器

```powershell
cd citypulse-v2x-sim
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

必须使用 `--workers 1`，因为应用只允许一个全局 `SimulationManager` 和一个 TraCI 所有者。活动仿真期间不要使用 `--reload`。

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

正常结果：

```json
{
  "status": "ok",
  "sumo_home_configured": true,
  "generated_artifacts_ready": true,
  "simulation_manager_ready": true
}
```

Swagger 文档：http://127.0.0.1:8000/docs

### 窗口 B：启动 Vite 前端

```powershell
cd citypulse-v2x-sim\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

访问：http://127.0.0.1:5173

前端通过 Vite 将 `/api` 和仿真 WebSocket 代理到 `VITE_BACKEND_PROXY_TARGET`。

## 7. 启动一次仿真

在页面中选择：

- 路口：`demo_2`
- 时段：`morning_peak`、`off_peak` 或 `evening_peak`
- 控制模式：`fixed`
- 仿真时长：大于 0

点击启动后，后端状态应依次变化：

```text
STARTING → RUNNING → COMPLETED
```

也可用 PowerShell 验证：

```powershell
$body = @{
  intersection_ids = @('demo_2')
  period = 'off_peak'
  origins = @{}
  window_start_seconds = 0
  duration_seconds = 10
  flow_multiplier = 1.0
  control_mode = 'fixed'
  seed = 42
  step_length = 0.05
  realtime = $true
  gui = $false
  snapshot_interval_seconds = 0.2
  initial_events = @()
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/simulations `
  -Method Post `
  -ContentType 'application/json' `
  -Body $body
```

返回值包含 `session_id`，状态查询地址为：

```text
GET /api/v1/simulations/{session_id}
```

## 8. 前后端接口契约

当前前端只调用后端实际存在的接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 环境健康检查 |
| GET | `/api/v1/catalog` | 路口、车道和时段目录 |
| GET | `/api/v1/maps/{intersection_id}/geojson` | SUMO 路网 GeoJSON |
| POST | `/api/v1/simulations` | 启动仿真 |
| GET | `/api/v1/simulations/{session_id}` | 获取状态快照 |
| POST | `/api/v1/simulations/{session_id}/stop` | 停止仿真 |
| POST | `/api/v1/simulations/{session_id}/events` | 添加事件 |
| DELETE | `/api/v1/simulations/{session_id}/events/{event_id}` | 取消事件 |
| WebSocket | `/api/v1/simulations/{session_id}/stream` | 实时快照和心跳 |

## 9. 后端未启动时的地图行为

地图与 SUMO 生命周期相互独立。后端未启动时仍可显示：

- 天地图或 Cesium Ion 影像
- Cesium 地球和地形
- 本地雄安 3D Tiles
- OSM Buildings 回退
- NaturalEarthII 最终离线回退

不可用的是 catalog、SUMO 路网、车辆、信号灯、指标和仿真控制。

## 10. 常见问题

### `/api/v1/*` 返回 503

检查后端是否启动：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
```

如果 `status=degraded`，检查 SUMO 自动探测结果并重新生成产物。

### GeoJSON 返回 500

确认已安装 `pyproj`：

```powershell
python -c "import pyproj; print(pyproj.__version__)"
```

### 地图呈纯绿色

这通常表示天地图和 Cesium Ion 都不可用，页面正在显示最后一级 `NaturalEarthII` 离线底图。检查浏览器控制台和两个 Token。

### 天地图返回 429

使用自己的天地图 Token，确认白名单，并等待旧 Token 限流窗口结束。前端会自动切换到 Cesium Ion 全球影像。

### `3D Tiles tile load failed`

单个瓦片失败不会再覆盖整张地图。累计失败达到阈值时，本地建筑会被移除并尝试 OSM Buildings；影像底图仍保持可用。

## 11. 停止服务

分别在前端和后端窗口按：

```text
Ctrl+C
```

如仿真仍在运行，建议先通过页面停止按钮或调用停止接口，再关闭后端。
