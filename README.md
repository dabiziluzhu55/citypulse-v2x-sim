# CityPulse V2X Simulation

城市交通与车路云协同仿真可视化项目，包含 Vue 3 仪表盘前端和 FastAPI Mock 后端。

## 环境要求

- Node.js 20.19+ 或 22.12+
- Python 3.10+

## 启动后端

```powershell
cd backend
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

## 启动前端

```powershell
cd frontend
npm install
npm run dev
```

前端默认地址为 `http://127.0.0.1:5173`，开发代理连接 `http://127.0.0.1:8001`。

## 构建检查

```powershell
cd frontend
npm run build
```

雄安新区本地 3D Tiles 可通过环境变量 `XIONGAN_3DTILES_DIR` 指定；未配置时后端仍可正常提供其他 Mock API。
