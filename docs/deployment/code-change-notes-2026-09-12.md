# CityPulse 部署准备代码修改说明

日期：2026-09-12
用途：供项目成员审阅本次代码修改、确认部署影响，并作为后续提交和发布的说明。

> 下文状态表记录部署准备时的快照。用户随后授权开始部署；实际发布目录、容器环境、部署期间新增修复及验收结果见 [SSH 隧道验收部署记录](acceptance-deployment-2026-09-12.md)。修复仍未自动提交或推送，`main` 未修改。

> fix2 补充：已修复 Redis Backend 丢失车辆经纬度的问题；已从原开发版只读找到天地图浏览器 Key，接入独立部署运行配置并验证瓦片。百度在线瓦片在原开发副本和部署版均返回 403，Carto 仍缺 Key。下文“天地图 Key 缺失”等早期判断以最新部署记录为准。原开发版源码、配置、依赖和服务均未修改。

## 1. 修改范围与当前状态

本次修改基于 `develop` 的提交 `6f18a23e2e74fc32fb94c5a4c1359ec74b484c52`，主要处理三件事：

1. Redis 模式的 Backend 不应直接或间接加载 SUMO 仿真内核。
2. 地图静态资源应能够被构建、发布和正确读取，不能把加载失败当作成功。
3. 浏览器地图 Key 应支持部署时配置，服务端密钥应与前端公开配置分开。

| 位置 | 状态 |
|---|---|
| 原工作目录 `D:\GitCode\citypulse-v2x-sim` | `main` 分支，仍为 `a5d568c`；最近核对时工作区干净，本次未修改 |
| 修复目录 `D:\GitCode\citypulse-deploy-fix` | 独立 worktree，分支 `codex/deploy-boundaries` |
| Git 提交与远端 | 修复仍是未提交的工作区文件；尚未 commit、push，也没有合入 `develop` |
| 服务器验证目录 | 核心修复已通过文件打包复制到 `/home/kemove/devdata1/citypulse-deploy/validation/code`，用于验证，不是 Git 拉取更新 |
| 服务器发布目录 | `/home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23` 尚未应用这些修复 |
| 服务器正在运行的开发服务 | 仍使用原开发目录，没有切换到修复版本 |

**本说明文档也只新增在修复 worktree 中，不修改 `main`，不自动提交或推送。**

## 2. Backend 与 SUMO 的依赖隔离

### 2.1 原问题

虽然 `develop` 已有 `simulation_protocol` 和 Redis client，但仍存在间接导入：

| 触发路径 | 原有问题 |
|---|---|
| Backend 导入场景导出服务 | 顶层导入 `simulation.sumo.engine.scenario` 和 `session` |
| Backend 导入公共指标模块 | `traffic_eval` 包初始化导入本机评估 runner，采集器也引用 session 中的快照类型 |
| Redis 正常后初始化 AI 接管功能 | `traffic_llm_runtime` 读取产物路径和快照类型时仍引用 SUMO 包 |
| 请求地图接口 | 即使运行在 Redis 模式，依赖注入仍强制检查 `SUMO_HOME`，导致返回 503 |

这也解释了为什么仅测试“应用能被 import”或“Redis 不可用时能启动”，不足以证明生产后端已完全隔离。

### 2.2 修改方式

将不需要运行 SUMO 的数据处理代码迁入共享协议包：

| 新模块 | 职责 |
|---|---|
| `simulation_protocol/artifacts.py` | 生成产物的目录与文件路径定义 |
| `simulation_protocol/vehicle_profiles.py` | 车型属性、解析与校验 |
| `simulation_protocol/traffic_scopes.py` | 全局、东部密集区、西部密集区等作用域常量 |
| `simulation_protocol/scenario.py` | 基于已有清单和 XML 生成场景文件，不启动仿真 |

原 `simulation/sumo/building/artifacts.py`、`vehicle_profiles.py` 和 `engine/scenario.py` 保留兼容别名，指向同一份新实现。文件行数大量减少主要是代码迁移，不是删除场景编译或车型功能。

其他配套修改：

