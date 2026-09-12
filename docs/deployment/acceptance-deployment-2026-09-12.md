> 最新发布已更新为 fix3，详见 [正确性修复与专项优化](fix3-correctness-and-traffic-optimization.md)。以下 fix2 内容保留为历史记录。

# SSH 隧道验收部署记录

## 发布来源与目录

- 基线：`develop`，`6f18a23e2e74fc32fb94c5a4c1359ec74b484c52`。
- 部署修复来自本地 `D:\GitCode\citypulse-deploy-fix` 的 `codex/deploy-boundaries` 工作区，通过文件同步应用。尚未提交、推送或合并到 `develop`。
- 原 `D:\GitCode\citypulse-v2x-sim` 的 `main` 不参与部署修改。
- 服务器发布目录：`/home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23-acceptance`。
- 私有配置目录：`/home/kemove/devdata1/citypulse-deploy/config/acceptance`。
- 持久数据目录：`/home/kemove/devdata1/citypulse-deploy/shared/acceptance`。
- 当前发布标签：`develop-6f18a23-fix2`；它是发布标签，不是修复提交号。Backend 为本轮新镜像；Frontend、Worker 复用 fix1 的相同镜像内容并增加 fix2 标签，运行中的 Worker 无需重启。

## 运行结构

Compose 项目为 `citypulse-acceptance`，包含 Frontend、Backend、SUMO Worker 和独立 Redis。仅 Frontend 映射服务器 `127.0.0.1:18080`。Backend 通过 Redis 派发任务；SUMO 只安装在 Worker 中。Backend 的预测模型和 RAG 嵌入模型使用 CPU。

复用服务器已有的 `8001` 端口 Qwen 服务，容器通过 `host.docker.internal` 访问。因此 Qwen 服务仍是外部运行依赖；本 Compose 不管理其启动和重启。

容器自行安装依赖，不向共享 `v2x-ai-py310` Conda 环境安装或升级包。本次没有启停原开发服务。最终复核原 8000 后端仍为 `ok/local`；5173 当时没有监听，不影响新的 18080 验收入口。

验收网络固定为 `172.21.0.0/16`，写在私有 `acceptance.env` 的 `CITYPULSE_ACCEPTANCE_SUBNET` 中。UFW 仅新增允许该网段访问 `172.17.0.1:8001/tcp` 的规则，备注 `CityPulse acceptance Qwen`。未开放新的公网入口。更改 Docker 网段或宿主机网关时应同步调整该规则。

## 访问方式

在个人电脑运行并保持窗口打开：

```powershell
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 18080:127.0.0.1:18080 -p 49760 kemove@frp-fun.com
```

然后打开 `http://127.0.0.1:18080`。SSH 密码不写入文档或仓库。服务器仍仅本机监听；需要访问的同伴自行建立隧道。

## 运维命令

以下命令在服务器执行：

```bash
cd /home/kemove/devdata1/citypulse-deploy/releases/develop-6f18a23-acceptance
export CITYPULSE_ACCEPTANCE_ENV=/home/kemove/devdata1/citypulse-deploy/config/acceptance/acceptance.env
docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml ps
docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml logs --tail 100 backend worker
docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml up -d
```

停止本次部署使用同一 Compose 命令的 `stop` 子命令。不要使用 `down -v`，避免删除验收 Redis 持久卷。服务设有 `restart: unless-stopped`；被手动停止的服务需手动恢复。

## 地图 Key

百度浏览器 Key 私下复制到配置目录的 `browser.env`，通过启动时生成的 `runtime-config.js` 提供。它属于浏览器可见配置，应按地图平台要求配置域名白名单。服务端认证字段放在独立 `secrets` 目录中。

本轮在原开发版 `backend/.env` 找到了 `TIANDITU_TOKEN`。此前“服务器没有天地图 Token”的判断不完整。官方接口确认该 Token 属于浏览器端 Key：服务器请求返回 403/code 301012，提示使用浏览器访问。现已只复制到部署版私有 `browser.env` 的 `CITYPULSE_BROWSER_TIANDITU_TOKEN`，前端运行配置已生效。浏览器直连实际影像瓦片返回 HTTP 200、`image/jpg`、21818 字节。原开发版配置未修改，天地图选项继续保留。

