# develop 生产部署边界

此变更基于 `6f18a23`，修复 Redis Backend 的间接 SUMO 导入、静态地图交付与运行时配置。

## 本次已确认的验收方式

- 先通过 SSH 隧道验收：服务器前端仅监听 `127.0.0.1:18080`，暂不开放内网/公网入口。
- 保留天地图选项，不隐藏、不禁用；当前不配置天地图 Key，选择该底图时可能无法加载在线瓦片。
- `demo_5/demo_6/demo_9` 道路/建筑遮挡数据问题暂不处理，不作为本次验收实例启动的阻碍。
- 原有开发服务保留，使用独立端口与独立 Redis；正式镜像和业务链路仍需验收。

验收实例启动后，在个人电脑执行：

```bash
ssh -N -L 18080:127.0.0.1:18080 346-4090-home
```

保持 SSH 连接，然后浏览器打开 `http://127.0.0.1:18080`。

## Backend 与 Worker

Redis API 使用 `simulation_protocol` 的 DTO、Redis client、清单读取和纯 XML 场景编译。
`traffic_eval` 的采集器只依赖 DTO；本机评估 runner 仅在调用 CLI 功能时加载。
旧 `simulation.sumo` 路径保留兼容别名供 Worker 与训练代码使用。
本地开发的 `SIMULATION_MANAGER_MODE=local` 仍然支持 SUMO；生产固定为 `redis`。

`backend/Dockerfile` 以仓库根目录为 context，仅复制 Backend、共享协议/评估/事件识别、
控制模式注册表与知识库，不复制 SUMO 内核、算法实现和算法 checkpoint。
Backend 的预测模型仍属于 Backend；“无 checkpoint”指 Worker 管控算法权重，不包含预测所需模型。
图像构建时确认 SUMO 包不存在，运行时还需完成下述隔离验收。

```bash
docker build -f backend/Dockerfile -t citypulse-backend:VERSION .
```

部署时只读挂载 `data/maps/sumo`（含 official 映射与 generated 数据）、本地 RAG 模型和索引；
Backend 与 Worker 共用可写的 sessions 目录。不要挂载整个开发仓库覆盖 `/app`。
Backend 必须使用单个 Uvicorn worker。Worker 独立运行 Celery prefork。

## 预生成地图

Backend 的道路 GeoJSON 与前端绿地/水系 GeoJSON 是两组不同资源。
在 SUMO 环境中离线执行（默认半径 600 米，与 Backend 配置一致）：

```bash
PYTHONPATH=. python scripts/deployment/build_map_artifacts.py
python scripts/deployment/check_map_artifacts.py
```

生成 `generated/geojson/demo_1.roads.wgs84.geojson` 至 `demo_20...`。
Backend 不在请求时运行生成器。缺失时 Redis 健康检查显示 degraded。
若更改地图请求半径，需要同步生成对应半径的产物。

前端已有 `public/intersections/v3/demo_N/environment.json`，其中 green/water 指向同目录
静态 FeatureCollection。`prebuild` 与 `postbuild` 校验全部 20 组路径、JSON 和多边形坐标。
Nginx 对 `/intersections/` 返回 JSON；缺失文件返回 404，不返回 SPA 首页。
必需的绿地/水系请求失败不再被吞掉并缓存成“已加载”。
空 FeatureCollection 是有效源数据，不自动补造多边形；此时可显示已有百度底图绿地/水系。

```bash
cd frontend
npm ci
npm run build
node --test scripts/static-landcover.test.mjs scripts/showcase-layers.test.mjs
node scripts/check-static-landcover.mjs https://YOUR_DEPLOYMENT_ORIGIN
```

## 浏览器 Key：运行时配置

复制 `deploy/browser.env.example` 到 Git 忽略的 `deploy/local/browser.env`，只在服务器填写。
前端容器启动脚本从显式白名单生成 `/runtime-config.js`，在入口模块之前加载。
生成器采用 JSON 编码，支持 `CITYPULSE_BROWSER_*_FILE` 文件挂载；不遍历/公开其他环境变量。
该文件响应为 `Cache-Control: no-store`。更换 Key 后用相同镜像重建容器并刷新浏览器，
无需 `npm run build`。Docker 的 env-file 修改不会自动改变已有容器环境。

```bash
docker build -t citypulse-frontend:VERSION frontend
# 在已有应用 Docker 网络中启动，网络内 Backend 服务名必须可解析为 backend。
docker run -d --name citypulse-frontend --network YOUR_APP_NETWORK \
  --env-file deploy/local/browser.env -p 127.0.0.1:18080:80 \
  -v /ABSOLUTE/roadside_media/encoded:/usr/share/nginx/html/roadside-media:ro \
  citypulse-frontend:VERSION
```

VITE_BAIDU_MAP_AK / VITE_TIANDITU_TOKEN / VITE_CARTO_BASEMAP_KEY / VITE_AMAP_MAP_KEY
只作为 Vite **开发模式**兼容输入；生产构建忽略这些 Key，禁止使用 build ARG 注入。
浏览器 Key 是公开客户端凭据；runtime-config 不隐藏 Key。上线前需在各地图平台核对
浏览器应用类型与域名/来源限制，只允许计划中的测试和生产来源，不使用全站通配授权。
当前没有天地图 Key，不能把静态资源验收等同于天地图在线授权验收。

## 服务端 secrets

复制 `deploy/backend.env.example` 到 `deploy/local/backend.env`，在容器运行时注入。
普通配置用 env-file；服务端 Key 可挂载为 `/run/secrets/citypulse_llm_api_key`，
并设置 `CITYPULSE_SECRETS_DIR=/run/secrets`。Pydantic 按字段名读取，环境变量优先于挂载文件。
生产 `CITYPULSE_ENV_FILE=` 禁止隐式读取开发 `backend/.env`。
Redis URL 如包含密码同样属于服务端配置，不能进入浏览器配置。
根目录与前端 `.dockerignore` 排除本机配置和 secrets，Git 忽略 `deploy/local` 与 `deploy/secrets`。
`prebuild` 还扫描浏览器 Key 的硬编码回退与非空模板（只报路径，不打印值）；这是基础防回归检查，不替代完整历史秘密扫描。
仓库中原有的高德硬编码 Key 已移除；历史提交中的值不会因本次删除而消失，持有人需评估旧值是否仍有效。

## 验收与回滚

```bash
PYTHONPATH=. python -m pytest tests/deployment backend/tests/test_scenario_export.py backend/tests/test_standard_traffic_metrics.py -q
PYTHONPATH=. python scripts/deployment/check_backend_isolation.py
# 可选：仅连接专门用于验证的 Redis，不指向开发/生产 Redis。
PYTHONPATH=. python scripts/deployment/check_backend_isolation.py --redis-url redis://127.0.0.1:16381/1
```

隔离脚本在全新解释器中禁止 simulation/libsumo/sumolib/traci 导入，执行真实 lifespan、
Redis 故障降级、20 个地图读取和实际场景 ZIP 导出；提供独立 Redis 时也验证健康路径与 HTTP 地图接口。
配置测试检查服务端秘密不会进入公开配置，前端检查同一镜像切换配置后的值和缓存策略。
正式切换前还需业务仿真、WebSocket 与算法控制验收。本次边界修复不自动替换已有开发服务。
回滚应同时恢复代码/镜像与对应配置，保留会话数据；停止前先处理活动仿真。