- `backend/app/services/scenario_export_service.py` 改用共享协议包，不再依赖实际 `SimulationManager` 类型。
- `traffic_eval/collector.py`、`session_hub.py` 使用 `simulation_protocol.dto.SimulationSnapshot`。
- `traffic_eval/__init__.py` 延迟加载本机评估入口，Backend 普通导入不再触发 runner。
- `traffic_llm_runtime/manifest.py`、`phase_service.py`、`snapshot.py` 改用共享产物路径和 DTO。
- `backend/app/api/deps.py` 仅在 `local` 模式检查 `SUMO_HOME`；地图和导出服务检查管理器就绪状态，Redis 不可用时返回明确的服务错误。

### 2.3 修改后的边界

```text
Frontend → Backend → simulation_protocol → Redis → SUMO Worker
               │                                  │
               ├─ traffic_eval                    ├─ simulation
               ├─ traffic_intelligence            └─ traffic_control 算法实现
               └─ traffic_llm_runtime
```

本机开发的 `local` 模式仍可以使用 SUMO；此次隔离针对生产采用的 `redis` 模式。Backend 可以读取 SUMO 生成的数据文件，这不等于加载或执行 SUMO 内核。

## 3. 地图产物与静态资源

### 3.1 Backend 道路 GeoJSON

检查发现服务器验证副本缺少 Redis 地图接口需要的 20 个道路 GeoJSON。新增：

```text
scripts/deployment/build_map_artifacts.py
```

该脚本在 SUMO 环境中离线运行，复用已有道路转换代码，为 `demo_1` 至 `demo_20` 生成：

```text
data/maps/sumo/generated/geojson/demo_N.roads.wgs84.geojson
```

默认半径为 600 米；改变 Backend 请求半径时，需要同步生成匹配的产物。

`backend/app/core/config.py` 同时补充 Redis 模式的产物存在性检查，并支持产物目录位于仓库外部。缺失时健康状态会显示降级，不再仅凭基础清单存在就认为准备完成。

这些道路文件已在服务器验证副本生成并通过接口验证；不表示正式发布目录已同步。

### 3.2 Frontend 绿地与水系

前端原本已有以下静态数据，本次未重画或补造多边形：

```text
frontend/public/intersections/v3/demo_N/
├── environment.json
├── green.geojson
└── water.geojson
```

新增 `frontend/scripts/check-static-landcover.mjs`，支持检查本地 `public`、构建后的 `dist`，以及实际 HTTP 入口。检查内容包括：

- 20 个路口清单的编号、版本及 green/water 路径。
- 40 个 GeoJSON 的 FeatureCollection 结构、多边形类型、坐标范围和闭合环。
- HTTP 状态码与 JSON 响应类型。

该检查纳入 `prebuild` 和 `postbuild`，缺失或损坏的资源会阻止构建流程通过。

其他修复：

| 文件 | 行为变化 |
|---|---|
| `frontend/nginx.conf` | `/intersections/` 按 JSON 提供资源；文件缺失返回 404，不落入 SPA 首页 |
| `ShowcaseGeoJsonLayers.ts` | 使用统一 JSON 请求校验；必需的 green/water 加载失败会向上抛出，不再被吞掉并缓存为已完成 |

目前静态数据包含 37 个绿地 feature、35 个水系 feature。10 个路口至少有一类为空集合，属于现有数据内容，不等同于文件缺失。空集合允许正常加载，是否显示在线底图仍取决于地图服务和 Key。

## 4. 浏览器地图 Key 改为运行时配置

### 4.1 原问题与新流程

原先从 `import.meta.env.VITE_*` 读取地图 Key，生产构建时会固化到 JavaScript 中。

现在的生产加载流程为：

```text
前端容器环境变量或挂载文件
  → write_runtime_config.py
  → runtime-config.js
  → window.__CITYPULSE_CONFIG__
  → 地图组件
```