后续更换浏览器 Key 时编辑私有 `browser.env`，然后执行：

```bash
docker compose --env-file "$CITYPULSE_ACCEPTANCE_ENV" -f deploy/compose.acceptance.yml up -d --no-deps --force-recreate frontend
```

刷新浏览器即可，无需重建前端镜像。`runtime-config.js` 使用 `Cache-Control: no-store`。

## 构建说明

Docker 使用官方 `python:3.10-slim`、`ubuntu:22.04`、`node:20-alpine`、`nginx:1.27-alpine` 和 `redis:7.4-alpine`。服务器 Docker 直连镜像源不可用，本次通过现有代理下载并校验官方镜像内容后导入，没有修改 Docker daemon 或重启既有服务。

主要 Python 版本见 `deploy/requirements/runtime-constraints.txt`。使用 CPU PyTorch。Worker 最终基于 Ubuntu 22.04 安装 `sumo=1.12.0+dfsg1-1`：现有 Conda 的 `_libsumo.so` 实际来自该 Ubuntu 包，而非同版本 PyPI wheel。已确认二进制 SHA256 完全一致：`e8f15671c48f9c41d5de9afc5a0f494879646c84403d53abf945529046fad66e`。这也保留了原有 PROJ 投影行为，没有修改路网投影参数。

实际构建日志保存于服务器 `citypulse-deploy/validation/build-*.log`，完整 Python 依赖快照为同目录的 `backend-pip-freeze.txt`、`worker-pip-freeze.txt`。

本轮另修复了会话运行时未定义的 `_read_json` 调用；地图预生成脚本现在默认生成 600 米和 900 米两种数据，后端按半径读取，解决 3D 页面请求 900 米数据时的 503。新增/调整的地图服务测试 5 项通过。

缺少 Carto Key 时默认使用 OSM，避免原默认底图显示 `API KEY REQUIRED` 水印；配置 Carto Key 后恢复默认暗色底图。天地图选项保持不变。

## 验收状态

服务已启动，Backend、Redis 健康检查通过，Worker Celery ping 返回 `pong`。浏览器通过 SSH 隧道打开页面进入 `READY`，最终 2D 页面没有捕获到 JavaScript 异常。

| 验收项 | 结果 |
|---|---|
| Backend 无 SUMO | 镜像中不存在 `simulation/libsumo/sumolib/traci`；Redis 模式健康 |
| 六种管控算法 | `west_dense/off_peak` 下均完成 120 秒真实仿真，各 53 辆发车，无会话错误 |
| 默认全路网场景 | `xiongan_20/morning_peak` 完成 120 秒，421 辆发车 |
| 校园场景 | `east_dense/off_peak` 完成 120 秒，34 辆发车 |
| 预测 | 上述会话均报告 `NarrowNet-TDP`，`fallback=false` |
| WebSocket | 60 秒、5 倍速会话完成，接收 126 条消息 |
| Qwen 与 RAG | 实际问答调用 `search_knowledge`，检索 3 条向量结果并生成回答；嵌入运行在 CPU |
| 静态资源 | 20 份环境清单、40 份绿地/水系 GeoJSON 通过 HTTP 校验 |
| 地图 API | 20 路口各自 600 米、900 米均返回有效 JSON |
| 路侧视频 | demo_14/15/19 均支持 Range 请求，返回 HTTP 206 |
| 评估报告 | 6 算法会话生成 PDF，HTTP 类型正确，5566 字节 |
| 运行配置 | 百度 Key 已注入；`runtime-config.js` 返回 `no-store` |

