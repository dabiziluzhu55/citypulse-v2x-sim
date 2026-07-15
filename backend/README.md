# 后端 Mock 服务

FastAPI Mock 服务，供前端开发联调。实现 `docs/backend_mock_spec.md` 中的 HTTP 与 WebSocket 接口。

## 启动

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

启动后访问：

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

## 默认数据

- 预置运行：`run_20260704_001`（状态 `running`）
- 与前端 `.env.example` 中 `VITE_DEFAULT_RUN_ID` 对齐

## 前端联调

1. 终端 1：启动后端（端口 8000）
2. 终端 2：`cd frontend && npm run dev`
3. 前端 Vite 会将 `/api` 代理到 `http://localhost:8000`