| 文件 | 职责 |
|---|---|
| `frontend/src/config/runtime.ts` | 定义公开配置类型与统一读取函数 |
| `frontend/public/runtime-config.js` | 默认空配置，供开发和未注入配置时使用 |
| `frontend/docker/write_runtime_config.py` | 只读取白名单字段，以 JSON 编码生成配置，先写临时文件再替换 |
| `frontend/docker/40-runtime-config.sh` | 前端容器启动时执行配置生成器 |
| `frontend/index.html` | 在应用入口模块之前加载运行时配置 |
| `frontend/nginx.conf` | 对运行时配置返回 `Cache-Control: no-store` |
| `frontend/Dockerfile` | 安装生成器所需 Python，并安装入口脚本 |

支持的公开字段如下：

| 容器运行时变量 | 浏览器字段 | 用途 |
|---|---|---|
| `CITYPULSE_BROWSER_BAIDU_AK` | `baiduMapAk` | 百度地图 |
| `CITYPULSE_BROWSER_TIANDITU_TOKEN` | `tiandituToken` | 天地图 |
| `CITYPULSE_BROWSER_CARTO_KEY` | `cartoBasemapKey` | Carto，可选 |
| `CITYPULSE_BROWSER_AMAP_KEY` | `amapMapKey` | 高德测试页面 |

各变量支持追加 `_FILE` 从挂载文件读取；同一个字段同时设置非空变量值与 `_FILE` 时会报错，避免配置歧义。

### 4.2 开发兼容与生产更新

- Vite 开发模式仍兼容原 `VITE_BAIDU_MAP_AK`、`VITE_TIANDITU_TOKEN` 等变量。
- 生产构建忽略上述开发 Key，使用运行时配置。
- 更换 Key 后，用相同镜像和新配置重新创建前端容器，再刷新浏览器；无需重新构建前端。
- 浏览器 Key 对访问者可见。运行时配置解决的是配置与更换方式，不是隐藏浏览器凭据。
- 高德测试页面及模板中的硬编码 Key 已移除；增加 `check-browser-credentials.mjs`，在构建前检查字面量回退和非空 Key 模板。扫描只报告路径，不输出值。
- 本次删除不清除 Git 历史中的旧值；扫描是基础防回归措施，不是完整历史秘密扫描。

## 5. 服务端配置、secrets 与镜像

Backend 新增配置入口：

| 配置 | 作用 |
|---|---|
| `CITYPULSE_ENV_FILE` | 指定后端环境文件；设置为空可禁止读取开发 `backend/.env` |
| `CITYPULSE_SECRETS_DIR` | 指定按字段名读取的 secret 文件目录，例如 `/run/secrets` |

例如，服务端模型认证密钥可以挂载为 `/run/secrets/citypulse_llm_api_key`。同字段的环境变量优先于 secret 文件。含密码的 Redis URL 属于服务端配置，不能写入浏览器配置文件。

新增 `deploy/backend.env.example` 和 `deploy/browser.env.example`。真实配置使用被 Git 忽略的 `deploy/local/` 或 `deploy/secrets/`；本说明不包含真实 Key 或服务器登录密码。

新增根目录 `.dockerignore`，配合已有前端 `.dockerignore` 排除本机环境文件、secrets、缓存等。`.gitattributes` 为 shell 脚本固定 LF 行尾，避免 Linux 容器执行时受 Windows 换行影响。

新增 `backend/Dockerfile`：

- 基于 Python 3.10，默认 Redis 模式、单个 Uvicorn 进程。
- 安装 Backend 依赖和 CPU PyTorch。
- 只复制 Backend、共享协议/评估/AI runtime、知识库及控制模式注册表。
- 不复制 SUMO 内核或 Worker 管控算法实现与权重。
- 保留 Backend 自身需要的预测模型；“不带算法权重”不等于去掉预测功能。
- 定义 SUMO 包不存在的构建检查。

以上是代码和镜像定义的修改，**不代表镜像已经构建成功**。完整 Worker 镜像和整套服务启动配置仍待完成。

## 6. 测试与验证结果

以下为此前修复过程中实际执行的结果，本次编写文档没有重复运行全部测试。