**尚未通过的外部地图项：** 百度 `apimaponline*.bdimg.com/pvd/` 返回 HTTP 403。独立复制原开发前端后，在同一台电脑、同一浏览器环境复现相同错误；原开发版与部署版的 mapv-three 1.7.1 模块 SHA256 一致。进一步请求百度鉴权接口 `qt=verify&type=three&v=1.7.1`，收到 `error=-1`、“版本过低请使用最新版本JSAPI”。官方 npm 当前最新 1.7.2 的独立构建测试仍返回同样提示和瓦片 403，因此没有发布该试验升级，最终保持 1.7.1。该提示是鉴权接口观测结果，尚不足以证明所有 403 都由版本号导致；不能直接断言 Key 丢失或域名白名单错误。需由 Key 管理者向百度确认该 AK 的 JSAPI Three 服务可用性及兼容版本。[百度错误排查说明](https://lbs.baidu.com/faq/details?id=2273&title=2484)

原开发版环境配置中没有找到 Carto Key，原前端直接使用无 Key 的 Carto URL。运行独立对照副本后，整页和瓦片均实际观察到 `API KEY REQUIRED` 水印。随后用户提供了新申请的项目 Key，已写入部署版私有 `browser.env` 的 `CITYPULSE_BROWSER_CARTO_KEY`（本文及仓库不记录 Key 值）。仅重新创建 Frontend 容器，未重建镜像、未修改原开发版。部署版已自动恢复默认 Carto 暗色底图：浏览器捕获 74 条瓦片响应，抽查请求携带 Key、HTTP 200，瓦片和整页截图均不再出现水印。百度在线底图问题仍未解决，不能宣称全部视觉验收完成。[CARTO 配置说明](https://carto.com/basemaps/apikey/)。

## fix2：车辆坐标修复与原开发版保护

部署版此前存在真实问题：Redis 模式下 `MapService.xy_to_lonlat()` 返回空坐标，前端过滤掉没有经纬度的车辆，造成有车辆计数但地图不显示。现通过纯 XML 读取 SUMO 路网的 `location`、`netOffset` 和车道形状，用 `pyproj.Proj` 转换坐标并缓存；没有在 Backend 引入 SUMO、sumolib 或 traci。启动时也校验投影。

- 同一份停止会话：修复前 154 辆车全部坐标为空，修复后 154/154 有有效经纬度。
- 三组真实路网坐标与原 sumolib 转换结果一致；新增带偏移投影、车道中心及车辆序列化测试。
- 地图服务、导入边界、启动检查共 18 项测试通过。
- 新 Backend 镜像内禁止导入 SUMO 的集成检查通过：20 路口 GeoJSON、坐标转换、约 9.2 MB 场景 ZIP 导出均完成。
- 部署页面创建独立测试会话 `faa69228-de73-4047-aaa8-36e6fe59107b`，运行至 91 秒，381/381 车辆有坐标；3D 截图中道路已有车辆。测试后仅停止此测试会话。

原开发版目录 `/home/kemove/devdata1/zrl/citypulse-v2x-sim` 只用于读取与复制对照。对照服务运行于独立的 `citypulse-deploy/comparison/frontend`；原 8000 服务 PID 73871 保持运行，最后健康检查为 `ok/local`。原 Conda 环境未安装或升级依赖。所有源码修改仍位于本地 `codex/deploy-boundaries` 修复工作区及独立发布目录；本地主仓库 `main` 工作区干净，没有提交或推送。

已知 `demo_5/demo_6/demo_9` 道路、建筑问题按用户决定暂缓。本轮是功能冒烟验收，没有进行并发压力、长期稳定性或 AI 扰动闭环的完整测试。

## 当前镜像标识

| 服务 | 镜像 SHA256 |
|---|---|
| Backend | `05e0d134b85fb57539b2dd0fe8e29d4dfd97c41c990067109dc02419da8f6fc7` |
| Worker | `d1326750bac7e87b175a4029b56b27aead55afcbc039257c2506b3ff293593ab` |
| Frontend | `95a4763cc55a1c4773b5a7fe07739fd50b169367a296a844d93b0ccc51858859` |
