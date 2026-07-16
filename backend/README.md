# 后端 Mock 服务

FastAPI Mock 服务，供前端开发联调。实现 `docs/backend_mock_spec.md` 中的 HTTP 与 WebSocket 接口，并通过 Swagger 发布 OpenAPI 契约。

## 本地端口

| 服务 | 地址 |
|---|---|
| 前端 Vite | `http://localhost:5173` |
| 后端 FastAPI | `http://localhost:8000` |
| Swagger UI | `http://localhost:8000/docs` |
| OpenAPI JSON | `http://localhost:8000/openapi.json` |
| 健康检查 | `http://localhost:8000/health` |

## 启动

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查会同时返回 API 基础路径、主 WebSocket 路径和 3D Tiles 可用状态。

## 前端联调

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

## 3D Tiles

后端默认读取：

```text
E:\city\3dtiles\雄安新区建筑_彩色_3dtiles\tileset.json
```

也可以通过环境变量指定包含 `tileset.json` 的目录：

```powershell
$env:XIONGAN_3DTILES_DIR = "D:\data\xiongan-3dtiles"
```

只有目录中的 `tileset.json` 存在时，后端才会挂载 `/3dtiles/xiongan`。可通过 `/health` 的 `tiles_available` 判断资源是否可用。

## 默认数据

- 预置运行：`run_20260704_001`，状态为 `running`。
- 与前端 `.env.example` 中 `VITE_DEFAULT_RUN_ID` 对齐。

## 安全约定

- 不要提交 `frontend/.env`、后端 `.env` 或真实 token。
- Cesium ion 和天地图 token 应通过本地忽略文件或部署平台 Secrets 注入。
- `.env.example` 只能保留变量名和空值。