| 验证 | 结果与范围 |
|---|---|
| Python 回归 | 50 项通过，覆盖部署边界、配置、场景导出、指标、地图、道路转换和密集区域 |
| Redis 健康路径 | 独立 Redis 下真实执行 Backend lifespan；健康正常，20 个地图 HTTP 接口返回 200 |
| SUMO 隔离 | 全新解释器禁止导入 `simulation/libsumo/sumolib/traci`，仍完成启动、故障降级、地图读取和约 9.3 MB 场景 ZIP 导出 |
| 前端构建 | Vue 类型检查与 Vite 生产构建通过；注入开发测试 Key 后确认其未进入生产 JavaScript |
| 图层测试 | 8 项通过；public、dist 的资源校验通过 |
| Nginx HTTP | 独立 Nginx 验证 20 份清单、40 份 GeoJSON；缺失文件返回 404 |
| 配置更新 | 同一构建产物生成两个不同测试 Key，HTTP 读取到新值；响应不缓存，未混入服务端测试密钥 |
| 基础检查 | 浏览器 Key 扫描和 `git diff --check` 通过 |

新增/调整的验证入口包括：

```text
scripts/deployment/check_backend_isolation.py
tests/deployment/test_backend_lifespan.py
tests/deployment/test_api_dependencies.py
tests/deployment/test_import_boundaries.py
tests/deployment/test_runtime_config.py
frontend/scripts/static-landcover.test.mjs
```

`tests/test_dense_traffic_scopes.py` 原有测试输入缺少编译器要求的 TLS 清单，本次补齐 fixture，没有放宽生产校验。

服务器测试使用 `v2x-ai-py310` 加验证专用依赖目录，没有向共享 Conda 环境安装或升级包。验证用 Redis 容器和独立 Nginx 已清理，原有开发服务保持运行。

## 7. 已确认的部署选择

- 先通过 SSH 隧道验收，前端计划仅绑定服务器 `127.0.0.1:18080`。
- 保留天地图选项，不隐藏、不禁用；当前不准备天地图 Key。
- 当前默认底图不是天地图：全站背景使用 Carto，OpenLayers 面板默认 OSM，3D 场景使用百度地图。
- 选择未配置 Key 的天地图时可能无法加载在线瓦片；以后可以通过运行时配置补充。
- `demo_5/demo_6/demo_9` 道路/建筑遮挡数据问题先不处理，不作为本次验收实例启动的阻碍。
- 后续工作只围绕修复分支和 `develop`，不修改或合并到 `main`。

## 8. 未完成事项及验收边界

1. **代码尚未提交或推送。** 后续需审阅修改、提交到修复分支，并确定如何纳入 `develop`。
2. **镜像构建未完成。** 前端构建曾在基础镜像获取/下载阶段长时间等待而取消；Backend Dockerfile 尚未完成实际构建验收。
3. **完整部署配置待完成。** Worker 镜像、服务网络、模型/数据挂载、持久化和启动管理仍需落实。
4. **真实地图授权待验证。** 百度 Key 在原开发配置中存在；新入口下是否授权有效尚未验收，天地图 Key 缺失。
5. **完整业务链路未验收。** 已通过的隔离与接口测试不能替代实际仿真、算法控制、WebSocket、RAG、预测、AI 控制和恢复测试。
6. **三处原有场景数据问题保留。** 扩展 intersection-environment 审计报告道路/建筑遮挡范围不一致；它未计入上述 8 项通过测试，也未在本次修改中处理。
7. **没有进行带真实地图 Key 的完整 3D 视觉验收。** HTTP 资源通过不等于所有页面最终视觉效果都已确认。

## 9. 审阅建议

审阅时优先检查以下内容：

1. `simulation_protocol` 中迁移后的实现与旧路径兼容性，是否保持 Worker 和场景导出语义。
2. Redis 健康路径及 API 依赖注入，是否仍存在隐藏的 SUMO 导入或 local 回退。
3. 浏览器运行时配置白名单与 Docker 构建上下文，确认服务端密钥不会进入前端。
4. 地图产物的版本和半径是否与本次发布配置一致。
5. 正式镜像完成后，重新运行隔离验证及完整业务验收，再切换入口。

操作说明见同目录 `production_boundaries.md`；验证记录见 `verification-2026-09-12.md`。
